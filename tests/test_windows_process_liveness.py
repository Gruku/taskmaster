"""A liveness check must never signal or terminate the process it inspects."""
import os
import subprocess
import sys

import pytest

from taskmaster import store


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_windows_probe_never_calls_os_kill(monkeypatch):
    def forbidden(*args):
        raise AssertionError("os.kill(pid, 0) terminates processes on Windows")
    monkeypatch.setattr(store.os, "kill", forbidden)
    assert store._local_pid_alive(os.getpid()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_windows_live_and_exited_child(monkeypatch):
    def forbidden(*args):
        raise AssertionError("liveness must not signal the child")
    monkeypatch.setattr(store.os, "kill", forbidden)
    # The parent owns this child. EOF requests an orderly exit; no kill probe.
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        assert store._local_pid_alive(child.pid) is True
        assert child.poll() is None
    finally:
        child.communicate(timeout=10)
    assert store._local_pid_alive(child.pid) is False


@pytest.mark.parametrize("pid", [None, 0, -1, "invalid", 2**40])
def test_invalid_process_ids_are_not_probed(pid):
    assert store._local_pid_alive(pid) is False
