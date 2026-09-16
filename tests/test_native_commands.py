"""Exactly-once retry receipts and transactional command outcomes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import sqlite3

import pytest

from taskmaster.admission import UnsupportedStoreError, assert_compatible
from taskmaster.native.commands import execute, Conflict, CancelledBeforeExecution
from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from test_native_migration import legacy  # noqa: F401


@pytest.fixture
def native(legacy):
    # Test-only activation. No runtime entry point activates a real project.
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        connection.execute("INSERT INTO meta VALUES('minimum_client_protocol','2')")
        connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1')")
        connection.execute("INSERT INTO id_reservations VALUES('note','NOTE-999')")
        connection.execute("INSERT INTO id_counters VALUES('note','NOTE-',999)")
        connection.commit()
    return legacy


def envelope(op="task.patch", args=None, key="request-1", expected=None):
    return {"protocol": 2, "store_id": "fixture-store", "caller_scope": "tests", "request_id": key,
            "operation": op, "arguments": args if args is not None else {"id": "same", "set": {"next_step": "Continue"}},
            "expected_revisions": expected or []}


def test_receipt_replay_precedes_stale_revision_check_and_returns_original_outcome(native):
    request = envelope(expected=[{"kind": "task", "id": "same", "revision": 4}])
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        first = execute(connection, request)
        assert first["affected"][0]["revision"] == 5
        execute(connection, envelope(args={"id": "same", "set": {"next_step": "Peer"}}, key="peer"))
        assert execute(connection, request) == first
        with Repository(connection).snapshot() as query:
            assert query.get("task", "same")["fields"]["next_step"] == "Peer"
        assert connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 2


def test_request_key_payload_mismatch_and_stale_edit_cannot_overwrite(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        execute(connection, envelope())
        with pytest.raises(Conflict, match="payload"):
            execute(connection, envelope(args={"id": "same", "set": {"next_step": "different"}}))
        with pytest.raises(Conflict, match="revision"):
            execute(connection, envelope(key="stale", expected=[{"kind": "task", "id": "same", "revision": 4}]))


def test_noop_has_no_new_revision_event_or_projection_job(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        result = execute(connection, envelope(args={"id": "same", "set": {"title": None}}))
        assert result["commit_seq"] == 100
        assert result["affected"] == []
        assert connection.execute("SELECT revision FROM entity_core WHERE kind='task'").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM command_commits").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0] == 0


def test_note_creation_retries_allocate_once_above_reserved_high_water(native):
    request = envelope("note.create", {"text": "Exact note", "author": "claude", "pinned": True})
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        first = execute(connection, request)
        assert execute(connection, request) == first
        assert first["affected"][0]["id"] == "NOTE-1000"
        assert connection.execute("SELECT COUNT(*) FROM entity_core WHERE public_id='NOTE-1000'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0] == 1
        with Repository(connection).snapshot() as query:
            assert query.get("note", "NOTE-1000", include_body=True)["body"] == "Exact note"


def test_composite_failure_rolls_back_earlier_operations_and_ids(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        request = envelope("batch", {"commands": [
            {"operation": "note.create", "arguments": {"text": "discard me", "author": "claude"}},
            {"operation": "task.patch", "arguments": {"id": "absent", "set": {"title": "bad"}}},
        ]})
        with pytest.raises(KeyError):
            execute(connection, request)
        assert connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM entity_core WHERE public_id='NOTE-1000'").fetchone()[0] == 0
        result = execute(connection, envelope("note.create", {"text": "valid", "author": "claude"}, key="valid"))
        assert result["affected"][0]["id"] == "NOTE-1000"


@pytest.mark.parametrize("stage", ["admitted", "mutated", "receipt", "before_commit"])
def test_injected_failure_has_no_partial_commit(native, stage):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        before = list(connection.iterdump())
        def fail(current):
            if current == stage:
                raise RuntimeError("injected")
        with pytest.raises(RuntimeError, match="injected"):
            execute(connection, envelope(), checkpoint=fail)
        assert list(connection.iterdump()) == before


def test_duplicate_concurrent_requests_share_one_durable_receipt(native):
    request = envelope("note.create", {"text": "create once", "author": "claude"})
    def send(_):
        with closing(sqlite3.connect(native, isolation_level=None, timeout=10)) as connection:
            return execute(connection, copy.deepcopy(request))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(12)))
    assert all(result == results[0] for result in results)
    with closing(sqlite3.connect(native)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM domain_events WHERE seq>100").fetchone()[0] == 1


def test_bridge_refuses_native_authority_and_staging_refuses_commands(native, legacy):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with pytest.raises(UnsupportedStoreError):
            assert_compatible(connection)
        connection.execute("UPDATE native_manifest SET value='legacy' WHERE key='authority'")
        with pytest.raises(UnsupportedStoreError):
            execute(connection, envelope())


def test_cancellation_before_execution_has_known_no_effect(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with pytest.raises(CancelledBeforeExecution):
            execute(connection, envelope(), cancelled=lambda: True)
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 0


def test_all_or_nothing_batch_has_one_commit_group_and_final_receipt(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        result = execute(connection, envelope("batch", {"commands": [
            {"operation": "task.patch", "arguments": {"id": "same", "set": {"next_step": "one"}}},
            {"operation": "note.create", "arguments": {"text": "two", "author": "claude"}},
        ]}))
        assert len(result["affected"]) == 2
        assert result["commit_seq"] == 102
        assert connection.execute("SELECT COUNT(DISTINCT commit_key) FROM domain_events WHERE seq>100").fetchone()[0] == 1
        assert connection.execute("SELECT first_seq,final_seq FROM command_commits").fetchone() == (101, 102)
        assert all(json.loads(r[0])["revision"] >= 1 for r in connection.execute("SELECT input_json FROM projection_jobs"))


def test_peer_compare_and_swap_race_has_one_winner(native):
    def send(index):
        with closing(sqlite3.connect(native, isolation_level=None, timeout=10)) as connection:
            try:
                return execute(connection, envelope(args={"id": "same", "set": {"next_step": str(index)}}, key=f"peer-{index}",
                                                     expected=[{"kind": "task", "id": "same", "revision": 4}]))
            except Conflict:
                return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(8)))
    assert len([r for r in results if r is not None]) == 1


def test_lost_response_after_process_commit_is_recoverable_by_retry(native):
    import os
    from pathlib import Path
    import subprocess
    import sys
    request = envelope("note.create", {"text": "survives lost response", "author": "claude"})
    script = """
import json,os,sqlite3,sys
from taskmaster.native.commands import execute
connection=sqlite3.connect(sys.argv[1],isolation_level=None)
execute(connection,json.loads(sys.argv[2]))
os._exit(37)
"""
    result = subprocess.run([sys.executable, "-c", script, str(native), json.dumps(request)],
                            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])), timeout=30)
    assert result.returncode == 37
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        receipt = execute(connection, request)
        assert receipt["affected"][0]["id"] == "NOTE-1000"
        assert connection.execute("SELECT COUNT(*) FROM domain_events WHERE seq>100").fetchone()[0] == 1


def test_missing_id_high_water_fails_closed(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("DELETE FROM id_counters")
        with pytest.raises(RuntimeError, match="high-water"):
            execute(connection, envelope("note.create", {"text": "must not allocate"}))


def test_metadata_edit_has_no_graph_or_fts_work_when_inputs_are_unchanged(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        statements = []
        connection.set_trace_callback(statements.append)
        receipt = execute(connection, envelope())
        assert all(value == 0 for value in receipt["work"].values())
        assert not any("entity_paths" in sql or "FROM related" in sql or "INTO related" in sql or "document_search " in sql for sql in statements)


def test_search_replacement_keeps_stable_document_identity(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        key = connection.execute("SELECT document_key FROM document_search_keys k JOIN entity_core c USING(entity_key) WHERE c.kind='task'").fetchone()[0]
        for index, title in enumerate(["Oranges", "Apples", "Oranges"]):
            receipt = execute(connection, envelope(args={"id": "same", "set": {"title": title}}, key=f"title-{index}"))
            assert receipt["work"]["fts_documents"] == 1
            assert connection.execute("SELECT rowid FROM document_search WHERE kind='task' AND id='same'").fetchone()[0] == key
            with Repository(connection).snapshot() as query:
                assert query.search(title)["items"][0]["id"] == "same"
        with Repository(connection).snapshot() as query:
            assert not query.search("apples")["items"]


def test_native_admission_does_not_read_unrelated_meta_payloads(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO meta VALUES('unrelated_history',?)", ("large history" * 10000,))
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            query.get("task", "same", fields=["id"])
        assert all("WHERE" in sql for sql in statements if "FROM meta" in sql)
