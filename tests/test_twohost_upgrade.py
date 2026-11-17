# User intent: `serve --force-upgrade` is the operator's hand-over from a running older daemon to a newer build across two real processes: the old one exits 0 through the operator-token proof, keys and claims survive, and no member (or bad proof) can retire a daemon (team-server-015 S12, closing the two-process case 009 left untested).
from __future__ import annotations

import json

import pytest

from twohost.fixtures import team
from twohost.host import Host
from twohost.member import Member
from twohost.waits import until

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(420)]

OLD_BUILD = "0.0.1:" + "d" * 32
TASK = "test-epic-001"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s12") / "run", patch, build=OLD_BUILD) as the_team:
            yield the_team
    finally:
        patch.undo()


def test_a_member_key_cannot_retire_the_daemon_and_a_bad_proof_is_refused(world):
    for path in ("/retire", "/coordinator/retire", "/serve/retire"):
        response = world.ann.http("POST", path, json={"force_upgrade": "0" * 64})
        assert response.status_code in (400, 401, 403, 404, 405), (path, response.status_code)
    from taskmaster.serve import upgrade
    client = upgrade._DiscoveryClient(world.root)
    answer = client.retire("0" * 64)
    assert answer["state"] == "refused" and "proof is not valid" in answer["reason"], answer
    assert "Build the thing" in world.ann.call("backlog_list_tasks", {"limit": 5})   # the daemon is still serving
    assert world.host.child.poll() is None


def test_force_upgrade_takes_over_from_the_running_daemon_and_keys_and_claims_survive(world):
    picked = world.ann.call("backlog_pick_task", {"task_id": TASK})
    assert TASK in picked and "locked by another" not in picked, picked
    old = world.host
    old_pid = old.child.pid
    new = Host(old.machine, world.base, name="host-upgraded", flags=["--force-upgrade", "--upgrade-timeout", "60"])
    try:
        new.start(timeout=120)
        assert until(lambda: old.child.poll() is not None, 30, "the old daemon to exit")
        assert old.child.returncode == 0, old.child.returncode                       # the first exits 0
        assert new.child.pid != old_pid
        ann, bob = Member(world.ann.machine, new, world.keys["ann"], world.evidence), Member(
            world.bob.machine, new, world.keys["bob"], world.evidence)
        status = json.loads(ann.call("backlog_claim", {"action": "status", "task_id": TASK}))
        assert status, status
        assert "locked by another session" in bob.call("backlog_pick_task", {"task_id": TASK}), "ann's claim did not survive"
        assert "Build the thing" in bob.call("backlog_list_tasks", {"limit": 5})      # bob's key survived too
    finally:
        new.stop()
        old.child = None
