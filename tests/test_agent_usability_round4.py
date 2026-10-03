"""User intent: review fixes for the second agent tool-use batch (tm-audit-030). A task
dependency has one source of truth — the `depends_on` field every gate reads — so a link
tool must never report a dependency the gates ignore; link writes must never freeze links a
field derives; list edits must refuse what they cannot honestly do; and the thread board's
default answer must name the open lines of work it does not list. Both stores alike.
"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, hand_edit_entity, make_twins, native_connection
from test_agent_usability_round3 import _seed


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _blocking(twins, task_id):
    """`backlog_context`'s blockers for a task, the same on both stores."""
    legacy, native = twins.call("backlog_context", focus=task_id, scope="task")
    legacy, native = json.loads(legacy)["mandatory"], json.loads(native)["mandatory"]
    assert legacy == native
    return legacy


# ── 1. A task dependency is the depends_on field, whichever way it is made ──


@pytest.mark.parametrize("link_type,source,target", [
    ("depends_on", "providers-002", "providers-001"), ("blocks", "providers-001", "providers-002")])
def test_a_task_dependency_link_is_refused_with_the_field_to_use(twins, link_type, source, target):
    before = committed(twins.legacy)
    refused, _native = twins.same("backlog_link", action="create", source=source, target=target, type=link_type)
    assert refused.startswith("Error:") and "depends_on" in refused
    assert 'backlog_update_task(task_id="providers-002", field="depends_on", value="+providers-001")' in refused
    assert committed(twins.legacy) == before
    twins.assert_state_matches()


def test_removing_a_task_dependency_link_is_refused_the_same_way(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="providers-001")
    refused, _native = twins.same("backlog_link", action="remove", source="providers-002", target="providers-001",
                                  type="depends_on")
    assert refused.startswith("Error:") and 'value="-providers-001"' in refused
    assert not _blocking(twins, "providers-002")["clear"]


@pytest.mark.parametrize("how", ["replace", "add", "add_task"])
def test_a_dependency_made_any_supported_way_gates_the_task(twins, how):
    dependent = "providers-002"
    if how == "replace":
        twins.same("backlog_update_task", task_id=dependent, field="depends_on", value="providers-001")
    elif how == "add":
        twins.same("backlog_update_task", task_id=dependent, field="depends_on", value="+providers-001")
    else:
        twins.same("backlog_add_task", title="Depends on failover", epic="providers", phase="dev",
                   depends_on="providers-001")
        dependent = "providers-003"
    answer = _blocking(twins, dependent)
    assert answer["clear"] is False and "providers-001" in json.dumps(answer["blockers"])
    shown = json.loads(twins.same("backlog_link", action="query", source=dependent)[0])
    assert {"source": dependent, "target": "providers-001", "type": "depends_on"} in shown


# ── 2. Link writes never freeze the links a field derives ───────────────────


def test_a_link_onto_a_task_stores_only_that_link_and_later_dependencies_still_show(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on", value="providers-001")
    twins.same("backlog_link", action="create", source="IDEA-001", target="test-epic-001", type="relates_to")
    for side in (committed(twins.legacy), committed(twins.native)):
        assert side[("task", "test-epic-001")][0]["links"] == [{"type": "relates_to", "target": "IDEA-001"}]
    twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on", value="+providers-002")
    shown = json.loads(twins.same("backlog_link", action="query", source="test-epic-001")[0])
    assert {(edge["type"], edge["target"]) for edge in shown} == {
        ("depends_on", "providers-001"), ("depends_on", "providers-002"), ("relates_to", "IDEA-001")}


def _stored_asymmetric_pair(twins):
    """ISS-001 stores a relates_to IDEA-001 whose inverse IDEA-001 does not store."""
    with twins.at(twins.legacy):
        hand_edit_entity("issue", "ISS-001", lambda doc: doc.__setitem__(
            "links", [{"type": "relates_to", "target": "IDEA-001"}]))


def test_reconcile_reports_without_writing_unless_asked(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="providers-001")
    _stored_asymmetric_pair(twins)
    before = committed(twins.legacy)
    with twins.at(twins.legacy):
        report = json.loads(bs.backlog_link(action="reconcile"))
    assert report["fixed"] == 0 and report["written"] is False
    assert report["repairable"] == [{"source": "ISS-001", "target": "IDEA-001", "type": "relates_to",
                                     "missing_inverse": "relates_to"}]
    assert committed(twins.legacy) == before


def test_reconcile_repairs_only_stored_pairs_and_freezes_nothing(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="providers-001")
    _stored_asymmetric_pair(twins)
    before = committed(twins.legacy)
    with twins.at(twins.legacy):
        report = json.loads(bs.backlog_link(action="reconcile", write=True))
    assert report["fixed"] == 1 and report["written"] is True
    after = committed(twins.legacy)
    assert after[("idea", "IDEA-001")][0]["links"] == [{"type": "relates_to", "target": "ISS-001"}]
    changed = {key for key in after if after[key] != before.get(key)}
    assert changed == {("idea", "IDEA-001")}, changed  # no task gained a frozen links array


# ── 3. The board names the archived-only threads it does not list ──────────


def _many_threads(count):
    def seed():
        for n in range(count):
            bs.backlog_handover_create(tldr=f"Line of work {n:02d}", thread=f"line-{n:02d}",
                                       next_action=f"Step {n:02d}")
    return seed


def test_the_board_names_a_few_archived_only_threads(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _many_threads(32))
    board, _native = twins.same("backlog_thread_list")
    assert ("…2 more threads have only archived handovers (outside the 30-entry index): line-01, line-00"
            in board) and "include_archived=True" in board


def test_the_board_bounds_the_names_on_a_store_with_many(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _many_threads(37))
    board, _native = twins.same("backlog_thread_list")
    assert "…7 more threads have only archived handovers" in board
    assert "line-06, line-05, line-04, line-03, line-02 (+2 more)" in board and "line-00" not in board


# ── 4. Area anchors are not replaced or cleared by accident ─────────────────


@pytest.mark.parametrize("value", ["docs/**", "", "docs/**,more/**"])
def test_a_bare_anchor_value_is_refused_rather_than_replacing_the_list(twins, value):
    refused, _native = twins.same("backlog_area_update", area_id="pipeline", field="anchors", value=value)
    assert refused.startswith("Error:") and "+" in refused and "[]" in refused
    assert "anchors: ['pipeline/**']" in twins.same("backlog_area_get", area_id="pipeline")[0]


def test_an_explicit_empty_list_clears_area_anchors(twins):
    twins.same("backlog_area_update", area_id="pipeline", field="anchors", value="[]")
    assert "anchors: []" in twins.same("backlog_area_get", area_id="pipeline")[0]


# ── 5. +/- dependency edits refuse what they cannot honestly do ─────────────


def test_a_task_cannot_be_added_as_its_own_dependency(twins):
    refused, _native = twins.same("backlog_update_task", task_id="providers-001", field="depends_on",
                                  value="+providers-001")
    assert refused.startswith("Error:") and "itself" in refused


def test_an_added_dependency_that_closes_a_cycle_is_refused(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="providers-001")
    refused, _native = twins.same("backlog_update_task", task_id="providers-001", field="depends_on",
                                  value="+providers-002")
    assert refused.startswith("Error:") and "cycle" in refused
    twins.assert_state_matches()


def test_removing_a_dependency_that_is_not_there_says_so(twins):
    refused, _native = twins.same("backlog_update_task", task_id="providers-001", field="depends_on",
                                  value="-providers-002")
    assert refused.startswith("Error:") and "not" in refused and "providers-002" in refused


def test_batch_update_refuses_dependency_edits_with_the_tool_that_takes_them(twins):
    refused, _native = twins.same("backlog_batch_update", operations="update providers-002 depends_on +providers-001")
    assert "backlog_update_task" in refused and "+" in refused
    assert "dependencies not found" not in refused


# ── 6. The resume header says which handover it is ──────────────────────────


def test_resume_names_both_the_open_handover_and_the_newer_closed_one(tmp_path, monkeypatch):
    from test_agent_usability_audit import MIXED_OPEN, _seed_mixed
    twins = make_twins(tmp_path, monkeypatch, _seed_mixed)
    resumed, _native = twins.same("backlog_thread_resume", ref="mixed")
    assert f"- resume: {MIXED_OPEN} (newest open handover)" in resumed
    assert "- newest: 2026-09-17-mixed-side-note (closed)" in resumed


# ── 7. An archived link target is archived, not an orphan ───────────────────


def test_a_link_to_an_archived_bug_is_reported_as_archived(twins):
    twins.same("backlog_link", action="create", source="providers-001", target="B-002", type="relates_to")
    twins.same("backlog_bug_update", bug_id="B-002", field="fix_commit", value="abc1234")
    twins.same("backlog_bug_update", bug_id="B-002", field="status", value="fixed")
    assert twins.same("backlog_bug_archive", bug_id="B-002")[0].startswith("Bug archived")
    report = json.loads(twins.same("backlog_link", action="validate")[0])
    assert {"source": "providers-001", "target": "B-002", "type": "relates_to"} in report["archived_targets"]
    assert all(entry["target"] != "B-002" for entry in report["orphans"])


# ── 8. The last session counts handovers the same way on both stores ────────


def test_last_session_counts_stored_handovers_not_stray_files(twins):
    for root in (twins.legacy, twins.native):
        (root / ".taskmaster" / "local" / "PROGRESS.md").write_text("## Changelog\n\n### 2026-09-10 — S\n- a\n",
                                                                     encoding="utf-8")
        (root / ".taskmaster" / "handovers" / "_archive").mkdir(exist_ok=True)
        (root / ".taskmaster" / "handovers" / "_archive" / "2026-09-30-stray.md").write_text(
            "not a handover", encoding="utf-8")
    answer, _native = twins.same("backlog_last_session")
    assert "1 handover is newer than this entry" in answer and "stray" not in answer


# ── 9. A refused phase advance reads as an error ────────────────────────────


def test_a_refused_phase_advance_starts_with_error(twins):
    for answer in twins.same("backlog_advance_phase"):
        assert answer.startswith("Error: blocked — 4 tasks in phase **Development** are not done")


# ── 11. Behaviour the first tests did not pin ───────────────────────────────


def test_a_capped_continuity_answer_keeps_the_most_actionable_items(twins):
    for n in range(3):
        twins.same("backlog_issue_create", title=f"Outage {n}", severity="P1", evidence="down")
    everything = json.loads(twins.call("backlog_continuity_items", limit=0)[0])["items"]
    actionable = [item for item in everything if item["action_class"] != "ambient"]
    assert 3 <= len(actionable) < len(everything)  # the premise: ambient items exist and would come first
    capped = json.loads(twins.call("backlog_continuity_items", limit=len(actionable))[0])["items"]
    assert all(item["action_class"] != "ambient" for item in capped)


def test_open_archived_handover_rows_reads_only_open_ones(tmp_path, monkeypatch):
    from taskmaster.native.queries import Repository
    from taskmaster.native.workflow import open_archived_handover_rows
    from test_agent_usability_audit import DIGITS, MIXED_OPEN, _seed_mixed
    twins = make_twins(tmp_path, monkeypatch, _seed_mixed)
    twins.same("backlog_handover_update_status", handover_id="2026-09-17-filler-handover-00", status="closed",
               reason="done")
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        found = {ident for ident, _fields, _body in open_archived_handover_rows(snapshot)}
    assert found == {MIXED_OPEN, DIGITS, "2026-09-17-filler-handover-01"}
