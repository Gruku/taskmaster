"""User intent: final review fixes for the agent tool-use rounds (tm-audit-030). A task's
dependencies have one source of truth, its `depends_on` field: dependency entries a task
stores in `links` (written by an older link migration) are ignored on read, so clearing
the field clears the dependency everywhere; reconcile reports what it cannot repair; link
writes touch only stored documents. Both stores alike.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, hand_edit_entity, make_twins
from test_agent_usability_round3 import _seed

STALE = [{"type": "depends_on", "target": "providers-001"}, {"type": "relates_to", "target": "IDEA-001"}]


def _seed_stored_dependency():
    """providers-002 depends on providers-001 by its field and, as an older link
    migration left it, by a stored link too; providers-001 stores the inverse."""
    _seed()
    bs.backlog_update_task(task_id="providers-002", field="depends_on", value="providers-001")
    hand_edit_entity("task", "providers-002", lambda doc: doc.__setitem__("links", list(STALE)))
    hand_edit_entity("task", "providers-001", lambda doc: doc.__setitem__(
        "links", [{"type": "blocks", "target": "providers-002"}]))
    # A legacy field that derives a link: test-epic-001 relates to ISS-001 through it.
    hand_edit_entity("task", "test-epic-001", lambda doc: doc.__setitem__("related_issues", ["ISS-001"]))


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed_stored_dependency)


def _query(twins, **kwargs):
    legacy, _native = twins.same("backlog_link", action="query", **kwargs)
    return {(edge["source"], edge["type"], edge["target"]) for edge in json.loads(legacy)}


# ── One source of truth for a task's dependencies ───────────────────────────


def test_a_dependency_shows_while_the_field_holds_it(twins):
    assert ("providers-002", "depends_on", "providers-001") in _query(twins, source="providers-002")
    assert ("providers-001", "blocks", "providers-002") in _query(twins, source="providers-001")


def test_clearing_the_field_clears_a_stored_dependency_link_everywhere(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="-providers-001")
    assert _query(twins, source="providers-002") == {("providers-002", "relates_to", "IDEA-001")}
    assert all(edge[1] not in ("depends_on", "blocks") for edge in _query(twins))
    shown, _native = twins.same("backlog_get_task", task_id="providers-002")
    assert "depends_on" not in shown.split("**links:**")[-1]
    blocks, _native = twins.same("backlog_get_task", task_id="providers-001")
    assert "blocks" not in blocks.split("**links:**")[-1]


def test_validate_draws_the_cycle_graph_from_the_fields_only(twins):
    twins.same("backlog_update_task", task_id="providers-002", field="depends_on", value="-providers-001")
    twins.same("backlog_update_task", task_id="providers-001", field="depends_on", value="+providers-002")
    report = json.loads(twins.same("backlog_link", action="validate")[0])
    assert report["cycles"] == []
    assert not [entry for entry in report["asymmetric"] if entry["type"] in ("depends_on", "blocks")]


def test_validate_does_not_report_the_derived_blocks_side(twins):
    report = json.loads(twins.same("backlog_link", action="validate")[0])
    assert not [entry for entry in report["asymmetric"] if entry["type"] in ("depends_on", "blocks")]


def test_reconcile_reports_and_drops_stored_dependency_links(twins):
    with twins.at(twins.legacy):
        report = json.loads(bs.backlog_link(action="reconcile"))
        assert report["stored_dependency_links"] == 2 and report["written"] is False
        written = json.loads(bs.backlog_link(action="reconcile", write=True))
    assert written["stored_dependency_links"] == 2 and written["dropped_dependency_links"] == 2
    after = committed(twins.legacy)
    assert after[("task", "providers-002")][0]["links"] == [{"type": "relates_to", "target": "IDEA-001"}]
    assert "links" not in after[("task", "providers-001")][0]
    assert after[("task", "providers-002")][0]["depends_on"] == ["providers-001"]  # the field is the dependency


# ── Reconcile is complete about what it will not fix ───────────────────────


def test_reconcile_lists_derived_links_it_will_not_repair(twins):
    twins.same("backlog_issue_create", title="Linked by field", severity="P3", evidence="x",
               related_tasks=["providers-001"])
    before = committed(twins.legacy)[("task", "providers-001")]
    with twins.at(twins.legacy):
        report = json.loads(bs.backlog_link(action="reconcile", write=True))
    pair = {"source": "ISS-004", "target": "providers-001", "type": "relates_to", "missing_inverse": "relates_to"}
    assert pair not in report["repairable"]
    assert any({key: entry[key] for key in pair} == pair and "field" in entry["reason"]
               for entry in report["not_repaired"])
    assert report["not_repaired_count"] == len(report["not_repaired"])
    assert (committed(twins.legacy)[("task", "providers-001")][0].get("links") or []) == [
        link for link in (before[0].get("links") or []) if link["type"] != "blocks"]


# ── Auto-linking writes only the stored document ────────────────────────────


def test_auto_linking_a_task_writes_only_the_new_reference(twins):
    """ISS-001 is already linked through a field, so its mention adds nothing; ISS-002
    is new. Only that reference is stored — the derived link is not written back."""
    twins.same("backlog_update_task", task_id="test-epic-001", field="notes",
               value="See ISS-001 and ISS-002 before starting.")
    for side in (committed(twins.legacy), committed(twins.native)):
        assert side[("task", "test-epic-001")][0]["links"] == [{"type": "references", "target": "ISS-002"}]


# ── Whole-list dependencies get the same refusals as +id ────────────────────


def test_a_whole_list_naming_the_task_itself_is_refused(twins):
    refused, _native = twins.same("backlog_update_task", task_id="providers-001", field="depends_on",
                                  value="providers-001")
    assert refused.startswith("Error:") and "itself" in refused


def test_a_whole_list_closing_a_cycle_is_refused(twins):
    refused, _native = twins.same("backlog_update_task", task_id="providers-001", field="depends_on",
                                  value="test-epic-001,providers-002")
    assert refused.startswith("Error:") and "cycle" in refused
    twins.assert_state_matches()


def test_an_empty_list_edit_entry_is_refused(twins):
    for refused in (twins.same("backlog_area_update", area_id="pipeline", field="anchors", value="+")[0],
                    twins.same("backlog_update_task", task_id="providers-001", field="depends_on", value="+")[0]):
        assert refused.startswith("Error:") and "empty" in refused
