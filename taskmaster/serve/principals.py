# User intent: per-person API keys: minted once, stored only as SHA-256 in an owner-only file on the host, rotated or revoked by the operator, and read by the daemon without a restart.
"""The principals file: one record per issued key, hashes only, plus the daemon's forgiving reader."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from taskmaster.coordinator import identity_gate, ownership, protocol
from taskmaster.native import profiles
from taskmaster.native.queries import Repository
from taskmaster.team_identity import CREATED_FORMAT, DEGENERATE_NAMES, HANDLE_RE, NEWLY_RESERVED, RESERVED_HANDLES

from .config import ServeRefused, ServeUnavailable

PRINCIPALS_FILE = "principals.json"
LOCK_FILE = "principals.lock"
KEY_RE = re.compile(r"tm_u_([0-9a-f]{16})_([A-Za-z0-9_-]{43})")   # always fullmatch
KEY_ID_RE = re.compile(r"[0-9a-f]{16}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
ROLES = ("member", "operator")
FILE_VERSION = 1
LOCK_SECONDS = 10.0
REPLACE_ATTEMPTS, REPLACE_PAUSE = 20, 0.05
WARN_EVERY_SECONDS = 60.0
LOG = logging.getLogger("taskmaster.serve")
_FIELDS = {"key_id", "handle", "role", "name", "emails", "created", "sha256", "revoked", "revoked_reason"}


class PrincipalsRefused(ServeRefused):
    pass


class PrincipalsBusy(ServeUnavailable):
    pass


class RegistryUnavailable(Exception):
    """A transient read error; never cached, so the next request retries (design D6)."""


def _safe(value) -> str:
    from .app import log_safe      # app imports auth imports this module, so only at call time
    return log_safe(value)


@dataclass(frozen=True)
class Record:
    key_id: str
    handle: str
    role: str
    name: str
    emails: tuple
    created: str
    sha256: str | None
    revoked: str | None
    revoked_reason: str | None

    @property
    def active(self) -> bool:
        return self.revoked is None

    @property
    def digest(self) -> bytes | None:
        return bytes.fromhex(self.sha256) if self.active and self.sha256 else None

    def public(self) -> dict:
        return {"key_id": self.key_id, "handle": self.handle, "role": self.role, "name": self.name,
                "emails": list(self.emails), "created": self.created, "revoked": self.revoked,
                "revoked_reason": self.revoked_reason}

    def stored(self) -> dict:
        return {**self.public(), "sha256": self.sha256}


def principals_path(root: Path) -> Path:
    from .auth import serve_directory
    return serve_directory(root) / PRINCIPALS_FILE


def mint_key(taken) -> tuple[str, str]:
    key_id = secrets.token_hex(8)
    while key_id in taken:
        key_id = secrets.token_hex(8)
    key = f"tm_u_{key_id}_{secrets.token_urlsafe(32)}"
    assert KEY_RE.fullmatch(key)
    return key, key_id


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def check_person(handle, name, emails, role, *, existing=False) -> None:
    """`existing`: a record already on file; a handle reserved only by 027 stays loadable (the registry warns)."""
    if existing and handle in NEWLY_RESERVED:
        handle = "member"
    try:
        if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
            raise ValueError(f"{handle!r} is not a valid handle (lowercase letters, digits and hyphens)")
        if handle in RESERVED_HANDLES or handle in DEGENERATE_NAMES:
            raise ValueError(f"handle {handle!r} is reserved or a placeholder word")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        profiles.validate("profile.create", {"handle": handle, "name": name, "emails": emails,
                                             "created": "2000-01-01T00:00:00Z"})
    except ValueError as exc:
        raise PrincipalsRefused(str(exc)) from None


def _timestamp(value) -> bool:
    try:
        datetime.strptime(value, CREATED_FORMAT)
    except (TypeError, ValueError):
        return False
    return isinstance(value, str)


def _record(item, index: int) -> Record:
    def bad(reason):
        label = item.get("key_id") if isinstance(item, dict) and isinstance(item.get("key_id"), str) else f"#{index}"
        return PrincipalsRefused(f"principals file is malformed: record {label[:16]}: {reason}")
    if not isinstance(item, dict) or set(item) != _FIELDS:
        raise bad("fields are not exactly the expected set")
    key_id, handle, role = item["key_id"], item["handle"], item["role"]
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise bad("key_id is not 16 lowercase hex characters")
    try:
        emails = item["emails"]
        if not isinstance(emails, list):
            raise ValueError("emails must be a list")
        check_person(handle, item["name"], emails, role, existing=True)
    except PrincipalsRefused as exc:
        raise bad(str(exc)) from None
    if not _timestamp(item["created"]):
        raise bad("created is not a UTC timestamp")
    sha, revoked, reason = item["sha256"], item["revoked"], item["revoked_reason"]
    if revoked is None:
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise bad("an active record needs a 64-hex sha256")
        if reason is not None:
            raise bad("an active record has a revoked_reason")
    else:
        if not _timestamp(revoked):
            raise bad("revoked is not a UTC timestamp")
        if sha is not None:
            raise bad("a revoked record must not keep a hash")
        if reason not in ("revoked", "rotated"):
            raise bad("revoked_reason must be revoked or rotated")
    return Record(key_id, handle, role, item["name"], tuple(emails), item["created"], sha, revoked, reason)


def parse(raw: bytes) -> dict[str, Record]:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise PrincipalsRefused("principals file is malformed: not JSON") from None
    if not isinstance(doc, dict) or doc.get("version") != FILE_VERSION or not isinstance(doc.get("keys"), list) \
            or set(doc) != {"version", "keys"}:
        raise PrincipalsRefused("principals file is malformed: expected {\"version\": 1, \"keys\": [...]}")
    records, active = {}, {}
    for index, item in enumerate(doc["keys"]):
        record = _record(item, index)
        if record.key_id in records:
            raise PrincipalsRefused(f"principals file is malformed: duplicate key_id {record.key_id}")
        if record.active:
            if record.handle in active:
                raise PrincipalsRefused(f"principals file is malformed: two active keys for {record.handle}")
            active[record.handle] = record.key_id
        records[record.key_id] = record
    return records


def load(path: Path) -> dict[str, Record]:
    path = Path(path)
    try:
        ownership.verify_private(path)
    except FileNotFoundError:
        return {}
    except PermissionError as exc:
        raise PrincipalsRefused(f"{path} must be owner-only (chmod 600 on Linux): {exc}") from exc
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    return parse(raw)


def write(path: Path, records: dict[str, Record]) -> None:
    body = json.dumps({"version": FILE_VERSION, "keys": [r.stored() for r in records.values()]}, indent=1) + "\n"
    try:
        replace_private(Path(path), body.encode("utf-8"))
    except PermissionError:
        raise PrincipalsBusy("could not replace the principals file; the daemon is reading it, retry") from None


def replace_private(path: Path, data: bytes) -> None:
    """Publish `data` at `path` atomically: an owner-only (0600) fsynced temp file, then `os.replace`.

    Shared by principals.json and team-server-013's export.json. On Windows a reader holding the file makes
    the replace fail for a moment, so it is retried; PermissionError when it never succeeds."""
    path = Path(path)
    temp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt + 1 == REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_PAUSE)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


@contextmanager
def locked(root: Path):
    from .auth import serve_directory
    fd = os.open(serve_directory(root) / LOCK_FILE, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    held = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        deadline = time.monotonic() + LOCK_SECONDS
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13, 36):
                    raise
                if time.monotonic() >= deadline:
                    raise PrincipalsBusy("another add-user, rotate-user or revoke-user is running") from None
                time.sleep(0.05)
        yield
    finally:
        try:
            if held:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def write_key_file(path: Path, key: str) -> None:
    path = Path(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        raise PrincipalsRefused(f"{path} already exists") from None
    except OSError as exc:
        raise PrincipalsRefused(f"could not write the key file {path}: {exc.strerror or type(exc).__name__}") from None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((key + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            ownership._windows_private(path)
        ownership.verify_private(path)
    except Exception as exc:  # noqa: BLE001 - the file must not survive a half-private state
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise PrincipalsRefused(f"could not write the key file {path}: {exc.strerror if isinstance(exc, OSError) and exc.strerror else type(exc).__name__}") from None


def _now(now) -> str:
    return now or datetime.now(timezone.utc).strftime(CREATED_FORMAT)


def add_user(root, handle, *, name=None, emails=(), role="member", key_file=None, now=None):
    emails = list(emails)
    check_person(handle, name or handle, emails, role)
    path = principals_path(root)
    with locked(root):
        records = load(path)
        for record in records.values():
            if record.active and record.handle == handle:
                raise PrincipalsRefused(f"{handle} already has a key ({record.key_id}); use rotate-user to replace it")
        reinstated = any(r.handle == handle for r in records.values())
        key, key_id = mint_key(set(records))
        if key_file is not None:
            write_key_file(key_file, key)
        record = Record(key_id, handle, role, name or handle, tuple(emails), _now(now), key_digest(key), None, None)
        try:
            write(path, {**records, key_id: record})
        except BaseException:
            if key_file is not None:
                with contextlib.suppress(OSError):
                    os.unlink(key_file)
            raise
    return key, record, reinstated


def _active(records, handle) -> Record:
    for record in records.values():
        if record.active and record.handle == handle:
            return record
    raise PrincipalsRefused(f"{handle} has no active key")


def rotate_user(root, handle, *, key_file=None, now=None):
    path = principals_path(root)
    with locked(root):
        records = load(path)
        old = _active(records, handle)
        key, key_id = mint_key(set(records))
        if key_file is not None:
            write_key_file(key_file, key)
        stamp = _now(now)
        retired = Record(old.key_id, old.handle, old.role, old.name, old.emails, old.created, None, stamp, "rotated")
        new = Record(key_id, old.handle, old.role, old.name, old.emails, stamp, key_digest(key), None, None)
        records[old.key_id] = retired
        try:
            write(path, {**records, key_id: new})
        except BaseException:
            if key_file is not None:
                with contextlib.suppress(OSError):
                    os.unlink(key_file)
            raise
    return key, new, retired


def revoke_user(root, handle, *, now=None) -> Record:
    path = principals_path(root)
    with locked(root):
        records = load(path)
        old = _active(records, handle)
        revoked = Record(old.key_id, old.handle, old.role, old.name, old.emails, old.created, None, _now(now), "revoked")
        records[old.key_id] = revoked
        write(path, records)
    return revoked


def _init_journal(root: Path) -> Path:
    return principals_path(root).parent / "init" / "init.json"


def _read_journal(root: Path) -> dict:
    try:
        value = json.loads(_init_journal(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def pre_team(root) -> tuple:
    """`(owner handle | None, cutoff seq | None)` of the migrated history (027 D6). Events at or below the cutoff
    are pre-team whatever their actor; the owner, when one is set and still has an active key, wrote them."""
    journal = _read_journal(Path(root))
    cutoff = journal.get("pre_team_through_seq")
    cutoff = cutoff if type(cutoff) is int and cutoff >= 0 else None
    owner = journal.get("pre_team_owner")
    if cutoff is None or not isinstance(owner, str):
        return None, cutoff
    active = {r.handle for r in load(principals_path(Path(root))).values() if r.active}
    return (owner if owner in active else None), cutoff


def set_pre_team_owner(root, handle: str) -> None:
    """`serve set-user <handle> --owns-pre-team-history`: names the one person whose text the migration brought in.
    The owner is kept beside the cutoff in the init journal; a second holder is refused."""
    root = Path(root)
    with locked(root):
        journal = _read_journal(root)
        if type(journal.get("pre_team_through_seq")) is not int:
            raise PrincipalsRefused("this host has no recorded migration (serve init --from): no pre-team history to own")
        _active(load(principals_path(root)), handle)
        held = journal.get("pre_team_owner")
        if isinstance(held, str) and held != handle:
            raise PrincipalsRefused(f"{held} already owns the pre-team history; one person may")
        journal["pre_team_owner"] = handle
        replace_private(_init_journal(root), (json.dumps(journal, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def list_users(root) -> list[Record]:
    return list(load(principals_path(root)).values())


@dataclass(frozen=True)
class StoreCheck:
    profile: str             # "live" | "deleted" | "absent" | "unknown"
    name: str | None
    team_mode: bool | None


def inspect_store(root, handle) -> StoreCheck:
    try:
        with contextlib.closing(protocol.connect(Path(root), readonly=True)) as connection:
            row = connection.execute("SELECT deleted FROM entity_core WHERE kind='profile' AND public_id=?",
                                     (handle,)).fetchone()
            name = None
            if row is None:
                state = "absent"
            elif row[0]:
                state = "deleted"
            else:
                state = "live"
                with Repository(connection).snapshot() as snapshot:
                    name = snapshot.get("profile", handle)["fields"].get("name")
            team = bool(identity_gate.team_mode_enabled(Path(root), project_doc=identity_gate._project_doc(connection)))
            return StoreCheck(state, name, team)
    except Exception:  # noqa: BLE001 - best effort; the gate is the authority
        return StoreCheck("unknown", None, None)


class PrincipalRegistry:
    """The daemon's read-only view: reloaded when the file changes; empty (fail closed) when the file's
    content or permissions are bad; a transient I/O error refuses one call and is retried on the next."""

    def __init__(self, path: Path | None):
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()
        self._key = None
        self._records: dict[str, Record] = {}
        self._loaded = False
        self._warned: dict[str, float] = {}

    def _warn_transient(self, exc) -> None:
        name = type(exc).__name__
        now = time.monotonic()
        if now - self._warned.get(name, -WARN_EVERY_SECONDS) >= WARN_EVERY_SECONDS:
            self._warned[name] = now
            LOG.warning("principals file could not be read (%s); refusing this request, retrying on the next",
                        _safe(name))

    def refresh(self) -> None:
        if self.path is None:
            return
        with self._lock:
            try:
                info = os.stat(self.path)
                key = (info.st_mtime_ns, info.st_size, info.st_ino)
            except FileNotFoundError:
                key = None
            except OSError as exc:
                self._warn_transient(exc)
                raise RegistryUnavailable from None
            if self._loaded and key == self._key:
                return
            if key is None:
                records = {}
            else:
                try:
                    records = load(self.path)
                except PrincipalsRefused as exc:
                    records = {}
                    LOG.error("principals file unusable, refusing every member key: %s", exc)
                except OSError as exc:
                    self._warn_transient(exc)
                    raise RegistryUnavailable from None
            for record in records.values():
                if record.active and record.handle in RESERVED_HANDLES:
                    LOG.warning("principals: handle %s is reserved (framing treats it as a trusted voice); rename it",
                                _safe(record.handle))
            before = {k for k, r in self._records.items() if r.active}
            was_loaded = self._loaded
            self._key, self._records, self._loaded = key, records, True
            if was_loaded:
                after = {k for k, r in records.items() if r.active}
                LOG.info("principals reloaded: %d active keys; no longer active: %s", len(after),
                         ", ".join(sorted(before - after)) or "none")

    def lookup(self, key_id: str) -> Record | None:
        self.refresh()
        return self._records.get(key_id)

    def active_ids(self) -> frozenset:
        self.refresh()
        return frozenset(k for k, r in self._records.items() if r.active)
