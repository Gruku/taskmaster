"""User intent: pin what the N08 synchronous drain did before N11 changed it, so each
N11 step shows exactly which behaviour it replaced. The hand-edit overwrite and the
mid-drain rollback were the defects N11 fixes; their tests flipped when the drain
moved onto the outbox protocol (S6), and each says what it used to pin.
"""
from __future__ import annotations

import hashlib

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native_routing import projection
from native_twins import commit_only, make_twins, native_connection


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="first notes")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev", notes="second notes")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _file(root, rel):
    return root / ".taskmaster" / rel


def test_a_hand_edit_is_kept_and_flagged_instead_of_overwritten(twins):
    """Was: the next native write overwrote a hand edit silently (§1.3). Now (D2) the
    edit is kept byte for byte, flagged, and the entity's exports pause."""
    rel = "tasks/test-epic-001.md"
    path = _file(twins.native, rel)
    edited = path.read_bytes() + b"\nA hand edit.\n"
    path.write_bytes(edited)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
    assert path.read_bytes() == edited
    assert f"export pending: {rel} is flagged" in answer
    with native_connection(twins.native) as connection:
        kept = connection.execute("SELECT file_content FROM projection_conflict WHERE file=?", (rel,)).fetchone()[0]
    assert bytes(kept) == edited


def test_a_failure_mid_drain_keeps_the_record_of_an_already_replaced_file(twins, monkeypatch):
    """Was (§1.2(3)): one failure rolled back every record in the drain, including
    files already replaced. Now each file is acked on its own."""
    with native_connection(twins.native) as connection:
        commit_only(connection, "batch", {"commands": [
            {"operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": "One"}}},
            {"operation": "task.patch", "arguments": {"id": "test-epic-002", "set": {"title": "Two"}}}]})

        def checkpoint(stage, rel):
            if stage == "before_write" and rel == "tasks/test-epic-002.md":
                raise RuntimeError("the drain broke on the second file")

        monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
        with pytest.raises(RuntimeError):
            projection.drain(connection, twins.native / ".taskmaster", session="t")
        first = _file(twins.native, "tasks/test-epic-001.md").read_bytes()
        recorded = connection.execute(
            "SELECT content_hash FROM projection WHERE file='tasks/test-epic-001.md'").fetchone()[0]
        states = dict(connection.execute("SELECT file,state FROM projection_jobs"))
        assert b"title: One" in first
        assert recorded == hashlib.sha1(first).hexdigest()
        assert states["tasks/test-epic-001.md"] == "exported"
        monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
        assert projection.drain(connection, twins.native / ".taskmaster", session="t") == []
    assert b"title: Two" in _file(twins.native, "tasks/test-epic-002.md").read_bytes()


def test_terminal_jobs_are_retained_one_per_file(twins):
    """Was: terminal jobs were never deleted. Now (D7) one export record per file."""
    with twins.at(twins.native):
        for n in range(5):
            bs.backlog_update_task(task_id="test-epic-001", field="notes", value=f"edit {n}")
    with native_connection(twins.native) as connection:
        jobs = connection.execute(
            "SELECT COUNT(*) FROM projection_jobs WHERE file='tasks/test-epic-001.md'").fetchone()[0]
    assert jobs == 1


def test_a_held_job_stays_pending_and_unleased_while_other_calls_export(twins):
    """Was: every drain walked a held job again. Now it stays `pending`, is never
    claimed, and each call still names the flagged file."""
    rel = "tasks/test-epic-001.md"
    with native_connection(twins.native) as connection:
        connection.execute("INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) "
                           "VALUES(?,?,?,'2026-09-21T00:00:00Z','x',x'00')", (rel, "task", "test-epic-001"))
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="held")
        answer = bs.backlog_update_task(task_id="test-epic-002", field="notes", value="unrelated")
    assert f"export pending: {rel} is flagged" in answer
    with native_connection(twins.native) as connection:
        rows = connection.execute("SELECT state,lease_owner FROM projection_jobs WHERE file=? "
                                  "AND state NOT IN ('exported','superseded')", (rel,)).fetchall()
    assert rows == [("pending", None)]


def test_twin_projections_are_byte_identical_after_a_mixed_journey(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="notes", value="journey")
    twins.same("backlog_archive_task", task_id="test-epic-002", reason="wont-fix")
    twins.same("backlog_bug_create", title="Journey bug", components=["ui"])
    twins.same("backlog_idea_create", title="Journey idea", body="an idea")
    twins.same("backlog_update_epic", epic_id="test-epic", field="description", value="Epic prose")
    twins.same("backlog_handover_create", tldr="Journey handover", task_ids=["test-epic-001"])
    twins.same("backlog_update_epic", epic_id="test-epic", field="description", value="")
    twins.assert_state_matches()
    twins.assert_files_match()
