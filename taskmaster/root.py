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


def _core_settings(config: Path) -> dict[str, str] | None:
    """`core.worktree` / `core.bare` from one git config file, `{}` when it is absent.

    None when the file cannot be read plainly (unreadable, includes, quoting or
    escapes), because then only git knows what those keys resolve to.
    """
    try:
        text = config.read_text(encoding="utf-8")
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
        if not line or line[0] in "#;" or section != "core":
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if key not in ("worktree", "bare"):
            continue
        value = value.split("#", 1)[0].split(";", 1)[0].strip()
        if '"' in value or "\\" in value:
            return None
        settings[key] = value if sep else "true"
    return settings


def _is_git_dir(path: Path) -> bool:
    """What git's own `is_git_directory` checks, minus validating HEAD's contents."""
    return (path / "HEAD").is_file() and (path / "objects").is_dir() and (path / "refs").is_dir()


def _discover_git(start: Path):
    """`(checkout root, common git dir)` read off the filesystem, as git discovery finds them.

    None outside a repository; `_ASK_GIT` wherever only git can answer reliably:
    discovery overridden from the environment, a `.git` file or `commondir` that
    cannot be read, a start inside a git dir or bare repository, a work tree moved
    by `core.worktree`, or a repository marked bare. Spawning git costs 50-150 ms
    on Windows, and every tool call, hook and CLI resolves its root this way.
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
            git_dir = dot_git
        elif dot_git.is_file():
            # A linked worktree or a submodule: `gitdir: <path>`, relative to here.
            try:
                content = dot_git.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                return _ASK_GIT
            if not content.startswith("gitdir:") or "\n" in content:
                return _ASK_GIT
            git_dir = _absolute(directory / content[len("gitdir:"):].strip())
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
        # Git applies the shared config's core.bare/core.worktree only when there is
        # no commondir; a linked worktree's own work tree is where its .git file is.
        configs = [git_dir / "config.worktree"]
        if common == git_dir:
            configs.insert(0, common / "config")
        for config in configs:
            core = _core_settings(config)
            if core is None:
                return _ASK_GIT
            if core.get("bare", "false").lower() not in ("false", "no", "off", "0", ""):
                return _ASK_GIT
            if "worktree" in core and _absolute(git_dir / core["worktree"]) != directory:
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
