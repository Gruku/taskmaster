# User intent: run the team server on the shipped systemd unit's limits under an agent swarm (claims, writes, reads) and one key's deadline-length query burst, write the numbers to costs.json for the security review (006 G3), and prove the probe at a tiny size; the full 50-70 agent run is the same test with TASKMASTER_TWOHOST_AGENTS and TASKMASTER_TWOHOST_SECONDS set (team-server-015 S10 load, Step 8 part 3).
from __future__ import annotations

import os

import pytest

import surface_inventory as inv
from twohost.fixtures import parse
from twohost import load
from twohost.fixtures import team
from taskmaster.serve import config
from taskmaster.serve.principals import add_user

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(900)]

AGENTS = int(os.environ.get("TASKMASTER_TWOHOST_AGENTS", "4"))
SECONDS = float(os.environ.get("TASKMASTER_TWOHOST_SECONDS", "6"))
THINK = float(os.environ.get("TASKMASTER_TWOHOST_THINK", "0.3"))     # seconds an agent pauses between cycles
HEAVY = int(os.environ.get("TASKMASTER_TWOHOST_HEAVY", "3"))     # the full run uses min(in-flight, burst) = 32


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("load") / "run", patch, flags=inv.unit_flags(), name="load") as the_team:
            yield the_team
    finally:
        patch.undo()


def limits_in_force(flags: list[str]) -> dict:
    """The effective limits: the unit's flags over the daemon's defaults (the ready line does not echo them)."""
    return {"flags": flags, "rate_per_minute": config.DEFAULT_RATE_PER_MINUTE, "rate_burst": config.DEFAULT_RATE_BURST,
            "max_inflight": config.DEFAULT_MAX_INFLIGHT, "max_body_bytes": config.DEFAULT_MAX_BODY_BYTES,
            "max_connections": config.DEFAULT_MAX_CONNECTIONS, "heavy_per_key": config.HEAVY_PER_KEY,
            "heavy_total": config.HEAVY_TOTAL, "cpu_count": os.cpu_count()}


def test_the_agent_swarm_claims_writes_and_reads_correctly_and_the_costs_are_recorded(world):
    run = load.swarm(world.host, world.root, agents=AGENTS, seconds=SECONDS, think=THINK)
    # correctness, not speed: every agent's work completed, claims are exclusive, nothing 5xx'd or dropped
    assert run["cycles"] >= AGENTS, run
    # an agent's own new task is always pickable; the only picks that may fail are throttled ones (429, counted)
    assert run["writes"] - run["throttled"] <= run["claims"] <= run["writes"], run
    assert run["contested_pick_winners"] <= 1, run             # one task, N racers: never two claims
    assert run["contested_pick_winners"] == 1, run             # exactly one, even when other calls were throttled
    holder = parse(world.bob.call("backlog_claim", {"action": "status", "task_id": run["contested_task"]}))
    assert holder["holder"] and holder["state"] == "held", holder      # the store agrees: one holder (answer text alone could lie)
    bad = {k: v for k, v in run["errors"].items() if "exception" in k or k.endswith(":error")}
    assert bad == {}, run
    world.evidence.write("costs-swarm.json", {"limits": limits_in_force(inv.unit_flags()), "swarm": run})
    print("SWARM", run)


def test_one_keys_deadline_length_burst_is_bounded_and_does_not_starve_another_key(world):
    key = add_user(world.root, "burst", name="Burst", emails=["burst@twohost.invalid"])[0]
    run = load.heavy_single_key(world.host, key, world.bob.key, calls=HEAVY)
    answers_seen = run["latency"]["heavy:query"]["n"]
    assert answers_seen == HEAVY, run                          # every heavy call came back (deadline, not a hang)
    assert run["latency"]["heavy:query"]["max"] < 60, run      # bounded by the query deadline plus queueing, not unbounded
    assert "bystander:list_tasks" in run["latency"] and run["latency"]["bystander:list_tasks"]["n"] >= 1, run
    # security review I-1: one key runs at most HEAVY_PER_KEY of them; the rest are refused at once, not queued. A
    # full-size burst also meets the transport limits, so a refusal is the in-band "rate limited" answer or an HTTP 429
    # (seen at 32 calls on the live host, 2026-11-17: 15 + 16).
    refused = run["errors"].get("heavy:query:rate", 0) + run["errors"].get("heavy:query:throttled", 0)
    assert refused == max(0, HEAVY - config.HEAVY_PER_KEY), run
    world.evidence.write("costs.json", {"limits": limits_in_force(inv.unit_flags()), "burst": run})
    print("BURST", run)
