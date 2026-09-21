"""User intent: pin what the N08 synchronous drain does before N11 changes it, so each
N11 step shows exactly which behaviour it replaced. The hand-edit overwrite and the
mid-drain rollback are the defects N11 fixes; their tests say so and flip when fixed.
"""
from __future__ import annotations

import hashlib

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
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


def test_a_hand_edit_is_overwritten_by_the_next_native_write(twins):
    """The defect: native never compares the render with the bytes on disk (§1.3)."""
    path = _file(twins.native, "tasks/test-epic-001.md")
    path.write_bytes(path.read_bytes() + b"\nA hand edit.\n")
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
    assert b"A hand edit." not in path.read_bytes()


def test_a_failure_mid_drain_rolls_back_the_record_of_an_already_replaced_file(twins, monkeypatch):
    """The defect of §1.2(3): one failure un-does every record in the drain."""
    with native_connection(twins.native) as connection:
        commit_only(connection, "batch", {"commands": [
            {"operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": "One"}}},
            {"operation": "task.patch", "arguments": {"id": "test-epic-002", "set": {"title": "Two"}}}]})
        real, calls = store.render_entity_file, []

        def render(kind, fields, body):
            calls.append(fields["id"])
            if len(calls) == 2:
                raise RuntimeError("renderer broke on the second file")
            return real(kind, fields, body)

        monkeypatch.setattr(store, "render_entity_file", render)
        with pytest.raises(RuntimeError):
            projection.drain(connection, twins.native / ".taskmaster", session="t")
        first = _file(twins.native, "tasks/test-epic-001.md").read_bytes()
        recorded = connection.execute(
            "SELECT content_hash FROM projection WHERE file='tasks/test-epic-001.md'").fetchone()[0]
        states = dict(connection.execute("SELECT file,state FROM projection_jobs WHERE state!='exported'"))
    assert b"title: One" in first
    assert recorded != hashlib.sha1(first).hexdigest()
    assert states["tasks/test-epic-001.md"] == "pending"


def test_terminal_jobs_are_never_deleted(twins):
    with twins.at(twins.native):
        for n in range(5):
            bs.backlog_update_task(task_id="test-epic-001", field="notes", value=f"edit {n}")
    with native_connection(twins.native) as connection:
        jobs = connection.execute(
            "SELECT COUNT(*) FROM projection_jobs WHERE file='tasks/test-epic-001.md'").fetchone()[0]
    assert jobs == 5


def test_a_held_job_is_walked_again_on_every_drain(twins, monkeypatch):
    from native_twins import native_connection as connect
    rel = "tasks/test-epic-001.md"
    with connect(twins.native) as connection:
        connection.execute("INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) "
                           "VALUES(?,?,?,'2026-09-21T00:00:00Z','x',x'00')", (rel, "task", "test-epic-001"))
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="held")
        real, walked = projection._held_files, []

        def held(connection, kind, ident):
            walked.append(ident)
            return real(connection, kind, ident)

        monkeypatch.setattr(projection, "_held_files", held)
        answer = bs.backlog_update_task(task_id="test-epic-002", field="notes", value="unrelated")
    assert "test-epic-001" in walked
    assert f"export pending: {rel} is flagged" in answer


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
