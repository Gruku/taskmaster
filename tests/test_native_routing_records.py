"""User intent: the continuity records an agent files while working — bugs, issues,
ideas and decisions — served by the native core (N08) must answer, refuse, commit
and project exactly as the legacy tools do, including the derived backlog.yaml
indexes, inline-mention auto-links and bug-to-issue promotion.
"""
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


def _seed():
    bs.backlog_add_task(title="Host task", epic="test-epic", phase="dev", notes="host")
    bs.backlog_add_task(title="Other task", epic="test-epic", phase="dev")
    bs.backlog_bug_create(title="Seed bug crashes on load", found_in="test-epic-001", components=["ui"],
                          body="Repro: open the viewer")
    bs.backlog_issue_create(title="Seed issue", severity="P2", evidence="Seen three times",
                            related_tasks=["test-epic-001"])
    bs.backlog_idea_create(title="Seed idea", body="An idea mentioning test-epic-002", tags=["x"])
    bs.backlog_decision_create(title="Seed decision", options=["A", "B"], recommendation=1,
                               task_id="test-epic-001")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


# ── Bugs ────────────────────────────────────────────────────────────────────


def test_bug_lifecycle_matches(twins):
    twins.same("backlog_bug_create", title="Second bug in the ui loader", found_in="test-epic-002",
               discovered_by="claude", severity="P1", components=["ui", "loader"], location=["a.py:3"],
               body="Details")
    twins.same("backlog_bug_create", title="  ")
    twins.same("backlog_bug_create", title="Bad severity", severity="P9")
    twins.same("backlog_bug_create", title="Bad discoverer", discovered_by="robot")
    for field, value in (("title", "Renamed bug"), ("severity", "P3"), ("components", "a, b"),
                         ("location", ""), ("status", "fixed"), ("fix_commit", "abc123"), ("status", "fixed"),
                         ("status", "nope"), ("body", "New body mentioning test-epic-001"), ("bogus", "x")):
        twins.same("backlog_bug_update", bug_id="B-001", field=field, value=value)
    twins.same("backlog_bug_update", bug_id="B-404", field="title", value="x")
    twins.same("backlog_bug_archive", bug_id="B-002")
    twins.same("backlog_bug_archive", bug_id="B-001")
    twins.same("backlog_bug_archive", bug_id="B-404")
    _check(twins)


def test_bug_reads_match(twins):
    twins.same("backlog_bug_create", title="Loader crashes on load again", components=["ui"])
    twins.same("backlog_bug_update", bug_id="B-002", field="status", value="shelved")
    for kwargs in ({}, {"status": "open"}, {"found_in": "test-epic-001"}, {"limit": 1},
                   {"include_archive": True}):
        twins.same("backlog_bug_list", **kwargs)
    for bug_id in ("B-001", "B-404"):
        twins.same("backlog_bug_get", bug_id=bug_id)
        twins.same("backlog_bug_get", bug_id=bug_id, verbose=True)
    for mode in ("all", "open_only", "end_of_task", "bogus"):
        twins.same("backlog_bug_pattern_scan", mode=mode)


def test_bug_promotion_matches(twins):
    twins.same("backlog_bug_create", title="Twin crash", components=["loader"])
    twins.same("backlog_bug_promote", bug_ids=[], title="x", severity="P1", evidence_text="e")
    twins.same("backlog_bug_promote", bug_ids=["B-001"], title="x", severity="P1", evidence_text=" ")
    twins.same("backlog_bug_promote", bug_ids=["B-001", "B-404"], title="x", severity="P1", evidence_text="e")
    twins.same("backlog_bug_promote", bug_ids=["B-001"], title="x", severity="P7", evidence_text="e")
    twins.same("backlog_bug_promote", bug_ids=["B-001", "B-002"], title="Systemic loader crash",
               severity="P1", evidence_text="Recurs across sessions", body="Mentions test-epic-002")
    twins.same("backlog_issue_list")
    _check(twins)


# ── Issues ──────────────────────────────────────────────────────────────────


def test_issue_lifecycle_matches(twins):
    twins.same("backlog_issue_create", title="Systemic save loss", severity="P0", evidence="Lost twice",
               impact="Users lose work. Badly.", components=["store"], location=["s.py:1"],
               related_tasks=["test-epic-002"], discovered_by="qa", body="See test-epic-001 and ISS-001")
    twins.same("backlog_issue_create", title="Bad", severity="P5", evidence="x")
    twins.same("backlog_issue_create", title=" ", severity="P1", evidence="x")
    twins.same("backlog_issue_create", title="With tldr", severity="P3", evidence="x", tldr="Short")
    for field, value in (("status", "investigating"), ("severity", "P1"), ("severity", "P8"),
                         ("status", "gone"), ("related_tasks", "test-epic-001, test-epic-002"),
                         ("fixed_in_task", "test-epic-001"), ("status", "fixed"), ("impact", "Now fixed"),
                         ("body", "Body mentioning IDEA-001"), ("bogus", "x")):
        twins.same("backlog_issue_update", issue_id="ISS-001", field=field, value=value)
    twins.same("backlog_issue_update", issue_id="ISS-404", field="title", value="x")
    _check(twins)


def test_issue_reads_match(twins):
    twins.same("backlog_issue_create", title="Second issue", severity="P0", evidence="x", body="## Repro\nsteps")
    for kwargs in ({}, {"severity": "P2"}, {"status": "open"}, {"limit": 1}, {"verbose": True}):
        twins.same("backlog_issue_list", **kwargs)
    for kwargs in ({}, {"verbose": True}, {"expand_links": True}, {"verbose": True, "expand_links": True},
                   {"sections": ["repro"]}, {"sections": []}, {"sections": ["nope"]}):
        twins.same("backlog_issue_get", issue_id="ISS-001", **kwargs)
        twins.same("backlog_issue_get", issue_id="ISS-002", **kwargs)
    twins.same("backlog_issue_get", issue_id="ISS-404")


# ── Ideas ───────────────────────────────────────────────────────────────────


def test_idea_lifecycle_and_reads_match(twins):
    twins.same("backlog_idea_create", title="Second idea", body="Explore ISS-001 and test-epic-001", tags=["a", "b"],
               status="exploring", related_tasks=["test-epic-001"], related_issues=["ISS-001"], created_by="user")
    twins.same("backlog_idea_create", title=" ")
    twins.same("backlog_idea_create", title="Tldr idea", tldr="Given")
    for field, value in (("title", "Renamed idea"), ("tags", "c"), ("status", "parking-lot"),
                         ("related_tasks", ""), ("promoted_to", "test-epic-002"), ("body", "Mentions ISS-001"),
                         ("archived", "maybe"), ("archived", "true"), ("archived", "false"), ("archived", "true"),
                         ("bogus", "x")):
        twins.same("backlog_idea_update", idea_id="IDEA-001", field=field, value=value)
    twins.same("backlog_idea_update", idea_id="IDEA-404", field="title", value="x")
    for kwargs in ({}, {"archived": True}, {"tag": "a"}, {"status": "exploring"}, {"related_task": "test-epic-001"},
                   {"related_issue": "ISS-001"}, {"verbose": True}, {"limit": 1}, {"idea_id": "IDEA-002"},
                   {"idea_id": "IDEA-404"}):
        twins.same("backlog_idea_list", **kwargs)
    for kwargs in ({}, {"verbose": True}, {"expand_links": True}, {"verbose": True, "expand_links": True},
                   {"sections": ["x"]}, {"sections": []}):
        twins.same("backlog_idea_get", idea_id="IDEA-002", **kwargs)
    twins.same("backlog_idea_get", idea_id="IDEA-404")
    _check(twins)


# ── Decisions ───────────────────────────────────────────────────────────────


def test_decision_lifecycle_and_reads_match(twins):
    twins.same("backlog_decision_create", title="Second decision", options=["One", "Two", "Three"],
               recommendation=2, related_issues=["ISS-001"], branch="feature/x", raised_in="h-1", body="Context")
    twins.same("backlog_decision_create", title="Too few", options=["Only"])
    twins.same("backlog_decision_create", title=" ", options=["A", "B"])
    twins.same("backlog_decision_create", title="Bad rec", options=["A", "B"], recommendation=5)
    twins.same("backlog_decision", action="update", decision_id="DEC-001", title="Edited", options=["X", "Y"],
               recommendation=2, body="Edited body")
    twins.same("backlog_decision", action="update", decision_id="DEC-001", recommendation=9)
    twins.same("backlog_decision", action="update", decision_id="DEC-404", title="x")
    twins.same("backlog_decision", action="resolve", decision_id="DEC-001")
    twins.same("backlog_decision", action="resolve", decision_id="DEC-001", resolved_with=7)
    twins.same("backlog_decision", action="resolve", decision_id="DEC-001", resolved_with=1,
               rationale="Because", resolved_in="h-2")
    twins.same("backlog_decision", action="resolve", decision_id="DEC-001", resolved_with=1)
    twins.same("backlog_decision", action="update", decision_id="DEC-001", title="Too late")
    twins.same("backlog_decision", action="drop", decision_id="DEC-002", reason="Not needed")
    twins.same("backlog_decision", action="drop", decision_id="DEC-404", reason="x")
    for kwargs in ({}, {"status": "all"}, {"status": "resolved"}, {"task_id": "test-epic-001", "status": "all"},
                   {"status": "all", "limit": 1}):
        twins.same("backlog_decision", action="list", **kwargs)
    for decision_id in ("DEC-001", "DEC-002", "DEC-404"):
        twins.same("backlog_decision", action="get", decision_id=decision_id)
    _check(twins)
