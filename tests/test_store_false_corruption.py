# User intent: B-092 -- two concurrent processes must never cause a healthy store to be
# renamed aside as corrupt and rebuilt from the lagging file projection, which silently
# loses every row that lives only in SQLite. These tests pin both halves of the rule: a
# retained-connection verdict a fresh read-only snapshot contradicts is not corruption,
# and a database the snapshot agrees is damaged is still recovered.
"""Admission-path corruption verdicts: which connection the verdict comes from.

`PRAGMA quick_check` on a connection that has already inspected the FTS5 index
reports `malformed inverted index for FTS5 table main.entity_fts` once a peer
connection commits to that index.  `docs/reports/2026-09-09-native-foundation.md`
(N00) establishes that this is retained runtime state and not damage: across a
20-case matrix on SQLite 3.45.3 and 3.47.1 every fresh read-only snapshot of the
same file reports `ok`.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest
import yaml

from taskmaster import store
from taskmaster.integrity import check_database


_FTS_ARTIFACT = "malformed inverted index for FTS5 table main.entity_fts"


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _project(tmp_path):
    """A projection on disk with one task, written by the test itself."""
    backlog_path = tmp_path / "repo" / ".taskmaster"
    (backlog_path / "tasks").mkdir(parents=True)
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 4,
                "project": "false-corruption-tests",
                "meta": {
                    "schema_version": 4,
                    "projection_schema": store.PROJECTION_SCHEMA,
                },
                "epics": [{"id": "core", "title": "Core", "status": "active"}],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (backlog_path / "tasks" / "core-001.md").write_text(
        "---\nid: core-001\ntitle: Projected title\nstatus: todo\nepic: core\n"
        "order: 1.0\npriority: medium\n---\n\n## Notes\n",
        encoding="utf-8",
    )
    return backlog_path


def _set_branch(tx, branch):
    doc = dict(tx.get("task", "core-001"))
    doc["branch"] = branch
    tx.put("task", "core-001", doc)


def _corrupt_backups(db_path):
    return sorted(db_path.parent.glob("store.db.corrupt-*"))


def _damage_fts_index(db_path):
    """Break index/content agreement so a fresh snapshot reports real damage.

    Only the content table is rewritten, so the file stays a readable SQLite
    database: this is the case the store must still catch, as opposed to a
    shredded header, which the byte-level header probe already refuses.
    """
    with closing(sqlite3.connect(db_path, isolation_level=None)) as connection:
        connection.execute("UPDATE entity_fts_content SET c3='tokens that were never indexed'")


def test_retained_connection_verdict_never_renames_a_healthy_store(tmp_path, monkeypatch):
    """The committed file is healthy, so the admission path must not destroy it."""
    backlog_path = _project(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="healthy")
    with opened.transaction(tool="false-corruption-tests") as tx:
        _set_branch(tx, "feature/only-in-sqlite")
    db_path = opened.db_path
    assert check_database(db_path)["integrity"] == ["ok"]

    # Exactly what a peer commit does to a retained connection's FTS diagnostic.
    monkeypatch.setattr(store, "_quick_check", lambda connection, limit=None: _FTS_ARTIFACT)

    opened._bootstrapped = False
    opened._ensure_open()

    assert _corrupt_backups(db_path) == [], "a healthy store was renamed aside as corrupt"
    monkeypatch.undo()
    store.reset_for_tests()
    reopened = store.open_store(backlog_path=backlog_path, session="after")
    task = next(
        task
        for epic in reopened.load_dict()["epics"]
        for task in epic.get("tasks", [])
        if task["id"] == "core-001"
    )
    assert task["branch"] == "feature/only-in-sqlite", "the SQLite-only write was lost"


def test_unconfirmed_corruption_error_is_retried_not_recovered(tmp_path, monkeypatch):
    """A corruption-shaped *exception* from a retained connection is gated too.

    `_prepare_schema` is not the only statement that can raise a corruption
    marker off stale FTS state, so the gate sits at the recovery decision as
    well as at the pragma verdict.
    """
    backlog_path = _project(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="healthy")
    db_path = opened.db_path
    monkeypatch.setattr(store.Store, "_open_existing_unlocked", lambda self: False)

    real_prepare = store.Store._prepare_schema
    calls = 0

    def fail_first(self, connection):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.DatabaseError(f"malformed database: quick_check={_FTS_ARTIFACT}")
        return real_prepare(self, connection)

    monkeypatch.setattr(store.Store, "_prepare_schema", fail_first)
    store.reset_for_tests()
    reopened = store.open_store(backlog_path=backlog_path, session="retry")

    assert calls >= 2, "the open never retried on a connection without stale state"
    assert _corrupt_backups(db_path) == [], "an unconfirmed verdict still renamed the family"
    assert reopened.db_path == db_path


def test_genuinely_damaged_database_is_still_detected_and_recovered(tmp_path):
    """The other half: damage a fresh snapshot confirms must still be recovered."""
    backlog_path = _project(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="before-damage")
    db_path = opened.db_path
    store.reset_for_tests()
    _damage_fts_index(db_path)
    assert check_database(db_path)["integrity"] != ["ok"]

    rebuilt = store.open_store(backlog_path=backlog_path, session="after-damage")

    backups = [path for path in _corrupt_backups(db_path) if not path.name.endswith(("-wal", "-shm"))]
    assert len(backups) == 1, "genuine corruption was not renamed aside"
    task = next(
        task
        for epic in rebuilt.load_dict()["epics"]
        for task in epic.get("tasks", [])
        if task["id"] == "core-001"
    )
    assert task["title"] == "Projected title"
    with rebuilt.transaction(tool="false-corruption-tests") as tx:
        _set_branch(tx, "feature/after-recovery")
    assert check_database(rebuilt.db_path)["integrity"] == ["ok"]


def test_a_real_peer_commit_leaves_the_store_intact(tmp_path):
    """End to end, without simulating anything: peer commit, then reopen.

    Whether this runtime raises the artifact is a property of SQLite, so the
    test does not require it; it requires that the store survive it either way,
    and reports which case it saw.
    """
    backlog_path = _project(tmp_path)
    opened = store.open_store(backlog_path=backlog_path, session="retained")
    db_path = opened.db_path
    retained = opened.connection
    assert store._quick_check(retained).lower() == "ok"
    with closing(sqlite3.connect(db_path, isolation_level=None)) as peer:
        peer.execute("PRAGMA busy_timeout=30000")
        peer.execute(
            "INSERT INTO entity_fts(kind,id,title,body) VALUES('task','peer-001','Peer','peer document')"
        )
    reproduced = store._quick_check(retained).lower() != "ok"

    opened._bootstrapped = False
    opened._ensure_open()

    assert _corrupt_backups(db_path) == [], (
        f"a healthy store was renamed aside (artifact reproduced: {reproduced})"
    )
    assert check_database(db_path)["integrity"] == ["ok"]
