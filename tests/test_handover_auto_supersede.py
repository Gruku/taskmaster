"""User intent: a thread has one live resume point (tm-audit-029). Writing a
handover into a thread that already has an open one supersedes the older open
handover(s) automatically — same mechanics as `supersedes=` — says so in the
create result, and leaves a hand-set status alone with a warning instead.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from tests.entity_helpers import write_handover


@pytest.fixture()
def project(tm_epic_phase):
    return tm_epic_phase / ".taskmaster" / "backlog.yaml"


def _create(**kwargs) -> tuple[str, str]:
    result = bs.backlog_handover_create(**kwargs)
    assert result.startswith("Handover written: "), result
    return result.splitlines()[0].removeprefix("Handover written: "), result


def _listed() -> dict:
    out = json.loads(bs.backlog_handover_list(format="json", limit=0))
    return {h["id"]: h for h in out["handovers"]}


def _body(handover_id) -> str:
    return bs.backlog_handover_get(handover_id, verbose=True).split("\n---\n", 1)[1]


def test_create_in_a_thread_supersedes_its_open_handover(project):
    first, _ = _create(tldr="first", thread="line-a")
    second, result = _create(tldr="second", thread="line-a")

    listed = _listed()
    assert (listed[first]["status"], listed[first]["superseded_by"]) == ("superseded", second)
    assert listed[second]["status"] == "open"
    assert _body(first).startswith(f"> **SUPERSEDED ") and f"[{second}](./{second}.md)" in _body(first)
    assert f"- Auto-superseded (same thread): {first}" in result.splitlines()


def test_other_threads_and_threadless_handovers_are_untouched(project):
    other, _ = _create(tldr="other line", thread="line-b")
    _second, result = _create(tldr="in a", thread="line-a")

    assert _listed()[other]["status"] == "open"
    assert "Auto-superseded" not in result and "WARNING" not in result


def test_a_hand_set_status_is_skipped_with_a_warning(project):
    pinned, _ = _create(tldr="pinned", thread="line-a")
    bs.backlog_handover_update_status(handover_id=pinned, status="open", reason="still using")
    newer, result = _create(tldr="newer", thread="line-a")

    listed = _listed()
    assert (listed[pinned]["status"], listed[pinned]["superseded_by"]) == ("open", "")
    assert "SUPERSEDED" not in _body(pinned)
    assert "Auto-superseded" not in result
    warning = [line for line in result.splitlines() if line.startswith("- WARNING:")]
    assert len(warning) == 1 and pinned in warning[0] and "set by hand" in warning[0]


def test_explicit_supersedes_still_works_and_is_not_applied_twice(project):
    first, _ = _create(tldr="first", thread="line-a")
    second, result = _create(tldr="second", thread="line-a", supersedes=first)

    lines = result.splitlines()
    assert f"- Superseded: {first}" in lines
    assert "Auto-superseded" not in result
    assert _listed()[first]["superseded_by"] == second
    assert _body(first).count("SUPERSEDED") == 1


def test_explicit_and_automatic_supersession_combine(project):
    elsewhere, _ = _create(tldr="elsewhere", thread="line-b")
    first, _ = _create(tldr="first", thread="line-a")
    second, result = _create(tldr="second", thread="line-a", supersedes=elsewhere)

    lines = result.splitlines()
    assert f"- Superseded: {elsewhere}" in lines
    assert f"- Auto-superseded (same thread): {first}" in lines
    listed = _listed()
    assert listed[elsewhere]["superseded_by"] == second and listed[first]["superseded_by"] == second


def test_every_older_open_handover_in_the_thread_is_superseded(project):
    # Written newest first, so neither supersedes the other and both stay open.
    newer, _ = write_handover(project, tldr="newer", thread="line-a", when="2026-01-02")
    older, _ = write_handover(project, tldr="older", thread="line-a", when="2026-01-01")
    closed, _ = write_handover(project, tldr="closed", thread="line-a", when="2025-12-31", session_kind="auto-stage")
    assert {_listed()[h]["status"] for h in (newer, older)} == {"open"}

    latest, result = _create(tldr="latest", thread="line-a")

    listed = _listed()
    assert [listed[h]["superseded_by"] for h in (newer, older)] == [latest, latest]
    assert (listed[closed]["status"], listed[closed]["superseded_by"]) == ("closed", "")
    assert f"- Auto-superseded (same thread): {newer}, {older}" in result.splitlines()


def test_a_handover_born_closed_supersedes_nothing(project):
    first, _ = _create(tldr="first", thread="line-a")
    _checkpoint, result = _create(tldr="checkpoint", thread="line-a", session_kind="auto-stage")

    assert _listed()[first]["status"] == "open"
    assert "Auto-superseded" not in result


def test_a_backdated_handover_does_not_supersede_a_newer_one(project):
    newer, _ = write_handover(project, tldr="newer", thread="line-a", when="2026-01-02")
    write_handover(project, tldr="backdated", thread="line-a", when="2026-01-01")

    assert (_listed()[newer]["status"], _listed()[newer]["superseded_by"]) == ("open", "")
