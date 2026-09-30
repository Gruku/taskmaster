# User intent: one root-resolution rule for every entry point, including the
# git/edit hooks that run under the system interpreter without the uv venv.
# Standard library only — importing yaml here would make the hooks dead on
# every machine that lacks the venv, which is why they cannot import store.py.
"""Root resolution and storage-safety probes for Taskmaster.

`taskmaster.store` re-exports every name defined here, so `store.resolve_root`
stays the public entry point and there is exactly one implementation of the
rule (``TASKMASTER_ROOT`` -> git common dir -> nearest ancestor holding a
backlog -> cwd).  Hooks import this module directly because it costs nothing
but the standard library.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .bounded_run import run_bounded


DB_RELPATH = Path("local") / "store.db"


@dataclass(frozen=True)
class RootResolution:
    root: Path
    backlog_path: Path
    source: str
    filesystem_warning: str | None = None


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


# Discovery that the filesystem cannot answer the way git would: these change
# where git looks or what it treats as the work tree.
_GIT_DISCOVERY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)
_ASK_GIT = object()


def _git_rev_parse(start: Path, flag: str) -> str | None:
    try:
        proc = run_bounded(
            ["git", "-C", str(start), "rev-parse", flag],
            check=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip()


def _git_config(config: Path) -> dict[str, str] | None:
    """The keys discovery depends on from one git config file, `{}` when it is absent.

    Keys come back as `section.key` in lower case: `core.bare`, `core.worktree`,
    `extensions.worktreeconfig`. None when the file cannot be read plainly
    (unreadable, includes, quoting or escapes), because then only git knows what
    those keys resolve to.
    """
    try:
        # Git's config parser skips a UTF-8 byte order mark.
        text = config.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        return None
    settings: dict[str, str] = {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            close = line.find("]")
            if close < 0:
                return None
            section = line[1:close].strip().lower()
            if section.startswith("include"):
                return None
            line = line[close + 1:].strip()
        if not line or line[0] in "#;" or section not in ("core", "extensions"):
            continue
        key, sep, value = line.partition("=")
        name = f"{section}.{key.strip().lower()}"
        if name not in _DISCOVERY_KEYS:
            continue
        value = value.split("#", 1)[0].split(";", 1)[0].strip()
        if '"' in value or "\\" in value:
            return None
        settings[name] = value if sep else "true"
    return settings


_DISCOVERY_KEYS = ("core.bare", "core.worktree", "extensions.worktreeconfig")


def _truthy(value: str) -> bool:
    return value.lower() not in ("false", "no", "off", "0", "")


def _is_git_dir(path: Path) -> bool:
    """What git's own `is_git_directory` checks, minus validating HEAD's contents."""
    return (path / "HEAD").is_file() and (path / "objects").is_dir() and (path / "refs").is_dir()


# Ownership verdicts per path: git's safe.directory check only ever asks about a
# handful of directories per process.
_OWNED: dict[str, bool] = {}
_WINDOWS_SID = None   # (advapi32, kernel32, the token user's SID buffer), on first use


def _owned_by_current_user(path: Path) -> bool:
    """Whether git's "dubious ownership" check accepts `path` without safe.directory.

    False whenever that cannot be established, so the caller asks git, which then
    applies safe.directory itself and answers exactly as it always did.
    """
    key = str(path)
    verdict = _OWNED.get(key)
    if verdict is None:
        try:
            verdict = _windows_owned(path) if os.name == "nt" else _posix_owned(path)
        except (OSError, AttributeError, ValueError):
            verdict = False
        _OWNED[key] = verdict
    return verdict


def _posix_owned(path: Path) -> bool:
    return os.lstat(path).st_uid == os.geteuid()


def _windows_owned(path: Path) -> bool:
    global _WINDOWS_SID
    import ctypes
    from ctypes import wintypes

    if _WINDOWS_SID is None:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))   # TokenUser
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation")
        finally:
            kernel.CloseHandle(token)
        _WINDOWS_SID = (advapi, kernel, buffer)
    advapi, kernel, buffer = _WINDOWS_SID
    user_sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION
    if advapi.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner), None, None, None,
                                    ctypes.byref(descriptor)) != 0:
        return False
    try:
        return bool(owner.value) and bool(advapi.EqualSid(owner, user_sid))
    finally:
        kernel.LocalFree(descriptor)


def _discover_git(start: Path):
    """`(checkout root, common git dir)` read off the filesystem, as git discovery finds them.

    None outside a repository; `_ASK_GIT` wherever only git can answer reliably:
    discovery overridden from the environment, a `.git` file or `commondir` that
    cannot be read, a start inside a git dir or bare repository, a work tree moved
    by `core.worktree`, a repository marked bare, per-worktree config, or a
    repository not owned by the current user (git's safe.directory check).
    Spawning git costs 50-150 ms on Windows, and every tool call, hook and CLI
    resolves its root this way.
    """
    if any(os.environ.get(name) for name in _GIT_DISCOVERY_ENV):
        return _ASK_GIT
    for name, value in os.environ.items():
        if name.upper().startswith("GIT_CONFIG") and (
            "core.worktree" in value.lower() or "core.bare" in value.lower()
        ):
            return _ASK_GIT
    start = _absolute(start)
    if not start.is_dir():
        return None  # `git -C` cannot enter it either
    for directory in (start, *start.parents):
        dot_git = directory / ".git"
        if not os.path.lexists(dot_git):
            if _is_git_dir(directory):
                return _ASK_GIT
            continue
        if dot_git.is_dir():
            # Resolved, as git's own answer was: a `.git` that is a symlink or
            # junction must give the common dir its linked worktrees name.
            git_dir = _absolute(dot_git)
        elif dot_git.is_file():
            # A linked worktree or a submodule: exactly `gitdir: <path>`, as git
            # reads it -- only trailing line breaks are dropped.
            try:
                content = dot_git.read_text(encoding="utf-8").rstrip("\r\n")
            except (OSError, UnicodeError):
                return _ASK_GIT
            target = content[len("gitdir: "):]
            if (not content.startswith("gitdir: ") or not target or target != target.strip()
                    or "\n" in target or "\r" in target):
                return _ASK_GIT
            git_dir = _absolute(directory / target)
        else:
            return _ASK_GIT
        common = git_dir
        commondir = git_dir / "commondir"
        if commondir.exists():
            try:
                common = _absolute(git_dir / commondir.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeError):
                return _ASK_GIT
        if not (git_dir / "HEAD").is_file() or not _is_git_dir(common):
            return _ASK_GIT
        config = _git_config(common / "config")
        if config is None or _truthy(config.get("extensions.worktreeconfig", "false")):
            # With per-worktree config, git also applies the shared core.bare and
            # core.worktree to linked worktrees; only git resolves that.
            return _ASK_GIT
        # Without it, git applies them only when there is no commondir; a linked
        # worktree's own work tree is where its .git file is.
        if common == git_dir:
            if _truthy(config.get("core.bare", "false")):
                return _ASK_GIT
            if ("core.worktree" in config
                    and _absolute(git_dir / config["core.worktree"]) != directory):
                return _ASK_GIT
        owned = (directory, dot_git, git_dir, common)
        if not all(_owned_by_current_user(path) for path in owned):
            return _ASK_GIT
        return directory, common
    return None


def _git_common_root(start: Path) -> Path | None:
    found = _discover_git(start)
    if found is _ASK_GIT:
        common = _git_rev_parse(start, "--git-common-dir")
        if common is None:
            return None
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = start / common_path
        return _absolute(common_path).parent
    return None if found is None else found[1].parent


def _git_checkout_root(start: Path) -> Path | None:
    found = _discover_git(start)
    if found is _ASK_GIT:
        top = _git_rev_parse(start, "--show-toplevel")
        return None if top is None else _absolute(Path(top))
    return None if found is None else found[0]


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


def walk_up_for_backlog(start: Path) -> Path | None:
    """Nearest ancestor of `start` (itself included) that owns a backlog.

    A `.taskmaster/backlog.yaml` is the strong signal and wins outright; a bare
    `.taskmaster/` directory is accepted only when no ancestor has the file, so
    a half-initialised directory cannot shadow the real project above it.
    """
    start = _absolute(start)
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".taskmaster" / "backlog.yaml").is_file():
            return candidate
    for candidate in candidates:
        if (candidate / ".taskmaster").is_dir():
            return candidate
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
            # Outside a repository there is no checkout boundary to lean on, so
            # the backlog itself marks the root. Without this a tool or hook run
            # from a subdirectory resolves to a root with no `.taskmaster/` and
            # reports an empty project.
            walked = walk_up_for_backlog(start_path)
            if walked is not None:
                root = walked
                source = "walk-up"
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
    """`<root>/.taskmaster/local/store.db` for a backlog dir or backlog.yaml."""
    return _backlog_dir(backlog_path) / DB_RELPATH
