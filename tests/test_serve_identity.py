# User intent: the coordinator, not the caller, turns the authenticated key into the actor and the profile, and never consults the host's git identity in server mode.
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import identity_gate
from taskmaster.coordinator.actor_resolver import Origin
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.identity_gate import IdentityGate
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve.daemon import ServeCoordinator
from taskmaster.serve.principals import PrincipalRegistry, add_user, principals_path, revoke_user, rotate_user
from taskmaster.team_identity import IdentityRefused
from native_twins import native_connection
from test_native_service_build import request, root  # noqa: F401
from test_native_service_identity import _value


def receipts(root_):
    return _value(root_, "SELECT COUNT(*) FROM command_receipts")[0][0]


@pytest.fixture
def world(root):  # noqa: F811
    ann_key, ann, _ = add_user(root, "ann", name="Ann Lee", emails=["ann@example.com"])
    bob_key, bob, _ = add_user(root, "bob")
    registry = PrincipalRegistry(principals_path(root))
    owner = ServeCoordinator(root, registry=registry).start()
    try:
        yield SimpleNamespace(root=root, owner=owner, registry=registry, ann=ann.key_id, bob=bob.key_id,
                              ann_key=ann_key, ann_record=ann)
    finally:
        owner.close()


# Served commands run in their key's own session (team-server-003 D5): the scope names the handle.
ANN_SCOPE, BOB_SCOPE, OPERATOR_SCOPE = "ann@" + "0" * 16, "bob@" + "0" * 16, "operator@" + "0" * 16


def scope_of(world, who):
    return {world.bob: BOB_SCOPE, "operator": OPERATOR_SCOPE}.get(who, ANN_SCOPE)


def envelope(world, key, title="T", actor=None, scope=ANN_SCOPE):
    value = {"protocol": 2, "store_id": world.owner.identity["store_id"], "caller_scope": scope,
             "request_id": key, "operation": "task.patch",
             "arguments": {"id": "test-epic-001", "set": {"title": title}}, "expected_revisions": []}
    if actor is not None:
        value["actor"] = actor
    return value


def test_origin_names_the_actor(world):
    first = world.owner.submit(envelope(world, "o1"), origin=Origin(principal=world.ann)).result(30)
    assert first["actor"] == {"handle": "ann", "agent": "", "clone_id": world.ann}
    second = world.owner.submit(envelope(world, "o2", scope=BOB_SCOPE), origin=Origin(principal=world.bob)).result(30)
    assert second["actor"] == {"handle": "bob", "agent": "", "clone_id": world.bob}


def test_a_client_sent_handle_is_ignored(world):
    forged = {"handle": "mallory", "agent": "claude", "clone_id": "evil-clone-1"}
    receipt = world.owner.submit(envelope(world, "f1", actor=forged), origin=Origin(principal=world.ann)).result(30)
    assert receipt["actor"] == {"handle": "ann", "agent": "claude", "clone_id": world.ann}
    over_ipc = Client(world.root, autostart=False).call(
        "execute", envelope=envelope(world, "f2", actor=forged), visibility="native",
        origin={"principal": world.ann})
    assert over_ipc["receipt"]["actor"] == {"handle": "ann", "agent": "claude", "clone_id": world.ann}


def test_local_origin_is_the_operator(world):
    receipt = Client(world.root, autostart=False).execute(envelope(world, "l1"))["receipt"]
    assert receipt["actor"] == {"handle": "operator", "agent": "", "clone_id": "operator"}


@pytest.mark.parametrize("bad", [{"principal": "../x"}, {"principal": 5}, {"principal": "ANNANNANNANNANNA", "x": 1},
                                 "ann", {"principal": "A" * 16}, {"handle": "x"}])
def test_invalid_origin_is_refused(world, bad):
    client = Client(world.root, autostart=False)
    with pytest.raises(ValueError, match="invalid origin"):
        client.call("execute", envelope=envelope(world, "bad1"), visibility="native", origin=bad)
    assert client.receipt(ANN_SCOPE, "bad1")["state"] == "unknown"


def test_session_coordinator_ignores_an_origin(root):  # noqa: F811
    class TokenSource:
        def identify(self, connection, request_, origin):
            return {"tok-ann": ("ann", "client-7f3a")}.get(origin.principal, ("", ""))
    owner = Coordinator(root, actor_source=TokenSource()).start()
    try:
        value = {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": "serve-tests",
                 "request_id": "s1", "operation": "task.patch",
                 "arguments": {"id": "test-epic-001", "set": {"title": "S"}}, "expected_revisions": []}
        answer = Client(root, autostart=False).call("execute", envelope=value, visibility="native",
                                                    origin={"principal": "tok-ann"})
        assert answer["receipt"]["actor"] == {"handle": "", "agent": "", "clone_id": ""}
    finally:
        owner.close()


def test_a_revoked_key_is_refused_at_the_writer(world):
    state = {"first": True}

    def revoke_on_dequeue(stage):
        if stage == "dequeued" and state["first"]:
            state["first"] = False
            revoke_user(world.root, "ann")
    world.owner.checkpoint = revoke_on_dequeue
    before = receipts(world.root)
    with pytest.raises(IdentityRefused, match="revoked"):
        world.owner.submit(envelope(world, "r1"), origin=Origin(principal=world.ann)).result(30)
    assert Client(world.root, autostart=False).receipt(ANN_SCOPE, "r1")["state"] == "unknown"
    assert receipts(world.root) == before


def test_an_unknown_key_id_is_refused_at_the_writer(world):
    with pytest.raises(IdentityRefused):
        world.owner.submit(envelope(world, "u1"), origin=Origin(principal="f" * 16)).result(30)


def test_client_sends_the_origin_only_with_a_provider(world, monkeypatch):
    sent = []
    real = Client._send

    def spy(self, record, method, **arguments):
        if method == "execute":
            sent.append(arguments)
        return real(self, record, method, **arguments)
    monkeypatch.setattr(Client, "_send", spy)
    client = Client(world.root, autostart=False)
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: world.ann)
    client.execute(envelope(world, "c1"))
    assert sent[-1]["origin"] == {"principal": world.ann}
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", None)
    client.execute(envelope(world, "c2"))
    assert set(sent[-1]) - {"wait"} == {"envelope", "visibility"} and sent[-1]["visibility"] == "native"

    def refuse():
        raise PermissionError("authenticated principal missing")
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", refuse)
    count = len(sent)
    with pytest.raises(PermissionError):
        client.execute(envelope(world, "c3"))
    assert len(sent) == count


def test_admit_accepts_an_origin(root):  # noqa: F811
    gate = IdentityGate(root, store_id=Client(root, autostart=False).identity["store_id"])
    value = {"operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": "x"}},
             "request_id": "a1", "caller_scope": "serve-tests"}
    with native_connection(root) as connection:
        assert gate.admit(connection, value) is False
        assert gate.admit(connection, value, Origin(principal="x")) is False


# --- the server-mode identity gate (Step 5) ---

@pytest.fixture
def team_on(monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)


@pytest.fixture
def no_host_identity(world, team_on, monkeypatch):
    """Server mode never reads host git, the machine name, the identity file or users/*/profile.yaml."""
    def forbidden(*args, **kwargs):
        raise AssertionError("server mode consulted the host's identity")
    for name in ("read_git_identity", "write_identity", "disk_profiles"):
        monkeypatch.setattr(identity_gate, name, forbidden)
    monkeypatch.setattr(identity_gate.socket, "gethostname", forbidden)


def _schema_value(root_):
    return _value(root_, "SELECT value FROM native_manifest WHERE key='schema_version'")[0][0]


def _profile_creates(root_):
    return _value(root_, "SELECT request_id, caller_scope, outcome_json FROM command_receipts "
                         "WHERE request_id LIKE 'profile-%' ORDER BY commit_seq")


def _profile_ids(root_):
    return [r[0] for r in _value(root_, "SELECT public_id FROM entity_core WHERE kind='profile' ORDER BY public_id")]


def _write(world, key, who, **kwargs):
    kwargs.setdefault("scope", scope_of(world, who))
    return world.owner.submit(envelope(world, key, **kwargs), origin=Origin(principal=who)).result(30)


def test_first_write_creates_the_keys_profile(world, no_host_identity):
    from taskmaster.native.queries import Repository
    from taskmaster.team_identity import IDENTITY_REL
    assert _write(world, "p1", world.ann)["actor"]["handle"] == "ann"
    with native_connection(world.root) as connection, Repository(connection).snapshot() as snapshot:
        fields = snapshot.get("profile", "ann")["fields"]
    assert fields["name"] == "Ann Lee" and fields["emails"] == ["ann@example.com"]
    assert fields["created"] == world.ann_record.created
    [(request_id, scope, outcome)] = _profile_creates(world.root)
    assert (request_id, scope) == (f"profile-{world.ann}-ann", "coordinator-identity")
    assert json.loads(outcome)["actor"] == {"handle": "ann", "agent": "taskmaster", "clone_id": world.ann}
    assert _schema_value(world.root) == "5"
    assert not (world.root / ".taskmaster" / IDENTITY_REL).exists()


def test_existing_profile_is_reused(world, no_host_identity):
    _write(world, "e1", world.ann)
    new_key, new, _ = rotate_user(world.root, "ann")
    receipt = _write(world, "e2", new.key_id)
    assert receipt["actor"] == {"handle": "ann", "agent": "", "clone_id": new.key_id}
    assert len(_profile_creates(world.root)) == 1


def test_deleted_profile_refuses(world, no_host_identity):
    _write(world, "d1", world.ann)
    with native_connection(world.root) as connection:
        connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='profile' AND public_id='ann'")
    with pytest.raises(IdentityRefused, match="was deleted"):
        _write(world, "d2", world.ann)


def test_operator_and_local_writes_need_a_person_in_team_mode(world, no_host_identity):
    before = receipts(world.root)
    with pytest.raises(IdentityRefused, match="add-user"):
        _write(world, "n1", "operator")
    with pytest.raises(IdentityRefused, match="add-user"):
        Client(world.root, autostart=False).execute(envelope(world, "n2"))
    assert receipts(world.root) == before


def test_exempt_operations_pass_for_the_operator(world, no_host_identity):
    from taskmaster.coordinator.sync_jobs import RECORD_SCOPE
    record = {"state": "running", "files": [], "started_at": "2026-11-01T00:00:00Z"}
    internal = {"protocol": 2, "store_id": world.owner.identity["store_id"], "caller_scope": RECORD_SCOPE,
                "request_id": "job-1:running", "operation": "sync.job", "arguments": {"id": "20261101T000000Z-0123abcd", "record": record},
                "expected_revisions": [], "actor": {"agent": "taskmaster"}}
    receipt = world.owner.submit(internal).result(30)
    assert receipt["actor"] == {"handle": "operator", "agent": "taskmaster", "clone_id": "operator"}


def test_solo_mode_creates_no_profile(world):
    receipt = _write(world, "s1", world.ann)
    assert receipt["actor"] == {"handle": "ann", "agent": "", "clone_id": world.ann}
    assert _profile_ids(world.root) == [] and _schema_value(world.root) == "3"


def test_revoked_key_refuses_before_any_profile_is_made(world, no_host_identity):
    revoke_user(world.root, "ann")
    with pytest.raises(IdentityRefused, match="revoked"):
        _write(world, "v1", world.ann)
    assert _profile_ids(world.root) == [] and _schema_value(world.root) == "3"


def test_replay_makes_no_second_profile(world, no_host_identity):
    first = _write(world, "rp1", world.ann)
    again = _write(world, "rp1", world.ann)
    assert again["commit_seq"] == first["commit_seq"]
    assert len(_profile_creates(world.root)) == 1


def test_a_revoked_keys_replay_is_refused_without_side_effects(world, no_host_identity):
    first = _write(world, "rv1", world.ann)
    events, count = _value(world.root, "SELECT COUNT(*) FROM domain_events")[0][0], receipts(world.root)
    revoke_user(world.root, "ann")
    with pytest.raises(IdentityRefused, match="revoked"):
        _write(world, "rv1", world.ann)
    assert _value(world.root, "SELECT COUNT(*) FROM domain_events")[0][0] == events
    assert receipts(world.root) == count
    assert Client(world.root, autostart=False).receipt(ANN_SCOPE, "rv1")["receipt"]["commit_seq"] == first["commit_seq"]
