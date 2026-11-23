# User intent: prove the shared local-server contract helpers behave as both the server and the bridge rely on,
# and that the module stays stdlib-only so the bridge can import it without the server's environment.
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from taskmaster import localserve as ls
from taskmaster import server_fence, server_marker

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
BUILD = {"version": "7.5.0", "digest": "a" * 32}


@pytest.fixture(autouse=True)
def _fence_clean():
    server_fence.reset_for_tests()
    yield
    assert not server_fence.reset_for_tests()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    return root


def _private(project):
    from taskmaster.coordinator.ownership import private_directory
    return private_directory(project, ls.SERVE_DIR)


def _discover(project, **over):
    args = dict(state=ls.STATE_SERVING, url="http://127.0.0.1:5555/mcp", pid=os.getpid(), plugin_dir=project,
                token_file=project / "tok", build=BUILD, started="2026-11-22T00:00:00Z", nonce="ab")
    args.update(over)
    return ls.write_discovery(project, **args)


def test_atomic_write_roundtrip_leaves_no_temp(project):
    path = _discover(project)
    assert [p.name for p in path.parent.iterdir()] == [ls.DISCOVERY_NAME]
    record = ls.read_private_json(path)
    assert record["format"] == 1 and record["profile"] == "local" and record["build"] == BUILD
    ls.write_private_json(path, {"a": 1})
    assert ls.read_private_json(path) == {"a": 1}


def test_read_private_json_missing_and_garbage(project):
    assert ls.read_private_json(project / "nope.json") is None
    from taskmaster.coordinator.ownership import private_directory
    bad = private_directory(project, ls.SERVE_DIR) / "bad.json"
    bad.write_text("{not json")
    assert ls.read_private_json(bad) is None
    with pytest.raises(PermissionError):                # a file in a directory that is not owner-only
        ls.read_private_json(Path(__file__))


@pytest.mark.skipif(os.name == "nt", reason="posix mode bits")
def test_privacy_check_refuses_group_readable_file(project):
    path = _discover(project)
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        ls.read_private_json(path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_privacy_check_refuses_a_link(project):
    real = _discover(project)
    link = real.with_name("link.json")
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        ls.read_private_json(link)


def test_read_discovery_requires_a_held_lease(project):
    _discover(project)
    assert ls.read_discovery(project) is None          # lease free: stale
    with server_fence.claim_host(project):
        record = ls.read_discovery(project)
        assert record["url"] == "http://127.0.0.1:5555/mcp" and record["state"] == "serving"
        _discover(project, state=ls.STATE_DRAINING)
        assert ls.read_discovery(project)["state"] == "draining"
    ls.remove_discovery(project)
    ls.remove_discovery(project)                        # idempotent


def test_read_discovery_rejects_malformed_records(project):
    path = _discover(project)
    record = json.loads(path.read_text())
    with server_fence.claim_host(project):
        for change in ({"format": 2}, {"profile": "team"}, {"state": "x"}, {"url": 1}, {"build": {"version": "1"}}, {"pid": "9"}):
            ls.write_private_json(path, {**record, **change})
            assert ls.read_discovery(project) is None, change


def test_write_discovery_rejects_bad_state(project):
    with pytest.raises(ValueError):
        _discover(project, state="up")


def test_lease_probe_held_and_free(project):
    assert ls.host_lease_held(project) is False
    with server_fence.claim_host(project):
        assert ls.host_lease_held(project) is True
    assert ls.host_lease_held(project) is False


def test_result_file_roundtrip(project):
    path = _private(project) / "result.json"
    ls.write_result(path, ls.STATUS_READY, "", url="http://127.0.0.1:1/mcp")
    got = ls.read_result(path, timeout=1)
    assert got["status"] == "ready" and got["url"] == "http://127.0.0.1:1/mcp" and got["pid"] == os.getpid()
    with pytest.raises(ValueError):
        ls.write_result(path, "weird")


def test_result_file_timeout_is_an_error_outcome(project):
    started = time.monotonic()
    got = ls.read_result(_private(project) / "never.json", timeout=0.3, poll=0.02)
    assert got["status"] == ls.STATUS_ERROR and "no launch result" in got["reason"]
    assert 0.25 <= time.monotonic() - started < 3


def test_result_file_read_waits_for_a_late_write(project):
    import threading
    path = _private(project) / "late.json"
    threading.Timer(0.2, lambda: ls.write_result(path, ls.STATUS_LEASE_HELD, "other won")).start()
    assert ls.read_result(path, timeout=5, poll=0.02)["status"] == "lease_held"


def test_result_file_ignores_partial_and_unknown_status(project):
    path = _private(project) / "r.json"
    path.write_text('{"status": "bogus"}')
    assert ls.read_result(path, timeout=0.2, poll=0.02)["status"] == ls.STATUS_ERROR


def test_cwd_header_roundtrip():
    cwd = "C:\\Users\\x y\\proj-é"
    assert ls.decode_cwd(ls.encode_cwd(cwd)) == cwd
    assert " " not in ls.encode_cwd(cwd) and "é" not in ls.encode_cwd(cwd)


def test_header_and_status_constants():
    assert ls.HEADER_SESSION == "X-Taskmaster-Session" and ls.HEADER_SESSION_NONCE == "X-Taskmaster-Session-Nonce"
    assert ls.HEADER_CWD == "X-Taskmaster-Cwd" and ls.HEADER_AUTHORIZATION == "Authorization"
    assert set(ls.RESULT_STATUSES) == {"ready", "lease_held", "store_owned_busy", "refused", "error"}


def test_reexports_work():
    build = {"version": "7.5.0", "digest": "b" * 32}
    assert ls.same_build(build, dict(build)) and not ls.same_build(build, BUILD)
    assert ls.compare_versions({"version": "7.4.0"}, {"version": "7.5.0"}) == -1
    assert ls.declared_version(PLUGIN_ROOT) != ""
    assert len(ls.package_digest(PLUGIN_ROOT / "taskmaster")) == 32


def test_marker_detection_is_read_only(project):
    assert ls.server_mode_reason(project) is None
    server_marker.write_marker(project, "https://tm.example.com/mcp", HOST_ID)
    assert "tm.example.com" in ls.server_mode_reason(project)
    assert not os.path.lexists(project / ".git" / server_marker.LATCH_NAME)


def test_marker_detection_never_writes_a_latch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=60)
    (repo / ".taskmaster" / "local").mkdir(parents=True)
    server_marker.write_marker(repo, "https://tm.example.com/mcp", HOST_ID)
    assert ls.server_mode_reason(repo)
    assert not (repo / ".git" / server_marker.LATCH_NAME).exists()


def test_latch_alone_is_detected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=60)
    (repo / ".taskmaster" / "local").mkdir(parents=True)
    server_marker.record_latch(repo, server_marker.Marker(url="https://tm.example.com/mcp", host_id=HOST_ID))
    assert "tm.example.com" in ls.server_mode_reason(repo)


def test_unreadable_marker_is_a_reason(project):
    (project / server_marker.MARKER_NAME).write_text("{broken")
    assert ls.server_mode_reason(project)


def test_legacy_detection(project):
    assert ls.legacy_store(project) is True             # no database at all
    db = project / ".taskmaster" / "local" / "store.db"
    import sqlite3
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO meta VALUES('schema_version','1')")
    assert ls.legacy_store(project) is True             # schema 1
    with sqlite3.connect(db) as c:
        c.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    assert ls.is_native_store(project) is False          # schema 2 but no ready native manifest
    (project / ".taskmaster" / "local" / "store.db").write_bytes(b"garbage")
    assert ls.legacy_store(project) is True


def test_real_legacy_store_is_legacy_and_activated_one_is_native(tmp_path, monkeypatch):
    from native_twins import activate_native, point_server_at, scaffold
    from taskmaster import backlog_server as bs
    from taskmaster import store
    root = scaffold(tmp_path / "native")
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="e", name="E", done_when="done")
    store.reset_for_tests()
    assert ls.legacy_store(root) and not ls.is_native_store(root)
    activate_native(root)
    store.reset_for_tests()
    assert ls.is_native_store(root) and not ls.legacy_store(root)


def test_import_pin_no_third_party_modules():
    code = ("import sys; sys.path.insert(0, %r); import taskmaster.localserve; "
            "bad = [m for m in ('fastmcp','pydantic','starlette','uvicorn','yaml','anyio') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)") % str(PLUGIN_ROOT)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
