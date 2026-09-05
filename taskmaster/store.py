"""SQLite-authoritative storage for Taskmaster.

The database is the runtime authority; YAML and Markdown remain a Git-facing
projection.  This module deliberately has no dependency on ``backlog_server``
so the server can adopt it without creating an import cycle.
"""
from __future__ import annotations

import base64
import copy
import errno
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
import weakref
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import yaml

from taskmaster import yaml_io
from taskmaster.index import extract_prose_paths, normalize_location, normalize_task_anchor
from taskmaster.taskmaster_v3 import (
    BODY_KEY,
    EPIC_HEAVY_FIELDS,
    PHASE_HEAVY_FIELDS,
    SCHEMA_V3,
    SCHEMA_V4,
    REVERSE_TYPE,
    _split_entity_for_v3,
    _three_way_merge_fields,
    _v4_strip_private_fields,
    detect_schema_version,
    epic_file_path,
    load_v3,
    load_v4,
    legacy_links_to_typed,
    make_handover_id,
    parse_frontmatter,
    phase_file_path,
    render_frontmatter,
    render_ideas_index,
    task_file_path,
    task_v4_from_file,
    task_v4_to_file,
)


SCHEMA_VERSION = 1
PROJECTION_SCHEMA = 5
DB_RELPATH = Path("local") / "store.db"
BUSY_TIMEOUT_MS = 30_000
HEARTBEAT_INTERVAL_SECONDS = 20.0
_MONOTONIC = time.monotonic
_BACKLOG_ID = "__backlog__"
_PROJECT_ID = "__project__"
_RETRYABLE_REPLACE_ERRNOS = {5, 13, 32, errno.EACCES, errno.EPERM}
_CORRUPTION_MARKERS = ("malformed", "not a database", "file is encrypted")
# `ideas/IDEAS.md` is derived output, not an entity: the exporter regenerates it
# from the idea rows and the scan refreshes its hash without ever parsing it.
_IDEAS_INDEX_KIND = "ideas-index"
_IDEAS_INDEX_REL = "ideas/IDEAS.md"
_LINEAR_QUEUE_REL = "integrations/linear-queue.json"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entities(
  kind TEXT NOT NULL,
  id TEXT NOT NULL,
  epic TEXT,
  status TEXT,
  archived INTEGER DEFAULT 0,
  deleted INTEGER DEFAULT 0,
  doc TEXT NOT NULL,
  body TEXT,
  rev INTEGER NOT NULL,
  updated_seq INTEGER NOT NULL,
  PRIMARY KEY(kind, id)
);
CREATE TABLE IF NOT EXISTS changes(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session TEXT NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  id TEXT NOT NULL,
  op TEXT NOT NULL,
  fields TEXT,
  before TEXT,
  after TEXT
);
CREATE TABLE IF NOT EXISTS projection(
  file TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  id TEXT,
  content_hash TEXT NOT NULL,
  mtime REAL,
  size INTEGER,
  dirty INTEGER DEFAULT 0,
  quarantined INTEGER DEFAULT 0,
  exported_seq INTEGER
);
CREATE TABLE IF NOT EXISTS projection_base(file TEXT PRIMARY KEY, content BLOB);
CREATE TABLE IF NOT EXISTS sessions(
  session TEXT PRIMARY KEY,
  pid INTEGER,
  host TEXT,
  started TEXT,
  last_seen TEXT,
  cwd TEXT,
  current_tool TEXT
);
CREATE TABLE IF NOT EXISTS linear_queue(
  seq INTEGER PRIMARY KEY,
  op TEXT,
  target_id TEXT,
  tracker_id TEXT,
  payload TEXT,
  state TEXT,
  attempts INTEGER,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS entity_paths(
  kind TEXT NOT NULL,
  id TEXT NOT NULL,
  path TEXT NOT NULL,
  match_kind TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS links(
  src_kind TEXT NOT NULL,
  src_id TEXT NOT NULL,
  type TEXT NOT NULL,
  dst_kind TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  derived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS related(
  a_kind TEXT NOT NULL,
  a_id TEXT NOT NULL,
  b_kind TEXT NOT NULL,
  b_id TEXT NOT NULL,
  via TEXT NOT NULL,
  weight INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS handover_tasks(
  handover_id TEXT NOT NULL,
  task_id TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
  kind UNINDEXED, id UNINDEXED, title, body,
  tokenize='porter unicode61'
);
CREATE INDEX IF NOT EXISTS ix_entity_paths_kind_id ON entity_paths(kind, id);
CREATE INDEX IF NOT EXISTS ix_entity_paths_path ON entity_paths(path);
CREATE INDEX IF NOT EXISTS ix_links_src ON links(src_kind, src_id);
CREATE INDEX IF NOT EXISTS ix_links_dst ON links(dst_kind, dst_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_links_edge
  ON links(src_kind,src_id,type,dst_kind,dst_id,derived);
CREATE INDEX IF NOT EXISTS ix_related_a ON related(a_kind, a_id);
CREATE INDEX IF NOT EXISTS ix_related_b ON related(b_kind, b_id);
CREATE INDEX IF NOT EXISTS ix_handover_tasks_pair ON handover_tasks(handover_id, task_id);
CREATE INDEX IF NOT EXISTS ix_entities_kind_status ON entities(kind, status);
CREATE INDEX IF NOT EXISTS ix_changes_entity ON changes(kind, id, seq);
"""


class LegacyLayoutError(RuntimeError):
    """The backlog is not at `<root>/.taskmaster`, so no store may be opened.

    The store is only ever `<root>/.taskmaster/local/store.db` (design spec
    §"Root resolution").  Silently redirecting a `.claude/` or root-layout
    backlog to `<root>/.taskmaster` opened an empty database beside the real
    backlog and reported "no tasks"; refusing is the only safe answer, and
    `backlog_canonicalize_layout` is the one supported way forward.
    """


def unsafe_storage_reason(path: Path) -> str | None:
    """Why `path` cannot host a SQLite store (network filesystem), or None."""
    return _network_filesystem_reason(path)


@dataclass(frozen=True)
class RootResolution:
    root: Path
    backlog_path: Path
    source: str
    filesystem_warning: str | None = None


@dataclass(frozen=True)
class StoreStatus:
    root: Path
    db_path: Path
    creation_token: str
    max_seq: int
    dirty_files: tuple[str, ...]
    quarantined_files: tuple[str, ...]
    resolution_source: str = ""
    schema_version: int = 0
    db_size: int = 0
    wal_size: int = 0
    recent_changes: tuple[dict[str, Any], ...] = ()
    live_sessions: tuple[dict[str, Any], ...] = ()
    merge_conflicts_24h: int = 0
    warning: str | None = None
    corrupt_files: tuple[str, ...] = ()


_STATE_LOCK = threading.RLock()
_THREAD_STATE = threading.local()
_ROOT_RESOLUTION: RootResolution | None = None
_EXPLICIT_RESOLUTIONS: dict[Path, RootResolution] = {}
_STORES: dict[Path, "Store"] = {}
_ALL_CONNECTIONS: list[sqlite3.Connection] = []
_CONNECTION_IDENTITIES: dict[int, tuple[int, int]] = {}
_WARNED_CLOUD_ROOTS: set[Path] = set()
_ACTIVE_DICT = threading.local()
_CACHE: dict[Path, tuple[str, int, dict[str, Any]]] = {}
_CONTEXT_BUILDER: Callable[[dict[str, Any]], None] | None = None
_PROGRESS_RENDERER: Callable[[dict[str, Any], str], str] | None = None
_PROCESS_ID = os.getpid()
_OPEN_LOCK_FDS: set[int] = set()


class _ThreadConnectionRegistry:
    """Thread-local connections with an explicit retirement cleanup hook."""

    __slots__ = ("connections", "finalizer", "__weakref__")

    def __init__(self) -> None:
        self.connections: dict[Path, sqlite3.Connection] = {}
        self.finalizer = weakref.finalize(
            self, _close_connections, self.connections
        )


def _after_fork_child() -> None:
    """Discard inherited SQLite handles, sessions, locks, and caches."""
    global _PROCESS_ID, _STATE_LOCK, _THREAD_STATE, _ACTIVE_DICT
    global _ROOT_RESOLUTION, _STORES, _ALL_CONNECTIONS, _CACHE, _OPEN_LOCK_FDS
    global _CONNECTION_IDENTITIES
    global _EXPLICIT_RESOLUTIONS
    for descriptor in tuple(_OPEN_LOCK_FDS):
        try:
            os.close(descriptor)
        except OSError:
            pass
    _OPEN_LOCK_FDS = set()
    _PROCESS_ID = os.getpid()
    _STATE_LOCK = threading.RLock()
    _THREAD_STATE = threading.local()
    _ACTIVE_DICT = threading.local()
    _ROOT_RESOLUTION = None
    _EXPLICIT_RESOLUTIONS = {}
    _STORES = {}
    _ALL_CONNECTIONS = []
    _CONNECTION_IDENTITIES = {}
    _CACHE = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _git_common_root(start: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = start / common
    return _absolute(common).parent


def _git_checkout_root(start: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _absolute(Path(proc.stdout.strip()))


def _cloud_filesystem_reason(path: Path) -> str | None:
    lower = str(path).replace("\\", "/").lower()
    markers = (
        "/onedrive/",
        "/dropbox/",
        "/google drive/",
        "/google drivefs/",
        "/icloud drive/",
        "/cloudstorage/",
        "/mobile documents/",
    )
    if any(marker in f"/{lower.strip('/')} /" for marker in markers):
        return "cloud-synced local folder detected; SQLite WAL remains host-local"
    return None


def _network_filesystem_reason(path: Path) -> str | None:
    """Return why *path* is unsafe for WAL, or ``None`` for host-local storage.

    The function is intentionally small and monkeypatchable.  Windows UNC and
    remote drives are detected here; POSIX filesystem type probing is best
    effort because reads must continue even when the platform cannot classify.
    """
    raw = str(_absolute(path))
    if raw.startswith("\\\\") or raw.startswith("//"):
        return "network filesystem (UNC path) is unsafe for SQLite WAL"
    if os.name == "nt":
        try:
            import ctypes

            drive = Path(raw).drive
            if drive:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
                if drive_type == 4:  # DRIVE_REMOTE
                    return "network filesystem (remote drive) is unsafe for SQLite WAL"
        except (AttributeError, OSError):
            pass
    elif Path("/proc/mounts").exists():
        try:
            candidates: list[tuple[int, str]] = []
            for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount = parts[1].replace("\\040", " ")
                if raw == mount or raw.startswith(mount.rstrip("/") + "/"):
                    candidates.append((len(mount), parts[2].lower()))
            if candidates and max(candidates)[1] in {"nfs", "nfs4", "cifs", "smbfs"}:
                return f"network filesystem ({max(candidates)[1]}) is unsafe for SQLite WAL"
        except OSError:
            pass
    return None


def resolve_root(
    start: Path | None = None, *, explicit_root: Path | None = None
) -> RootResolution:
    start_path = _absolute(start or Path.cwd())
    source = "cwd"
    if explicit_root is not None:
        root = _absolute(explicit_root)
        source = "explicit"
    elif os.environ.get("TASKMASTER_ROOT"):
        root = _absolute(Path(os.environ["TASKMASTER_ROOT"]))
        source = "env"
    else:
        common_root = _git_common_root(start_path)
        if common_root is not None:
            root = common_root
            source = "git-common-dir"
        else:
            root = start_path
    return RootResolution(
        root=root,
        backlog_path=root / ".taskmaster",
        source=source,
        filesystem_warning=_cloud_filesystem_reason(root),
    )


def _backlog_dir(path: Path) -> Path:
    path = _absolute(path)
    return path.parent if path.name == "backlog.yaml" else path


def db_path(backlog_path: Path) -> Path:
    return _backlog_dir(backlog_path) / DB_RELPATH


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _from_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _close_connections(connections: dict[Path, sqlite3.Connection]) -> None:
    for connection in tuple(connections.values()):
        try:
            connection.close()
        except sqlite3.Error:
            pass
        try:
            with _STATE_LOCK:
                _CONNECTION_IDENTITIES.pop(id(connection), None)
                while connection in _ALL_CONNECTIONS:
                    _ALL_CONNECTIONS.remove(connection)
        except (NameError, TypeError):
            pass
    connections.clear()


def _connections() -> dict[Path, sqlite3.Connection]:
    registry = getattr(_THREAD_STATE, "registry", None)
    if registry is None:
        registry = _ThreadConnectionRegistry()
        _THREAD_STATE.registry = registry
    return registry.connections


def _is_corruption(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.DatabaseError) or isinstance(
        exc, sqlite3.OperationalError
    ):
        return False
    return any(marker in str(exc).lower() for marker in _CORRUPTION_MARKERS)


def _projection_schema(backlog_dir: Path) -> int | None:
    path = backlog_dir / "backlog.yaml"
    if not path.exists():
        return None
    try:
        raw = yaml_io.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        # An existing store must get the opportunity to quarantine a broken
        # projection instead of failing before SQLite is opened.
        return None
    try:
        _validate_backlog_document(raw)
    except ValueError:
        return None
    meta = raw.get("meta") or {}
    value = meta.get("projection_schema")
    if value is None:
        return None
    try:
        return _schema_integer(value)
    except ValueError:
        return None


def _schema_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a schema integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("schema value must be an integer")


def _validate_backlog_document(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("backlog.yaml must be a mapping")
    meta_value = raw.get("meta")
    if meta_value is None:
        meta: dict[str, Any] = {}
    elif not isinstance(meta_value, dict):
        raise ValueError("backlog.yaml meta must be a mapping")
    else:
        meta = meta_value
    for field in ("schema_version", "projection_schema"):
        if field not in meta:
            continue
        try:
            _schema_integer(meta[field])
        except ValueError as exc:
            raise ValueError(f"backlog.yaml meta.{field} must be an integer") from exc


def _read_file_snapshot(path: Path) -> tuple[bytes, os.stat_result]:
    with path.open("rb") as handle:
        content = handle.read()
        stat = os.fstat(handle.fileno())
    return content, stat


def _read_sqlite_header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(16)


def _validate_safe_identifier(ident: str) -> None:
    if (
        not ident
        or ident in {".", ".."}
        or ident != Path(ident).name
        or any(character in ident for character in '<>:"/\\|?*\x00')
        or ident[-1] in {" ", "."}
        or any(ord(character) < 32 for character in ident)
    ):
        raise ValueError(f"unsafe entity id {ident!r}: expected one safe filename component")


def _local_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def open_store(
    backlog_path: Path | None = None,
    *,
    root: Path | None = None,
    session: str | None = None,
) -> "Store":
    global _ROOT_RESOLUTION
    with _STATE_LOCK:
        if backlog_path is not None:
            backlog_dir = _backlog_dir(backlog_path)
            resolved = _EXPLICIT_RESOLUTIONS.get(backlog_dir)
            if resolved is None:
                if backlog_dir.name != ".taskmaster":
                    raise LegacyLayoutError(
                        f"backlog lives at {backlog_dir}; the store is only ever at "
                        f"<project root>/.taskmaster/local/store.db. Run "
                        f"backlog_canonicalize_layout first to move it."
                    )
                checkout_root = _git_checkout_root(backlog_dir.parent)
                common_root = (
                    _git_common_root(backlog_dir.parent)
                    if checkout_root == backlog_dir.parent
                    else None
                )
                if common_root is not None:
                    resolved = RootResolution(
                        root=common_root,
                        backlog_path=common_root / ".taskmaster",
                        source="git-common-dir",
                        filesystem_warning=_cloud_filesystem_reason(common_root),
                    )
                else:
                    resolved = RootResolution(
                        root=backlog_dir.parent,
                        backlog_path=backlog_dir,
                        source="explicit",
                        filesystem_warning=_cloud_filesystem_reason(backlog_dir.parent),
                    )
                _EXPLICIT_RESOLUTIONS[backlog_dir] = resolved
        elif _ROOT_RESOLUTION is not None:
            resolved = _ROOT_RESOLUTION
        else:
            resolved = resolve_root(explicit_root=root) if root is not None else resolve_root()
            _ROOT_RESOLUTION = resolved
        if _ROOT_RESOLUTION is None:
            _ROOT_RESOLUTION = resolved

        forward = _projection_schema(resolved.backlog_path)
        if forward is not None and forward > PROJECTION_SCHEMA:
            raise RuntimeError(
                f"projection schema {forward} is newer than supported schema "
                f"{PROJECTION_SCHEMA}; upgrade Taskmaster"
            )

        path = db_path(resolved.backlog_path)
        instance = _STORES.get(path)
        if instance is None:
            instance = Store(resolved, session=session)
            _STORES[path] = instance
        instance._ensure_open()
        return instance


def close_thread_connection() -> None:
    registry = getattr(_THREAD_STATE, "registry", None)
    if registry is None:
        return
    registry.finalizer()
    delattr(_THREAD_STATE, "registry")


def checkpoint_all() -> None:
    for instance in list(_STORES.values()):
        if _network_filesystem_reason(getattr(instance, "root", instance.db_path.parent)):
            continue
        if not instance.db_path.exists():
            continue
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{instance.db_path.as_posix()}?mode=rw",
                timeout=0,
                isolation_level=None,
                check_same_thread=False,
                uri=True,
            )
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass


def reset_for_tests() -> None:
    global _ROOT_RESOLUTION, _CONTEXT_BUILDER, _PROGRESS_RENDERER
    with _STATE_LOCK:
        seen: set[int] = set()
        for connection in list(_ALL_CONNECTIONS):
            if id(connection) in seen:
                continue
            seen.add(id(connection))
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            try:
                connection.close()
            except sqlite3.Error:
                pass
        _ALL_CONNECTIONS.clear()
        _CONNECTION_IDENTITIES.clear()
        registry = getattr(_THREAD_STATE, "registry", None)
        if registry is not None:
            registry.finalizer.detach()
            delattr(_THREAD_STATE, "registry")
        if hasattr(_ACTIVE_DICT, "value"):
            delattr(_ACTIVE_DICT, "value")
        _ROOT_RESOLUTION = None
        _EXPLICIT_RESOLUTIONS.clear()
        _STORES.clear()
        _CACHE.clear()
        _WARNED_CLOUD_ROOTS.clear()
        _CONTEXT_BUILDER = None
        _PROGRESS_RENDERER = None


def configure_derivers(
    *,
    context_builder: Callable[[dict[str, Any]], None] | None = None,
    progress_renderer: Callable[[dict[str, Any], str], str] | None = None,
) -> None:
    """Install server-owned pure derivation callbacks.

    ``store.py`` cannot import ``backlog_server`` without a cycle.  Step 2
    supplies the existing context/dashboard logic through this narrow seam;
    the store retains ownership of throttling and the actual local file write.
    """
    global _CONTEXT_BUILDER, _PROGRESS_RENDERER
    _CONTEXT_BUILDER = context_builder
    _PROGRESS_RENDERER = progress_renderer


def load_dict(backlog_path: Path | None = None) -> dict[str, Any]:
    active = getattr(_ACTIVE_DICT, "value", None)
    if active is not None:
        active_path, data, _tx = active
        if backlog_path is None or db_path(backlog_path) == active_path:
            return data
    return open_store(backlog_path=backlog_path).load_dict()


def active_transaction(backlog_path: Path | None = None) -> "Transaction | None":
    """The `Transaction` behind the thread's open `transaction_dict`, if any.

    The compatibility dict cannot express a removal — a key deleted from
    ``epics``/``phases``/a task list is ignored by the write-back — so a caller
    that needs to archive or delete an entity has to reach the transaction and
    say so explicitly.  Returns ``None`` outside a transaction.
    """
    active = getattr(_ACTIVE_DICT, "value", None)
    if active is None:
        return None
    active_path, _data, tx = active
    if backlog_path is None or db_path(backlog_path) == active_path:
        return tx
    return None


@contextmanager
def transaction(
    *, tool: str, backlog_path: Path | None = None
) -> Iterator["Transaction"]:
    with open_store(backlog_path=backlog_path).transaction(tool=tool) as tx:
        yield tx


@contextmanager
def transaction_dict(
    *, tool: str, backlog_path: Path | None = None
) -> Iterator[dict[str, Any]]:
    with open_store(backlog_path=backlog_path).transaction_dict(tool=tool) as data:
        yield data


def status(backlog_path: Path | None = None) -> StoreStatus:
    return open_store(backlog_path=backlog_path).status()


class Store:
    def __init__(self, resolution: RootResolution, *, session: str | None = None):
        self.resolution = resolution
        self.root = resolution.root
        self.backlog_path = resolution.backlog_path
        self.db_path = db_path(self.backlog_path)
        self.session = session or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._owner_pid = os.getpid()
        self._bootstrapped = False
        self._connection_creation_state = threading.local()
        self._network_projection_only = False
        self._bootstrap_quarantine: dict[str, str] = {}
        self._last_progress_clock: float | None = None
        self._last_read_scan_clock: float | None = None

    def _git_generation(self) -> str:
        marker = self.root / ".git"
        git_dirs: list[Path] = []
        if marker.is_dir():
            git_dirs.append(marker)
        elif marker.is_file():
            try:
                line = marker.read_text(encoding="utf-8").strip()
                if line.startswith("gitdir:"):
                    candidate = Path(line.split(":", 1)[1].strip())
                    if not candidate.is_absolute():
                        candidate = marker.parent / candidate
                    git_dirs.append(_absolute(candidate))
            except OSError:
                pass
        common = self.root / ".git"
        if common.is_dir() and common not in git_dirs:
            git_dirs.append(common)
        digest = hashlib.sha1()
        for git_dir in git_dirs:
            for name in ("HEAD", "index", "ORIG_HEAD"):
                path = git_dir / name
                try:
                    content, stat = _read_file_snapshot(path)
                except OSError:
                    continue
                digest.update(str(path).encode("utf-8", "surrogatepass"))
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(str(stat.st_size).encode("ascii"))
                digest.update(content)
        return digest.hexdigest()

    def _ensure_process(self) -> None:
        """Invalidate state on a Store reference inherited across ``fork()``."""
        current = os.getpid()
        if current == self._owner_pid:
            return
        self._owner_pid = current
        self.session = f"{socket.gethostname()}-{current}-{uuid.uuid4().hex[:8]}"
        self._bootstrapped = False
        self._network_projection_only = False
        self._connection_creation_state = threading.local()
        self._last_progress_clock = None
        self._last_read_scan_clock = None

    @property
    def connection(self) -> sqlite3.Connection:
        self._ensure_process()
        connections = _connections()
        connection = connections.get(self.db_path)
        if connection is not None:
            try:
                stat = self.db_path.stat()
                if _CONNECTION_IDENTITIES.get(id(connection)) != (
                    stat.st_dev,
                    stat.st_ino,
                ):
                    raise sqlite3.ProgrammingError("store database generation changed")
                connection.execute("SELECT 1")
                return connection
            except (OSError, sqlite3.ProgrammingError):
                connections.pop(self.db_path, None)
                _CONNECTION_IDENTITIES.pop(id(connection), None)
                with _STATE_LOCK:
                    while connection in _ALL_CONNECTIONS:
                        _ALL_CONNECTIONS.remove(connection)
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
        network = _network_filesystem_reason(self.root)
        target: str | Path = self.db_path
        connect_options: dict[str, Any] = {}
        if network:
            target = f"file:{self.db_path.as_posix()}?mode=ro"
            connect_options["uri"] = True
        elif not getattr(self._connection_creation_state, "allowed", False):
            target = f"file:{self.db_path.as_posix()}?mode=rw"
            connect_options["uri"] = True
        connection = sqlite3.connect(
            target,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
            # Each connection still belongs to exactly one registry/thread.
            # Disabling sqlite's guard only lets the registry finalizer close
            # the handle safely if thread-local cleanup runs elsewhere.
            check_same_thread=False,
            **connect_options,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            if network:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA journal_mode=WAL").fetchone()
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            connection.close()
            raise
        connections[self.db_path] = connection
        _ALL_CONNECTIONS.append(connection)
        stat = self.db_path.stat()
        _CONNECTION_IDENTITIES[id(connection)] = (stat.st_dev, stat.st_ino)
        return connection

    @contextmanager
    def _connection_creation_allowed(self) -> Iterator[None]:
        prior = getattr(self._connection_creation_state, "allowed", False)
        self._connection_creation_state.allowed = True
        try:
            yield
        finally:
            self._connection_creation_state.allowed = prior

    @contextmanager
    def _writer_mutex(
        self, *, timeout_ms: int | None = None, diagnose: bool = True
    ) -> Iterator[None]:
        """Cross-process crash-recovery gate shared by every writer."""
        path = self.db_path.parent / "store.recovery.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        _OPEN_LOCK_FDS.add(descriptor)
        acquired = False
        wait_ms = BUSY_TIMEOUT_MS if timeout_ms is None else timeout_ms
        deadline = _MONOTONIC() + wait_ms / 1000
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, 13, 36}:
                        raise
                    if _MONOTONIC() >= deadline:
                        message = (
                            self._busy_diagnostic()
                            if diagnose
                            else "store busy for read-side projection scan; retry"
                        )
                        raise RuntimeError(message) from exc
                    time.sleep(0.02)
            yield
        finally:
            if acquired:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            _OPEN_LOCK_FDS.discard(descriptor)
            os.close(descriptor)

    def _ensure_open(self) -> None:
        self._ensure_process()
        network_reason = _network_filesystem_reason(self.root)
        if network_reason:
            if not self.db_path.exists():
                self._network_projection_only = True
                self._bootstrapped = True
                return
            try:
                connection = self.connection
                quick = connection.execute("PRAGMA quick_check").fetchone()
                required = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name IN ('meta','entities','changes','projection')"
                ).fetchone()[0]
                ready = bool(quick and str(quick[0]).lower() == "ok" and required == 4)
            except sqlite3.Error:
                ready = False
            self._network_projection_only = not ready
            self._bootstrapped = True
            return

        warning = self.resolution.filesystem_warning
        if warning and self.root not in _WARNED_CLOUD_ROOTS:
            warnings.warn(warning, RuntimeWarning, stacklevel=3)
            _WARNED_CLOUD_ROOTS.add(self.root)
        if self._bootstrapped:
            try:
                header = _read_sqlite_header(self.db_path)
            except OSError:
                header = b""
            if header != b"SQLite format 3\x00":
                with self._writer_mutex():
                    self._ensure_open_locked()
                return
            try:
                self.connection
            except sqlite3.OperationalError as exc:
                if "unable to open database file" not in str(exc).lower():
                    raise
                with self._writer_mutex():
                    self._ensure_open_locked()
            except sqlite3.DatabaseError as exc:
                if not _is_corruption(exc):
                    raise
                with self._writer_mutex():
                    self._ensure_open_locked()
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ignore = self.db_path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_bytes(b"*\n")
        with self._writer_mutex():
            self._ensure_open_locked()

    def _ensure_open_locked(self) -> None:
        database_existed = self.db_path.exists() and bool(self.db_path.stat().st_size)
        self._prune_corrupt_backups()
        if self.db_path.exists() and self.db_path.stat().st_size:
            try:
                header = _read_sqlite_header(self.db_path)
            except OSError:
                header = b"SQLite format 3\x00"
            if header != b"SQLite format 3\x00":
                self._recover_corrupt_database()
        try:
            with self._connection_creation_allowed():
                connection = self.connection
            self._begin_immediate(connection)
            try:
                self._prepare_schema(connection)
                self._recover_export_intents(connection)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            self._recover_corrupt_database(connection)
            with self._connection_creation_allowed():
                connection = self.connection
            self._begin_immediate(connection)
            try:
                self._prepare_schema(connection)
                self._recover_export_intents(connection)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        self._register_session(connection, current_tool=None)
        count = connection.execute(
            "SELECT COUNT(*) FROM entities WHERE deleted=0"
        ).fetchone()[0]
        if count == 0 and (not self._bootstrapped or not database_existed):
            self._bootstrap(connection)
        elif not self._reservation_path.exists():
            self._reserve_ids(
                (row["kind"], row["id"])
                for row in connection.execute("SELECT kind,id FROM entities")
            )
        self._bootstrapped = True

    def _prepare_schema(self, connection: sqlite3.Connection) -> None:
        try:
            quick = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError:
            raise
        if quick and str(quick[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"malformed database: quick_check={quick[0]}")

        has_meta = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if not has_meta:
            self._execute_schema(connection)
            connection.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('creation_token',?)",
                (str(uuid.uuid4()),),
            )
            return

        metadata = dict(connection.execute("SELECT key,value FROM meta"))
        version = int(metadata.get("schema_version", "0"))
        if version > SCHEMA_VERSION:
            self._rebuild_for_version(connection)
            return
        self._execute_schema(connection)
        if version < SCHEMA_VERSION:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
        if "creation_token" not in metadata:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('creation_token',?)",
                (str(uuid.uuid4()),),
            )

    @staticmethod
    def _execute_schema(connection: sqlite3.Connection) -> None:
        for statement in SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)

    def _rebuild_for_version(self, connection: sqlite3.Connection) -> None:
        # A schema incompatibility is not corruption. Rebuild in place so a
        # live Windows connection never has to rename its own database file.
        # Drop the virtual table first; its FTS shadow tables disappear with it.
        try:
            self._reserve_ids(
                (row["kind"], row["id"])
                for row in connection.execute("SELECT kind,id FROM entities")
            )
        except sqlite3.Error:
            pass
        connection.execute("DROP TABLE IF EXISTS entity_fts")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            connection.execute(f'DROP TABLE IF EXISTS "{table}"')
        self._execute_schema(connection)
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            "INSERT INTO meta(key,value) VALUES('creation_token',?)",
            (str(uuid.uuid4()),),
        )
        self._log("rebuilt store for unsupported schema version")

    @property
    def _reservation_path(self) -> Path:
        return self.db_path.parent / "id-reservations.json"

    def _load_reserved_ids(self) -> dict[str, set[str]]:
        try:
            raw = json.loads(self._reservation_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            return {
                str(kind): {str(ident) for ident in ids}
                for kind, ids in raw.items()
                if isinstance(ids, list)
            }
        except (OSError, ValueError, TypeError):
            return {}

    def _reserve_ids(self, keys: Iterator[tuple[str, str]] | list[tuple[str, str]] | set[tuple[str, str]]) -> None:
        materialized = {(str(kind), str(ident)) for kind, ident in keys}
        if not materialized:
            return
        reserved = self._load_reserved_ids()
        changed = False
        for kind, ident in materialized:
            bucket = reserved.setdefault(kind, set())
            if ident not in bucket:
                bucket.add(ident)
                changed = True
        if not changed:
            return
        payload = {
            kind: sorted(ids) for kind, ids in sorted(reserved.items())
        }
        path = self._reservation_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp.{self.session}")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def _flush_reservations(self, tx: "Transaction") -> None:
        self._reserve_ids(tx._reserved_keys)

    def _recover_export_intents(self, connection: sqlite3.Connection) -> None:
        for intent_path in sorted(self.db_path.parent.glob("export-intent.*.json")):
            try:
                payload = json.loads(intent_path.read_text(encoding="utf-8"))
                entries = payload.get("entries", {})
                if not isinstance(entries, dict):
                    raise ValueError("entries must be a mapping")
                for rel, entry in entries.items():
                    if not isinstance(entry, dict):
                        raise ValueError("intent entry must be a mapping")
                    row = connection.execute(
                        "SELECT content_hash FROM projection WHERE file=?", (rel,)
                    ).fetchone()
                    expected = entry.get("expected_hash")
                    committed = row is None if expected is None else bool(
                        row and row["content_hash"] == expected
                    )
                    if committed:
                        continue
                    path = self.backlog_path / Path(rel)
                    prior = entry.get("prior")
                    expected_bytes = entry.get("expected")
                    # Restore only bytes produced by the interrupted exporter.
                    # A different current hash is a hand edit made after the
                    # crash and must survive for the normal import scan.
                    if path.exists():
                        current_hash = hashlib.sha1(path.read_bytes()).hexdigest()
                        prior_hash = (
                            hashlib.sha1(base64.b64decode(prior)).hexdigest()
                            if prior is not None
                            else None
                        )
                        if expected is None:
                            if current_hash != prior_hash:
                                continue
                        elif current_hash != expected:
                            if expected_bytes is None:
                                # Compatibility with intents written before
                                # expected bytes were persisted.
                                continue
                            stamp = datetime.now(timezone.utc).strftime(
                                "%Y%m%dT%H%M%S%fZ"
                            )
                            conflict = path.with_name(
                                f"{path.name}.intent-conflict-{stamp}"
                            )
                            shutil.copy2(path, conflict)
                            self._log(
                                f"quarantined mixed crash recovery bytes as {conflict.name}"
                            )
                    if prior is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        restore = path.with_name(f"{path.name}.tmp.intent-recovery")
                        with restore.open("wb") as handle:
                            handle.write(base64.b64decode(prior))
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(restore, path)
                intent_path.unlink(missing_ok=True)
                self._log(f"recovered export intent {intent_path.name}")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._log(f"could not recover export intent {intent_path.name}: {exc!r}")

    def _persist_export_intent(self, tx: "Transaction") -> None:
        if not tx._intent_entries:
            return
        payload = {"session": tx.session, "entries": tx._intent_entries}
        path = tx._intent_path
        temp = path.with_name(f"{path.name}.tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def _remember_projection_change(
        self,
        tx: "Transaction",
        path: Path,
        prior: bytes | None,
        expected_hash: str | None,
        expected: bytes | None = None,
    ) -> None:
        tx._replaced_files.setdefault(path, prior)
        rel = path.relative_to(self.backlog_path).as_posix()
        tx._intent_entries.setdefault(
            rel,
            {
                "prior": None if prior is None else base64.b64encode(prior).decode("ascii"),
                "expected_hash": expected_hash,
                "expected": None
                if expected is None
                else base64.b64encode(expected).decode("ascii"),
            },
        )
        self._persist_export_intent(tx)

    def _recover_corrupt_database(
        self, connection: sqlite3.Connection | None = None
    ) -> None:
        close_thread_connection()
        try:
            self._rename_database_family("corrupt")
        except OSError as exc:
            self._backup_database_family("corrupt")
            try:
                header = _read_sqlite_header(self.db_path)
            except OSError:
                header = b""
            if header != b"SQLite format 3\x00":
                raise RuntimeError(
                    "corrupt SQLite store could not be renamed because another process "
                    "holds it; close Taskmaster processes and retry; forensic backup preserved"
                ) from exc
            rebuilt = self.connection
            self._begin_immediate(rebuilt)
            try:
                self._rebuild_for_version(rebuilt)
                rebuilt.commit()
            except BaseException:
                if rebuilt.in_transaction:
                    rebuilt.rollback()
                raise
        self._bootstrapped = False
        self._log("renamed corrupt database family and scheduled projection rebuild")

    def _backup_database_family(self, label: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                target = self.db_path.with_name(f"store.db.{label}-{stamp}{suffix}")
                shutil.copy2(candidate, target)

    def _rename_database_family(self, label: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.replace(self.db_path.with_name(f"store.db.{label}-{stamp}{suffix}"))

    def _prune_corrupt_backups(self) -> None:
        cutoff = time.time() - 7 * 24 * 60 * 60
        for candidate in self.db_path.parent.glob("store.db.corrupt-*"):
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except OSError:
                pass

    def _register_session(
        self,
        connection: sqlite3.Connection,
        *,
        current_tool: str | None,
        session_id: str | None = None,
    ) -> None:
        now = _now()
        connection.execute(
            "INSERT INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET "
            "pid=excluded.pid,host=excluded.host,last_seen=excluded.last_seen,"
            "cwd=excluded.cwd,current_tool=excluded.current_tool",
            (
                session_id or self.session,
                os.getpid(),
                socket.gethostname(),
                now,
                now,
                str(Path.cwd().resolve()),
                current_tool,
            ),
        )

    def _try_register_session(
        self,
        connection: sqlite3.Connection,
        *,
        current_tool: str | None,
        session_id: str | None = None,
    ) -> None:
        activity: sqlite3.Connection | None = None
        try:
            activity = sqlite3.connect(
                f"file:{self.db_path.as_posix()}?mode=rw",
                timeout=0,
                isolation_level=None,
                check_same_thread=False,
                uri=True,
            )
            activity.execute("PRAGMA busy_timeout=0")
            activity.row_factory = sqlite3.Row
            self._register_session(
                activity,
                current_tool=current_tool,
                session_id=session_id or f"{self.session}:t{threading.get_ident()}",
            )
        except sqlite3.OperationalError as exc:
            lowered = str(exc).lower()
            if not any(
                marker in lowered
                for marker in ("locked", "busy", "unable to open database file")
            ):
                raise
        finally:
            if activity is not None:
                try:
                    activity.close()
                except sqlite3.Error:
                    pass

    @contextmanager
    def _session_heartbeat(
        self, connection: sqlite3.Connection, *, tool: str, session_id: str
    ) -> Iterator[None]:
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                self._try_register_session(
                    connection, current_tool=tool, session_id=session_id
                )

        worker = threading.Thread(
            target=heartbeat,
            name=f"taskmaster-store-heartbeat-{os.getpid()}",
            daemon=True,
        )
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=1.0)

    def _bootstrap(self, connection: sqlite3.Connection) -> None:
        original_version = SCHEMA_V4
        backlog_file = self.backlog_path / "backlog.yaml"
        if backlog_file.exists():
            try:
                raw = yaml_io.safe_load(backlog_file.read_text(encoding="utf-8")) or {}
                _validate_backlog_document(raw)
                original_version = detect_schema_version(raw)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                original_version = SCHEMA_V4
        committed = False
        self._begin_immediate(connection)
        try:
            tx = Transaction(self, connection, tool="bootstrap")
            self._import_projection(tx, full=True)
            self._refresh_derived(tx)
            if original_version < SCHEMA_V4:
                tx._export_keys.update(
                    (row["kind"], row["id"])
                    for row in connection.execute(
                        "SELECT kind,id FROM entities WHERE kind IN ('task','epic','phase') "
                        "AND deleted=0"
                    )
                )
                tx._export_backlog = True
                self._export_touched(tx)
            else:
                self._export_backlog(tx, force=True)
            self._flush_reservations(tx)
            tx._capture_committed()
            connection.commit()
            committed = True
            tx._finish_committed()
        except BaseException:
            if not committed:
                tx._restore_replaced()
                if connection.in_transaction:
                    connection.rollback()
            raise
        self._checkpoint_passive(connection)

    @contextmanager
    def transaction(
        self,
        *,
        tool: str,
        _writer_timeout_ms: int | None = None,
        _diagnose_busy: bool = True,
    ) -> Iterator["Transaction"]:
        self._ensure_open()
        reason = _network_filesystem_reason(self.root)
        if reason:
            raise RuntimeError(reason)
        connection = self.connection
        if connection.in_transaction:
            raise RuntimeError("nested store transactions are not supported")
        tx = Transaction(self, connection, tool=tool)
        committed = False
        activity_session = f"{self.session}:t{threading.get_ident()}"
        try:
            self._try_register_session(
                connection, current_tool=tool, session_id=activity_session
            )
            with self._session_heartbeat(
                connection, tool=tool, session_id=activity_session
            ):
                with self._writer_mutex(
                    timeout_ms=_writer_timeout_ms, diagnose=_diagnose_busy
                ):
                    current_connection = self.connection
                    if current_connection is not connection:
                        connection = current_connection
                        if connection.in_transaction:
                            raise RuntimeError("nested store transactions are not supported")
                        tx = Transaction(self, connection, tool=tool)
                        self._try_register_session(
                            connection,
                            current_tool=tool,
                            session_id=activity_session,
                        )
                    self._recover_export_intents(connection)
                    self._begin_immediate(connection)
                    self._scan_projection(tx)
                    self._drain_dirty(tx)
                    yield tx
                    self._refresh_derived(tx)
                    self._export_touched(tx)
                    self._regenerate_progress_if_due(tx)
                    self._flush_reservations(tx)
                    tx._capture_committed()
                    connection.commit()
                    committed = True
            tx._finish_committed()
        except BaseException:
            if not committed:
                tx._restore_replaced()
                if connection.in_transaction:
                    connection.rollback()
            raise
        finally:
            if not committed and connection.in_transaction:
                connection.rollback()
            try:
                self._try_register_session(
                    connection, current_tool=None, session_id=activity_session
                )
            except sqlite3.Error:
                pass
        self._checkpoint_passive(connection)

    @contextmanager
    def transaction_dict(self, *, tool: str) -> Iterator[dict[str, Any]]:
        with self.transaction(tool=tool) as tx:
            data = self._load_cached_dict_from_connection(tx.connection, publish=False)
            snapshot = copy.deepcopy(data)
            prior = getattr(_ACTIVE_DICT, "value", None)
            _ACTIVE_DICT.value = (self.db_path, data, tx)
            try:
                yield data
                self._apply_dict_diff(tx, snapshot, data)
            finally:
                if prior is None:
                    if hasattr(_ACTIVE_DICT, "value"):
                        delattr(_ACTIVE_DICT, "value")
                else:
                    _ACTIVE_DICT.value = prior

    def load_dict(self) -> dict[str, Any]:
        return self.load_dict_with_identity()[0]

    def load_dict_with_identity(self) -> tuple[dict[str, Any], str, int]:
        """The compatibility dict plus `(creation_token, max_seq)` for its snapshot.

        A caller that stamps an ETag on what it just read has to take both from
        one snapshot; reading the payload and the identity separately let a
        concurrent commit slip between them, so an old payload could be served
        under a newer revision and a following edit would overwrite the newer
        state while its precondition still matched.
        """
        self._ensure_open()
        active = getattr(_ACTIVE_DICT, "value", None)
        if active is not None and active[0] == self.db_path:
            connection = active[2].connection
            token = connection.execute(
                "SELECT value FROM meta WHERE key='creation_token'"
            ).fetchone()[0]
            max_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM changes"
                ).fetchone()[0]
            )
            return active[1], token, max_seq
        if self._network_projection_only:
            data = self._bootstrap_backlog_data()
            data.setdefault("context", {})
            if _CONTEXT_BUILDER is not None:
                _CONTEXT_BUILDER(data)
            return data, "", 0
        self._maybe_scan_on_read()
        connection = self.connection
        owns_snapshot = not connection.in_transaction
        if owns_snapshot:
            connection.execute("BEGIN")
        try:
            token = connection.execute(
                "SELECT value FROM meta WHERE key='creation_token'"
            ).fetchone()[0]
            max_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM changes"
                ).fetchone()[0]
            )
            data = self._load_cached_dict_from_connection(connection)
            if owns_snapshot:
                connection.commit()
        except BaseException:
            if owns_snapshot and connection.in_transaction:
                connection.rollback()
            raise
        return data, token, max_seq

    def _load_cached_dict_from_connection(
        self, connection: sqlite3.Connection, *, publish: bool = True
    ) -> dict[str, Any]:
        token = connection.execute(
            "SELECT value FROM meta WHERE key='creation_token'"
        ).fetchone()[0]
        max_seq = int(
            connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
        )
        cached = _CACHE.get(self.db_path)
        if cached and cached[0] == token:
            if cached[1] == max_seq:
                return copy.deepcopy(cached[2])
            data = self._refresh_cached_dict(connection, cached[2], cached[1])
        else:
            data = self._load_dict_from_connection(connection)
        if publish:
            _CACHE[self.db_path] = (token, max_seq, copy.deepcopy(data))
        return copy.deepcopy(data)

    def _refresh_cached_dict(
        self,
        connection: sqlite3.Connection,
        cached_data: Mapping[str, Any],
        cached_seq: int,
    ) -> dict[str, Any]:
        data = copy.deepcopy(dict(cached_data))
        data["context"] = {}
        data.setdefault("_orphan_tasks", [])
        rows = connection.execute(
            "SELECT kind,id,epic,deleted,doc,body,updated_seq FROM entities "
            "WHERE updated_seq>? ORDER BY updated_seq",
            (cached_seq,),
        ).fetchall()
        for row in rows:
            kind = row["kind"]
            ident = row["id"]
            doc = _from_json(row["doc"], {})
            if row["body"]:
                doc[BODY_KEY] = row["body"]
            if kind == "backlog":
                epics = data.get("epics") or []
                phases = data.get("phases") or []
                orphans = data.get("_orphan_tasks") or []
                data = copy.deepcopy(doc) if not row["deleted"] else {}
                data["epics"] = epics
                data["phases"] = phases
                data["_orphan_tasks"] = orphans
                data["context"] = {}
                continue
            if kind == "epic":
                epics = data.setdefault("epics", [])
                existing = next(
                    (index for index, epic in enumerate(epics) if epic.get("id") == ident),
                    None,
                )
                tasks = epics[existing].get("tasks", []) if existing is not None else []
                if existing is not None:
                    epics.pop(existing)
                if not row["deleted"]:
                    doc["tasks"] = tasks
                    epics.append(doc)
                continue
            if kind == "phase":
                phases = data.setdefault("phases", [])
                phases[:] = [phase for phase in phases if phase.get("id") != ident]
                if not row["deleted"]:
                    phases.append(doc)
                continue
            if kind != "task":
                continue
            for epic in data.get("epics") or []:
                epic["tasks"] = [
                    task for task in epic.get("tasks", []) if task.get("id") != ident
                ]
            data["_orphan_tasks"] = [
                item for item in data.get("_orphan_tasks") or [] if item != ident
            ]
            if row["deleted"]:
                continue
            epic = next(
                (
                    candidate
                    for candidate in data.get("epics") or []
                    if candidate.get("id") == (row["epic"] or doc.get("epic"))
                ),
                None,
            )
            if epic is None:
                data["_orphan_tasks"].append(ident)
            else:
                epic.setdefault("tasks", []).append(doc)
                epic["tasks"].sort(
                    key=lambda task: (
                        float(task.get("order", 0.0)),
                        str(task.get("id", "")),
                    )
                )
        if _CONTEXT_BUILDER is not None:
            _CONTEXT_BUILDER(data)
        return data

    def force_scan_on_next_read(self) -> None:
        """Drop the read-scan throttle so the next read re-imports hand edits."""
        self._last_read_scan_clock = None

    def linear_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """The oldest pending Linear pushes, `limit` at most, oldest first."""
        self._ensure_open()
        return [
            {
                "seq": int(row["seq"]),
                "op": row["op"],
                "target_id": row["target_id"],
                "tracker_id": row["tracker_id"],
                "payload": _from_json(row["payload"], None),
                "state": row["state"],
                "attempts": int(row["attempts"] or 0),
                "last_error": row["last_error"],
            }
            for row in self.connection.execute(
                "SELECT seq,op,target_id,tracker_id,payload,state,attempts,last_error "
                "FROM linear_queue WHERE state='pending' ORDER BY seq LIMIT ?",
                (limit,),
            )
        ]

    def linear_mark(self, seq: int, *, state: str, error: str | None = None) -> None:
        """Record the outcome of one drain attempt on queue row `seq`.

        Its own short `BEGIN IMMEDIATE` so a drain that spends seconds in HTTP
        never holds the writer lock across a round-trip.  The store keeps no
        retry policy: `attempts` counts marks, the caller chooses the state.
        """
        self._ensure_open()
        if self.connection.in_transaction:
            raise RuntimeError("linear_mark needs its own transaction")
        with self._writer_mutex():
            # Re-read under the mutex, as `transaction` does: a recovery that
            # finished while this caller queued for the lock closes the handle
            # we would otherwise have captured before waiting.
            connection = self.connection
            if connection.in_transaction:
                raise RuntimeError("linear_mark needs its own transaction")
            self._begin_immediate(connection)
            try:
                cursor = connection.execute(
                    "UPDATE linear_queue SET state=?,last_error=?,attempts=attempts+1 "
                    "WHERE seq=?",
                    (state, error, seq),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"linear queue row {seq} not found")
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def _import_linear_queue(self, tx: "Transaction") -> None:
        """Adopt a legacy `integrations/linear-queue.json` into `linear_queue`.

        Called from bootstrap and from every scan, because a project migrated
        before the table existed still has its pending pushes on disk.  Removing
        the file inside the caller's transaction is what makes this idempotent.
        """
        path = self.backlog_path / _LINEAR_QUEUE_REL
        if not path.exists():
            return
        try:
            prior = path.read_bytes()
            raw = json.loads(prior.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            tx.warnings.append(f"linear queue import failed: {exc}")
            tx.log_entries.append(f"linear queue import failed: {exc!r}")
            return
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op") or "")
            target_id = str(item.get("target_id") or "")
            if not op or not target_id:
                continue
            payload = {
                key: value
                for key, value in item.items()
                if key not in {"op", "target_id", "tracker_id", "attempts", "last_error"}
            }
            seq = tx.linear_enqueue(op, target_id, item.get("tracker_id"), payload or None)
            attempts = int(item.get("attempts") or 0)
            last_error = item.get("last_error")
            if attempts or last_error:
                tx.connection.execute(
                    "UPDATE linear_queue SET attempts=?,last_error=? WHERE seq=?",
                    (attempts, last_error, seq),
                )
        # Not a projection file, so it carries no export intent; the in-process
        # rollback path restores it if this transaction never commits.
        tx._replaced_files.setdefault(path, prior)
        path.unlink()
        tx.log_entries.append(f"imported and removed {_LINEAR_QUEUE_REL}")

    def _maybe_scan_on_read(self) -> None:
        """Import hand edits at most once per two seconds for read callers."""
        now = time.monotonic()
        if (
            self._last_read_scan_clock is not None
            and now - self._last_read_scan_clock < 2.0
        ):
            return
        self._last_read_scan_clock = now
        if _network_filesystem_reason(self.root):
            return
        if not self._projection_changed_on_disk():
            return
        try:
            connection = self.connection
            connection.execute("PRAGMA busy_timeout=0")
            try:
                with self.transaction(
                    tool="projection-read-import",
                    _writer_timeout_ms=0,
                    _diagnose_busy=False,
                ):
                    pass
            finally:
                connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        except RuntimeError as exc:
            # A read never waits behind a writer merely to ingest projection
            # edits.  The next read/transaction will retry the scan.
            if not str(exc).startswith("store busy for "):
                raise

    def _projection_changed_on_disk(self) -> bool:
        connection = self.connection
        rows = self.connection.execute(
            "SELECT file,content_hash,mtime,size,quarantined FROM projection"
        ).fetchall()
        if any(row["quarantined"] for row in rows):
            return True
        generation_row = connection.execute(
            "SELECT value FROM meta WHERE key='last_scan_generation'"
        ).fetchone()
        force_hash = not generation_row or generation_row[0] != self._git_generation()
        known = {row["file"]: row["content_hash"] for row in rows}
        by_rel = {row["file"]: row for row in rows}
        actual: dict[str, Path] = {
            path.relative_to(self.backlog_path).as_posix(): path
            for _kind, _ident, path in self._known_entity_files()
        }
        for name in ("backlog.yaml", "project.yaml"):
            path = self.backlog_path / name
            if path.exists():
                actual[name] = path
        if set(known) != set(actual):
            return True
        for rel, path in actual.items():
            try:
                row = by_rel[rel]
                stat = path.stat()
                candidate = (
                    force_hash
                    or rel in {"backlog.yaml", "project.yaml"}
                    or stat.st_mtime != row["mtime"]
                    or stat.st_size != row["size"]
                )
                if candidate and hashlib.sha1(path.read_bytes()).hexdigest() != known[rel]:
                    return True
            except OSError:
                return True
        return False

    def status(self) -> StoreStatus:
        self._ensure_open()
        network_reason = _network_filesystem_reason(self.root)
        if self._network_projection_only:
            projection_schema = _projection_schema(self.backlog_path)
            return StoreStatus(
                root=self.root,
                db_path=self.db_path,
                creation_token="",
                max_seq=0,
                dirty_files=(),
                quarantined_files=(),
                resolution_source=self.resolution.source,
                schema_version=0,
                db_size=self.db_path.stat().st_size if self.db_path.exists() else 0,
                wal_size=0,
                warning=network_reason or self.resolution.filesystem_warning,
                corrupt_files=tuple(
                    sorted(
                        path.name
                        for path in self.db_path.parent.glob("store.db.corrupt-*")
                    )
                )
                if self.db_path.parent.exists()
                else (),
            )
        connection = self.connection
        owns_snapshot = not connection.in_transaction
        if owns_snapshot:
            connection.execute("BEGIN")
        try:
            token_row = connection.execute(
                "SELECT value FROM meta WHERE key='creation_token'"
            ).fetchone()
            dirty = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT file FROM projection WHERE dirty=1 ORDER BY file"
                )
            )
            quarantined = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT file FROM projection WHERE quarantined=1 ORDER BY file"
                )
            )
            seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM changes"
                ).fetchone()[0]
            )
            schema_row = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            recent = tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT seq,ts,session,tool,kind,id,op,fields,before,after "
                    "FROM changes ORDER BY seq DESC LIMIT 20"
                )
            )
            cutoff_60s = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            live = tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT session,pid,host,started,last_seen,cwd,current_tool "
                    "FROM sessions WHERE last_seen>=? OR current_tool IS NOT NULL "
                    "ORDER BY last_seen DESC",
                    (cutoff_60s,),
                )
                if row["last_seen"] >= cutoff_60s
                or (
                    row["current_tool"]
                    and row["host"] == socket.gethostname()
                    and _local_pid_alive(row["pid"])
                )
            )
            cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            merge_conflicts = int(
                connection.execute(
                    "SELECT COUNT(*) FROM changes WHERE op='merge' AND ts>=? "
                    "AND before LIKE '%\"_conflicts\"%'",
                    (cutoff_24h,),
                ).fetchone()[0]
            )
            if owns_snapshot:
                connection.commit()
        except BaseException:
            if owns_snapshot and connection.in_transaction:
                connection.rollback()
            raise
        return StoreStatus(
            root=self.root,
            db_path=self.db_path,
            creation_token=token_row[0],
            max_seq=seq,
            dirty_files=dirty,
            quarantined_files=quarantined,
            resolution_source=self.resolution.source,
            schema_version=int(schema_row[0]) if schema_row else 0,
            db_size=self.db_path.stat().st_size if self.db_path.exists() else 0,
            wal_size=Path(f"{self.db_path}-wal").stat().st_size
            if Path(f"{self.db_path}-wal").exists()
            else 0,
            recent_changes=recent,
            live_sessions=live,
            merge_conflicts_24h=merge_conflicts,
            warning=network_reason or self.resolution.filesystem_warning,
            corrupt_files=tuple(
                sorted(path.name for path in self.db_path.parent.glob("store.db.corrupt-*"))
            ),
        )

    def _begin_immediate(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            lowered = str(exc).lower()
            if "locked" not in lowered and "busy" not in lowered:
                raise
            raise RuntimeError(self._busy_diagnostic()) from exc

    def _busy_diagnostic(self) -> str:
        seconds = BUSY_TIMEOUT_MS / 1000
        prefix = f"store busy for {seconds:g}s"
        diagnostic: sqlite3.Connection | None = None
        try:
            diagnostic = sqlite3.connect(
                f"file:{self.db_path.as_posix()}?mode=rw",
                timeout=2.0,
                isolation_level=None,
                uri=True,
            )
            diagnostic.row_factory = sqlite3.Row
            diagnostic.execute("PRAGMA busy_timeout=2000")
            cutoff_iso = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - 60, timezone.utc
            ).isoformat()
            holders = diagnostic.execute(
                "SELECT session,current_tool,pid,host,last_seen FROM sessions "
                "WHERE last_seen>=? OR current_tool IS NOT NULL "
                "ORDER BY last_seen DESC LIMIT 10",
                (cutoff_iso,),
            ).fetchall()
            holders = [
                row
                for row in holders
                if row["last_seen"] >= cutoff_iso
                or (
                    row["current_tool"]
                    and row["host"] == socket.gethostname()
                    and _local_pid_alive(row["pid"])
                )
            ]
            writer = diagnostic.execute(
                "SELECT session,tool,seq FROM changes ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            return f"{prefix}; could not read session table; retry"
        finally:
            if diagnostic is not None:
                try:
                    diagnostic.close()
                except sqlite3.Error:
                    pass
        parts: list[str] = []
        if holders:
            parts.append(
                "probable holders: "
                + ", ".join(
                    f"{row['session']} ({row['current_tool'] or 'idle'})" for row in holders
                )
            )
        if writer:
            parts.append(
                f"last committed writer: {writer['session']} ({writer['tool']}, seq {writer['seq']})"
            )
        return prefix + ("; " + "; ".join(parts) if parts else "; retry")

    def rebuild_derived(self) -> None:
        connection = self.connection
        if connection.in_transaction:
            raise RuntimeError("nested store transactions are not supported")
        tx = Transaction(self, connection, tool="rebuild-derived")
        try:
            self._try_register_session(connection, current_tool="rebuild-derived")
            with self._writer_mutex():
                self._begin_immediate(connection)
                rows = tx.connection.execute(
                    "SELECT kind,id,doc,body FROM entities WHERE deleted=0"
                ).fetchall()
                for row in rows:
                    tx._derived_keys.add((row["kind"], row["id"]))
                self._refresh_derived(tx)
                connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            try:
                self._try_register_session(connection, current_tool=None)
            except sqlite3.Error:
                pass

    def _load_dict_from_connection(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            "SELECT doc FROM entities WHERE kind='backlog' AND id=? AND deleted=0",
            (_BACKLOG_ID,),
        ).fetchone()
        data: dict[str, Any] = _from_json(row[0], {}) if row else {}
        epics: list[dict[str, Any]] = []
        by_epic: dict[str, dict[str, Any]] = {}
        for item in connection.execute(
            "SELECT id,doc,body FROM entities WHERE kind='epic' AND deleted=0 "
            "ORDER BY rowid"
        ):
            epic = _from_json(item["doc"], {})
            if item["body"]:
                epic[BODY_KEY] = item["body"]
            epic["tasks"] = []
            epics.append(epic)
            by_epic[item["id"]] = epic
        orphan_ids: list[str] = []
        for item in connection.execute(
            "SELECT id,epic,doc,body FROM entities WHERE kind='task' AND deleted=0 "
            "ORDER BY epic,id"
        ):
            task = _from_json(item["doc"], {})
            if item["body"]:
                task[BODY_KEY] = item["body"]
            epic = by_epic.get(item["epic"] or task.get("epic"))
            if epic is None:
                orphan_ids.append(item["id"])
            else:
                epic["tasks"].append(task)
        for epic in epics:
            epic["tasks"].sort(key=lambda task: (float(task.get("order", 0.0)), str(task.get("id", ""))))
        phases: list[dict[str, Any]] = []
        for item in connection.execute(
            "SELECT doc,body FROM entities WHERE kind='phase' AND deleted=0 ORDER BY rowid"
        ):
            phase = _from_json(item["doc"], {})
            if item["body"]:
                phase[BODY_KEY] = item["body"]
            phases.append(phase)
        data["epics"] = epics
        data["phases"] = phases
        data["context"] = {}
        data["_orphan_tasks"] = orphan_ids
        if _CONTEXT_BUILDER is not None:
            _CONTEXT_BUILDER(data)
        return data

    def _apply_dict_diff(
        self, tx: "Transaction", before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        before_rows = _flatten_backlog_dict(before)
        after_rows = _flatten_backlog_dict(after)
        for key, (doc, body) in after_rows.items():
            if key not in before_rows:
                tx.create(key[0], doc, body=body, requested_id=key[1])
                continue
            old_doc, old_body = before_rows[key]
            if old_doc != doc or old_body != body:
                materialized = copy.deepcopy(doc)
                if body:
                    materialized[BODY_KEY] = body
                tx.put(key[0], key[1], materialized)

    def _bootstrap_backlog_data(self) -> dict[str, Any]:
        path = self.backlog_path / "backlog.yaml"
        if not path.exists():
            return {"version": 4, "meta": {"schema_version": 4}, "epics": [], "phases": []}
        try:
            raw = yaml_io.safe_load(path.read_text(encoding="utf-8")) or {}
            _validate_backlog_document(raw)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            return {"version": 4, "meta": {"schema_version": 4}, "epics": [], "phases": []}
        version = detect_schema_version(raw)
        if version >= SCHEMA_V4:
            self._bootstrap_quarantine.clear()
            data = load_v4(path)
            original_epics = {
                str(item.get("id")): copy.deepcopy(item)
                for item in raw.get("epics") or []
                if item.get("id")
            }
            original_phases = {
                str(item.get("id")): copy.deepcopy(item)
                for item in raw.get("phases") or []
                if item.get("id")
            }
            for kind, ident, entity_path in self._known_entity_files():
                if kind not in {"epic", "phase"}:
                    continue
                rel = entity_path.relative_to(self.backlog_path).as_posix()
                try:
                    doc, _body = self._parse_entity_file(kind, entity_path)
                    self._validate_projected_identity(kind, ident, doc)
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    self._bootstrap_quarantine[rel] = str(exc)
                    collection = data.get("epics" if kind == "epic" else "phases") or []
                    original = (original_epics if kind == "epic" else original_phases).get(ident)
                    for index, item in enumerate(collection):
                        if str(item.get("id")) == ident and original is not None:
                            collection[index] = copy.deepcopy(original)
                            break

            by_epic = {
                str(epic.get("id")): epic for epic in data.get("epics") or [] if epic.get("id")
            }
            for epic in by_epic.values():
                epic["tasks"] = []
            orphans: list[str] = []
            for kind, ident, task_path in self._known_entity_files():
                if kind != "task":
                    continue
                rel = task_path.relative_to(self.backlog_path).as_posix()
                try:
                    task, body = self._parse_entity_file(kind, task_path)
                    self._validate_projected_identity(kind, ident, task)
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    self._bootstrap_quarantine[rel] = str(exc)
                    continue
                if body:
                    task[BODY_KEY] = body
                if _is_archive_path(task_path, self.backlog_path):
                    task["archived"] = True
                epic = by_epic.get(str(task.get("epic") or ""))
                if epic is None:
                    orphans.append(ident)
                else:
                    epic["tasks"].append(task)
            for epic in by_epic.values():
                epic["tasks"].sort(
                    key=lambda task: (
                        float(task.get("order", 0.0)),
                        str(task.get("id", "")),
                    )
                )
            data["_orphan_tasks"] = orphans
            return data
        if version >= SCHEMA_V3:
            return load_v3(path)
        return raw

    def _import_projection(self, tx: "Transaction", *, full: bool = False) -> None:
        backlog_file = self.backlog_path / "backlog.yaml"
        data = self._bootstrap_backlog_data()
        # A pre-v4 projection splits a task across backlog.yaml (slim fields)
        # and tasks/<id>.md (heavy fields only). `_bootstrap_backlog_data`
        # already merged the two halves, so the per-file import below must not
        # replace that row with the heavy-only document.
        legacy_projection = detect_schema_version(data) < SCHEMA_V4
        if legacy_projection:
            data["version"] = SCHEMA_V4
            data.setdefault("meta", {})["schema_version"] = SCHEMA_V4
            for epic in data.get("epics") or []:
                for order, task in enumerate(epic.get("tasks") or [], start=1):
                    task.setdefault("epic", epic.get("id"))
                    task.setdefault("order", float(order))
        self._import_linear_queue(tx)
        for key, (doc, body) in _flatten_backlog_dict(data).items():
            tx._import_row(key[0], key[1], doc, body)

        if backlog_file.exists():
            backlog_content, backlog_stat = _read_file_snapshot(backlog_file)
            self._record_projection_bytes(
                tx.connection,
                "backlog.yaml",
                "backlog",
                None,
                backlog_content,
                backlog_stat,
            )
            try:
                parsed_backlog = yaml_io.safe_load(backlog_content.decode("utf-8")) or {}
                _validate_backlog_document(parsed_backlog)
            except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                tx.connection.execute(
                    "UPDATE projection SET quarantined=1 WHERE file='backlog.yaml'"
                )
                tx.warnings.append(f"quarantined backlog.yaml: {exc}")
                tx.log_entries.append(f"quarantined backlog.yaml: {exc}")
        for kind, ident, path in self._known_entity_files():
            if kind in {"task", "epic", "phase"}:
                rel = path.relative_to(self.backlog_path).as_posix()
                quarantine_error = self._bootstrap_quarantine.get(rel)
                if path.exists():
                    content, stat = _read_file_snapshot(path)
                    if quarantine_error is None:
                        try:
                            parsed_doc, parsed_body = self._parse_entity_text(
                                kind, content.decode("utf-8")
                            )
                            self._validate_projected_identity(kind, ident, parsed_doc)
                            if kind == "task" and legacy_projection:
                                current = tx.connection.execute(
                                    "SELECT doc FROM entities WHERE kind=? AND id=?",
                                    (kind, ident),
                                ).fetchone()
                                if current:
                                    merged = _from_json(current["doc"], {})
                                    merged.update(parsed_doc)
                                    parsed_doc = merged
                            if kind in {"epic", "phase"}:
                                current = tx.connection.execute(
                                    "SELECT doc FROM entities WHERE kind=? AND id=?",
                                    (kind, ident),
                                ).fetchone()
                                merged = _from_json(current["doc"], {}) if current else {}
                                heavy_fields = (
                                    EPIC_HEAVY_FIELDS if kind == "epic" else PHASE_HEAVY_FIELDS
                                )
                                for field in heavy_fields:
                                    if field in parsed_doc:
                                        merged[field] = parsed_doc[field]
                                    else:
                                        merged.pop(field, None)
                                parsed_doc = merged
                            if _is_archive_path(path, self.backlog_path):
                                parsed_doc["archived"] = True
                            tx._import_row(kind, ident, parsed_doc, parsed_body)
                        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                            quarantine_error = str(exc)
                    self._record_projection_bytes(
                        tx.connection,
                        path.relative_to(self.backlog_path).as_posix(),
                        kind,
                        ident,
                        content,
                        stat,
                    )
                    if quarantine_error is not None:
                        tx.connection.execute(
                            "UPDATE projection SET quarantined=1 WHERE file=?", (rel,)
                        )
                        tx.warnings.append(f"quarantined {rel}: {quarantine_error}")
                        tx.log_entries.append(f"quarantined {rel}: {quarantine_error}")
                continue
            try:
                content, stat = _read_file_snapshot(path)
                doc, body = self._parse_entity_text(kind, content.decode("utf-8"))
                self._validate_projected_identity(kind, ident, doc)
            except (OSError, ValueError, yaml.YAMLError):
                continue
            if _is_archive_path(path, self.backlog_path):
                doc["archived"] = True
            tx._import_row(kind, ident, doc, body)
            self._record_projection_bytes(
                tx.connection,
                path.relative_to(self.backlog_path).as_posix(),
                kind,
                ident,
                content,
                stat,
            )

        project = self.backlog_path / "project.yaml"
        if project.exists():
            project_content, project_stat = _read_file_snapshot(project)
            project_doc = yaml_io.safe_load(project_content.decode("utf-8")) or {}
            tx._import_row("project", _PROJECT_ID, project_doc, None)
            self._record_projection_bytes(
                tx.connection,
                "project.yaml",
                "project",
                _PROJECT_ID,
                project_content,
                project_stat,
            )

    def _known_entity_files(self) -> list[tuple[str, str, Path]]:
        specs = (
            ("task", ("tasks/*.md", "tasks/archive/*.md")),
            ("epic", ("epics/*.md",)),
            ("phase", ("phases/*.md",)),
            ("bug", ("bugs/*.md", "bugs/archive/*.md")),
            ("issue", ("issues/*.md", "issues/archive/*.md")),
            (
                "handover",
                ("handovers/*.md", "handovers/_archive/*/*.md", "handovers/archive/*.md"),
            ),
            ("decision", ("decisions/*.md",)),
            ("idea", ("ideas/IDEA-*.md",)),
            ("note", ("notes/NOTE-*.md", "notes/_archive/NOTE-*.md")),
            ("area", ("areas/*.md",)),
            ("tracker", ("trackers/*.md", "integrations/trackers/*.md")),
        )
        found: dict[tuple[str, str], Path] = {}
        for kind, patterns in specs:
            for pattern in patterns:
                for path in sorted(self.backlog_path.glob(pattern)):
                    if path.name.startswith(".") or ".tmp." in path.name or ".corrupt-" in path.name:
                        continue
                    # Patterns are ordered canonical-first.  Legacy duplicate
                    # paths are import fallbacks and never override canonical.
                    found.setdefault((kind, path.stem), path)
        return [(kind, ident, path) for (kind, ident), path in found.items()]

    def _parse_entity_file(self, kind: str, path: Path) -> tuple[dict[str, Any], str | None]:
        return self._parse_entity_text(kind, path.read_text(encoding="utf-8"))

    def _parse_entity_text(self, kind: str, raw: str) -> tuple[dict[str, Any], str | None]:
        if any(marker in raw for marker in ("<<<<<<<", "=======", ">>>>>>>")):
            raise ValueError("git conflict markers")
        fm, body = parse_frontmatter(raw)
        if not fm or not isinstance(fm, dict):
            raise ValueError("missing or invalid frontmatter")
        if kind == "task":
            doc = task_v4_from_file(fm, body.removesuffix("\n"))
            return _split_body(doc)
        return _clean_doc(fm), body.removesuffix("\n") or None

    @staticmethod
    def _validate_projected_identity(kind: str, ident: str, doc: Mapping[str, Any]) -> None:
        declared = doc.get("id")
        if declared is not None and str(declared) != ident:
            raise ValueError(
                f"{kind} path id {ident!r} does not match frontmatter id {declared!r}"
            )

    def _scan_projection(self, tx: "Transaction") -> None:
        self._import_linear_queue(tx)
        generation = self._git_generation()
        generation_row = tx.connection.execute(
            "SELECT value FROM meta WHERE key='last_scan_generation'"
        ).fetchone()
        force_hash = not generation_row or generation_row[0] != generation
        rows = tx.connection.execute(
            "SELECT file,kind,id,content_hash,mtime,size,dirty,quarantined "
            "FROM projection ORDER BY file"
        ).fetchall()
        known_rel = {row["file"] for row in rows}
        for row in rows:
            rel = row["file"]
            path = self.backlog_path / Path(rel)
            if not path.exists():
                if row["quarantined"]:
                    tx.connection.execute(
                        "UPDATE projection SET quarantined=0 WHERE file=?", (rel,)
                    )
                if row["id"]:
                    entity = tx.connection.execute(
                        "SELECT deleted FROM entities WHERE kind=? AND id=?",
                        (row["kind"], row["id"]),
                    ).fetchone()
                    if entity and not entity["deleted"]:
                        tx._export_keys.add((row["kind"], row["id"]))
                elif row["kind"] == "backlog":
                    tx._export_backlog = True
                elif row["kind"] == _IDEAS_INDEX_KIND:
                    tx._export_ideas = True
                continue
            if not force_hash and rel not in {"backlog.yaml", "project.yaml"}:
                try:
                    unchanged_stat = path.stat()
                except OSError:
                    unchanged_stat = None
                if (
                    unchanged_stat is not None
                    and unchanged_stat.st_mtime == row["mtime"]
                    and unchanged_stat.st_size == row["size"]
                    and not row["quarantined"]
                ):
                    continue
            content, stat = _read_file_snapshot(path)
            digest = hashlib.sha1(content).hexdigest()
            if digest == row["content_hash"] and not row["quarantined"]:
                tx.connection.execute(
                    "UPDATE projection SET mtime=?,size=? WHERE file=?",
                    (stat.st_mtime, stat.st_size, rel),
                )
                continue
            if row["dirty"]:
                if row["kind"] == "backlog":
                    self._merge_dirty_backlog_edit(tx, row, content, stat)
                    continue
                if row["kind"] == _IDEAS_INDEX_KIND:
                    # A pending export outranks whatever is on disk: refreshing
                    # the hash here would launder the failure into "clean".
                    tx._export_ideas = True
                    continue
                self._merge_dirty_external_edit(tx, row, content, stat)
                continue
            if row["kind"] == _IDEAS_INDEX_KIND:
                # Derived output (R2): a hand edit is not an entity change, so
                # only the hash moves.  The next idea write regenerates it.
                self._record_projection_bytes(
                    tx.connection, rel, _IDEAS_INDEX_KIND, None, content, stat
                )
                continue
            if row["kind"] == "backlog":
                self._import_backlog_file(tx, path, content, stat)
                continue
            if row["kind"] == "project":
                try:
                    doc = yaml_io.safe_load(content.decode("utf-8")) or {}
                    if not isinstance(doc, dict):
                        raise ValueError("project.yaml must be a mapping")
                except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                    tx.connection.execute(
                        "UPDATE projection SET quarantined=1,dirty=0 WHERE file=?", (rel,)
                    )
                    tx.warnings.append(f"quarantined {rel}: {exc}")
                    tx.log_entries.append(f"quarantined {rel}: {exc}")
                    continue
                tx._import_row("project", row["id"] or _PROJECT_ID, doc, None)
                self._record_projection_bytes(
                    tx.connection, rel, "project", row["id"] or _PROJECT_ID, content, stat
                )
                continue
            if not row["id"]:
                continue
            try:
                doc, body = self._parse_entity_text(row["kind"], content.decode("utf-8"))
                self._validate_projected_identity(row["kind"], row["id"], doc)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                tx.connection.execute(
                    "UPDATE projection SET quarantined=1,dirty=0 WHERE file=?", (rel,)
                )
                tx.warnings.append(f"quarantined {rel}: {exc}")
                tx.log_entries.append(f"quarantined {rel}: {exc}")
                continue
            if row["kind"] in {"epic", "phase"}:
                current = tx.connection.execute(
                    "SELECT doc FROM entities WHERE kind=? AND id=?",
                    (row["kind"], row["id"]),
                ).fetchone()
                if current:
                    merged = _from_json(current["doc"], {})
                    heavy_fields = (
                        EPIC_HEAVY_FIELDS
                        if row["kind"] == "epic"
                        else PHASE_HEAVY_FIELDS
                    )
                    for field in heavy_fields:
                        if field in doc:
                            merged[field] = doc[field]
                        else:
                            merged.pop(field, None)
                    doc = merged
            tx._import_row(row["kind"], row["id"], doc, body)
            self._record_projection_bytes(
                tx.connection, rel, row["kind"], row["id"], content, stat
            )

        # Files created by a hand edit or checkout after bootstrap have no
        # projection row yet. Discover and import them under the same writer
        # transaction as the rest of the scan.
        for kind, ident, path in self._known_entity_files():
            rel = path.relative_to(self.backlog_path).as_posix()
            if rel in known_rel:
                continue
            try:
                content, stat = _read_file_snapshot(path)
                doc, body = self._parse_entity_text(kind, content.decode("utf-8"))
                self._validate_projected_identity(kind, ident, doc)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                try:
                    content, stat = _read_file_snapshot(path)
                except OSError:
                    stat = None
                if stat is not None:
                    self._record_projection_bytes(
                        tx.connection, rel, kind, ident, content, stat
                    )
                    tx.connection.execute(
                        "UPDATE projection SET quarantined=1 WHERE file=?", (rel,)
                    )
                tx.warnings.append(f"quarantined {rel}: {exc}")
                tx.log_entries.append(f"quarantined {rel}: {exc}")
                continue
            if _is_archive_path(path, self.backlog_path):
                doc["archived"] = True
            tx._import_row(kind, ident, doc, body)
            self._record_projection_bytes(
                tx.connection, rel, kind, ident, content, stat
            )

        project = self.backlog_path / "project.yaml"
        if project.exists() and "project.yaml" not in known_rel:
            try:
                content, stat = _read_file_snapshot(project)
                doc = yaml_io.safe_load(content.decode("utf-8")) or {}
                if not isinstance(doc, dict):
                    raise ValueError("project.yaml must be a mapping")
                tx._import_row("project", _PROJECT_ID, doc, None)
                self._record_projection_bytes(
                    tx.connection,
                    "project.yaml",
                    "project",
                    _PROJECT_ID,
                    content,
                    stat,
                )
            except (OSError, ValueError, yaml.YAMLError) as exc:
                tx.warnings.append(f"quarantined project.yaml: {exc}")
                tx.log_entries.append(f"quarantined project.yaml: {exc}")
        tx.connection.execute(
            "INSERT INTO meta(key,value) VALUES('last_scan_generation',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (generation,),
        )

    def _merge_dirty_external_edit(
        self,
        tx: "Transaction",
        projection_row: sqlite3.Row,
        content: bytes,
        stat: os.stat_result,
    ) -> None:
        rel = projection_row["file"]
        kind = projection_row["kind"]
        ident = projection_row["id"]
        if not ident:
            return
        base_row = tx.connection.execute(
            "SELECT content FROM projection_base WHERE file=?", (rel,)
        ).fetchone()
        entity = tx.connection.execute(
            "SELECT doc,body,rev FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        if base_row is None or entity is None:
            return
        try:
            if kind == "project":
                base_doc = yaml_io.safe_load(
                    bytes(base_row["content"]).decode("utf-8")
                ) or {}
                their_doc = yaml_io.safe_load(content.decode("utf-8")) or {}
                if not isinstance(base_doc, dict) or not isinstance(their_doc, dict):
                    raise ValueError("project.yaml must be a mapping")
                base_body = their_body = None
            else:
                base_doc, base_body = self._parse_entity_text(
                    kind, bytes(base_row["content"]).decode("utf-8")
                )
                their_doc, their_body = self._parse_entity_text(
                    kind, content.decode("utf-8")
                )
                self._validate_projected_identity(kind, ident, their_doc)
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            tx.connection.execute(
                "UPDATE projection SET quarantined=1 WHERE file=?", (rel,)
            )
            tx.warnings.append(f"quarantined {rel}: {exc}")
            tx.log_entries.append(f"quarantined {rel}: {exc}")
            return
        our_doc = _from_json(entity["doc"], {})
        our_body = entity["body"]
        merged_doc = _three_way_merge_fields(base_doc, our_doc, their_doc)
        if our_body == their_body:
            merged_body = our_body
        elif our_body != base_body:
            merged_body = our_body
        elif their_body != base_body:
            merged_body = their_body
        else:
            merged_body = our_body
        fields, before, after = _merge_change_details(
            base_doc,
            our_doc,
            their_doc,
            merged_doc,
            base_body=base_body,
            our_body=our_body,
            their_body=their_body,
            merged_body=merged_body,
        )
        if not fields:
            return
        seq = tx._record_change(kind, ident, "merge", fields, before, after)
        tx.connection.execute(
            "UPDATE entities SET epic=?,status=?,archived=?,doc=?,body=?,rev=rev+1,updated_seq=? "
            "WHERE kind=? AND id=?",
            (
                merged_doc.get("epic"),
                merged_doc.get("status"),
                int(bool(merged_doc.get("archived"))),
                _json(merged_doc),
                merged_body,
                seq,
                kind,
                ident,
            ),
        )
        tx.connection.execute(
            "UPDATE projection SET content_hash=?,mtime=?,size=?,dirty=1,quarantined=0 "
            "WHERE file=?",
            (hashlib.sha1(content).hexdigest(), stat.st_mtime, stat.st_size, rel),
        )
        tx.seq = seq
        tx.log_entries.append(f"merged external edit {rel} into {kind}:{ident} at seq {seq}")
        tx._derived_keys.add((kind, ident))
        tx._export_keys.add((kind, ident))

    def _merge_dirty_backlog_edit(
        self,
        tx: "Transaction",
        projection_row: sqlite3.Row,
        content: bytes,
        stat: os.stat_result,
    ) -> None:
        base_row = tx.connection.execute(
            "SELECT content FROM projection_base WHERE file='backlog.yaml'"
        ).fetchone()
        if base_row is None:
            return
        try:
            base = yaml_io.safe_load(bytes(base_row["content"]).decode("utf-8")) or {}
            theirs = yaml_io.safe_load(content.decode("utf-8")) or {}
            _validate_backlog_document(base)
            _validate_backlog_document(theirs)
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            tx.connection.execute(
                "UPDATE projection SET quarantined=1 WHERE file='backlog.yaml'"
            )
            tx.warnings.append(f"quarantined backlog.yaml: {exc}")
            tx.log_entries.append(f"quarantined backlog.yaml: {exc}")
            return

        base_rows = _flatten_backlog_dict(base)
        their_rows = _flatten_backlog_dict(theirs)
        for key, (their_doc, _their_body) in their_rows.items():
            kind, ident = key
            current = tx.connection.execute(
                "SELECT doc,body,deleted FROM entities WHERE kind=? AND id=?",
                key,
            ).fetchone()
            if current is None:
                tx._import_row(kind, ident, their_doc, None)
                continue
            our_doc = _from_json(current["doc"], {})
            base_doc = base_rows.get(key, ({}, None))[0]
            if kind in {"epic", "phase"}:
                heavy_fields = EPIC_HEAVY_FIELDS if kind == "epic" else PHASE_HEAVY_FIELDS
                our_slim = {k: v for k, v in our_doc.items() if k not in heavy_fields}
                merged = _three_way_merge_fields(base_doc, our_slim, their_doc)
                for field in heavy_fields:
                    if field in our_doc:
                        merged[field] = our_doc[field]
            else:
                merged = _three_way_merge_fields(base_doc, our_doc, their_doc)
            self._apply_external_merge_row(
                tx,
                kind,
                ident,
                base_doc,
                our_doc,
                their_doc,
                current["body"],
                merged,
                current["body"],
            )

        tx.connection.execute(
            "UPDATE projection SET content_hash=?,mtime=?,size=?,dirty=1,quarantined=0 "
            "WHERE file='backlog.yaml'",
            (hashlib.sha1(content).hexdigest(), stat.st_mtime, stat.st_size),
        )
        tx._export_backlog = True
        tx.log_entries.append("merged external edit backlog.yaml")

    @staticmethod
    def _apply_external_merge_row(
        tx: "Transaction",
        kind: str,
        ident: str,
        base_doc: dict[str, Any],
        old_doc: dict[str, Any],
        their_doc: dict[str, Any],
        old_body: str | None,
        merged_doc: dict[str, Any],
        merged_body: str | None,
    ) -> None:
        fields, before, after = _merge_change_details(
            base_doc,
            old_doc,
            their_doc,
            merged_doc,
            our_body=old_body,
            merged_body=merged_body,
        )
        if not fields:
            return
        seq = tx._record_change(kind, ident, "merge", fields, before, after)
        tx.connection.execute(
            "UPDATE entities SET epic=?,status=?,archived=?,deleted=0,doc=?,body=?,"
            "rev=rev+1,updated_seq=? WHERE kind=? AND id=?",
            (
                merged_doc.get("epic"),
                merged_doc.get("status"),
                int(bool(merged_doc.get("archived"))),
                _json(merged_doc),
                merged_body,
                seq,
                kind,
                ident,
            ),
        )
        tx.seq = seq
        tx._derived_keys.add((kind, ident))

    def _import_backlog_file(
        self, tx: "Transaction", path: Path, content: bytes, stat: os.stat_result
    ) -> None:
        try:
            raw = yaml_io.safe_load(content.decode("utf-8")) or {}
            _validate_backlog_document(raw)
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            tx.connection.execute(
                "UPDATE projection SET quarantined=1,dirty=0 WHERE file='backlog.yaml'"
            )
            tx.warnings.append(f"quarantined backlog.yaml: {exc}")
            tx.log_entries.append(f"quarantined backlog.yaml: {exc}")
            return
        rows = _flatten_backlog_dict(raw)
        for key, (doc, body) in rows.items():
            if key[0] in {"epic", "phase"}:
                current = tx.connection.execute(
                    "SELECT doc,body FROM entities WHERE kind=? AND id=?", key
                ).fetchone()
                if current:
                    current_doc = _from_json(current["doc"], {})
                    heavy_fields = (
                        EPIC_HEAVY_FIELDS if key[0] == "epic" else PHASE_HEAVY_FIELDS
                    )
                    for field in heavy_fields:
                        if field in current_doc:
                            doc[field] = current_doc[field]
                    body = current["body"]
            tx._import_row(key[0], key[1], doc, body)
        self._record_projection_bytes(
            tx.connection, "backlog.yaml", "backlog", None, content, stat
        )

    def _drain_dirty(self, tx: "Transaction") -> None:
        for row in tx.connection.execute(
            "SELECT kind,id,file FROM projection WHERE dirty=1 AND quarantined=0"
        ).fetchall():
            if row["id"]:
                tx._export_keys.add((row["kind"], row["id"]))
            elif row["kind"] == _IDEAS_INDEX_KIND:
                tx._export_ideas = True
            else:
                tx._export_backlog = True

    def _refresh_derived(self, tx: "Transaction") -> None:
        for kind, ident in tx._derived_keys:
            tx.connection.execute(
                "DELETE FROM entity_fts WHERE kind=? AND id=?", (kind, ident)
            )
            tx.connection.execute(
                "DELETE FROM entity_paths WHERE kind=? AND id=?", (kind, ident)
            )
            tx.connection.execute(
                "DELETE FROM links WHERE (src_kind=? AND src_id=?) "
                "OR (dst_kind=? AND dst_id=? AND derived=1)",
                (kind, ident, kind, ident),
            )
            if kind == "handover":
                tx.connection.execute(
                    "DELETE FROM handover_tasks WHERE handover_id=?", (ident,)
                )
            row = tx.connection.execute(
                "SELECT doc,body,deleted FROM entities WHERE kind=? AND id=?",
                (kind, ident),
            ).fetchone()
            if not row or row["deleted"]:
                continue
            doc = _from_json(row["doc"], {})
            title = str(doc.get("title") or doc.get("name") or doc.get("tldr") or "")
            prose = "\n".join(
                str(doc.get(field) or "")
                for field in ("description", "notes", "review_instructions", "next_action")
            )
            if row["body"]:
                prose = f"{prose}\n{row['body']}"
            tx.connection.execute(
                "INSERT INTO entity_fts(kind,id,title,body) VALUES(?,?,?,?)",
                (kind, ident, title, prose),
            )
            for anchor in doc.get("anchors") or []:
                value, match_kind = normalize_task_anchor(
                    str(anchor), doc.get("sub_repo")
                )
                tx.connection.execute(
                    "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)",
                    (kind, ident, value, match_kind, "anchors"),
                )
            located: set[str] = set()
            for location in doc.get("location") or []:
                value = normalize_location(str(location))
                located.add(value)
                tx.connection.execute(
                    "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)",
                    (kind, ident, value, "exact", "location"),
                )
            for value in extract_prose_paths(str(row["body"] or "")):
                if value in located:
                    continue
                tx.connection.execute(
                    "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)",
                    (kind, ident, value, "exact", "prose"),
                )
            for link in legacy_links_to_typed(doc, kind):
                if not isinstance(link, dict) or not link.get("target"):
                    continue
                target_id = str(link["target"])
                target_kind = self._kind_for_id(tx.connection, target_id)
                link_type = str(link.get("type") or "relates_to")
                tx.connection.execute(
                    "INSERT OR IGNORE INTO links(src_kind,src_id,type,dst_kind,dst_id,derived) "
                    "VALUES(?,?,?,?,?,0)",
                    (kind, ident, link_type, target_kind, target_id),
                )
                reverse = REVERSE_TYPE.get(link_type)
                if reverse:
                    tx.connection.execute(
                        "INSERT OR IGNORE INTO links(src_kind,src_id,type,dst_kind,dst_id,derived) "
                        "VALUES(?,?,?,?,?,1)",
                        (target_kind, target_id, reverse, kind, ident),
                    )
            if kind == "handover":
                for task_id in doc.get("task_ids") or []:
                    tx.connection.execute(
                        "INSERT INTO handover_tasks(handover_id,task_id) VALUES(?,?)",
                        (ident, str(task_id)),
                    )
        if tx._derived_keys:
            self._rebuild_related(tx.connection)

    @staticmethod
    def _kind_for_id(connection: sqlite3.Connection, ident: str) -> str:
        row = connection.execute(
            "SELECT kind FROM entities WHERE id=? AND deleted=0 "
            "ORDER BY CASE kind WHEN 'task' THEN 0 WHEN 'issue' THEN 1 ELSE 2 END LIMIT 1",
            (ident,),
        ).fetchone()
        return str(row[0]) if row else "task"

    @staticmethod
    def _rebuild_related(connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM related")
        paths = connection.execute(
            "SELECT kind,id,path,match_kind FROM entity_paths "
            "WHERE source IN ('anchors','location') ORDER BY kind,id,path"
        ).fetchall()
        weights: dict[tuple[tuple[str, str], tuple[str, str]], int] = {}
        for index, left in enumerate(paths):
            left_key = (left["kind"], left["id"])
            for right in paths[index + 1 :]:
                right_key = (right["kind"], right["id"])
                if left_key == right_key:
                    continue
                matches = (
                    left["path"] == right["path"]
                    or (
                        left["match_kind"] == "glob"
                        and fnmatch.fnmatchcase(right["path"], left["path"])
                    )
                    or (
                        right["match_kind"] == "glob"
                        and fnmatch.fnmatchcase(left["path"], right["path"])
                    )
                )
                if matches:
                    pair = tuple(sorted((left_key, right_key)))
                    weights[pair] = weights.get(pair, 0) + 1
        for (left, right), weight in weights.items():
            connection.execute(
                "INSERT INTO related(a_kind,a_id,b_kind,b_id,via,weight) "
                "VALUES(?,?,?,?,?,?)",
                (left[0], left[1], right[0], right[1], "path", weight),
            )
        handovers = connection.execute(
            "SELECT handover_id,task_id FROM handover_tasks ORDER BY handover_id,task_id"
        ).fetchall()
        by_handover: dict[str, list[str]] = {}
        for row in handovers:
            by_handover.setdefault(row["handover_id"], []).append(row["task_id"])
        for task_ids in by_handover.values():
            for index, left in enumerate(task_ids):
                for right in task_ids[index + 1 :]:
                    connection.execute(
                        "INSERT INTO related(a_kind,a_id,b_kind,b_id,via,weight) "
                        "VALUES('task',?,'task',?,'handover',1)",
                        tuple(sorted((left, right))),
                    )

    def _export_touched(self, tx: "Transaction") -> None:
        if any(kind in {"backlog", "epic", "phase"} for kind, _ in tx._export_keys):
            tx._export_backlog = True
        for kind, ident in sorted(tx._export_keys):
            if kind == "backlog":
                continue
            row = tx.connection.execute(
                "SELECT * FROM entities WHERE kind=? AND id=?", (kind, ident)
            ).fetchone()
            if not row:
                continue
            self._export_entity_row(tx, row)
        if tx._export_ideas or any(kind == "idea" for kind, _ in tx._export_keys):
            self._export_ideas_index(tx)
        if tx._export_backlog:
            self._export_backlog(tx)

    def _export_ideas_index(self, tx: "Transaction") -> None:
        """Regenerate `ideas/IDEAS.md` from the idea rows this store holds."""
        entries = [
            _from_json(row["doc"], {})
            for row in tx.connection.execute(
                "SELECT doc FROM entities WHERE kind='idea' AND deleted=0 ORDER BY id"
            )
        ]
        content = render_ideas_index(entries).encode("utf-8")
        seq = int(
            tx.connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
        )
        self._replace_projection(
            tx, _IDEAS_INDEX_REL, _IDEAS_INDEX_KIND, None, content, seq
        )

    def _regenerate_progress_if_due(self, tx: "Transaction") -> None:
        if _PROGRESS_RENDERER is None or tx.seq is None:
            return
        now = time.monotonic()
        if (
            self._last_progress_clock is not None
            and now - self._last_progress_clock < 5.0
        ):
            return
        target = self.db_path.parent / "PROGRESS.md"
        temp = target.with_name(f"{target.name}.tmp.{self.session}")
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            rendered = _PROGRESS_RENDERER(
                self._load_dict_from_connection(tx.connection), existing
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            tx.connection.execute(
                "INSERT INTO meta(key,value) VALUES('last_progress_seq',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(tx.seq),),
            )
            self._last_progress_clock = now
        except Exception as exc:
            temp.unlink(missing_ok=True)
            self._log(f"progress export failed: {exc!r}")

    def _log(self, message: str) -> None:
        path = self.db_path.parent / "store.log"
        try:
            if path.exists() and path.stat().st_size > 1024 * 1024:
                tail = path.read_bytes()[-512 * 1024 :]
                path.write_bytes(tail)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{_now()} {message}\n")
        except OSError:
            pass

    def _export_entity_row(self, tx: "Transaction", row: sqlite3.Row) -> None:
        kind, ident = row["kind"], row["id"]
        old_rows = tx.connection.execute(
            "SELECT file FROM projection WHERE kind=? AND id=? ORDER BY file",
            (kind, ident),
        ).fetchall()
        old_rel = old_rows[0]["file"] if old_rows else None
        if row["deleted"]:
            for old_row in old_rows:
                prior_rel = old_row["file"]
                if not self._remove_projection_file(
                    tx, self.backlog_path / prior_rel, kind, ident, prior_rel
                ):
                    continue
                tx.connection.execute("DELETE FROM projection WHERE file=?", (prior_rel,))
                tx.connection.execute("DELETE FROM projection_base WHERE file=?", (prior_rel,))
            return
        target = self._entity_path(kind, ident, bool(row["archived"]))
        if target is None:
            return
        rel = target.relative_to(self.backlog_path).as_posix()
        for old_row in old_rows:
            prior_rel = old_row["file"]
            if prior_rel == rel:
                continue
            old_path = self.backlog_path / prior_rel
            if not self._remove_projection_file(
                tx, old_path, kind, ident, prior_rel
            ):
                return
            tx.connection.execute("DELETE FROM projection WHERE file=?", (prior_rel,))
            tx.connection.execute("DELETE FROM projection_base WHERE file=?", (prior_rel,))
        if any(old_row["file"] == rel for old_row in old_rows):
            old_rel = rel
        doc = _from_json(row["doc"], {})
        body = row["body"] or ""
        if kind == "task":
            fm, rendered_body = task_v4_to_file(doc | ({BODY_KEY: body} if body else {}))
            content = render_frontmatter(fm, rendered_body).encode("utf-8")
        elif kind in {"epic", "phase"}:
            heavy_fields = EPIC_HEAVY_FIELDS if kind == "epic" else PHASE_HEAVY_FIELDS
            _slim, heavy, rendered_body = _split_entity_for_v3(
                doc | ({BODY_KEY: body} if body else {}), heavy_fields
            )
            if not any(field in heavy for field in heavy_fields) and not rendered_body:
                if old_rel:
                    if not self._remove_projection_file(
                        tx, self.backlog_path / old_rel, kind, ident, old_rel
                    ):
                        return
                    tx.connection.execute("DELETE FROM projection WHERE file=?", (old_rel,))
                    tx.connection.execute("DELETE FROM projection_base WHERE file=?", (old_rel,))
                return
            content = render_frontmatter(heavy, rendered_body).encode("utf-8")
        elif kind == "project":
            content = yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True).encode("utf-8")
        else:
            content = render_frontmatter(doc, body).encode("utf-8")
        self._replace_projection(tx, rel, kind, ident, content, row["updated_seq"])

    def _remove_projection_file(
        self,
        tx: "Transaction",
        path: Path,
        kind: str,
        ident: str,
        rel: str,
    ) -> bool:
        """Remove a projected file while retaining rollback bytes in memory."""
        if not path.exists():
            return True
        prior = path.read_bytes()
        tx.connection.execute(
            "INSERT INTO projection_base(file,content) VALUES(?,?) "
            "ON CONFLICT(file) DO NOTHING",
            (rel, prior),
        )
        self._remember_projection_change(tx, path, prior, None)
        deadline: float | None = None
        while True:
            try:
                path.unlink(missing_ok=True)
                return True
            except OSError as exc:
                tx.log_entries.append(f"projection remove retry {rel}: {exc!r}")
                if exc.errno not in _RETRYABLE_REPLACE_ERRNOS:
                    error = exc
                    break
                if deadline is None:
                    deadline = time.monotonic() + 2.0
                if time.monotonic() >= deadline:
                    error = exc
                    break
                time.sleep(random.uniform(0.02, 0.08))
        tx.connection.execute(
            "UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,)
        )
        seq = tx._record_change(
            kind,
            ident,
            "export-fail",
            ["file"],
            {"file": rel},
            {"error": str(error)},
        )
        tx.seq = seq
        tx.warnings.append(f"export pending: {rel} — retried on next call")
        tx.log_entries.append(f"projection remove pending {rel} at seq {seq}: {error!r}")
        return False

    def _export_backlog(self, tx: "Transaction", *, force: bool = False) -> None:
        data = self._load_dict_from_connection(tx.connection)
        data.pop("context", None)
        data.pop("_orphan_tasks", None)
        slim_epics: list[dict[str, Any]] = []
        for epic in data.get("epics", []):
            persistable = {k: v for k, v in epic.items() if k != "tasks"}
            slim, heavy, body = _split_entity_for_v3(persistable, EPIC_HEAVY_FIELDS)
            slim_epics.append(
                slim
                if (any(field in heavy for field in EPIC_HEAVY_FIELDS) or body)
                else _clean_doc(persistable)
            )
        data["epics"] = slim_epics
        slim_phases: list[dict[str, Any]] = []
        for phase in data.get("phases", []):
            slim, heavy, body = _split_entity_for_v3(phase, PHASE_HEAVY_FIELDS)
            slim_phases.append(
                slim
                if (any(field in heavy for field in PHASE_HEAVY_FIELDS) or body)
                else _clean_doc(phase)
            )
        data["phases"] = slim_phases
        meta = dict(data.get("meta") or {})
        meta.pop("updated", None)
        meta["projection_schema"] = PROJECTION_SCHEMA
        data["meta"] = meta
        content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True).encode("utf-8")
        seq = int(tx.connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0])
        self._replace_projection(tx, "backlog.yaml", "backlog", None, content, seq)

    def _entity_path(self, kind: str, ident: str, archived: bool) -> Path | None:
        if kind != "project":
            _validate_safe_identifier(ident)
        if kind == "task":
            base = self.backlog_path / "tasks" / ("archive" if archived else "")
            return base / f"{ident}.md"
        if kind == "epic":
            return epic_file_path(self.backlog_path / "backlog.yaml", ident)
        if kind == "phase":
            return phase_file_path(self.backlog_path / "backlog.yaml", ident)
        if kind == "bug":
            return self.backlog_path / "bugs" / ("archive" if archived else "") / f"{ident}.md"
        if kind == "issue":
            return self.backlog_path / "issues" / ("archive" if archived else "") / f"{ident}.md"
        if kind == "handover":
            if archived:
                year = ident[:4] if re.match(r"^\d{4}-", ident) else str(datetime.now(timezone.utc).year)
                return self.backlog_path / "handovers" / "_archive" / year / f"{ident}.md"
            return self.backlog_path / "handovers" / f"{ident}.md"
        if kind == "decision":
            return self.backlog_path / "decisions" / f"{ident}.md"
        if kind == "idea":
            return self.backlog_path / "ideas" / f"{ident}.md"
        if kind == "note":
            base = self.backlog_path / "notes" / ("_archive" if archived else "")
            return base / f"{ident}.md"
        if kind == "area":
            return self.backlog_path / "areas" / f"{ident}.md"
        if kind == "tracker":
            return self.backlog_path / "trackers" / f"{ident}.md"
        if kind == "project":
            return self.backlog_path / "project.yaml"
        return None

    def _replace_projection(
        self,
        tx: "Transaction",
        rel: str,
        kind: str,
        ident: str | None,
        content: bytes,
        exported_seq: int,
    ) -> None:
        existing = tx.connection.execute(
            "SELECT content_hash,quarantined FROM projection WHERE file=?", (rel,)
        ).fetchone()
        if existing and existing["quarantined"]:
            tx.connection.execute(
                "UPDATE projection SET dirty=1 WHERE file=?", (rel,)
            )
            tx.warnings.append(f"export pending: {rel} is quarantined")
            tx.log_entries.append(f"projection export suppressed for quarantined {rel}")
            return
        digest = hashlib.sha1(content).hexdigest()
        if existing and existing["content_hash"] == digest:
            path = self.backlog_path / rel
            if path.exists():
                stat = path.stat()
                tx.connection.execute(
                    "UPDATE projection SET mtime=?,size=?,dirty=0,exported_seq=? WHERE file=?",
                    (stat.st_mtime, stat.st_size, exported_seq, rel),
                )
                tx.connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))
                return
        path = self.backlog_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        prior = path.read_bytes() if existed else b""
        tx.connection.execute(
            "INSERT INTO projection_base(file,content) VALUES(?,?) "
            "ON CONFLICT(file) DO NOTHING",
            (rel, prior),
        )
        temp = path.with_name(f"{path.name}.tmp.{self.session}")
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            stat = os.fstat(handle.fileno())
        self._remember_projection_change(
            tx, path, prior if existed else None, digest, content
        )
        deadline: float | None = None
        error: OSError | None = None
        while True:
            try:
                os.replace(temp, path)
                error = None
                break
            except OSError as exc:
                error = exc
                tx.log_entries.append(f"export retry {rel}: {exc!r}")
                if exc.errno not in _RETRYABLE_REPLACE_ERRNOS:
                    break
                if deadline is None:
                    deadline = time.monotonic() + 2.0
                if time.monotonic() >= deadline:
                    break
                time.sleep(random.uniform(0.02, 0.08))
        if error is not None:
            temp.unlink(missing_ok=True)
            if existing:
                tx.connection.execute(
                    "UPDATE projection SET dirty=1,quarantined=0 WHERE file=?", (rel,)
                )
            else:
                tx.connection.execute(
                    "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
                    "VALUES(?,?,?,?,?,?,1,0,?)",
                    (rel, kind, ident, hashlib.sha1(prior).hexdigest(), None, len(prior), exported_seq),
                )
            seq = tx._record_change(
                kind,
                ident or _BACKLOG_ID,
                "export-fail",
                ["file"],
                {"file": rel},
                {"error": str(error)},
            )
            tx.seq = seq
            tx.warnings.append(f"export pending: {rel} — retried on next call")
            tx.log_entries.append(f"export pending {rel} at seq {seq}: {error!r}")
            return
        tx.connection.execute(
            "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
            "VALUES(?,?,?,?,?,?,0,0,?) ON CONFLICT(file) DO UPDATE SET "
            "kind=excluded.kind,id=excluded.id,content_hash=excluded.content_hash,"
            "mtime=excluded.mtime,size=excluded.size,dirty=0,quarantined=0,"
            "exported_seq=excluded.exported_seq",
            (rel, kind, ident, digest, stat.st_mtime, stat.st_size, exported_seq),
        )
        tx.connection.execute("DELETE FROM projection_base WHERE file=?", (rel,))

    @staticmethod
    def _record_projection_bytes(
        connection: sqlite3.Connection,
        rel: str,
        kind: str,
        ident: str | None,
        content: bytes,
        stat: os.stat_result,
    ) -> None:
        connection.execute(
            "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
            "VALUES(?,?,?,?,?,?,0,0,NULL) ON CONFLICT(file) DO UPDATE SET "
            "kind=excluded.kind,id=excluded.id,content_hash=excluded.content_hash,"
            "mtime=excluded.mtime,size=excluded.size,dirty=0,quarantined=0",
            (rel, kind, ident, hashlib.sha1(content).hexdigest(), stat.st_mtime, stat.st_size),
        )

    @staticmethod
    def _checkpoint_passive(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        except sqlite3.Error:
            pass


class Transaction:
    def __init__(self, store: Store, connection: sqlite3.Connection, *, tool: str):
        self.store = store
        self.connection = connection
        self.tool = tool
        self.session = store.session
        self.committed: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_committed: dict[tuple[str, str], dict[str, Any]] = {}
        self.seq: int | None = None
        self.warnings: list[str] = []
        self.log_entries: list[str] = []
        self._committed_keys: set[tuple[str, str]] = set()
        self._export_keys: set[tuple[str, str]] = set()
        self._derived_keys: set[tuple[str, str]] = set()
        self._export_backlog = False
        self._export_ideas = False
        self._replaced_files: dict[Path, bytes | None] = {}
        self._reserved_keys: set[tuple[str, str]] = set()
        intent_token = uuid.uuid4().hex
        self._intent_path = self.store.db_path.parent / f"export-intent.{intent_token}.json"
        self._intent_entries: dict[str, dict[str, str | None]] = {}

    def get(self, kind: str, ident: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT doc,body,deleted FROM entities WHERE kind=? AND id=?",
            (kind, ident),
        ).fetchone()
        if row is None or row["deleted"]:
            raise KeyError(f"{kind} {ident} not found")
        doc = _from_json(row["doc"], {})
        if row["body"]:
            doc[BODY_KEY] = row["body"]
        return doc

    def list(
        self, kind: str, *, include_archived: bool = False
    ) -> list[tuple[str, dict[str, Any], str | None]]:
        """Every live row of `kind` as `(id, doc, body)`, ordered by id.

        Tools that used to glob a directory read the authority through this
        instead, so a list and the write that follows it see the same snapshot.
        Tombstoned rows are never returned; archived rows only on request.
        """
        sql = "SELECT id,doc,body FROM entities WHERE kind=? AND deleted=0"
        if not include_archived:
            sql += " AND archived=0"
        return [
            (row["id"], _from_json(row["doc"], {}), row["body"])
            for row in self.connection.execute(sql + " ORDER BY id", (kind,))
        ]

    def linear_enqueue(
        self,
        op: str,
        target_id: str,
        tracker_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        """Queue one Linear push inside this transaction; returns its seq.

        De-duplicates on `(op, target_id)` among pending rows: the drain re-reads
        task state, so stacking requests for one target is wasted round-trips.
        """
        existing = self.connection.execute(
            "SELECT seq FROM linear_queue WHERE op=? AND target_id=? AND state='pending'",
            (op, target_id),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = self.connection.execute(
            "INSERT INTO linear_queue(op,target_id,tracker_id,payload,state,attempts,last_error) "
            "VALUES(?,?,?,?,'pending',0,NULL)",
            (op, target_id, tracker_id, None if payload is None else _json(payload)),
        )
        return int(cursor.lastrowid)

    def put(
        self, kind: str, ident: str, doc: Mapping[str, Any], body: str | None = None
    ) -> None:
        row = self.connection.execute(
            "SELECT doc,body,rev,archived,deleted FROM entities WHERE kind=? AND id=?",
            (kind, ident),
        ).fetchone()
        if row is None:
            raise KeyError(f"{kind} {ident} not found")
        clean, split_body = _split_body(dict(doc))
        if kind not in {"backlog", "project"} and str(clean.get("id") or "") != ident:
            raise ValueError(
                f"{kind} id is immutable: key {ident!r} does not match document id "
                f"{clean.get('id')!r}"
            )
        if body is None:
            body = split_body
        old = _from_json(row["doc"], {})
        old_body = row["body"]
        fields, before, after = _top_level_diff(old, clean)
        if old_body != body:
            fields.append(BODY_KEY)
            before[BODY_KEY] = old_body
            after[BODY_KEY] = body
        if not fields:
            return
        seq = self._record_change(kind, ident, "update", fields, before, after)
        self.connection.execute(
            "UPDATE entities SET epic=?,status=?,archived=?,doc=?,body=?,rev=rev+1,updated_seq=? "
            "WHERE kind=? AND id=?",
            (
                clean.get("epic"),
                clean.get("status"),
                int(bool(clean.get("archived", row["archived"]))),
                _json(clean),
                body,
                seq,
                kind,
                ident,
            ),
        )
        self.seq = seq
        self._mark(kind, ident)

    def create(
        self,
        kind: str,
        doc: Mapping[str, Any],
        body: str | None = None,
        requested_id: str | None = None,
    ) -> str:
        clean, split_body = _split_body(dict(doc))
        if body is None:
            body = split_body
        ident = requested_id or str(clean.get("id") or self.allocate_id(kind, clean))
        if kind == "handover" and self._id_taken(kind, ident):
            base = ident
            suffix = 2
            while self._id_taken(kind, f"{base}-{suffix}"):
                suffix += 1
            ident = f"{base}-{suffix}"
        _validate_safe_identifier(ident)
        clean["id"] = ident
        if self._id_taken(kind, ident):
            raise ValueError(f"{kind} {ident} already exists or is reserved")
        self._reserved_keys.add((kind, ident))
        seq = self._record_change(kind, ident, "create", sorted(clean), {}, clean)
        self.connection.execute(
            "INSERT INTO entities(kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq) "
            "VALUES(?,?,?,?,?,0,?,?,1,?)",
            (
                kind,
                ident,
                clean.get("epic"),
                clean.get("status"),
                int(bool(clean.get("archived"))),
                _json(clean),
                body,
                seq,
            ),
        )
        self.seq = seq
        self._mark(kind, ident)
        return ident

    def archive(self, kind: str, ident: str) -> None:
        row = self.connection.execute(
            "SELECT doc,archived,deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        if not row or row["deleted"]:
            raise KeyError(f"{kind} {ident} not found")
        if row["archived"]:
            return
        doc = _from_json(row["doc"], {})
        before = {"archived": bool(row["archived"])}
        doc["archived"] = True
        after = {"archived": True}
        seq = self._record_change(kind, ident, "archive", ["archived"], before, after)
        self.connection.execute(
            "UPDATE entities SET archived=1,doc=?,rev=rev+1,updated_seq=? WHERE kind=? AND id=?",
            (_json(doc), seq, kind, ident),
        )
        self.seq = seq
        self._mark(kind, ident)

    def unarchive(self, kind: str, ident: str) -> None:
        """Undo `archive`: clear the flag so the projection returns to the live path.

        The inverse matters because `put` never lowers the flag on its own — it
        falls back to the stored value for any document that omits ``archived``,
        so an archive would otherwise be a one-way door.
        """
        row = self.connection.execute(
            "SELECT doc,archived,deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        if not row or row["deleted"]:
            raise KeyError(f"{kind} {ident} not found")
        if not row["archived"]:
            return
        doc = _from_json(row["doc"], {})
        doc.pop("archived", None)
        seq = self._record_change(
            kind, ident, "unarchive", ["archived"], {"archived": True}, {"archived": False}
        )
        self.connection.execute(
            "UPDATE entities SET archived=0,doc=?,rev=rev+1,updated_seq=? WHERE kind=? AND id=?",
            (_json(doc), seq, kind, ident),
        )
        self.seq = seq
        self._mark(kind, ident)

    def delete(self, kind: str, ident: str) -> None:
        row = self.connection.execute(
            "SELECT deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        if not row:
            raise KeyError(f"{kind} {ident} not found")
        if row["deleted"]:
            return
        seq = self._record_change(
            kind, ident, "delete", ["deleted"], {"deleted": False}, {"deleted": True}
        )
        self.connection.execute(
            "UPDATE entities SET deleted=1,rev=rev+1,updated_seq=? WHERE kind=? AND id=?",
            (seq, kind, ident),
        )
        self.seq = seq
        self._mark(kind, ident)

    def allocate_id(self, kind: str, doc: Mapping[str, Any]) -> str:
        prefix_by_kind = {
            "bug": "B-",
            "issue": "ISS-",
            "decision": "DEC-",
            "idea": "IDEA-",
            "note": "NOTE-",
        }
        if kind == "handover":
            # Slug ids collide by design (one date, similar tldrs), so the
            # suffix search belongs here, next to `id_taken`, rather than in a
            # filesystem probe that cannot see rows this transaction created.
            date_str = str(doc.get("date") or "").strip()
            tldr = str(doc.get("tldr") or "").strip()
            if not date_str or not tldr:
                raise ValueError("handover date and tldr are required for id allocation")
            base = make_handover_id(date_str, tldr)
            ident = base
            suffix = 2
            while self._id_taken(kind, ident):
                ident = f"{base}-{suffix}"
                suffix += 1
            return ident
        if kind == "task":
            epic = str(doc.get("epic") or "").strip()
            if not epic:
                raise ValueError("task epic is required for id allocation")
            prefix = f"{epic}-"
        else:
            prefix = prefix_by_kind.get(kind)
        if prefix is None:
            raise ValueError(f"caller-derived id required for {kind}")
        maximum = 0
        for row in self.connection.execute(
            "SELECT id FROM entities WHERE kind=? AND id LIKE ?", (kind, f"{prefix}%")
        ):
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", row[0])
            if match:
                maximum = max(maximum, int(match.group(1)))
        for ident in self.store._load_reserved_ids().get(kind, set()):
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", ident)
            if match:
                maximum = max(maximum, int(match.group(1)))
        for file_kind, ident, _path in self.store._known_entity_files():
            if file_kind != kind:
                continue
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", ident)
            if match:
                maximum = max(maximum, int(match.group(1)))
        return f"{prefix}{maximum + 1:03d}"

    def id_taken(self, kind: str, ident: str) -> bool:
        """True when `ident` is already live, tombstoned-reserved or on disk.

        Public because callers that allocate an id themselves (a caller-supplied
        `task_id`) must be able to refuse the collision with a readable message
        instead of letting `create` raise out of the transaction.
        """
        return self._id_taken(kind, ident)

    def _id_taken(self, kind: str, ident: str) -> bool:
        if self.connection.execute(
            "SELECT 1 FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone():
            return True
        if ident in self.store._load_reserved_ids().get(kind, set()):
            return True
        return any(
            file_kind == kind and file_id == ident
            for file_kind, file_id, _path in self.store._known_entity_files()
        )

    def _import_row(
        self, kind: str, ident: str, doc: Mapping[str, Any], body: str | None
    ) -> None:
        clean = _clean_doc(dict(doc))
        if kind not in {"backlog", "project"}:
            self.store._validate_projected_identity(kind, ident, clean)
        self._reserved_keys.add((kind, ident))
        row = self.connection.execute(
            "SELECT doc,body,rev,deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        if row is None:
            seq = self._record_change(kind, ident, "import", sorted(clean), {}, clean)
            self.connection.execute(
                "INSERT INTO entities(kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq) "
                "VALUES(?,?,?,?,?,0,?,?,1,?)",
                (
                    kind,
                    ident,
                    clean.get("epic"),
                    clean.get("status"),
                    int(bool(clean.get("archived"))),
                    _json(clean),
                    body,
                    seq,
                ),
            )
        else:
            old = _from_json(row["doc"], {})
            fields, before, after = _top_level_diff(old, clean)
            if row["body"] != body:
                fields.append(BODY_KEY)
                before[BODY_KEY] = row["body"]
                after[BODY_KEY] = body
            if not fields and not row["deleted"]:
                return
            seq = self._record_change(kind, ident, "import", fields, before, after)
            self.connection.execute(
                "UPDATE entities SET epic=?,status=?,archived=?,deleted=0,doc=?,body=?,"
                "rev=rev+1,updated_seq=? WHERE kind=? AND id=?",
                (
                    clean.get("epic"),
                    clean.get("status"),
                    int(bool(clean.get("archived"))),
                    _json(clean),
                    body,
                    seq,
                    kind,
                    ident,
                ),
            )
        self.seq = seq
        self.log_entries.append(f"imported {kind}:{ident} at seq {seq}")
        self._derived_keys.add((kind, ident))

    def _record_change(
        self,
        kind: str,
        ident: str,
        op: str,
        fields: list[str],
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO changes(ts,session,tool,kind,id,op,fields,before,after) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                _now(),
                self.session if op != "import" else "<external>",
                self.tool,
                kind,
                ident,
                op,
                _json(fields),
                _json(before),
                _json(after),
            ),
        )
        return int(cursor.lastrowid)

    def _mark(self, kind: str, ident: str) -> None:
        key = (kind, ident)
        self._committed_keys.add(key)
        self._export_keys.add(key)
        self._derived_keys.add(key)

    def _capture_committed(self) -> None:
        """Snapshot return payloads while this writer still owns the lock."""
        for kind, ident in self._committed_keys:
            row = self.connection.execute(
                "SELECT doc,body,deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
            ).fetchone()
            if row and not row["deleted"]:
                doc = _from_json(row["doc"], {})
                if row["body"]:
                    doc[BODY_KEY] = row["body"]
                self._pending_committed[(kind, ident)] = doc

    def _finish_committed(self) -> None:
        self.committed.update(self._pending_committed)
        self._pending_committed.clear()
        for entry in self.log_entries:
            self.store._log(entry)
        self.log_entries.clear()
        self._replaced_files.clear()
        self._intent_entries.clear()
        try:
            self._intent_path.unlink(missing_ok=True)
        except OSError as exc:
            # This is post-commit housekeeping.  Never report a successful,
            # possibly non-idempotent mutation as failed because cleanup was
            # temporarily blocked; the next writer reconciles the intent.
            self.store._log(
                f"post-commit intent cleanup pending {self._intent_path.name}: {exc!r}"
            )

    def _restore_replaced(self) -> None:
        """Best-effort restoration when DB rollback follows a file replace."""
        for path, prior in reversed(tuple(self._replaced_files.items())):
            try:
                if prior is None:
                    path.unlink(missing_ok=True)
                    continue
                restore = path.with_name(f"{path.name}.tmp.rollback-{self.session}")
                with restore.open("wb") as handle:
                    handle.write(prior)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore, path)
            except OSError:
                # The unchanged projection metadata remains in the rolled-back
                # DB, so a later scan still detects and repairs this file.
                pass
        self._replaced_files.clear()
        self._intent_entries.clear()
        self._intent_path.unlink(missing_ok=True)


def _clean_doc(doc: Mapping[str, Any]) -> dict[str, Any]:
    return _v4_strip_private_fields(dict(doc), preserve_body=False)


def _split_body(doc: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    materialized = copy.deepcopy(dict(doc))
    body = materialized.pop(BODY_KEY, None)
    if isinstance(body, str):
        body = body.removesuffix("\n") or None
    return _clean_doc(materialized), body


def _top_level_diff(
    before_doc: Mapping[str, Any], after_doc: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    missing = object()
    fields: list[str] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for key in sorted(set(before_doc) | set(after_doc)):
        old = before_doc.get(key, missing)
        new = after_doc.get(key, missing)
        if old == new:
            continue
        fields.append(key)
        if old is not missing:
            before[key] = old
        if new is not missing:
            after[key] = new
    return fields, before, after


def _merge_change_details(
    base_doc: Mapping[str, Any],
    our_doc: Mapping[str, Any],
    their_doc: Mapping[str, Any],
    merged_doc: Mapping[str, Any],
    *,
    base_body: str | None = None,
    our_body: str | None = None,
    their_body: str | None = None,
    merged_body: str | None = None,
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Describe a merge without discarding the foreign side of conflicts."""
    fields, before, after = _top_level_diff(our_doc, merged_doc)
    if our_body != merged_body:
        fields.append(BODY_KEY)
        before[BODY_KEY] = our_body
        after[BODY_KEY] = merged_body

    missing = object()
    conflicts: dict[str, Any] = {}

    def preserved(value: Any) -> dict[str, Any]:
        return {"present": value is not missing, "value": None if value is missing else value}

    for key in sorted(set(base_doc) | set(our_doc) | set(their_doc)):
        base = base_doc.get(key, missing)
        ours = our_doc.get(key, missing)
        theirs = their_doc.get(key, missing)
        if ours != base and theirs != base and ours != theirs:
            conflicts[key] = {
                "base": preserved(base),
                "ours": preserved(ours),
                "theirs": preserved(theirs),
            }
            marker = f"conflict:{key}"
            if marker not in fields:
                fields.append(marker)
    if our_body != base_body and their_body != base_body and our_body != their_body:
        conflicts[BODY_KEY] = {
            "base": preserved(base_body),
            "ours": preserved(our_body),
            "theirs": preserved(their_body),
        }
        marker = f"conflict:{BODY_KEY}"
        if marker not in fields:
            fields.append(marker)
    if conflicts:
        before["_conflicts"] = conflicts
    return fields, before, after


def _flatten_backlog_dict(
    data: Mapping[str, Any],
) -> dict[tuple[str, str], tuple[dict[str, Any], str | None]]:
    result: dict[tuple[str, str], tuple[dict[str, Any], str | None]] = {}

    def claim(key: tuple[str, str], value: tuple[dict[str, Any], str | None]) -> None:
        # Two documents under one id used to collapse silently here, which turned
        # an accidental duplicate create into an update that replaced the live
        # entity's fields.  A duplicate is never a legal write-back.
        if key in result:
            raise ValueError(
                f"{key[0]} {key[1]} appears twice in the backlog dict; a create "
                f"cannot reuse an existing id"
            )
        result[key] = value

    backlog_doc = {
        key: copy.deepcopy(value)
        for key, value in data.items()
        if key not in {"epics", "phases", "context"}
        and not (isinstance(key, str) and key.startswith("_"))
    }
    if isinstance(backlog_doc.get("meta"), dict):
        backlog_doc["meta"].pop("updated", None)
    claim(("backlog", _BACKLOG_ID), (_clean_doc(backlog_doc), None))
    for epic in data.get("epics") or []:
        epic_doc = {key: copy.deepcopy(value) for key, value in epic.items() if key != "tasks"}
        epic_doc, body = _split_body(epic_doc)
        ident = str(epic_doc.get("id") or "")
        if ident:
            claim(("epic", ident), (epic_doc, body))
        for task in epic.get("tasks") or []:
            task_doc, task_body = _split_body(task)
            task_id = str(task_doc.get("id") or "")
            if task_id:
                task_doc.setdefault("epic", ident)
                claim(("task", task_id), (task_doc, task_body))
    for phase in data.get("phases") or []:
        phase_doc, body = _split_body(phase)
        ident = str(phase_doc.get("id") or "")
        if ident:
            claim(("phase", ident), (phase_doc, body))
    return result


def _is_archive_path(path: Path, backlog_dir: Path) -> bool:
    parts = path.relative_to(backlog_dir).parts[:-1]
    return any(part in {"archive", "_archive"} for part in parts)
