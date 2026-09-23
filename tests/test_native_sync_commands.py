"""N13 applies prepared observations through atomic, revision-fenced commands."""
import base64
import hashlib
import json

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import contracts, sync
from taskmaster.native.queries import Repository
from native_twins import commit_only, make_twins, native_connection


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_add_task(title="Original", epic="test-epic", phase="dev"),
                      engine_oracle=True)


def entity(connection, ident="test-epic-001"):
    with Repository(connection).snapshot() as snapshot:
        return snapshot.get("task", ident, include_body=True)


def candidate(connection, *, mode="apply", rows=None, raw=b"observed external bytes", file="tasks/test-epic-001.md"):
    return {"file": file, "mode": mode, "rows": rows or [], "reason": "test observation",
            "observed_base64": None if raw is None else base64.b64encode(raw).decode("ascii"),
            "observed_hash": None if raw is None else hashlib.sha1(raw).hexdigest(),
            "expected_manifest": sync.manifest_token(connection, file)}


def replacement(connection, **updates):
    current = entity(connection)
    return {"kind": "task", "id": current["id"], "revision": current["revision"],
            "fields": dict(current["fields"], **updates), "body": current["body"]}


def test_import_commits_revision_event_search_and_job_together(twins):
    with native_connection(twins.native) as connection:
        args = candidate(connection, rows=[replacement(connection, title="Imported narwhal", custom={"unknown": 7})])
        receipt = commit_only(connection, "sync.apply", args)
        current = entity(connection)
        assert current["fields"]["title"] == "Imported narwhal"
        assert current["fields"]["custom"] == {"unknown": 7}
        assert current["revision"] == args["rows"][0]["revision"] + 1
        assert connection.execute("SELECT count(*) FROM document_search WHERE document_search MATCH 'narwhal'").fetchone()[0] == 1
        assert connection.execute("SELECT state FROM projection_jobs ORDER BY job_key DESC LIMIT 1").fetchone()[0] == "pending"
        assert receipt["result"]["state"] == "accepted"
        assert "b2JzZXJ2ZWQgZXh0ZXJuYWwgYnl0ZXM=" in connection.execute(
            "SELECT before FROM domain_events WHERE op='sync.apply' ORDER BY seq DESC LIMIT 1").fetchone()[0]


def test_revision_or_manifest_race_rolls_back_every_import_effect(twins):
    with native_connection(twins.native) as connection:
        args = candidate(connection, rows=[replacement(connection, title="Stale parse")])
        commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": "Concurrent"}})
        before = connection.execute("SELECT count(*) FROM domain_events").fetchone()[0]
        with pytest.raises(contracts.Conflict, match="revision"):
            commit_only(connection, "sync.apply", args)
        assert entity(connection)["fields"]["title"] == "Concurrent"
        assert connection.execute("SELECT count(*) FROM domain_events").fetchone()[0] == before
        args = candidate(connection, rows=[replacement(connection, title="Other")])
        connection.execute("UPDATE projection SET quarantined=1 WHERE file=?", (args["file"],))
        connection.commit()
        with pytest.raises(contracts.Conflict, match="manifest"):
            commit_only(connection, "sync.apply", args)


def test_quarantine_keeps_current_entity_and_captures_exact_bad_bytes(twins):
    with native_connection(twins.native) as connection:
        before = entity(connection)
        args = candidate(connection, mode="quarantine", raw=b"\xff broken yaml")
        receipt = commit_only(connection, "sync.apply", args)
        assert entity(connection) == before
        assert receipt["result"]["state"] == "quarantined"
        assert connection.execute("SELECT quarantined,quarantine_hash FROM projection WHERE file=?",
                                  (args["file"],)).fetchone() == (1, args["observed_hash"])


def test_conflict_keeps_external_bytes_and_can_accept_disjoint_fields(twins):
    with native_connection(twins.native) as connection:
        args = candidate(connection, mode="conflict", rows=[replacement(connection, notes="disjoint import")])
        receipt = commit_only(connection, "sync.apply", args)
        assert receipt["result"]["state"] == "conflict"
        assert entity(connection)["fields"]["notes"] == "disjoint import"
        assert connection.execute("SELECT file_content FROM projection_conflict WHERE file=?",
                                  (args["file"],)).fetchone()[0] == b"observed external bytes"


def test_new_id_reserved_and_claim_not_imported(twins):
    with native_connection(twins.native) as connection:
        row = {"kind": "task", "id": "test-epic-900", "revision": 0, "body": "authored",
               "fields": {"id": "test-epic-900", "title": "New", "epic": "test-epic",
                          "status": "todo", "locked_by": "stale-session"}}
        args = candidate(connection, file="tasks/test-epic-900.md", rows=[row])
        commit_only(connection, "sync.apply", args)
        assert "locked_by" not in entity(connection, "test-epic-900")["fields"]
        assert connection.execute("SELECT 1 FROM id_reservations WHERE kind='task' AND public_id='test-epic-900'").fetchone()
        connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='task' AND public_id='test-epic-900'")
        connection.commit()
        args["expected_manifest"] = sync.manifest_token(connection, args["file"])
        with pytest.raises(contracts.Conflict, match="tombstone"):
            commit_only(connection, "sync.apply", args)


@pytest.mark.parametrize("deleted", [False, True])
def test_imported_numeric_id_advances_counter_even_when_later_tombstoned(twins, deleted):
    with native_connection(twins.native) as connection:
        row = {"kind": "note", "id": "NOTE-900", "revision": 0, "body": "Authored note",
               "fields": {"id": "NOTE-900", "author": "claude"}}
        args = candidate(connection, file="notes/NOTE-900.md", rows=[row])
        commit_only(connection, "sync.apply", args)
        if deleted:
            connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='note' AND public_id='NOTE-900'")
            connection.commit()
        receipt = commit_only(connection, "note.create", {"text": "After imported note"})
        assert receipt["affected"][0]["id"] == "NOTE-901"


@pytest.mark.parametrize("mode", ["apply", "repair", "conflict", "quarantine"])
def test_sync_retains_previous_exporter_flag_before_clearing_or_replacing_it(twins, mode):
    from taskmaster.native import projection
    with native_connection(twins.native) as connection:
        rel = "tasks/test-epic-001.md"
        previous = b"older external version no longer on disk"
        projection.ensure_conflict_table(connection)
        connection.execute("INSERT INTO projection_conflict VALUES(?,?,?,'earlier',?,?)",
                           (rel, "task", "test-epic-001", hashlib.sha1(previous).hexdigest(), previous))
        connection.commit()
        args = candidate(connection, mode=mode, raw=None if mode == "repair" else b"new observed version")
        commit_only(connection, "sync.apply", args)
        event = json.loads(connection.execute("SELECT before FROM domain_events WHERE op='sync.apply' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        assert base64.b64decode(event["flagged_base64"]) == previous


def test_multirow_failure_rolls_back_earlier_creation_and_every_side_effect(twins, monkeypatch):
    from taskmaster.native.commands import Transaction
    with native_connection(twins.native) as connection:
        tables = ("entity_core", "entity_documents", "entity_extensions", "id_counters", "id_reservations",
                  "domain_events", "command_commits", "command_receipts", "projection", "projection_base",
                  "projection_jobs", "document_search", "document_search_keys")
        before = {table: connection.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
        # backlog.yaml is the one multi-row import; it owns epics and phases, never tasks.
        first = {"kind": "epic", "id": "later-epic", "revision": 0, "body": None,
                 "fields": {"id": "later-epic", "name": "Earlier create"}}
        with Repository(connection).snapshot() as snapshot:
            epic = snapshot.get("epic", "test-epic", include_body=True)
        second = {"kind": "epic", "id": "test-epic", "revision": epic["revision"],
                  "fields": dict(epic["fields"], name="Later failing replace"), "body": epic["body"]}
        args = candidate(connection, file="backlog.yaml", rows=[first, second])
        original = Transaction.replace

        def fail_after_creation(transaction, kind, ident, *args, **kwargs):
            assert transaction.connection.execute("SELECT 1 FROM id_reservations WHERE public_id='later-epic'").fetchone()
            original(transaction, kind, ident, *args, **kwargs)
            raise RuntimeError("injected failure after both entity writes")

        monkeypatch.setattr(Transaction, "replace", fail_after_creation)
        with pytest.raises(RuntimeError, match="injected failure"):
            commit_only(connection, "sync.apply", args)
        assert {table: connection.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables} == before


def test_import_command_preserves_current_claim_fields(twins):
    # A claim's liveness is irrelevant to import: ordinary file fields cannot
    # forge a release or renewal. Use the supported native claim operation.
    with native_connection(twins.native) as connection:
        commit_only(connection, "task.pick", {"id": "test-epic-001", "session": "current-owner"})
        before = entity(connection)["fields"]
        args = candidate(connection, rows=[replacement(connection, title="Authored", locked_by="stale-file-owner")])
        commit_only(connection, "sync.apply", args)
        after = entity(connection)["fields"]
        for field in ("locked_by", "claim_expires", "claim_expires_for"):
            assert after.get(field) == before.get(field)


def test_missing_file_repairs_without_deleting_entity(twins):
    with native_connection(twins.native) as connection:
        before = entity(connection)
        args = candidate(connection, mode="repair", raw=None)
        receipt = commit_only(connection, "sync.apply", args)
        assert entity(connection) == before
        assert receipt["result"]["state"] == "repair_pending"
        assert connection.execute("SELECT effect,state FROM projection_jobs ORDER BY job_key DESC LIMIT 1").fetchone() == ("write", "pending")


def test_seed_observed_base_has_no_domain_edit(twins):
    with native_connection(twins.native) as connection:
        before = connection.execute("SELECT count(*) FROM domain_events").fetchone()[0]
        args = candidate(connection, mode="observe")
        # Seeding requires bytes that match the currently trusted manifest.
        connection.execute("UPDATE projection SET content_hash=? WHERE file=?", (args["observed_hash"], args["file"]))
        connection.commit()
        args["expected_manifest"] = sync.manifest_token(connection, args["file"])
        receipt = commit_only(connection, "sync.apply", args)
        assert receipt["result"]["state"] == "observed"
        assert connection.execute("SELECT count(*) FROM domain_events").fetchone()[0] == before
        assert connection.execute("SELECT content FROM projection_base WHERE file=?", (args["file"],)).fetchone()[0] == b"observed external bytes"


@pytest.mark.parametrize("change", [{"file": "local/store.db"}, {"observed_hash": "wrong"},
                                    {"mode": "repair"}, {"mode": "observe", "rows": [{}]}])
def test_malformed_import_rejected_before_any_mutation(twins, change):
    with native_connection(twins.native) as connection:
        args = candidate(connection)
        args.update(change)
        with pytest.raises(ValueError):
            commit_only(connection, "sync.apply", args)
