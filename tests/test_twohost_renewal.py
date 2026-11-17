# User intent: a person's claim lives exactly as long as their connection does, on the real daemon with a moved claim clock and no real waiting: renewed to +4 h while ann is connected, cut to 15 minutes when her connection closes or her key is revoked, free for bob once it lapses, and given one grace after a server outage (team-server-015 S4, closing the part S2 does not cover; team-server-010).
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from twohost.fixtures import team
from twohost.host import Host
from twohost.member import Member
from twohost.waits import until

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(600)]

FOUR_HOURS, QUARTER_HOUR = 4 * 3600, 15 * 60


class Clock:
    """The daemon's claim clock: real time plus an offset the test moves through a file (no sleeping)."""
    def __init__(self, path):
        self.path = path
        self.set(0)

    def set(self, seconds: float) -> None:
        self.offset = seconds
        self.path.write_text(str(seconds), encoding="utf-8")

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=self.offset)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("s4") / "run"
    base.mkdir()
    clock = Clock(base / "clock.txt")
    try:
        with team(base, patch, clock_file=clock.path, keeper_seconds=0.3) as the_team:
            the_team.clock = clock
            yield the_team
    finally:
        patch.undo()


def status(member, task) -> dict:
    return json.loads(member.call("backlog_claim", {"action": "status", "task_id": task}, record=False))


def expires(member, task):
    stamp = status(member, task).get("expires_at")
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if stamp else None


def left(world, member, task) -> float:
    """Seconds the claim has left on the daemon's own (moved) clock."""
    return (expires(member, task) - world.clock.now()).total_seconds()


def test_while_ann_is_connected_a_claim_under_an_hour_from_expiry_is_renewed_to_four_hours(world):
    task = "test-epic-001"
    with world.ann.mcp() as connection:
        assert connection.call("backlog_pick_task", {"task_id": task}).startswith("Picked")
        assert left(world, world.bob, task) > FOUR_HOURS - 300
        world.clock.set(3.5 * 3600)                               # 30 minutes left: under the 60 minute line
        until(lambda: left(world, world.bob, task) > FOUR_HOURS - 600, 25, "the keeper to renew to +4 h", interval=1.0)
    # her connection closes: the stamp is cut to about 15 minutes on the same clock
    until(lambda: left(world, world.bob, task) <= QUARTER_HOUR + 60, 25, "the stamp to be cut to the disconnected TTL",
          interval=1.0)
    assert left(world, world.bob, task) > QUARTER_HOUR - 300


def test_sixteen_minutes_after_a_clean_close_bob_may_take_the_task(world):
    task = "test-epic-001"
    assert "locked by another session" in world.bob.call("backlog_pick_task", {"task_id": task})
    world.clock.set(world.clock.offset + 16 * 60)
    # a lapsed claim is not taken silently: the refusal says it has expired, and the release it names frees it
    refusal = world.bob.call("backlog_pick_task", {"task_id": task})
    assert "locked by another session" in refusal and "has expired" in refusal, refusal
    released = world.bob.call("backlog_claim", {"action": "release", "task_id": task})
    assert "released" in released.lower() or "[seq" in released, released
    picked = world.bob.call("backlog_pick_task", {"task_id": task})
    assert "locked by another" not in picked and task in picked, picked          # the task stays in-progress; the claim is bob's
    assert status(world.bob, task)["holder"].startswith("bob@"), status(world.bob, task)


def test_revoking_ann_counts_as_a_disconnect_and_her_claim_lapses_on_the_short_ttl(world):
    task = "test-epic-002"
    connection = world.ann.mcp().__enter__()
    try:
        assert connection.call("backlog_pick_task", {"task_id": task}).startswith("Picked")
        assert left(world, world.bob, task) > FOUR_HOURS - 300
        revoked = world.operator.cli("serve", "revoke-user", "ann", "--root", str(world.root))
        assert revoked.returncode == 0, revoked.stderr
        until(lambda: left(world, world.bob, task) <= QUARTER_HOUR + 60, 25, "revocation to cut the claim to 15 minutes",
              interval=1.0)
    finally:
        try:
            connection.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - the daemon already ended this session
            pass
    world.clock.set(world.clock.offset + 16 * 60)
    assert "has expired" in world.bob.call("backlog_pick_task", {"task_id": task})


def test_a_restart_after_an_outage_gives_a_connected_claim_one_grace(world):
    from taskmaster.serve.principals import add_user
    task = "test-epic-003"
    key = add_user(world.root, "cleo", name="Cleo", emails=["cleo@twohost.invalid"])[0]
    from twohost.world import Machine
    cleo = Member(Machine.create(world.base, "cleo"), world.host, key, world.evidence)
    with cleo.mcp() as connection:
        assert connection.call("backlog_pick_task", {"task_id": task}).startswith("Picked")
        until(lambda: (world.root / ".taskmaster" / "local" / "serve" / "claim-keeper.json").exists(), 20,
              "the keeper's heartbeat naming cleo", interval=0.5)
        world.host.stop()                                             # the outage, while she is still connected
    world.clock.set(world.clock.offset + 5 * 3600)                    # the 4 h stamp is long past
    again = Host(world.host.machine, world.base, name="host-restart", clock_file=world.clock.path, keeper_seconds=0.3)
    try:
        again.start()
        bob = Member(world.bob.machine, again, world.keys["bob"], world.evidence)
        until(lambda: 0 < left(world, bob, task) <= QUARTER_HOUR + 60, 25, "the restart grace to re-stamp the claim",
              interval=1.0)
        assert "locked by another session" in bob.call("backlog_pick_task", {"task_id": task})
    finally:
        again.stop()
