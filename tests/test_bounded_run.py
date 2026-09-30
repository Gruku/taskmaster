# User intent: prove a subprocess timeout bounds the call even when a grandchild keeps
# the output handles open — the Git-for-Windows launcher hang seen on 7.0.0-rc.1.
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from taskmaster.bounded_run import run_bounded

# The child starts a long-lived grandchild that inherits stdout/stderr, reports its
# pid to the file named by argv[1], then stalls: the shape of `cmd\git.exe`
# launching `mingw64\bin\git.exe`.
LAUNCHER = ("import subprocess, sys, time\n"
            "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(sys.argv[1], 'w').write(str(grandchild.pid))\n"
            "time.sleep(60)\n")


def _alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def test_timeout_returns_even_when_a_grandchild_holds_the_output(tmp_path):
    marker = tmp_path / "pid"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable, "-c", LAUNCHER, str(marker)], timeout=5)
    assert time.monotonic() - started < 20
    if os.name == "nt":
        grandchild = int(marker.read_text())
        deadline = time.monotonic() + 5
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(grandchild), "timeout must end the whole process tree"


def test_captures_bytes_by_default_and_reports_the_exit_code():
    done = run_bounded([sys.executable, "-c",
                        "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)"],
                       timeout=30)
    assert (done.returncode, done.stdout, done.stderr) == (3, b"out", b"err")


def test_text_mode_translates_newlines_and_feeds_input():
    done = run_bounded([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                       input="abc", text=True, timeout=30)
    assert done.stdout == "ABC\n"


def test_check_raises_called_process_error():
    with pytest.raises(subprocess.CalledProcessError) as raised:
        run_bounded([sys.executable, "-c", "import sys; sys.stderr.write('no'); sys.exit(2)"],
                    check=True, timeout=30)
    assert raised.value.returncode == 2 and raised.value.stderr == b"no"


def test_stdin_is_not_inherited_by_default():
    done = run_bounded([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
                       text=True, timeout=30)
    assert done.stdout.strip() == "''"
