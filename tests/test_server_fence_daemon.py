# User intent: prove the daemon claims the host lease before it touches the store, refuses to start on a bad or
# foreign marker, announces the marker, and is the only process the fence lets through (team-server-009 Step 7).
from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from serve_helpers import call_tool
from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import protocol
from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.config import ServeConfig, ServeRefused, ServeUnavailable
from taskmaster.serve.daemon import ServeDaemon
from test_native_service_build import root  # noqa: F401
from test_server_fence import HOST_ID, URL, run_python


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    assert not fence.reset_for_tests()


def config(root):
    return ServeConfig(root=root, port=0, viewer_port=None)


def mark_for_host(root, url=URL):
    host_id = fence.ensure_host_id(root)
    sm.write_marker(root, url, host_id)
    return host_id


def start(root):
    out = io.StringIO()
    daemon = ServeDaemon(config(root), stdout=out).start()
    thread = threading.Thread(target=daemon.serve, daemon=True)
    thread.start()
    assert daemon.ready.wait(30)
    return daemon, thread, out


def stop(daemon, thread):
    daemon.request_stop("test")
    thread.join(30)
    assert not thread.is_alive()


def test_the_lease_is_claimed_before_the_first_store_open(root, monkeypatch):
    mark_for_host(root)
    seen = []
    real = protocol.connect

    def spy(path, **kwargs):
        seen.append(fence.exempt(path))
        return real(path, **kwargs)

    monkeypatch.setattr(protocol, "connect", spy)
    daemon, thread, _ = start(root)
    try:
        assert seen and seen[0] is True
    finally:
        stop(daemon, thread)


def test_unreadable_marker_refuses_the_start(root):
    (root / sm.MARKER_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ServeRefused):
        ServeDaemon(config(root)).start()
    assert not fence.leaked_leases()


def test_host_id_mismatch_names_both_ids(root):
    fence.ensure_host_id(root)
    sm.write_marker(root, URL, HOST_ID)
    with pytest.raises(ServeRefused) as raised:
        ServeDaemon(config(root)).start()
    assert HOST_ID in str(raised.value) and fence.read_host_id(root) in str(raised.value)


def test_missing_host_id_is_refused(root):
    sm.write_marker(root, URL, HOST_ID)
    with pytest.raises(ServeRefused) as raised:
        ServeDaemon(config(root)).start()
    assert "missing" in str(raised.value)


def test_the_latch_binds_the_host_id_when_the_marker_is_gone(root):
    (root / ".git").mkdir(exist_ok=True)
    sm.write_marker(root, URL, HOST_ID)
    sm.record_latch(root, sm.read_marker(root))
    (root / sm.MARKER_NAME).unlink()
    fence.ensure_host_id(root)
    with pytest.raises(ServeRefused):
        ServeDaemon(config(root)).start()


def test_no_marker_logs_a_warning_and_announces_null(root, caplog):
    with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        daemon, thread, out = start(root)
    try:
        ready = json.loads(out.getvalue().splitlines()[0])
        assert ready["marker"] is None
        assert "no server marker" in caplog.text or "no server marker" in (
            root / ".taskmaster/local/serve/serve.log").read_text(encoding="utf-8")
    finally:
        stop(daemon, thread)


def test_marker_is_announced_and_other_processes_stay_fenced(root):
    host_id = mark_for_host(root)
    token, _ = ensure_operator_token(root)
    daemon, thread, out = start(root)
    try:
        ready = json.loads(out.getvalue().splitlines()[0])
        assert ready["marker"] == {"url": URL, "host_id": host_id}
        assert call_tool(daemon.url, token, "backlog_list_tasks")
        code = ("from pathlib import Path\n"
                "from taskmaster.coordinator import protocol\n"
                "from taskmaster.coordinator.adapter import Client\n"
                "from taskmaster.server_fence import ServerModeRefused\n"
                f"root = Path({str(root)!r})\n"
                "for build in (lambda: protocol.connect(root, readonly=True), lambda: Client(root)):\n"
                "    try:\n        build()\n    except ServerModeRefused:\n        print('refused')\n")
        result = run_python(code)
        assert result.stdout.split() == ["refused", "refused"], result.stdout + result.stderr
        with pytest.raises(ServeUnavailable) as second:
            ServeDaemon(config(root)).start()
        assert "holds the host lease" in str(second.value)
    finally:
        stop(daemon, thread)
    assert not fence.host_lease_held(root)


def test_host_lease_works_without_a_daemon(root):
    mark_for_host(root)
    assert fence.refusal(root) is not None
    with fence.host_lease(root):
        assert fence.refusal(root) is None
    assert fence.refusal(root) is not None


# -- team-server-014 D13: the host HEAD guard -----------------------------------------------------------------

def test_a_marker_root_whose_head_tracks_taskmaster_refuses_the_start(root):
    from native_git_helpers import init_repo
    mark_for_host(root)
    init_repo(root)                                    # commits the projection files: HEAD tracks .taskmaster
    with pytest.raises(ServeRefused, match=r"tracks \.taskmaster/.*host-head"):
        ServeDaemon(config(root)).start()
    assert not fence.leaked_leases()


def test_the_same_root_without_a_marker_starts_as_before(root):
    from native_git_helpers import init_repo
    init_repo(root)
    daemon, thread, _ = start(root)
    stop(daemon, thread)


def test_a_marker_root_with_an_untracked_head_starts(root):
    from native_git_helpers import git, init_repo
    mark_for_host(root)
    init_repo(root)
    git(root, "rm", "-r", "-q", "--cached", ".taskmaster")
    git(root, "commit", "-q", "-m", "untrack")
    daemon, thread, _ = start(root)
    stop(daemon, thread)
