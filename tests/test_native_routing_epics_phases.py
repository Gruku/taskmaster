"""User intent: epics and phases served by the native core (N08) — creation, field
edits, the archive cascade, phase activation and advance, and the epic/phase status
reads — must answer, refuse, commit and project exactly as the legacy tools do.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


def _seed():
    bs.backlog_add_phase(phase_id="later", name="Later Work", target_date="2030-01-01")
    bs.backlog_add_epic(epic_id="other", name="Other Epic", done_when="never", description="Second")
    bs.backlog_area_create(area_id="viewer", name="Viewer")
    for title, epic, phase, priority in (("One", "test-epic", "dev", "high"), ("Two", "test-epic", "dev", "low"),
                                         ("Three", "other", "later", "medium"), ("Four", "other", "dev", "medium")):
        bs.backlog_add_task(title=title, epic=epic, phase=phase, priority=priority)
    bs.backlog_update_task(task_id="test-epic-002", field="lane", value="express")
    bs.backlog_pick_task(task_id="test-epic-002")
    bs.backlog_record_gate(task_id="test-epic-002", gate="review-gate", verdict="pass")
    bs.backlog_complete_task(task_id="test-epic-002")
    bs.backlog_update_task(task_id="other-002", field="status", value="blocked")
    bs.backlog_update_task(task_id="other-002", field="blockers", value="needs infra")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


@pytest.mark.parametrize("kwargs", [
    {"epic_id": "fresh", "name": "Fresh", "done_when": "shipped", "description": "d", "status": "active",
     "area": "viewer"},
    {"epic_id": "fresh", "name": "Fresh", "done_when": "  "},
    {"epic_id": "fresh", "name": "Fresh", "done_when": "x", "status": "someday"},
    {"epic_id": "Not Kebab", "name": "Fresh", "done_when": "x"},
    {"epic_id": "fresh", "name": "Fresh", "done_when": "x", "area": "nowhere"},
    {"epic_id": "other", "name": "Dup", "done_when": "x"},
])
def test_add_epic_matches(twins, kwargs):
    twins.same("backlog_add_epic", **kwargs)
    _check(twins)


@pytest.mark.parametrize("field,value", [
    ("name", "Renamed"), ("description", "New description"), ("status", "active"), ("status", "archived"),
    ("status", "sideways"), ("docs", "design:docs/d.md"), ("docs", "nocolon"), ("docs", "bogus:x"),
    ("docs", "plan:"), ("components", "not json"), ("components", json.dumps({"_unassigned": {}})),
    ("components", json.dumps({"ui": {"title": "UI", "after": []}, "api": {"title": "API", "after": ["ui"]}})),
    ("design_status", "locked"), ("design_status", "frozen"), ("done_when", " "), ("done_when", "all done"),
    ("area", "viewer"), ("area", "nowhere"), ("area", ""), ("bogus", "x"),
])
def test_update_epic_matches(twins, field, value):
    twins.same("backlog_update_epic", epic_id="test-epic", field=field, value=value)
    twins.same("backlog_update_epic", epic_id="ghost", field=field, value=value)
    _check(twins)


def test_epic_docs_set_then_clear_and_status_round_trip(twins):
    twins.same("backlog_update_epic", epic_id="other", field="docs", value="plan:docs/p.md")
    twins.same("backlog_update_epic", epic_id="other", field="docs", value="plan:")
    twins.same("backlog_update_epic", epic_id="other", field="status", value="done")
    _check(twins)


def test_archive_epic_cascade_matches(twins):
    twins.same("backlog_archive_epic", epic_id="other", reason="nonsense")
    twins.same("backlog_archive_epic", epic_id="ghost")
    twins.same("backlog_archive_epic", epic_id="other", reason="superseded")
    twins.same("backlog_archive_epic", epic_id="other")
    twins.same("backlog_update_epic", epic_id="other", field="status", value="active")
    _check(twins)


@pytest.mark.parametrize("kwargs", [
    {"phase_id": "polish", "name": "Polish", "description": "shine", "target_date": "2031-02-03",
     "start_date": "2030-12-01"},
    {"phase_id": "polish", "name": "Polish", "order": 7},
    {"phase_id": "Bad_Id", "name": "x"},
    {"phase_id": "dev", "name": "dup"},
    {"phase_id": "polish", "name": "x", "target_date": "tomorrow"},
    {"phase_id": "polish", "name": "x", "start_date": "2030-13-40"},
])
def test_add_phase_matches(twins, kwargs):
    twins.same("backlog_add_phase", **kwargs)
    _check(twins)


@pytest.mark.parametrize("phase_id,field,value", [
    ("later", "status", "active"), ("later", "status", "done"), ("later", "status", "archived"),
    ("later", "status", "paused"), ("Later Work", "name", "Renamed by name"), ("ghost", "name", "x"),
    ("later", "order", "5"), ("later", "order", "five"), ("later", "target_date", ""),
    ("later", "target_date", "2029-02-30"), ("later", "start_date", "2029-01-01"),
    ("dev", "deliverables", json.dumps({"action": "add", "text": "Ship it"})),
    ("dev", "deliverables", json.dumps({"action": "add", "text": " "})),
    ("dev", "deliverables", json.dumps({"action": "toggle", "index": 0})),
    ("dev", "deliverables", json.dumps({"action": "remove", "index": 3})),
    ("dev", "deliverables", json.dumps({"action": "set", "items": [{"text": "A", "done": True}, {"text": "B"}]})),
    ("dev", "deliverables", json.dumps({"action": "explode"})), ("dev", "deliverables", "not json"),
    ("dev", "docs", "roadmap:docs/r.md"), ("dev", "docs", "x"), ("dev", "docs", "nope:y"),
    ("dev", "description", "Described"), ("dev", "bogus", "x"),
])
def test_update_phase_matches(twins, phase_id, field, value):
    twins.same("backlog_update_phase", phase_id=phase_id, field=field, value=value)
    _check(twins)


def test_phase_deliverables_sequence_and_advance_match(twins):
    twins.same("backlog_update_phase", phase_id="dev", field="deliverables",
               value=json.dumps({"action": "add", "text": "First"}))
    twins.same("backlog_update_phase", phase_id="dev", field="deliverables",
               value=json.dumps({"action": "add", "text": "Second"}))
    twins.same("backlog_update_phase", phase_id="dev", field="deliverables",
               value=json.dumps({"action": "toggle", "index": 1}))
    twins.same("backlog_update_phase", phase_id="dev", field="deliverables",
               value=json.dumps({"action": "remove", "index": 0}))
    twins.same("backlog_update_phase", phase_id="dev", field="deliverables",
               value=json.dumps({"action": "add", "text": "Third"}))
    twins.same("backlog_phase_status")
    twins.same("backlog_advance_phase")
    twins.same("backlog_advance_phase", force=True)
    twins.same("backlog_phase_status")
    twins.same("backlog_phase_status", phase_id="dev")
    twins.same("backlog_advance_phase", force=True)
    twins.same("backlog_advance_phase")
    twins.same("backlog_phase_status")
    _check(twins)


def test_status_reads_match(twins):
    twins.same("backlog_update_epic", epic_id="other", field="components",
               value=json.dumps({"ui": {"title": "UI", "after": []}, "api": {"title": "API", "after": ["ui"]}}))
    twins.same("backlog_update_task", task_id="other-001", field="component", value="ui")
    twins.same("backlog_update_epic", epic_id="other", field="design_status", value="locked")
    for kwargs in ({}, {"phase_id": "later"}, {"phase_id": "Development"}, {"phase_id": "ghost"}):
        twins.same("backlog_phase_status", **kwargs)
    for epic_id in ("test-epic", "other", "ghost"):
        twins.same("backlog_epic_status", epic_id=epic_id)
    twins.same("backlog_archive_task", task_id="test-epic-002")
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="archived")
    twins.same("backlog_epic_status", epic_id="test-epic")
