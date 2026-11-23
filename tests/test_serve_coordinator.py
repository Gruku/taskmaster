# User intent: the daemon's coordinator stays loopback-only and cannot be retired or shut down, and no client in the daemon starts or retires one.
import json
import threading

import pytest

from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import control_cli
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import HandshakeError, ServiceUnavailable
from taskmaster.serve.daemon import ServeCoordinator
from test_native_service_build import NEW, OLD, forbid_launch, owners, request, root  # noqa: F401

FUTURE = {'version': '99.0.0', 'digest': 'c' * 32}


@pytest.fixture
def serve_owner():
    started = []

    def make(root):
        owner = ServeCoordinator(root).start()
        started.append(owner)
        threading.Thread(target=lambda: (owner.stopping.wait(), owner.close()), daemon=True).start()
        return owner

    yield make
    for owner in started:
        owner.stop()
    for owner in started:
        owner.close()


def test_serve_coordinator_listens_on_loopback_only(root, serve_owner):
    owner = serve_owner(root)
    assert owner.server.server_address[0] == "127.0.0.1"
    record = json.loads((root / ".taskmaster/local/coordinator/discovery.json").read_text())
    assert record["port"] == owner.server.server_port


@pytest.mark.parametrize("build", [NEW, OLD, FUTURE])
def test_serve_coordinator_refuses_retire_from_any_build(root, serve_owner, build):
    owner = serve_owner(root)
    answer = owner.retire(build)
    assert answer["state"] == "refused" and "taskmaster serve" in answer["reason"]
    assert not owner.stopping.is_set()
    assert Client(root, autostart=False).status()["nonce"] == owner.nonce


def test_newer_build_client_cannot_retire_the_daemon_coordinator(root, serve_owner, monkeypatch):
    owner = serve_owner(root)
    nonce = owner.nonce
    forbid_launch(monkeypatch)
    client = Client(root)
    client.build = FUTURE
    with pytest.raises(HandshakeError, match="refused to retire") as exc:
        client.execute(request(client))
    assert exc.value.may_have_committed is False
    assert "systemctl restart taskmaster" in str(exc.value) and "--force-upgrade" in str(exc.value)
    assert not owner.stopping.is_set() and owner.nonce == nonce
    same = Client(root, autostart=False)
    assert same.execute(request(same, key="two"))["receipt"]["affected"]


def test_shutdown_over_ipc_is_refused(root, serve_owner):
    owner = serve_owner(root)
    with pytest.raises(ValueError, match="taskmaster serve"):
        Client(root, autostart=False).shutdown()
    assert not owner.stopping.is_set()
    assert Client(root, autostart=False).status()["nonce"] == owner.nonce


def test_shutdown_with_a_bad_handshake_is_refused_first(root, serve_owner):
    serve_owner(root)
    client = Client(root, autostart=False)
    record = client._discovery()
    with pytest.raises(HandshakeError):
        client._send(record, "shutdown", identity=dict(client.identity, store_id="x"))


def test_pinned_process_never_autostarts(root, monkeypatch):
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", False)
    forbid_launch(monkeypatch)
    client = Client(root)
    assert client.autostart is False
    with pytest.raises(ServiceUnavailable):
        client.execute(request(client))


def test_pinned_process_never_retires(root, owners, monkeypatch):  # noqa: F811
    serve, _ = owners
    old = serve(OLD, root=root)
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", False)
    forbid_launch(monkeypatch)
    client = Client(root)
    with pytest.raises(HandshakeError, match="differs"):
        client.execute(request(client))
    assert not old.stopping.is_set()


def test_retire_refusal_without_a_reason_is_unchanged(root, owners, monkeypatch):  # noqa: F811
    serve, _ = owners
    serve(OLD, root=root)
    forbid_launch(monkeypatch)
    client = Client(root)
    client.build = NEW
    real = client._send

    def send(record, method, **kw):
        if method == "retire":
            return {"state": "refused"}
        return real(record, method, **kw)
    monkeypatch.setattr(client, "_send", send)
    with pytest.raises(HandshakeError) as exc:
        client.execute(request(client))
    # execute() appends its own "; no command ran; request_id=..." note after the message.
    assert "refused to retire; no command ran" in str(exc.value)


def test_coordinator_stop_cli_reports_the_serve_refusal(root, serve_owner, capsys):
    owner = serve_owner(root)
    assert control_cli.main(["stop", "--root", str(root), "--wait", "2"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "refused" and "stop the daemon" in report["detail"]
    assert not owner.stopping.is_set()
    assert Client(root, autostart=False).status()["nonce"] == owner.nonce


def test_unpinned_default_is_unchanged(root):
    assert client_module.AUTOSTART_ALLOWED is True
    assert Client(root).autostart is True


def test_serve_coordinator_sanitizes_over_ipc(root, serve_owner, monkeypatch):  # noqa: F811
    import sqlite3
    original = ServeCoordinator.dispatch

    def dispatch(self, message):
        if isinstance(message, dict) and message.get("method") == "status":
            raise sqlite3.OperationalError("no such table: secret")
        return original(self, message)
    monkeypatch.setattr(ServeCoordinator, "dispatch", dispatch)
    serve_owner(root)
    with pytest.raises(ServiceUnavailable) as exc:
        Client(root, autostart=False).status()
    assert "internal error in the coordinator (ref c" in str(exc.value) and "secret" not in str(exc.value)


def test_shutdown_if_idle_never_stops_the_serve_coordinator_and_the_hand_over_leaves_it(root, serve_owner):
    # local-server-004 review I1: shutdown_if_idle is for session coordinators; the daemon's own is never stopped by it
    from taskmaster.serve import local as local_profile
    from taskmaster.serve.local import StoreOwnedBusy
    owner = serve_owner(root)
    with pytest.raises(ValueError):
        Client(root, autostart=False).call("shutdown_if_idle")
    with pytest.raises(ValueError):
        Client(root, autostart=False).call("shutdown_if_idle", origin={"principal": "a" * 16})
    assert not owner.stopping.is_set()
    with pytest.raises(StoreOwnedBusy):
        local_profile.hand_over_session_coordinator(root, wait=0.6)
    assert not owner.stopping.is_set()
