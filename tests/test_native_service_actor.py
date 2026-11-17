# User intent: through the real coordinator, every command is stamped with an actor the coordinator derived (never
# the client's claim), the seam can be re-sourced for server mode, and solo stores stay at schema 3 for 7.4.x.
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import identity_gate, sync_jobs
from taskmaster.coordinator.actor_resolver import Origin
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.linear_worker import LinearWorker
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import upgrade
from taskmaster.team_identity import GitIdentity, IdentityRefused, new_identity, read_identity, write_identity
from native_twins import commit_only, make_twins, native_connection
from test_native_service_identity import GitStub, _client, _schema, _value


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_add_task(title="Seed task", epic="test-epic", phase="dev"))


@pytest.fixture
def git_stub(monkeypatch):
    stub = GitStub()
    monkeypatch.setattr(identity_gate, "read_git_identity", stub)
    return stub


@pytest.fixture
def team_on(monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)


def _last_receipt_actor(root):
    row = _value(root, "SELECT outcome_json FROM command_receipts ORDER BY commit_seq DESC LIMIT 1")[0][0]
    return json.loads(row).get("actor")


def _envelope(root, key, title, actor=None):
    client = _client(root)
    return client, _request(client.identity["store_id"], key, title, actor)


def _request(store_id, key, title, actor=None):
    request = {"protocol": 2, "store_id": store_id, "caller_scope": "actor-tests", "request_id": key,
               "operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": title}},
               "expected_revisions": []}
    if actor is not None:
        request["actor"] = actor
    return request


def test_tool_write_is_stamped_in_solo_mode(twins):
    root = twins.native
    with twins.at(root):
        assert not bs.backlog_add_task(title="Solo", epic="test-epic", phase="dev").startswith("Error")
    assert _schema(root) == "3"
    assert _last_receipt_actor(root) == {"handle": "", "agent": "", "clone_id": ""}


def test_tool_write_is_stamped_in_team_mode(twins, git_stub, team_on):
    root = twins.native
    with twins.at(root):
        answer = bs.backlog_add_task(title="Team task", epic="test-epic", phase="dev")
        assert not answer.startswith("Error"), answer
    identity = read_identity(root / ".taskmaster")
    assert _schema(root) == "5"
    expected = {"handle": "jdoe", "agent": "", "clone_id": identity.clone_id}
    row = _value(root, "SELECT d.actor,d.entity_key,c.entity_key FROM domain_events d JOIN entity_core c "
                       "ON c.kind=d.kind AND c.public_id=d.id WHERE d.kind='task' AND d.op='create' ORDER BY d.seq DESC LIMIT 1")[0]
    assert json.loads(row[0]) == expected and row[1] == row[2]
    assert _last_receipt_actor(root) == expected


def test_client_sent_handle_is_overwritten(twins, git_stub, team_on):
    root = twins.native
    forged = {"handle": "mallory", "clone_id": "forged-clone-01", "agent": "codex"}
    with twins.at(root):
        assert not bs.backlog_add_task(title="Boot", epic="test-epic", phase="dev").startswith("Error")
        client, request = _envelope(root, "forge-1", "Forged", forged)
        receipt = client.execute(request)["receipt"]
    identity = read_identity(root / ".taskmaster")
    assert receipt["actor"] == {"handle": "jdoe", "agent": "codex", "clone_id": identity.clone_id}
    event = _value(root, "SELECT actor FROM domain_events ORDER BY seq DESC LIMIT 1")[0][0]
    assert json.loads(event) == receipt["actor"]


def test_client_sent_handle_is_overwritten_in_solo_mode(twins):
    root = twins.native
    with twins.at(root):
        client, request = _envelope(root, "forge-2", "Forged", {"handle": "mallory", "clone_id": "forged-clone-01", "agent": "codex"})
        receipt = client.execute(request)["receipt"]
    assert receipt["actor"] == {"handle": "", "agent": "codex", "clone_id": ""}


def test_retry_after_a_handle_change_matches_its_receipt(twins, git_stub, team_on):
    root = twins.native
    with twins.at(root):
        assert not bs.backlog_add_task(title="Boot", epic="test-epic", phase="dev").startswith("Error")
        client, request = _envelope(root, "r1", "Retried")
        first = client.execute(request)
        assert first["receipt"]["actor"]["handle"] == "jdoe"
        seq = _value(root, "SELECT MAX(seq) FROM domain_events")[0][0]
        identity = read_identity(root / ".taskmaster")
        write_identity(root / ".taskmaster", dataclasses.replace(identity, handle="johnd"))
        path = root / ".taskmaster" / "local" / "identity.yaml"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        assert client.execute(request) == first
        assert _value(root, "SELECT MAX(seq) FROM domain_events")[0][0] == seq
        _, fresh = _envelope(root, "r2", "Fresh")
        assert client.execute(fresh)["receipt"]["actor"]["handle"] == "johnd"
    assert "johnd" in [row[0] for row in _value(root, "SELECT public_id FROM entity_core WHERE kind='profile'")]


def test_queued_retry_ignores_the_actor(twins):
    root = twins.native
    with Coordinator(root) as owner:
        request = _request(owner.identity["store_id"], "q1", "Queued")
        owner.pause_writer()
        try:
            first = owner.submit(dict(request, actor={"agent": "claude"}))
            second = owner.submit(dict(request, actor={"agent": "codex"}))
            assert first is second
        finally:
            owner.resume_writer()
        assert first.result(timeout=30)["actor"]["agent"] == "claude"


def _write_identity(root, handle):
    write_identity(root / ".taskmaster", new_identity(handle, GitIdentity("John Doe", "jd@corp.io"),
                                                      clone_label="box", now=datetime.now(timezone.utc)))


def test_placeholder_handle_refuses_and_keeps_schema_3(twins, git_stub, team_on):
    root = twins.native
    _write_identity(root, "admin")
    with twins.at(root):
        answer = bs.backlog_add_task(title="Refused", epic="test-epic", phase="dev")
        assert answer.startswith("Error"), answer
        client, request = _envelope(root, "ph", "Nope")
        with pytest.raises(IdentityRefused):
            client.execute(request)
    assert _schema(root) == "3"


def test_deleted_profile_refuses_and_keeps_schema_4(twins, git_stub, team_on):
    root = twins.native
    _write_identity(root, "jdoe")
    with native_connection(root) as connection:
        upgrade.upgrade(connection, to=4)
        commit_only(connection, "profile.create", {"handle": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"],
                                                    "created": "2026-10-30T12:00:00Z"})
        connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='profile' AND public_id='jdoe'")
    with twins.at(root):
        client, request = _envelope(root, "dp", "Nope")
        with pytest.raises(IdentityRefused):
            client.execute(request)
    assert _schema(root) == "4"


def test_internal_sync_writes_carry_taskmaster(twins, git_stub, team_on):
    root = twins.native
    with twins.at(root):
        assert not bs.backlog_add_task(title="Boot", epic="test-epic", phase="dev").startswith("Error")
        assert bs.backlog_sync().startswith("Sync complete")
        before = _value(root, "SELECT MAX(seq) FROM domain_events")[0][0]
        path = root / ".taskmaster" / "tasks" / "test-epic-001.md"
        path.write_bytes(path.read_bytes().replace(b"Seed task", b"Seed edited"))
        assert bs.backlog_sync().startswith("Sync complete")
    rows = _value(root, "SELECT json_extract(actor,'$.agent'),json_extract(actor,'$.handle') FROM domain_events "
                        "WHERE seq>? AND tool IN ('sync.apply','sync.job')", (before,))
    assert _value(root, "SELECT 1 FROM domain_events WHERE seq>? AND tool='sync.apply'", (before,))
    assert rows and all(r == ("taskmaster", "jdoe") for r in rows)


def test_linear_envelopes_carry_taskmaster():
    worker = LinearWorker(SimpleNamespace(identity={"store_id": "s"}))
    assert worker._envelope("scope", "id", "linear.settle", {})["actor"] == {"agent": "taskmaster"}


def test_sync_job_records_carry_taskmaster():
    sent = []

    class Owner:
        identity = {"store_id": "s"}

        def submit(self, envelope):
            sent.append(envelope)
            return SimpleNamespace(result=lambda timeout=None: None)
    sync_jobs._write(Owner(), SimpleNamespace(id="20261031T120000Z-0123abcd"), {"state": "running"})
    assert sent[0]["actor"] == {"agent": "taskmaster"}


def test_an_identity_source_replaces_the_git_source_without_touching_callers(twins):
    root = twins.native

    class TokenSource:
        def identify(self, connection, request, origin):
            return {"tok-ann": ("ann", "client-7f3a")}.get(origin.principal, ("", ""))
    owner = Coordinator(root, actor_source=TokenSource()).start()
    try:
        request = _request(owner.identity["store_id"], "t1", "Token")
        receipt = owner.submit(request, origin=Origin(principal="tok-ann")).result(timeout=30)
        assert receipt["actor"] == {"handle": "ann", "agent": "", "clone_id": "client-7f3a"}
        local = _request(owner.identity["store_id"], "t2", "Local")
        assert Client(root, autostart=False).execute(local)["receipt"]["actor"] == {"handle": "", "agent": "", "clone_id": ""}
    finally:
        owner.close()


# --- compatibility with 7.4.x for solo stores (Step 6) ---

from contextlib import closing as _closing  # noqa: E402
import sqlite3  # noqa: E402

from taskmaster.native import commands, db  # noqa: E402
from native_twins import native_database  # noqa: E402
from test_native_schema_upgrade import (  # noqa: E402
    _OLD_BUILD_PROBE, _as_older_build, _released_build, _settled_digest, patch as _upgrade_patch)
from taskmaster.bounded_run import run_bounded  # noqa: E402
import sys  # noqa: E402


def _solo_writes():
    assert not bs.backlog_add_task(title="Solo one", epic="test-epic", phase="dev").startswith("Error")
    assert not bs.backlog_update_task("test-epic-001", "title", "Renamed").startswith("Error")
    assert not bs.backlog_note(action="create", text="a note").startswith("Error")
    assert bs.backlog_sync().startswith("Sync complete")
    assert not bs.backlog_batch_update(operations="update test-epic-001 title Batched").startswith("Error")


def test_solo_store_stays_at_schema_3(twins, monkeypatch):
    from tests.native_coordinator_helpers import close_owned
    root = twins.native
    monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")
    with twins.at(root):
        _solo_writes()
        close_owned()
        assert _schema(root) == "3"
        assert not bs.backlog_add_task(title="After restart", epic="test-epic", phase="dev").startswith("Error")
    assert _schema(root) == "3"
    with _closing(sqlite3.connect(native_database(root))) as connection:
        assert not {"actor", "entity_key"} & {r[1] for r in connection.execute("PRAGMA table_info(domain_events)")}
        outcomes = [json.loads(r[0]) for r in connection.execute("SELECT outcome_json FROM command_receipts")]
    assert not (root / ".taskmaster" / "local" / "identity.yaml").exists()
    assert all(o["actor"]["handle"] == "" for o in outcomes if "actor" in o) and any("actor" in o for o in outcomes)


def test_7_4_shaped_build_opens_a_solo_store_this_build_wrote(twins, monkeypatch):
    root = twins.native
    monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")
    with twins.at(root):
        client, request = _envelope(root, "compat-1", "Compat", {"agent": "claude"})
        first = client.execute(request)["receipt"]
    with native_connection(root) as connection:
        top = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
        with monkeypatch.context() as older:
            _as_older_build(older)
            connection.execute("BEGIN")
            db.assert_native(connection)
            connection.rollback()
            with db.verified_snapshot(connection):
                pass
            assert commands.execute(connection, _upgrade_patch(root, "older-1", "Older"))["affected"]
            actorless = {k: v for k, v in request.items() if k != "actor"}
            again = commands.execute(connection, actorless)
            assert again["commit_seq"] == first["commit_seq"]
            assert connection.execute("SELECT COUNT(*) FROM domain_events WHERE seq>?", (top,)).fetchone()[0] == 1


_REPLAY_PROBE = _OLD_BUILD_PROBE.replace(
    "    print(json.dumps(sorted([item[\"id\"], item[\"fields\"].get(\"title\")] for item in items)))",
    "    tasks = sorted([item[\"id\"], item[\"fields\"].get(\"title\")] for item in items)\n"
    "    from taskmaster.native import commands\n"
    "    receipt = commands.execute(connection, json.loads(sys.argv[3]))\n"
    "    print(json.dumps({\"tasks\": tasks, \"commit_seq\": receipt[\"commit_seq\"], \"actor\": receipt.get(\"actor\")}))")


@pytest.mark.slow
def test_released_7_4_0_build_reads_and_replays_a_solo_store_this_build_wrote(twins, tmp_path, monkeypatch):
    from tests.native_coordinator_helpers import close_owned
    root = twins.native
    old = _released_build(tmp_path)
    with twins.at(root):
        client, request = _envelope(root, "compat-2", "Patched by this build", {"agent": "claude"})
        first = client.execute(request)["receipt"]
    close_owned()
    database = native_database(root)
    actorless = {k: v for k, v in request.items() if k != "actor"}
    with _closing(sqlite3.connect(database)) as connection:
        top = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    before = _settled_digest(database)
    completed = run_bounded([sys.executable, "-I", "-c", _REPLAY_PROBE, str(old), str(database), json.dumps(actorless)], timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    answer = json.loads(completed.stdout.decode("utf-8").strip())
    assert ["test-epic-001", "Patched by this build"] in answer["tasks"]
    assert answer["commit_seq"] == first["commit_seq"]
    assert answer["actor"] == {"handle": "", "agent": "claude", "clone_id": ""}
    with _closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0] == top
    assert _settled_digest(database) == before
