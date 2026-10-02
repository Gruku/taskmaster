"""User intent: the second agent tool-use batch (tm-audit-030) reached the right answers but
exposed tool defects and wasted calls — a link reported as created and never stored, lists
that could not answer "what waits on me", "what is under this area" or "which bugs point at
this path", list edits that silently replace, filters that silently narrow. Each fix must
hold on the legacy store and on its native copy alike.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


def _seed():
    bs.backlog_area_create(area_id="pipeline", name="Pipeline", anchors=["pipeline/**"])
    bs.backlog_add_epic(epic_id="providers", name="Providers", done_when="failover ships", area="pipeline")
    bs.backlog_add_task(title="Fail over on timeout", epic="providers", phase="dev")           # providers-001
    bs.backlog_add_task(title="Retry budget", epic="providers", phase="dev", notes="Budget is three retries.")
    bs.backlog_add_task(title="Loose end", epic="test-epic", phase="dev")                      # test-epic-001
    bs.backlog_add_task(title="Tagged directly", epic="test-epic", phase="dev", options={"area": "pipeline"})
    bs.backlog_update_task(task_id="providers-001", field="human_action", value="Approve the vendor contract")
    bs.backlog_bug_create(title="Router drops the negative prompt", location=["pipeline/providers/router.py:12"])
    bs.backlog_bug_create(title="Viewer flickers", location=["viewer/app.js:3"])
    bs.backlog_issue_create(title="Timeouts under load", severity="P2", evidence="p95 28 s",
                            location=["pipeline/providers/timeouts.py:40"])
    bs.backlog_issue_create(title="Stale previews", severity="P2", evidence="seen twice", location=["viewer/cache.js"])
    bs.backlog_issue_create(title="Closed long ago", severity="P3", evidence="n/a")
    bs.backlog_issue_update(issue_id="ISS-002", field="status", value="investigating")
    bs.backlog_issue_update(issue_id="ISS-003", field="status", value="wontfix")
    bs.backlog_idea_create(title="Cache provider responses", body="Maybe.")
    bs.backlog_note(action="create", text="Remember the vendor call", pinned=False)
    bs.backlog_decision_create(title="Which provider is primary", options=["A", "B"])
    bs.backlog_decision_create(title="Retry count", options=["3", "5"])
    bs.backlog_decision(action="resolve", decision_id="DEC-002", resolved_with=1, rationale="enough")
    bs.backlog_handover_create(tldr="Failover work parked", thread="failover", task_ids=["providers-001"])


HANDOVER = "2026-09-17-failover-work-parked"


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


# ── A. Links are stored, for every kind, and seen from both ends ────────────


def _edges(twins, **kwargs):
    legacy, _native = twins.same("backlog_link", action="query", **kwargs)
    return {(edge["source"], edge["type"], edge["target"]) for edge in json.loads(legacy)}


@pytest.mark.parametrize("source,target", [
    ("IDEA-001", "ISS-001"), ("providers-001", "B-001"), ("DEC-001", "providers-002"),
    ("NOTE-001", "IDEA-001"), ("B-002", HANDOVER), ("test-epic-001", "providers-001"),
])
def test_a_link_between_any_two_kinds_is_stored_and_seen_from_both_ends(twins, source, target):
    created, _native = twins.same("backlog_link", action="create", source=source, target=target, type="relates_to")
    assert created.startswith(f"ok: linked {source} -[relates_to]-> {target}")
    assert (source, "relates_to", target) in _edges(twins, source=source)
    assert (target, "relates_to", source) in _edges(twins, source=target)
    assert (source, "relates_to", target) in _edges(twins)  # the unfiltered listing reaches every kind
    twins.assert_state_matches()


def test_a_link_can_be_removed_again_from_either_kind(twins):
    twins.same("backlog_link", action="create", source="providers-001", target="B-001", type="references")
    assert ("B-001", "referenced_by", "providers-001") in _edges(twins, source="B-001")
    twins.same("backlog_link", action="remove", source="providers-001", target="B-001", type="references")
    assert _edges(twins, source="B-001") == set() == _edges(twins, source="providers-001")


def test_a_link_to_nothing_is_refused_not_reported_as_created(twins):
    refused, _native = twins.same("backlog_link", action="create", source="providers-001", target="providers-999",
                                  type="relates_to")
    assert refused.startswith("Error:") and "providers-999" in refused
    refused, _native = twins.same("backlog_link", action="create", source="providers-001", target="B-001", type="fixes")
    assert refused.startswith("Error: invalid link") and "bug" in refused  # the kind is named truthfully


def test_links_validate_knows_every_kind(twins):
    twins.same("backlog_link", action="create", source="providers-001", target="B-001", type="relates_to")
    report, _native = twins.same("backlog_link", action="validate")
    assert json.loads(report)["orphans"] == [] and json.loads(report)["asymmetric"] == []


# ── B. What is waiting on a human ───────────────────────────────────────────


def test_tasks_waiting_on_a_human_are_one_call(twins):
    listed, _native = twins.same("backlog_list_tasks", waiting_on_human=True)
    assert listed.splitlines()[0] == "**1 tasks:**"
    assert "`providers-001`" in listed and "waiting-on-human: Approve the vendor contract" in listed
    assert "providers-002" not in listed


def test_continuity_items_are_bounded_and_say_what_was_left_out(twins):
    legacy, native = twins.call("backlog_continuity_items", limit=3)
    assert json.loads(native) == json.loads(legacy)
    answer = json.loads(legacy)
    assert len(answer["items"]) == 3 and answer["total"] > 3 and answer["truncated"] is True
    everything = json.loads(twins.call("backlog_continuity_items", limit=0)[0])
    assert len(everything["items"]) == everything["total"] == answer["total"] and "truncated" not in everything


def test_continuity_items_can_be_asked_for_one_action_class(twins):
    twins.same("backlog_issue_create", title="Outage", severity="P1", evidence="down")
    answer = json.loads(twins.call("backlog_continuity_items", action_class="review")[0])
    assert answer["items"] and {item["action_class"] for item in answer["items"]} == {"review"}
    assert "ISS-004" in [item["id"] for item in answer["items"]]


# ── C. What work is under an area ───────────────────────────────────────────


def test_tasks_of_an_epic_filed_under_an_area_are_in_that_area(twins):
    listed, _native = twins.same("backlog_list_tasks", area="pipeline")
    for ident in ("providers-001", "providers-002", "test-epic-002"):
        assert f"`{ident}`" in listed
    assert "test-epic-001" not in listed


def test_a_task_tagged_with_another_area_leaves_its_epics_area(twins):
    twins.same("backlog_area_create", area_id="viewer", name="Viewer")
    twins.same("backlog_update_task", task_id="providers-002", field="area", value="viewer")
    listed, _native = twins.same("backlog_list_tasks", area="pipeline")
    assert "providers-002" not in listed and "providers-001" in listed
    assert "`providers-002`" in twins.same("backlog_list_tasks", area="viewer")[0]


def test_an_area_shows_its_epics_and_how_much_work_it_holds(twins):
    area, _native = twins.same("backlog_area_get", area_id="pipeline")
    assert "- providers — Providers (planned, 0/2 done)" in area
    assert "3 tasks" in area and "backlog_list_tasks(area=\"pipeline\")" in area


# ── D. Bugs and issues by where they point ──────────────────────────────────


def test_bugs_can_be_listed_by_the_path_they_point_at(twins):
    listed, _native = twins.same("backlog_bug_list", path="pipeline/providers")
    assert "B-001" in listed and "B-002" not in listed
    assert "pipeline/providers/router.py:12" in listed  # the row shows where
    assert "B-002" in twins.same("backlog_bug_list", path="viewer/*.js")[0]
    assert twins.same("backlog_bug_list", path="pipeline/prov")[0] == "No bugs match."  # a path, not a string prefix


def test_issues_can_be_listed_by_the_path_they_point_at(twins):
    listed, _native = twins.same("backlog_issue_list", path="pipeline")
    assert "ISS-001" in listed and "ISS-002" not in listed and "pipeline/providers/timeouts.py:40" in listed


# ── F. List fields: replace, or add and remove ──────────────────────────────


def test_depends_on_can_be_added_to_and_removed_from_without_resending_the_list(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on", value="providers-001")
    twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on", value="+providers-002")
    shown, _native = twins.same("backlog_get_task", task_id="test-epic-001")
    assert "providers-001" in shown and "providers-002" in shown
    twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on", value="-providers-001")
    shown, _native = twins.same("backlog_get_task", task_id="test-epic-001")
    assert "providers-001" not in shown and "providers-002" in shown
    twins.assert_state_matches()


def test_a_half_prefixed_list_is_refused_rather_than_guessed(twins):
    refused, _native = twins.same("backlog_update_task", task_id="test-epic-001", field="depends_on",
                                  value="+providers-001,providers-002")
    assert refused.startswith("Error:") and "+" in refused


@pytest.mark.parametrize("value", [["pipeline/**", "docs/pipeline/**"], '["pipeline/**", "docs/pipeline/**"]',
                                   "+docs/pipeline/**", ["+docs/pipeline/**"]])
def test_area_anchors_take_a_list_a_json_array_or_an_addition(twins, value):
    twins.same("backlog_area_update", area_id="pipeline", field="anchors", value=value)
    area, _native = twins.same("backlog_area_get", area_id="pipeline")
    assert "anchors: ['pipeline/**', 'docs/pipeline/**']" in area
    twins.assert_state_matches()


def test_area_anchors_can_be_removed_one_at_a_time(twins):
    twins.same("backlog_area_update", area_id="pipeline", field="anchors", value="+docs/pipeline/**")
    twins.same("backlog_area_update", area_id="pipeline", field="anchors", value="-pipeline/**")
    assert "anchors: ['docs/pipeline/**']" in twins.same("backlog_area_get", area_id="pipeline")[0]


def test_area_create_takes_the_shapes_area_update_takes(twins):
    twins.same("backlog_area_create", area_id="docs", name="Docs", anchors='["docs/**"]')
    assert "anchors: ['docs/**']" in twins.same("backlog_area_get", area_id="docs")[0]


# ── G. Filters that narrowed silently ───────────────────────────────────────


def test_unresolved_issues_are_one_call(twins):
    listed, _native = twins.same("backlog_issue_list", status="unresolved")
    assert "ISS-001" in listed and "ISS-002" in listed and "ISS-003" not in listed
    only_open, _native = twins.same("backlog_issue_list", status="open")
    assert "ISS-002" not in only_open  # `open` still means the one status


def test_an_unknown_issue_status_is_refused_with_the_valid_ones(twins):
    refused, _native = twins.same("backlog_issue_list", status="pending")
    assert refused.startswith("Error:") and "unresolved" in refused and "investigating" in refused


def test_a_decision_list_says_what_it_left_out(twins):
    listed, _native = twins.same("backlog_decision", action="list")
    assert "DEC-001" in listed and "DEC-002" not in listed
    assert "1 more" in listed and 'status="all"' in listed


# ── H. Index status on a legacy store ───────────────────────────────────────


def test_verifying_a_legacy_index_answers_what_was_and_was_not_checked(twins):
    with twins.at(twins.legacy):
        answer = bs.backlog_index_status(verify=True)
    assert not answer.startswith("Error") and "Rows: entities=" in answer
    assert "not compared" in answer and "rebuild=True" in answer


# ── I. Advancing a phase with unfinished tasks ──────────────────────────────


def test_a_phase_with_unfinished_tasks_does_not_advance_unless_forced(twins):
    twins.same("backlog_add_phase", phase_id="next", name="Next")
    blocked, _native = twins.same("backlog_advance_phase")
    assert blocked.startswith("**Blocked:** 4 tasks in phase **Development** are not done")
    assert "force=True" in blocked
    assert "**Active Phase:** Development" in twins.same("backlog_status")[0]
    twins.assert_state_matches()
    forced, _native = twins.same("backlog_advance_phase", force=True)
    assert forced.startswith("Completed phase **Development**") and "**Warning:** 4 tasks" in forced


# ── J. A task's prose, wherever it is kept ──────────────────────────────────


def test_a_task_document_with_no_body_shows_the_text_its_fields_hold(twins):
    shown, _native = twins.same("backlog_document", kind="task", entity_id="providers-002")
    assert "Budget is three retries." in shown and "### notes" in shown
    assert "(no body)" not in shown


def test_a_task_document_with_no_text_anywhere_says_so(twins):
    shown, _native = twins.same("backlog_document", kind="task", entity_id="providers-001")
    assert shown.endswith("(no body)")
