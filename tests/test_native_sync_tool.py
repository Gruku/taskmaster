"""User intent: `backlog_sync` is the explicit MCP recourse when a hand edit to `.taskmaster/`
files has not taken effect on a native store (normal commands never scan projection files).
It starts a sync the coordinator drives to the end on its own; each call waits a bounded time
(clients have fixed tool timeouts) and later calls attach by id. Answers are honest and compact:
counts for the whole sync, conflicts named once with next steps, stored results labelled as
such, one sync at a time, and a clear no-op on a legacy store.
"""
from __future__ import annotations

import re
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import sync_jobs
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


def _replace(root, rel, old, new):
    path = root / ".taskmaster" / rel
    content = path.read_bytes()
    assert old.encode() in content
    path.write_bytes(content.replace(old.encode(), new.encode()))


def _new_tasks(root, count, start=3):
    """`count` new task files, written by hand: each is one import."""
    text = (root / ".taskmaster" / T1).read_text(encoding="utf-8")
    for number in range(start, start + count):
        ident = f"test-epic-{number:03d}"
        (root / ".taskmaster" / f"tasks/{ident}.md").write_text(
            text.replace("test-epic-001", ident).replace("title: First", f"title: Hand {number}"), encoding="utf-8")


def _title(root, ident):
    with native_connection(root) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get("task", ident)["fields"]["title"]


def _sync_id(answer):
    match = re.search(r"sync id ([0-9TZ]+-[0-9a-f]+)\)", answer)
    assert match, answer
    return match.group(1)


def _owner(twins):
    from tests.native_coordinator_helpers import _owners
    return _owners[twins.native.resolve()]


def _slow_imports(monkeypatch, owner, seconds):
    monkeypatch.setattr(owner, "checkpoint", lambda stage: time.sleep(seconds) if stage == "sync_prepared" else None)


def _poll(first, limit=60):
    """Every answer from the first until the sync is no longer running."""
    answers = [first]
    while answers[-1].startswith("Sync running") and len(answers) < limit:
        answers.append(bs.backlog_sync(sync_id=_sync_id(first)))
    return answers


def _counted(answer):
    numbers = re.search(r"(\d+) file\(s\) checked — (\d+) imported, (\d+) repaired, (\d+) unchanged, (\d+) conflicts",
                        answer)
    assert numbers, answer
    selected, *parts = map(int, numbers.groups())
    assert sum(parts) == selected, answer
    return dict(zip(("selected", "imported", "repaired", "unchanged", "conflicts"), [selected, *parts]))


def test_sync_imports_a_hand_edit_with_counts_and_a_seq(twins):
    _replace(twins.native, T1, "title: First", "title: Edited by hand")
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete"), answer
    assert _counted(answer)["imported"] == 1, answer
    assert f"Imported: {T1}" in answer, answer
    assert re.search(r"\[seq \d+\]$", answer), answer
    assert _title(twins.native, "test-epic-001") == "Edited by hand"


def test_sync_with_nothing_edited_imports_nothing(twins):
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete") and _counted(answer)["imported"] == 0, answer
    assert "[seq" not in answer, answer


def test_sync_merges_edits_to_different_fields(twins):
    with (twins.native / ".taskmaster" / T1).open("a", encoding="utf-8") as handle:
        handle.write("\nHand edit.\n")
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store change")
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete") and _counted(answer)["imported"] == 1, answer


def test_sync_names_a_conflict_once_with_the_resolver_as_next_step(twins):
    # The file and the store changed the same field: nothing is merged, the file is flagged.
    _replace(twins.native, T1, "title: First", "title: File title")
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="title", value="Store title")
        answer = bs.backlog_sync()
    assert answer.startswith("Sync finished without synchronizing every file"), answer
    assert "1 conflict(s)" in answer and _counted(answer)["conflicts"] == 1, answer
    assert answer.count(f'backlog_resolve_conflict(file="{T1}")') == 1, answer
    assert "Warning:" not in answer, answer
    assert _title(twins.native, "test-epic-001") == "Store title"


def test_sync_names_a_quarantined_file(twins):
    _replace(twins.native, T2, "title: Second", "title: [unclosed")
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync finished without synchronizing every file"), answer
    assert "Quarantined" in answer and T2 in answer and "backlog_resolve_conflict" in answer, answer
    assert _title(twins.native, "test-epic-002") == "Second"


def test_a_running_sync_is_polled_by_id_to_completion(twins, monkeypatch):
    _new_tasks(twins.native, 5)
    _slow_imports(monkeypatch, _owner(twins), 0.3)
    monkeypatch.setattr(resync, "TOOL_WAIT", 0.6)
    with twins.at(twins.native):
        answers = _poll(bs.backlog_sync())
    assert answers[0].startswith("Sync running") and "Call backlog_sync(sync_id=" in answers[0], answers[0]
    assert answers[-1].startswith(f"Sync complete (sync id {_sync_id(answers[0])})"), answers
    assert _counted(answers[-1])["imported"] == 5, answers[-1]
    assert _title(twins.native, "test-epic-007") == "Hand 7"


def test_the_coordinator_continues_across_rounds_with_cumulative_counts(twins, monkeypatch):
    # Each round's budget covers only some imports; the job runs further rounds itself.
    _new_tasks(twins.native, 6)
    _slow_imports(monkeypatch, _owner(twins), 0.3)
    monkeypatch.setattr(sync_jobs, "ROUND_BUDGET", 1.5)
    monkeypatch.setattr(sync_jobs, "RETRY_PAUSE", 0)
    with twins.at(twins.native):
        answers = _poll(bs.backlog_sync())
    assert answers[-1].startswith("Sync complete"), answers
    counts = _counted(answers[-1])
    assert counts["imported"] == 6 and counts["selected"] == 9, answers[-1]  # + backlog.yaml
    job = _owner(twins).sync_jobs[_sync_id(answers[0])]
    assert job.totals["rounds"] > 1, job.totals


def test_a_second_caller_attaches_to_the_running_sync(twins, monkeypatch):
    _new_tasks(twins.native, 4)
    _slow_imports(monkeypatch, _owner(twins), 0.4)
    monkeypatch.setattr(resync, "TOOL_WAIT", 0.6)
    with twins.at(twins.native):
        first = bs.backlog_sync()
        other = bs.backlog_sync()
        named = bs.backlog_sync(files=[T1])
        answers = _poll(first)
    assert first.startswith("Sync running"), first
    assert _sync_id(other) == _sync_id(first) and "attached to it" in other, other
    assert _sync_id(named) == _sync_id(first) and "yours was not started" in named, named
    assert len(_owner(twins).sync_jobs) == 2  # the fixture's first sync and this one
    assert answers[-1].startswith("Sync complete") and _counted(answers[-1])["imported"] == 4, answers[-1]


def test_a_finished_sync_id_replays_its_stored_result_labelled(twins):
    from tests.native_coordinator_helpers import close_owned
    _replace(twins.native, T1, "title: First", "title: Replayed edit")
    with twins.at(twins.native):
        done = bs.backlog_sync()
        ident = _sync_id(done)
        again = bs.backlog_sync(sync_id=ident)
    assert done.startswith(f"Sync complete (sync id {ident}):"), done
    assert again.startswith(f"Sync complete (sync id {ident}) — the stored result of a sync that finished at 20"), again
    assert "need a fresh backlog_sync()" in again and "[seq" not in again, again
    assert _counted(again)["imported"] == 1, again
    close_owned()  # a new coordinator knows the sync only from the store
    with twins.at(twins.native):
        stored = bs.backlog_sync(sync_id=ident)
    assert stored.startswith(f"Sync complete (sync id {ident}) — the stored result"), stored
    assert _counted(stored)["imported"] == 1, stored


def test_ids_the_tool_did_not_issue_are_refused(twins):
    with twins.at(twins.native):
        foreign = bs.backlog_sync(sync_id="my-sync")
        unknown = bs.backlog_sync(sync_id="20260101T000000Z-00000000")
    assert foreign.startswith("Error:") and "not a sync id backlog_sync issued" in foreign, foreign
    assert unknown.startswith("Error:") and "start a fresh backlog_sync()" in unknown, unknown


def test_a_sync_id_with_different_files_is_refused_with_guidance(twins):
    with twins.at(twins.native):
        ident = _sync_id(bs.backlog_sync(files=[T1]))
        answer = bs.backlog_sync(sync_id=ident, files=[T2])
    assert answer.startswith("Error:") and "fresh backlog_sync(files=...)" in answer, answer


def test_named_files_sync_only_those_files(twins):
    _replace(twins.native, T1, "title: First", "title: Named edit")
    _replace(twins.native, T2, "title: Second", "title: Unnamed edit")
    with twins.at(twins.native):
        answer = bs.backlog_sync(files=[".taskmaster/" + T1])
    assert answer.startswith("Sync complete") and _counted(answer)["selected"] == 1, answer
    assert _title(twins.native, "test-epic-001") == "Named edit"
    assert _title(twins.native, "test-epic-002") == "Second"


def test_long_lists_are_truncated_but_counted_in_full(twins, monkeypatch):
    monkeypatch.setattr(resync, "_SHOWN", 2)
    _new_tasks(twins.native, 5)
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete"), answer
    assert _counted(answer)["imported"] == 5, answer
    assert "(3 further results not listed)" in answer and "Not synchronized" not in answer, answer


def test_an_invalid_file_is_refused_by_name(twins):
    with twins.at(twins.native):
        answer = bs.backlog_sync(files=["not-a-projection.txt"])
    assert answer.startswith("Error:"), answer


def test_legacy_store_answers_a_clear_no_op(twins):
    with twins.at(twins.legacy):
        answer = bs.backlog_sync()
    assert "legacy store" in answer and "Nothing was changed" in answer, answer
    assert not answer.startswith("Error"), answer


def test_one_call_stays_well_under_a_30_second_client_timeout():
    assert resync.TOOL_WAIT <= 15


@pytest.mark.real_service_process
@pytest.mark.xdist_group("heavy_processes")
def test_real_coordinator_sync_outlasts_the_call_and_polling_completes(tmp_path, monkeypatch):
    """A real coordinator process: its sync takes longer than one call may wait; polling by id
    reaches "complete" with the whole sync's counts, and no call overruns its deadline."""
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.protocol import ServiceUnavailable
    twins = make_twins(tmp_path, monkeypatch, _seed, visibility=None)
    root = twins.native
    wait = 1.0
    monkeypatch.setattr(resync, "TOOL_WAIT", wait)
    _new_tasks(root, 150)
    timings, answers = [], []
    try:
        with twins.at(root):
            ident = ""
            give_up = time.monotonic() + 300
            while time.monotonic() < give_up:
                started = time.monotonic()
                answer = bs.backlog_sync(sync_id=ident)
                timings.append(time.monotonic() - started)
                answers.append(answer)
                if answer.startswith(("Sync complete", "Sync finished", "Sync failed", "Error")):
                    break
                if not ident and "sync id" in answer:
                    ident = _sync_id(answer)
    finally:
        try:
            Client(root, autostart=False, timeout=5).shutdown()
        except ServiceUnavailable:
            pass
        time.sleep(1.0)
    assert answers[-1].startswith("Sync complete"), answers[-3:]
    assert any(answer.startswith("Sync running") for answer in answers), answers[:3]
    counts = _counted(answers[-1])
    assert counts["imported"] == 150 and counts["selected"] == 153, answers[-1]  # + backlog.yaml
    assert max(timings) <= wait + 0.5, timings
    assert _title(root, "test-epic-152") == "Hand 152"
