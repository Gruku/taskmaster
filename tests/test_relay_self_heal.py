# User intent: pin the SessionStart relay self-heal - a tm-relay.exe that cannot start is moved aside so tm degrades to the Python bridge, while anything uncertain (slow, odd, failing after start) is left alone, and the whole block is dormant until .mcp.json launches bin/tm-relay.
import getpass
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from taskmaster.bounded_run import run_bounded

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
WIN = os.name == "nt"




HOOK = ROOT / "hooks" / "session-start.sh"
pytestmark = [pytest.mark.skipif(BASH is None, reason="bash not available"),
              pytest.mark.skipif(not WIN, reason="the self-heal is Windows-only (MINGW/MSYS uname)")]
ACTIVE = '{"mcpServers": {"tm": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/tm-relay"}}}'
DORMANT = '{"mcpServers": {"tm": {"command": "uv", "args": ["run", "${CLAUDE_PLUGIN_ROOT}/tm_bridge.py"]}}}'


@pytest.fixture
def env(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    return plugin, proj


def _hook(env, mcp=ACTIVE, path_prefix=None, **extra):
    plugin, proj = env
    (plugin / ".mcp.json").write_text(mcp)
    e = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    e["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    e.update(extra)
    if path_prefix:
        e["PATH"] = str(path_prefix) + os.pathsep + e["PATH"]
    r = run_bounded([BASH, str(HOOK)], cwd=proj, env=e, timeout=90, text=True)
    assert r.returncode == 0, r.stderr
    json.loads(r.stdout)  # the hook's JSON output is never disturbed
    return r


def _wait(cond, limit=20):
    end = time.time() + limit
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return cond()


def _probe_done(mark):
    """The detached probe ran the stand-in (it writes the mark on start); give its verdict a moment to land."""
    assert _wait(mark.exists), "the probe never ran the stand-in"
    time.sleep(1.5)


def _exe(env, standin=None, junk=None):
    exe = env[0] / "bin" / "tm-relay.exe"
    if standin:
        shutil.copy(standin, exe)
    else:
        exe.write_bytes(junk)
    return exe


def _blocked(exe):
    return exe.with_name(exe.name + ".blocked")


def test_dormant_when_mcp_json_does_not_launch_the_relay(env):
    exe = _exe(env, junk=b"not an executable\n")
    r = _hook(env, mcp=DORMANT)
    assert exe.exists() and not _blocked(exe).exists() and "moved aside" not in r.stderr


def test_dormant_without_plugin_root_or_exe(env):
    r = _hook(env)  # active, but no exe present: nothing to do
    assert "moved aside" not in r.stderr


@pytest.mark.parametrize("junk", [b"not an executable\n", b"MZ\x00\x01garbage\xff\x00"], ids=["text", "pe-garbage"])
def test_damaged_exe_is_moved_aside(env, junk):
    exe = _exe(env, junk=junk)
    r = _hook(env)
    assert "moved aside" not in r.stderr  # the blocked session is not told by this hook
    assert _wait(lambda: _blocked(exe).exists()) and not exe.exists()
    r2 = _hook(env)  # the next start tells the user, once
    assert "tm-relay.exe could not start" in r2.stderr and "/mcp" in r2.stderr
    out = json.loads(r2.stdout)  # ...and puts it where the user and the model see it
    assert "moved aside" in out["systemMessage"] and "/mcp" in out["systemMessage"]
    assert "moved aside" in out["hookSpecificOutput"]["additionalContext"]
    r3 = _hook(env)
    assert "moved aside" not in r3.stderr and "systemMessage" not in json.loads(r3.stdout)


def test_denied_execute_is_moved_aside(env, standin):
    exe = _exe(env, standin)
    user = getpass.getuser()
    run_bounded(["icacls", str(exe), "/deny", "%s:(X)" % user], timeout=30, check=True)
    try:
        _hook(env)
        # the rename needs delete rights on the parent, which the deny ACE on the file does not remove
        assert _wait(lambda: _blocked(exe).exists()) and not exe.exists()
        assert "moved aside" in _hook(env).stderr
    finally:
        for p in (exe, _blocked(exe)):
            if p.exists():
                run_bounded(["icacls", str(p), "/remove:d", user], timeout=30)


def test_runnable_exe_is_left_alone(env, standin):
    exe = _exe(env, standin)
    mark = env[1] / "mark"
    r = _hook(env, TM_STANDIN_MARK=str(mark))
    _probe_done(mark)
    assert exe.exists() and not _blocked(exe).exists() and not r.stderr.strip()


@pytest.mark.parametrize("mode", ["odd", "exit:3"])
def test_odd_output_or_nonzero_exit_is_left_and_logged(env, standin, mode):
    exe = _exe(env, standin)
    serve = env[1] / ".taskmaster" / "local" / "serve"
    serve.mkdir(parents=True)
    _hook(env, TM_STANDIN=mode)
    assert _wait(lambda: (serve / "relay-heal.log").exists())
    assert exe.exists() and not _blocked(exe).exists()
    assert "left in place" in (serve / "relay-heal.log").read_text()


def test_leave_does_not_create_a_store_layout(env, standin):
    exe = _exe(env, standin)
    mark = env[1] / "mark"
    _hook(env, TM_STANDIN="odd", TM_STANDIN_MARK=str(mark))
    _probe_done(mark)
    assert exe.exists() and not (env[1] / ".taskmaster").exists()


def test_slow_start_past_the_bound_is_left_alone(env, standin):
    exe = _exe(env, standin)
    serve = env[1] / ".taskmaster" / "local" / "serve"
    serve.mkdir(parents=True)
    start = time.time()
    _hook(env, TM_STANDIN="sleep:8")
    assert time.time() - start < 4  # the hook returns without waiting on the probe
    assert _wait(lambda: (serve / "relay-heal.log").exists(), 25)
    assert exe.exists() and not _blocked(exe).exists()
    assert "rc=124" in (serve / "relay-heal.log").read_text()


def test_bare_timeout_on_path_is_not_used(env, standin, tmp_path):
    exe = _exe(env, standin)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    t = fake / "timeout"
    t.write_bytes(b"#!/bin/sh\nexit 126\n")  # would read as a definite start failure if the hook used it
    t.chmod(0o755)
    mark = env[1] / "mark"
    _hook(env, path_prefix=fake, TM_STANDIN_MARK=str(mark))
    _probe_done(mark)
    assert exe.exists() and not _blocked(exe).exists()


def test_the_detached_probe_does_not_hold_the_hooks_pipes(env, standin):
    _exe(env, standin)
    plugin, proj = env
    (plugin / ".mcp.json").write_text(ACTIVE)
    e = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    e.update(CLAUDE_PLUGIN_ROOT=str(plugin), TM_STANDIN="sleep:8")
    start = time.time()
    p = subprocess.Popen([BASH, str(HOOK)], cwd=proj, env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.communicate(timeout=20)  # returns on EOF of both pipes, like a node 'close' event
    assert time.time() - start < 4
