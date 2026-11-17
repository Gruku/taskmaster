# User intent: start and stop the real serve daemon through the harness launcher, record every pid it owns with its creation time and command line, and sweep left-overs from a killed run without touching any other process (015 A4, R2).
"""The host machine: a serve daemon child, its log, and the left-over pid sweep. The pid sweep only helps within one run's
base (a killed run's pid file sits in a basetemp pytest later renumbers); the launcher's parent watchdog is the guard
against orphans across runs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import serve_helpers
from . import launch_host, procid
from .world import Machine

LAUNCHER = Path(__file__).resolve().parent / "launch_host.py"
PID_FILE = "twohost-pids.json"


def _read_pids(base: Path) -> list[dict]:
    try:
        return json.loads((Path(base) / PID_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_pids(base: Path, entries: list[dict]) -> None:
    (Path(base) / PID_FILE).write_text(json.dumps(entries, indent=1), encoding="utf-8")


def register(base: Path, pid: int, root: Path) -> None:
    """Record `pid` with the creation time and command line read now; an unreadable identity is not recorded."""
    found = procid.identity(pid)
    if found is None:
        return
    entries = [e for e in _read_pids(base) if e["pid"] != pid]
    entries.append({"pid": pid, "root": str(root), **found})
    _write_pids(base, entries)


def unregister(base: Path, pid: int) -> None:
    _write_pids(base, [e for e in _read_pids(base) if e["pid"] != pid])


def ours(entry: dict, found: dict | None) -> bool:
    """Exact match of creation time and command line, and the line names this harness (R2)."""
    if not found or not entry.get("create_time") or not entry.get("cmdline"):
        return False
    if found["create_time"] != entry["create_time"] or found["cmdline"] != entry["cmdline"]:
        return False
    line = found["cmdline"]
    root = str(entry.get("root", ""))
    return ("launch_host.py" in line or "serve" in line) and bool(root) and (
        root in line or root.replace("\\", "/") in line)


def sweep_leftovers(base: Path, *, kill=procid.kill_tree) -> list[int]:
    """Kill each recorded process only if pid, creation time and command line all still match. Returns pids killed.
    Every entry is dropped afterwards: a mismatch means "not ours"."""
    killed = []
    for entry in _read_pids(base):
        if procid.alive(entry["pid"]) and ours(entry, procid.identity(entry["pid"])):
            kill(entry["pid"])
            killed.append(entry["pid"])
    _write_pids(base, [])
    return killed


class Host:
    def __init__(self, machine: Machine, base: Path, *, flags: list[str] | None = None, fault: str | None = None,
                 clock_file: Path | None = None, keeper_seconds: float | None = None, name: str = "host",
                 build: str | None = None):
        self.machine, self.base, self.flags = machine, Path(base), list(flags or [])
        self.fault, self.clock_file, self.keeper_seconds, self.build = fault, clock_file, keeper_seconds, build
        self.root = machine.root
        self.child = None
        self.ready: dict = {}
        self.stderr_path = self.base / f"{name}-{os.getpid()}.stderr"
        self.args_used: list[str] = []

    @property
    def url(self) -> str:
        return self.ready["url"]

    @property
    def viewer_url(self) -> str | None:
        return self.ready.get("viewer_url") or self.ready.get("viewer")

    def command(self) -> list[str]:
        launcher = [sys.executable, str(LAUNCHER), "--parent-pid", str(os.getpid()), "--base", str(self.base)]
        if self.clock_file:
            launcher += ["--clock-file", str(self.clock_file)]
        if self.keeper_seconds is not None:
            launcher += ["--keeper-seconds", str(self.keeper_seconds)]
        if self.build:
            launcher += ["--build", self.build]
        if self.fault:
            launcher += ["--harness-fault", self.fault]
        self.args_used = ["--root", str(self.root), "--host", "127.0.0.1", "--port", "0", "--viewer-port", "0",
                          *self.flags]
        return [*launcher, "--", *self.args_used]

    def start(self, timeout: float = 90) -> dict:
        env = {**self.machine.env, "TASKMASTER_TWOHOST": "1"}
        (self.base / launch_host.SENTINEL).write_text("a twohost harness run base", encoding="utf-8")
        with open(self.stderr_path, "wb") as stderr:
            self.child = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=stderr, env=env,
                                          cwd=str(self.base), creationflags=(
                                              subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                                          start_new_session=(os.name != "nt"))
        register(self.base, self.child.pid, self.root)
        self.ready = serve_helpers.read_ready_line(self.child, timeout=timeout, stderr_path=self.stderr_path)
        if self.ready.get("pid") and self.ready["pid"] != self.child.pid:
            register(self.base, int(self.ready["pid"]), self.root)
        return self.ready

    def stop(self) -> None:
        child, self.child = self.child, None
        if child is None:
            return
        pids = {child.pid, int(self.ready.get("pid") or 0)} - {0}
        serve_helpers.stop_tree(child)
        for pid in pids:
            if procid.alive(pid):
                procid.kill_tree(pid)
            unregister(self.base, pid)
        try:
            child.stdout.close()
        except OSError:
            pass

    def log_text(self) -> str:
        parts = [self.stderr_path.read_text(errors="replace")] if self.stderr_path.exists() else []
        serve_log = Path(self.root) / ".taskmaster" / "local" / "serve" / "serve.log"
        if serve_log.exists():
            parts.append(serve_log.read_text(errors="replace"))
        return "\n".join(parts)
