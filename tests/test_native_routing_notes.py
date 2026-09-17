"""User intent: the first routed family (N08) — sticky notes served by the native
core must answer, commit and project exactly as the legacy tools do, and a native
store must never be handed to the legacy writer for a tool that is not routed yet.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, is_native, make_twins, normalize


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_note(action="create", text="Seeded legacy note\nsecond line", pinned=True)
    return make_twins(tmp_path, monkeypatch, seed)


def _manifest_state(root):
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        return connection.execute("SELECT value FROM native_manifest WHERE key='state'").fetchone()[0]


def test_an_unrouted_tool_refuses_on_a_native_store_and_changes_nothing(twins):
    before = committed(twins.native)
    with twins.at(twins.native):
        answer = bs.backlog_migrate_v3()
    assert answer.startswith("Error:") and "not yet routed through the native core" in answer
    assert committed(twins.native) == before
    assert is_native(twins.native) and _manifest_state(twins.native) == "ready"


def test_an_unknown_action_is_refused_like_the_legacy_router(twins):
    legacy, native = twins.call("backlog_note", action="explode")
    assert native == legacy == "Error: unknown action 'explode'"


def test_note_create_matches_answer_state_and_files(twins):
    twins.same("backlog_note", action="create", text="Native parity note", pinned=False)
    twins.assert_state_matches()
    twins.assert_files_match()
    assert _manifest_state(twins.native) == "ready"


def test_note_create_refusal_matches(twins):
    twins.same("backlog_note", action="create", text="   ")
    twins.assert_state_matches()


def test_note_list_and_get_match(twins):
    twins.same("backlog_note", action="create", text="Another note")
    twins.same("backlog_note", action="list")
    twins.same("backlog_note", action="list", limit=1)
    twins.same("backlog_note", action="list", include_archived=True, limit=0)
    twins.same("backlog_note", action="get", note_id="NOTE-001")
    twins.same("backlog_note", action="get", note_id="NOTE-404")


def test_note_update_and_archive_match_including_the_archive_move(twins):
    twins.same("backlog_note", action="update", note_id="NOTE-001", text="Edited text", pinned=False)
    twins.same("backlog_note", action="update", note_id="NOTE-404", text="x")
    twins.same("backlog_note", action="archive", note_id="NOTE-001")
    twins.same("backlog_note", action="archive", note_id="NOTE-404")
    twins.same("backlog_note", action="list", include_archived=True)
    twins.assert_state_matches()
    twins.assert_files_match()
    assert (twins.native / ".taskmaster" / "notes" / "_archive" / "NOTE-001.md").exists()
    assert not (twins.native / ".taskmaster" / "notes" / "NOTE-001.md").exists()


def test_a_native_answer_carries_its_commit_sequence(twins):
    _legacy, native = twins.call("backlog_note", action="create", text="Sequenced")
    assert normalize(native).endswith("[seq #]"), native
