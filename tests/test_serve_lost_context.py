# User intent: daemon code that sends a person's session without that person's key must be refused before it
# leaves the daemon, never run as the host (team-server-004 D4; closes the detectable case of team-server-020).
from __future__ import annotations

import contextvars
import http.client
import logging
import threading

import pytest

from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.client import LOST_CONTEXT_REFUSED, Client
from taskmaster.serve import auth
from taskmaster.team_identity import IdentityRefused
from test_native_service_build import root  # noqa: F401
from test_serve_session_guard import ANN, BOB, envelope, world  # noqa: F401


@pytest.fixture
def daemon_client(world, monkeypatch):  # noqa: F811
    """A Client as the serve daemon has it: an origin provider installed, here with no request in flight."""
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: None)
    return Client(world.root, autostart=False)


def spies(monkeypatch, client):
    seen = {"ready": 0, "connections": 0}
    ready = client._ready
    monkeypatch.setattr(client, "_ready", lambda *a, **k: seen.__setitem__("ready", seen["ready"] + 1) or ready(*a, **k))
    real = http.client.HTTPConnection.__init__

    def counting(self, *a, **k):
        seen["connections"] += 1
        real(self, *a, **k)

    monkeypatch.setattr(http.client.HTTPConnection, "__init__", counting)
    return seen


def test_execute_naming_a_person_without_a_key_is_refused_before_any_connection(world, daemon_client, monkeypatch, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    seen = spies(monkeypatch, daemon_client)
    with pytest.raises(IdentityRefused) as raised:
        daemon_client.call("execute", envelope=envelope(world, ANN), visibility="native")
    assert str(raised.value) == LOST_CONTEXT_REFUSED and type(raised.value) is IdentityRefused
    assert seen == {"ready": 0, "connections": 0}          # the check sits before _ready() (M4)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "execute" in errors[0].getMessage() and ANN in errors[0].getMessage()


def test_a_nested_claim_session_is_caught_too(world, daemon_client):  # noqa: F811
    batch = envelope(world, "host-1-aaaaaaaa", "batch", {"commands": [
        {"operation": "task.pick", "arguments": {"id": "test-epic-001", "session": ANN}}]})
    with pytest.raises(IdentityRefused, match="no authenticated key"):
        daemon_client.call("execute", envelope=batch, visibility="native")


def test_named_scope_methods_are_caught_and_other_methods_pass(world, daemon_client):  # noqa: F811
    with pytest.raises(IdentityRefused, match="no authenticated key"):
        daemon_client.call("linear_retry", caller_scope=ANN, request_id="r1")
    assert daemon_client.call("status")["nonce"] == world.owner.nonce


def test_a_host_shaped_scope_without_a_key_still_passes(world, daemon_client):  # noqa: F811
    # The merge-stamp replay shape: host work, host session, no origin.
    daemon_client.call("execute", envelope=envelope(world, "host-1-aaaaaaaa"), visibility="native")


def test_a_present_key_is_left_to_the_coordinator(world, daemon_client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: world.ann)
    daemon_client.call("execute", envelope=envelope(world, ANN), visibility="native")
    with pytest.raises(IdentityRefused, match="does not belong"):
        daemon_client.call("execute", envelope=envelope(world, BOB), visibility="native")


def test_host_cli_without_a_provider_is_unchanged(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", None)
    Client(world.root, autostart=False).call("execute", envelope=envelope(world, ANN), visibility="native")


def test_a_thread_that_lost_its_request_context_is_refused(world, monkeypatch):  # noqa: F811
    # The real provider reads a context variable; a plain thread starts with an empty context (003 T8).
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", auth.request_key_id)
    client = Client(world.root, autostart=False)


    outcome = {}

    def run(label, context):
        def work():
            try:
                client.call("execute", envelope=envelope(world, ANN), visibility="native")
                outcome[label] = "ran"
            except IdentityRefused as exc:
                outcome[label] = str(exc)
        thread = threading.Thread(target=context.run, args=(work,))
        thread.start()
        thread.join(60)

    with auth.viewer_principal(world.ann):
        run("copied", contextvars.copy_context())
        run("lost", contextvars.Context())
    assert outcome["copied"] == "ran"
    assert outcome["lost"] == LOST_CONTEXT_REFUSED
