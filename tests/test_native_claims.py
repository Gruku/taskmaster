"""User intent: several agents work one backlog at once, so a claim has to be
atomic, renewable, releasable and reclaimable when its holder dies — and a claim
that cannot be *proven* dead must never be stolen, because the cost of guessing
is two agents editing one task.

These are the failures a single-threaded happy path cannot see: a renew that
lands after a peer already took the task, a holder whose process is gone, a
release that arrives twice, and a bundle that must move as one unit.
"""
from contextlib import closing
import os
import socket
import sqlite3

import pytest

from taskmaster.native import claims
from taskmaster.native.commands import execute, Conflict
from taskmaster.native.queries import Repository
from test_native_commands import native, envelope  # noqa: F401
from test_native_migration import legacy  # noqa: F401

DEAD_PID = 2 ** 30
ALPHA, PEER = "sess-alpha", "sess-peer"
LONG_AGO = "2000-01-01T00:00:00+00:00"


def run(connection, operation, arguments, key):
    return execute(connection, envelope(operation, arguments, key=key))


def fields(connection, ident):
    with Repository(connection).snapshot() as query:
        return query.get("task", ident)["fields"]


def heartbeat(connection, session, *, last_seen, pid=None, host=None):
    """A `sessions` row — the local-only liveness signal both stores already keep."""
    connection.execute("INSERT INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
                       "VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET "
                       "pid=excluded.pid,host=excluded.host,last_seen=excluded.last_seen",
                       (session, pid, host or socket.gethostname(), last_seen, last_seen, ".", None))


def burn_ttl(connection, ident):
    """Push one task's stored expiry into the past, as a real clock would."""
    connection.execute(
        "UPDATE entity_extensions SET value_json=json_quote('2000-01-01T00:00:00Z') WHERE field=? "
        "AND entity_key=(SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?)",
        (claims.EXPIRES_FIELD, ident))


@pytest.fixture
def workspace(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        run(connection, "phase.create", {"phase_id": "foundation", "name": "Foundation"}, "phase")
        run(connection, "epic.create", {"epic_id": "demo", "name": "Demo", "done_when": "shipped"}, "epic")
        for index, title in enumerate(("First", "Second", "Third"), start=1):
            run(connection, "task.create", {"title": title, "epic": "demo", "phase": "foundation"}, f"t{index}")
    return native


def state_of(connection, ident, session=ALPHA):
    return claims.read(fields(connection, ident), task_id=ident, session=session, connection=connection)


# ── The expiry predicate ────────────────────────────────────────────────────


def test_a_claim_with_no_holder_is_no_claim_at_all(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        state = state_of(connection, "demo-001")
    assert state.holder == "" and state.expired is False and state.blocking is False


def test_a_holder_with_a_dead_local_pid_is_proven_expired_before_its_ttl_burns_down(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        heartbeat(connection, PEER, last_seen=LONG_AGO, pid=DEAD_PID)
        state = state_of(connection, "demo-001")
    assert state.holder == PEER and state.live is False and state.expired is True
    # The TTL has not passed — liveness is the authority for a quick reclaim.
    assert claims.parse_stamp(state.expires_at) > claims.now_utc()


def test_a_dead_local_process_is_proven_dead_from_the_holder_id_with_no_sessions_row(workspace):
    """The production shape: `locked_by` carries `backlog_server.SESSION_ID`,
    which never appears in `sessions` (that table is keyed by the store's own,
    separately generated id). If liveness needed a row, the fast path would
    never fire on a real project — so the holder id itself is read."""
    holder = f"{socket.gethostname()}-{DEAD_PID}-abcdef12"
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.update", {"id": "demo-001", "field": "locked_by", "value": holder}, "lock")
        assert claims.session_row(connection, holder) is None
        state = state_of(connection, "demo-001")
    assert state.live is False and state.expired is True


def test_a_live_local_process_is_proven_live_from_the_holder_id_alone(workspace):
    holder = f"{socket.gethostname()}-{os.getpid()}-abcdef12"
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.update", {"id": "demo-001", "field": "locked_by", "value": holder}, "lock")
        state = state_of(connection, "demo-001")
    assert state.live is True and state.expired is False and state.blocking is True


def test_a_holder_id_naming_another_machine_cannot_be_judged_from_the_id(workspace):
    """A pid is only meaningful on the machine that issued it — asking this host
    about a remote pid would be a coin flip dressed as proof."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.update", {"id": "demo-001", "field": "locked_by",
                                        "value": f"some-other-box-{os.getpid()}-abcdef12"}, "lock")
        state = state_of(connection, "demo-001")
    assert state.live is None and state.expired is False


def test_a_holder_that_cannot_be_judged_keeps_its_claim(workspace):
    """No `sessions` row at all: the answer is unknown, and unknown does not steal."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        state = state_of(connection, "demo-001")
    assert state.live is None and state.expired is False and state.blocking is True


def test_a_live_pid_on_this_host_holds_the_claim_even_with_a_stale_heartbeat(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        heartbeat(connection, PEER, last_seen=LONG_AGO, pid=os.getpid())
        state = state_of(connection, "demo-001")
    assert state.live is True and state.expired is False


def test_a_passed_ttl_expires_a_claim_whose_holder_cannot_be_judged(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER, "ttl_seconds": 60}, "pick")
        burn_ttl(connection, "demo-001")
        state = state_of(connection, "demo-001")
    assert state.live is None and state.expired is True


def test_a_migrated_lock_with_no_expiry_stands_until_a_human_forces_it(workspace):
    """Today's rows carry `locked_by` and no expiry. Nothing about them can be
    proven, so nothing about them may be stolen — `force` stays the escape hatch."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.update", {"id": "demo-001", "field": "locked_by", "value": PEER}, "lock")
        state = state_of(connection, "demo-001")
    assert state.expires_at == "" and state.expired is False and state.blocking is True


# ── Renew ───────────────────────────────────────────────────────────────────


def test_pick_stamps_an_expiry_and_renew_moves_it_to_now_plus_ttl_not_past_it(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 3600}, "pick")
        first = fields(connection, "demo-001")[claims.EXPIRES_FIELD]
        run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 120}, "renew")
        second = fields(connection, "demo-001")[claims.EXPIRES_FIELD]
    # `now + ttl`, never `expires + ttl`: a shorter renew must shorten the lease,
    # or a chatty agent could push its claim arbitrarily far into the future.
    assert claims.parse_stamp(second) < claims.parse_stamp(first)
    assert claims.parse_stamp(second) > claims.now_utc()


def test_renew_refuses_a_task_this_session_does_not_hold(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        heartbeat(connection, PEER, last_seen=claims.stamp(claims.now_utc()), pid=os.getpid())
        with pytest.raises(Conflict, match="sess-peer"):
            run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA}, "renew")
        assert fields(connection, "demo-001")["locked_by"] == PEER


def test_renew_refuses_a_task_that_is_not_claimed(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        with pytest.raises(ValueError, match="not claimed"):
            run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA}, "renew")


def test_a_renew_that_lands_after_a_peer_took_the_task_is_a_conflict(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 60}, "pick")
        run(connection, "task.pick", {"id": "demo-001", "session": PEER, "force": True}, "steal")
        with pytest.raises(Conflict, match="sess-peer"):
            run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA}, "renew")


def test_the_holder_may_renew_its_own_expired_claim(workspace):
    """Expiry governs who *else* may take a claim. Nothing took this one."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 60}, "pick")
        burn_ttl(connection, "demo-001")
        run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA}, "renew")
        assert claims.parse_stamp(fields(connection, "demo-001")[claims.EXPIRES_FIELD]) > claims.now_utc()


@pytest.mark.parametrize("ttl", [-1, 0.5, "3600", claims.MAX_TTL_SECONDS + 1, claims.MIN_TTL_SECONDS - 1])
def test_a_ttl_outside_the_contract_is_refused_before_anything_is_written(workspace, ttl):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA}, "pick")
        with pytest.raises(ValueError, match="ttl_seconds"):
            run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA, "ttl_seconds": ttl}, "renew")


# ── Release ─────────────────────────────────────────────────────────────────


def test_release_clears_the_holder_and_the_expiry_but_not_the_status(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA}, "pick")
        run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release")
        task = fields(connection, "demo-001")
    # Releasing gives up the lock; it does not un-pick the work.
    assert "locked_by" not in task and claims.EXPIRES_FIELD not in task
    assert task["status"] == "in-progress"


def test_a_second_release_is_idempotent_and_commits_nothing(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA}, "pick")
        run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release")
        before = connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]
        again = run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release-2")
        assert again["affected"] == []
        assert connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0] == before


def test_release_by_a_non_holder_is_refused_while_the_claim_stands(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        with pytest.raises(Conflict, match="sess-peer"):
            run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release")
        assert fields(connection, "demo-001")["locked_by"] == PEER


def test_release_by_a_non_holder_reclaims_a_claim_that_is_proven_expired(workspace):
    """The reclaim path for a dead holder that does not require `force`."""
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        heartbeat(connection, PEER, last_seen=LONG_AGO, pid=DEAD_PID)
        run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release")
        assert "locked_by" not in fields(connection, "demo-001")


# ── Bundles move as one unit ────────────────────────────────────────────────


@pytest.fixture
def bundled(workspace):
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        for index, ident in enumerate(("demo-001", "demo-002"), start=1):
            run(connection, "task.update", {"id": ident, "field": "bundle", "value": "pair"}, f"b{index}")
    return workspace


def test_a_bundle_renews_every_member_in_one_commit(bundled):
    with closing(sqlite3.connect(bundled, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 3600}, "pick")
        before = {i: fields(connection, i)[claims.EXPIRES_FIELD] for i in ("demo-001", "demo-002")}
        receipt = run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA,
                                                       "ttl_seconds": 120}, "renew")
        after = {i: fields(connection, i)[claims.EXPIRES_FIELD] for i in ("demo-001", "demo-002")}
    assert sorted(a["id"] for a in receipt["affected"]) == ["demo-001", "demo-002"]
    assert all(claims.parse_stamp(after[i]) < claims.parse_stamp(before[i]) for i in before)


def test_a_bundle_member_held_by_a_peer_refuses_the_whole_renew(bundled):
    with closing(sqlite3.connect(bundled, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 3600}, "pick")
        run(connection, "task.update", {"id": "demo-002", "field": "locked_by", "value": PEER}, "steal")
        before = fields(connection, "demo-001")[claims.EXPIRES_FIELD]
        with pytest.raises(Conflict, match="demo-002"):
            run(connection, "task.claim_renew", {"id": "demo-001", "session": ALPHA, "ttl_seconds": 120}, "renew")
        assert fields(connection, "demo-001")[claims.EXPIRES_FIELD] == before


def test_a_bundle_releases_every_member_in_one_commit(bundled):
    with closing(sqlite3.connect(bundled, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": ALPHA}, "pick")
        receipt = run(connection, "task.claim_release", {"id": "demo-001", "session": ALPHA}, "release")
        assert sorted(a["id"] for a in receipt["affected"]) == ["demo-001", "demo-002"]
        assert all("locked_by" not in fields(connection, i) for i in ("demo-001", "demo-002"))


# ── The blocker seam ────────────────────────────────────────────────────────


def test_claim_state_reaches_the_mandatory_resolver_as_a_blocker(workspace):
    from taskmaster.native import blockers
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        heartbeat(connection, PEER, last_seen=claims.stamp(claims.now_utc()), pid=os.getpid())
        live = state_of(connection, "demo-001")
        heartbeat(connection, PEER, last_seen=LONG_AGO, pid=DEAD_PID)
        dead = state_of(connection, "demo-001")
    held = blockers.resolve(blockers.Facts(task={"id": "demo-001"}, dependencies={}, bugs=[], handovers=[],
                                           claim=live.as_blocker(), session=ALPHA))
    assert [(b.kind, b.extra.get("by")) for b in held.blockers] == [("claim", PEER)]
    freed = blockers.resolve(blockers.Facts(task={"id": "demo-001"}, dependencies={}, bugs=[], handovers=[],
                                            claim=dead.as_blocker(), session=ALPHA))
    assert freed.clear is True


def test_an_unjudgeable_holder_blocks_as_a_claim_not_as_an_unanswered_producer(workspace):
    """The claim is determinate — held, unexpired — even though its holder's
    liveness is not. Reporting it as `unknown` would misattribute a known fact."""
    from taskmaster.native import blockers
    with closing(sqlite3.connect(workspace, isolation_level=None)) as connection:
        run(connection, "task.pick", {"id": "demo-001", "session": PEER}, "pick")
        state = state_of(connection, "demo-001")
    resolution = blockers.resolve(blockers.Facts(task={"id": "demo-001"}, dependencies={}, bugs=[],
                                                 handovers=[], claim=state.as_blocker(), session=ALPHA))
    assert [(b.kind, b.extra.get("by"), b.extra.get("live")) for b in resolution.blockers] \
        == [("claim", PEER, None)]
