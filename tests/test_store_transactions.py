"""Transactional semantics for the SQLite-authoritative store.

These tests exercise only the public store surface.  Projection import is used to
seed realistic v4 rows; assertions against SQLite are read-only verification of
the authority and change log promised by the store contract.
"""
from __future__ import annotations

import json
import subprocess
import sqlite3
import sys
import textwrap
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _write_v4_project(tmp_path: Path, *, title_prefix: str = "Task") -> Path:
    tm_dir = tmp_path / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "transaction-tests", "schema_version": 4},
                "epics": [{"id": "e", "name": "Epic E"}],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for number in range(1, 4):
        task = {
            "id": f"e-{number:03d}",
            "title": f"{title_prefix} {number}",
            "epic": "e",
            "order": float(number),
            "status": "todo",
            "priority": "medium",
        }
        frontmatter, body = v3.task_v4_to_file(task)
        v3.write_task_file(
            tm_dir / "tasks" / f"{task['id']}.md", frontmatter, body
        )
    return backlog_path


@pytest.fixture()
def transaction_store(tmp_path: Path) -> Iterator[tuple[Any, Path]]:
    store.reset_for_tests()
    backlog_path = _write_v4_project(tmp_path)
    opened = store.open_store(backlog_path, session="transaction-test-session")
    try:
        yield opened, backlog_path
    finally:
        store.reset_for_tests()


def _connect(backlog_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(store.db_path(backlog_path))
    connection.row_factory = sqlite3.Row
    return connection


def _entity(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM entities WHERE kind = 'task' AND id = ?", (task_id,)
    ).fetchone()
    assert row is not None
    return row


def _task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for epic in data["epics"]:
        for candidate in epic.get("tasks", []):
            if candidate["id"] == task_id:
                return candidate
    raise AssertionError(f"task {task_id!r} is absent from the public backlog shape")


def _max_seq(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
    )


def _json(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def test_put_records_exact_diff_revision_sequence_and_committed_row(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    with _connect(backlog_path) as connection:
        before_row = _entity(connection, "e-001")
        before_rev = before_row["rev"]
        before_seq = _max_seq(connection)

    with opened.transaction(tool="backlog_update_task") as tx:
        task = tx.get("task", "e-001")
        task["status"] = "in-progress"
        task["priority"] = "high"
        tx.put("task", "e-001", task)
        assert ("task", "e-001") not in tx.committed

    with _connect(backlog_path) as connection:
        row = _entity(connection, "e-001")
        changes = connection.execute(
            "SELECT * FROM changes WHERE seq > ? ORDER BY seq", (before_seq,)
        ).fetchall()

    assert len(changes) == 1
    change = changes[0]
    assert change["kind"] == "task"
    assert change["id"] == "e-001"
    assert change["op"] == "update"
    assert change["session"] == "transaction-test-session"
    assert change["tool"] == "backlog_update_task"
    assert set(_json(change["fields"])) == {"priority", "status"}
    assert _json(change["before"]) == {"priority": "medium", "status": "todo"}
    assert _json(change["after"]) == {
        "priority": "high",
        "status": "in-progress",
    }
    assert row["rev"] == before_rev + 1
    assert row["updated_seq"] == change["seq"]
    assert tx.seq == change["seq"]

    committed = tx.committed["task", "e-001"]
    assert committed == _json(row["doc"])
    assert committed["status"] == "in-progress"
    assert committed["priority"] == "high"


def test_committed_payload_is_captured_before_a_later_writer_can_land(
    transaction_store: tuple[Any, Path], monkeypatch
) -> None:
    opened, _backlog_path = transaction_store
    original_finish = store.Transaction._finish_committed
    injected = False

    def finish_with_later_writer(tx):
        nonlocal injected
        if tx.tool == "writer-a" and not injected:
            injected = True
            with opened.transaction(tool="writer-b") as later:
                task = later.get("task", "e-001")
                task["title"] = "Writer B"
                later.put("task", "e-001", task)
        return original_finish(tx)

    monkeypatch.setattr(store.Transaction, "_finish_committed", finish_with_later_writer)
    with opened.transaction(tool="writer-a") as first:
        task = first.get("task", "e-001")
        task["title"] = "Writer A"
        first.put("task", "e-001", task)

    assert first.committed[("task", "e-001")]["title"] == "Writer A"
    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Writer B"


def test_unchanged_put_is_a_true_noop(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    with _connect(backlog_path) as connection:
        before = dict(_entity(connection, "e-001"))
        before_seq = _max_seq(connection)
        before_change_count = connection.execute(
            "SELECT COUNT(*) FROM changes"
        ).fetchone()[0]

    with opened.transaction(tool="noop-test") as tx:
        unchanged = deepcopy(tx.get("task", "e-001"))
        tx.put("task", "e-001", unchanged)

    with _connect(backlog_path) as connection:
        after = dict(_entity(connection, "e-001"))
        after_seq = _max_seq(connection)
        after_change_count = connection.execute(
            "SELECT COUNT(*) FROM changes"
        ).fetchone()[0]

    assert after == before
    assert after_seq == before_seq
    assert after_change_count == before_change_count


def test_exception_rolls_back_all_transactional_work_and_connection_is_reusable(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    with _connect(backlog_path) as connection:
        entity_before = dict(_entity(connection, "e-001"))
        changes_before = connection.execute(
            "SELECT * FROM changes ORDER BY seq"
        ).fetchall()
        projection_before = connection.execute(
            "SELECT * FROM projection ORDER BY file"
        ).fetchall()

    with pytest.raises(RuntimeError, match="abort transaction"):
        with opened.transaction(tool="rollback-test") as tx:
            task = tx.get("task", "e-001")
            task["status"] = "done"
            tx.put("task", "e-001", task)
            raise RuntimeError("abort transaction")

    with _connect(backlog_path) as connection:
        assert dict(_entity(connection, "e-001")) == entity_before
        assert connection.execute(
            "SELECT * FROM changes ORDER BY seq"
        ).fetchall() == changes_before
        assert connection.execute(
            "SELECT * FROM projection ORDER BY file"
        ).fetchall() == projection_before

    # The same pooled thread reuses its connection.  A rollback in ``finally``
    # must leave it able to acquire the next BEGIN IMMEDIATE and commit.
    with opened.transaction(tool="after-rollback") as tx:
        task = tx.get("task", "e-001")
        task["title"] = "committed after rollback"
        tx.put("task", "e-001", task)

    assert tx.committed["task", "e-001"]["title"] == "committed after rollback"


def test_rejected_nested_transaction_never_rolls_back_or_leaks_outer_work(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    with _connect(backlog_path) as connection:
        before = dict(_entity(connection, "e-001"))
        change_count = connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0]

    with pytest.raises(RuntimeError, match="outer abort"):
        with opened.transaction(tool="outer") as tx:
            task = tx.get("task", "e-001")
            task["title"] = "must roll back"
            tx.put("task", "e-001", task)
            with pytest.raises(RuntimeError, match="nested store transactions"):
                with opened.transaction(tool="inner"):
                    pass
            task = tx.get("task", "e-001")
            task["priority"] = "critical"
            tx.put("task", "e-001", task)
            raise RuntimeError("outer abort")

    with _connect(backlog_path) as connection:
        assert dict(_entity(connection, "e-001")) == before
        assert connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == change_count


def test_load_dict_uses_one_consistent_wal_snapshot(
    transaction_store: tuple[Any, Path], monkeypatch
) -> None:
    opened, _backlog_path = transaction_store
    original_loader = opened._load_dict_from_connection
    injected = False

    def load_after_peer_commit(connection):
        nonlocal injected
        if not injected:
            injected = True

            def write_peer():
                with opened.transaction(tool="snapshot-peer") as tx:
                    task = tx.get("task", "e-001")
                    task["title"] = "Peer committed"
                    tx.put("task", "e-001", task)

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(write_peer).result(timeout=5)
        return original_loader(connection)

    monkeypatch.setattr(opened, "_load_dict_from_connection", load_after_peer_commit)
    snapshot = opened.load_dict()
    assert snapshot["epics"][0]["tasks"][0]["title"] == "Task 1"
    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Peer committed"


def test_cached_read_refreshes_only_entities_newer_than_cached_sequence(
    transaction_store: tuple[Any, Path], monkeypatch
) -> None:
    opened, _backlog_path = transaction_store
    assert _task(opened.load_dict(), "e-001")["title"] == "Task 1"
    with opened.transaction(tool="incremental-cache-update") as tx:
        task = tx.get("task", "e-001")
        task["title"] = "Incrementally refreshed"
        tx.put("task", "e-001", task)

    def full_reload_forbidden(_connection):
        raise AssertionError("cache refresh rebuilt the full graph")

    monkeypatch.setattr(opened, "_load_dict_from_connection", full_reload_forbidden)
    assert _task(opened.load_dict(), "e-001")["title"] == "Incrementally refreshed"


def test_two_process_writers_preserve_both_independent_updates(tmp_path: Path) -> None:
    store.reset_for_tests()
    backlog_path = _write_v4_project(tmp_path)
    store.open_store(backlog_path, session="multiprocess-bootstrap")
    gate = tmp_path / "writer-gate"
    script = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from taskmaster import store
        gate, backlog, task_id, title = map(Path, sys.argv[1:])
        while not gate.exists():
            time.sleep(0.005)
        opened = store.open_store(backlog_path=backlog, session=f"writer-{task_id.name}")
        with opened.transaction(tool=f"writer-{task_id.name}") as tx:
            task = tx.get("task", task_id.name)
            task["title"] = title.name
            tx.put("task", task_id.name, task)
        """
    )
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(gate),
                str(backlog_path),
                task_id,
                title,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for task_id, title in (("e-001", "Writer one"), ("e-002", "Writer two"))
    ]
    gate.write_text("go", encoding="ascii")
    failures = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=20)
        if worker.returncode:
            failures.append((worker.returncode, stdout, stderr))
    assert failures == []

    store.reset_for_tests()
    result = store.load_dict(backlog_path)
    assert _task(result, "e-001")["title"] == "Writer one"
    assert _task(result, "e-002")["title"] == "Writer two"


def test_transaction_dict_nested_load_is_identical_and_removed_field_is_persisted(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store

    with opened.transaction_dict(tool="compat-update") as data:
        assert store.load_dict(backlog_path) is data
        task = _task(data, "e-001")
        task["status"] = "done"
        del task["priority"]

    with _connect(backlog_path) as connection:
        row = _entity(connection, "e-001")
        change = connection.execute(
            "SELECT * FROM changes "
            "WHERE kind = 'task' AND id = 'e-001' AND tool = 'compat-update' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    doc = _json(row["doc"])
    assert doc["status"] == "done"
    assert "priority" not in doc
    assert change is not None
    assert set(_json(change["fields"])) == {"priority", "status"}
    assert _json(change["before"]) == {"priority": "medium", "status": "todo"}
    assert _json(change["after"]) == {"status": "done"}


def test_put_rejects_document_id_that_differs_from_primary_key(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store

    with pytest.raises(ValueError, match="id is immutable"):
        with opened.transaction(tool="mismatched-put") as tx:
            task = tx.get("task", "e-001")
            task["id"] = "e-999"
            tx.put("task", "e-001", task)

    assert _task(opened.load_dict(), "e-001")["id"] == "e-001"


@pytest.mark.parametrize("unsafe_id", ["../../escape", "nested/name", r"nested\\name"])
def test_create_rejects_ids_that_are_not_safe_filename_components(
    transaction_store: tuple[Any, Path], unsafe_id: str
) -> None:
    opened, _backlog_path = transaction_store

    with pytest.raises(ValueError, match="unsafe entity id"):
        with opened.transaction(tool="unsafe-id") as tx:
            tx.create(
                "task",
                {"id": unsafe_id, "title": "Unsafe", "epic": "e"},
                requested_id=unsafe_id,
            )


def test_transaction_dict_ignores_missing_entities_but_creates_new_ones(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store

    with opened.transaction_dict(tool="compat-shape-diff") as data:
        epic = next(epic for epic in data["epics"] if epic["id"] == "e")
        epic["tasks"] = [task for task in epic["tasks"] if task["id"] != "e-002"]
        epic["tasks"].append(
            {
                "id": "e-004",
                "title": "Created through compatibility dict",
                "epic": "e",
                "order": 4.0,
                "status": "todo",
            }
        )

    with _connect(backlog_path) as connection:
        omitted = _entity(connection, "e-002")
        created = _entity(connection, "e-004")
        ops = connection.execute(
            "SELECT id, op FROM changes WHERE tool = 'compat-shape-diff' ORDER BY seq"
        ).fetchall()

    assert omitted["archived"] == 0
    assert omitted["deleted"] == 0
    assert _json(omitted["doc"])["title"] == "Task 2"
    assert created["archived"] == 0
    assert created["deleted"] == 0
    assert _json(created["doc"])["title"] == "Created through compatibility dict"
    assert [(row["id"], row["op"]) for row in ops] == [("e-004", "create")]


def test_archive_and_delete_are_explicit_and_leave_tombstone_rows(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store

    # Merely omitting an entity from compatibility data did not archive it in
    # the preceding contract.  These state transitions require named row ops.
    with opened.transaction(tool="explicit-removal") as tx:
        tx.archive("task", "e-002")
        tx.delete("task", "e-003")

    with _connect(backlog_path) as connection:
        archived = _entity(connection, "e-002")
        deleted = _entity(connection, "e-003")
        ops = connection.execute(
            "SELECT id, op FROM changes WHERE tool = 'explicit-removal' ORDER BY seq"
        ).fetchall()

    assert archived["archived"] == 1
    assert archived["deleted"] == 0
    assert deleted["deleted"] == 1
    assert [(row["id"], row["op"]) for row in ops] == [
        ("e-002", "archive"),
        ("e-003", "delete"),
    ]


def test_creation_token_change_invalidates_cache_even_without_a_new_change(
    transaction_store: tuple[Any, Path],
) -> None:
    _, backlog_path = transaction_store
    first = store.load_dict(backlog_path)
    assert _task(first, "e-001")["status"] == "todo"

    # Simulate replacing/rebuilding the database beneath a process whose
    # MAX(changes.seq) has not advanced.  The creation token is the cache epoch.
    with _connect(backlog_path) as connection:
        row = _entity(connection, "e-001")
        replacement = _json(row["doc"])
        replacement["status"] = "done"
        connection.execute(
            "UPDATE entities SET doc = ? WHERE kind = 'task' AND id = 'e-001'",
            (json.dumps(replacement, sort_keys=True, separators=(",", ":")),),
        )
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'creation_token'", (str(uuid.uuid4()),)
        )

    refreshed = store.load_dict(backlog_path)
    assert _task(refreshed, "e-001")["status"] == "done"


def test_reset_for_tests_prevents_same_path_cache_contamination(tmp_path: Path) -> None:
    store.reset_for_tests()
    backlog_path = _write_v4_project(tmp_path, title_prefix="First generation")
    first_status = store.status(backlog_path)
    assert _task(store.load_dict(backlog_path), "e-001")["title"] == "First generation 1"

    store.reset_for_tests()
    db = store.db_path(backlog_path)
    for candidate in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
        candidate.unlink(missing_ok=True)
    task_path = backlog_path.parent / "tasks" / "e-001.md"
    frontmatter, body = v3.task_v4_to_file(
        {
            "id": "e-001",
            "title": "Second generation 1",
            "epic": "e",
            "order": 1.0,
            "status": "todo",
            "priority": "medium",
        }
    )
    v3.write_task_file(task_path, frontmatter, body)

    try:
        second_status = store.status(backlog_path)
        second = store.load_dict(backlog_path)
        assert second_status.creation_token != first_status.creation_token
        assert _task(second, "e-001")["title"] == "Second generation 1"
    finally:
        store.reset_for_tests()


def test_unarchive_returns_the_projection_file_to_the_live_path(
    transaction_store: tuple[Any, Path],
) -> None:
    """`archive` must not be a one-way door: `put` never lowers the flag itself."""
    opened, backlog_path = transaction_store
    tasks_dir = backlog_path.parent / "tasks"
    with opened.transaction(tool="archive") as tx:
        tx.archive("task", "e-001")
    assert (tasks_dir / "archive" / "e-001.md").exists()
    assert not (tasks_dir / "e-001.md").exists()

    with opened.transaction(tool="unarchive") as tx:
        tx.unarchive("task", "e-001")

    connection = _connect(backlog_path)
    try:
        row = _entity(connection, "e-001")
        assert row["archived"] == 0
        assert "archived" not in _json(row["doc"])
    finally:
        connection.close()
    assert (tasks_dir / "e-001.md").exists()
    assert not (tasks_dir / "archive" / "e-001.md").exists()


def test_unarchive_is_a_noop_on_a_live_entity(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    connection = _connect(backlog_path)
    try:
        before = _max_seq(connection)
        with opened.transaction(tool="unarchive") as tx:
            tx.unarchive("task", "e-001")
        assert _max_seq(_connect(backlog_path)) == before
    finally:
        connection.close()


def test_unarchive_rejects_an_unknown_entity(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with pytest.raises(KeyError):
        with opened.transaction(tool="unarchive") as tx:
            tx.unarchive("task", "does-not-exist")


def test_active_transaction_is_the_one_behind_the_compatibility_dict(
    transaction_store: tuple[Any, Path],
) -> None:
    """The dict cannot express a removal, so callers need the transaction itself."""
    opened, backlog_path = transaction_store
    assert store.active_transaction() is None
    with opened.transaction_dict(tool="archive-through-the-dict") as data:
        tx = store.active_transaction()
        assert tx is not None
        assert tx.connection is opened.connection
        assert store.active_transaction(backlog_path) is tx
        _task(data, "e-002")["title"] = "Renamed inside the same transaction"
        tx.archive("task", "e-001")
    assert store.active_transaction() is None

    connection = _connect(backlog_path)
    try:
        assert _entity(connection, "e-001")["archived"] == 1
        assert _json(_entity(connection, "e-002")["doc"])["title"] == (
            "Renamed inside the same transaction"
        )
    finally:
        connection.close()


def test_active_transaction_ignores_a_different_store_path(
    transaction_store: tuple[Any, Path], tmp_path: Path
) -> None:
    opened, _backlog_path = transaction_store
    other = tmp_path / "elsewhere" / ".taskmaster"
    other.mkdir(parents=True)
    with opened.transaction_dict(tool="scoped") as _data:
        assert store.active_transaction(other / "backlog.yaml") is None


def _entity_doc(kind: str, ident: str, **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": ident, "title": f"{kind} {ident}", "status": "open"}
    doc.update(extra)
    return doc


def test_list_returns_live_rows_with_bodies_ordered_by_id(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="seed-bugs") as tx:
        tx.create("bug", _entity_doc("bug", "B-002"), body="Second body.")
        tx.create("bug", _entity_doc("bug", "B-001"), body="First body.")
        tx.create("bug", _entity_doc("bug", "B-003"))

    with opened.transaction(tool="list-bugs") as tx:
        rows = tx.list("bug")

    assert [ident for ident, _doc, _body in rows] == ["B-001", "B-002", "B-003"]
    assert [body for _ident, _doc, body in rows] == ["First body.", "Second body.", None]
    assert rows[0][1]["title"] == "bug B-001"


def test_list_hides_archived_and_tombstoned_rows_unless_asked(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="seed-notes") as tx:
        tx.create("note", _entity_doc("note", "NOTE-001"))
        tx.create("note", _entity_doc("note", "NOTE-002"))
        tx.create("note", _entity_doc("note", "NOTE-003"))
        tx.archive("note", "NOTE-002")
        tx.delete("note", "NOTE-003")

    with opened.transaction(tool="list-notes") as tx:
        live = tx.list("note")
        including_archived = tx.list("note", include_archived=True)

    assert [ident for ident, _doc, _body in live] == ["NOTE-001"]
    assert [ident for ident, _doc, _body in including_archived] == [
        "NOTE-001",
        "NOTE-002",
    ]


def test_list_of_an_unknown_kind_is_empty(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="list-nothing") as tx:
        assert tx.list("bug") == []


def test_linear_enqueue_returns_a_seq_that_pending_reads_back(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="enqueue-linear") as tx:
        first = tx.linear_enqueue("push", "e-001", "linear-acme-ENG-1", {"reason": "status"})
        second = tx.linear_enqueue("push", "e-002", None, None)

    assert first != second
    pending = opened.linear_pending(10)
    assert [item["seq"] for item in pending] == [first, second]
    assert pending[0] == {
        "seq": first,
        "op": "push",
        "target_id": "e-001",
        "tracker_id": "linear-acme-ENG-1",
        "payload": {"reason": "status"},
        "state": "pending",
        "attempts": 0,
        "last_error": None,
    }
    assert pending[1]["payload"] is None
    assert opened.linear_pending(1) == [pending[0]]


def test_linear_enqueue_dedupes_a_pending_push_for_the_same_target(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="enqueue-linear-twice") as tx:
        first = tx.linear_enqueue("push", "e-001", "linear-acme-ENG-1", None)
        again = tx.linear_enqueue("push", "e-001", "linear-acme-ENG-1", None)

    assert again == first
    assert [item["seq"] for item in opened.linear_pending(10)] == [first]


def test_linear_mark_commits_its_own_transaction(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    with opened.transaction(tool="enqueue-linear") as tx:
        seq = tx.linear_enqueue("push", "e-001", None, None)

    opened.linear_mark(seq, state="failed", error="429 from Linear")

    assert opened.linear_pending(10) == []
    with _connect(backlog_path) as connection:
        row = connection.execute(
            "SELECT state,attempts,last_error FROM linear_queue WHERE seq=?", (seq,)
        ).fetchone()
    assert row["state"] == "failed"
    assert row["attempts"] == 1
    assert row["last_error"] == "429 from Linear"

    opened.linear_mark(seq, state="pending")
    reopened = opened.linear_pending(10)
    assert [item["seq"] for item in reopened] == [seq]
    assert reopened[0]["attempts"] == 2
    assert reopened[0]["last_error"] is None


def test_linear_mark_rejects_an_unknown_seq(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, _backlog_path = transaction_store
    with pytest.raises(KeyError):
        opened.linear_mark(9999, state="done")


def test_legacy_linear_queue_json_is_imported_once_and_the_file_removed(
    tmp_path: Path,
) -> None:
    store.reset_for_tests()
    backlog_path = _write_v4_project(tmp_path)
    queue_file = backlog_path.parent / "integrations" / "linear-queue.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(
        json.dumps(
            [
                {
                    "op": "push",
                    "target_id": "e-001",
                    "tracker_id": "linear-acme-ENG-1",
                    "enqueued_at": "2026-09-01T10:00:00Z",
                    "attempts": 2,
                    "last_error": "transient",
                },
                {
                    "op": "push",
                    "target_id": "e-002",
                    "tracker_id": None,
                    "enqueued_at": "2026-09-01T10:05:00Z",
                    "attempts": 0,
                    "last_error": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    try:
        opened = store.open_store(backlog_path, session="linear-queue-import")
        pending = opened.linear_pending(10)
        assert [item["target_id"] for item in pending] == ["e-001", "e-002"]
        assert pending[0]["tracker_id"] == "linear-acme-ENG-1"
        assert pending[0]["attempts"] == 2
        assert pending[0]["last_error"] == "transient"
        assert pending[0]["payload"] == {"enqueued_at": "2026-09-01T10:00:00Z"}
        assert not queue_file.exists()

        store.reset_for_tests()
        reopened = store.open_store(backlog_path, session="linear-queue-reopen")
        with reopened.transaction(tool="scan-after-import"):
            pass
        assert [item["target_id"] for item in reopened.linear_pending(10)] == [
            "e-001",
            "e-002",
        ]
    finally:
        store.reset_for_tests()


def test_force_scan_on_next_read_defeats_the_read_throttle(
    transaction_store: tuple[Any, Path],
) -> None:
    opened, backlog_path = transaction_store
    assert _task(opened.load_dict(), "e-001")["title"] == "Task 1"

    task_file = backlog_path.parent / "tasks" / "e-001.md"
    frontmatter, body = v3.parse_frontmatter(task_file.read_text(encoding="utf-8"))
    frontmatter["title"] = "Edited by hand"
    task_file.write_text(v3.render_frontmatter(frontmatter, body), encoding="utf-8")

    assert _task(opened.load_dict(), "e-001")["title"] == "Task 1"
    opened.force_scan_on_next_read()
    assert _task(opened.load_dict(), "e-001")["title"] == "Edited by hand"


def test_linear_mark_rereads_the_connection_after_waiting_for_the_writer_mutex(
    transaction_store: tuple[Any, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovery that lands while `linear_mark` queues for the lock must not
    leave it writing through the handle it held before waiting."""
    opened, _backlog_path = transaction_store
    with opened.transaction(tool="enqueue-linear") as tx:
        seq = tx.linear_enqueue("push", "e-001", None, None)

    real_mutex = type(opened)._writer_mutex
    retired: list[Any] = []

    @contextmanager
    def recovering_mutex(self: Any, **kwargs: Any) -> Iterator[None]:
        with real_mutex(self, **kwargs):
            if not retired:
                stale = self.connection
                # Stand in for a concurrent recovery: the identity check in the
                # `connection` property closes this handle and opens another.
                store._CONNECTION_IDENTITIES[id(stale)] = (-1, -1)
                retired.append(stale)
                assert self.connection is not stale
            yield

    monkeypatch.setattr(store.Store, "_writer_mutex", recovering_mutex)
    opened.linear_mark(seq, state="failed", error="boom")
    monkeypatch.undo()

    assert retired and retired[0] is not opened.connection
    assert opened.linear_pending(10) == []
    with _connect(_backlog_path) as connection:
        row = connection.execute(
            "SELECT state,last_error FROM linear_queue WHERE seq=?", (seq,)
        ).fetchone()
    assert (row["state"], row["last_error"]) == ("failed", "boom")
