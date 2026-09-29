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
    """The counts of an ended sync; they always add up to the files it selected."""
    head = re.search(r"(\d+) file\(s\) — ([^;.\n]+)", answer)
    assert head, answer
    counts = dict.fromkeys(("imported", "repaired", "unchanged", "conflicts", "pending", "not checked"), 0)
    counts.update({label: int(number) for number, label in re.findall(r"(\d+) ([a-z ]+?)(?:,|$)", head.group(2))})
    counts["selected"] = int(head.group(1))
    assert sum(value for key, value in counts.items() if key != "selected") == counts["selected"], answer
    return counts


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


# ── Re-review (N17): faithful replays, no silent resume, honest counts, hard deadline ──

def _conflicted(twins):
    _replace(twins.native, T1, "title: First", "title: File title")
    bs.backlog_update_task(task_id="test-epic-001", field="title", value="Store title")


def test_a_replayed_unsynchronized_sync_is_rendered_as_it_ended(twins):
    from tests.native_coordinator_helpers import close_owned
    with twins.at(twins.native):
        _conflicted(twins)
        ended = _poll(bs.backlog_sync())[-1]
        ident = _sync_id(ended)
        again = bs.backlog_sync(sync_id=ident)
    assert ended.startswith(f"Sync finished without synchronizing every file (sync id {ident}):"), ended
    prefix = f"Sync finished without synchronizing every file (sync id {ident}) — the stored result of a sync"
    assert again.startswith(prefix) and "Sync complete" not in again, again
    assert f'backlog_resolve_conflict(file="{T1}")' in again, again
    close_owned()  # the same answer from the store's job record
    with twins.at(twins.native):
        stored = bs.backlog_sync(sync_id=ident)
    assert stored.startswith(prefix) and f'backlog_resolve_conflict(file="{T1}")' in stored, stored


def test_a_failed_sync_replays_as_failed(twins, monkeypatch):
    def broken(**arguments):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(_owner(twins), "sync", broken)
    with twins.at(twins.native):
        failed = _poll(bs.backlog_sync())[-1]
        again = bs.backlog_sync(sync_id=_sync_id(failed))
    assert failed.startswith("Sync failed") and "injected failure" in failed, failed
    assert again.startswith(f"Sync failed (sync id {_sync_id(failed)}) — the stored result"), again


def test_an_unfinished_sync_is_reported_never_resumed_and_names_the_running_one(twins, monkeypatch):
    owner = _owner(twins)
    ghost = sync_jobs.Job("20260101T000000Z-0badc0de", None)
    sync_jobs._write(owner, ghost, dict(state="running", files=None, started_at=ghost.started_at))
    with twins.at(twins.native):
        alone = bs.backlog_sync(sync_id=ghost.id)
    assert alone.startswith(f"Sync {ghost.id} did not finish") and "not resumed" in alone, alone
    assert "call backlog_sync() to start a fresh sync" in alone.lower(), alone
    assert sync_jobs.running(owner) is None  # nothing was re-run
    _new_tasks(twins.native, 3)
    _slow_imports(monkeypatch, owner, 0.4)
    monkeypatch.setattr(resync, "TOOL_WAIT", 0.6)
    with twins.at(twins.native):
        started = bs.backlog_sync()
        ghost_again = bs.backlog_sync(sync_id=ghost.id)
        running = [job for job in owner.sync_jobs.values() if not job.done.is_set()]
        _poll(started)
    assert len(running) == 1, running
    assert f'backlog_sync(sync_id="{_sync_id(started)}")' in ghost_again, ghost_again


def test_files_never_reached_are_not_counted_unchanged(twins, monkeypatch):
    owner = _owner(twins)
    monkeypatch.setattr(sync_jobs, "MAX_ROUNDS", 1)
    monkeypatch.setattr(sync_jobs, "ROUND_BUDGET", 1)
    monkeypatch.setattr(owner, "checkpoint", lambda stage: time.sleep(1.2) if stage == "sync_files_selected" else None)
    _replace(twins.native, T1, "title: First", "title: never reached")
    with twins.at(twins.native):
        ended = _poll(bs.backlog_sync())[-1]
    counts = _counted(ended)
    assert counts["unchanged"] == 0 and counts["imported"] == 0, ended
    assert counts["pending"] + counts["not checked"] == counts["selected"], ended


def test_a_running_job_keeps_the_coordinator_busy_between_rounds(twins, monkeypatch):
    owner = _owner(twins)
    seen, real = [], owner.sync

    def sync(**arguments):
        result = real(**arguments)
        seen.append((sync_jobs.running(owner) is not None, owner.idle_expired(0)))
        if len(seen) == 1:
            return dict(result, state="pending", notices=["sync pending: forced; retry the same sync id"])
        return result
    monkeypatch.setattr(owner, "sync", sync)
    monkeypatch.setattr(sync_jobs, "RETRY_PAUSE", 0)
    with twins.at(twins.native):
        ended = _poll(bs.backlog_sync())[-1]
    assert ended.startswith("Sync complete"), ended
    assert seen == [(True, False), (True, False)], seen


class _NoChild:
    def poll(self):
        return None


def test_the_call_deadline_is_hard_on_a_slow_probe_and_a_start_that_never_publishes(twins, monkeypatch):
    from taskmaster.coordinator import client as client_module
    from taskmaster.coordinator.protocol import ServiceUnavailable
    monkeypatch.setattr(client_module.Client, "_probe", lambda self: (time.sleep(1.5), None)[1])
    monkeypatch.setattr(client_module, "_launch", lambda root: _NoChild())

    def missing(self):
        raise FileNotFoundError("no discovery")
    monkeypatch.setattr(client_module.Client, "_discovery", missing)
    client = client_module.Client(twins.native)
    started = time.monotonic()
    with pytest.raises(ServiceUnavailable):
        client.sync_job(deadline=started + 2)
    assert time.monotonic() - started <= 2.3


def test_the_call_deadline_is_hard_while_another_thread_holds_the_start_lock(twins, monkeypatch):
    import threading
    from taskmaster.coordinator import client as client_module
    from taskmaster.coordinator.protocol import ServiceUnavailable
    monkeypatch.setattr(client_module.Client, "_probe", lambda self: None)
    release = threading.Event()
    holder = threading.Thread(target=lambda: (client_module._START_LOCK.acquire(), release.wait(5),
                                              client_module._START_LOCK.release()))
    holder.start()
    time.sleep(0.1)
    try:
        client = client_module.Client(twins.native)
        started = time.monotonic()
        with pytest.raises(ServiceUnavailable):
            client.sync_job(deadline=started + 1)
        assert time.monotonic() - started <= 1.3
    finally:
        release.set()
        holder.join()


def test_the_call_deadline_bounds_the_wait_for_a_successor_coordinator(twins, monkeypatch):
    from taskmaster.coordinator import client as client_module
    client = client_module.Client(twins.native)
    record = {"nonce": "n" * 48}
    monkeypatch.setattr(client_module, "ownership_held", lambda root: True)
    monkeypatch.setattr(client_module.Client, "_discovery", lambda self: dict(record))
    started = time.monotonic()
    client._deadline = started + 0.5
    client._await_successor(record, started + 60)  # the handshake's own budget is far longer
    assert time.monotonic() - started <= 0.8


# ── N17 integrated review ──────────────────────────────────────────────────


def test_queued_merge_stamps_never_hold_backlog_sync_or_a_read(twins, monkeypatch):
    """Replaying queued merge stamps inline added the coordinator's 30 s client timeout (and a
    retry) to every native call, breaking backlog_sync's 15 s bound. backlog_sync never
    replays; other calls replay in the background, one replay per project at a time, and
    the stamps are still applied."""
    import threading

    from taskmaster.native_routing import merge_stamps, registry

    backlog = twins.native / ".taskmaster"
    merge_stamps.enqueue(backlog, {"task_id": "test-epic-001", "rung": "master", "sha": "abc1234"})
    started, finished = [], threading.Event()

    def slow_replay(*args, **kwargs):
        started.append(kwargs.get("autostart"))
        time.sleep(3)
        finished.set()
        return {}

    monkeypatch.setattr(merge_stamps, "replay", slow_replay)
    with twins.at(twins.native):
        began = time.monotonic()
        answer = bs.backlog_sync()
        assert time.monotonic() - began < resync.TOOL_WAIT + 1, answer
        assert started == [], "backlog_sync must never replay merge stamps"
        for _ in range(2):
            began = time.monotonic()
            bs.backlog_get_task(task_id="test-epic-001")
            assert time.monotonic() - began < 2.0
        assert registry.wait_for_merge_stamp_replays(10)
    assert started == [False], started  # one replay, never starting a coordinator
    assert finished.is_set()


def test_idle_expiry_stops_in_the_same_guard_so_no_sync_is_admitted_after_it(twins):
    from taskmaster.coordinator.protocol import CoordinatorStopping

    owner = _owner(twins)
    assert owner.idle_expired(0)
    assert owner.stopping.is_set(), "an expired owner must be stopping before the guard is released"
    with pytest.raises(CoordinatorStopping):
        sync_jobs.request(owner)


def test_a_finished_job_refreshes_activity_before_it_reports_done(twins, monkeypatch):
    owner = _owner(twins)
    seen = []
    real_event = sync_jobs.threading.Event

    class Watched(real_event().__class__):
        def set(self):
            seen.append(owner.last_activity)
            super().set()

    monkeypatch.setattr(sync_jobs.threading, "Event", Watched)
    finished = []
    real_finish = sync_jobs.Job.finish

    def finish(self, owner_, state):
        real_finish(self, owner_, state)
        finished.append(time.monotonic())

    monkeypatch.setattr(sync_jobs.Job, "finish", finish)
    with twins.at(twins.native):
        _poll(bs.backlog_sync())
    assert finished and seen and seen[-1] >= finished[-1], (seen, finished)


def test_a_handshake_refusal_is_rendered_with_its_own_guidance(twins, monkeypatch):
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.protocol import HandshakeError

    refusal = ("a newer taskmaster build (7.1.0) runs this repository's coordinator; this client (7.0.0) will "
               "not downgrade it; restart this session to load the updated plugin")

    def refuse(self, **kwargs):
        raise HandshakeError(refusal, may_have_committed=False)

    monkeypatch.setattr(Client, "sync_job", refuse)
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert "restart this session" in answer, answer
    assert "did not answer" not in answer and "call backlog_sync() again" not in answer, answer
