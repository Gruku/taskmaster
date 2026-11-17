# User intent: pin the claim keeper's rules (team-server-010 D3-D5): renew while connected, shorten on disconnect,
# one grace after a restart, never extend, never touch host-shaped or terminal claims.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from taskmaster.native import claims
from taskmaster.serve import claim_keeper as ck
from taskmaster.serve.claim_keeper import Action, Outage, Row, decide, plan

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
ANN, ANN2, BOB = "ann@" + "a" * 16, "ann@" + "f" * 16, "bob@" + "b" * 16


def row(task="t-1", holder=ANN, left=None, status="in-progress", bundle="", revision=1):
    return Row(task, revision, holder, status, bundle, None if left is None else NOW + timedelta(minutes=left))


@pytest.mark.parametrize("rows, connected, expected", [
    ([row(left=59)], True, "renew"), ([row(left=61)], True, None), ([row(left=None)], True, "renew"),
    ([row(left=240)], False, "shorten"), ([row(left=10)], False, None), ([row(left=-5)], False, None),
    ([row(left=None)], False, "shorten"),
    ([row(holder="box-1-deadbeef", left=240)], False, None), ([row(holder="ann@viewer", left=240)], False, None),
    ([row(holder="ann@read", left=1)], True, None),
    ([row(status="done", left=240)], False, None), ([row(status="archived", left=1)], True, None),
    ([row(left=240), row("t-2", left=5)], False, None),                 # M8: never extends a short member
    ([row(left=240), row("t-2", left=30)], True, "renew"),
    ([row(left=240), row("t-2", left=240)], False, "shorten"),
])
def test_steady_rules(rows, connected, expected):
    assert decide(rows, now=NOW, connected=connected) == expected


def test_startup_rules():
    at = NOW - timedelta(hours=2)
    before, after = row(left=-130), row(left=100)                        # stamps relative to NOW: before / after `at`
    assert decide([before], now=NOW, connected=False, outage=Outage(at, frozenset({ANN}))) == "grace"
    assert decide([after], now=NOW, connected=False, outage=Outage(at, frozenset())) == "grace"
    assert decide([before], now=NOW, connected=False, outage=Outage(at, frozenset())) is None
    assert decide([row(left=None)], now=NOW, connected=False, outage=Outage(at, frozenset())) == "grace"
    assert decide([after, row("t-2", left=-130)], now=NOW, connected=False, outage=Outage(at)) is None
    assert decide([before], now=NOW, connected=False, outage=Outage(None)) == "grace"
    assert decide([row(status="done")], now=NOW, connected=False, outage=Outage(None)) is None
    assert decide([row(holder="box-1-deadbeef")], now=NOW, connected=False, outage=Outage(None)) is None


def test_plan_groups_a_bundle_and_keeps_holders_apart():
    rows = [row("t-1", ANN, 240, bundle="b", revision=3), row("t-2", ANN, 240, bundle="b", revision=5),
            row("t-3", BOB, 240, bundle="b", revision=7), row("t-4", ANN2, 240, revision=1)]
    actions = plan(rows, now=NOW, is_connected=lambda holder: False)
    assert sorted(actions, key=lambda a: a.holder) == [
        Action("shorten", ANN, claims.DISCONNECTED_TTL_SECONDS, (("t-1", 3), ("t-2", 5))),
        Action("shorten", ANN2, claims.DISCONNECTED_TTL_SECONDS, (("t-4", 1),)),
        Action("shorten", BOB, claims.DISCONNECTED_TTL_SECONDS, (("t-3", 7),))]
    renewed = plan([row(left=10)], now=NOW, is_connected=lambda holder: True)
    assert renewed == [Action("renew", ANN, claims.DEFAULT_TTL_SECONDS, (("t-1", 1),))]


def test_heartbeat_round_trip_and_rejections(tmp_path):
    root = tmp_path
    (root / ".taskmaster" / "local").mkdir(parents=True)
    ck.write_heartbeat(root, "store-1", now=NOW, connected={ANN, BOB})
    beat = ck.read_heartbeat(root, "store-1", now=NOW)
    assert beat == Outage(NOW, frozenset({ANN, BOB}))
    assert ck.read_heartbeat(root, "store-2", now=NOW) is None
    path = ck.heartbeat_path(root)
    path.write_text("{not json", encoding="utf-8")
    assert ck.read_heartbeat(root, "store-1", now=NOW) is None
    path.write_text(json.dumps({"version": 2, "store_id": "store-1", "at": claims.stamp(NOW), "connected": []}))
    assert ck.read_heartbeat(root, "store-1", now=NOW) is None
    path.unlink()
    assert ck.read_heartbeat(root, "store-1", now=NOW) is None


def test_future_heartbeat_is_clamped_to_now(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    ck.write_heartbeat(tmp_path, "s", now=NOW + timedelta(hours=3), connected=set())
    assert ck.read_heartbeat(tmp_path, "s", now=NOW).at == NOW


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_heartbeat_is_owner_only(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    ck.write_heartbeat(tmp_path, "s", now=NOW, connected=set())
    assert ck.heartbeat_path(tmp_path).stat().st_mode & 0o077 == 0


# ---- the pass against a real coordinator (Step 3) ----------------------------------------------------------------

from contextlib import closing  # noqa: E402
import logging  # noqa: E402
import sqlite3  # noqa: E402

from native_twins import committed  # noqa: E402
from taskmaster.coordinator import scopes  # noqa: E402
from taskmaster.coordinator.actor_resolver import LOCAL, Origin  # noqa: E402
from taskmaster.native.contracts import Conflict  # noqa: E402
from taskmaster.serve.principals import revoke_user  # noqa: E402
from test_native_service_build import root  # noqa: F401,E402
from test_serve_session_guard import ANN as S_ANN, BOB as S_BOB, envelope, world  # noqa: F401,E402

HOST = "host-1-aaaaaaaa"


class FakeSessions:
    def __init__(self):
        self.live = set()
        self.is_attached = True

    def connected(self, session):
        return session in self.live

    def attached(self):
        return self.is_attached


@pytest.fixture
def kw(world, monkeypatch):  # noqa: F811
    sessions = FakeSessions()
    monkeypatch.setattr(claims, "CONNECTED", sessions.connected)
    keeper = ck.ClaimKeeper(world.owner, sessions, world.root)
    return type("KW", (), dict(world=world, sessions=sessions, keeper=keeper))()


def submit(world, scope, operation, arguments, who=None, expected=()):
    value = envelope(world, scope, operation, arguments)
    value["expected_revisions"] = list(expected)
    return world.owner.submit(value, origin=LOCAL if who is None else Origin(principal=who)).result(30)


def new_tasks(world, count):
    before = {ident for kind, ident in committed(world.root) if kind == "task"}
    for index in range(count):
        submit(world, HOST, "task.create", {"title": f"T{index}", "epic": "test-epic", "phase": "dev"})
    return sorted({ident for kind, ident in committed(world.root) if kind == "task"} - before)


def pick(world, ident, session, who, ttl=None):
    arguments = {"id": ident, "session": session}
    if ttl:
        arguments["ttl_seconds"] = ttl
    return submit(world, session, "task.pick", arguments, who)


def doc(world, ident):
    return committed(world.root)[("task", ident)][0]


def stamp_of(world, ident):
    return claims.parse_stamp(doc(world, ident).get("claim_expires"))


def keeper_receipts(world):
    with closing(sqlite3.connect(world.root / ".taskmaster/local/store.db")) as connection:
        return connection.execute("SELECT count(*) FROM command_receipts WHERE caller_scope=?",
                                  (scopes.CLAIM_KEEPER,)).fetchone()[0]


def near(moment, expected, seconds=120):
    return abs((moment - expected).total_seconds()) < seconds


def real_now():
    return claims.now_utc()


def test_connected_claim_with_30_min_left_is_renewed(kw):
    [task] = new_tasks(kw.world, 1)
    pick(kw.world, task, S_ANN, kw.world.ann, ttl=1800)
    kw.sessions.live.add(S_ANN)
    kw.keeper.run_pass()
    assert near(stamp_of(kw.world, task), real_now() + timedelta(seconds=claims.DEFAULT_TTL_SECONDS))
    assert doc(kw.world, task)["locked_by"] == S_ANN
    with closing(sqlite3.connect(kw.world.root / ".taskmaster/local/store.db")) as connection:
        [session] = connection.execute("SELECT session FROM domain_events WHERE id=? ORDER BY seq DESC LIMIT 1",
                                       (task,)).fetchone()
    # (the actor column exists only on team-mode stores; the daemon-level tests cover the host actor)
    assert session == scopes.CLAIM_KEEPER


def test_disconnected_claim_is_shortened_once_without_churn(kw):
    [task] = new_tasks(kw.world, 1)
    pick(kw.world, task, S_ANN, kw.world.ann)
    kw.keeper.run_pass()
    assert near(stamp_of(kw.world, task), real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS))
    count = keeper_receipts(kw.world)
    kw.keeper.run_pass()
    assert keeper_receipts(kw.world) == count


def test_a_bundle_is_written_by_one_command(kw):
    a, b = new_tasks(kw.world, 2)
    for ident in (a, b):
        submit(kw.world, HOST, "task.update", {"id": ident, "field": "bundle", "value": "bun"})
    pick(kw.world, a, S_ANN, kw.world.ann)
    assert doc(kw.world, b)["locked_by"] == S_ANN
    before = keeper_receipts(kw.world)
    kw.keeper.run_pass()
    assert keeper_receipts(kw.world) == before + 1
    for ident in (a, b):
        assert near(stamp_of(kw.world, ident), real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS))


def test_a_race_with_an_adoption_is_skipped_quietly(kw, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    [task] = new_tasks(kw.world, 1)
    pick(kw.world, task, S_ANN, kw.world.ann)
    second = "ann@" + "e" * 16
    real_apply = kw.keeper.apply

    def adopt_first(action):
        pick(kw.world, task, second, kw.world.ann)        # ann's new session adopts it between scan and write
        return real_apply(action)

    monkeypatch.setattr(kw.keeper, "apply", adopt_first)
    kw.keeper.run_pass()
    assert doc(kw.world, task)["locked_by"] == second
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any("moved since its scan" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)


def mixed_bundle(world, holder_x=S_ANN, holder_y=S_BOB, who_y=None):
    x, y = new_tasks(world, 2)
    pick(world, x, holder_x, world.ann)
    pick(world, y, holder_y, who_y or world.bob)
    for ident in (x, y):
        submit(world, HOST, "task.update", {"id": ident, "field": "bundle", "value": "mix"})
    return x, y


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_a_mixed_bundle_is_skipped_with_one_warning_and_recovers(kw, caplog):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    x, y = mixed_bundle(kw.world)
    before = keeper_receipts(kw.world)
    kw.keeper.run_pass()
    kw.keeper.run_pass()
    assert keeper_receipts(kw.world) == before
    [message] = [m for m in warnings(caplog) if "bundle mix" in m and f"`{S_ANN}`'s" in m]
    assert y in message and S_BOB in message
    # a bundle releases as a unit, so bob cannot release Y alone: take Y out of the bundle (E6's second remedy)
    submit(kw.world, HOST, "task.update", {"id": y, "field": "bundle", "value": ""})
    kw.keeper.run_pass()
    assert near(stamp_of(kw.world, x), real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS))
    assert not kw.keeper._warned_mixed        # forgotten once unmixed, so a later mix warns again
    assert len([m for m in warnings(caplog) if "bundle mix" in m and f"`{S_ANN}`'s" in m]) == 1


def test_a_second_session_of_the_same_person_is_mixed_too_and_not_adopted(kw, caplog):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    other = "ann@" + "f" * 16
    x, y = mixed_bundle(kw.world, holder_y=other, who_y=kw.world.ann)
    kw.keeper.run_pass()
    assert doc(kw.world, y)["locked_by"] == other and doc(kw.world, x)["locked_by"] == S_ANN
    assert len([m for m in warnings(caplog) if "bundle mix" in m and f"`{S_ANN}`'s" in m]) == 1


def test_one_failing_group_does_not_stop_the_pass(kw, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    first, second = new_tasks(kw.world, 2)
    pick(kw.world, first, S_ANN, kw.world.ann)
    pick(kw.world, second, S_BOB, kw.world.bob)
    real = kw.world.owner.submit

    def flaky(value, **kwargs):
        if value["arguments"].get("id") == first and value["caller_scope"] == scopes.CLAIM_KEEPER:
            raise RuntimeError("boom")
        return real(value, **kwargs)

    monkeypatch.setattr(kw.world.owner, "submit", flaky)
    kw.keeper.run_pass()
    assert near(stamp_of(kw.world, second), real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS))
    [message] = [m for m in warnings(caplog) if "failed" in m]
    assert first in message and "RuntimeError" in message and "boom" not in message


def test_the_keeper_still_works_after_the_holders_key_is_revoked(kw):
    [task] = new_tasks(kw.world, 1)
    pick(kw.world, task, S_ANN, kw.world.ann)
    revoke_user(kw.world.root, "ann")
    kw.keeper.run_pass()
    assert near(stamp_of(kw.world, task), real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS))


def test_a_member_can_never_use_the_keepers_scope(kw):
    from taskmaster.team_identity import IdentityRefused
    [task] = new_tasks(kw.world, 1)
    with pytest.raises(IdentityRefused):
        submit(kw.world, scopes.CLAIM_KEEPER, "task.claim_renew", {"id": task, "session": S_ANN}, kw.world.ann)


def test_a_refusing_coordinator_fails_each_group_and_the_pass_returns(kw, caplog):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    first, second = new_tasks(kw.world, 2)
    pick(kw.world, first, S_ANN, kw.world.ann)
    pick(kw.world, second, S_BOB, kw.world.bob)
    kw.world.owner.refuse_writes("test")
    kw.keeper.run_pass()
    assert len([m for m in warnings(caplog) if "failed" in m]) == 2


def test_the_scan_reads_only_claimed_tasks(kw, monkeypatch):
    from taskmaster.native.queries import Snapshot
    new_tasks(kw.world, 200)
    [claimed] = new_tasks(kw.world, 1)
    pick(kw.world, claimed, S_ANN, kw.world.ann)
    calls = []
    real = Snapshot.get
    monkeypatch.setattr(Snapshot, "get", lambda self, kind, ident, **k: calls.append(ident) or real(self, kind, ident, **k))
    kw.keeper.run_pass()
    assert set(calls) == {claimed}, calls


# ---- the restart grace (Step 4) ----------------------------------------------------------------------------------

S_CAT = "opal@" + "c" * 16
OTHER_STORE = "not-this-store"


def beat(kw, at, connected, store=None):
    ck.write_heartbeat(kw.world.root, store or kw.world.owner.identity["store_id"], now=at, connected=connected)


def three_claims(kw):
    ann, bob, cat = new_tasks(kw.world, 3)
    pick(kw.world, ann, S_ANN, kw.world.ann)                 # 4 h stamp: before the outage
    pick(kw.world, bob, S_BOB, kw.world.bob, ttl=24 * 3600)  # 24 h stamp: still running at the outage
    pick(kw.world, cat, S_CAT, kw.world.opal)                # 4 h stamp: before the outage, not listed
    return ann, bob, cat


def grace_stamp():
    return real_now() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS)


def test_restart_grants_one_grace_to_sessions_that_were_alive(kw):
    ann, bob, cat = three_claims(kw)
    start = real_now()
    cat_before = stamp_of(kw.world, cat)
    beat(kw, start + timedelta(hours=5), {S_ANN})
    kw.keeper.startup(now=start + timedelta(hours=6))
    assert near(stamp_of(kw.world, ann), grace_stamp()) and near(stamp_of(kw.world, bob), grace_stamp())
    assert stamp_of(kw.world, cat) == cat_before
    assert doc(kw.world, ann)["locked_by"] == S_ANN


@pytest.mark.parametrize("foreign_store", [False, True])
def test_no_usable_heartbeat_grants_everyone_the_grace(kw, foreign_store, caplog):
    caplog.set_level(logging.INFO, logger="taskmaster.serve")
    ann, bob, cat = three_claims(kw)
    start = real_now()
    if foreign_store:
        beat(kw, start + timedelta(hours=5), {S_ANN}, store=OTHER_STORE)
    kw.keeper.startup(now=start + timedelta(hours=6))
    for ident in (ann, bob, cat):
        assert near(stamp_of(kw.world, ident), grace_stamp())
    assert any("no usable heartbeat" in r.getMessage() for r in caplog.records)


def test_host_shaped_claims_and_done_tasks_are_untouched(kw):
    host_task, done_task = new_tasks(kw.world, 2)
    pick(kw.world, host_task, HOST, None)
    before = stamp_of(kw.world, host_task)
    pick(kw.world, done_task, S_ANN, kw.world.ann)
    submit(kw.world, HOST, "task.update", {"id": done_task, "field": "status", "value": "archived"})
    done_before = doc(kw.world, done_task)
    kw.keeper.startup(now=real_now() + timedelta(hours=6))
    assert stamp_of(kw.world, host_task) == before
    assert doc(kw.world, done_task) == done_before


def conflict_by_text():
    return Conflict("revision conflict for task x")


def test_startup_isolates_every_group(kw, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    first, second, third = new_tasks(kw.world, 3)
    pick(kw.world, first, S_ANN, kw.world.ann)
    pick(kw.world, second, S_BOB, kw.world.bob)
    pick(kw.world, third, S_CAT, kw.world.opal)
    real = kw.world.owner.submit
    broken = {first: RuntimeError("boom"), second: conflict_by_text()}

    def flaky(value, **kwargs):
        if value["caller_scope"] == scopes.CLAIM_KEEPER and value["arguments"]["id"] in broken:
            raise broken[value["arguments"]["id"]]
        return real(value, **kwargs)

    monkeypatch.setattr(kw.world.owner, "submit", flaky)
    kw.keeper.startup(now=real_now() + timedelta(hours=6))
    assert near(stamp_of(kw.world, third), grace_stamp())
    [failed] = [m for m in warnings(caplog) if "failed" in m]
    assert first in failed and "RuntimeError" in failed
    assert any("moved since its scan" in r.getMessage() and second in r.getMessage()
               for r in caplog.records if r.levelno == logging.DEBUG)


def test_a_concurrent_release_wins_over_the_grace(kw):
    ann, bob, _ = three_claims(kw)
    real_apply = kw.keeper.apply
    state = {"done": False}

    def release_first(action):
        if not state["done"]:
            state["done"] = True
            # bob's own session releases: a host session would be refused against his unexpired claim
            submit(kw.world, HOST, "task.claim_release", {"id": bob, "session": S_BOB})
        return real_apply(action)

    kw.keeper.apply = release_first
    kw.keeper.startup(now=real_now() + timedelta(hours=6))
    assert "locked_by" not in doc(kw.world, bob) or not doc(kw.world, bob).get("locked_by")
    assert near(stamp_of(kw.world, ann), grace_stamp())


def test_a_failing_scan_changes_nothing_and_does_not_raise(kw, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve")
    ann, _, _ = three_claims(kw)
    before = stamp_of(kw.world, ann)

    def broken():
        raise RuntimeError("scan down")

    monkeypatch.setattr(kw.keeper, "scan", broken)
    kw.keeper.startup(now=real_now() + timedelta(hours=6))
    assert stamp_of(kw.world, ann) == before
    assert any("could not read the claims" in m and "RuntimeError" in m for m in warnings(caplog))


def test_a_pass_on_a_detached_registry_writes_nothing(kw):
    [task] = new_tasks(kw.world, 1)
    pick(kw.world, task, S_ANN, kw.world.ann)
    before = stamp_of(kw.world, task)
    kw.sessions.is_attached = False          # before the lifespan attaches the manager, everyone reads as disconnected
    kw.keeper.run_pass()
    assert stamp_of(kw.world, task) == before and keeper_receipts(kw.world) == 0


def test_notice_words_lapsed_claims_and_caps_the_list(kw):
    from taskmaster.serve.claim_keeper import adoptable_notice
    tasks = new_tasks(kw.world, 12)
    for ident in tasks:
        pick(kw.world, ident, S_ANN, kw.world.ann)
    newer = "ann@" + "e" * 16
    with closing(kw.world.owner._connect(readonly=True)) as connection:
        fresh = adoptable_notice(connection, newer, real_now())
        stale = adoptable_notice(connection, newer, real_now() + timedelta(hours=5))
        none = adoptable_notice(connection, S_BOB, real_now())
    assert "until" in fresh and "lapsed at" not in fresh and "and 2 more" in fresh and fresh.count("`test-epic-") == 10
    assert "lapsed at" in stale and none is None
