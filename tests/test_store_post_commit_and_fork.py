"""User intent: a write that committed must never be reported as failed, and a
forked child must never inherit a writer gate or lock some parent thread held
(6.0.3 review findings 3 and 4). A failure in either place makes an agent retry
a committed write, or hangs every write the child attempts.
"""
from __future__ import annotations

import sqlite3

from taskmaster import store as store_mod

from test_store_bug_cluster import _build_projection


def test_session_bookkeeping_after_commit_cannot_fail_the_committed_write(
    tmp_path, monkeypatch
):
    backlog_path = _build_projection(tmp_path)
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    original = store_mod.Transaction._finish_committed

    def publish_fence_after_commit(self):
        original(self)
        connection = sqlite3.connect(store_obj.db_path)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('migration_state','migrating')"
            )
            connection.commit()
        finally:
            connection.close()

    monkeypatch.setattr(
        store_mod.Transaction, "_finish_committed", publish_fence_after_commit
    )

    with store_obj.transaction(tool="test-write") as tx:
        created = tx.create("bug", {"title": "committed", "status": "open"})

    connection = sqlite3.connect(store_obj.db_path)
    try:
        ids = [row[0] for row in connection.execute("SELECT id FROM entities WHERE kind='bug'")]
    finally:
        connection.close()
    assert ids == [created]

