# User intent: pin the Python half of the relay contract: what "tm_bridge.py --relay-helper" (and --ensure-only) prints for each mode and
# reason, so the compiled relay can rely on one answer from the existing decision code instead of re-implementing it.
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tm_bridge as tb
from fake_local_server import TOKEN
from taskmaster import localserve as ls
from test_relay_blackbox import BRIDGE, NO_WINDOW, PLUGIN_ROOT, fake_up, native_root  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch):
    for name in ("TASKMASTER_LOCAL_SERVER", "TASKMASTER_BRIDGE_TESTING", "TASKMASTER_BRIDGE_TEST_BOUNDS", "TASKMASTER_BRIDGE_DISCRIMINATOR"):
        monkeypatch.delenv(name, raising=False)


def run_helper(stdin=b"", ensure_only=False):
    out = io.BytesIO()
    code = tb.helper_main(ensure_only, stdin=io.BytesIO(stdin), stdout=out)
    return code, [json.loads(line) for line in out.getvalue().decode().splitlines()]


def test_local_mode_prints_a_session_line_then_an_endpoint_line(native_root, fake_up, monkeypatch):
    from taskmaster.coordinator.protocol import build_identity
    fake, _ = fake_up()
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    code, lines = run_helper()
    assert code == 0 and len(lines) == 2 and all(line["v"] == 1 for line in lines)
    session, endpoint = lines
    assert session["kind"] == "session" and session["mode"] == "local" and session["reason"] is None
    assert Path(session["root"]) == native_root and session["build"] == build_identity() and Path(session["plugin_dir"]) == PLUGIN_ROOT
    assert endpoint == {"v": 1, "kind": "endpoint", "url": fake.record()["url"], "token": TOKEN, "build": fake.build}


def test_the_relays_discriminator_is_the_retire_requester(native_root, monkeypatch):
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    monkeypatch.setenv("TASKMASTER_BRIDGE_DISCRIMINATOR", "disc-from-relay")
    seen = []

    def fake_ensure(root, build, requester, **kw):
        seen.append(requester)
        raise tb.ServerUnavailable("not now")
    monkeypatch.setattr(tb, "ensure_server", fake_ensure)
    code, lines = run_helper()
    assert seen == ["disc-from-relay"] and lines[1] == {"v": 1, "kind": "unavailable", "reason": "not now"}


def test_an_unexpected_error_while_ensuring_is_an_unavailable_line(native_root, monkeypatch):
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    monkeypatch.setattr(tb, "ensure_server", lambda *a, **k: (_ for _ in ()).throw(ValueError("odd")))
    _, lines = run_helper()
    assert lines[1]["kind"] == "unavailable" and lines[1]["reason"] == "local server unavailable: ValueError('odd')"


@pytest.mark.parametrize("how,expected", [
    ("env", "TASKMASTER_LOCAL_SERVER=0"),
    ("setting", "project setting local_server: off"),
    ("legacy", "the project store is not a ready native store"),
])
def test_fallback_reasons_end_the_helper_after_line_one(native_root, monkeypatch, how, expected):
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    setting = native_root / ".taskmaster" / "local" / "settings.json"
    if how == "env":
        monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "off")
    elif how == "legacy":
        monkeypatch.setattr(ls, "legacy_store", lambda root: True)
    else:
        setting.write_text('{"local_server": "off"}', encoding="utf-8")
    try:
        code, lines = run_helper()
    finally:
        if setting.exists():
            setting.unlink()
    assert code == 0 and len(lines) == 1 and lines[0]["mode"] == "fallback" and expected in lines[0]["reason"]


def test_no_backlog_falls_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    _, lines = run_helper()
    assert lines[0]["mode"] == "fallback" and "no backlog" in lines[0]["reason"] and len(lines) == 1


def test_an_exception_in_the_decision_code_is_a_fallback_with_the_reason(native_root, monkeypatch):
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    monkeypatch.setattr(tb, "fallback_reason", lambda root: (_ for _ in ()).throw(RuntimeError("x")))
    _, lines = run_helper()
    assert lines[0]["mode"] == "fallback" and lines[0]["reason"] == "local server unavailable: RuntimeError('x')" and len(lines) == 1


def test_a_team_clone_is_mode_team_with_no_endpoint_line(tmp_path, monkeypatch):
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster-server.json").write_text(json.dumps(
        {"format": 1, "host_id": "0" * 32, "mode": "server", "url": "http://127.0.0.1:9/mcp"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    code, lines = run_helper()
    assert code == 0 and len(lines) == 1 and lines[0]["mode"] == "team" and lines[0]["kind"] == "session"


def test_ensure_only_answers_one_line_per_op_prints_no_session_line_and_exits_at_eof(native_root, fake_up, monkeypatch):
    fake, _ = fake_up()
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    code, lines = run_helper(b'{"op":"ensure"}\n{"op":"ensure"}\n{"op":"bogus"}\n', ensure_only=True)
    assert code == 0 and [line["kind"] for line in lines] == ["endpoint", "endpoint", "unavailable"]
    assert lines[0]["url"] == fake.record()["url"] and lines[0]["token"] == TOKEN and "unknown op" in lines[2]["reason"]


def test_ensure_only_in_a_fallback_project_answers_unavailable_with_the_reason(native_root, monkeypatch):
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "0")
    _, lines = run_helper(b'{"op":"ensure"}\n', ensure_only=True)
    assert lines == [{"v": 1, "kind": "unavailable", "reason": "TASKMASTER_LOCAL_SERVER=0"}]


def test_the_token_is_only_on_stdout_never_on_stderr(native_root, fake_up, monkeypatch, capsys):
    fake_up()
    monkeypatch.chdir(native_root)
    monkeypatch.setenv("TASKMASTER_ROOT", str(native_root))
    run_helper()
    assert TOKEN not in capsys.readouterr().err


def test_the_real_helper_process_speaks_only_json_lines_and_exits_zero(native_root, fake_up):
    fake, _ = fake_up()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TASKMASTER_LOCAL_SERVER") and not k.upper().startswith("CLAUDE")}
    env.update(TASKMASTER_ROOT=str(native_root), TASKMASTER_BRIDGE_DISCRIMINATOR="d-1")
    done = subprocess.run([sys.executable, str(BRIDGE), "--relay-helper"], cwd=str(native_root), env=env, capture_output=True,
                          timeout=120, creationflags=NO_WINDOW)
    lines = [json.loads(line) for line in done.stdout.decode().splitlines()]
    assert done.returncode == 0 and [line["kind"] for line in lines] == ["session", "endpoint"] and TOKEN not in done.stderr.decode()
    both = subprocess.run([sys.executable, str(BRIDGE), "--relay-helper", "--ensure-only"], cwd=str(native_root), env=env,
                          input=b'{"op":"ensure"}\n', capture_output=True, timeout=120, creationflags=NO_WINDOW)
    answers = [json.loads(line) for line in both.stdout.decode().splitlines()]
    assert both.returncode == 0 and [line["kind"] for line in answers] == ["endpoint"]


# -- test-mode variables (only with TASKMASTER_BRIDGE_TESTING=1) --------------------------------------------------------------

def test_test_bounds_apply_in_test_mode_and_are_ignored_otherwise(monkeypatch):
    monkeypatch.setattr(tb, "SHORT_TIMEOUT", tb.SHORT_TIMEOUT)
    monkeypatch.setattr(tb, "GET_BACKOFF", tb.GET_BACKOFF)
    monkeypatch.setenv("TASKMASTER_BRIDGE_TEST_BOUNDS", json.dumps({"SHORT_TIMEOUT": 1.5, "GET_BACKOFF": [0.1, 0.2], "INIT_WAIT": 0}))
    assert tb._apply_test_bounds() is None and tb.SHORT_TIMEOUT == 30.0           # no TESTING flag: nothing changes
    monkeypatch.setenv("TASKMASTER_BRIDGE_TESTING", "1")
    assert tb._apply_test_bounds() is None
    assert tb.SHORT_TIMEOUT == 1.5 and tb.GET_BACKOFF == (0.1, 0.2)


def test_an_unknown_test_bound_is_refused_in_test_mode_only(monkeypatch, capsys):
    monkeypatch.setenv("TASKMASTER_BRIDGE_TEST_BOUNDS", json.dumps({"NOPE": 1}))
    assert tb._apply_test_bounds() is None
    monkeypatch.setenv("TASKMASTER_BRIDGE_TESTING", "1")
    assert tb._apply_test_bounds() == "NOPE" and "tm-bridge: unknown test bound NOPE" in capsys.readouterr().err
    assert tb.main(["--relay-helper"]) == 2


def test_test_argv_variables_need_the_testing_flag(monkeypatch):
    monkeypatch.setenv("TASKMASTER_BRIDGE_TEST_SERVER_ARGV", json.dumps(["x", "y"]))
    assert tb._test_argv("TASKMASTER_BRIDGE_TEST_SERVER_ARGV") is None
    monkeypatch.setenv("TASKMASTER_BRIDGE_TESTING", "1")
    assert tb._test_argv("TASKMASTER_BRIDGE_TEST_SERVER_ARGV") == ["x", "y"]
    monkeypatch.setenv("TASKMASTER_BRIDGE_TESTING", "yes")
    assert tb._test_argv("TASKMASTER_BRIDGE_TEST_SERVER_ARGV") is None
