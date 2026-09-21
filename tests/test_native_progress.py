"""User intent: native completions may carry a session changelog again (N11 S7-S9):
every paragraph is a row that survives any crash until PROGRESS.md carries it, the
~200 paragraphs a legacy store already wrote are copied into native once and never
lost, the dashboard is refreshed at most every 5 s, and the result is byte-identical
to what the legacy store writes for the same calls.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native import commands
from taskmaster.native import projection as outbox
from taskmaster.native_routing import progress, reads, runtime
from native_twins import commit_only, make_twins, native_connection, native_database

LOG_BEGIN, LOG_END = "<!-- taskmaster:session-log -->", "<!-- /taskmaster:session-log -->"


class Crash(BaseException):
    """An injected crash: BaseException, so no handler on the way out can swallow it."""


class FakeTime:
    def __init__(self, step=0.0):
        self.at, self.step = 2_000_000.0, step

    def clock(self):
        now = self.at
        self.at += self.step
        return now

    def sleep(self, seconds):
        self.at += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setitem(progress.HOOKS, "clock", fake.clock)
    monkeypatch.setitem(progress.HOOKS, "sleep", fake.sleep)
    return fake


def _seed():
    for n, title in enumerate(("First", "Second", "Third"), start=1):
        bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
        # The shortest lane, satisfied, so completion is legal.
        bs.backlog_update_task(task_id=f"test-epic-00{n}", field="lane", value="express")
        bs.backlog_record_gate(task_id=f"test-epic-00{n}", gate="review-gate", verdict="pass")
    bs.backlog_pick_task(task_id="test-epic-001")


LEGACY_PARAGRAPH = "### 2026-09-17 — Legacy session"
QUEUED_PARAGRAPH = "### queued before activation\n- still pending"


def _seed_with_history():
    """A legacy store that has already written a session log, plus one paragraph it
    committed but never exported: the state D5's seed must carry into native."""
    _seed()
    bs.backlog_complete_task(task_id="test-epic-001", session_title="Legacy session", done="- legacy work")
    bs.backlog_pick_task(task_id="test-epic-002")
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db", isolation_level=None)) as connection:
        connection.execute("INSERT INTO meta(key,value) VALUES('pending_progress_log',?)",
                           (json.dumps([{"ts": "", "text": QUEUED_PARAGRAPH}]),))
    store.reset_for_tests()


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


@pytest.fixture
def history(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_with_history)


def _progress_path(root):
    return root / ".taskmaster" / "local" / "PROGRESS.md"


def _progress(root) -> str:
    return _progress_path(root).read_text(encoding="utf-8")


def _pending(root) -> list[tuple[str, dict]]:
    with native_connection(root) as connection:
        return [(key, json.loads(value)) for key, value in connection.execute(
            "SELECT key,value_json FROM sync_state WHERE key LIKE 'progress.pending.%' ORDER BY key")]


def _sync(root, key):
    with native_connection(root) as connection:
        row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def _applied(root) -> list[str]:
    return [entry["text"] for entry in (_sync(root, "progress.applied") or [])]


def _put(root, key, value):
    with native_connection(root) as connection:
        connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                           "value_json=excluded.value_json", (key, json.dumps(value)))


def _meta(root) -> dict:
    with native_connection(root) as connection:
        return dict(connection.execute("SELECT key,value FROM meta"))


def _region(text: str) -> str:
    begin, end = text.find(LOG_BEGIN), text.find(LOG_END)
    return text[begin:end] if begin != -1 and end > begin else ""


def _touch(root, value="touch"):
    return bs.backlog_update_task(task_id="test-epic-003", field="notes", value=value)


# ── S7: rows, applied log, seed ─────────────────────────────────────────────


def _complete_many(root, count):
    """`count` completions with a changelog, committed without any export drain,
    returning the `sync_state` diff each one made."""
    diffs = []
    with native_connection(root) as connection:
        for n in range(count):
            ident = commit_only(connection, "task.create", {"title": f"Bulk {n}", "epic": "test-epic",
                                                            "phase": "dev"})["affected"][0]["id"]
            commit_only(connection, "task.pick", {"id": ident, "session": "tests"})
            commit_only(connection, "task.update", {"id": ident, "field": "lane", "value": "express"})
            commit_only(connection, "task.gate", {"id": ident, "gate": "review-gate", "verdict": "pass"})
            before = dict(connection.execute("SELECT key,value_json FROM sync_state"))
            commit_only(connection, "task.complete", {"id": ident, "changelog": f"### Bulk {n}\n- shipped"})
            after = dict(connection.execute("SELECT key,value_json FROM sync_state"))
            diffs.append(({k for k in after if k not in before},
                          {k for k in after if k in before and after[k] != before[k]},
                          {k for k in before if k not in after}))
    return diffs


def test_each_completion_adds_one_row_and_never_rewrites_the_queue(twins):
    """The plan's N11 note: the old queue re-read and re-wrote one JSON list per
    completion, O(pending) each and O(n^2) over a store's life. Now a completion adds
    exactly one row holding only its own paragraph and touches no existing row."""
    diffs = _complete_many(twins.native, 30)
    for n, (added, changed, removed) in enumerate(diffs):
        progress_added = {k for k in added if k.startswith("progress.")}
        assert len(progress_added) == 1 and not any(k.startswith("progress.") for k in changed | removed), (n, diffs[n])
        assert "pending_progress_log" not in added | changed
    rows = _pending(twins.native)
    assert [entry["text"] for _key, entry in rows] == [f"### Bulk {n}\n- shipped" for n in range(30)]
    assert all(set(entry) == {"ts", "text"} for _key, entry in rows)
    # One key per paragraph, ordered by commit (§2.8).
    assert all(re.fullmatch(r"progress\.pending\.\d{12}\.\d{4}", key) for key, _entry in rows)


def test_a_retried_completion_logs_its_paragraph_once(twins):
    with native_connection(twins.native) as connection:
        store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        envelope = {"protocol": 2, "store_id": store_id, "caller_scope": bs.SESSION_ID, "request_id": "retry-me",
                    "operation": "task.complete",
                    "arguments": {"id": "test-epic-001", "changelog": "### Once\n- only once"},
                    "expected_revisions": []}
        first = commands.execute(connection, envelope)
        second = commands.execute(connection, envelope)
    assert first["commit_seq"] == second["commit_seq"]
    assert [entry["text"] for _key, entry in _pending(twins.native)] == ["### Once\n- only once"]


def test_the_cap_trims_only_written_paragraphs(twins, fake_time):
    _put(twins.native, "progress.seeded", {"seq": 0})
    _put(twins.native, "progress.applied", [{"ts": "", "text": f"old {n:03d}"} for n in range(199)])
    for n in range(5):
        _put(twins.native, f"progress.pending.{1:012d}.{n:04d}", {"ts": "", "text": f"new {n}"})
    with twins.at(twins.native):
        answer = _touch(twins.native)
    assert "export pending" not in answer, answer
    region = _region(_progress(twins.native))
    assert all(f"new {n}" in region for n in range(5))
    assert all(f"old {n:03d}" not in region for n in range(4)) and all(
        f"old {n:03d}" in region for n in range(4, 199))
    applied = _applied(twins.native)
    assert len(applied) == 200 and applied[-5:] == [f"new {n}" for n in range(5)]
    assert _pending(twins.native) == []


def test_more_pending_paragraphs_than_the_cap_are_all_written(twins, fake_time):
    _put(twins.native, "progress.seeded", {"seq": 0})
    _put(twins.native, "progress.applied", [{"ts": "", "text": f"old {n:03d}"} for n in range(200)])
    for n in range(250):
        _put(twins.native, f"progress.pending.{1:012d}.{n:04d}", {"ts": "", "text": f"new {n:03d}"})
    with twins.at(twins.native):
        _touch(twins.native)
    region = _region(_progress(twins.native))
    assert all(f"new {n:03d}" in region for n in range(250)) and "old " not in region
    assert _applied(twins.native) == [f"new {n:03d}" for n in range(250)]


def test_the_legacy_session_log_is_seeded_once_and_meta_is_never_written(history, fake_time):
    root = history.native
    before_file, before_meta = _progress(root), _meta(root)
    assert LEGACY_PARAGRAPH in before_file
    with history.at(root):
        answer = _touch(root)
    assert "export pending" not in answer, answer
    text = _progress(root)
    region = _region(text)
    # The legacy paragraph survived the first native render, and the paragraph the
    # legacy store had only queued is written too, newest first.
    assert region.count(LEGACY_PARAGRAPH) == 1 and region.count(QUEUED_PARAGRAPH) == 1
    assert region.index(QUEUED_PARAGRAPH) < region.index(LEGACY_PARAGRAPH)
    assert _meta(root) == before_meta
    with native_connection(root) as connection:
        assert connection.execute("SELECT value FROM native_manifest WHERE key='state'").fetchone()[0] == "ready"
    assert _sync(root, "progress.seeded") is not None
    applied = _applied(root)
    assert applied[-1] == QUEUED_PARAGRAPH and any(LEGACY_PARAGRAPH in t for t in applied)
    fake_time.at += 60
    with history.at(root):
        _touch(root, "again")
    assert _applied(root) == applied
    assert _region(_progress(root)).count(LEGACY_PARAGRAPH) == 1


def test_a_seed_interrupted_before_its_commit_seeds_nothing_then_seeds_once(history, fake_time, monkeypatch):
    root = history.native

    def checkpoint(stage):
        if stage == "progress_seeded":
            raise Crash("seed")

    monkeypatch.setitem(progress.HOOKS, "checkpoint", checkpoint)
    with history.at(root), pytest.raises(Crash):
        _touch(root)
    assert _sync(root, "progress.seeded") is None and _sync(root, "progress.applied") is None
    assert _pending(root) == []
    monkeypatch.setitem(progress.HOOKS, "checkpoint", None)
    with history.at(root):
        _touch(root, "retry")
    assert _applied(root).count(QUEUED_PARAGRAPH) == 1
    assert _region(_progress(root)).count(LEGACY_PARAGRAPH) == 1


def test_the_pre_n11_native_queue_is_written_before_newer_rows_then_dropped(twins, fake_time):
    _put(twins.native, "pending_progress_log", [{"ts": "", "text": "older, from the list"}])
    _put(twins.native, f"progress.pending.{10**6:012d}.0000", {"ts": "", "text": "newer, a row"})
    with twins.at(twins.native):
        _touch(twins.native)
    region = _region(_progress(twins.native))
    # Newest first in the file.
    assert region.index("newer, a row") < region.index("older, from the list")
    assert _sync(twins.native, "pending_progress_log") is None
    assert _applied(twins.native)[-2:] == ["older, from the list", "newer, a row"]


# ── S8: the render ──────────────────────────────────────────────────────────

CHILD = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from taskmaster.native_routing import progress, runtime
    root, stage, operation, arguments = sys.argv[1:5]

    def checkpoint(at):
        if at == stage:
            os._exit(17)

    progress.HOOKS["checkpoint"] = checkpoint
    backlog_dir = Path(root) / ".taskmaster"
    with runtime.open_call(backlog_dir / "local" / "store.db", backlog_dir, "child") as call:
        call.execute(operation, json.loads(arguments))
    os._exit(0)
""")

PARAGRAPH = "### Crash session\n**Done:**\n- survived"


@pytest.mark.parametrize("variant", ("exception", "exit"))
def test_a_crash_between_the_write_and_the_move_rewrites_the_identical_file(twins, fake_time, monkeypatch,
                                                                            variant):
    """§5.2 PROGRESS row: the file already carries the paragraph, the row is still
    pending; the next render writes the identical file and no duplicate."""
    root = twins.native
    arguments = {"id": "test-epic-001", "changelog": PARAGRAPH}
    if variant == "exit":
        done = subprocess.run([sys.executable, "-c", CHILD, str(root), "progress_written", "task.complete",
                               json.dumps(arguments)], capture_output=True, text=True, timeout=120)
        assert done.returncode == 17, (done.returncode, done.stderr[-2000:])
        import time
        fake_time.at = time.time()  # the child leased on the real clock
        # The dead writer's lease is still live: a caller in that window waits, then
        # reports the paragraph pending rather than blocking.
        with native_connection(root) as connection:
            assert progress.export(connection, root / ".taskmaster", "early") == [progress.NOTICE]
        fake_time.at += progress.LEASE_SECONDS + 1
    else:
        def checkpoint(stage):
            if stage == "progress_written":
                raise Crash("after write")

        monkeypatch.setitem(progress.HOOKS, "checkpoint", checkpoint)
        with runtime.open_call(native_database(root), root / ".taskmaster", "parent") as call, \
                pytest.raises(Crash):
            call.execute("task.complete", arguments)
        monkeypatch.setitem(progress.HOOKS, "checkpoint", None)
    crashed = _progress_path(root).read_bytes()
    assert crashed.decode("utf-8").count("- survived") == 1
    assert [entry["text"] for _key, entry in _pending(root)] == [PARAGRAPH]
    with native_connection(root) as connection:
        assert progress.export(connection, root / ".taskmaster", "recovery") == []
    assert _progress_path(root).read_bytes() == crashed
    assert _pending(root) == [] and _applied(root) == [PARAGRAPH]
    assert [p.name for p in _progress_path(root).parent.glob("PROGRESS.md.tmp.*")] == []


def test_a_crash_inside_the_move_rolls_it_back_as_a_unit(twins, fake_time, monkeypatch):
    root = twins.native

    def checkpoint(stage):
        if stage == "progress_applying":
            raise Crash("during move")

    monkeypatch.setitem(progress.HOOKS, "checkpoint", checkpoint)
    with runtime.open_call(native_database(root), root / ".taskmaster", "parent") as call, pytest.raises(Crash):
        call.execute("task.complete", {"id": "test-epic-001", "changelog": PARAGRAPH})
    monkeypatch.setitem(progress.HOOKS, "checkpoint", None)
    assert [entry["text"] for _key, entry in _pending(root)] == [PARAGRAPH] and _applied(root) == []
    crashed = _progress_path(root).read_bytes()
    with native_connection(root) as connection:
        assert progress.export(connection, root / ".taskmaster", "recovery") == []
    assert _progress_path(root).read_bytes() == crashed and _applied(root) == [PARAGRAPH]


def test_text_outside_the_markers_is_preserved(twins, fake_time):
    root = twins.native
    _progress_path(root).write_text(
        "# Old dashboard\n\n## Changelog\n\nHand-written note above.\n\n" + LOG_BEGIN + "\n" + LOG_END +
        "\n\n### 2025-01-01 — Pre-store history\n- kept\n", encoding="utf-8")
    with twins.at(root):
        bs.backlog_complete_task(task_id="test-epic-001", session_title="Kept", done="- marker test")
    text = _progress(root)
    assert "Hand-written note above." in text and "### 2025-01-01 — Pre-store history\n- kept" in text
    assert "- marker test" in _region(text) and "# Old dashboard" not in text


def test_a_failed_write_keeps_the_rows_and_warns(twins, fake_time, monkeypatch):
    root = twins.native
    before = _progress(root)

    def checkpoint(stage):
        if stage == "progress_temp_written":
            raise PermissionError(13, "sharing violation")

    monkeypatch.setitem(progress.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        answer = bs.backlog_complete_task(task_id="test-epic-001", session_title="Blocked", done="- kept safe")
    assert progress.NOTICE in answer, answer
    assert answer.startswith("Completed `test-epic-001`")
    assert _progress(root) == before
    assert [entry["text"] for _key, entry in _pending(root)][0].startswith("### ")
    assert [p.name for p in _progress_path(root).parent.glob("PROGRESS.md.tmp.*")] == []
    monkeypatch.setitem(progress.HOOKS, "checkpoint", None)
    with twins.at(root):
        answer = _touch(root)
    assert progress.NOTICE not in answer and "- kept safe" in _region(_progress(root))
    assert _pending(root) == []


def test_the_dashboard_is_rendered_at_most_every_five_seconds(twins, fake_time, monkeypatch):
    """D6: the dashboard is the one full-tree read a normal native command makes,
    named here: `reads.tree`, at most once per 5 s unless a paragraph is pending."""
    root = twins.native
    reads_made = []
    real_tree = reads.tree
    monkeypatch.setattr(reads, "tree", lambda *a, **k: reads_made.append(1) or real_tree(*a, **k))
    with twins.at(root):
        _touch(root, "one")
        assert len(reads_made) == 1
        _touch(root, "two")
        fake_time.at += 4.9
        _touch(root, "three")
        assert len(reads_made) == 1
        fake_time.at += 0.2
        _touch(root, "four")
        assert len(reads_made) == 2
        # A pending paragraph is never throttled.
        bs.backlog_complete_task(task_id="test-epic-001", session_title="Unthrottled", done="- now")
        assert len(reads_made) == 3
    assert "- now" in _region(_progress(root))


def test_a_live_writer_makes_a_caller_wait_then_report_its_paragraph_pending(twins, fake_time):
    root = twins.native
    _put(root, "progress.writer", {"owner": "elsewhere", "generation": 4, "until": fake_time.at + 30})
    with twins.at(root):
        answer = bs.backlog_complete_task(task_id="test-epic-001", session_title="Waits", done="- queued")
    assert progress.NOTICE in answer, answer
    assert len(_pending(root)) == 1
    fake_time.at += 31
    with twins.at(root):
        answer = _touch(root)
    assert progress.NOTICE not in answer and "- queued" in _region(_progress(root))


# ── S9: the lifted refusal, compared with legacy ────────────────────────────


@pytest.fixture
def twin_clock(monkeypatch):
    """Both twins re-render the dashboard on every call: legacy because each call
    opens a fresh store, native because its clock moves 10 s per reading."""
    fake = FakeTime(step=10.0)
    monkeypatch.setitem(progress.HOOKS, "clock", fake.clock)
    monkeypatch.setitem(progress.HOOKS, "sleep", fake.sleep)
    return fake


def _same_progress(twins):
    assert _progress(twins.native) == _progress(twins.legacy)


def test_a_session_changelog_completion_matches_legacy_byte_for_byte(twins, twin_clock):
    twins.same("backlog_update_task", task_id="test-epic-002", field="status", value="in-progress")
    _same_progress(twins)
    twins.same("backlog_complete_task", task_id="test-epic-001", session_title="Twin session",
               done="shipped one\n- shipped two", decisions="kept both", issues="",
               tasks_touched="test-epic-001")
    _same_progress(twins)
    twins.same("backlog_pick_task", task_id="test-epic-003")
    twins.same("backlog_complete_task", task_id="test-epic-003", done="- stats: 3 files", auto_summary=True,
               tasks_touched="test-epic-003")
    _same_progress(twins)
    twins.same("backlog_complete_task", task_id="test-epic-002", done="only done")
    _same_progress(twins)
    twins.assert_state_matches()
    twins.assert_files_match()


def test_a_legacy_history_renders_identically_after_activation(history, twin_clock):
    history.same("backlog_complete_task", task_id="test-epic-002", session_title="After cutover",
                 done="- native paragraph")
    _same_progress(history)
    assert _region(_progress(history.native)).count(LEGACY_PARAGRAPH) == 1


# ── Flag notices and exporter liveness on native results ────────────────────


def test_a_native_flag_is_named_on_every_result(twins):
    root = twins.native
    path = root / ".taskmaster" / "tasks" / "test-epic-002.md"
    path.write_bytes(path.read_bytes() + b"\nHand edit.\n")
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-002", field="notes", value="store side")
        from taskmaster.native_routing.conflicts import flag_notice
        notice = flag_notice({"file": "tasks/test-epic-002.md", "kind": "task", "id": "test-epic-002"})
        assert notice.startswith(store.projection_conflict_notice(
            {"file": "tasks/test-epic-002.md", "kind": "task", "id": "test-epic-002"})[:120])
        assert 'take="file"' not in notice and 'take="store"' in notice
        answer = bs.backlog_get_task(task_id="test-epic-001")
        assert notice in answer, answer
        assert answer.count(notice) == 1
        assert notice in bs.backlog_status()
        payload = json.loads(bs.backlog_changes_since(since_seq=0, limit=1))
        assert notice in payload.get("projection_conflicts", []), payload


def test_no_flag_notice_when_nothing_is_flagged(twins):
    with twins.at(twins.native):
        assert "was edited while the store held" not in bs.backlog_get_task(task_id="test-epic-001")


def _status_line(root) -> str:
    report = bs.backlog_store_status()
    lines = [line for line in report.splitlines() if line.startswith("Exporter lease:")]
    assert len(lines) == 1, report
    return lines[0]


def test_store_status_says_whether_the_exporter_lease_holder_is_alive(twins):
    root = twins.native
    with twins.at(root):
        assert _status_line(root).startswith("Exporter lease: free")
    import time
    host = socket.gethostname()
    _put(root, outbox.EXPORTER_KEY, {"owner": f"peer:abc:{os.getpid()}@{host}", "generation": 3,
                                     "until": time.time() + 25})
    with twins.at(root):
        line = _status_line(root)
    assert "generation 3" in line and "holder alive" in line, line
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True, timeout=60)
    dead = int(finished.stdout.strip())
    _put(root, outbox.EXPORTER_KEY, {"owner": f"peer:abc:{dead}@{host}", "generation": 4,
                                     "until": time.time() + 25})
    with twins.at(root):
        line = _status_line(root)
    assert "holder not running" in line and "generation 4" in line, line
    _put(root, outbox.EXPORTER_KEY, {"owner": "elsewhere", "generation": 5, "until": time.time() + 25})
    with twins.at(root):
        assert "holder unknown" in _status_line(root)
    _put(root, outbox.EXPORTER_KEY, {"owner": "elsewhere", "generation": 5, "until": time.time() - 5})
    with twins.at(root):
        assert _status_line(root).startswith("Exporter lease: free")


def test_the_drain_names_its_process_in_the_lease(twins):
    with twins.at(twins.native):
        _touch(twins.native)
    with native_connection(twins.native) as connection:
        lease = outbox.lease(connection)
    assert lease["owner"].endswith(f":{os.getpid()}@{socket.gethostname()}"), lease
