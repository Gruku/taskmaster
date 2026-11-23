# User intent: the serve CLI's refusals and token command, plus one real end-to-end MCP tool call over HTTP in a child process.
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import serve_helpers
from serve_helpers import call_tool
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.serve import cli, daemon
from taskmaster.serve.auth import ensure_operator_token, read_operator_token, token_path
from taskmaster.serve.config import ServeUnavailable
from test_native_service_build import root  # noqa: F401

REPO = Path(__file__).resolve().parent.parent


def test_root_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_non_loopback_bind_is_refused_without_the_flag(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "--host", "0.0.0.0"]) == 2
    assert "--insecure-bind" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("flag", [["--idle-exit", "abc"], ["--idle-exit", "5"], ["--plugin-dir", "x"]])
def test_local_options_without_local_are_refused(tmp_path, capsys, flag):
    assert cli.main(["--root", str(tmp_path), *flag]) == 2
    assert "only apply with --local" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_token_prints_and_rotates(tmp_path, capsys):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    assert cli.main(["token", "--root", str(tmp_path)]) == 0
    first = capsys.readouterr().out.strip()
    assert cli.main(["token", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == first
    assert cli.main(["token", "--root", str(tmp_path), "--rotate"]) == 0
    rotated = capsys.readouterr().out.strip()
    assert rotated != first and read_operator_token(token_path(tmp_path)) == rotated


def test_token_refuses_a_non_project(tmp_path, capsys):
    assert cli.main(["token", "--root", str(tmp_path)]) == 2
    assert capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_start_refusal_maps_to_its_exit_code(monkeypatch, tmp_path, capsys):
    def start(self):
        raise ServeUnavailable("x")
    monkeypatch.setattr(daemon.ServeDaemon, "start", start)
    assert cli.main(["--root", str(tmp_path)]) == 69
    assert capsys.readouterr().err.startswith("taskmaster serve: x")


@pytest.mark.parametrize("error, code", [(RuntimeError("fastmcp internals changed"), 2),
                                         (OSError(38, "Function not implemented"), 69)])
def test_unexpected_start_failures_map_to_documented_exit_codes(monkeypatch, tmp_path, capsys, error, code):
    # review-gate M4: no traceback and no undocumented exit 1 that systemd would restart-loop on.
    def start(self):
        raise error
    monkeypatch.setattr(daemon.ServeDaemon, "start", start)
    assert cli.main(["--root", str(tmp_path)]) == code
    err = capsys.readouterr().err
    assert err.startswith("taskmaster serve:") and "Traceback" not in err


def assert_no_import_time_viewer(root, ready):
    """team-server-006 D10.1: the child serves only the guarded viewer, never the unguarded solo one."""
    import hashlib
    solo_port = 6800 + int(hashlib.md5(str(Path(ready["server_root"]).resolve()).encode()).hexdigest(), 16) % 100
    try:
        answer = httpx.get(f"http://127.0.0.1:{solo_port}/api/identity", timeout=2)
    except httpx.TransportError:
        pass
    else:
        root_seen = answer.json().get("root") if answer.status_code == 200 else None
        assert root_seen is None or Path(root_seen).resolve() != root.resolve()
    assert ready["viewer_url"] and ready["viewer_url"].startswith("http://127.0.0.1:")
    login = httpx.get(ready["viewer_url"] + "login", timeout=5)
    assert login.status_code == 200 and 'name="token"' in login.text


@pytest.mark.slow
@pytest.mark.xdist_group("heavy_processes")
def test_end_to_end_mcp_tool_call_over_http(root, tmp_path):  # noqa: F811
    token, _ = ensure_operator_token(root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stderr_path = tmp_path / "serve.stderr"
    env = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    env["TASKMASTER_SERVICE_IDLE_SECONDS"] = "1"
    with open(stderr_path, "wb") as stderr:
        child = subprocess.Popen(
            [sys.executable, str(REPO / "taskmaster_cli.py"), "serve", "--root", str(root), "--port", "0",
             "--viewer-port", "0"],
            stdout=subprocess.PIPE, stderr=stderr, env=env, cwd=str(elsewhere),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"))
        try:
            ready = serve_helpers.read_ready_line(child, timeout=90, stderr_path=stderr_path)
            assert Path(ready["root"]) == root.resolve() and Path(ready["server_root"]) == root.resolve()
            assert_no_import_time_viewer(root, ready)
            time.sleep(2.5)                      # past the child's 1 s idle timeout
            added = call_tool(ready["url"], token, "backlog_add_task",
                              {"title": "Over HTTP", "epic": "test-epic", "phase": "dev"})
            assert "[seq " in added
            assert "Over HTTP" in call_tool(ready["url"], token, "backlog_get_task", {"task_id": "test-epic-002"})
            with pytest.raises(httpx.HTTPStatusError) as exc:
                call_tool(ready["url"], None, "backlog_list_tasks")
            assert exc.value.response.status_code == 401
            record = json.loads((root / ".taskmaster/local/coordinator/discovery.json").read_text())
            assert record["pid"] == ready["pid"]        # the venv python.exe is a launcher on Windows
            if os.name == "nt":
                serve_helpers.stop_tree(child)
            else:
                child.send_signal(signal.SIGTERM)
                assert child.wait(30) == 0
                assert "stopped (exit 0)" in stderr_path.read_text(errors="replace")
            deadline = time.monotonic() + 15
            while ownership_held(root) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not ownership_held(root)
        finally:
            serve_helpers.stop_tree(child)
            child.stdout.close()


# --- user commands (team-server-002) ---

import hashlib
import logging
import re
import threading

from taskmaster.coordinator import ownership
from taskmaster.serve import principals
from taskmaster.serve.principals import KEY_RE, StoreCheck, principals_path

HEX64 = re.compile(r"[0-9a-f]{64}")


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    return tmp_path


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def stored(proj):
    return principals_path(proj).read_text(encoding="utf-8")


def test_add_user_prints_the_key_once(proj, capsys):
    code, out, err = run(capsys, "add-user", "ann", "--root", str(proj), "--name", "Ann Lee", "--email", "ann@example.com")
    assert code == 0
    assert len(out.splitlines()) == 1 and KEY_RE.fullmatch(out.strip())
    assert "shown once" in err and out.strip() not in err
    assert out.strip() not in stored(proj) and hashlib.sha256(out.strip().encode()).hexdigest() in stored(proj)


@pytest.mark.parametrize("args, message", [
    (["ann"], "rotate-user"), (["operator"], "reserved"), (["Ann"], "handle")])
def test_add_user_refusals(proj, capsys, args, message):
    if args == ["ann"]:
        assert run(capsys, "add-user", "ann", "--root", str(proj))[0] == 0
    code, out, err = run(capsys, "add-user", *args, "--root", str(proj))
    assert code == 2 and message in err and not KEY_RE.search(out)


def test_add_user_refuses_a_non_project(tmp_path, capsys):
    code, out, err = run(capsys, "add-user", "ann", "--root", str(tmp_path))
    assert code == 2 and "not a taskmaster project" in err and list(tmp_path.iterdir()) == []


def test_add_user_reports_the_store_check(proj, capsys, monkeypatch):
    def answer(check):
        monkeypatch.setattr(principals, "inspect_store", lambda root, handle: check)
    answer(StoreCheck("deleted", None, True))
    code, out, err = run(capsys, "add-user", "ann", "--root", str(proj))
    assert code == 2 and "was deleted" in err and not principals_path(proj).exists()
    answer(StoreCheck("live", "Ann Lee", True))
    code, out, err = run(capsys, "add-user", "ann", "--root", str(proj), "--name", "X")
    assert code == 0 and "existing profile" in err and "ignored" in err
    answer(StoreCheck("unknown", None, None))
    code, out, err = run(capsys, "add-user", "bob", "--root", str(proj))
    assert code == 0 and "server checks on first write" in err and "team mode is off" not in err
    answer(StoreCheck("absent", None, False))
    code, out, err = run(capsys, "add-user", "cy", "--root", str(proj))
    assert code == 0 and "team mode is off" in err and KEY_RE.fullmatch(out.strip())


def test_key_file_keeps_the_key_off_stdout(proj, tmp_path_factory, capsys):
    kf = tmp_path_factory.mktemp("keys") / "ann.key"
    code, out, err = run(capsys, "add-user", "ann", "--root", str(proj), "--key-file", str(kf))
    assert code == 0 and not KEY_RE.search(out) and not KEY_RE.search(err) and "written to" in out
    [line] = kf.read_text().splitlines()
    assert KEY_RE.fullmatch(line) and hashlib.sha256(line.encode()).hexdigest() in stored(proj)
    ownership.verify_private(kf)
    if os.name != "nt":
        assert kf.stat().st_mode & 0o777 == 0o600
    before = stored(proj)
    code, out, err = run(capsys, "add-user", "bob", "--root", str(proj), "--key-file", str(kf))
    assert code == 2 and "already exists" in err and stored(proj) == before
    kf2 = kf.with_name("rotated.key")
    code, out, err = run(capsys, "rotate-user", "ann", "--root", str(proj), "--key-file", str(kf2))
    assert code == 0 and not KEY_RE.search(out) and not KEY_RE.search(err) and "written to" in out
    assert "rotated ann:" in err and KEY_RE.fullmatch(kf2.read_text().strip())


def test_cli_never_leaks_a_key_beyond_its_one_line(proj, capfd, caplog):
    caplog.set_level(logging.DEBUG)
    keys, outputs = [], []

    def call(*argv):
        capfd.readouterr()
        code = cli.main(list(argv))
        out, err = capfd.readouterr()
        outputs.append((out, err))
        return code, out

    base = ["--root", str(proj)]
    keys.append(call("add-user", "cy", *base)[1].strip())
    call("list-users", *base)
    call("list-users", "--json", *base)
    keys.append(call("rotate-user", "cy", *base)[1].strip())
    call("revoke-user", "cy", *base)
    keys.append(call("add-user", "cy", *base)[1].strip())      # reinstated: a third key
    assert call("add-user", "cy", *base)[0] == 2                # refused: already active
    assert call("rotate-user", "nobody", *base)[0] == 2
    assert all(KEY_RE.fullmatch(k) for k in keys) and len(set(keys)) == 3
    everything = {"stored": stored(proj), "caplog": caplog.text, "stderr": "".join(e for _, e in outputs)}
    for key in keys:
        secret = KEY_RE.fullmatch(key).group(2)
        for name, text in everything.items():
            assert key not in text and secret not in text, name
        assert sum(out.count(key) for out, _ in outputs) == 1
        assert sum(out.count(secret) for out, _ in outputs) == 1


def test_add_user_takes_no_key_argument(proj, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["add-user", "ann", "--root", str(proj), "--key", "x"])
    assert exc.value.code == 2 and not principals_path(proj).exists()


def test_rotate_and_revoke(proj, capsys):
    run(capsys, "add-user", "ann", "--root", str(proj))
    old = principals.list_users(proj)[0].key_id
    code, out, err = run(capsys, "rotate-user", "ann", "--root", str(proj))
    new = [r for r in principals.list_users(proj) if r.active][0].key_id
    assert code == 0 and KEY_RE.fullmatch(out.strip()) and f"rotated ann: {old} -> {new}" in err
    code, out, err = run(capsys, "revoke-user", "ann", "--root", str(proj))
    assert code == 0 and out.strip() == f"revoked ann ({new})"
    for command in ("rotate-user", "revoke-user"):
        assert run(capsys, command, "ann", "--root", str(proj))[0] == 2
        assert run(capsys, command, "nobody", "--root", str(proj))[0] == 2


def test_list_users_never_prints_a_hash(proj, capsys):
    run(capsys, "add-user", "ann", "--root", str(proj))
    run(capsys, "rotate-user", "ann", "--root", str(proj))
    run(capsys, "add-user", "bob", "--root", str(proj))
    run(capsys, "revoke-user", "bob", "--root", str(proj))
    code, out, err = run(capsys, "list-users", "--root", str(proj))
    lines = out.splitlines()
    assert code == 0 and any(" rotated " in line for line in lines) and any(" revoked " in line for line in lines)
    assert sum(line.endswith(" active") for line in lines) == 1
    assert not HEX64.search(out) and "sha256" not in out
    code, out, err = run(capsys, "list-users", "--json", "--root", str(proj))
    records = json.loads(out)
    assert code == 0 and not HEX64.search(out) and "sha256" not in out
    assert {"key_id", "handle", "role", "created", "revoked", "revoked_reason"} <= set(records[0])


def test_list_users_when_empty(proj, capsys):
    assert run(capsys, "list-users", "--root", str(proj)) == (0, "(no users)\n", "")


def test_lock_timeout_exits_69(proj, capsys, monkeypatch):
    monkeypatch.setattr(principals, "LOCK_SECONDS", 0.2)
    held, release = threading.Event(), threading.Event()

    def hold():
        with principals.locked(proj):
            held.set()
            release.wait(30)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(10)
    try:
        code, out, err = run(capsys, "add-user", "ann", "--root", str(proj))
        assert code == 69 and "another add-user" in err
    finally:
        release.set()
        thread.join(10)


def test_user_commands_need_no_running_daemon(root, capsys):  # noqa: F811
    base = ["--root", str(root)]
    assert run(capsys, "add-user", "ann", *base)[0] == 0
    assert run(capsys, "list-users", *base)[0] == 0
    assert run(capsys, "rotate-user", "ann", *base)[0] == 0
    assert run(capsys, "revoke-user", "ann", *base)[0] == 0
    assert ownership_held(root) is False


@pytest.mark.slow
@pytest.mark.xdist_group("heavy_processes")
def test_end_to_end_member_key_over_http(root, tmp_path):  # noqa: F811
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    env["TASKMASTER_SERVICE_IDLE_SECONDS"] = "1"
    made = subprocess.run([sys.executable, str(REPO / "taskmaster_cli.py"), "serve", "add-user", "ann", "--root", str(root)],
                          capture_output=True, text=True, env=env, cwd=str(elsewhere), timeout=120)
    assert made.returncode == 0, made.stderr
    key = made.stdout.strip()
    assert KEY_RE.fullmatch(key)
    stderr_path = tmp_path / "serve.stderr"
    with open(stderr_path, "wb") as stderr:
        child = subprocess.Popen(
            [sys.executable, str(REPO / "taskmaster_cli.py"), "serve", "--root", str(root), "--port", "0",
             "--viewer-port", "0"],
            stdout=subprocess.PIPE, stderr=stderr, env=env, cwd=str(elsewhere),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"))
        try:
            ready = serve_helpers.read_ready_line(child, timeout=90, stderr_path=stderr_path)
            assert not call_tool(ready["url"], key, "backlog_list_tasks").startswith("Error")
        finally:
            serve_helpers.stop_tree(child)
            child.stdout.close()


# --- team-server-006: hardening flags (plan Step 0) ---

@pytest.fixture
def captured_config(monkeypatch):
    seen = []

    class Fake:
        def __init__(self, config, **kwargs):
            seen.append(config)

        def start(self):
            return self

        def serve(self):
            return 0
    monkeypatch.setattr(daemon, "ServeDaemon", Fake)
    return seen


def test_hardening_flags_reach_the_config(tmp_path, captured_config):
    assert cli.main(["--root", str(tmp_path), "--max-body-bytes", "65536", "--header-timeout", "2",
                     "--body-timeout", "3", "--max-connections", "9", "--rate-limit", "5", "--rate-burst", "6",
                     "--max-inflight", "4", "--viewer-port", "0", "--viewer-origin", "https://v.example",
                     "--linger-seconds", "1.5", "--linger-bytes", "4096"]) == 0
    cfg = captured_config[0]
    assert (cfg.max_body_bytes, cfg.header_timeout, cfg.body_timeout, cfg.max_connections) == (65536, 2.0, 3.0, 9)
    assert (cfg.rate_per_minute, cfg.rate_burst, cfg.max_inflight) == (5, 6, 4)
    assert cfg.viewer_port == 0 and cfg.viewer_origins == ("https://v.example",)
    assert (cfg.linger_seconds, cfg.linger_bytes) == (1.5, 4096)


def test_viewer_port_defaults_to_7471_on_the_cli(tmp_path, captured_config):
    assert cli.main(["--root", str(tmp_path)]) == 0
    assert captured_config[0].viewer_port == 7471 and captured_config[0].viewer_origins == ()


def test_no_viewer_sets_none(tmp_path, captured_config):
    assert cli.main(["--root", str(tmp_path), "--no-viewer"]) == 0
    assert captured_config[0].viewer_port is None


def test_no_viewer_and_viewer_port_conflict(tmp_path, captured_config):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(tmp_path), "--no-viewer", "--viewer-port", "8000"])
    assert exc.value.code == 2 and captured_config == []
