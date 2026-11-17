# User intent: where a teammate's machine keeps the server key and the read cache: the platform's per-user
# config and cache dirs, every taskmaster directory owner-only, never through a link (team-server-007 D9, D11).
"""Per-user config and cache directories for the remote client."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from taskmaster.coordinator.ownership import private_directory_at

APP = "taskmaster"


def _home(env) -> Path:
    home = env.get("USERPROFILE") if sys.platform == "win32" else env.get("HOME")
    return Path(home) if home else Path.home()


def cache_base(env=None) -> Path:
    """The user's cache dir for taskmaster (not created). `TASKMASTER_CACHE_DIR` wins."""
    env = os.environ if env is None else env
    if env.get("TASKMASTER_CACHE_DIR"):
        return Path(env["TASKMASTER_CACHE_DIR"])
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else _home(env) / "AppData" / "Local") / APP
    if sys.platform == "darwin":
        return _home(env) / "Library" / "Caches" / APP
    base = env.get("XDG_CACHE_HOME")
    return (Path(base) if base else _home(env) / ".cache") / APP


def config_base(env=None) -> Path:
    """The user's config dir for taskmaster (not created). `TASKMASTER_CONFIG_DIR` wins (tests, CI)."""
    env = os.environ if env is None else env
    if env.get("TASKMASTER_CONFIG_DIR"):
        return Path(env["TASKMASTER_CONFIG_DIR"])
    if sys.platform == "win32":
        base = env.get("APPDATA")
        return (Path(base) if base else _home(env) / "AppData" / "Roaming") / APP
    if sys.platform == "darwin":
        return _home(env) / "Library" / "Application Support" / APP
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else _home(env) / ".config") / APP


def private_chain(base: Path, *parts: str) -> Path:
    """`base/parts...`, creating `base` (plainly) and every part under it owner-only.

    Raises PermissionError when any part is a link or not owned by this user.
    """
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    path = base
    for part in parts:
        path = private_directory_at(path / part)
    return path
