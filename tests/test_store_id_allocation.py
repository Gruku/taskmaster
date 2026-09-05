"""Contract tests for collision-safe entity id allocation in the SQLite store."""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import render_frontmatter


NUMERIC_KINDS = (
    ("task", "core-"),
    ("bug", "B-"),
    ("issue", "ISS-"),
    ("decision", "DEC-"),
    ("idea", "IDEA-"),
    ("note", "NOTE-"),
)

NUMERIC_FILE_DIRS = (
    ("task", "core-", "tasks", "tasks/archive"),
    ("bug", "B-", "bugs", "bugs/archive"),
    ("issue", "ISS-", "issues", "issues/archive"),
    ("decision", "DEC-", "decisions", None),
    ("idea", "IDEA-", "ideas", None),
    ("note", "NOTE-", "notes", "notes/_archive"),
)


@pytest.fixture()
def opened_store(tmp_path):
    from taskmaster import store

    store.reset_for_tests()
    root = tmp_path / "repo"
    backlog_path = root / ".taskmaster" / "backlog.yaml"
    backlog_path.parent.mkdir(parents=True)
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "version": 4,
                "project": "id-allocation-tests",
                "meta": {"schema_version": 4},
                "epics": [],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    instance = store.open_store(
        backlog_path=backlog_path,
        session="id-allocation-tests",
    )
    yield store, instance, backlog_path
    store.reset_for_tests()


def _doc(kind: str, label: str) -> dict:
    doc = {"title": label, "status": "todo"}
    if kind == "task":
        doc.update(
            {
                "epic": "core",
                "phase": "build",
                "tldr": f"{label}.",
                "order": 1.0,
            }
        )
    return doc


def _numeric_id(prefix: str, suffix: int) -> str:
    return f"{prefix}{suffix:03d}"


def _write_orphan(
    backlog_path: Path,
    relative_directory: str,
    kind: str,
    ident: str,
) -> Path:
    path = backlog_path.parent / relative_directory / f"{ident}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = _doc(kind, f"orphan {ident}")
    doc["id"] = ident
    path.write_text(
        render_frontmatter(doc, "Orphan projection retained for allocation."),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(("kind", "prefix"), NUMERIC_KINDS)
def test_numeric_allocator_counts_live_archived_and_tombstoned_rows(
    opened_store,
    kind,
    prefix,
):
    _store_api, instance, _backlog_path = opened_store
    live_id = _numeric_id(prefix, 4)
    archived_id = _numeric_id(prefix, 7)
    tombstoned_id = _numeric_id(prefix, 11)

    with instance.transaction(tool=f"seed-{kind}-row-states") as tx:
        tx.create(kind, _doc(kind, "live"), requested_id=live_id)
        tx.create(kind, _doc(kind, "archived"), requested_id=archived_id)
        tx.archive(kind, archived_id)
        tx.create(kind, _doc(kind, "tombstoned"), requested_id=tombstoned_id)
        tx.delete(kind, tombstoned_id)

    with instance.transaction(tool=f"allocate-{kind}-after-row-states") as tx:
        allocated = tx.create(kind, _doc(kind, "next"))

    assert allocated == _numeric_id(prefix, 12)


def test_tombstoned_id_remains_reserved_after_schema_rebuild(opened_store):
    store_api, instance, backlog_path = opened_store
    with instance.transaction(tool="reserve-before-rebuild") as tx:
        tx.create("task", _doc("task", "reserved"), requested_id="core-050")
        tx.delete("task", "core-050")

    instance.connection.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        (str(store_api.SCHEMA_VERSION + 100),),
    )
    store_api.reset_for_tests()
    rebuilt = store_api.open_store(backlog_path=backlog_path, session="after-rebuild")
    with rebuilt.transaction(tool="allocate-after-rebuild") as tx:
        allocated = tx.create("task", _doc("task", "next"))

    assert allocated == "core-051"


@pytest.mark.parametrize(
    ("kind", "prefix", "live_directory", "archive_directory"),
    NUMERIC_FILE_DIRS,
)
def test_numeric_allocator_counts_live_and_archive_projection_files(
    opened_store,
    kind,
    prefix,
    live_directory,
    archive_directory,
):
    _store_api, instance, backlog_path = opened_store
    _write_orphan(
        backlog_path,
        live_directory,
        kind,
        _numeric_id(prefix, 13),
    )
    highest = 17 if archive_directory else 13
    if archive_directory:
        _write_orphan(
            backlog_path,
            archive_directory,
            kind,
            _numeric_id(prefix, highest),
        )

    with instance.transaction(tool=f"allocate-{kind}-after-orphan-files") as tx:
        allocated = tx.create(kind, _doc(kind, "after orphan files"))

    assert allocated == _numeric_id(prefix, highest + 1)


def test_requested_id_rejects_tombstoned_row(opened_store):
    _store_api, instance, _backlog_path = opened_store
    requested_id = "core-050"

    with instance.transaction(tool="seed-requested-id-tombstone") as tx:
        tx.create("task", _doc("task", "reserved"), requested_id=requested_id)
        tx.delete("task", requested_id)

    with pytest.raises(
        ValueError,
        match=rf"(?i)task.*{re.escape(requested_id)}.*(exists|conflict|reserved)",
    ):
        with instance.transaction(tool="reuse-tombstoned-requested-id") as tx:
            tx.create("task", _doc("task", "must not reuse"), requested_id=requested_id)


@pytest.mark.parametrize(
    "relative_directory",
    ("tasks", "tasks/archive"),
    ids=("live-file", "archive-file"),
)
def test_requested_id_rejects_orphan_projection_file(
    opened_store,
    relative_directory,
):
    _store_api, instance, backlog_path = opened_store
    requested_id = "core-051"
    orphan = _write_orphan(
        backlog_path,
        relative_directory,
        "task",
        requested_id,
    )
    original = orphan.read_bytes()

    with pytest.raises(
        ValueError,
        match=rf"(?i)task.*{re.escape(requested_id)}.*(exists|conflict|reserved)",
    ):
        with instance.transaction(tool="requested-id-file-collision") as tx:
            tx.create("task", _doc("task", "must not overwrite"), requested_id=requested_id)

    assert orphan.read_bytes() == original


def test_concurrent_transactions_allocate_unique_ids(opened_store):
    store_api, _instance, backlog_path = opened_store
    worker_count = 8
    start = threading.Barrier(worker_count)

    def allocate(index: int) -> str:
        try:
            start.wait(timeout=10)
            with store_api.transaction(
                tool=f"concurrent-allocator-{index}",
                backlog_path=backlog_path,
            ) as tx:
                return tx.create("task", _doc("task", f"worker {index}"))
        finally:
            store_api.close_thread_connection()

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        allocated = list(pool.map(allocate, range(worker_count)))

    expected = {_numeric_id("core-", value) for value in range(1, worker_count + 1)}
    assert set(allocated) == expected
    assert len(allocated) == len(set(allocated))

    instance = store_api.open_store(backlog_path=backlog_path)
    with instance.transaction(tool="verify-concurrent-allocations") as tx:
        assert {tx.get("task", ident)["id"] for ident in allocated} == expected


def test_same_id_is_valid_in_different_kinds(opened_store):
    _store_api, instance, _backlog_path = opened_store
    shared_id = "shared-001"

    with instance.transaction(tool="cross-kind-id") as tx:
        task_id = tx.create(
            "task",
            _doc("task", "task namespace"),
            requested_id=shared_id,
        )
        bug_id = tx.create(
            "bug",
            _doc("bug", "bug namespace"),
            requested_id=shared_id,
        )

    assert task_id == bug_id == shared_id
    with instance.transaction(tool="verify-cross-kind-id") as tx:
        assert tx.get("task", shared_id)["title"] == "task namespace"
        assert tx.get("bug", shared_id)["title"] == "bug namespace"


def test_duplicate_handover_slug_gets_monotonic_suffixes(opened_store):
    _store_api, instance, _backlog_path = opened_store
    base_id = "2026-09-04-sqlite-store"

    with instance.transaction(tool="duplicate-handover-slugs") as tx:
        allocated = [
            tx.create(
                "handover",
                _doc("handover", f"handover {number}"),
                requested_id=base_id,
            )
            for number in range(1, 4)
        ]

    assert allocated == [base_id, f"{base_id}-2", f"{base_id}-3"]


@pytest.mark.parametrize(
    ("kind", "requested_id"),
    (("area", "store-core"), ("tracker", "linear-main")),
)
def test_duplicate_area_and_tracker_ids_fail_clearly(
    opened_store,
    kind,
    requested_id,
):
    _store_api, instance, _backlog_path = opened_store

    with instance.transaction(tool=f"seed-{kind}-id") as tx:
        assert (
            tx.create(kind, _doc(kind, "original"), requested_id=requested_id)
            == requested_id
        )

    with pytest.raises(
        ValueError,
        match=rf"(?i){kind}.*{re.escape(requested_id)}.*(exists|conflict|reserved)",
    ):
        with instance.transaction(tool=f"duplicate-{kind}-id") as tx:
            tx.create(kind, _doc(kind, "duplicate"), requested_id=requested_id)

    with instance.transaction(tool=f"verify-{kind}-id") as tx:
        assert tx.get(kind, requested_id)["title"] == "original"


def test_handover_id_is_derived_from_the_document_date_and_tldr(opened_store):
    _store_api, instance, _backlog_path = opened_store

    with instance.transaction(tool="allocate-handover") as tx:
        allocated = tx.allocate_id(
            "handover", {"date": "2026-09-05", "tldr": "Ship the store"}
        )

    assert allocated == "2026-09-05-ship-the-store"


def test_handover_id_suffixes_past_slugs_already_taken(opened_store):
    _store_api, instance, _backlog_path = opened_store
    doc = {"date": "2026-09-05", "tldr": "Ship the store", "status": "open"}

    with instance.transaction(tool="seed-handovers") as tx:
        first = tx.create("handover", dict(doc, title="First"))
        second = tx.create("handover", dict(doc, title="Second"))
        third = tx.allocate_id("handover", doc)

    assert first == "2026-09-05-ship-the-store"
    assert second == "2026-09-05-ship-the-store-2"
    assert third == "2026-09-05-ship-the-store-3"


def test_handover_allocation_requires_a_date_and_tldr(opened_store):
    _store_api, instance, _backlog_path = opened_store

    with pytest.raises(ValueError, match="(?i)tldr"):
        with instance.transaction(tool="allocate-handover-without-tldr") as tx:
            tx.allocate_id("handover", {"date": "2026-09-05"})


def test_issue_allocation_sees_the_archive_directory(opened_store):
    _store_api, instance, backlog_path = opened_store
    _write_orphan(backlog_path, "issues/archive", "issue", "ISS-042")

    with instance.transaction(tool="allocate-issue-after-archive") as tx:
        allocated = tx.allocate_id("issue", {})

    assert allocated == "ISS-043"


def test_area_and_tracker_ids_stay_caller_derived(opened_store):
    _store_api, instance, _backlog_path = opened_store

    for kind in ("area", "tracker"):
        with pytest.raises(ValueError, match="caller-derived"):
            with instance.transaction(tool=f"allocate-{kind}") as tx:
                tx.allocate_id(kind, {"name": "Nope"})
