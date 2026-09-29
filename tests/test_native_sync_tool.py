"""User intent: `backlog_sync` is the explicit MCP recourse when a hand edit to `.taskmaster/`
files has not taken effect on a native store (normal commands never scan projection files).
It runs the coordinator's full or named sync, answers honestly and compactly (synchronized
with counts, or pending with a retryable sync id, conflicts named with next steps), stays
under a fixed client tool timeout, and is a clear no-op on a legacy store.
"""
from __future__ import annotations

import re

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native.queries import Repository
from taskmaster.native_routing import resync
from native_twins import make_twins, native_connection

T1 = "tasks/test-epic-001.md"
T2 = "tasks/test-epic-002.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded notes")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed)
    # The copied files were never published by this store: a first sync records their
    # bytes as trusted bases, so later edits import instead of flagging.
    with twins.at(twins.native):
        first = bs.backlog_sync()
    assert first.startswith("Sync complete"), first
    return twins


def _replace(twins, rel, old, new):
    path = twins.native / ".taskmaster" / rel
    content = path.read_bytes()
    assert old.encode() in content
    path.write_bytes(content.replace(old.encode(), new.encode()))


def _title(twins, ident):
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get("task", ident)["fields"]["title"]


def _sync_id(answer):
    match = re.search(r'backlog_sync\(sync_id="([^"]+)"', answer)
    assert match, answer
    return match.group(1)


def test_sync_imports_a_hand_edit_with_counts_and_a_seq(twins):
    _replace(twins, T1, "title: First", "title: Edited by hand")
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete"), answer
    assert "1 imported" in answer and "0 conflicts" in answer, answer
    assert re.search(r"\d+ unchanged", answer), answer
    assert f"Imported: {T1}" in answer, answer
    assert re.search(r"\[seq \d+\]$", answer), answer
    assert _title(twins, "test-epic-001") == "Edited by hand"


def test_sync_with_nothing_edited_imports_nothing(twins):
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete") and "0 imported" in answer, answer
    assert "[seq" not in answer, answer


def test_sync_merges_edits_to_different_fields(twins):
    # The file and the store changed different things: the sync merges both, no conflict.
    with (twins.native / ".taskmaster" / T1).open("a", encoding="utf-8") as handle:
        handle.write("\nHand edit.\n")
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store change")
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete") and "1 imported" in answer and "0 conflicts" in answer, answer


def test_sync_names_a_conflict_with_the_resolver_as_next_step(twins):
    # The file and the store changed the same field: nothing is merged, the file is flagged.
    _replace(twins, T1, "title: First", "title: File title")
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="title", value="Store title")
        answer = bs.backlog_sync()
    assert answer.startswith("Sync pending"), answer
    assert "1 conflict" in answer, answer
    assert T1 in answer and f'backlog_resolve_conflict(file="{T1}")' in answer, answer
    assert _title(twins, "test-epic-001") == "Store title"


def test_sync_names_a_quarantined_file(twins):
    _replace(twins, T2, "title: Second", "title: [unclosed")
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync pending"), answer
    assert "quarantined" in answer and T2 in answer and "backlog_resolve_conflict" in answer, answer
    assert _title(twins, "test-epic-002") == "Second"


def test_a_pending_sync_continues_under_its_sync_id_until_synchronized(twins, monkeypatch):
    from tests.native_coordinator_helpers import _owners
    import time
    _replace(twins, T1, "title: First", "title: Slow edit")
    owner = _owners[twins.native.resolve()]
    monkeypatch.setattr(resync, "TOOL_BUDGET", 1)
    monkeypatch.setattr(owner, "checkpoint",
                        lambda stage: time.sleep(1.2) if stage == "sync_files_selected" else None)
    with twins.at(twins.native):
        pending = bs.backlog_sync()
    assert pending.startswith("Sync pending"), pending
    assert "time budget" in pending, pending
    assert _title(twins, "test-epic-001") == "First"
    sync_id = _sync_id(pending)
    monkeypatch.setattr(owner, "checkpoint", lambda stage: None)
    monkeypatch.setattr(resync, "TOOL_BUDGET", 15)
    with twins.at(twins.native):
        done = bs.backlog_sync(sync_id=sync_id)
    assert done.startswith(f"Sync complete (sync id {sync_id})"), done
    assert _title(twins, "test-epic-001") == "Slow edit"
    # A completed sync id answers its stored result again, importing nothing new.
    with twins.at(twins.native):
        again = bs.backlog_sync(sync_id=sync_id)
    assert again.startswith(f"Sync complete (sync id {sync_id})"), again


def test_named_files_sync_only_those_files(twins):
    _replace(twins, T1, "title: First", "title: Named edit")
    _replace(twins, T2, "title: Second", "title: Unnamed edit")
    with twins.at(twins.native):
        answer = bs.backlog_sync(files=[".taskmaster/" + T1])
    assert answer.startswith("Sync complete") and "1 file(s) checked" in answer, answer
    assert _title(twins, "test-epic-001") == "Named edit"
    assert _title(twins, "test-epic-002") == "Second"


def test_a_named_retry_must_name_the_same_files(twins, monkeypatch):
    from tests.native_coordinator_helpers import _owners
    import time
    _replace(twins, T1, "title: First", "title: Named slow")
    owner = _owners[twins.native.resolve()]
    monkeypatch.setattr(resync, "TOOL_BUDGET", 1)
    monkeypatch.setattr(owner, "checkpoint",
                        lambda stage: time.sleep(1.2) if stage == "sync_files_selected" else None)
    with twins.at(twins.native):
        pending = bs.backlog_sync(files=[T1])
    sync_id = _sync_id(pending)
    assert f'backlog_sync(sync_id="{sync_id}", files=["{T1}"])' in pending, pending


def test_an_invalid_file_is_refused_by_name(twins):
    with twins.at(twins.native):
        answer = bs.backlog_sync(files=["not-a-projection.txt"])
    assert answer.startswith("Error:"), answer


def test_legacy_store_answers_a_clear_no_op(twins):
    with twins.at(twins.legacy):
        answer = bs.backlog_sync()
    assert "legacy store" in answer and "Nothing was changed" in answer, answer
    assert not answer.startswith("Error"), answer


def test_the_tool_budget_leaves_room_under_a_30_second_client_timeout():
    from taskmaster.coordinator.sync_worker import FINISH_TIMEOUT
    assert resync.TOOL_BUDGET + FINISH_TIMEOUT <= resync.REPLY_TARGET < 30
