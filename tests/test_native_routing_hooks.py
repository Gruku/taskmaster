"""User intent: the three store-reading hooks (edit resurfacing, the merge gate and the
merge recorder) must give a native-authority project the same answers as its legacy
twin, from live native rows — never from the frozen legacy tables or project.yaml —
so activating a project cannot silently mute resurfacing or open the merge gate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

import pytest
import yaml

from taskmaster import backlog_server as bs
from native_twins import committed, make_twins

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
MANIFEST = {
    "schema_version": 1,
    "meta": {"name": "Hooks", "slug": "hooks", "kind": "app"},
    "conventions": {"policies": {"review_gate_required_for_merge": True,
                                 "merge_targets": [{"label": "qa", "branches": ["qa-line"]}]}},
}


def _module(name):
    spec = importlib.util.spec_from_file_location(f"native_hook_{name}", HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed():
    bs.backlog_add_task(title="Anchored task", epic="test-epic", phase="dev", options={"anchors": "api/model.py"})
    bs.backlog_add_task(title="Gated task", epic="test-epic", phase="dev")
    bs.backlog_update_task(task_id="test-epic-002", field="branch", value="feature/seeded")
    bs.backlog_bug_create(title="Seeded bug", found_in="test-epic-001", location=["api/model.py"])


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed)
    # Everything below lands after activation: a native read of the frozen legacy
    # tables, or of project.yaml, would miss it.
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(MANIFEST))
    twins.same("backlog_add_task", title="Late task", epic="test-epic", phase="dev",
               options={"anchors": "api/**"})
    twins.same("backlog_update_task", task_id="test-epic-003", field="branch", value="feature/late")
    twins.same("backlog_issue_create", title="Late issue", severity="P2", evidence="x",
               location=["api/model.py"], related_tasks=["test-epic-001"])
    twins.same("backlog_bug_update", bug_id="B-001", field="status", value="shelved")
    return twins


def _database(root):
    return root / ".taskmaster" / "local" / "store.db"


def _shape(result):
    return ([(e.kind, e.id, e.status) for e in result.listed], result.closed, result.prose, result.related)


def test_edit_resurface_answers_from_live_native_rows(twins):
    hook = _module("edit_resurface")
    for rel in ("api/model.py", "api/other.py", "web/none.py"):
        legacy = hook.resolve(_database(twins.legacy), rel)
        native = hook.resolve(_database(twins.native), rel)
        assert _shape(native) == _shape(legacy), rel
        assert hook.format_line(rel, native) == hook.format_line(rel, legacy)
    assert "ISS-001" in hook.format_line("api/model.py", hook.resolve(_database(twins.native), "api/model.py"))


def test_edit_resurface_revision_advances_with_native_commits(twins):
    hook = _module("edit_resurface")
    before = hook.max_change_seq(_database(twins.native))
    twins.same("backlog_bug_update", bug_id="B-001", field="status", value="open")
    assert hook.max_change_seq(_database(twins.native)) > before
    assert _shape(hook.resolve(_database(twins.native), "api/model.py")) == \
        _shape(hook.resolve(_database(twins.legacy), "api/model.py"))


def test_merge_gate_decides_from_live_native_rows(twins):
    hook = _module("merge_gate_decide")
    verdicts = []

    def both(src):
        legacy = hook.decide_from_store(_database(twins.legacy), src, twins.legacy)
        native = hook.decide_from_store(_database(twins.native), src, twins.native)
        assert native == legacy, src
        verdicts.append(native)

    both("feature/late")          # no gate yet: BLOCK
    twins.same("backlog_update_task", task_id="test-epic-003", field="lane", value="express")
    twins.same("backlog_record_gate", task_id="test-epic-003", gate="impl", status="done")
    assert not twins.same("backlog_record_gate", task_id="test-epic-003", gate="review-gate",
                          verdict="fail")[1].startswith("Error")
    both("feature/late")          # failing gate: BLOCK
    twins.same("backlog_record_gate", task_id="test-epic-003", gate="review-gate", verdict="pass")
    both("feature/late")          # passing gate outside git: ALLOW
    both("feature/seeded")
    both("feature/untracked")
    assert [v.split(":")[0] for v in verdicts] == ["BLOCK", "BLOCK", "ALLOW", "BLOCK", "ALLOW"]
    assert verdicts[0].startswith("BLOCK:test-epic-003")


def test_merge_gate_policy_comes_from_the_native_project_row(twins):
    hook = _module("merge_gate_decide")
    off = dict(MANIFEST, conventions={"policies": {"review_gate_required_for_merge": False}})
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(off))
    for root in (twins.legacy, twins.native):
        assert hook.decide_from_store(_database(root), "feature/late", root) == "ALLOW"


def test_merge_recorder_stamps_the_live_native_task_on_the_native_ladder(twins, monkeypatch):
    hook = _module("merge_recorder_stamp")
    assert hook.task_id_for_branch(_database(twins.native), "feature/late") == "test-epic-003"
    assert hook.task_id_for_branch(_database(twins.native), "feature/late") == \
        hook.task_id_for_branch(_database(twins.legacy), "feature/late")
    for root in (twins.legacy, twins.native):
        monkeypatch.setenv("TASKMASTER_ROOT", str(root))
        with twins.at(root):
            hook.stamp("feature/late", root, "qa-line", "f00dcafe")
    legacy, native = committed(twins.legacy), committed(twins.native)
    assert legacy[("task", "test-epic-003")][0]["merge_status"]["qa"]["merge_commit"] == "f00dcafe"
    twins.assert_state_matches()
    twins.assert_files_match()
    assert native[("task", "test-epic-003")][0]["merge_gate_state"] == "qa"


def test_merge_recorder_stamps_when_the_native_ladder_read_fails(twins, monkeypatch):
    """A merge landing while the server holds the writer is ordinary, so a busy-timeout
    on the ladder read must cost the rung label, not the whole stamp. The legacy ladder
    read falls back to the default targets; the native one must keep that posture."""
    hook = _module("merge_recorder_stamp")
    from taskmaster.native_routing import tasks as native_tasks

    real = native_tasks._merge_targets
    calls = []

    def locked(snapshot):
        # Only the hook's own ladder read times out; the recorder's read behind it
        # succeeds, as it would when the writer is released a moment later.
        calls.append(snapshot)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(snapshot)

    monkeypatch.setattr(native_tasks, "_merge_targets", locked)
    monkeypatch.setenv("TASKMASTER_ROOT", str(twins.native))
    with twins.at(twins.native):
        hook.stamp("feature/late", twins.native, "qa-line", "f00dcafe")
    stamped = committed(twins.native)[("task", "test-epic-003")][0]["merge_status"]
    assert stamped["qa"]["merge_commit"] == "f00dcafe", stamped
    log = (twins.native / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "merge_recorder_stamp" in log and "database is locked" in log, log


def test_merge_recorder_never_starts_a_coordinator_and_queues_the_stamp(twins, monkeypatch):
    """A hook must not bootstrap: a coordinator started from a hook's interpreter would
    be reused by the MCP server. With none running, the stamp is queued durably, logged,
    and applied by the server's next native call, which may start one."""
    import json

    from taskmaster.coordinator import client as client_module
    from tests.native_coordinator_helpers import close_owned

    hook = _module("merge_recorder_stamp")
    close_owned()
    monkeypatch.setattr(client_module, "_launch", lambda root: pytest.fail("a hook started a coordinator"))
    monkeypatch.setenv("TASKMASTER_ROOT", str(twins.native))
    hook.stamp("feature/late", twins.native, "qa-line", "f00dcafe")

    pending = twins.native / ".taskmaster" / "local" / "merge-stamps-pending.jsonl"
    entry = json.loads(pending.read_text(encoding="utf-8").splitlines()[0])
    assert (entry["task_id"], entry["rung"], entry["sha"]) == ("test-epic-003", "qa", "f00dcafe")
    log = (twins.native / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "queued" in log and "f00dcafe" in log, log
    assert "qa" not in (committed(twins.native)[("task", "test-epic-003")][0].get("merge_status") or {})

    with twins.at(twins.native):
        bs.backlog_get_task(task_id="test-epic-003")
    stamped = committed(twins.native)[("task", "test-epic-003")][0]
    assert stamped["merge_status"]["qa"]["merge_commit"] == "f00dcafe", stamped
    assert stamped["merge_status"]["qa"]["merged_at"] == entry["merged_at"]
    assert not pending.exists()


def test_a_queued_stamp_older_than_the_recorded_one_is_dropped(twins, monkeypatch):
    import json

    from tests.native_coordinator_helpers import close_owned

    hook = _module("merge_recorder_stamp")
    close_owned()
    monkeypatch.setenv("TASKMASTER_ROOT", str(twins.native))
    hook.stamp("feature/late", twins.native, "qa-line", "0ld5ha")
    pending = twins.native / ".taskmaster" / "local" / "merge-stamps-pending.jsonl"
    queued = json.loads(pending.read_text(encoding="utf-8").splitlines()[0])
    later = dict(queued, merged_at="2999-01-01T00:00")  # recorded afterwards, by hand
    with twins.at(twins.native):
        pending.rename(pending.with_suffix(".hold"))
        bs.backlog_record_merge(task_id="test-epic-003", rung="qa", sha="n3wsha", merged_at=later["merged_at"])
        pending.with_suffix(".hold").rename(pending)
        bs.backlog_get_task(task_id="test-epic-003")
    stamped = committed(twins.native)[("task", "test-epic-003")][0]["merge_status"]["qa"]
    assert stamped["merge_commit"] == "n3wsha", stamped
    assert not pending.exists()


def test_a_poisoned_queue_neither_wedges_nor_costs_the_current_stamp(twins, monkeypatch):
    """One unparseable queue line used to escape replay before the claim was released,
    wedging the queue for good and losing the merge being stamped."""
    from taskmaster.native_routing import merge_stamps

    hook = _module("merge_recorder_stamp")
    local = twins.native / ".taskmaster" / "local"
    (local / merge_stamps.PENDING).write_text('{"task_id": "test-epic-003", "rung": "mas', encoding="utf-8")
    monkeypatch.setenv("TASKMASTER_ROOT", str(twins.native))
    with twins.at(twins.native):
        hook.stamp("feature/late", twins.native, "qa-line", "f00dcafe")
    stamped = committed(twins.native)[("task", "test-epic-003")][0]["merge_status"]
    assert stamped["qa"]["merge_commit"] == "f00dcafe", stamped
    assert not merge_stamps.has_pending(twins.native / ".taskmaster")
    assert "unreadable entry" in (local / merge_stamps.REJECTED).read_text(encoding="utf-8")


def test_a_replay_failure_does_not_cost_the_current_stamp(twins, monkeypatch):
    from taskmaster.native_routing import merge_stamps

    hook = _module("merge_recorder_stamp")

    def broken(*a, **k):
        raise RuntimeError("replay exploded")

    monkeypatch.setattr(merge_stamps, "replay", broken)
    monkeypatch.setenv("TASKMASTER_ROOT", str(twins.native))
    with twins.at(twins.native):
        hook.stamp("feature/late", twins.native, "qa-line", "f00dcafe")
    stamped = committed(twins.native)[("task", "test-epic-003")][0]["merge_status"]
    assert stamped["qa"]["merge_commit"] == "f00dcafe", stamped
    log = (twins.native / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "replay exploded" in log, log


def test_the_server_replays_only_without_starting_a_coordinator(twins, monkeypatch):
    """A read must not wait up to 15 s for a coordinator started only to replay."""
    from taskmaster.native_routing import merge_stamps

    merge_stamps.enqueue(twins.native / ".taskmaster", {"task_id": "test-epic-003", "rung": "qa", "sha": "abc"})
    calls = []
    monkeypatch.setattr(merge_stamps, "replay", lambda *a, **k: calls.append(k.get("autostart")) or {})
    with twins.at(twins.native):
        bs.backlog_get_task(task_id="test-epic-003")
    assert calls == [False]


def test_a_stamp_decided_on_stale_state_is_redecided_not_forced(twins, monkeypatch):
    """The replay writes with the snapshot's task revision as the expected revision, so a
    merge recorded between its read and its write makes it decide again, and the newer
    record survives."""
    from taskmaster.coordinator import adapter
    from taskmaster.native_routing import merge_stamps

    backlog = twins.native / ".taskmaster"
    merge_stamps.enqueue(backlog, {"task_id": "test-epic-003", "rung": "qa", "sha": "0ld5ha",
                                   "merged_at": "2026-01-01T00:00"})
    real = adapter.NativeCall.execute
    raced = []

    def execute(self, operation, arguments, *, expected=None):
        if operation == "task.merge" and arguments["sha"] == "0ld5ha" and not raced:
            raced.append(expected)
            real(self, operation, dict(arguments, sha="n3wsha", merged_at="2999-01-01T00:00"))
        return real(self, operation, arguments, expected=expected)

    monkeypatch.setattr(adapter.NativeCall, "execute", execute)
    with twins.at(twins.native):
        merge_stamps.replay(backlog, backlog / "local" / "store.db", "test", autostart=False)
    assert raced and raced[0] and raced[0][0]["kind"] == "task"
    stamped = committed(twins.native)[("task", "test-epic-003")][0]["merge_status"]["qa"]
    assert stamped["merge_commit"] == "n3wsha", stamped
    assert not merge_stamps.has_pending(backlog)


# ── N14: the edit hook's related count from the canonical neighbourhood ──

def _both(twins, hook, rels):
    for rel in rels:
        legacy = hook.resolve(_database(twins.legacy), rel)
        native = hook.resolve(_database(twins.native), rel)
        assert _shape(native) == _shape(legacy), rel
        yield rel, _shape(native)


def test_native_related_open_work_is_counted_not_named(twins):
    """Path and handover neighbours of listed work count once each, open ones only."""
    hook = _module("edit_resurface")
    twins.same("backlog_add_task", title="Path neighbour", epic="test-epic", phase="dev",
               options={"anchors": "api/other.py"})           # matched by test-epic-003's api/**
    twins.same("backlog_add_task", title="Closed neighbour", epic="test-epic", phase="dev",
               options={"anchors": "api/third.py"})
    twins.same("backlog_update_task", task_id="test-epic-005", field="status", value="archived")
    twins.same("backlog_handover_create", tldr="Pairing", task_ids=["test-epic-001", "test-epic-002", "test-epic-002"])
    answers = dict(_both(twins, hook, ["api/model.py"]))
    # test-epic-004 via path, test-epic-002 via handover (twice listed, counted once).
    assert answers["api/model.py"][3] == 2, answers
    line = hook.format_line("api/model.py", hook.resolve(_database(twins.native), "api/model.py"))
    assert "+2 related" in line and "test-epic-004" not in line and "test-epic-002" not in line


def test_native_edit_hook_reads_no_related_table(twins):
    """The native branch answers from the canonical neighbourhood, not the `related` table."""
    hook = _module("edit_resurface")
    twins.same("backlog_handover_create", tldr="Pairing", task_ids=["test-epic-001", "test-epic-002"])
    statements = []
    connection = hook._connect_ro(_database(twins.native))
    try:
        connection.set_trace_callback(statements.append)
        result = hook._resolve(connection, "api/model.py")
    finally:
        connection.close()
    assert result.related == 1
    assert not any("FROM related" in sql for sql in statements), statements


def test_edit_hook_answers_match_across_stores_over_seeded_edits(twins):
    import random
    rng = random.Random(1914)
    hook = _module("edit_resurface")
    rels = ["api/model.py", "api/other.py", "web/x.py", "docs/a.md"]
    anchors = ["api/model.py", "api/other.py", "api/*", "web/x.py", "web/", "docs/*.md", "*.py", "Api/model.py"]
    tasks = ["test-epic-001", "test-epic-002", "test-epic-003"]
    for step in range(16):
        action = rng.randrange(4)
        if action == 0:
            twins.same("backlog_add_task", title=f"Seeded {step}", epic="test-epic", phase="dev",
                       options={"anchors": ",".join(rng.sample(anchors, rng.randrange(1, 3)))})
            tasks.append(f"test-epic-{len(tasks) + 1:03d}")
        elif action == 1:
            twins.same("backlog_update_task", task_id=rng.choice(tasks), field="anchors",
                       value=",".join(rng.sample(anchors, rng.randrange(1, 3))))
        elif action == 2:
            twins.same("backlog_update_task", task_id=rng.choice(tasks), field="status",
                       value=rng.choice(["todo", "in-progress", "done"]))
        else:
            twins.same("backlog_handover_create", tldr=f"Seeded {step}",
                       task_ids=[rng.choice(tasks) for _ in range(rng.randrange(1, 4))])
        list(_both(twins, hook, rels))


def test_native_dedupe_reprints_only_when_the_related_count_changes(twins):
    """Dedupe on a native store, with the neighbourhood read in the subprocess hook."""
    import json
    import os
    import subprocess
    import sys
    root = twins.native
    (root / "api").mkdir(exist_ok=True)
    (root / "api" / "model.py").write_text("x", encoding="utf-8")
    # The native branch must run as the hook does in production: without yaml.
    blocker = root.parent / "blocked"
    blocker.mkdir(exist_ok=True)
    for name in ("yaml", "fastmcp"):
        (blocker / f"{name}.py").write_text("raise ImportError('blocked for the hook test')", encoding="utf-8")
    env = dict(os.environ, TASKMASTER_ROOT=str(root), PYTHONPATH=str(blocker))

    def run():
        payload = {"session_id": "s1", "cwd": str(root), "tool_name": "Edit",
                   "tool_input": {"file_path": str(root / "api" / "model.py")}, "tool_response": {"success": True}}
        done = subprocess.run([sys.executable, str(HOOKS / "edit_resurface.py")], input=json.dumps(payload), text=True,
                              encoding="utf-8", capture_output=True, cwd=str(root), env=env, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] if done.stdout else ""

    first = run()
    assert first.startswith("TM: api/model.py → ") and "related" not in first, first
    assert run() == ""
    twins.same("backlog_handover_create", tldr="Pairing", task_ids=["test-epic-001", "test-epic-002"])
    assert run().endswith(", +1 related)")
    assert run() == ""
    twins.same("backlog_update_task", task_id="test-epic-002", field="status", value="archived")
    assert run() == first
    assert run() == ""
