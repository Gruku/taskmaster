"""User intent: the agent tool-use pilot (tm-audit-030) showed where agents were misled
or left to wander — an open line of work vanishing from the thread board, a stale "last
session", no way to ask "what changed since X", search hits with no hint of where they
matched. Each fix must hold on the legacy store and on its native copy alike.
"""
from __future__ import annotations

import datetime as _datetime
import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import CLOCK, make_twins

ATLAS = "2026-09-17-atlas-budget-settled"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")
    bs.backlog_note(action="create", text="Seeded note", pinned=False)
    # The oldest handover: thirty newer ones push it out of the 30-entry index
    # while it is still the open resume point of its thread.
    bs.backlog_handover_create(
        tldr="Atlas budget settled", thread="atlas-budget", next_action="Split the foliage atlas",
        body="## Decisions\nThe schema version lives in the manifest header because loaders read it first.\n")
    for n in range(30):
        bs.backlog_handover_create(tldr=f"Filler handover {n:02d}", thread=f"filler-{n:02d}")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


# ── An open thread outside the handover index ───────────────────────────────


def test_the_thread_board_lists_a_thread_whose_only_open_handover_is_archived(twins):
    listed, _native = twins.same("backlog_handover_list", limit=0)
    assert ATLAS not in listed and "1 older handovers" in listed  # the premise
    board, _native = twins.same("backlog_thread_list")
    assert "**atlas-budget**" in board and "next: Split the foliage atlas" in board


def test_resume_reaches_a_thread_whose_only_open_handover_is_archived(twins):
    resumed, _native = twins.same("backlog_thread_resume", ref="atlas-budget")
    assert f"- newest: {ATLAS}" in resumed and "manifest header" in resumed


def test_a_thread_outside_the_index_can_be_parked(twins):
    answer, _native = twins.same("backlog_thread_update", name="atlas-budget", status="parked", reason="later")
    assert answer.startswith("Thread atlas-budget → parked.")
    board, _native = twins.same("backlog_thread_list")
    assert "**atlas-budget** [parked]" in board
    twins.assert_state_matches()
    twins.assert_files_match()


def test_a_closed_archived_handover_does_not_bring_its_thread_back(twins):
    twins.same("backlog_handover_update_status", handover_id=ATLAS, status="closed", reason="done")
    board, _native = twins.same("backlog_thread_list", include_closed=True)
    assert "atlas-budget" not in board


# ── A resume that misses ────────────────────────────────────────────────────


def test_a_resume_miss_names_the_closest_threads(twins):
    missed, _native = twins.same("backlog_thread_resume", ref="atlas")
    assert missed.startswith("No thread or handover matches 'atlas'.")
    assert "atlas-budget" in missed and "filler-00" not in missed


def test_a_resume_miss_with_nothing_close_lists_open_threads(twins):
    missed, _native = twins.same("backlog_thread_resume", ref="zzz-nothing-like-it")
    assert "Open threads:" in missed and "filler-29" in missed
    assert "backlog_thread_list" in missed  # the list is capped; the board is the whole answer


# ── The dashboard points at the thread board ────────────────────────────────


def test_the_dashboard_points_at_the_thread_board(twins):
    dashboard, _native = twins.same("backlog_status")
    assert "backlog_thread_list" in dashboard
    assert "| Workstream |" not in dashboard  # epics are not the lines of work


# ── The last session, and whether it is the newest work ─────────────────────


def _progress(twins, text):
    for root in (twins.legacy, twins.native):
        (root / ".taskmaster" / "local" / "PROGRESS.md").write_text(text, encoding="utf-8")


def test_last_session_says_when_handovers_are_newer_than_the_entry(twins):
    _progress(twins, "# Progress\n\n## Changelog\n\n### 2026-09-15 — Session A\n- a\n\n### 2026-09-14 — Older\n- b\n")
    answer, _native = twins.same("backlog_last_session")
    assert "### 2026-09-15 — Session A" in answer
    assert "30 handovers are newer than this entry" in answer
    assert "2026-09-17-filler-handover-29" in answer and "backlog_handover_list" in answer


def test_last_session_adds_nothing_when_no_handover_is_newer(twins):
    _progress(twins, "# Progress\n\n## Changelog\n\n### 2026-09-17 — Today\n- a\n")
    answer, _native = twins.same("backlog_last_session")
    assert answer == "**Last Session:**\n\n### 2026-09-17 — Today\n- a"


# ── What changed since a time or an entity ──────────────────────────────────


def _changes(twins, root, **kwargs):
    with twins.at(root):
        return json.loads(bs.backlog_changes_since(**kwargs))


def _moved(answer):
    return [(c["kind"], c["id"], c["op"]) for commit in answer["commits"] for c in commit["changes"]]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_an_entity_starts_right_after_it_was_created(twins, side):
    root = getattr(twins, side)
    with twins.at(root):
        bs.backlog_note(action="create", text="The anchor", pinned=False)
        bs.backlog_note(action="create", text="Written after the anchor", pinned=False)
    moved = _moved(_changes(twins, root, since="NOTE-002", limit=500))
    assert ("note", "NOTE-003", "create") in moved
    assert ("note", "NOTE-002", "create") not in moved and ("note", "NOTE-001", "create") not in moved


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_a_timestamp_starts_at_the_first_change_at_or_after_it(twins, side):
    root = getattr(twins, side)
    CLOCK.update(at=CLOCK["at"] + _datetime.timedelta(minutes=5), tick=True)
    moment = CLOCK["at"].isoformat()
    with twins.at(root):
        bs.backlog_note(action="create", text="Written after the moment", pinned=False)
    assert _moved(_changes(twins, root, since=moment, limit=500)) == [("note", "NOTE-002", "create")]


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_a_date_covers_that_day_onward(twins, side):
    root = getattr(twins, side)
    everything = _moved(_changes(twins, root, since_seq=0, limit=500))
    assert _moved(_changes(twins, root, since="2020-01-01", limit=500)) == everything
    assert _moved(_changes(twins, root, since="2999-01-01", limit=500)) == []


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_changes_since_refuses_an_anchor_it_cannot_place(twins, side):
    root = getattr(twins, side)
    unknown = _changes(twins, root, since="NOTE-999")
    assert set(unknown) == {"error"} and "NOTE-999" in unknown["error"]
    assert set(_changes(twins, root, since="NOTE-001", since_seq=0)) == {"error"}
    start = _changes(twins, root)
    assert set(_changes(twins, root, since="NOTE-001", cursor=start["cursor"])) == {"error"}


# ── Where a search hit matched ──────────────────────────────────────────────


def test_a_search_hit_in_the_body_alone_shows_the_matching_text(twins):
    found, _native = twins.same("backlog_search", query="loaders")
    lines = found.splitlines()
    hit = next(i for i, line in enumerate(lines) if ATLAS in line)
    assert "manifest header because loaders read it first" in lines[hit + 1]


def test_a_search_hit_in_the_title_adds_no_snippet(twins):
    found, _native = twins.same("backlog_search", query="settled")
    assert found.splitlines() == ["**1 match** for `settled`:", f"- `{ATLAS}` — Atlas budget settled (handover, open)"]
