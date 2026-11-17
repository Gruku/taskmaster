# User intent: build each harness "machine" (a directory tree plus an environment that cannot see the real user's home, keys, Git config or server settings), so host, ann and bob never share state except over HTTP (015 A2).
"""Machines and their environments."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DROPPED_EXACT = ("TASKMASTER_ROOT", "TASKMASTER_SERVER_URL", "ANTHROPIC_API_KEY")
FIXED_DATES = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


def dropped(name: str) -> bool:
    return (name in DROPPED_EXACT or name.startswith("GIT_")
            or (name.startswith("TASKMASTER_") and "KEY" in name))


def machine_env(base: Path, name: str, extra: dict | None = None, parent: dict | None = None) -> dict:
    """`parent` (default os.environ) minus the variables A2 drops, with HOME, USERPROFILE, APPDATA, LOCALAPPDATA,
    the Taskmaster config and cache dirs and a per-machine Git config all inside `<base>/machines/<name>`."""
    home = Path(base) / "machines" / name / "home"
    config, cache = home / ".config-tm", home / ".cache-tm"
    for directory in (home, config, cache, home / "AppData" / "Roaming", home / "AppData" / "Local", home / ".claude"):
        directory.mkdir(parents=True, exist_ok=True)
    gitconfig = home / "gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text(f"[user]\n\tname = {name}\n\temail = {name}@twohost.invalid\n"
                             "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    env = {k: v for k, v in (os.environ if parent is None else parent).items() if not dropped(k)}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"), "TASKMASTER_CONFIG_DIR": str(config),
        "TASKMASTER_CACHE_DIR": str(cache), "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
        "TASKMASTER_SERVICE_IDLE_SECONDS": "3", "PYTHONIOENCODING": "utf-8", **FIXED_DATES,
    })
    env.update(extra or {})
    return env


@dataclass
class Machine:
    name: str
    base: Path
    root: Path | None = None
    env: dict = field(default_factory=dict)

    @property
    def home(self) -> Path:
        return Path(self.env["HOME"])

    @classmethod
    def create(cls, base: Path, name: str, *, root: Path | None = None, extra: dict | None = None) -> "Machine":
        return cls(name, Path(base), root, machine_env(base, name, extra))
