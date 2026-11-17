# User intent: the server's coordinator refuses any command whose session belongs to another person, before it is queued and again before it runs.
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from taskmaster.coordinator import identity_gate
from taskmaster.coordinator.actor_resolver import LOCAL, Origin
from taskmaster.coordinator.client import Client
from taskmaster.native import claims, commands
from taskmaster.native.claims import Requester
from taskmaster.native.contracts import Conflict
from taskmaster.serve.daemon import ServeCoordinator
from taskmaster.serve.principals import PrincipalRegistry, add_user, principals_path, revoke_user
from taskmaster.serve.sessions import SESSION_REFUSED
from taskmaster.team_identity import IdentityRefused
from test_native_service_build import root  # noqa: F401

ANN, BOB, OPAL = "ann@" + "a" * 16, "bob@" + "b" * 16, "opal@" + "c" * 16
_KEYS = iter(range(10 ** 6))


@pytest.fixture
def world(root, monkeypatch):  # noqa: F811
    # Team mode off: ServeIdentityGate must not be what refuses (review P-M3).
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root_, *, project_doc=None: False)
    _, ann, _ = add_user(root, "ann")
    _, bob, _ = add_user(root, "bob")
    _, opal, _ = add_user(root, "opal", role="operator")
    registry = PrincipalRegistry(principals_path(root))
    owner = ServeCoordinator(root, registry=registry).start()
    try:
        yield SimpleNamespace(root=root, owner=owner, ann=ann.key_id, bob=bob.key_id, opal=opal.key_id)
    finally:
        owner.close()


def envelope(world, scope, operation="task.patch", arguments=None, key=None):
    return {"protocol": 2, "store_id": world.owner.identity["store_id"], "caller_scope": scope,
            "request_id": key or f"g{next(_KEYS)}", "operation": operation,
            "arguments": arguments if arguments is not None else {"id": "test-epic-001", "set": {"title": "T"}},
            "expected_revisions": []}


def submit(world, value, who):
    origin = LOCAL if who is None else Origin(principal=who)
    return world.owner.submit(value, origin=origin).result(30)


def test_own_scope_commits(world):
    assert submit(world, envelope(world, ANN), world.ann)["affected"]


def test_another_handles_scope_is_refused_before_queueing(world, monkeypatch):
    puts = []
    real = world.owner.queue.put_nowait
    monkeypatch.setattr(world.owner.queue, "put_nowait", lambda work: puts.append(work) or real(work))
    value = envelope(world, BOB)
    with pytest.raises(IdentityRefused) as raised:
        world.owner.submit(value, origin=Origin(principal=world.ann))
    assert type(raised.value) is IdentityRefused and str(raised.value) == SESSION_REFUSED
    assert puts == [] and (BOB, value["request_id"]) not in world.owner.pending


@pytest.mark.parametrize("scope", ["host-1-aaaaaaaa", "explicit-sync", "coordinator-identity", "ann@viewer2"])
def test_host_shaped_scope_from_a_remote_origin_is_refused(world, scope):
    with pytest.raises(IdentityRefused, match="does not belong"):
        submit(world, envelope(world, scope), world.ann)


def test_claim_session_argument_must_be_the_scope(world):
    pick = {"id": "test-epic-001", "session": BOB}
    with pytest.raises(IdentityRefused, match="does not belong"):
        submit(world, envelope(world, ANN, "task.pick", pick), world.ann)
    nested = {"commands": [{"operation": "task.pick", "arguments": pick}]}
    with pytest.raises(IdentityRefused, match="does not belong"):
        submit(world, envelope(world, ANN, "batch", nested), world.ann)


def test_local_origin_is_unrestricted(world):
    submit(world, envelope(world, "anything-goes"), None)
    submit(world, envelope(world, "host-1-aaaaaaaa", "task.pick", {"id": "test-epic-001", "session": BOB}), None)


def test_writer_rechecks_and_passes_the_requester(world, monkeypatch):
    seen = []
    real = commands.execute

    def spy(connection, request, **kwargs):
        seen.append(kwargs.get("requester"))
        return real(connection, request, **kwargs)

    monkeypatch.setattr(commands, "execute", spy)
    submit(world, envelope(world, ANN), world.ann)
    submit(world, envelope(world, OPAL), world.opal)
    submit(world, envelope(world, "operator@" + "0" * 16), "operator")
    submit(world, envelope(world, "host"), None)
    assert seen == [Requester("ann", False), Requester("opal", True), Requester("operator", True), None]

    state = {"first": True}

    def revoke_on_dequeue(stage):
        if stage == "dequeued" and state["first"]:
            state["first"] = False
            revoke_user(world.root, "ann")

    seen.clear()
    world.owner.checkpoint = revoke_on_dequeue
    with pytest.raises(IdentityRefused, match="revoked"):
        submit(world, envelope(world, ANN), world.ann)
    assert seen == []


def test_member_force_is_refused_end_to_end_in_process(world, monkeypatch):
    monkeypatch.setattr(claims, "CONNECTED", lambda s: False)
    submit(world, envelope(world, BOB, "task.pick", {"id": "test-epic-001", "session": BOB}), world.bob)
    forced = {"id": "test-epic-001", "session": ANN, "force": True}
    with pytest.raises(Conflict, match="Only an operator"):
        submit(world, envelope(world, ANN, "task.pick", forced), world.ann)
    submit(world, envelope(world, OPAL, "task.pick", dict(forced, session=OPAL)), world.opal)


def test_session_refusal_survives_the_ipc_client(world, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    client = Client(world.root, autostart=False)
    with pytest.raises(IdentityRefused) as raised:
        client.call("execute", envelope=envelope(world, BOB), visibility="native", origin={"principal": world.ann})
    error = raised.value
    assert type(error) is IdentityRefused and str(error) == SESSION_REFUSED
    assert error.may_have_committed is False and "retry" not in str(error)
    monkeypatch.setattr(claims, "CONNECTED", lambda s: False)
    client.call("execute", envelope=envelope(world, BOB, "task.pick", {"id": "test-epic-001", "session": BOB}),
                visibility="native", origin={"principal": world.bob})
    with pytest.raises(Conflict, match="Only an operator"):
        client.call("execute", envelope=envelope(world, ANN, "task.pick",
                                                  {"id": "test-epic-001", "session": ANN, "force": True}),
                    visibility="native", origin={"principal": world.ann})
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "ref=" not in caplog.text


# ---- team-server-004 D2: caller-named scopes on non-execute methods ----------------------------------------------

from taskmaster.coordinator import scopes  # noqa: E402
from taskmaster.coordinator import allowlist  # noqa: E402


def _retry_spy(world, monkeypatch):
    seen = []
    monkeypatch.setattr(world.owner.linear, "response",
                        lambda scope, request_id, target_id="": seen.append((scope, request_id)) or {"ok": True})
    return seen


def retry(world, scope, who, request_id="r1"):
    message = {"method": "linear_retry", "caller_scope": scope, "request_id": request_id,
               **({} if who is None else {"origin": {"principal": who}}),
               "identity": dict(world.owner.identity, nonce=world.owner.nonce, build=world.owner.build)}
    return world.owner.dispatch(message)


# User ruling on the security review's m-7: Linear retry is the operator's on a team server. A member's retry is refused
# by the allowlist whatever scope it names, own sessions and channels included, before any scope check or worker.
OPERATOR_ONLY_RETRY = allowlist.ADMIN_REFUSED.format(what=allowlist.WHAT["linear_retry"])


@pytest.mark.parametrize("scope", [ANN, "ann@" + "f" * 16, "ann@viewer", BOB, "bob@viewer", "host-1-aaaaaaaa", "", None,
                                    7, *sorted(scopes.INTERNAL), "sync-" + "0" * 64, "git:ann@" + "a" * 16])
def test_a_members_retry_is_refused_as_operator_only_before_the_worker(world, monkeypatch, scope):
    seen = _retry_spy(world, monkeypatch)
    with pytest.raises(ValueError) as raised:
        retry(world, scope, world.ann)
    assert str(raised.value) == OPERATOR_ONLY_RETRY and type(raised.value) is ValueError
    assert "operator action on a team server" in str(raised.value)
    assert seen == [] and not world.owner.linear.jobs


def test_retry_operator_origins_are_strict(world, monkeypatch):
    from taskmaster.serve import auth
    seen = _retry_spy(world, monkeypatch)
    with pytest.raises(IdentityRefused, match="does not belong"):
        retry(world, ANN, auth.OPERATOR_KEY_ID)
    assert retry(world, "operator@" + "0" * 16, auth.OPERATOR_KEY_ID)
    with pytest.raises(ValueError, match="operator action on a team server"):
        retry(world, ANN, world.opal)          # R3c: an operator-role member key is still a member, not the host
    assert len(seen) == 1


def test_retry_with_a_revoked_key_is_refused(world, monkeypatch):
    seen = _retry_spy(world, monkeypatch)
    revoke_user(world.root, "ann")
    with pytest.raises(ValueError, match="operator action on a team server"):
        retry(world, ANN, world.ann)
    assert seen == []


def test_retry_without_origin_is_unrestricted(world, monkeypatch):
    seen = _retry_spy(world, monkeypatch)
    for scope in (BOB, "explicit-sync", "host-1-aaaaaaaa"):
        retry(world, scope, None)
    assert len(seen) == 3


def test_member_receipt_keeps_the_allowlist_text(world, monkeypatch):
    message = {"method": "receipt", "caller_scope": BOB, "request_id": "x", "origin": {"principal": world.ann},
               "identity": dict(world.owner.identity, nonce=world.owner.nonce, build=world.owner.build)}
    with pytest.raises(ValueError) as raised:
        world.owner.dispatch(message)
    assert str(raised.value) == allowlist.refusal("receipt", message, world.ann) != SESSION_REFUSED
    assert type(raised.value) is ValueError


def test_retry_refusal_survives_the_ipc_client(world, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    seen = _retry_spy(world, monkeypatch)
    client = Client(world.root, autostart=False)
    with pytest.raises(ValueError) as raised:
        client.call("linear_retry", caller_scope=BOB, request_id="r9", origin={"principal": world.ann})
    assert str(raised.value) == OPERATOR_ONLY_RETRY and seen == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
