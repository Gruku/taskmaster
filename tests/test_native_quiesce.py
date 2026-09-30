"""User intent: the cutover's quiesce probes must see a live coordinator, another open store
connection and a launcher process — proven against real child processes — without
disturbing them, and the process scan must degrade to an empty, explained result.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from taskmaster.coordinator.ownership import ownership_held
from taskmaster.native import quiesce

PACKAGE = Path(__file__).resolve().parents[1]


class Child:
    """A Python child that runs `code`, prints `ready <pid>`, then waits for stdin EOF."""

    def __init__(self, code: str, *argv: str):
        body = textwrap.dedent(code) + "\nimport os, sys\nprint('ready', os.getpid(), flush=True)\nsys.stdin.read()\n"
        env = dict(os.environ, PYTHONPATH=str(PACKAGE) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        self.process = subprocess.Popen([sys.executable, "-c", body, *argv], cwd=PACKAGE, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        line = self.process.stdout.readline().split()
        assert line[:1] == ["ready"], f"child failed to start: {line}"
        self.pid = int(line[1])

    @property
    def pids(self):
        return {self.pid, self.process.pid}

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    self.process.kill()
                self.process.wait(timeout=10)
        self.process.stdout.close()


@pytest.fixture
def spawn():
    children = []

    def start(code, *argv):
        child = Child(code, *argv)
        children.append(child)
        return child
    yield start
    for child in children:
        child.stop()


# ── live_owner ──────────────────────────────────────────────────────────────

OWNER = """
import os, sys
from pathlib import Path
from taskmaster.coordinator.ownership import Ownership
owner = Ownership(Path(sys.argv[1])).acquire()
owner.publish({"pid": os.getpid(), "port": 4321, "token": "t" * 64, "nonce": "n" * 48, "root": sys.argv[1]})
"""


def test_live_owner_sees_a_running_coordinator_and_leaves_it_alone(tmp_path, spawn):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    assert quiesce.live_owner(tmp_path) is None
    child = spawn(OWNER, str(tmp_path))
    for _ in range(3):
        owner = quiesce.live_owner(tmp_path)
        assert owner["lock_held"] is True
        assert owner["pid"] == child.pid and owner["port"] == 4321
        assert "token" not in owner["discovery"] and "nonce" not in owner["discovery"]
    assert ownership_held(tmp_path)  # the probe never took the lock from the owner
    assert child.process.poll() is None
    child.stop()
    # The discovery record outlives its owner; a free lock makes it stale.
    assert (tmp_path / ".taskmaster" / "local" / "coordinator" / "discovery.json").exists()
    assert quiesce.live_owner(tmp_path) is None


# ── open_writers ────────────────────────────────────────────────────────────

@pytest.fixture
def database(tmp_path):
    path = tmp_path / "store.db"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t(x)")
    connection.execute("INSERT INTO t VALUES(1)")
    connection.close()
    return path


IDLE = """
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("SELECT * FROM t").fetchall()
"""
WRITING = IDLE + 'connection.execute("BEGIN IMMEDIATE")\nconnection.execute("INSERT INTO t VALUES(2)")\n'


def test_open_writers_sees_idle_and_writing_connections_in_other_processes(database, spawn):
    assert quiesce.open_writers(database) is False
    idle = spawn(IDLE, str(database))
    assert quiesce.open_writers(database) is True
    idle.stop()
    assert quiesce.open_writers(database) is False
    writer = spawn(WRITING, str(database))
    assert quiesce.open_writers(database) is True
    writer.stop()
    assert quiesce.open_writers(database) is False
    connection = sqlite3.connect(database)  # the writer's transaction never committed
    try:
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        connection.close()


def test_open_writers_counts_this_processes_own_connection(database):
    connection = sqlite3.connect(database)
    try:
        connection.execute("SELECT 1 FROM t").fetchall()
        assert quiesce.open_writers(database) is True
    finally:
        connection.close()
    assert quiesce.open_writers(database) is False


def test_open_writers_cannot_tell_for_a_missing_file_and_never_creates_it(tmp_path):
    missing = tmp_path / "absent.db"
    assert quiesce.open_writers(missing) is None
    assert not missing.exists()


def test_open_writers_cannot_tell_for_a_rollback_journal_database(tmp_path):
    path = tmp_path / "plain.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE t(x)")
    connection.commit()
    connection.close()
    assert quiesce.open_writers(path) is None


# ── scan_processes ──────────────────────────────────────────────────────────

SLEEPER = "pass"


def _found(result, child):
    return [m for m in result if m["pid"] in child.pids]


def test_scan_finds_a_server_launched_for_this_root(tmp_path, spawn):
    root = tmp_path / "project"
    root.mkdir()
    server = spawn(SLEEPER, "backlog_server.py", "--root", str(root))
    other = spawn(SLEEPER, "backlog_server.py", "--root", str(tmp_path / "project2"))
    unrelated = spawn(SLEEPER, "sleeper.py", str(root))
    result = quiesce.scan_processes(root)
    assert result.note is None
    matches = _found(result, server)
    assert matches and {m["launcher"] for m in matches} == {"mcp_server"}
    assert {m["scope"] for m in matches} == {"root"}
    assert str(root).lower() in matches[0]["command_line"].lower()
    assert not _found(result, other)
    assert not _found(result, unrelated)
    assert os.getpid() not in {m["pid"] for m in result}


def test_scan_reports_plugin_installs_and_hooks(tmp_path, spawn):
    plugin = tmp_path / "home" / ".claude" / "plugins" / "cache" / "gruku" / "taskmaster" / "6.0.2" / "backlog_server.py"
    server = spawn(SLEEPER, str(plugin))
    hook = spawn(SLEEPER, str(tmp_path / "project" / "hooks" / "merge_gate.py"))
    result = quiesce.scan_processes(tmp_path / "project")
    assert {m["scope"] for m in _found(result, server)} == {"plugin"}
    assert {(m["launcher"], m["scope"]) for m in _found(result, hook)} == {("hook", "root")}


@pytest.mark.skipif(os.name != "nt", reason="the PowerShell path is Windows-only")
def test_scan_without_powershell_is_empty_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(quiesce.shutil, "which", lambda name: None)
    result = quiesce.scan_processes(tmp_path)
    assert result == [] and "PowerShell" in result.note


def test_scan_timeout_and_failure_never_raise(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 0.1)
    monkeypatch.setattr(quiesce, "run_bounded", timeout)
    monkeypatch.setattr(quiesce.os.path, "isdir", lambda path: False)  # force `ps` on POSIX
    result = quiesce.scan_processes(tmp_path, timeout=0.1)
    assert result == [] and result.note
    assert "timed out" in result.note or "not available" in result.note

    def broken(*args, **kwargs):
        raise OSError("no such executable")
    monkeypatch.setattr(quiesce, "run_bounded", broken)
    result = quiesce.scan_processes(tmp_path)
    assert result == [] and result.note


def test_ps_output_parses():
    text = "  1     0 /sbin/init\n 4242   1 /usr/bin/python3 -m taskmaster.coordinator.service --root /p\nbad line\n"
    assert quiesce.parse_ps(text) == [
        {"pid": 1, "ppid": 0, "name": "init", "command_line": "/sbin/init"},
        {"pid": 4242, "ppid": 1, "name": "python3",
         "command_line": "/usr/bin/python3 -m taskmaster.coordinator.service --root /p"}]


@pytest.mark.parametrize("command,cwd,expected", [
    ("python -m taskmaster.coordinator.service --root /work/proj", None, "root"),
    ("python -m taskmaster.coordinator.service --root /work/proj2", None, None),
    ("uv run backlog_server.py", None, "unscoped"),
    ("uv run backlog_server.py", "/work/proj", "root"),
    ("uv run backlog_server.py", "/elsewhere", None),
    ("python /work/proj/hooks/edit_resurface.py", None, "root"),
    ("python /opt/tool.py /work/proj", None, None),
])
def test_classification_rules(command, cwd, expected, monkeypatch):
    monkeypatch.setattr(quiesce, "_norm", lambda text: text.replace("\\", "/"))
    match = quiesce._classify({"pid": 9, "command_line": command, "cwd": cwd}, ["/work/proj"])
    assert (match or {}).get("scope") == expected
