"""User intent: the claim surface an agent actually calls — `backlog_claim` and
`ttl_seconds` on `backlog_pick_task` — has to work on the stores that exist
today. Every real project is still legacy, so both paths are implemented and
both answer the same shape.

The interesting assertions are the ones a happy path cannot make: a release from
a session that does not hold the claim, a refusal that tells the truth about a
dead holder, and two writers racing for one task with exactly one winner.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import socket
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import claims
from native_twins import hand_set_holder, make_twins

PEER = "sess-peer"
DEAD_PID = 2 ** 30
LONG_AGO = "2000-01-01T00:00:00+00:00"


def _seed():
    bs.backlog_add_task(title="Claimable", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Bundled one", epic="test-epic", phase="dev", bundle="pair")
    bs.backlog_add_task(title="Bundled two", epic="test-epic", phase="dev", bundle="pair")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def answer(text):
    return json.loads(text)


def claimed_by(twins, holder, task_id="test-epic-001", **kwargs):
    """A peer's claim, taken the only way a tool takes one: that session picks."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bs, "SESSION_ID", holder)
        for text in twins.same("backlog_pick_task", task_id=task_id, **kwargs):
            assert text.startswith(f"Picked `{task_id}`"), text


def heartbeat(root, session, *, last_seen, pid):
    """A `sessions` row in whichever store this root carries — both keep one."""
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db", isolation_level=None)) as connection:
        connection.execute("INSERT INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
                           "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(session) DO UPDATE SET "
                           "pid=excluded.pid,host=excluded.host,last_seen=excluded.last_seen",
                           (session, pid, socket.gethostname(), last_seen, last_seen, "."))


# ── The tool's shape, on both stores ────────────────────────────────────────


def test_pick_stamps_a_claim_that_status_reports_and_release_gives_back(twins):
    twins.same("backlog_pick_task", task_id="test-epic-001", ttl_seconds=3600)
    held = twins.same("backlog_claim", action="status")
    for text in held:
        rows = answer(text)["claims"]
        assert [row["task_id"] for row in rows] == ["test-epic-001"]
        assert rows[0]["holder"] == bs.SESSION_ID and rows[0]["expires_at"]
    twins.same("backlog_claim", action="release", task_id="test-epic-001")
    for text in twins.same("backlog_claim", action="status"):
        assert answer(text)["claims"] == []
    twins.assert_state_matches()
    twins.assert_files_match()


def test_renew_answers_where_the_claim_moved_from_and_to(twins):
    twins.same("backlog_pick_task", task_id="test-epic-001", ttl_seconds=3600)
    for text in twins.same("backlog_claim", action="renew", task_id="test-epic-001", ttl_seconds=120):
        renewed = answer(text)
        assert renewed["ok"] is True and renewed["state"] == "held"
        assert renewed["holder"] == bs.SESSION_ID
        assert claims.parse_stamp(renewed["expires_at"]) < claims.parse_stamp(renewed["renewed_from"])
    twins.assert_state_matches()


def test_a_second_release_is_idempotent_and_says_the_claim_is_already_gone(twins):
    twins.same("backlog_pick_task", task_id="test-epic-001")
    twins.same("backlog_claim", action="release", task_id="test-epic-001")
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        again = answer(text)
        assert again["ok"] is True and again["state"] == "released" and again["holder"] == ""
    twins.assert_state_matches()


def test_a_peers_standing_claim_refuses_renew_and_release_with_a_usable_hint(twins):
    claimed_by(twins, PEER)
    for action in ("renew", "release"):
        for text in twins.same("backlog_claim", action=action, task_id="test-epic-001"):
            refusal = answer(text)
            assert refusal["ok"] is False and refusal["error"] == "claim_conflict"
            assert refusal["holder"] == PEER and refusal["live"] is None
            assert "force=True" in refusal["hint"]
    twins.assert_state_matches()


def test_an_unclaimed_task_cannot_be_renewed(twins):
    for text in twins.same("backlog_claim", action="renew", task_id="test-epic-001"):
        refusal = answer(text)
        assert refusal["ok"] is False and refusal["error"] == "not_claimed"


def test_a_missing_task_and_an_unknown_action_refuse_the_same_way_on_both_stores(twins):
    for text in twins.same("backlog_claim", action="status", task_id="ghost-001"):
        assert answer(text)["error"] == "not_found"
    for text in twins.same("backlog_claim", action="renew", task_id=""):
        assert answer(text)["error"] == "task_required"


@pytest.mark.parametrize("ttl", [-1, 5, claims.MAX_TTL_SECONDS + 1])
def test_a_ttl_outside_the_contract_is_refused_by_both_tools(twins, ttl):
    for text in twins.same("backlog_claim", action="renew", task_id="test-epic-001", ttl_seconds=ttl):
        assert answer(text)["error"] == "invalid_ttl"
    legacy, native = twins.call("backlog_pick_task", task_id="test-epic-001", ttl_seconds=ttl)
    assert "ttl_seconds" in legacy and legacy.startswith("Error:") and native.startswith("Error:")


# ── A dead holder, and what the refusals say about it ───────────────────────


def test_a_proven_dead_holder_is_released_by_a_peer_and_named_in_picks_refusal(twins):
    claimed_by(twins, PEER)
    for root in (twins.legacy, twins.native):
        heartbeat(root, PEER, last_seen=LONG_AGO, pid=DEAD_PID)
    for text in twins.same("backlog_pick_task", task_id="test-epic-001"):
        # The refusal stays a refusal — a claim is never stolen silently — but it
        # now says the holder is gone, so `force` is an informed choice.
        assert "locked by another session" in text and "expired" in text
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["ok"] is True and answer(text)["state"] == "released"
    twins.assert_state_matches()


def test_a_dead_holder_is_reclaimable_through_the_tools_with_no_sessions_row(twins):
    """End to end, in the shape production actually produces: the holder is a
    `SESSION_ID`, nothing ever wrote it into `sessions`, and its process is gone.
    Before the holder id was read directly this case was unjudgeable, so the
    dead-process fast path could not fire on any real project."""
    holder = f"{socket.gethostname()}-{DEAD_PID}-abcdef12"
    claimed_by(twins, holder)
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-001"):
        assert answer(text)["expired"] is True and answer(text)["live"] is False
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["ok"] is True and answer(text)["state"] == "released"
    twins.same("backlog_pick_task", task_id="test-epic-001")
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-001"):
        assert answer(text)["holder"] == bs.SESSION_ID
    twins.assert_state_matches()


def test_a_holder_that_cannot_be_judged_is_never_released_by_a_peer(twins):
    """No `sessions` row: unknown liveness, and unknown does not release."""
    claimed_by(twins, PEER)
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["error"] == "claim_conflict"
    twins.assert_state_matches()


# ── An expiry belongs to the holder it was stamped for ──────────────────────


def _handed_over(tmp_path, monkeypatch, holder):
    """A claim of this session's, paused (its expiry stays behind), then a
    holder written by hand — the only way left to a holder no pick stamped,
    since no tool but the claim tools writes `locked_by` (N09 §4f)."""
    def seed():
        _seed()
        bs.backlog_pick_task(task_id="test-epic-001", ttl_seconds=claims.MIN_TTL_SECONDS)
        bs.backlog_update_task(task_id="test-epic-001", field="status", value="todo")
        bs.backlog_update_task(task_id="test-epic-001", field="status", value="in-progress")
        hand_set_holder("test-epic-001", holder)
    return make_twins(tmp_path, monkeypatch, seed)


def test_a_new_holder_never_inherits_the_expiry_an_earlier_claim_left_behind(tmp_path, monkeypatch):
    """Only pick and renew stamp `claim_expires` and only release clears it. So a
    paused claim leaves its expiry behind, and when the task was later handed to
    someone else that burnt expiry made the new holder's live claim read as
    expired: context reported the task clear, and any peer could release it
    without `force`."""
    twins = _handed_over(tmp_path, monkeypatch, PEER)
    twins.same("backlog_claim", action="status")  # a minute passes: the stale expiry has burnt
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-001"):
        state = answer(text)
        # No expiry was ever stamped for this holder, and it cannot be judged, so
        # the claim stands on liveness alone — as a migrated `locked_by` does.
        assert state["holder"] == PEER and state["expires_at"] == "" and state["expired"] is False
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["error"] == "claim_conflict"
    for text in twins.same("backlog_context", focus="test-epic-001", scope="task", include=[]):
        mandatory = answer(text)["mandatory"]
        assert mandatory["clear"] is False
        assert ("claim", "test-epic-001") in {(b["kind"], b["id"]) for b in mandatory["blockers"]}


def test_a_new_holder_with_no_expiry_of_its_own_still_expires_when_proven_dead(tmp_path, monkeypatch):
    """Ignoring a foreign expiry must not turn into ignoring expiry: liveness is
    still the authority, so a dead new holder releases without `force`."""
    twins = _handed_over(tmp_path, monkeypatch, f"{socket.gethostname()}-{DEAD_PID}-abcdef12")
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-001"):
        assert answer(text)["expires_at"] == "" and answer(text)["expired"] is True
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["ok"] is True
    twins.assert_state_matches()


def test_a_claims_own_burnt_expiry_still_expires_it(twins):
    """The binding narrows which expiry counts; it does not stop one counting."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bs, "SESSION_ID", PEER)
        twins.same("backlog_pick_task", task_id="test-epic-001", ttl_seconds=claims.MIN_TTL_SECONDS)
    twins.same("backlog_claim", action="status")  # a minute passes
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-001"):
        assert answer(text)["holder"] == PEER and answer(text)["expired"] is True
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["ok"] is True


# ── Bundles ─────────────────────────────────────────────────────────────────


def test_a_bundle_claim_renews_and_releases_every_member(twins):
    twins.same("backlog_pick_task", task_id="test-epic-002", ttl_seconds=3600)
    for text in twins.same("backlog_claim", action="renew", task_id="test-epic-002", ttl_seconds=120):
        assert answer(text)["members"] == ["test-epic-002", "test-epic-003"]
    for text in twins.same("backlog_claim", action="status"):
        assert [row["task_id"] for row in answer(text)["claims"]] == ["test-epic-002", "test-epic-003"]
    twins.same("backlog_claim", action="release", task_id="test-epic-002")
    for text in twins.same("backlog_claim", action="status"):
        assert answer(text)["claims"] == []
    twins.assert_state_matches()


def test_a_bundle_renew_extends_only_the_members_this_session_holds(twins):
    """A claim without a pick is not a state this system has (§2.5). A member
    that left the bundle's claim — its status changed, so its holder was dropped
    — must not be re-acquired by renewing its sibling."""
    twins.same("backlog_pick_task", task_id="test-epic-002", ttl_seconds=3600)
    twins.same("backlog_update_task", task_id="test-epic-003", field="status", value="todo")
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-003"):
        assert answer(text)["holder"] == ""
    for text in twins.same("backlog_claim", action="renew", task_id="test-epic-002", ttl_seconds=120):
        assert answer(text)["ok"] is True and answer(text)["holder"] == bs.SESSION_ID
    for text in twins.same("backlog_claim", action="status", task_id="test-epic-003"):
        assert answer(text)["holder"] == "" and answer(text)["state"] == "released"
    for text in twins.same("backlog_claim", action="status"):
        assert [row["task_id"] for row in answer(text)["claims"]] == ["test-epic-002"]
    twins.assert_state_matches()


# ── Two writers, one task ───────────────────────────────────────────────────


def test_two_sessions_racing_for_one_task_have_exactly_one_winner(twins):
    """The tool layer is single-session by construction — `SESSION_ID` is
    process-global — so the race runs against the command the tool submits, which
    is where the atomicity has to live anyway.
    """
    from taskmaster.native.commands import execute, Conflict
    database = twins.native / ".taskmaster" / "local" / "store.db"
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        store_id = connection.execute(
            "SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]

    def claim(index):
        envelope = {"protocol": 2, "store_id": store_id, "caller_scope": f"sess-{index}",
                    "request_id": f"race-{index}", "operation": "task.pick",
                    "arguments": {"id": "test-epic-001", "session": f"sess-{index}"},
                    "expected_revisions": []}
        with closing(sqlite3.connect(database, isolation_level=None, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout=30000")
            try:
                execute(connection, envelope)
            except (Conflict, ValueError):
                return None
            return f"sess-{index}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [name for name in pool.map(claim, range(8)) if name]
    assert len(winners) == 1
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        holders = connection.execute(
            "SELECT DISTINCT json_extract(t.locked_by_json,'$') FROM task_operational t "
            "JOIN entity_core c ON c.entity_key=t.entity_key WHERE c.public_id='test-epic-001'").fetchall()
    assert [row[0] for row in holders] == winners
