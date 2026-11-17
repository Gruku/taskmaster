# User intent: no member can act under, read from or attach to another person's session or one of the server's own internal scopes, and Linear retry is the operator's on a team server (user ruling on the security review's m-7): every member retry, whatever scope it names, is refused before it takes a queue slot, while the operator's retry still works over the server (team-server-015 S3, closing P-004).
from __future__ import annotations

import json

import pytest

from twohost.fixtures import parse, team

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(300)]

OPERATOR_ONLY = "operator action on a team server"
TASK = "test-epic-001"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s3") / "run", patch) as the_team:
            yield the_team
    finally:
        patch.undo()


def retry(connection, scope: str | None, request_id: str = "r-s3") -> dict:
    """One call on an open connection (a session per call would spend the rate-limit budget on handshakes)."""
    arguments = {"action": "retry", "request_id": request_id, **({} if scope is None else {"caller_scope": scope})}
    return json.loads(connection.call("backlog_linear", arguments))


def test_a_members_retry_is_refused_whatever_scope_it_names(world):
    from taskmaster.coordinator import scopes
    world.ann.call("backlog_pick_task", {"task_id": TASK})
    ann_session = json.loads(world.bob.call("backlog_claim", {"action": "status", "task_id": TASK}))["holder"]
    assert ann_session.startswith("ann@"), ann_session
    scopes_named = [ann_session, "ann@0000000000000000", "ann@viewer", *sorted(scopes.INTERNAL), "sync-x", "git:abc",
                    "bob@viewer", None]
    with world.bob.mcp() as connection:
        for scope in scopes_named:
            answer = retry(connection, scope)
            assert OPERATOR_ONLY in answer.get("error", ""), (scope, answer)
    with world.ann.mcp() as connection:                      # her own older session scope too
        answer = retry(connection, ann_session, "r-ann-old")
    assert OPERATOR_ONLY in answer.get("error", ""), answer


def test_a_refused_retry_takes_no_queue_slot_and_writes_nothing(world):
    with world.bob.mcp() as connection:
        before = connection.call("backlog_linear", {"action": "status"})
        assert parse(before)["queue_depth"] == 0 and parse(before)["pending"] == 0, before
        for index in range(40):                                  # far more than any per-key queue could hold
            assert OPERATOR_ONLY in connection.call("backlog_linear", {"action": "retry", "caller_scope": "ann@" + "0" * 16,
                                                                       "request_id": f"flood-{index}"})
        after = connection.call("backlog_linear", {"action": "status"})
        assert before == after and parse(after)["queue_depth"] == 0 and parse(after)["pending"] == 0, after


def test_the_operators_retry_still_works_over_the_server(world):
    with world.operator.mcp(off_host=False) as connection:   # the operator token works only on the host
        answer = retry(connection, None, "r-operator")
    # Past the allowlist and the scope check, into the Linear worker: this host has no Linear workspace configured.
    assert OPERATOR_ONLY not in json.dumps(answer) and "linear.yaml not found" in answer.get("error", ""), answer
