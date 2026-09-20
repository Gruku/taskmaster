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
from native_twins import make_twins

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
    for tool_args in (("backlog_update_task", {"task_id": "test-epic-001", "field": "status", "value": "in-progress"}),
                      ("backlog_update_task", {"task_id": "test-epic-001", "field": "locked_by", "value": PEER})):
        twins.same(tool_args[0], **tool_args[1])
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
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="in-progress")
    twins.same("backlog_update_task", task_id="test-epic-001", field="locked_by", value=PEER)
    for root in (twins.legacy, twins.native):
        heartbeat(root, PEER, last_seen=LONG_AGO, pid=DEAD_PID)
    for text in twins.same("backlog_pick_task", task_id="test-epic-001"):
        # The refusal stays a refusal — a claim is never stolen silently — but it
        # now says the holder is gone, so `force` is an informed choice.
        assert "locked by another session" in text and "expired" in text
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["ok"] is True and answer(text)["state"] == "released"
    twins.assert_state_matches()


def test_a_holder_that_cannot_be_judged_is_never_released_by_a_peer(twins):
    """No `sessions` row: unknown liveness, and unknown does not release."""
    twins.same("backlog_update_task", task_id="test-epic-001", field="status", value="in-progress")
    twins.same("backlog_update_task", task_id="test-epic-001", field="locked_by", value=PEER)
    for text in twins.same("backlog_claim", action="release", task_id="test-epic-001"):
        assert answer(text)["error"] == "claim_conflict"
    twins.assert_state_matches()


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


# ── Two writers, one task ───────────────────────────────────────────────────


def test_two_sessions_racing_for_one_task_have_exactly_one_winner(twins):
    """The tool layer is single-session by construction — `SESSION_ID` is
    process-global — so the race runs against the command the tool submits, which
    is where the atomicity has to live anyway.
    """
    from taskmaster.native.commands import execute, Conflict
    database = twins.native / ".taskmaster" / "local" / "store.db"

    def claim(index):
        envelope = {"protocol": 2, "store_id": "race", "caller_scope": f"sess-{index}",
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
