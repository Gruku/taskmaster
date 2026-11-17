# User intent: the actor resolver seam fills handle and clone_id from the coordinator's own knowledge (never the
# client's), without reading git or refusing, and the gate's internal profile write acts as taskmaster.
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import identity_gate, protocol
from taskmaster.coordinator.actor_resolver import LOCAL, ActorResolver, LocalCloneSource
from taskmaster.coordinator.identity_gate import IdentityGate
from taskmaster.native import upgrade
from taskmaster.team_identity import GitIdentity, new_identity, write_identity
from native_twins import commit_only, make_twins


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch,
                       lambda: bs.backlog_add_task(title="Gate task", epic="test-epic", phase="dev"), visibility=None)
    with twins.at(twins.native):
        yield twins.native


@pytest.fixture
def connection(root):
    connection = protocol.connect(root)
    try:
        yield connection
    finally:
        connection.close()


def gate_for(connection, root):
    store = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
    return IdentityGate(root, store_id=store)


def write_for(root, handle="jdoe"):
    identity = new_identity(handle, GitIdentity("John Doe", "jd@corp.io"), clone_label="box", now=datetime.now(timezone.utc))
    write_identity(root / ".taskmaster", identity)
    return identity


def test_local_source_reads_the_identity_file(root, connection, monkeypatch):
    identity = write_for(root)
    monkeypatch.setattr(identity_gate, "read_git_identity", lambda root: pytest.fail("git must not be read"))
    source = LocalCloneSource(gate_for(connection, root))
    assert source.identify(connection, {}, LOCAL) == ("jdoe", identity.clone_id)


def test_local_source_without_identity_is_empty(root, connection):
    assert LocalCloneSource(gate_for(connection, root)).identify(connection, {}, LOCAL) == ("", "")


def test_local_source_with_an_invalid_identity_file_is_empty(root, connection):
    path = write_for(root) and root / ".taskmaster" / "local" / "identity.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("handle: jdoe", "handle: JDoe"), encoding="utf-8")
    assert LocalCloneSource(gate_for(connection, root)).identify(connection, {}, LOCAL) == ("", "")


def test_stamp_overwrites_handle_and_clone_id_and_keeps_a_known_agent(root, connection):
    identity = write_for(root)
    resolver = ActorResolver(LocalCloneSource(gate_for(connection, root)))
    request = {"actor": {"handle": "mallory", "clone_id": "forged", "agent": "codex"}, "operation": "x"}
    before = copy.deepcopy(request)
    stamped = resolver.stamp(connection, request)
    assert stamped["actor"] == {"handle": "jdoe", "agent": "codex", "clone_id": identity.clone_id}
    assert request == before


def test_stamp_drops_an_unknown_agent(root, connection):
    resolver = ActorResolver(LocalCloneSource(gate_for(connection, root)))
    assert resolver.stamp(connection, {"actor": {"agent": "gpt"}})["actor"]["agent"] == ""
    assert resolver.stamp(connection, {})["actor"]["agent"] == ""


def _task_patch(connection):
    store = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
    return {"protocol": 2, "store_id": store, "caller_scope": "gate-tests", "request_id": "p1", "operation": "task.patch",
            "arguments": {"id": "test-epic-001", "set": {"title": "Changed"}}, "expected_revisions": []}


def test_gate_upgrades_a_schema_4_team_store_to_5(root, connection, monkeypatch):
    upgrade.upgrade(connection, to=4)
    identity = write_for(root)
    commit_only(connection, "profile.create", {"handle": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"],
                                                "created": "2026-10-30T12:00:00Z"})
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)
    assert gate_for(connection, root).admit(connection, _task_patch(connection)) is False
    assert connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0] == "5"
    assert identity.handle == "jdoe"


def test_gate_profile_create_carries_the_internal_actor(root, connection, monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)
    monkeypatch.setattr(identity_gate, "read_git_identity", lambda root: GitIdentity("John Doe", "jd@corp.io"))
    assert gate_for(connection, root).admit(connection, _task_patch(connection)) is True
    from taskmaster.team_identity import read_identity
    identity = read_identity(root / ".taskmaster")
    stored = connection.execute("SELECT actor FROM domain_events WHERE kind='profile'").fetchone()[0]
    assert json.loads(stored) == {"handle": "jdoe", "agent": "taskmaster", "clone_id": identity.clone_id}
