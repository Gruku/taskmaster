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


DB_RELPATH = Path("local") / "store.db"


@dataclass(frozen=True)
class RootResolution:
    root: Path
    backlog_path: Path
    source: str
    filesystem_warning: str | None = None


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
