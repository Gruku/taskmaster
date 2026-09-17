"""User intent: a write that committed must never be reported as failed, and a
forked child must never inherit a writer gate or lock some parent thread held
(6.0.3 review findings 3 and 4). A failure in either place makes an agent retry
a committed write, or hangs every write the child attempts.
"""
from __future__ import annotations

import sqlite3
import threading

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


def test_a_fork_child_does_not_inherit_held_arrival_gates(tmp_path):
    path = tmp_path / "writer.lock"
    parent_gate = store_mod._arrival_gate(path)
    ticket = parent_gate.enter()
    assert parent_gate.wait_turn(ticket, store_mod._MONOTONIC() + 1)
    parent_lock = store_mod._ARRIVAL_GATES_LOCK
    parent_lock.acquire()
    try:
        # In the child, the threads holding these no longer exist.
        store_mod._after_fork_child()

        child_lock = store_mod._ARRIVAL_GATES_LOCK
        assert child_lock.acquire(timeout=1), "the child inherited a held gate lock"
        child_lock.release()

        found: list = []
        worker = threading.Thread(
            target=lambda: found.append(store_mod._arrival_gate(path)), daemon=True
        )
        worker.start()
        worker.join(timeout=2)
        assert found, "the child hung taking an arrival gate"
        child_gate = found[0]
        assert child_gate is not parent_gate
        child_ticket = child_gate.enter()
        assert child_gate.wait_turn(child_ticket, store_mod._MONOTONIC() + 0.5)
        child_gate.release()
    finally:
        parent_lock.release()
        parent_gate.release()
