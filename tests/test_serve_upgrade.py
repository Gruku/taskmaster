# User intent: prove `serve --force-upgrade` replaces a running older daemon only for a local operator who can
# read the token, that no forged or malformed proof retires anything, and that the hand-over never opens the
# store or sends to a stale address (team-server-009 Steps 8-9).
from __future__ import annotations

import io
import json
import logging
import socket
import threading

import pytest

from taskmaster import server_fence as fence
from taskmaster.coordinator import protocol
from taskmaster.coordinator import service as service_module
from taskmaster.serve import upgrade
from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.config import ServeConfig, ServeRefused, ServeUnavailable
from taskmaster.serve.daemon import ServeCoordinator, ServeDaemon
from test_native_service_build import OLD, root  # noqa: F401

SAME = protocol.build_identity()
NEWER = {"version": "99.0.0", "digest": "c" * 32}
OLDER = {"version": "0.0.1", "digest": "d" * 32}


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    assert not fence.reset_for_tests()


def message(coordinator, requester, **extra):
    return {"identity": dict(coordinator.identity, nonce=coordinator.nonce, build=requester), "method": "retire",
            **extra}


def proof_for(root, coordinator, requester):
    token, _ = ensure_operator_token(root)
    return upgrade.upgrade_proof(upgrade.proof_key(token), coordinator.nonce, requester)


@pytest.fixture
def owner(root):
    coordinator = ServeCoordinator(root).start()
    threading.Thread(target=lambda: (coordinator.stopping.wait(), coordinator.close()), daemon=True).start()
    yield coordinator
    coordinator.stop()
    coordinator.close()


# -- Step 8: the retire fence -----------------------------------------------------------------------

def test_no_proof_is_refused_with_the_new_text(owner):
    answer = owner.dispatch(message(owner, NEWER))
    assert answer["state"] == "refused" and "--force-upgrade" in answer["reason"]
    assert not owner.stopping.is_set()


@pytest.mark.parametrize("bad", [
    "0" * 64,                                   # wrong
    "é" * 64,                                   # non-ASCII text (hmac.compare_digest would raise TypeError)
    b"0" * 64, 7, None, "0" * 63, "0" * 65, "A" * 64,
])
def test_a_bad_proof_is_refused_and_logged(owner, bad, caplog):
    with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        answer = owner.dispatch(message(owner, NEWER, force_upgrade=bad))
    assert answer["state"] == "refused" and "proof" in answer["reason"]
    assert "refused a retire request" in caplog.text
    assert not owner.stopping.is_set()


def test_a_proof_for_another_nonce_or_build_is_refused(root, owner):
    token, _ = ensure_operator_token(root)
    key = upgrade.proof_key(token)
    for forged in (upgrade.upgrade_proof(key, "x" * 48, NEWER), upgrade.upgrade_proof(key, owner.nonce, OLD)):
        assert owner.dispatch(message(owner, NEWER, force_upgrade=forged))["state"] == "refused"
    assert not owner.stopping.is_set()


def test_a_valid_proof_carrying_an_origin_is_refused(root, owner):
    answer = owner.dispatch(message(owner, NEWER, force_upgrade=proof_for(root, owner, NEWER),
                                    origin={"principal": "operator"}))
    assert answer["state"] == "refused" and "host-local" in answer["reason"]
    assert not owner.stopping.is_set()


def test_a_valid_proof_from_a_newer_build_retires_an_idle_owner(root, owner):
    answer = owner.dispatch(message(owner, NEWER, force_upgrade=proof_for(root, owner, NEWER)))
    assert answer["state"] == "retiring" and owner.retired_for == NEWER
    assert owner.stopping.is_set()


@pytest.mark.parametrize("requester", [SAME, OLDER])
def test_a_valid_proof_from_the_same_or_an_older_build_is_refused(root, owner, requester):
    answer = owner.dispatch(message(owner, requester, force_upgrade=proof_for(root, owner, requester)))
    assert answer["state"] == "refused" and owner.retired_for is None
    assert not owner.stopping.is_set()


def test_a_busy_owner_answers_busy_and_clears_retired_for(root, owner, monkeypatch):
    monkeypatch.setattr(service_module.Coordinator, "_busy", lambda self: True)
    answer = owner.dispatch(message(owner, NEWER, force_upgrade=proof_for(root, owner, NEWER)))
    assert answer["state"] == "busy" and owner.retired_for is None


def test_a_rotated_token_invalidates_old_proofs(root, owner):
    from taskmaster.serve.auth import rotate_operator_token
    old = proof_for(root, owner, NEWER)
    rotate_operator_token(root)
    assert owner.dispatch(message(owner, NEWER, force_upgrade=old))["state"] == "refused"
    assert owner.dispatch(message(owner, NEWER, force_upgrade=proof_for(root, owner, NEWER)))["state"] == "retiring"


# -- Step 9: --force-upgrade end to end ------------------------------------------------------------

def start_daemon(root, build, monkeypatch):
    """Start (and run) a daemon whose coordinator reports `build`; the patch is undone once it is up."""
    with monkeypatch.context() as patch:
        if build is not None:
            patch.setattr(service_module, "build_identity", lambda: build)
        daemon = ServeDaemon(ServeConfig(root=root, port=0, viewer_port=None), stdout=io.StringIO()).start()
    codes = []
    thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
    thread.start()
    assert daemon.ready.wait(30)
    return daemon, thread, codes


def test_discovery_client_exposes_only_retire():
    public = {name for name in vars(upgrade._DiscoveryClient) if not name.startswith("_")}
    assert public == {"retire"}


def test_force_upgrade_replaces_an_older_daemon(root, monkeypatch):
    old, old_thread, old_codes = start_daemon(root, OLD, monkeypatch)
    calls = []
    real = protocol.connect

    def spy(path, **kwargs):
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(protocol, "connect", spy)
    config = ServeConfig(root=root, port=0, viewer_port=None, force_upgrade=True, upgrade_timeout=60)
    new = ServeDaemon(config, stdout=io.StringIO())
    try:
        new.start()
        old_thread.join(30)
        assert not old_thread.is_alive() and old_codes == [0]
        assert new.coordinator.nonce != old.coordinator.nonce
        assert fence.host_lease_held(root)
    finally:
        new.close()
    assert not fence.host_lease_held(root)


def test_hand_over_never_opens_the_store(root, monkeypatch):
    old, old_thread, _ = start_daemon(root, OLD, monkeypatch)
    calls = []
    real = protocol.connect
    me = threading.get_ident()      # the old daemon runs in this process too; only this thread is the hand-over
    monkeypatch.setattr(protocol, "connect",
                        lambda p, **k: (calls.append(1) if threading.get_ident() == me else None, real(p, **k))[1])
    upgrade.hand_over(root, 60)
    old_thread.join(30)
    assert not calls


def test_the_lease_held_without_a_coordinator_refuses_and_sends_nothing(root):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    discovery = root / ".taskmaster/local/coordinator"
    discovery.mkdir(parents=True, exist_ok=True)
    (discovery / "discovery.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    with fence.host_lease(root):
        with pytest.raises(ServeUnavailable) as raised:
            upgrade.hand_over(root, 2)
    assert "non-daemon serve process" in str(raised.value)
    with pytest.raises(socket.timeout):
        listener.accept()
    listener.close()


def test_same_build_is_refused(root, monkeypatch):
    daemon, thread, _ = start_daemon(root, None, monkeypatch)
    try:
        with pytest.raises(ServeRefused) as same:
            upgrade.hand_over(root, 5)
        assert "already serves" in str(same.value)
    finally:
        daemon.request_stop("test")
        thread.join(30)


def test_a_newer_daemon_is_not_downgraded(root, monkeypatch):
    daemon, thread, _ = start_daemon(root, NEWER, monkeypatch)
    try:
        with pytest.raises(ServeRefused) as down:
            upgrade.hand_over(root, 5)
        assert "downgrade" in str(down.value)
    finally:
        daemon.request_stop("test")
        thread.join(30)


def test_a_busy_daemon_beyond_the_timeout_exits_69(root, monkeypatch):
    daemon, thread, _ = start_daemon(root, OLD, monkeypatch)
    monkeypatch.setattr(service_module.Coordinator, "_busy", lambda self: True)
    try:
        with pytest.raises(ServeUnavailable) as raised:
            upgrade.hand_over(root, 1)
        assert "still busy" in str(raised.value)
    finally:
        daemon.request_stop("test")
        thread.join(30)


def test_a_daemon_that_predates_force_upgrade_exits_69_with_guidance(root, monkeypatch):
    daemon, thread, _ = start_daemon(root, OLD, monkeypatch)
    monkeypatch.setattr(ServeCoordinator, "_upgrade_refusal", lambda self, r, m: (
        "this coordinator belongs to `taskmaster serve` and is never retired by a client; stop the daemon"))
    try:
        with pytest.raises(ServeUnavailable) as raised:
            upgrade.hand_over(root, 5)
        assert "predates --force-upgrade" in str(raised.value)
    finally:
        daemon.request_stop("test")
        thread.join(30)


def stray_session(root, build=None):
    from taskmaster.coordinator.service import Coordinator
    stray = Coordinator(root)
    if build is not None:
        stray.build = build
    stray.start()
    threading.Thread(target=lambda: (stray.stopping.wait(), stray.close()), daemon=True).start()
    return stray


def test_force_upgrade_retires_an_older_stray_session_coordinator(root):
    stray = stray_session(root, OLD)
    new = ServeDaemon(ServeConfig(root=root, port=0, viewer_port=None, force_upgrade=True, upgrade_timeout=60),
                      stdout=io.StringIO())
    try:
        new.start()
        assert stray.stopping.is_set() and fence.host_lease_held(root)
    finally:
        new.close()


@pytest.mark.real_service_process
def test_a_same_build_stray_started_before_the_marker_stops_itself(root):
    # A real second process: in-process it would share the daemon's pid, and so its exemption.
    import subprocess
    import sys
    from pathlib import Path
    from taskmaster import server_marker
    code = ("import sys, threading; sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "from taskmaster.coordinator.service import Coordinator\n"
            "c = Coordinator(Path(%r)); c.start(); print('ready', flush=True)\n"
            "c.stopping.wait(60); c.close(); print('stopped', flush=True)\n"
            ) % (str(Path(__file__).resolve().parents[1]), str(root))
    stray = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert stray.stdout.readline().strip() == "ready"
        server_marker.write_marker(root, "https://tm.example.com/mcp", fence.ensure_host_id(root))
        new = ServeDaemon(ServeConfig(root=root, port=0, viewer_port=None, force_upgrade=True, upgrade_timeout=60),
                          stdout=io.StringIO())
        try:
            new.start()
            assert stray.stdout.readline().strip() == "stopped"
        finally:
            new.close()
    finally:
        stray.kill()
        stray.wait(10)


@pytest.mark.parametrize("bad", [float("nan"), 0, -1, float("inf")])
def test_upgrade_timeout_must_be_finite_and_positive(root, bad):
    from taskmaster.serve.config import validate
    with pytest.raises(ServeRefused, match="upgrade-timeout"):
        validate(ServeConfig(root=root, upgrade_timeout=bad))


def test_retired_for_is_set_before_the_owner_stops(root, owner, monkeypatch):
    seen = []
    real = service_module.Coordinator.stop
    monkeypatch.setattr(service_module.Coordinator, "stop", lambda self: (seen.append(self.retired_for), real(self))[1])
    owner.dispatch(message(owner, NEWER, force_upgrade=proof_for(root, owner, NEWER)))
    assert seen and seen[0] == NEWER
