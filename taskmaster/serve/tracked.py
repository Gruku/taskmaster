# User intent: one rule for which project files a remote teammate may read: only git-tracked files inside the root, judged on the path a symlink really resolves to, never anything under .git/ or .taskmaster/local/ (the operator token lives there). The viewer's /file/ route and the server's doc reads share it.
"""The git-tracked allow-list for serving project files in server mode (team-server-006 D10.7; shared with 005)."""
from __future__ import annotations

import logging
import os
import stat
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath

from taskmaster.bounded_run import run_bounded

from .config import TRACKED_FILES_TTL

LOG = logging.getLogger("taskmaster.serve")
_FORBIDDEN_PREFIXES = ((".git",), (".taskmaster", "local"))


class TrackedFiles:
    """The git-tracked paths under root, cached for ttl; empty (every file refused) when git cannot say."""

    def __init__(self, root: Path, *, ttl: float = TRACKED_FILES_TTL, clock=time.monotonic, runner=run_bounded):
        self.root = Path(root)
        self.ttl = ttl
        self._clock = clock
        self._runner = runner
        self._lock = threading.Lock()
        self._loaded_at: float | None = None
        self._files: frozenset[str] = frozenset()
        self._failing = False

    def get(self) -> frozenset[str]:
        with self._lock:
            now = self._clock()
            if self._loaded_at is None or now - self._loaded_at >= self.ttl:
                self._files = self._load()
                self._loaded_at = now
            return self._files

    def _load(self) -> frozenset[str]:
        try:
            result = self._runner(["git", "-C", str(self.root), "ls-files", "-z", "--cached"], timeout=10)
            if result.returncode != 0:
                raise RuntimeError(f"git exited {result.returncode}")
            out = result.stdout.decode("utf-8", "surrogateescape") if isinstance(result.stdout, bytes) else result.stdout
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            if not self._failing:
                LOG.warning("tracked-file reads disabled (viewer /file/ disabled): cannot list git-tracked files "
                            "under the root (%s)", type(exc).__name__)
            self._failing = True
            return frozenset()
        self._failing = False
        return frozenset(name for name in out.split("\0") if name)


def _forbidden(parts: tuple[str, ...]) -> bool:
    return any(parts[:len(prefix)] == prefix for prefix in _FORBIDDEN_PREFIXES)


def tracked_file_path(root: Path, rel: str, tracked: frozenset[str]) -> Path | None:
    """The resolved path of root/rel if a remote caller may read it, else None. An allow-list: rel must be a plain
    relative path, and both rel and the root-relative path it resolves to (through any symlink) must be git-tracked,
    inside root, not under .git/ or .taskmaster/local/, and a regular file (a submodule gitlink is tracked but is
    a directory). Judging only the unresolved name let a tracked symlink to the operator token or .env through."""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel or rel.startswith("/") or ":" in rel:
        return None
    segments = rel.split("/")
    if any(segment in ("..", ".", "") for segment in segments):
        return None
    if _forbidden(PurePosixPath(rel).parts) or rel not in tracked:
        return None
    try:
        base = Path(root).resolve()
        resolved = (base / rel).resolve()
        if not resolved.is_relative_to(base):
            return None
        real = resolved.relative_to(base).as_posix()
        if _forbidden(PurePosixPath(real).parts) or real not in tracked or not resolved.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return resolved


def file_path_allowed(root: Path, rel: str, tracked: frozenset[str]) -> bool:
    return tracked_file_path(root, rel, tracked) is not None


def read_checked(root: Path, checked: Path) -> bytes:
    """Read the file at `checked` (a path tracked_file_path returned) so that what is read is that file, even if a
    component was swapped for a symlink after the check. POSIX: walk from the root with O_NOFOLLOW on every
    component. Windows: open, then require the handle's final path to be `checked`. Raises PermissionError
    when the opened file is not the checked one."""
    base = Path(root).resolve()
    parts = Path(checked).relative_to(base).parts
    if os.name == "nt":
        fd = os.open(str(checked), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            if os.path.normcase(_final_path(fd)) != os.path.normcase(str(checked)):
                raise PermissionError("the file changed after it was checked")
            return _read_regular(fd)
        finally:
            os.close(fd)
    nofollow = os.O_NOFOLLOW
    dirfd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dirfd)
            except OSError as exc:
                raise PermissionError("the file changed after it was checked") from exc
            os.close(dirfd)
            dirfd = nextfd
        try:
            fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=dirfd)
        except OSError as exc:
            raise PermissionError("the file changed after it was checked") from exc
    finally:
        os.close(dirfd)
    try:
        return _read_regular(fd)
    finally:
        os.close(fd)


def _read_regular(fd: int) -> bytes:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise PermissionError("not a regular file")
    chunks = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def _final_path(fd: int) -> str:
    """GetFinalPathNameByHandleW for an open descriptor, without its long-path prefix (Windows only)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    get = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    get.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(32768)
    n = get(wintypes.HANDLE(msvcrt.get_osfhandle(fd)), buf, 32768, 0)
    if n == 0 or n >= 32768:
        raise PermissionError("cannot name the opened file")
    path = buf.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


_SHARED: dict[Path, TrackedFiles] = {}
_SHARED_LOCK = threading.Lock()


def tracked_files_for(root: Path) -> TrackedFiles:
    """One cached TrackedFiles per resolved root, so every caller shares the git listing and its TTL."""
    key = Path(root).resolve()
    with _SHARED_LOCK:
        files = _SHARED.get(key)
        if files is None:
            files = _SHARED[key] = TrackedFiles(key)
        return files


def tracked_file_allowed(root: Path, rel_path: str, *, tracked: TrackedFiles | None = None) -> bool:
    """The single shared rule (viewer /file/, 005 doc reads): may a remote caller read root/rel_path?
    Prefer read_tracked_file when the content is wanted: checking and then opening by name leaves a race."""
    files = tracked if tracked is not None else tracked_files_for(root)
    return file_path_allowed(root, rel_path, files.get())


def read_tracked_file(root: Path, rel_path: str, *, tracked: TrackedFiles | None = None) -> bytes | None:
    """Check and read in one step: the bytes of root/rel_path if allowed, else None (also when it changed)."""
    files = tracked if tracked is not None else tracked_files_for(root)
    checked = tracked_file_path(root, rel_path, files.get())
    if checked is None:
        return None
    try:
        return read_checked(root, checked)
    except OSError:
        return None
