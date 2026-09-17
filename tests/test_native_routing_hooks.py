"""User intent: the three store-reading hooks (edit resurfacing, the merge gate and the
merge recorder) must give a native-authority project the same answers as its legacy
twin, from live native rows — never from the frozen legacy tables or project.yaml —
so activating a project cannot silently mute resurfacing or open the merge gate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
    answers = {"rev-parse --abbrev-ref HEAD": "qa-line", "rev-parse HEAD": "f00dcafe"}
    monkeypatch.setattr(hook, "_git", lambda args, cwd: answers[" ".join(args)])
    for root in (twins.legacy, twins.native):
        monkeypatch.setenv("TASKMASTER_ROOT", str(root))
        with twins.at(root):
            hook.stamp("feature/late", root)
    legacy, native = committed(twins.legacy), committed(twins.native)
    assert legacy[("task", "test-epic-003")][0]["merge_status"]["qa"]["merge_commit"] == "f00dcafe"
    twins.assert_state_matches()
    twins.assert_files_match()
    assert native[("task", "test-epic-003")][0]["merge_gate_state"] == "qa"
