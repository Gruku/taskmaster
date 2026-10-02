"""User intent: session continuity served by the native core (N08) — handover
creation with thread derivation, supersession and the 30-entry index cap, handover
reads, the thread board and resume, continuity items and the last-session read —
must answer, refuse, commit and project exactly as the legacy tools do.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins


def _seed():
    bs.backlog_add_epic(epic_id="other", name="Other", done_when="x")
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Second", epic="other", phase="dev")
    bs.backlog_issue_create(title="An issue", severity="P2", evidence="e")
    bs.backlog_decision_create(title="A decision", options=["a", "b"])
    bs.backlog_handover_create(tldr="Seeded handover about first", task_ids=["test-epic-001"],
                               next_action="Continue first", body="## Decisions\nnone\n## Blockers\nnone")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


def test_handover_create_variants_match(twins):
    twins.same("backlog_handover_create", tldr="Derived thread from other epic", task_ids=["other-001", "test-epic-001"],
               next_action="Next", body="Mentions ISS-001 inline")
    twins.same("backlog_handover_create", tldr="Explicit thread", thread="My Thread", session_kind="context-handoff",
               options={"branch": "feature/x", "tip_commit": "abc1234", "context_size_at_write": "80%"})
    twins.same("backlog_handover_create", tldr="Supersedes the seed", supersedes="2026-09-17-seeded-handover-about-first",
               task_ids=["test-epic-001"])
    twins.same("backlog_handover_create", tldr="Supersedes nothing", supersedes="2020-01-01-ghost")
    twins.same("backlog_handover_create", tldr="Flagged", flag_for_review=True, options={"review_reason": "retro"})
    twins.same("backlog_handover_create", tldr="  ")
    twins.same("backlog_handover_create", tldr="Bad kind", session_kind="nap")
    twins.same("backlog_handover_create", tldr="From tldr only")
    _check(twins)


def test_handover_index_cap_archives_overflow(twins):
    for n in range(31):
        twins.same("backlog_handover_create", tldr=f"Overflow handover number {n}", task_ids=["other-001"])
    legacy, _native = twins.same("backlog_handover_list", limit=0)
    assert "2 older handovers" in legacy
    for kwargs in ({"format": "json"}, {"format": "json", "include_archived": True, "limit": 0},
                   {"include_archived": True}, {"include_archived": True, "limit": 0, "verbose": True},
                   {"include_archived": True, "task_id": "test-epic-001", "format": "json"},
                   {"task_id": "test-epic-001"}):
        twins.same("backlog_handover_list", **kwargs)
    legacy, _native = twins.same("backlog_handover_list", format="json", limit=0)
    envelope = json.loads(legacy)
    assert (envelope["returned"], envelope["archived_omitted"], envelope["truncated"]) == (30, 2, True)
    twins.same("backlog_thread_list", include_closed=True)
    _check(twins)


def test_handover_reads_match(twins):
    seeded = "2026-09-17-seeded-handover-about-first"
    twins.same("backlog_handover_create", tldr="Second handover", task_ids=["other-001"], session_kind="context-handoff")
    twins.same("backlog_handover_update_status", handover_id=seeded, status="closed", reason="done")
    for kwargs in ({}, {"verbose": True}, {"task_id": "other-001"}, {"session_kind": "context-handoff"},
                   {"since": "2026-01-01"}, {"since": "yesterday"}, {"status": "closed"}, {"status": "bogus"},
                   {"limit": 1}, {"task_id": "ghost"},
                   {"format": "json"}, {"format": "json", "limit": 1}, {"format": "json", "status": "bogus"},
                   {"format": "json", "task_id": "ghost"}, {"format": "xml"},
                   {"thread": "other"}, {"thread": "no-such-thread"}, {"format": "json", "thread": "Test Epic"},
                   {"until": "2026-09-17"}, {"until": "2026-09-16"}, {"until": "tomorrow"},
                   {"since": "2026-09-17", "until": "2026-09-17", "format": "json"},
                   {"latest_per_thread": True}, {"latest_per_thread": True, "format": "json"},
                   {"latest_per_thread": True, "status": "closed"}, {"include_archived": True}):
        twins.same("backlog_handover_list", **kwargs)
    twins.same("backlog_handover_create", tldr="Third handover, with git context", supersedes=seeded,
               body="Mentions ISS-001 inline", options={"branch": "feature/x", "tip_commit": "abc1234"})
    legacy, _native = twins.same("backlog_handover_list", format="json")
    newest = json.loads(legacy)["handovers"][0]
    assert (newest["branch"], newest["tip_commit"]) == ("feature/x", "abc1234")
    assert {"type": "supersedes", "target": seeded} in newest["links"]
    assert {"type": "references", "target": "ISS-001"} in newest["links"]
    twins.same("backlog_handover_list", verbose=True)
    for kwargs in ({}, {"verbose": True}, {"expand_links": True}, {"verbose": True, "expand_links": True},
                   {"sections": ["decisions", "blockers"]}, {"sections": []}, {"sections": ["nope"]}):
        twins.same("backlog_handover_get", handover_id=seeded, **kwargs)
    twins.same("backlog_handover_get", handover_id="2020-01-01-ghost")


def test_supersede_and_status_match(twins):
    seeded = "2026-09-17-seeded-handover-about-first"
    twins.same("backlog_handover_create", tldr="Later handover")
    later = "2026-09-17-later-handover"
    twins.same("backlog_handover_supersede", old_id=seeded, new_id=later)
    twins.same("backlog_handover_supersede", old_id=seeded, new_id="2020-01-01-ghost")
    twins.same("backlog_handover_supersede", old_id="2020-01-01-ghost", new_id=later)
    twins.same("backlog_handover_update_status", handover_id=later, status="closed", reason="shipped")
    twins.same("backlog_handover_update_status", handover_id=later, status="asleep")
    twins.same("backlog_handover_update_status", handover_id="2020-01-01-ghost", status="closed")
    _check(twins)


def test_same_thread_create_auto_supersedes_like_the_tool(twins):
    seeded = "2026-09-17-seeded-handover-about-first"
    legacy, _native = twins.same("backlog_handover_create", tldr="Next in the seeded thread", task_ids=["test-epic-001"])
    assert f"- Auto-superseded (same thread): {seeded}" in legacy
    pinned = "2026-09-17-next-in-the-seeded-thread"
    twins.same("backlog_handover_update_status", handover_id=pinned, status="open", reason="still using")
    legacy, _native = twins.same("backlog_handover_create", tldr="Third in the thread", thread="test-epic")
    assert "WARNING" in legacy and pinned in legacy and "Auto-superseded" not in legacy
    twins.same("backlog_handover_create", tldr="Explicitly supersedes the third", thread="test-epic",
               supersedes="2026-09-17-third-in-the-thread")
    twins.same("backlog_handover_create", tldr="A checkpoint", thread="test-epic", session_kind="auto-stage")
    twins.same("backlog_handover_create", tldr="Other line", thread="other-line")
    twins.same("backlog_handover_create", tldr="Supersedes across threads", thread="test-epic",
               supersedes="2026-09-17-other-line")
    twins.same("backlog_handover_list", format="json", limit=0)
    twins.same("backlog_thread_list", include_closed=True)
    _check(twins)


def test_threads_match(twins):
    seeded = "2026-09-17-seeded-handover-about-first"
    twins.same("backlog_handover_create", tldr="Explicit thread one", thread="parked-line")
    twins.same("backlog_thread_list")
    twins.same("backlog_thread_update", name="parked-line", status="parked", reason="later")
    twins.same("backlog_thread_update", name="parked-line", status="sleeping")
    twins.same("backlog_thread_update", name="no-such-thread", status="closed")
    twins.same("backlog_thread_list")
    twins.same("backlog_thread_list", include_closed=True)
    for ref in ("test-epic", "Parked Line", seeded, "2020-01-01-ghost"):
        twins.same("backlog_thread_resume", ref=ref)
    twins.same("backlog_handover_create", tldr="Reopens the parked line", thread="parked-line")
    twins.same("backlog_thread_list")
    _check(twins)


def test_completion_smart_closes_handovers_like_the_tool(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="lane", value="express")
    twins.same("backlog_pick_task", task_id="test-epic-001")
    twins.same("backlog_record_gate", task_id="test-epic-001", gate="review-gate", verdict="pass")
    twins.same("backlog_complete_task", task_id="test-epic-001")
    twins.same("backlog_handover_list", verbose=True)
    _check(twins)


def test_continuity_items_and_last_session_match(twins):
    for root in (twins.legacy, twins.native):
        (root / ".taskmaster" / "local" / "PROGRESS.md").write_text(
            "# Progress\n\n## Changelog\n\n### 2026-09-16 — Session A\n**Done:**\n- a\n\n### 2026-09-15 — Older\n- b\n",
            encoding="utf-8")
    twins.same("backlog_last_session")
    for kwargs in ({}, {"view": "time"}, {"include_auto_stage": True}):
        legacy, native = twins.call("backlog_continuity_items", **kwargs)
        assert json.loads(native) == json.loads(legacy)


def test_twins_start_without_a_session_bundle_leaked_by_an_earlier_test(tmp_path, monkeypatch):
    """Found by the N08 full suite: `test_bundle_pick` leaves the process-global
    session bundle set, which renamed every later auto-derived handover thread."""
    monkeypatch.setattr(bs, "_session_bundle", {"slug": "leaked-bundle"})
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_handover_create(
        tldr="Threaded", task_ids=[]))
    assert bs._get_session_bundle() is None
    legacy, native = twins.same("backlog_thread_list")
    assert "threaded" in native and "leaked-bundle" not in native
