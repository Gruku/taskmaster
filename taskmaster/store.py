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
# The git probes moved to `taskmaster.root`, which uses this same module
# object; tests that simulate a missing repository patch `store.subprocess`.
import subprocess  # noqa: F401
import threading
import time
import uuid
import weakref
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import yaml

from taskmaster import yaml_io
from taskmaster.paths import (
    as_list,
    extract_prose_paths,
    normalize_location,
    normalize_task_anchor,
)
# Root resolution lives in a standard-library-only module so the hooks, which
# run under the system interpreter, share this exact rule without importing
# yaml.  Re-exported here: `store.resolve_root` stays the public entry point,
# and the module globals stay monkeypatchable for the callers below.
from taskmaster.root import (  # noqa: F401
    DB_RELPATH,
    RootResolution,
    _absolute,
    _backlog_dir,
    _cloud_filesystem_reason,
    _git_checkout_root,
    _git_common_root,
    _network_filesystem_reason,
    db_path,
    resolve_root,
)
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
BUSY_TIMEOUT_MS = 30_000
HEARTBEAT_INTERVAL_SECONDS = 20.0
_MONOTONIC = time.monotonic
_BACKLOG_ID = "__backlog__"
_PROJECT_ID = "__project__"
_RETRYABLE_REPLACE_ERRNOS = {5, 13, 32, errno.EACCES, errno.EPERM}

# How many times a projection-only read re-runs when the files moved underneath
# it. A share that changes three times during one read is being written
# continuously; a fourth attempt would not settle either.
_PROJECTION_IDENTITY_ATTEMPTS = 3

# Enough of a file to tell CRLF from LF without reading a 977 KB changelog back
# on every export: the first newline decides.
_LINE_ENDING_PROBE_BYTES = 8192


def _uses_crlf(path: Path) -> bool:
    """True when the file on disk already uses CRLF line endings."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_LINE_ENDING_PROBE_BYTES)
    except OSError:
        return False
    index = head.find(b"\n")
    return index > 0 and head[index - 1] == 0x0D


def _match_line_endings(content: bytes, path: Path) -> bytes:
    """`content` (rendered with LF) in the line-ending style `path` already has.

    A file that does not exist yet keeps LF: there is nothing to match, and LF
    is what the repository stores.
    """
    if not _uses_crlf(path):
        return content
    return content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
_CORRUPTION_MARKERS = ("malformed", "not a database", "file is encrypted")
# `ideas/IDEAS.md` is derived output, not an entity: the exporter regenerates it
# from the idea rows and the scan refreshes its hash without ever parsing it.
_IDEAS_INDEX_KIND = "ideas-index"
_IDEAS_INDEX_REL = "ideas/IDEAS.md"
_PROGRESS_REL = "local/PROGRESS.md"
_LINEAR_QUEUE_RECEIPT_KEY = "linear_queue_import_receipt"
# How long a drain's claim on a queue row survives without an outcome. Past
# it the row goes back to `pending`, so a drain killed mid-push strands
# nothing; it is well beyond any single HTTP round-trip.
LINEAR_CLAIM_LEASE_SECONDS = 900.0
_PROGRESS_LOG_KEY = "pending_progress_log"
_PROGRESS_APPLIED_KEY = "progress_log"
# The session-log region of PROGRESS.md shows this many entries. Bounded so one
# meta row cannot grow without limit on a long-lived project.
_PROGRESS_LOG_CAP = 200
_LINEAR_QUEUE_REL = "integrations/linear-queue.json"
# Tables `_refresh_derived` owns outright: every row in them is recomputed from
# `entities`, so dropping and rebuilding them can never lose authoritative state.
# `backlog_index_status` reports exactly this list.
DERIVED_TABLES = ("entity_fts", "entity_paths", "links", "related", "handover_tasks")
_DERIVED_REBUILT_KEY = "derived_rebuilt_at"
# Document fields whose prose is worth matching in `backlog_search`. `branch` and
# the `docs` values are here because the substring search this FTS index replaced
# scored them directly, and dropping them silently lost `search <branch-name>`.
_FTS_PROSE_FIELDS = (
    "description",
    "notes",
    "review_instructions",
    "next_action",
    "tldr",
    "impact",
    "evidence",
    "options",
    "branch",
    "done_when",
)
# Kinds whose rows the compatibility dict carries verbatim under `_rows`, so a
# list/get tool reads committed store state instead of re-parsing markdown.
_DICT_ROW_KINDS = (
    "bug",
    "issue",
    "handover",
    "decision",
    "idea",
    "note",
    "area",
    "tracker",
)


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
  last_error TEXT,
  claimed_by TEXT,
  claimed_at REAL
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


class AdoptionRoundTripError(RuntimeError):
    """Adoption rendered a file it cannot read back, so it refuses to commit.

    Adoption rewrites the whole projection in one transaction — 2,300 files on
    a real backlog — and used to commit that tree without ever proving it could
    reopen it. When it could not, the projection was unloadable and the store
    that could still answer was gone the moment `local/` was cleaned or the repo
    was cloned somewhere else. Refusing the adoption leaves the user with the
    tree they started from, which they can still open with the previous version.
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
    linear_pending: int = 0


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


def _json_default(value: Any) -> str:
    """Render what YAML produced but JSON cannot hold, as its ISO-8601 text.

    An unquoted `date: 2026-04-26T16:40:00Z` in a handover's frontmatter parses
    to a `datetime`, and the import that met one raised `TypeError` out of
    `_record_change` — taking down the whole bootstrap, not just that file. A
    hand-edited timestamp must not cost a project its store.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


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


SQLITE_HEADER = b"SQLite format 3\x00"


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


def _resolve_for(backlog_path: Path | None, root: Path | None) -> RootResolution:
    """Where this project's store lives, without opening or creating anything.

    Split out of `open_store` so a read-only caller can learn the root, the
    resolution source and the database path without `_ensure_open` running --
    that call creates the database, preps the schema, recovers export intents
    and can move a damaged file aside, none of which a diagnostic may do.
    `_STATE_LOCK` is re-entrant, so `open_store` may already hold it.
    """
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
        return resolved


def open_store(
    backlog_path: Path | None = None,
    *,
    root: Path | None = None,
    session: str | None = None,
) -> "Store":
    with _STATE_LOCK:
        resolved = _resolve_for(backlog_path, root)
        path = db_path(resolved.backlog_path)
        instance = _STORES.get(path)
        if instance is None:
            instance = Store(resolved, session=session)
            _STORES[path] = instance
        instance._ensure_open()
        return instance


def read_only_status(
    backlog_path: Path | None = None, *, root: Path | None = None
) -> StoreStatus:
    """`Store.read_only_status` for a project that may never have been opened.

    `open_store` opens as a side effect, so a diagnostic cannot go through it.
    An already-open store is reused when there is one -- it reads through its
    own read-only connection either way -- and otherwise a throwaway `Store` is
    built from the resolution alone and never registered.
    """
    with _STATE_LOCK:
        resolved = _resolve_for(backlog_path, root)
        instance = _STORES.get(db_path(resolved.backlog_path))
    return (instance or Store(resolved)).read_only_status()


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
        # Adoption rewrites the whole projection at once and has to prove it can
        # read the result back before it commits (see `_verify_round_trip`).
        # Ordinary writes touch a file or two and are re-read by the next scan,
        # so they do not pay for the check.
        self._verify_exports = False
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
            try:
                self._bootstrap(connection)
            except AdoptionRoundTripError:
                # A refused adoption must leave the project exactly as it was.
                # `open_store` created and committed the schema before bootstrap
                # began, so refusing left an empty-but-valid `store.db` behind —
                # and the merge gate reads an existing store as authoritative
                # (it fails open on one it cannot use) rather than falling back
                # to the projection, so the leftover silently disabled the gate.
                if not database_existed:
                    self._discard_empty_database(connection)
                raise
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

    # Columns added to an existing table after the first release. `CREATE
    # TABLE IF NOT EXISTS` cannot add them and the queue rows are not projected,
    # so a schema rebuild would destroy pending pushes: they are applied
    # additively on every open instead.
    _ADDED_COLUMNS = (
        ("linear_queue", "claimed_by", "TEXT"),
        ("linear_queue", "claimed_at", "REAL"),
    )

    @classmethod
    def _execute_schema(cls, connection: sqlite3.Connection) -> None:
        for statement in SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        for table, column, decl in cls._ADDED_COLUMNS:
            existing = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {decl}"
                )

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

    def _discard_empty_database(self, connection: sqlite3.Connection) -> None:
        """Delete the database this open created, after a refused adoption.

        The schema is created and committed before `_bootstrap` runs, so a
        refusal otherwise leaves an empty-but-valid `store.db` on disk. Both
        hooks treat an existing store as the authority — `merge_gate_decide`
        fails *open* on one it cannot use rather than reading the projection,
        and `merge_recorder_stamp` records nothing without one — so the leftover
        turns a refused adoption into a silently disabled merge gate. Removing
        it puts the project back in the state the hooks handle correctly:
        no store, read the files.

        Best effort. A file another process still holds is left alone; the empty
        store is a nuisance, and failing the refusal over it would replace a
        clear error with an obscure one.
        """
        try:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        except sqlite3.Error:
            pass
        close_thread_connection()
        for suffix in ("-wal", "-shm", ""):
            try:
                Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
            except OSError:
                self._log(f"could not remove {self.db_path}{suffix} after a refused adoption")
        self._bootstrapped = False

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

    def _relocate_machine_local_state(self, tx: "Transaction | None" = None) -> None:
        """Move a pre-v4 project's machine-local files under `local/`.

        `viewer.json` and `auto/` are per-machine state, not shared backlog
        content, and v4 put them under `local/`. This ran in the old
        `migrate_v3_to_v4` file writer; adoption is the only migration now, so
        it has to run here or the reader — which looks under `local/` as soon as
        the project reads as v4 — silently loses the user's saved viewer prefs
        to a fresh set of defaults. `snapshots/` is the retired pre-v4 backup
        directory and moves with them.

        Nothing here deletes. This used to `rmtree` `snapshots/`, which was
        defensible while only an operator calling `backlog_migrate_v4` could
        reach it; it now runs from `_bootstrap` the first time *any* tool —
        a read tool, a viewer GET — opens a pre-v4 project, with no
        confirmation and outside the SQL rollback, and `snapshots/` is the one
        directory that could have recovered the project from a bad adoption.
        Absence never deletes data (design spec decision 4).
        """
        root = self.backlog_path
        target = root / "local"
        # `PROGRESS.md` moves with them: `_progress_path` returns
        # `local/PROGRESS.md` the moment the project reads as v4, so leaving the
        # file at its pre-v4 path strands it. The next session log then creates
        # an empty one and the user's whole changelog history vanishes from the
        # dashboard while the real file sits unreferenced beside it.
        for name in ("viewer.json", "auto", "snapshots", "PROGRESS.md"):
            source = root / name
            if not source.exists():
                continue
            target.mkdir(parents=True, exist_ok=True)
            destination = target / name
            if destination.exists():
                if name in ("viewer.json", "auto"):
                    # Machine-local state already relocated by an earlier open:
                    # the destination is the live copy and wins.
                    continue
                # A backup or a changelog is never overwritten, and never
                # dropped because the name is taken: it goes to the first free
                # suffix, so the operator can reconcile the two by hand.
                stem, dot, extension = name.partition(".")
                suffix = 2
                while (target / f"{stem}-{suffix}{dot}{extension}").exists():
                    suffix += 1
                destination = target / f"{stem}-{suffix}{dot}{extension}"
            try:
                os.replace(source, destination)
            except OSError:
                # Losing the relocation is survivable (defaults are rebuilt,
                # and a backup left in place is still a backup); failing the
                # whole store open over it is not.
                continue
            if name in ("snapshots", "PROGRESS.md") and tx is not None:
                relative = destination.relative_to(root).as_posix()
                note = f"moved `{name}` to `{relative}`"
                tx.warnings.append(note)
                tx.log_entries.append(note)

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
        self._verify_exports = True
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
                self._relocate_machine_local_state(tx)
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
        finally:
            self._verify_exports = False
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
            if not committed:
                if connection.in_transaction:
                    connection.rollback()
                # The throttle clock is in memory and the rollback is not, so a
                # failed commit would otherwise suppress the next dashboard
                # export and leave PROGRESS.md showing work that never landed.
                self._last_progress_clock = None
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
            # The payload and the ETag have to describe one revision. Reading
            # the documents first and stamping them with an identity taken
            # afterwards returned old content under the new revision's ETag, so
            # a client cached the stale payload and its `If-Match` write still
            # passed. Bracket the read instead, and retry while the files move.
            for _attempt in range(_PROJECTION_IDENTITY_ATTEMPTS):
                before, _seq = self._projection_identity()
                data = self._bootstrap_backlog_data()
                data.setdefault("context", {})
                # Every non-task read tool serves `_rows` now. Without one here
                # the bugs, issues, handovers, decisions, ideas, notes, areas
                # and trackers sitting on this network share would read as
                # absent.
                data["_rows"] = self._entity_rows_from_projection()
                after, seq = self._projection_identity()
                if after == before:
                    if _CONTEXT_BUILDER is not None:
                        _CONTEXT_BUILDER(data)
                    return data, after, seq
            # A share being written continuously still has to answer. The last
            # read is stamped with the identity taken *before* it — the
            # conservative half of the pair: an ETag older than the payload
            # makes a later `If-Match` fail, where a newer one would let a write
            # built on a mixed read through.
            if _CONTEXT_BUILDER is not None:
                _CONTEXT_BUILDER(data)
            return data, before, seq
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
                # Already current: reuse it, but still fall through so the row
                # map below is attached. Returning early here handed read tools
                # a dict with no `_rows` at all.
                data = cached[2]
                publish = False
            else:
                data = self._refresh_cached_dict(connection, cached[2], cached[1])
        else:
            data = self._load_dict_from_connection(connection)
        if publish:
            _CACHE[self.db_path] = (token, max_seq, copy.deepcopy(data))
        result = copy.deepcopy(data)
        # Attached outside the cache entry so the incremental refresh above never
        # has to keep it in step: it is one query against the same snapshot.
        result["_rows"] = self._entity_rows_from_connection(connection)
        return result

    def _entity_rows_from_connection(
        self, connection: sqlite3.Connection
    ) -> dict[str, dict[str, tuple[dict[str, Any], str | None]]]:
        """`{kind: {id: (doc, body)}}` for every non-task entity kind.

        Read tools for bugs, issues, handovers, decisions, ideas, notes, areas
        and trackers render from this instead of globbing their directory, so a
        read and the write that follows it see one snapshot. Archived rows are
        included; their document carries `archived: True` and callers filter.
        """
        rows: dict[str, dict[str, tuple[dict[str, Any], str | None]]] = {
            kind: {} for kind in _DICT_ROW_KINDS
        }
        placeholders = ",".join("?" for _ in _DICT_ROW_KINDS)
        for row in connection.execute(
            f"SELECT kind,id,doc,body FROM entities WHERE deleted=0 AND kind IN ({placeholders}) "
            "ORDER BY id",
            _DICT_ROW_KINDS,
        ):
            rows[row["kind"]][row["id"]] = (_from_json(row["doc"], {}), row["body"])
        return rows

    def _projection_identity(self) -> tuple[str, int]:
        """A `(token, seq)` pair that moves whenever the projection does.

        There is no `changes` table on a network root, so the database identity
        every caller stamps on a read degrades to `("", 0)` — a constant, which
        made the viewer's ETag the fixed string `":0"` for the life of the
        project. Stat the projection instead: name, size and mtime of
        `backlog.yaml` and every entity file, digested. It is not a sequence
        number and never claims to be one — the token is prefixed `projection-`
        so nothing mistakes it for a store token — but it does change when the
        data changes, which is the whole contract an ETag has to keep.
        """
        digest = hashlib.sha256()
        paths = [self.backlog_path / "backlog.yaml"]
        paths.extend(path for _kind, _ident, path in self._known_entity_files())
        for path in sorted(paths):
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(str(path).encode("utf-8", "replace"))
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
        return f"projection-{digest.hexdigest()[:16]}", 0

    def _entity_rows_from_projection(
        self,
    ) -> dict[str, dict[str, tuple[dict[str, Any], str | None]]]:
        """`{kind: {id: (doc, body)}}` built from the projection files alone.

        The read-only shape of `_entity_rows_from_connection`, for a network
        root with no usable database. Archived entities are included and carry
        `archived: True`, exactly as the database rows do, so the callers that
        filter on it behave the same either way. A file that will not parse is
        skipped rather than failing the whole read: the alternative is a listing
        that raises instead of showing the entities that are fine.
        """
        rows: dict[str, dict[str, tuple[dict[str, Any], str | None]]] = {
            kind: {} for kind in _DICT_ROW_KINDS
        }
        for kind, ident, path in self._known_entity_files():
            if kind not in rows:
                continue
            try:
                doc, body = self._parse_entity_file(kind, path)
                self._validate_projected_identity(kind, ident, doc)
            except (OSError, ValueError, yaml.YAMLError):
                continue
            if _is_archive_path(path, self.backlog_path):
                doc["archived"] = True
            rows[kind][ident] = (doc, body)
        return {kind: dict(sorted(entries.items())) for kind, entries in rows.items()}

    def update_root_config(self, name: str, mutate: "Callable[[dict], dict]") -> dict:
        """Read-modify-write one root config file under the cross-process lock.

        `linear.yaml` sits beside the projection and is shared by every agent
        on the repo. Reading it, appending a workspace and truncating it back
        outside any lock let two agents both report success while only one
        addition survived. Holding the writer mutex across the whole cycle is
        what makes the second one see the first.

        `mutate` receives the current document (empty when the file is absent)
        and returns what to write; raising from it leaves the file untouched.
        Returns the document written.
        """
        _validate_safe_identifier(Path(name).stem)
        if Path(name).name != name or not name.endswith((".yaml", ".yml")):
            raise ValueError(f"not a root config file name: {name!r}")
        self._ensure_open()
        path = self.backlog_path / name
        with self._writer_mutex():
            current: dict[str, Any] = {}
            if path.exists():
                try:
                    loaded = yaml_io.safe_load(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, yaml.YAMLError) as exc:
                    raise ValueError(f"cannot read {name}: {exc}") from exc
                if loaded is not None and not isinstance(loaded, dict):
                    raise ValueError(f"{name} top-level must be a mapping")
                current = loaded or {}
            updated = mutate(current)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = yaml.dump(
                updated, default_flow_style=False, sort_keys=False, allow_unicode=True
            )
            temp = path.with_name(f"{path.name}.tmp.{self.session}")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        return updated

    def write_local_cache(self, name: str, data: bytes) -> None:
        """Write one derived file under `local/cache/`, atomically.

        Nothing reads these back as authority — they exist so an external tool
        can cheaply see when the backlog last changed. It lives here because
        `store.py` is the only module allowed to write inside `.taskmaster/`.
        """
        # The whole name, not just its stem: a separator anywhere in it would
        # let a caller write outside `local/cache/`.
        _validate_safe_identifier(name)
        cache_dir = self.db_path.parent / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / name
        temp = target.with_name(f"{target.name}.tmp.{self.session}")
        try:
            with temp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

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

    def scan_for_read(self) -> None:
        """Import hand edits before a read that does not go through `load_dict`.

        `load_dict_with_identity` runs this on the way past, so every tool that
        reads the compatibility dict adopts a hand-edited file for free. A tool
        that queries the tables directly -- `backlog_query` -- has to ask, or it
        serves pre-edit rows while the files on disk say otherwise (spec §3.4).
        Throttled and best-effort, exactly as the dict path is.
        """
        self._ensure_open()
        if self._network_projection_only:
            return
        self._maybe_scan_on_read()

    _LINEAR_COLUMNS = (
        "SELECT seq,op,target_id,tracker_id,payload,state,attempts,last_error,"
        "claimed_by,claimed_at FROM linear_queue"
    )

    @staticmethod
    def _linear_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "seq": int(row["seq"]),
            "op": row["op"],
            "target_id": row["target_id"],
            "tracker_id": row["tracker_id"],
            "payload": _from_json(row["payload"], None),
            "state": row["state"],
            "attempts": int(row["attempts"] or 0),
            "last_error": row["last_error"],
            "claimed_by": row["claimed_by"],
            "claimed_at": row["claimed_at"],
        }

    def projection_only_reason(self) -> str | None:
        """Why this store cannot be queried or written, or None when it can.

        On a network root with no usable database the store degrades to reading
        the projection files, and the queue tables simply are not there.  A
        caller that would otherwise hit `unable to open database file` asks this
        first and says something useful instead.
        """
        self._ensure_open()
        if not self._network_projection_only:
            return None
        return (
            _network_filesystem_reason(self.root)
            or "store.db is unavailable, so only the projection files can be read"
        )

    def linear_rows(
        self, *, states: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Every Linear queue row, oldest first, optionally filtered by state.

        `linear_pending` is the drain's read; this is the reporting read, so
        parked (`failed`) and settled (`done`) rows stay visible to
        `backlog_linear_status` and to the un-park that `/linear retry` runs.

        An explicitly empty `states` means "no state qualifies" and returns
        nothing, rather than building `IN ()` and raising a syntax error.
        """
        self._ensure_open()
        if self._network_projection_only or (states is not None and not states):
            return []
        sql = self._LINEAR_COLUMNS
        params: tuple[Any, ...] = ()
        if states is not None:
            placeholders = ",".join("?" for _ in states)
            sql += f" WHERE state IN ({placeholders})"
            params = tuple(states)
        return [
            self._linear_row(row)
            for row in self.connection.execute(sql + " ORDER BY seq", params)
        ]

    def linear_requeue(self, seqs: Iterable[int]) -> int:
        """Return the given queue rows to `pending` with a cleared attempt count.

        The un-park action behind `/linear retry`: parking is what stops a dead
        push from burning round-trips, so the only way back is explicit.  Its own
        short transaction, like `linear_mark`, so no HTTP is ever held under the
        writer lock.  Returns how many rows changed.

        Any claim is dropped with the state: an explicit retry outranks a drain
        that still holds the row, and clearing the owner is what stops that
        drain from later marking a row that is pending again as settled.
        """
        self._ensure_open()
        degraded = self.projection_only_reason()
        if degraded:
            raise RuntimeError(f"cannot write the Linear queue: {degraded}")
        wanted = [int(seq) for seq in seqs]
        if not wanted:
            return 0
        if self.connection.in_transaction:
            raise RuntimeError("linear_requeue needs its own transaction")
        with self._writer_mutex():
            connection = self.connection
            if connection.in_transaction:
                raise RuntimeError("linear_requeue needs its own transaction")
            self._recover_export_intents(connection)
            self._begin_immediate(connection)
            try:
                placeholders = ",".join("?" for _ in wanted)
                cursor = connection.execute(
                    "UPDATE linear_queue SET state='pending',attempts=0,"
                    "last_error=NULL,claimed_by=NULL,claimed_at=NULL"
                    f" WHERE seq IN ({placeholders})",
                    tuple(wanted),
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return int(cursor.rowcount)

    def linear_claim(
        self,
        limit: int = 100,
        *,
        targets: Sequence[str] | None = None,
        owner: str,
        lease_seconds: float = LINEAR_CLAIM_LEASE_SECONDS,
    ) -> list[dict[str, Any]]:
        """Take ownership of up to `limit` pending pushes, oldest first.

        One `BEGIN IMMEDIATE` moves the rows out of `pending` and stamps them
        with `owner`, which is what makes a drain safe:

          - a second drain running concurrently sees no pending row for them,
            so the same push is never issued twice;
          - an edit landing mid-push cannot be de-duped onto a claimed row
            (`linear_enqueue` folds only into `pending`), so it gets a request
            of its own instead of being marked done unsent.

        The claim is a lease, not a lock: a drain that dies mid-push would
        otherwise strand its rows forever, so a claim older than
        `lease_seconds` is returned to `pending` before this call selects.
        """
        self._ensure_open()
        degraded = self.projection_only_reason()
        if degraded:
            raise RuntimeError(f"cannot write the Linear queue: {degraded}")
        if targets is not None and not targets:
            return []
        if self.connection.in_transaction:
            raise RuntimeError("linear_claim needs its own transaction")
        with self._writer_mutex():
            connection = self.connection
            if connection.in_transaction:
                raise RuntimeError("linear_claim needs its own transaction")
            self._recover_export_intents(connection)
            self._begin_immediate(connection)
            try:
                now = time.time()
                connection.execute(
                    "UPDATE linear_queue SET state='pending',claimed_by=NULL,"
                    "claimed_at=NULL WHERE state='claimed' "
                    "AND (claimed_at IS NULL OR claimed_at <= ?)",
                    (now - lease_seconds,),
                )
                sql = self._LINEAR_COLUMNS + " WHERE state='pending'"
                params: list[Any] = []
                if targets is not None:
                    sql += " AND target_id IN (" + ",".join("?" for _ in targets) + ")"
                    params.extend(targets)
                params.append(limit)
                rows = [
                    self._linear_row(row)
                    for row in connection.execute(sql + " ORDER BY seq LIMIT ?", params)
                ]
                for row in rows:
                    connection.execute(
                        "UPDATE linear_queue SET state='claimed',claimed_by=?,"
                        "claimed_at=? WHERE seq=?",
                        (owner, now, row["seq"]),
                    )
                    row["state"] = "claimed"
                    row["claimed_by"] = owner
                    row["claimed_at"] = now
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return rows

    def linear_pending(
        self, limit: int = 100, *, targets: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """The oldest pending Linear pushes, `limit` at most, oldest first.

        `targets` filters before the limit, not after: a scoped retry whose rows
        sit behind hundreds of other pending pushes has to find them, and a
        filter applied to an already-truncated page would silently drain
        nothing and report success.
        """
        self._ensure_open()
        if self._network_projection_only or (targets is not None and not targets):
            return []
        sql = self._LINEAR_COLUMNS + " WHERE state='pending'"
        params: list[Any] = []
        if targets is not None:
            sql += " AND target_id IN (" + ",".join("?" for _ in targets) + ")"
            params.extend(targets)
        params.append(limit)
        return [
            self._linear_row(row)
            for row in self.connection.execute(sql + " ORDER BY seq LIMIT ?", params)
        ]

    def linear_mark(
        self,
        seq: int,
        *,
        state: str,
        error: str | None = None,
        owner: str | None = None,
    ) -> bool:
        """Record the outcome of one drain attempt on queue row `seq`.

        Its own short `BEGIN IMMEDIATE` so a drain that spends seconds in HTTP
        never holds the writer lock across a round-trip.  The store keeps no
        retry policy: the caller chooses the state, and `attempts` counts only
        *failed* attempts -- a mark carrying an `error`.  A success mark leaves
        the counter alone, so a row that succeeded on its first try never looks
        like it burned a retry.

        With `owner`, the mark only lands while that owner still holds the
        claim: a drain whose lease expired and was taken over by another must
        not settle the row the new owner is pushing.  Returns whether the mark
        landed; a row that does not exist at all still raises.
        """
        self._ensure_open()
        degraded = self.projection_only_reason()
        if degraded:
            raise RuntimeError(f"cannot write the Linear queue: {degraded}")
        if self.connection.in_transaction:
            raise RuntimeError("linear_mark needs its own transaction")
        with self._writer_mutex():
            # Re-read under the mutex, as `transaction` does: a recovery that
            # finished while this caller queued for the lock closes the handle
            # we would otherwise have captured before waiting.
            connection = self.connection
            if connection.in_transaction:
                raise RuntimeError("linear_mark needs its own transaction")
            # Same recovery the writing `transaction` path runs before it takes
            # the lock: this is a full BEGIN IMMEDIATE, so an export interrupted
            # by a crash must be reconciled here too rather than waiting for the
            # next entity write.
            self._recover_export_intents(connection)
            self._begin_immediate(connection)
            try:
                sql = (
                    "UPDATE linear_queue SET state=?,last_error=?,"
                    "attempts=attempts+?,claimed_by=NULL,claimed_at=NULL WHERE seq=?"
                )
                params: list[Any] = [
                    state, error, 1 if error is not None else 0, seq
                ]
                if owner is not None:
                    sql += " AND claimed_by=?"
                    params.append(owner)
                cursor = connection.execute(sql, params)
                if cursor.rowcount == 0:
                    exists = connection.execute(
                        "SELECT 1 FROM linear_queue WHERE seq=?", (seq,)
                    ).fetchone()
                    if exists is None:
                        raise KeyError(f"linear queue row {seq} not found")
                    connection.rollback()
                    return False
                connection.commit()
                return True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def _import_linear_queue(self, tx: "Transaction") -> None:
        """Adopt a legacy `integrations/linear-queue.json` into `linear_queue`.

        Called from bootstrap and from every scan, because a project migrated
        before the table existed still has its pending pushes on disk.

        The file is the only copy of those pending pushes, so it is never
        removed inside this transaction: a crash between the removal and COMMIT
        would roll the rows back with the file already gone.  Instead the
        transaction commits a receipt naming the digest it imported, and the
        removal runs after COMMIT.  A file that outlives its own receipt -- a
        crash in that window -- is recognised on the next scan and removed
        without importing its entries twice.
        """
        path = self.backlog_path / _LINEAR_QUEUE_REL
        receipt = self._linear_queue_receipt(tx.connection)
        if not path.exists():
            if receipt is not None:
                # The removal landed; the receipt has nothing left to protect.
                tx.connection.execute(
                    "DELETE FROM meta WHERE key=?", (_LINEAR_QUEUE_RECEIPT_KEY,)
                )
            return
        try:
            prior = path.read_bytes()
            raw = json.loads(prior.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            # Leaving an unreadable file in place made every later transaction
            # re-parse it and re-warn forever.  Move it aside once, under a name
            # the import never looks at, so the failure is preserved for a human
            # but costs nothing on the next scan.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
            try:
                path.rename(quarantine)
            except OSError as move_error:  # pragma: no cover - filesystem refusal
                tx.log_entries.append(
                    f"linear queue quarantine failed: {move_error!r}"
                )
            else:
                tx.log_entries.append(
                    f"quarantined unreadable {_LINEAR_QUEUE_REL} as {quarantine.name}"
                )
            tx.warnings.append(f"linear queue import failed: {exc}")
            tx.log_entries.append(f"linear queue import failed: {exc!r}")
            if receipt is not None:
                tx.connection.execute(
                    "DELETE FROM meta WHERE key=?", (_LINEAR_QUEUE_RECEIPT_KEY,)
                )
            return
        digest = hashlib.sha1(prior).hexdigest()
        if receipt == digest:
            # These exact bytes were imported by a transaction that committed;
            # only its post-commit removal was lost.  Re-importing would
            # duplicate every push it already enqueued.
            tx._post_commit_removals.append(path)
            tx.log_entries.append(
                f"{_LINEAR_QUEUE_REL} already imported (receipt {digest[:12]}); removing"
            )
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
            if item.get("permanent"):
                # The legacy queue's terminal state. Importing it as pending
                # hands a dead push straight back to the next drain and hides
                # it from the parked count the status tool reports.
                tx.connection.execute(
                    "UPDATE linear_queue SET state='failed' WHERE seq=?", (seq,)
                )
            if attempts or last_error:
                # Two legacy entries can share (op, target_id); `linear_enqueue`
                # folds them onto one row, so keep the worst history rather than
                # letting the last one seen reset the attempt count to zero and
                # un-park a push that had already exhausted its retries.
                tx.connection.execute(
                    "UPDATE linear_queue SET attempts=MAX(attempts,?),"
                    "last_error=COALESCE(?,last_error) WHERE seq=?",
                    (attempts, last_error, seq),
                )
        tx.connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_LINEAR_QUEUE_RECEIPT_KEY, digest),
        )
        tx._post_commit_removals.append(path)
        tx.log_entries.append(f"imported {_LINEAR_QUEUE_REL}; removing after commit")

    def _linear_queue_receipt(self, connection: sqlite3.Connection) -> str | None:
        """Digest of the legacy queue file whose import last committed, if any."""
        row = connection.execute(
            "SELECT value FROM meta WHERE key=?", (_LINEAR_QUEUE_RECEIPT_KEY,)
        ).fetchone()
        return None if row is None else str(row[0])

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
        for name in ("backlog.yaml", "project.yaml", _IDEAS_INDEX_REL):
            path = self.backlog_path / name
            if path.exists():
                actual[name] = path
        # `_known_entity_files` deliberately lists only entities, so a derived
        # projection row like the ideas index would otherwise never appear in
        # `actual` and the two sets would differ forever -- a writer
        # transaction and a full scan on every read past the throttle.
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

    def _corrupt_backup_names(self) -> tuple[str, ...]:
        """Names of databases an earlier recovery moved aside, oldest first."""
        if not self.db_path.parent.exists():
            return ()
        return tuple(
            sorted(path.name for path in self.db_path.parent.glob("store.db.corrupt-*"))
        )

    def _degraded_status(
        self, *, warning: str | None, identity: tuple[str, int] | None = None
    ) -> StoreStatus:
        """What the report can still say when the database cannot be read.

        Everything here comes from the filesystem, so it answers on a network
        share, before a store exists, and over an unreadable file alike -- the
        three situations an operator is most likely to be running the tool in.
        `identity` is the projection-derived `(token, seq)` a projection-only
        store reads under, passed in so the report and the ETag served beside it
        never disagree about which revision the caller is looking at.
        """
        token, seq = identity or ("", 0)
        return StoreStatus(
            root=self.root,
            db_path=self.db_path,
            creation_token=token,
            max_seq=seq,
            dirty_files=(),
            quarantined_files=(),
            resolution_source=self.resolution.source,
            schema_version=0,
            db_size=self.db_path.stat().st_size if self.db_path.exists() else 0,
            wal_size=0,
            warning=warning,
            corrupt_files=self._corrupt_backup_names(),
        )

    def _status_from(
        self, connection: sqlite3.Connection, *, warning: str | None
    ) -> StoreStatus:
        """Read the whole report off one connection under a single snapshot.

        Taken as one `BEGIN` so the change log, the session list and the Linear
        queue depth all describe the same instant; a report stitched from
        separate reads can show a change whose queue row it does not.
        """
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
            queued = int(
                connection.execute(
                    "SELECT COUNT(*) FROM linear_queue "
                    "WHERE state IN ('pending','claimed')"
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
            creation_token=token_row[0] if token_row else "",
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
            warning=warning,
            corrupt_files=self._corrupt_backup_names(),
            linear_pending=queued,
        )

    def status(self) -> StoreStatus:
        self._ensure_open()
        warning = (
            _network_filesystem_reason(self.root) or self.resolution.filesystem_warning
        )
        if self._network_projection_only:
            return self._degraded_status(
                warning=warning, identity=self._projection_identity()
            )
        return self._status_from(self.connection, warning=warning)

    def read_only_status(self) -> StoreStatus:
        """`status()` without any of the writing that opening a store does.

        `status()` goes through `_ensure_open`, which creates the database when
        it is missing, preps the schema and recovers export intents under
        `BEGIN IMMEDIATE`, and moves a bad file aside as `store.db.corrupt-*`.
        A diagnostic must do none of that: an operator runs it *because* they
        suspect the store is broken, and a tool that repairs the evidence before
        they can look at it is worse than one that says nothing.  So this
        reports a missing or unreadable database instead of acting on it, and
        reads through its own short-lived read-only connection.
        """
        network_reason = _network_filesystem_reason(self.root)
        base_warning = network_reason or self.resolution.filesystem_warning
        if not (self.db_path.exists() and self.db_path.stat().st_size):
            return self._degraded_status(
                warning=base_warning
                or "no store yet: store.db is created on the first read or write"
            )
        try:
            header = _read_sqlite_header(self.db_path)
        except OSError as exc:
            return self._degraded_status(warning=f"store.db is unreadable: {exc}")
        if header != SQLITE_HEADER:
            return self._degraded_status(
                warning="store.db is not a SQLite database; it has been left "
                "exactly as it is -- inspect it before running a tool that writes"
            )
        try:
            connection = sqlite3.connect(
                f"file:{self.db_path.as_posix()}?mode=ro",
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
                uri=True,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            return self._degraded_status(warning=f"store.db cannot be opened: {exc}")
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            return self._status_from(connection, warning=base_warning)
        except sqlite3.DatabaseError as exc:
            return self._degraded_status(
                warning=f"store.db could not be read: {exc}; it has been left "
                "exactly as it is -- inspect it before running a tool that writes"
            )
        finally:
            connection.close()

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
                tx.connection.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    (_DERIVED_REBUILT_KEY, _now()),
                )
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

    def derived_status(self) -> dict[str, Any]:
        """Row counts for the derived tables plus when they were last rebuilt whole.

        `rebuilt_at` is None until a `rebuild_derived()` runs: ordinary tool calls
        refresh only the keys they touched, so there is no whole-table build to
        date. It is a health report, never a freshness precondition.
        """
        connection = self.connection
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in DERIVED_TABLES
        }
        counts["entities"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM entities WHERE deleted=0"
            ).fetchone()[0]
        )
        row = connection.execute(
            "SELECT value FROM meta WHERE key=?", (_DERIVED_REBUILT_KEY,)
        ).fetchone()
        return {"row_counts": counts, "rebuilt_at": row[0] if row else None}

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
                if key not in tx._committed_keys:
                    tx.create(key[0], doc, body=body, requested_id=key[1])
                    continue
                # A tool that called `tx.create` itself and then mirrored the
                # new entity into the dict is not asking for a second create;
                # the row already exists, so this is an update like any other.
                old_doc, old_body = None, None
            else:
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
        # Absence never deletes data: an entity a hand edit left without an id
        # cannot be a row, so name it rather than drop it on the floor. This runs
        # *before* the legacy `epic` backfill below — naming afterwards left a
        # task under an id-less epic carrying `epic: None`, belonging to nothing.
        for note in name_missing_ids(data, known_ids=lambda kind: self._known_ids(tx, kind)):
            tx.warnings.append(note)
            tx.log_entries.append(note)
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
                                if current is None:
                                    # An `epics/<id>.md` that backlog.yaml never
                                    # mentions has no slim half to merge with,
                                    # so this used to keep the heavy fields and
                                    # throw the file's own identity away —
                                    # permanently. The export then wrote `- {}`
                                    # into backlog.yaml and a title-less file,
                                    # the epic's name survived nowhere on disk,
                                    # and the next cold open refused the
                                    # projection outright. For an orphan the
                                    # file is the only copy, so it supplies
                                    # everything the row lacks.
                                    #
                                    # Only for an orphan. `_split_entity_for_v3`
                                    # mirrors a readability `title` into every
                                    # heavy file and `_merge_entity_from_v3`
                                    # ignores it coming back; absorbing it into
                                    # the row put a second display field into
                                    # `backlog.yaml` on the first cold reopen of
                                    # *every* project, and it never moved again,
                                    # so a later rename left a stale name beside
                                    # the real one.
                                    for field, value in parsed_doc.items():
                                        if field == "title":
                                            continue
                                        merged.setdefault(field, value)
                                    if not merged.get("name"):
                                        # …and the mirror is where an orphan's
                                        # display name survives, so it is
                                        # recovered as `name` — the field every
                                        # reader uses — rather than as the
                                        # duplicate that drifts.
                                        recovered = (
                                            parsed_doc.get("name")
                                            or parsed_doc.get("title")
                                        )
                                        if recovered:
                                            merged["name"] = recovered
                                merged.setdefault("id", ident)
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

    def _known_ids(self, tx: "Transaction", kind: str) -> set[str]:
        """Every id of `kind` the store knows: rows, reserved ids, files on disk.

        The same three sources `Transaction.allocate_id` consults. An id
        synthesized without them can land on a leftover `tasks/<epic>-NNN.md`,
        and the per-file import that follows upserts, so the stray file's fields
        would merge into the entity that was just named.
        """
        known = {
            row[0]
            for row in tx.connection.execute(
                "SELECT id FROM entities WHERE kind=?", (kind,)
            )
        }
        known |= set(self._load_reserved_ids().get(kind, set()))
        known |= {
            ident for file_kind, ident, _path in self._known_entity_files()
            if file_kind == kind
        }
        return known

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
            parts = [str(doc.get(field) or "") for field in _FTS_PROSE_FIELDS]
            docs = doc.get("docs")
            if isinstance(docs, dict):
                parts.extend(str(value or "") for value in docs.values())
            anchors = [
                normalize_task_anchor(str(anchor), doc.get("sub_repo"))
                for anchor in as_list(doc.get("anchors"))
            ]
            parts.extend(path for path, _ in anchors)
            if row["body"]:
                parts.append(str(row["body"]))
            prose = "\n".join(part for part in parts if part)
            tx.connection.execute(
                "INSERT INTO entity_fts(kind,id,title,body) VALUES(?,?,?,?)",
                (kind, ident, title, prose),
            )
            for value, match_kind in anchors:
                tx.connection.execute(
                    "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)",
                    (kind, ident, value, match_kind, "anchors"),
                )
            located: set[str] = set()
            for location in as_list(doc.get("location")):
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
            if kind == "handover":
                for task_id in as_list(doc.get("task_ids")):
                    tx.connection.execute(
                        "INSERT INTO handover_tasks(handover_id,task_id) VALUES(?,?)",
                        (ident, str(task_id)),
                    )
        if tx._derived_keys:
            self._close_reverse_links(tx.connection)
            self._rebuild_related(tx.connection)

    @staticmethod
    def _close_reverse_links(connection: sqlite3.Connection) -> None:
        """Re-derive every mirror edge from the declared ones.

        A mirror lives under the *target* entity, so it is owned by no document
        and cannot be maintained while walking the touched keys: writing it when
        the source is refreshed and deleting it when the target is refreshed made
        the mirror's survival depend on the iteration order of a set. Wiping
        `derived=1` and re-deriving the whole table is order-free, and it drops
        mirrors whose forward edge has since been removed. A pair both sides
        declare is stored once, as `derived=0`.
        """
        connection.execute("DELETE FROM links WHERE derived=1")
        for link_type, reverse in REVERSE_TYPE.items():
            connection.execute(
                "INSERT OR IGNORE INTO links(src_kind,src_id,type,dst_kind,dst_id,derived) "
                "SELECT l.dst_kind, l.dst_id, ?, l.src_kind, l.src_id, 1 FROM links l "
                "WHERE l.type=? AND l.derived=0 AND NOT EXISTS ("
                "  SELECT 1 FROM links m WHERE m.derived=0 AND m.type=?"
                "  AND m.src_kind=l.dst_kind AND m.src_id=l.dst_id"
                "  AND m.dst_kind=l.src_kind AND m.dst_id=l.src_id)",
                (reverse, link_type, reverse),
            )

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
        content = _match_line_endings(content, self.backlog_path / _IDEAS_INDEX_REL)
        self._verify_text_round_trip(_IDEAS_INDEX_REL, content)
        self._replace_projection(
            tx, _IDEAS_INDEX_REL, _IDEAS_INDEX_KIND, None, content, seq,
            line_endings_matched=True,
        )

    def _regenerate_progress_if_due(self, tx: "Transaction") -> None:
        if _PROGRESS_RENDERER is None:
            return
        pending = tx.pending_progress_log()
        if tx.seq is None and not pending:
            return
        now = time.monotonic()
        if (
            not tx._force_progress
            and not pending
            and self._last_progress_clock is not None
            and now - self._last_progress_clock < 5.0
        ):
            return
        # The cap trims the *applied* tail only. Slicing the combined list
        # handed a truncated set to `apply_progress_log`, which clears every
        # pending row, so past the cap the oldest queued paragraphs were
        # discarded without ever reaching the file — and they exist nowhere
        # else. Room is what the cap leaves after the unwritten ones.
        applied = tx.applied_progress_log()
        room = max(_PROGRESS_LOG_CAP - len(pending), 0)
        entries = (applied[-room:] if room else []) + pending
        target = self.db_path.parent / "PROGRESS.md"
        temp = target.with_name(f"{target.name}.tmp.{self.session}")
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            rendered = _PROGRESS_RENDERER(
                self._load_dict_from_connection(tx.connection), existing, entries
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            if tx.seq is not None:
                tx.connection.execute(
                    "INSERT INTO meta(key,value) VALUES('last_progress_seq',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(tx.seq),),
                )
            # Applied only once the bytes are on disk, and inside this same
            # transaction. A rollback after this point un-applies the move as
            # well, and the next export regenerates the same region from the
            # same entries rather than appending them a second time.
            if pending:
                tx.apply_progress_log(entries)
            self._last_progress_clock = now
        except Exception as exc:
            temp.unlink(missing_ok=True)
            self._log(f"progress export failed: {exc!r}")
            if pending:
                # The paragraphs stay in `meta`, so the next transaction
                # retries them; the caller is told, rather than left believing
                # the session summary reached the file.
                tx.warnings.append(
                    f"export pending: {_PROGRESS_REL} — retried on next call"
                )

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
        # `(expected_doc, expected_body)` for the round-trip check, or None for a
        # whole-document YAML file, which has its own comparison.
        expected: tuple[Mapping[str, Any], str | None] | None
        if kind == "task":
            fm, rendered_body = task_v4_to_file(doc | ({BODY_KEY: body} if body else {}))
            content = render_frontmatter(fm, rendered_body).encode("utf-8")
            expected = (doc, body)
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
            expected = (heavy, rendered_body)
        elif kind == "project":
            content = yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True).encode("utf-8")
            expected = None
        else:
            content = render_frontmatter(doc, body).encode("utf-8")
            expected = (doc, body)
        # Matched here, once, so the verification below reads exactly the bytes
        # that land and `_replace_projection` does not probe the file a second
        # time — two extra opens per file across a 2,300-file adoption.
        content = _match_line_endings(content, self.backlog_path / rel)
        if expected is None:
            self._verify_yaml_round_trip(kind, ident, rel, content, doc)
        else:
            self._verify_round_trip(kind, ident, rel, content, *expected)
        self._replace_projection(
            tx, rel, kind, ident, content, row["updated_seq"],
            line_endings_matched=True,
        )

    def _verify_backlog_round_trip(
        self, content: bytes, expected: Mapping[str, Any]
    ) -> None:
        """`backlog.yaml` has to parse back to the index it was rendered from.

        It is the file the epic-identity bug actually corrupted — `- {}` was
        written here — and its failure is what made the next cold open raise
        before any tool ran. It is written through `_replace_projection`
        directly rather than through `_export_entity_row`, so the per-entity
        check never saw it; and on a project that already reads as v4 the
        bootstrap writes *only* this file, so without this the check verified
        nothing at all on that path.

        The comparison is the index the rest of the store keys on: every epic
        and phase entry, and `meta`. The task lists are not here (they live in
        `tasks/<id>.md` on v4), and `context` is derived.
        """
        if not self._verify_exports:
            return
        try:
            reloaded = yaml_io.safe_load(content.decode("utf-8")) or {}
            _validate_backlog_document(reloaded)
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise AdoptionRoundTripError(
                f"adoption refused: backlog.yaml cannot be read back ({exc}). "
                f"Nothing was changed."
            ) from exc
        for field in ("epics", "phases"):
            wanted = [_clean_doc(dict(entry)) for entry in expected.get(field) or []]
            actual = [_clean_doc(dict(entry)) for entry in reloaded.get(field) or []]
            if actual != wanted:
                missing = sorted(
                    str(entry.get("id") or "(no id)")
                    for entry in wanted
                    if entry not in actual
                )
                raise AdoptionRoundTripError(
                    f"adoption refused: backlog.yaml does not survive a round "
                    f"trip — {field} {missing or 'read back differently'}. "
                    f"Nothing was changed."
                )
        if reloaded.get("meta") != dict(expected.get("meta") or {}):
            raise AdoptionRoundTripError(
                "adoption refused: backlog.yaml does not survive a round trip — "
                "`meta` reads back differently. Nothing was changed."
            )

    def _verify_text_round_trip(self, rel: str, content: bytes) -> None:
        """A derived text file has at least to be the text it was rendered as.

        `ideas/IDEAS.md` is an index nothing re-parses (R2), so there is no
        document to compare it against — but it is written on the same adoption
        path, and a render that is not decodable text is still a file the user
        is handed.
        """
        if not self._verify_exports:
            return
        try:
            decoded = content.decode("utf-8")
        except UnicodeError as exc:
            raise AdoptionRoundTripError(
                f"adoption refused: {rel} is not valid UTF-8 ({exc}). "
                f"Nothing was changed."
            ) from exc
        if "\x00" in decoded:
            raise AdoptionRoundTripError(
                f"adoption refused: {rel} contains a NUL byte, so it is not the "
                f"text it was rendered as. Nothing was changed."
            )

    def _verify_yaml_round_trip(
        self,
        kind: str,
        ident: str | None,
        rel: str,
        content: bytes,
        expected: Mapping[str, Any],
    ) -> None:
        """`_verify_round_trip` for the whole-document YAML files.

        `project.yaml` carries the conventions and policies every gate reads. It
        has no frontmatter, so it needs the plain loader rather than
        `_parse_entity_text`, but it is on the same adoption path and answers
        the same question: can this be read back?
        """
        if not self._verify_exports:
            return
        try:
            reloaded = yaml_io.safe_load(content.decode("utf-8")) or {}
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise AdoptionRoundTripError(
                f"adoption refused: {rel} cannot be read back ({exc}). "
                f"Nothing was changed."
            ) from exc
        if reloaded != dict(expected):
            raise AdoptionRoundTripError(
                f"adoption refused: the {kind} {ident or ''!r} does not survive a "
                f"round trip through {rel}. Nothing was changed."
            )

    def _verify_round_trip(
        self,
        kind: str,
        ident: str,
        rel: str,
        content: bytes,
        expected_doc: Mapping[str, Any],
        expected_body: str | None,
    ) -> None:
        """Prove the rendered bytes parse back to what they were rendered from.

        Only during adoption, and always before `os.replace`: the 458-second
        migration commits a rewrite of every file in the project, and there was
        nothing in the path that checked the result was still readable. When it
        was not — an epic whose identity the import had dropped — the next cold
        open of the migrated tree raised before any tool ran, and the store that
        could still answer had been the only copy.

        The comparison is against what this render was given, parsed by the same
        `_parse_entity_text` the scan uses, so anything the renderer and the
        parser disagree about (a title YAML quotes one way and reads back
        another, a body whose fences swallow the frontmatter) is caught here.
        """
        if not self._verify_exports:
            return
        # `content` has already been through `_match_line_endings`, so what is
        # parsed here is byte-for-byte what lands on disk.
        try:
            actual_doc, actual_body = self._parse_entity_text(
                kind, content.decode("utf-8")
            )
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise AdoptionRoundTripError(
                f"adoption refused: the {kind} {ident!r} rendered to {rel}, which "
                f"cannot be read back ({exc}). Nothing was changed."
            ) from exc
        wanted_doc = _clean_doc(dict(expected_doc))
        wanted_body = (expected_body or "").removesuffix("\n") or None
        if actual_doc != wanted_doc or actual_body != wanted_body:
            differing = sorted(
                key for key in set(actual_doc) | set(wanted_doc)
                if actual_doc.get(key) != wanted_doc.get(key)
            )
            detail = (
                f"fields {differing}" if differing else "the body"
            )
            raise AdoptionRoundTripError(
                f"adoption refused: the {kind} {ident!r} does not survive a "
                f"round trip through {rel} ({detail} read back differently). "
                f"Nothing was changed."
            )

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
        content = _match_line_endings(content, self.backlog_path / "backlog.yaml")
        self._verify_backlog_round_trip(content, data)
        self._replace_projection(
            tx, "backlog.yaml", "backlog", None, content, seq, line_endings_matched=True
        )

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
        line_endings_matched: bool = False,
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
        # Everything is rendered with LF. Writing that over a CRLF working tree
        # (`core.autocrlf=true`, the Windows default) rewrites every line of
        # every file it touches: on a 2.2k-task backlog the adoption showed up
        # as a 2,300-file diff, and every later `git diff` on the backlog was
        # unreadable. Match what the file already uses; a new file gets LF.
        #
        # A caller that had to know the final bytes — anything that verified the
        # round trip first — has already matched them and says so, so the probe
        # runs once per exported file rather than twice.
        if not line_endings_matched:
            content = _match_line_endings(content, self.backlog_path / rel)
        # The hash is taken on the bytes actually written, or the next scan
        # reads the file as edited out of band and re-imports it forever.
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
        self._post_commit_removals: list[Path] = []
        self._reserved_keys: set[tuple[str, str]] = set()
        self._force_progress = False
        intent_token = uuid.uuid4().hex
        self._intent_path = self.store.db_path.parent / f"export-intent.{intent_token}.json"
        self._intent_entries: dict[str, dict[str, str | None]] = {}

    def request_progress_export(self) -> None:
        """Force this commit to regenerate PROGRESS.md, ignoring the throttle.

        The 5 s throttle exists so a burst of edits does not rewrite the
        dashboard repeatedly. Content that only this transaction carries — a
        session changelog entry — has nowhere else to land, so it opts out.
        """
        self._force_progress = True

    def _progress_entries(self, key: str) -> list[dict[str, Any]]:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        entries = _from_json(row[0], []) if row else []
        if not isinstance(entries, list):
            return []
        return [
            entry if isinstance(entry, dict) else {"ts": "", "text": str(entry)}
            for entry in entries
        ]

    def pending_progress_log(self) -> list[dict[str, Any]]:
        """Changelog paragraphs committed but not yet written into PROGRESS.md."""
        return self._progress_entries(_PROGRESS_LOG_KEY)

    def applied_progress_log(self) -> list[dict[str, Any]]:
        """Changelog paragraphs the session log in PROGRESS.md is rendered from."""
        return self._progress_entries(_PROGRESS_APPLIED_KEY)

    def queue_progress_log(self, entry: str) -> None:
        """Persist one changelog paragraph until PROGRESS.md carries it.

        The paragraph is a row, not a value held in memory by the calling tool:
        the dashboard export is best-effort, and text that exists nowhere else
        would be destroyed by a failed write while the tool reported success.
        Stored here it commits with the transition that produced it, and the
        next transaction retries the file.
        """
        pending = self.pending_progress_log()
        pending.append({"ts": _now(), "text": entry})
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_PROGRESS_LOG_KEY, _json(pending)),
        )
        self._force_progress = True

    def apply_progress_log(self, entries: list[dict[str, Any]]) -> None:
        """Move the pending paragraphs into the applied log, inside this transaction.

        The applied log is what the file's session-log region renders from, so
        a rollback that undoes this move also un-applies the entries and the
        next export regenerates the identical region rather than a doubled one.
        """
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_PROGRESS_APPLIED_KEY, _json(entries)),
        )
        self.clear_progress_log()

    def clear_progress_log(self) -> None:
        self.connection.execute("DELETE FROM meta WHERE key=?", (_PROGRESS_LOG_KEY,))

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
        A de-duped call still folds in what it knows -- a `tracker_id` the first
        call lacked, and payload keys the row is missing -- but never overwrites
        a key the row already carries, so the row keeps the moment the target
        first went dirty rather than sliding forward on every re-enqueue.
        """
        existing = self.connection.execute(
            "SELECT seq,tracker_id,payload FROM linear_queue "
            "WHERE op=? AND target_id=? AND state='pending'",
            (op, target_id),
        ).fetchone()
        if existing:
            seq = int(existing["seq"])
            merged = _from_json(existing["payload"], None)
            if payload:
                merged = {**dict(payload), **(merged or {})}
            self.connection.execute(
                "UPDATE linear_queue SET tracker_id=?,payload=? WHERE seq=?",
                (
                    existing["tracker_id"] if tracker_id is None else tracker_id,
                    None if merged is None else _json(merged),
                    seq,
                ),
            )
            return seq
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
        if kind == "idea" and not self.connection.execute(
            "SELECT 1 FROM projection WHERE file=?", (_IDEAS_INDEX_REL,)
        ).fetchone():
            # `ideas/IDEAS.md` is derived from the idea rows (R2), and nothing
            # else creates it: on a first v4 import the ideas arrive through
            # this path and the index would otherwise never exist at all.
            #
            # Only that case. Regenerating on *every* idea import looks right
            # and is not: each process that imports an idea renders the whole
            # shared index and rewrites it inside its own writer transaction,
            # so eight concurrent writers turn one idea into eight file
            # replaces under the lock and starve it. Measured: the 8x200
            # cross-process stress run went from passing to a 30 s writer
            # timeout. A hand edit to an existing idea therefore still leaves
            # the index stale until the next idea write; that gap is filed
            # rather than paid for with a lock-starving write amplification.
            self._export_ideas = True

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
        for path in self._post_commit_removals:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                # The committed receipt is what makes this recoverable: the
                # next scan sees the same bytes, skips the import and retries
                # the removal.  Never fail a committed transaction over it.
                self.log_entries.append(
                    f"post-commit removal pending {path.name}: {exc!r}"
                )
        self._post_commit_removals.clear()
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


_ID_SAFE_RE = re.compile(r"[^a-z0-9]+")


def _kebab(text: str) -> str:
    """A safe, deterministic identifier fragment from free text, or ""."""
    return _ID_SAFE_RE.sub("-", str(text or "").strip().lower()).strip("-")


def _unique_id(candidate: str, taken: set[str], fallback: str) -> str:
    """`candidate`, or the first free `<candidate>-<n>`; `fallback` when empty."""
    base = candidate or fallback
    if base not in taken:
        taken.add(base)
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    chosen = f"{base}-{suffix}"
    taken.add(chosen)
    return chosen


def name_missing_ids(
    data: dict[str, Any],
    *,
    known_ids: "Callable[[str], set[str]] | None" = None,
) -> list[str]:
    """Give every id-less epic, phase and task an id, in place.

    Absence never deletes data (design spec decision 4), but the store keys rows
    by id: an epic, phase or task that a hand edit left without one was dropped
    by `_flatten_backlog_dict` on adoption, taking its description, body and
    every task under it with it, silently.

    `known_ids(kind)` returns every id of that kind the store already knows —
    rows, reserved ids and entity files on disk — the same three sources
    `Transaction.allocate_id` consults. Without it a synthesized task id could
    land on a leftover `tasks/<epic>-NNN.md`, and the per-file import that
    follows upserts, so the stray file's fields would merge into the task that
    was just named.

    Epics and phases are named from their `name` (kebab-cased, suffixed on
    collision) so the id a user sees afterwards is recognisable and stable
    across re-adoption. Tasks follow their epic's `<epic>-<NNN>` convention at
    one past the highest number anything knows about, which is exactly what
    `allocate_id` would hand out. Returns one line per id assigned, for the
    caller to log.
    """
    lookup = known_ids or (lambda _kind: set())
    notes: list[str] = []
    for field, kind, fallback in (
        ("epics", "epic", "epic"),
        ("phases", "phase", "phase"),
    ):
        entries = data.get(field) or []
        missing = [
            entry for entry in entries
            if isinstance(entry, dict) and not entry.get("id")
        ]
        if not missing:
            continue
        taken = {
            str(entry.get("id")) for entry in entries if isinstance(entry, dict) and entry.get("id")
        }
        taken |= lookup(kind)
        for entry in missing:
            ident = _unique_id(_kebab(entry.get("name") or entry.get("title")), taken, fallback)
            entry["id"] = ident
            notes.append(f"named id-less {kind} {ident!r} from its name")

    # One task-id set for the whole backlog, not one per epic. A task moved
    # between epics keeps its old `<epic>-NNN` id, so an id-less task under
    # epic `a` and an existing `a-001` parked under epic `b` both live in the
    # same namespace: naming from epic `a`'s own list alone handed out `a-001`
    # a second time and `_flatten_backlog_dict` aborted the entire adoption
    # over the duplicate.
    task_ids = set(lookup("task"))
    for epic in data.get("epics") or []:
        if not isinstance(epic, dict):
            continue
        task_ids |= {
            str(t.get("id"))
            for t in epic.get("tasks") or []
            if isinstance(t, dict) and t.get("id")
        }
    for epic in data.get("epics") or []:
        if not isinstance(epic, dict):
            continue
        tasks = epic.get("tasks") or []
        missing = [task for task in tasks if isinstance(task, dict) and not task.get("id")]
        if not missing:
            continue
        epic_id = str(epic.get("id") or "epic")
        prefix = f"{epic_id}-"
        taken = task_ids
        highest = 0
        for tid in taken:
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", tid)
            if match:
                highest = max(highest, int(match.group(1)))
        for task in missing:
            highest += 1
            while f"{prefix}{highest:03d}" in taken:
                highest += 1
            ident = f"{prefix}{highest:03d}"
            taken.add(ident)
            task["id"] = ident
            notes.append(f"named id-less task {ident!r} in epic {epic_id!r}")
    return notes


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
