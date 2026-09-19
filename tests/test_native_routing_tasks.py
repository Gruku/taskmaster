"""User intent: the task lifecycle family served by the native core (N08) must answer,
refuse, commit and project exactly as the legacy task tools do — creation, field
edits, claims, completion, archive, gates, merges and spec review — and the reads
an agent relies on (get/list/pipeline/dependencies/next) must say the same thing.
"""
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, make_twins, normalize


def _seed_tasks():
    bs.backlog_add_phase(phase_id="later", name="Later Work")
    bs.backlog_add_epic(epic_id="other", name="Other Epic", done_when="never",
                        description="Second epic")
    bs.backlog_add_task(title="First task", epic="test-epic", phase="dev", notes="Initial notes")
    bs.backlog_add_task(title="Second task", epic="test-epic", phase="dev", priority="high")
    bs.backlog_add_task(title="Third task", epic="other", phase="later", depends_on="test-epic-001")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_tasks)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


# ── Creation ────────────────────────────────────────────────────────────────


def test_add_task_matches_including_options_and_ordering(twins):
    twins.same("backlog_add_task", title="Created natively", epic="test-epic", phase="dev",
               tldr="Short", next_step="Start", depends_on="test-epic-001, test-epic-002",
               bundle="my-bundle",
               options={"docs": "plan:docs/p.md;bogus:x;spec:docs/s.md", "sub_repo": "api",
                        "stage": "3", "estimate": "M", "anchors": "src/a.py, https://x.test"})
    twins.same("backlog_add_task", title="Explicit id", epic="other", phase="later",
               options={"task_id": "custom-id-1"})
    _check(twins)


@pytest.mark.parametrize("kwargs", [
    {"title": "X", "epic": "missing", "phase": "dev"},
    {"title": "X", "epic": "test-epic", "phase": ""},
    {"title": "X", "epic": "test-epic", "phase": "absent-phase"},
    {"title": "X", "epic": "test-epic", "phase": "dev", "priority": "urgent"},
    {"title": "X", "epic": "test-epic", "phase": "dev", "depends_on": "ghost-001"},
    {"title": "X", "epic": "test-epic", "phase": "dev", "bundle": "Bad Slug"},
    {"title": "X", "epic": "test-epic", "phase": "dev", "options": {"stage": "three"}},
    {"title": "X", "epic": "test-epic", "phase": "dev", "options": {"area": "nowhere"}},
    {"title": "X", "epic": "test-epic", "phase": "dev", "options": {"task_id": "test-epic-001"}},
])
def test_add_task_refusals_match(twins, kwargs):
    twins.same("backlog_add_task", **kwargs)
    _check(twins)


def test_add_task_resolves_a_phase_by_name_and_legacy_priority_codes(twins):
    twins.same("backlog_add_task", title="Named phase", epic="test-epic", phase="Later Work", priority="P0")
    _check(twins)


def test_add_task_bundle_sub_repo_conflict_matches(twins):
    twins.same("backlog_add_task", title="A", epic="test-epic", phase="dev", bundle="pair",
               options={"sub_repo": "one"})
    twins.same("backlog_add_task", title="B", epic="other", phase="dev", bundle="pair",
               options={"sub_repo": "two"})
    _check(twins)


# ── Field edits ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field,value", [
    ("title", "Renamed"), ("priority", "P1"), ("priority", "bogus"), ("notes", "Mentions other-001 and test-epic-002"),
    ("docs", "plan:docs/plan.md"), ("docs", "nocolon"), ("docs", "wrong:x"),
    ("depends_on", "test-epic-002, test-epic-003"), ("depends_on", "ghost-9"),
    ("stage", "4"), ("stage", "four"), ("locked_by", "someone"), ("locked_by", "none"),
    ("phase", "later"), ("phase", "ghost"), ("phase", ""), ("anchors", "a/b.py, c/*.py"), ("anchors", ""),
    ("patchnote", "Shipped it"), ("release", "none"), ("blast_radius_depth", "deep"),
    ("blast_radius_depth", "sideways"), ("tldr", ""), ("tldr", "New tldr"), ("next_step", "Go"),
    ("lane", "express"), ("lane", "warp"), ("component", "ui"), ("component", ""),
    ("design_change", "true"), ("design_change", "no"), ("bundle", "Bad Slug"), ("bundle", "solo"),
    ("area", "nowhere"), ("area", ""), ("human_action", "press the button"), ("branch", "feature/x"),
    ("status", "in-progress"), ("status", "flying"), ("status", "in-review"),
    ("not_allowed", "x"), ("", ""),
])
def test_update_task_single_field_matches(twins, field, value):
    twins.same("backlog_update_task", task_id="test-epic-001", field=field, value=value)
    _check(twins)


def test_archiving_through_a_status_edit_also_stamps_the_archive_time_natively(twins):
    """Recorded N08 difference: the legacy tool flips only the store row's archive
    flag, while the native core derives that flag from the document, so a native
    status edit to `archived` also stamps `archived: <now>` (as `backlog_archive_task`
    always has). Answer, file placement and every other field still match."""
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="archived")
    legacy, native = committed(twins.legacy), committed(twins.native)
    key = ("task", "test-epic-001")
    assert "archived" not in legacy[key][0] and native[key][0]["archived"]
    assert legacy[key][2] is native[key][2] is True
    strip = lambda doc: {k: v for k, v in doc.items() if k != "archived"}
    assert normalize(strip(native[key][0])) == normalize(strip(legacy[key][0]))
    assert (twins.native / ".taskmaster" / "tasks" / "archive" / "test-epic-001.md").exists()
    assert (twins.legacy / ".taskmaster" / "tasks" / "archive" / "test-epic-001.md").exists()


def test_update_task_keyword_style_and_its_refusals_match(twins):
    twins.same("backlog_update_task", task_id="test-epic-002", tldr="Keyword tldr", next_step="Keyword next")
    twins.same("backlog_update_task", task_id="test-epic-002", tldr="x", field="title", value="y")
    twins.same("backlog_update_task", task_id="ghost-1", tldr="x")
    twins.same("backlog_update_task", task_id="ghost-1", field="title", value="x")
    _check(twins)


def test_lane_transition_table_is_enforced_the_same_way(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="lane", value="standard")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="done")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="in-progress")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="done")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="todo")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="archived")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="todo")
    _check(twins)


# ── Claims ──────────────────────────────────────────────────────────────────


def test_pick_repick_and_refusals_match(twins):
    twins.same("backlog_pick_task", task_id="other-001")
    twins.same("backlog_pick_task", task_id="other-001")
    twins.same("backlog_pick_task", task_id="ghost-1")
    twins.same("backlog_update_task", task_id="test-epic-002", field="locked_by", value="someone-else")
    twins.same("backlog_update_task", task_id="test-epic-002", field="status", value="in-progress")
    twins.same("backlog_pick_task", task_id="test-epic-002")
    twins.same("backlog_pick_task", task_id="test-epic-002", force=True)
    _check(twins)


def test_bundle_pick_matches(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="bundle", value="duo")
    twins.same("backlog_update_task", task_id="test-epic-002", field="bundle", value="duo")
    twins.same("backlog_pick_task", task_id="test-epic-002")
    twins.same("backlog_pick_task", task_id="test-epic-001")
    _check(twins)


# ── Completion, archive and gates ───────────────────────────────────────────


def test_gates_pipeline_and_completion_match(twins):
    twins.same("backlog_pick_task", task_id="test-epic-002")
    twins.same("backlog_task_pipeline", task_id="test-epic-002")
    twins.same("backlog_complete_task", task_id="test-epic-002")
    twins.same("backlog_record_gate", task_id="test-epic-002", gate="review-gate", verdict="pass")
    twins.same("backlog_record_gate", task_id="test-epic-002", gate="spec", status="done", commit_sha="abc1234")
    twins.same("backlog_record_gate", task_id="test-epic-002", gate="spec", status="pending")
    twins.same("backlog_record_gate", task_id="test-epic-002", gate="nope", verdict="pass")
    twins.same("backlog_record_gate", task_id="test-epic-002", gate="spec-review", verdict="maybe")
    twins.same("backlog_skip_gate", task_id="test-epic-002", gate="spec-review", reason="  trivial  ", by="user")
    twins.same("backlog_skip_gate", task_id="test-epic-002", gate="plan-review", reason=" ")
    twins.same("backlog_clear_gate", task_id="test-epic-002", gate="design-review")
    twins.same("backlog_task_pipeline", task_id="test-epic-002")
    for gate in ("spec", "plan", "tests", "impl", "plan-review", "review-gate"):
        kwargs = {"verdict": "pass"} if gate.endswith(("review", "gate")) else {"status": "done"}
        twins.same("backlog_record_gate", task_id="test-epic-002", gate=gate, **kwargs)
    twins.same("backlog_task_pipeline", task_id="test-epic-002")
    twins.same("backlog_complete_task", task_id="test-epic-002", patchnote="Now works", release="alpha")
    twins.same("backlog_complete_task", task_id="test-epic-002")
    twins.same("backlog_task_pipeline", task_id="test-epic-001")
    _check(twins)


def test_in_review_completion_and_archive_match(twins):
    twins.same("backlog_pick_task", task_id="test-epic-001")
    twins.same("backlog_complete_task", task_id="test-epic-001", target_status="in-review")
    twins.same("backlog_complete_task", task_id="test-epic-001", target_status="sideways")
    twins.same("backlog_complete_task", task_id="test-epic-001", target_status="in-review",
               human_action="approve the plan")
    twins.same("backlog_complete_task", task_id="test-epic-001")
    twins.same("backlog_archive_task", task_id="test-epic-001", reason="nonsense")
    twins.same("backlog_archive_task", task_id="test-epic-002")
    twins.same("backlog_archive_task", task_id="test-epic-002", reason="wont-fix")
    twins.same("backlog_archive_task", task_id="test-epic-001")
    twins.same("backlog_archive_task", task_id="ghost-1")
    _check(twins)


def test_merge_and_spec_review_match(twins):
    twins.same("backlog_record_merge", task_id="test-epic-001", rung="develop", sha="0123456789abcdef")
    twins.same("backlog_record_merge", task_id="test-epic-001", rung=" ", sha="x")
    twins.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="")
    twins.same("backlog_set_spec_review", task_id="test-epic-001", verdict="warn", spec_path="docs/s.md",
               codex_used=True, critical_count=1, important_count=2)
    twins.same("backlog_set_spec_review", task_id="test-epic-001", verdict="meh", spec_path="docs/s.md")
    twins.same("backlog_clear_spec_review", task_id="test-epic-001")
    twins.same("backlog_clear_spec_review", task_id="test-epic-001")
    _check(twins)


def test_completion_with_a_session_changelog_is_refused_on_a_native_store(twins):
    """N08 constraint: native completion would queue the paragraph into a key no
    exporter drains until N11, silently dropping it from PROGRESS.md; queuing it
    in the legacy row as well would render it twice once N11 lands. The adapter
    therefore refuses before any mutation, and says why."""
    with twins.at(twins.native):
        bs.backlog_pick_task(task_id="test-epic-001")
    before = committed(twins.native)
    with twins.at(twins.native):
        for kwargs in ({"session_title": "Session"}, {"done": "- a thing"}, {"auto_summary": True}):
            answer = bs.backlog_complete_task(task_id="test-epic-001", **kwargs)
            assert answer.startswith("Error:") and "PROGRESS.md" in answer, answer
    assert committed(twins.native) == before


# ── Reads ───────────────────────────────────────────────────────────────────


def _seed_rich():
    """Mixed statuses, priorities, orders, locks and active epics, so every sort,
    filter and label in the read tools has something to discriminate."""
    _seed_tasks()
    for epic in ("test-epic", "other"):
        bs.backlog_update_epic(epic_id=epic, field="status", value="active")
    bs.backlog_add_task(title="Fourth, critical", epic="test-epic", phase="dev", priority="critical")
    bs.backlog_add_task(title="Fifth, low", epic="test-epic", phase="dev", priority="low",
                        depends_on="test-epic-002")
    bs.backlog_add_task(title="Sixth, later phase", epic="test-epic", phase="later", priority="high")
    bs.backlog_update_task(task_id="test-epic-004", field="lane", value="express")
    bs.backlog_pick_task(task_id="test-epic-004")
    bs.backlog_record_gate(task_id="test-epic-004", gate="review-gate", verdict="pass")
    bs.backlog_complete_task(task_id="test-epic-004")
    bs.backlog_pick_task(task_id="test-epic-002")
    bs.backlog_update_task(task_id="test-epic-001", field="status", value="blocked")
    bs.backlog_update_task(task_id="test-epic-001", field="blockers", value="waiting on infra")
    bs.backlog_update_task(task_id="test-epic-001", field="review_instructions", value="Check it")
    bs.backlog_update_task(task_id="test-epic-001", field="docs", value="plan:docs/plan.md")
    bs.backlog_update_task(task_id="test-epic-005", field="human_action", value="wait")
    bs.backlog_add_task(title="Medium tie A", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Medium tie B", epic="test-epic", phase="dev")
    bs.backlog_handover_create(tldr="Open work on the second task", task_ids=["test-epic-002"])


@pytest.fixture
def rich(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_rich)


def test_get_task_views_match(rich):
    for task_id in ("test-epic-001", "test-epic-002", "test-epic-005", "other-001"):
        for kwargs in ({}, {"verbose": True}, {"expand_links": True}, {"verbose": True, "expand_links": True},
                       {"sections": ["notes", "review_instructions"]}, {"sections": []}, {"sections": ["bogus"]}):
            rich.same("backlog_get_task", task_id=task_id, **kwargs)
    rich.same("backlog_get_task", task_id="ghost-1")


@pytest.mark.parametrize("kwargs", [
    {}, {"verbose": True}, {"epic": "other"}, {"status": "todo"}, {"priority": "high"},
    {"phase": "later"}, {"area": "x"}, {"limit": 2}, {"limit": 0}, {"status": "done"},
    {"epic": "ghost"},
])
def test_list_tasks_matches(rich, kwargs):
    rich.same("backlog_list_tasks", **kwargs)


def test_dependencies_and_next_available_match(rich):
    for task_id in ("other-001", "test-epic-001", "test-epic-002", "test-epic-005", "ghost-1"):
        rich.same("backlog_dependencies", task_id=task_id)
    rich.same("backlog_next_available")
    rich.same("backlog_next_available", include_future_phases=True)
    rich.same("backlog_pick_task", task_id="test-epic-003")
    rich.same("backlog_complete_task", task_id="test-epic-002", target_status="in-review", human_action="sign")
