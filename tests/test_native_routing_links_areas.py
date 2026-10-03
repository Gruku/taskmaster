"""User intent: typed links and areas served by the native core (N08) — link create
and remove with inverses and cycle refusal, link query/validate, area create/list/
get/update and the viewer-prefs read — must answer, refuse, commit and project
exactly as the legacy tools do, including links synthesized from legacy fields.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import committed, make_twins, normalize


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
            ("T-003", "T-002", "depends_on"), ("T-001", "T-003", "depends_on"), ("T-001", "T-002", "blocks"),
            ("T-002", "ISS-001", "fixes"), ("IDEA-001", "ISS-001", "fixes"), ("bogus", "T-001", "relates_to"),
            ("T-001", "bogus", "relates_to"), ("T-001", "T-002", "likes"), ("T-404", "T-001", "relates_to"),
            ("T-001", "T-404", "relates_to"), ("IDEA-001", "T-002", "references"), ("T-001", "T-002", "relates_to")):
        twins.same("backlog_link", action="create", source=source, target=target, type=link_type, note="n")
    _check(twins)
    for source, target, link_type in (("T-002", "ISS-001", "fixes"), ("T-003", "T-002", ""), ("T-001", "T-003", "likes"), ("T-404", "T-001", ""),
                                      ("bogus", "T-001", ""), ("T-001", "IDEA-001", ""),
                                      ("T-001", "T-002", "depends_on"), ("T-001", "T-002", "")):
        twins.same("backlog_link", action="remove", source=source, target=target, type=link_type)
    _check(twins)


def test_a_link_between_two_non_task_entities_persists_on_both_stores(twins):
    """The legacy link engine used to write a non-task entity without latching the
    transaction, so the commit rolled back while the tool reported `ok: linked`
    (found by N08). Both stores now commit both ends."""
    legacy_answer, _native = twins.same("backlog_link", action="create", source="ISS-001",
                                        target="IDEA-001", type="relates_to")
    assert normalize(legacy_answer) == "ok: linked ISS-001 -[relates_to]-> IDEA-001 [seq #]"
    for side in (committed(twins.legacy), committed(twins.native)):
        assert {"type": "relates_to", "target": "IDEA-001"} in side[("issue", "ISS-001")][0]["links"]
        assert {"type": "relates_to", "target": "ISS-001"} in side[("idea", "IDEA-001")][0]["links"]


def test_link_query_and_validate_match(twins):
    twins.same("backlog_link", action="create", source="T-003", target="T-002", type="depends_on")
    twins.same("backlog_link", action="create", source="IDEA-001", target="T-002", type="references")
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
                         ("anchors", "not json"), ("anchors", '{"a": 1}'), ("status", "x"), ("name", " ")):
        twins.same("backlog_area_update", area_id="store", field=field, value=value)
    twins.same("backlog_area_update", area_id="ghost", field="name", value="x")
    for kwargs in ({}, {"limit": 1}, {"limit": 0}):
        twins.same("backlog_area_list", **kwargs)
    for area_id in ("viewer", "store", "ghost"):
        twins.same("backlog_area_get", area_id=area_id)
    _check(twins)


def test_non_text_area_anchors_are_refused_on_both_stores(twins):
    """The legacy tool used to store `[1, 2]` as area anchors while the native core
    refused it; both now admit only text anchors."""
    legacy, _native = twins.same("backlog_area_update", area_id="viewer", field="anchors", value="[1, 2]")
    assert legacy == "Error: anchors value must be a list of strings"
    assert committed(twins.native)[("area", "viewer")][0]["anchors"] == ["viewer/**"]
    assert committed(twins.legacy)[("area", "viewer")][0]["anchors"] == ["viewer/**"]


def test_viewer_prefs_read_matches(twins):
    legacy, native = twins.call("viewer_prefs_get")
    assert json.loads(native) == json.loads(legacy)


def test_viewer_prefs_writes_match(twins):
    for patch in ('{"theme": "dark", "issues": {"aging": {"warn_days": 3}}}', '{"issues": {"aging": {"stale_days": 9}}}',
                  "not json", "[1]"):
        twins.same("viewer_prefs_set", patch_json=patch)
        legacy, native = twins.call("viewer_prefs_get")
        assert json.loads(native) == json.loads(legacy)
    aging = json.loads(twins.call("viewer_prefs_get")[1])["issues"]["aging"]
    assert (aging["warn_days"], aging["stale_days"]) == (3, 9)


def test_open_viewer_opens_the_native_served_board(twins, monkeypatch):
    opened = []
    monkeypatch.setattr(bs.webbrowser, "open", opened.append)
    monkeypatch.setattr(bs, "_start_viewer_server", lambda: 6899)
    legacy, native = twins.same("backlog_open_viewer")
    assert native == "Opened backlog viewer at http://127.0.0.1:6899/" and len(opened) == 2
