# User intent: before the N15 cutover fences a store, find what could still write to it — a
# live coordinator, other open SQLite connections, launcher processes (servers, hooks,
# scripts) — without disturbing any of them. The process scan only warns: it is best-effort.
"""Quiesce probes for the native cutover (N15 step 2). Standard library only.

- `live_owner(root)`: the coordinator ownership lock, probed without blocking, plus the
  discovery record it published. Authority comes from the kernel lock, never a PID.
- `open_writers(db_path)`: whether any other connection has the store open (WAL) or is
  writing, by two zero-timeout lock attempts on a fresh connection.
- `scan_processes(root)`: command lines that look like a launcher from the fencing
  handoff's inventory and point at this project or a Taskmaster plugin install.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

from taskmaster.bounded_run import run_bounded
from taskmaster.coordinator.ownership import ownership_held

_SECRETS = ("token", "nonce")


# ── live coordinator ────────────────────────────────────────────────────────

def live_owner(root) -> dict | None:
    """The live coordinator/service owning `root`, or None when the ownership lock is free.

    A running owner holds `owner.lock` exclusively, so the non-blocking probe fails at
    once and the owner is untouched. When the lock is free the probe takes and releases
    it in the same instant; a coordinator starting exactly then loses that race and exits
    cleanly (its client retries), as `ownership_held` documents. The discovery record's
    secrets (token, nonce) are never returned. A stale record with a free lock is None.
    """
    directory = Path(root) / ".taskmaster" / "local" / "coordinator"
    if not (directory / "owner.lock").exists():
        return None
    try:
        held = ownership_held(Path(root))
    except OSError as exc:
        # Cannot tell: report it as live, the cautious answer for a cutover.
        return {"lock_held": None, "error": f"ownership probe failed: {exc!r}", **_discovery(directory)}
    if not held:
        return None
    return {"lock_held": True, **_discovery(directory)}


def _discovery(directory: Path) -> dict:
    path = directory / "discovery.json"
    try:
        with path.open("rb") as stream:
            record = json.loads(stream.read(8193))
    except FileNotFoundError:
        return {"discovery": None}
    except (OSError, ValueError, UnicodeError) as exc:
        return {"discovery": None, "discovery_error": repr(exc)}
    if not isinstance(record, dict):
        return {"discovery": None, "discovery_error": "discovery record is not an object"}
    public = {k: v for k, v in record.items() if k not in _SECRETS}
    return {"pid": record.get("pid"), "port": record.get("port"), "discovery": public}


# ── other open connections ──────────────────────────────────────────────────

def _busy(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def open_writers(db_path) -> bool | None:
    """True if another connection has the store open or is writing; False if none; None
    if it cannot tell (no such file, not WAL, or an unexpected error).

    Two zero-timeout probes on a fresh read-write connection (never creating the file):

    1. `BEGIN IMMEDIATE` fails while any connection holds the write lock (an open write
       transaction), in any journal mode.
    2. In WAL mode every open connection, idle or reading, holds a shared lock for its
       lifetime, so `PRAGMA locking_mode=EXCLUSIVE` + `BEGIN EXCLUSIVE` fails while any
       other connection is open.

    It detects connections from any process on this machine, this process included —
    close your own connections to the file first. It cannot detect: idle connections
    to a rollback-journal (non-WAL) database (returns None), a process that has not yet
    opened the store (a next launch), writers on another host over a network share, or
    a connection opened the instant after the probe. It never blocks another connection
    for longer than the probe itself; a peer with a zero busy timeout that writes during
    that instant would see SQLITE_BUSY. If the probe is the last connection it closes,
    SQLite checkpoints the WAL as any clean close does; no data changes.
    """
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0,
                                     isolation_level=None, check_same_thread=False)
    except sqlite3.Error:
        return None
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            return True if _busy(exc) else None
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if mode != "wal":
            return None
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            return True if _busy(exc) else None
        return False
    except sqlite3.Error:
        return None
    finally:
        connection.close()


# ── launcher processes ──────────────────────────────────────────────────────

# The fencing handoff's launcher inventory (docs/handoffs/2026-09-09-native-client-fencing.md).
# The viewer and the Linear drain run inside a server or coordinator process, so they are
# found through their host; their module names are listed for a direct launch.
LAUNCHERS = (
    ("coordinator_service", ("taskmaster.coordinator.service",)),
    ("mcp_server", ("backlog_server",)),
    ("viewer", ("viewer_board", "viewer_detail")),
    ("hook", ("edit_resurface.py", "merge_gate.py", "merge_recorder.py", "taskmaster_merge_approve.py",
              "worktree_submodule_init.py", "session-start.sh", "run_hook.sh")),
    ("linear_worker", ("linear_worker",)),
    ("maintenance_script", ("backfill_tldr", "migrate_handover_statuses", "migrate_links")),
)
_ROOT_OPTION = re.compile(r"--root(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_PACKAGE = Path(__file__).resolve().parents[2]
_SCAN_TIMEOUT = 30.0


class ScanResult(list):
    """A list of process dicts; `note` says why the scan was partial or impossible."""

    def __init__(self, items=(), note: str | None = None):
        super().__init__(items)
        self.note = note


def _norm(text: str) -> str:
    text = text.replace("\\", "/")
    return text.lower() if os.name == "nt" else text


def _mentions(command: str, path: str) -> bool:
    """Whether `command` names `path` itself or something under it (not a sibling prefix)."""
    path = path.rstrip("/")
    start = command.find(path)
    while start >= 0:
        end = start + len(path)
        if end == len(command) or command[end] in "/\"' \t":
            return True
        start = command.find(path, start + 1)
    return False


def _spellings(root: Path) -> list[str]:
    spellings = {_norm(str(root))}
    try:
        spellings.add(_norm(str(root.resolve())))
    except OSError:
        pass
    return sorted(s for s in spellings if s)


def _classify(process: dict, roots: list[str]) -> dict | None:
    command = process.get("command_line") or ""
    norm = _norm(command)
    launcher = next((name for name, needles in LAUNCHERS if any(_norm(n) in norm for n in needles)), None)
    if launcher is None:
        return None
    option = _ROOT_OPTION.search(norm)
    cwd = process.get("cwd")
    if any(_mentions(norm, r) for r in roots):
        scope = "root"
    elif cwd and any(_mentions(_norm(cwd) + "/", r) for r in roots):
        scope = "root"
    elif option is not None or launcher == "coordinator_service":
        return None  # names a different project root explicitly
    elif "/plugins/" in norm and "taskmaster" in norm.split("/plugins/", 1)[1]:
        scope = "plugin"
    elif _mentions(norm, _norm(str(_PACKAGE))):
        scope = "checkout"
    elif cwd:
        return None  # its working directory is known and is another project
    else:
        scope = "unscoped"  # e.g. `uv run backlog_server.py` from an unknown directory
    return {"pid": process.get("pid"), "ppid": process.get("ppid"), "name": process.get("name"),
            "launcher": launcher, "scope": scope, "command_line": command[:1000],
            **({"cwd": cwd} if cwd else {})}


def _windows_processes(timeout: float) -> tuple[list[dict] | None, str | None]:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        return None, "PowerShell not found; process scan skipped"
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine "
              "| ConvertTo-Json -Compress")
    completed = run_bounded([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0:
        return None, f"process scan failed (exit {completed.returncode}): " \
                     f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}"
    text = completed.stdout.decode("utf-8", "replace").strip().lstrip("﻿")
    rows = json.loads(text) if text else []
    rows = [rows] if isinstance(rows, dict) else rows
    return [{"pid": r.get("ProcessId"), "ppid": r.get("ParentProcessId"), "name": r.get("Name"),
             "command_line": r.get("CommandLine")} for r in rows if isinstance(r, dict)], None


def _proc_processes() -> list[dict]:
    processes = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        base = Path(entry.path)
        try:
            argv = (base / "cmdline").read_bytes().split(b"\0")
            stat = (base / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        command = " ".join(a.decode("utf-8", "replace") for a in argv if a)
        if not command:
            continue
        name, _, rest = stat.rpartition(")")
        fields = rest.split()
        try:
            cwd = os.readlink(base / "cwd")
        except OSError:
            cwd = None
        processes.append({"pid": int(entry.name), "ppid": int(fields[1]) if len(fields) > 1 else None,
                          "name": name.partition("(")[2], "command_line": command, "cwd": cwd})
    return processes


def parse_ps(text: str) -> list[dict]:
    """`ps -A -o pid= -o ppid= -o args=` output as process dicts."""
    processes = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            processes.append({"pid": int(parts[0]), "ppid": int(parts[1]),
                              "name": os.path.basename(parts[2].split()[0]), "command_line": parts[2]})
    return processes


def _posix_processes(timeout: float) -> tuple[list[dict] | None, str | None]:
    if os.path.isdir("/proc") and os.path.exists("/proc/self/cmdline"):
        return _proc_processes(), None
    ps = shutil.which("ps")
    if ps is None:
        return None, "neither /proc nor ps is available; process scan skipped"
    completed = run_bounded([ps, "-A", "-o", "pid=", "-o", "ppid=", "-o", "args="], timeout=timeout)
    if completed.returncode != 0:
        return None, f"ps failed (exit {completed.returncode})"
    return parse_ps(completed.stdout.decode("utf-8", "replace")), None


def scan_processes(root, *, timeout: float = _SCAN_TIMEOUT) -> ScanResult:
    """Best-effort list of launcher processes that may write this project's store.

    Each match is `{pid, ppid, name, launcher, scope, command_line[, cwd]}`, where
    `scope` is `root` (the command line or, on Linux, the working directory names this
    project), `plugin` (a Taskmaster plugin install: its project is unknown), `checkout`
    (this Taskmaster source checkout) or `unscoped` (a relative launch such as
    `uv run backlog_server.py`, whose project cannot be told from the command line). A
    process naming another `--root` is excluded; so is this process.

    It cannot see: processes of other users or elevated ones whose command line is
    hidden, anything on another host, a launch that starts after the scan, or a process
    whose command line does not mention any inventory name (e.g. a renamed script, an
    arbitrary `sqlite3` shell). On Windows the working directory is not visible, so a
    plugin server for another project is still reported. Never raises: on failure the
    result is empty and `result.note` says why. Uses PowerShell CIM on Windows, `/proc`
    or `ps` elsewhere.
    """
    try:
        roots = _spellings(Path(root))
        if os.name == "nt":
            processes, note = _windows_processes(timeout)
        else:
            processes, note = _posix_processes(timeout)
        if processes is None:
            return ScanResult([], note)
        own = os.getpid()
        found = [match for process in processes if process.get("pid") != own
                 for match in [_classify(process, roots)] if match is not None]
        return ScanResult(sorted(found, key=lambda m: (m["launcher"], m["pid"] or 0)), note)
    except subprocess.TimeoutExpired:
        return ScanResult([], f"process scan timed out after {timeout:g}s")
    except Exception as exc:  # noqa: BLE001 - best-effort warning source, never a failure
        return ScanResult([], f"process scan failed: {exc!r}")


__all__ = ["live_owner", "open_writers", "scan_processes", "ScanResult", "LAUNCHERS", "parse_ps"]
