"""User intent: typed links and areas served by the native core (N08) — link create
and remove with inverses and cycle refusal, link query/validate, area create/list/
get/update and the viewer-prefs read — must answer, refuse, commit and project
exactly as the legacy tools do, including links synthesized from legacy fields.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


def _seed():
    for task_id, title in (("T-001", "Task one"), ("T-002", "Task two"), ("T-003", "Task three")):
        bs.backlog_add_task(title=title, epic="test-epic", phase="dev", options={"task_id": task_id})
    bs.backlog_update_task(task_id="T-002", field="depends_on", value="T-001")
    bs.backlog_issue_create(title="Linked issue", severity="P2", evidence="e", related_tasks=["T-003"])
    bs.backlog_idea_create(title="Linked idea", related_tasks=["T-001"])
    bs.backlog_area_create(area_id="viewer", name="Viewer", description="The viewer\nsecond line",
                           anchors=["viewer/**"])


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


def test_link_create_and_remove_match(twins):
    for source, target, link_type in (
            ("ISS-001", "IDEA-001", "relates_to"), ("ISS-001", "IDEA-001", "relates_to"),
            ("T-003", "T-002", "depends_on"), ("T-001", "T-003", "depends_on"), ("T-001", "T-002", "blocks"),
            ("T-002", "ISS-001", "fixes"), ("IDEA-001", "ISS-001", "fixes"), ("bogus", "T-001", "relates_to"),
            ("T-001", "bogus", "relates_to"), ("T-001", "T-002", "likes"), ("T-404", "T-001", "relates_to"),
            ("T-001", "T-404", "relates_to"), ("IDEA-001", "T-002", "references")):
        twins.same("backlog_link", action="create", source=source, target=target, type=link_type, note="n")
    _check(twins)
    for source, target, link_type in (("T-002", "ISS-001", "fixes"), ("ISS-001", "IDEA-001", ""),
                                      ("T-003", "T-002", ""), ("T-001", "T-003", "likes"), ("T-404", "T-001", ""),
                                      ("bogus", "T-001", ""), ("T-001", "IDEA-001", ""),
                                      ("T-001", "T-002", "depends_on")):
        twins.same("backlog_link", action="remove", source=source, target=target, type=link_type)
    _check(twins)


def test_link_query_and_validate_match(twins):
    twins.same("backlog_link", action="create", source="T-003", target="T-002", type="depends_on")
    twins.same("backlog_link", action="create", source="ISS-001", target="IDEA-001", type="references")
    for kwargs in ({}, {"source": "T-003"}, {"source": "T-003", "type": "depends_on", "depth": 3},
                   {"target": "T-002"}, {"type": "relates_to"}, {"source": "bogus"}, {"target": "bogus"},
                   {"source": "T-404"}):
        legacy, native = twins.call("backlog_link", action="query", **kwargs)
        assert native == legacy, (kwargs, legacy, native)
    legacy, native = twins.call("backlog_link", action="validate")
    assert json.loads(native) == json.loads(legacy)
    twins.same("backlog_link", action="explode")


def test_areas_match(twins):
    twins.same("backlog_area_create", area_id="store", name="Store", description="SQLite", anchors=["taskmaster/**"])
    twins.same("backlog_area_create", area_id="viewer", name="Dup")
    twins.same("backlog_area_create", area_id="Bad Id", name="x")
    twins.same("backlog_area_create", area_id="noname", name=" ")
    for field, value in (("name", "Store renamed"), ("description", "New"), ("anchors", '["a/**", "b"]'),
                         ("anchors", "not json"), ("anchors", '{"a": 1}'), ("anchors", "[1, 2]"), ("status", "x"),
                         ("name", " ")):
        twins.same("backlog_area_update", area_id="store", field=field, value=value)
    twins.same("backlog_area_update", area_id="ghost", field="name", value="x")
    for kwargs in ({}, {"limit": 1}, {"limit": 0}):
        twins.same("backlog_area_list", **kwargs)
    for area_id in ("viewer", "store", "ghost"):
        twins.same("backlog_area_get", area_id=area_id)
    _check(twins)


def test_viewer_prefs_read_matches(twins):
    legacy, native = twins.call("viewer_prefs_get")
    assert json.loads(native) == json.loads(legacy)
