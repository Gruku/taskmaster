# User intent: in a server-mode project, refuse every attempt to open or create a local store, except in the one
# `taskmaster serve` process that holds the host lease, so no teammate clone or stray host process can fork the
# team's backlog; with no marker this costs two lstat calls and changes nothing (team-server-009).
"""The split-brain fence: `refusal(path)` at every store choke point, and the host lease that exempts serve.

Design: docs/specs/2026-11-01-team-server-009-split-brain-fence-design.md §5-§7.

- The marker (and the latch) are looked for at the path a choke point holds *and* at its enclosing
  git repository root (`fence_roots`), so a session started in a subdirectory, or a store resolved
  under one, still meets the marker at the repo root.
- The only exemption is a held kernel lock, `.taskmaster/local/serve/host.lock`, recorded in this
  process's memory with its pid. No environment variable, argument or file content grants it; a
  spawned child doesn't inherit the descriptor (PEP 446) and a forked one fails the pid check.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from contextlib import suppress
from pathlib import Path

from taskmaster import server_marker as sm
from taskmaster.admission import UnsupportedStoreError
from taskmaster.coordinator.ownership import Ownership, OwnershipUnavailable, ownership_held, private_directory
from taskmaster.root import git_common_root_checked

SERVE_DIR = "serve"
HOST_LOCK = "host.lock"
HOST_ID_FILE = "host-id"
CLAIM_RETRY_SECONDS = 0.2
_HOST_ID = re.compile(r"[0-9a-f]{32}")


class ServerModeRefused(UnsupportedStoreError):
    """This process may not open or create a local store here: the project is in server mode."""

    def __init__(self, text: str, *, root=None, kind: str | None = None):
        super().__init__(text)
        self.root = root
        self.kind = kind


class HostLeaseHeld(RuntimeError):
    """Another process holds the serve host lease for this root."""


# -- fence roots -----------------------------------------------------------------------------------

_ROOTS: dict[str, tuple[Path, ...]] = {}
_ROOTS_LOCK = threading.Lock()


def _key(path) -> str:
    # String-only normalization: realpath/resolve costs 0.25-0.5 ms on Windows, and this runs on
    # every connect (review N1). The exemption key below does resolve, but only once a marker exists.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _walk_for_git(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if os.path.lexists(directory / ".git"):
            return directory
    return None


def fence_roots(path) -> tuple[Path, ...]:
    """`path`, plus its enclosing repository root when that differs. Cached per path as given.

    A None from a git call that failed is never cached (review N2): the nearest ancestor holding a
    `.git` stands in for this call, and the next call asks again.
    """
    key = _key(path)
    cached = _ROOTS.get(key)
    if cached is not None:
        return cached
    own = Path(key)
    start, definitive = own, True
    while not start.is_dir():
        if start.parent == start:
            break
        start, definitive = start.parent, False      # a path not created yet: decide again later
    repo, answered = git_common_root_checked(start)
    if not answered:
        repo, definitive = _walk_for_git(start), False
    roots = (own,) if repo is None or _key(repo) == key else (own, Path(_key(repo)))
    if definitive:
        with _ROOTS_LOCK:
            _ROOTS[key] = roots
    return roots


# -- the check -------------------------------------------------------------------------------------

_MARKER_TEXT = ("This project uses a Taskmaster server ({url}, from {marker}). Its backlog lives on the server, "
                "so this clone never opens or creates a local store. Use the server's MCP tools with your own "
                "API key (the operator mints it with `taskmaster serve add-user`). Nothing was changed.")
_UNREADABLE_TEXT = ("This project is marked for a Taskmaster server, but {error}; no local store is opened "
                    "until it is fixed or removed. Nothing was changed.")
_LATCH_TEXT = ("This clone has seen Taskmaster server mode ({url}), but {root} has no {name} (an older "
               "checkout?). The local store stays closed. Ask the user; only if the project has really left "
               "server mode, they can run `taskmaster serve marker clear-latch --root {root}`. "
               "Nothing was changed.")


def _present(roots) -> bool:
    for root in roots:
        if os.path.lexists(root / sm.MARKER_NAME) or os.path.lexists(root / ".git" / sm.LATCH_NAME):
            return True
    return False


def _describe(roots) -> tuple[str, str, Path]:
    """(text, kind, root) for roots where a marker or latch is present; records the latch."""
    repo = roots[-1]
    for root in roots:
        if not os.path.lexists(root / sm.MARKER_NAME):
            continue
        try:
            marker = sm.read_marker(root)
        except sm.ServerConfigError as exc:
            return _UNREADABLE_TEXT.format(error=str(exc)[0].lower() + str(exc)[1:]), "unreadable", root
        if marker is None:      # vanished between the probe and the read
            continue
        sm.record_latch(repo, marker)
        return _MARKER_TEXT.format(url=marker.url, marker=root / sm.MARKER_NAME), "marker", root
    for root in roots:
        if not os.path.lexists(root / ".git" / sm.LATCH_NAME):
            continue
        try:
            latched = sm.read_latch(root)
            url = latched.url if latched is not None else "unknown"
        except sm.ServerConfigError:
            url = "unknown"
        return _LATCH_TEXT.format(url=url, root=root, name=sm.MARKER_NAME), "latch", root
    # Both vanished between the probe and the read: still refuse once, fail closed.
    return _UNREADABLE_TEXT.format(error="the marker changed while it was read"), "unreadable", repo


def refusal(path) -> str | None:
    """Why this process may not open or create a local store at `path`, or None."""
    roots = fence_roots(path)
    if not _present(roots):
        return None
    if exempt(path):
        return None
    return _describe(roots)[0]


def check(path) -> None:
    """Raise ServerModeRefused when `refusal(path)` would answer."""
    roots = fence_roots(path)
    if not _present(roots) or exempt(path):
        return
    text, kind, root = _describe(roots)
    raise ServerModeRefused(text, root=root, kind=kind)


# -- the host lease --------------------------------------------------------------------------------

_HOST_LEASES: dict[str, "HostLease"] = {}
_LEASE_LOCK = threading.Lock()


def _lease_key(path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


class HostLease:
    """The held `host.lock` of one root. Exempts this process (this pid) while held."""

    def __init__(self, root: Path, ownership: Ownership):
        self.root = root
        self.key = _lease_key(root)
        self.ownership = ownership
        self.pid = os.getpid()

    def held(self) -> bool:
        return self.ownership.descriptor is not None and self.pid == os.getpid()

    def release(self) -> None:
        with _LEASE_LOCK:
            if _HOST_LEASES.get(self.key) is self:
                del _HOST_LEASES[self.key]
        self.ownership.close()

    def __enter__(self) -> "HostLease":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def exempt(path) -> bool:
    """True only in the process holding the host lease of `path` or of one of its fence roots."""
    if not _HOST_LEASES:
        return False
    for root in {Path(os.fspath(path)), *fence_roots(path)}:
        lease = _HOST_LEASES.get(_lease_key(root))
        if lease is not None and lease.held():
            return True
    return False


def claim_host(root, *, retry_seconds: float = CLAIM_RETRY_SECONDS) -> HostLease:
    """Take the host lease of `root` (a project with `.taskmaster/local/`), or raise HostLeaseHeld.

    Retries briefly, because a probe (`host_lease_held`) takes and releases the same lock.
    Only `taskmaster serve` code paths call this: the daemon, and 013/014's offline commands.
    """
    root = Path(root).resolve(strict=True)
    if not (root / ".taskmaster" / "local").is_dir():
        raise FileNotFoundError(f"{root} has no .taskmaster/local/; it is not a project with a local store")
    ownership = Ownership(root, SERVE_DIR, HOST_LOCK)
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            ownership.acquire()
            break
        except OwnershipUnavailable:
            if time.monotonic() >= deadline:
                raise HostLeaseHeld(f"another `taskmaster serve` process (daemon, export run or init) holds the "
                                    f"host lease for {root}; stop it first") from None
            time.sleep(0.02)
    lease = HostLease(root, ownership)
    with _LEASE_LOCK:
        _HOST_LEASES[lease.key] = lease
    return lease


host_lease = claim_host      # `with host_lease(root):` reads better at 013/014's call sites


def host_lease_held(root) -> bool:
    """Whether any process holds the host lease of `root` right now (a probe; see claim_host)."""
    return ownership_held(Path(root), SERVE_DIR, HOST_LOCK)


# -- host id ---------------------------------------------------------------------------------------

def host_id_path(root) -> Path:
    return Path(root) / ".taskmaster" / "local" / SERVE_DIR / HOST_ID_FILE


def read_host_id(root) -> str | None:
    """This host clone's id, or None when it has none (or it is malformed)."""
    try:
        value = host_id_path(root).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value if _HOST_ID.fullmatch(value) else None


def ensure_host_id(root, *, rotate: bool = False, value: str | None = None) -> str:
    """The host clone's id, created owner-only by exclusive create when missing (or replaced).

    `value` installs a given id (014's bundle carries the one `pack` minted): equal to the stored id returns
    it unchanged, missing or malformed writes it, a different stored id raises ValueError with nothing written.
    """
    if value is not None:
        if rotate:
            raise ValueError("rotate and value are exclusive")
        if not _HOST_ID.fullmatch(value):
            raise ValueError("host id must be 32 lowercase hex characters")
    directory = private_directory(Path(root), SERVE_DIR)
    path = directory / HOST_ID_FILE
    if not rotate:
        existing = read_host_id(root)
        if existing is not None:
            if value is not None and existing != value:
                raise ValueError("this host clone already has a different host id")
            return existing
    if value is None:
        value = sm.new_host_id()
    temp = directory / f"{HOST_ID_FILE}.{secrets.token_hex(6)}.tmp"
    try:
        with open(temp, "x", encoding="ascii", newline="\n") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if rotate or os.path.lexists(path):     # replacing a malformed one, or rotating
            os.replace(temp, path)
        else:
            try:
                os.link(temp, path)
            except FileExistsError:
                existing = read_host_id(root)
                if existing is not None:
                    return existing
                os.replace(temp, path)
    finally:
        with suppress(FileNotFoundError):
            temp.unlink()
    return value


# -- tests -----------------------------------------------------------------------------------------

def leaked_leases() -> list[HostLease]:
    with _LEASE_LOCK:
        return list(_HOST_LEASES.values())


def reset_for_tests() -> list[HostLease]:
    """Clear the root cache and release any lease still registered; returns the leaked ones."""
    leaked = leaked_leases()
    for lease in leaked:
        lease.release()
    with _ROOTS_LOCK:
        _ROOTS.clear()
    return leaked
