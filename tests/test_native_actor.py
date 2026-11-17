# User intent: pin the command actor's shape, its place in the contract (allowed, validated, outside the
# fingerprint), and how events and receipts record it, so team attribution can't drift.
from __future__ import annotations

import hashlib
import json

import pytest

from taskmaster.native import actor, contracts
from taskmaster.native.migrate import encode


def envelope(**extra):
    request = {'protocol': 2, 'store_id': 's', 'caller_scope': 'x', 'request_id': 'r1', 'operation': 'task.patch',
               'arguments': {'id': 'test-epic-001', 'set': {'title': 'T'}}, 'expected_revisions': []}
    request.update(extra)
    return request


def test_make_has_exactly_the_three_keys():
    assert actor.make("jdoe", "claude", "3f9a2c4e5b6d7e8f") == {"handle": "jdoe", "agent": "claude", "clone_id": "3f9a2c4e5b6d7e8f"}
    assert actor.make() == {"handle": "", "agent": "", "clone_id": ""}


@pytest.mark.parametrize("name,expected", [("claude-code", "claude"), ("codex-mcp-client", "codex"), ("codex-cli", "codex"),
                                           ("codex", "codex"), ("Claude-Code", ""), ("cursor", ""), ("", ""), (None, "")])
def test_agent_for_client(name, expected):
    assert actor.agent_for_client(name) == expected


def test_normalize_agent():
    for kept in ("claude", "codex", "taskmaster"):
        assert actor.normalize_agent(kept) == kept
    for dropped in ("gpt", "", None, 1):
        assert actor.normalize_agent(dropped) == ""


def test_contract_accepts_an_advisory_or_full_actor():
    contracts.validate(envelope())
    contracts.validate(envelope(actor={"agent": "claude"}))
    contracts.validate(envelope(actor={"handle": "", "agent": "claude", "clone_id": ""}))


@pytest.mark.parametrize("bad", [[], {"role": "x"}, {"agent": 1}, {"handle": "a" * 129}])
def test_contract_rejects_a_malformed_actor(bad):
    with pytest.raises(ValueError, match="invalid actor"):
        contracts.validate(envelope(actor=bad))


def test_actor_is_outside_the_fingerprint():
    prints = {contracts.validate(envelope(**extra))[1] for extra in
              ({}, {"actor": {"agent": "claude"}}, {"actor": {"handle": "mallory", "agent": "codex", "clone_id": "x"}})}
    assert len(prints) == 1


def test_actorless_fingerprint_is_unchanged_from_7_4():
    request = envelope()
    expected = hashlib.sha256(encode({k: v for k, v in contracts.validate(request)[0].items() if k != "request_id"}).encode()).hexdigest()
    assert contracts.validate(request)[1] == expected


# --- events and receipts (Step 3) ---

import sqlite3  # noqa: E402

from taskmaster import backlog_server as bs  # noqa: E402
from taskmaster.native import commands, events, upgrade  # noqa: E402
from native_twins import commit_only, make_twins, native_connection  # noqa: E402
from test_native_schema_upgrade import manifest_version, patch, store_id  # noqa: E402

WHO = actor.make("jdoe", "claude", "c" * 16)


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title='Upgrade task', epic='test-epic',
                                                                         phase='dev'), visibility=None)
    with twins.at(twins.native):
        yield twins.native


def last_event(connection):
    return connection.execute("SELECT kind,id,actor,entity_key FROM domain_events ORDER BY seq DESC LIMIT 1").fetchone()


def test_schema_3_events_have_no_actor_but_the_receipt_does(root):
    with native_connection(root) as connection:
        receipt = commands.execute(connection, patch(root, 'a1') | {'actor': WHO})
        assert receipt['actor'] == WHO
        stored = connection.execute("SELECT outcome_json FROM command_receipts ORDER BY commit_seq DESC LIMIT 1").fetchone()[0]
        assert json.loads(stored)['actor'] == WHO
        assert events.columns(connection) is False


def test_receipt_without_an_actor_has_no_actor_key(root):
    with native_connection(root) as connection:
        assert 'actor' not in commands.execute(connection, patch(root, 'a2'))


def test_schema_5_events_store_actor_and_entity_key(root):
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id='test-epic-001'").fetchone()[0]
        commands.execute(connection, patch(root, 'b1') | {'actor': WHO})
        kind, ident, stored, entity_key = last_event(connection)
        assert (kind, ident, json.loads(stored), entity_key) == ('task', 'test-epic-001', WHO, key)
        created = commit_only(connection, 'task.create', {'epic': 'test-epic', 'phase': 'dev', 'title': 'N'})['affected'][0]['id']
        new_key = connection.execute("SELECT entity_key FROM entity_core WHERE public_id=?", (created,)).fetchone()[0]
        assert connection.execute("SELECT entity_key FROM domain_events WHERE id=? AND op='create'", (created,)).fetchone()[0] == new_key
        commit_only(connection, 'handover.create', {'tldr': 'T', 'thread': 'main'})
        commit_only(connection, 'thread.update', {'name': 'main', 'status': 'parked', 'reason': 'x'})
        assert last_event(connection)[0] == 'backlog' and last_event(connection)[3] is None
        commit_only(connection, 'project.set', {'document': {'schema_version': 1, 'meta': {'name': 'P', 'slug': 'p'}}})
        assert last_event(connection)[0] == 'project' and last_event(connection)[3] is None


def test_schema_5_event_without_an_actor_stores_null(root):
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        commands.execute(connection, patch(root, 'c1'))
        assert last_event(connection)[2] is None


def test_direct_append_for_a_path_id_stores_null_entity_key(root):
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        connection.execute('BEGIN')
        events.append(connection, {'operation': 'x', 'caller_scope': 's'}, None, 'task', 'tasks/x.md', 'resolve', {}, {})
        assert last_event(connection)[3] is None
        connection.rollback()


def test_upgrade_mid_life_on_the_same_connection_is_seen_by_the_next_command(root, monkeypatch):
    calls = []
    real = events.columns
    monkeypatch.setattr(events, 'columns', lambda connection: calls.append(1) or real(connection))
    with native_connection(root) as connection:
        commands.execute(connection, patch(root, 'd1', 'One') | {'actor': WHO})
        assert len(calls) == 1
        upgrade.upgrade(connection)
        commands.execute(connection, patch(root, 'd2', 'Two') | {'actor': WHO})
        _, _, stored, entity_key = last_event(connection)
        assert json.loads(stored) == WHO and entity_key is not None and len(calls) == 2


def test_replay_returns_the_original_actor(root):
    other = actor.make("mallory", "codex", "z" * 16)
    with native_connection(root) as connection:
        upgrade.upgrade(connection)
        first = commands.execute(connection, patch(root, 'e1') | {'actor': WHO})
        top = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
        again = commands.execute(connection, patch(root, 'e1') | {'actor': other})
        assert again['actor'] == WHO and again['commit_seq'] == first['commit_seq']
        assert connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0] == top
