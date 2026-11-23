# User intent: pin every branch of bin/tm-relay (the sh entry) so a bad or missing relay binary always degrades to the Python bridge instead of taking tm down.
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from taskmaster.bounded_run import run_bounded

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
WIN = os.name == "nt"
EXE_NAME = "tm-relay.exe" if WIN else "tm-relay-%s-%s" % (platform.system().lower(), platform.machine())



ENTRY = ROOT / "bin" / "tm-relay"
pytestmark = pytest.mark.skipif(SH is None, reason="sh not available")


@pytest.fixture
def plugin(tmp_path):
    bin_dir = tmp_path / "plugin" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "tm-relay").write_bytes(ENTRY.read_bytes())
    fake = tmp_path / "fakebin"
    fake.mkdir()
    uv = fake / "uv"
    uv.write_bytes(b'#!/bin/sh\necho "UV $*"\n')
    uv.chmod(0o755)
    return tmp_path / "plugin", fake


def _run(plugin, args=(), input=None, **env):
    p, fake = plugin
    e = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"])
    e.pop("TASKMASTER_RELAY", None)
    e.update(env)
    return run_bounded([SH, str(p / "bin" / "tm-relay"), *args], timeout=60, text=True, input=input, env=e)


def _install(plugin, standin):
    shutil.copy(standin, plugin[0] / "bin" / EXE_NAME)
    (plugin[0] / "bin" / EXE_NAME).chmod(0o755)


def test_entry_is_lf_with_sh_shebang_and_exec_bit():
    raw = ENTRY.read_bytes()
    assert raw.startswith(b"#!/bin/sh\n") and b"\r" not in raw
    mode = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "bin/tm-relay"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30).stdout.split()[0]
    assert mode == "100755"


def test_no_binary_runs_the_python_bridge(plugin):
    r = _run(plugin, ["x"])
    assert r.returncode == 0
    assert "UV run --no-project --python >=3.11 python" in r.stdout and "tm_bridge.py x" in r.stdout
    assert "does not run" not in r.stderr


def test_binary_that_fails_version_falls_back_with_note(plugin, standin):
    _install(plugin, standin)
    r = _run(plugin, TM_STANDIN="exit:3")
    assert "UV run" in r.stdout and "present but does not run; using the Python bridge" in r.stderr


def test_non_executable_file_falls_back(plugin):
    (plugin[0] / "bin" / EXE_NAME).write_bytes(b"not a binary\n")
    r = _run(plugin)
    assert "UV run" in r.stdout
    assert "present but does not run" in r.stderr


def test_python_override_skips_a_working_binary(plugin, standin):
    _install(plugin, standin)
    r = _run(plugin, TASKMASTER_RELAY="python")
    assert "UV run" in r.stdout and "does not run" not in r.stderr


def test_working_binary_is_exec_with_args_and_stdin_intact(plugin, standin):
    _install(plugin, standin)
    r = _run(plugin, ["a", "b"], input="first-line\n", TM_STANDIN="echo")
    assert r.returncode == 0 and "UV" not in r.stdout
    assert "args=a b" in r.stdout and "stdin=first-line" in r.stdout  # the --version probe did not eat stdin


@pytest.mark.skipif(WIN, reason="same-pid exec is a POSIX property")
def test_working_binary_replaces_the_shell_process(plugin, standin):
    _install(plugin, standin)
    p, fake = plugin
    e = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"], TM_STANDIN="echo")
    r = run_bounded([SH, "-c", 'echo shell=$$; exec sh "$1"', "x", str(p / "bin" / "tm-relay")], timeout=60, text=True, env=e)
    pids = dict(kv.split("=") for kv in r.stdout.replace("\n", " ").split() if kv.startswith(("shell=", "pid=")))
    assert pids["shell"] == pids["pid"]
