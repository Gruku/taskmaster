# User intent: with a real daemon and two members plus the operator in separate machines, show each acts only as their own key allows, refusals read as designed, setup tools change nothing, and a member cannot read the host's private tables (team-server-015 S1, Step 3; P-026 is already closed by team-server-026).
from __future__ import annotations

import contextlib
import json
import re

import pytest

from twohost import waits
from twohost.member import Member
from twohost.world import Machine
from twohost.fixtures import parse, rows, team

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(180)]

ADD = {"epic": "test-epic", "phase": "dev"}
SETUP_TOOLS = {                  # tool -> arguments, each refused on a native store (F2 regression, review m9)
    "backlog_init": {}, "backlog_migrate_v3": {}, "backlog_migrate_v4": {},
    "backlog_canonicalize_layout": {}, "backlog_backfill_lanes": {},
    "backlog_project_init": {"name": "Hijack"},
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s1") / "run", patch) as the_team:
            yield the_team
    finally:
        patch.undo()


def revision(world) -> int:
    return json.loads(world.ann.http("GET", "/read/v1/meta").text)["revision"]


def change_sessions(world) -> dict[str, str]:
    """{task title -> the session that wrote it}, read as a member through MCP."""
    answer = world.ann.call("backlog_query", {
        "sql": "SELECT json_extract(after,'$.title') AS title, session FROM changes WHERE op='create' AND kind='task'"})
    return {cells[0]: cells[1] for cells in rows(answer)}


def test_each_member_writes_as_their_own_key(world):
    assert "[seq " in world.ann.call("backlog_add_task", {"title": "Ann's task", **ADD})
    assert "[seq " in world.bob.call("backlog_add_task", {"title": "Bob's task", **ADD})
    written = change_sessions(world)
    assert written["Ann's task"].startswith("ann@"), written
    assert written["Bob's task"].startswith("bob@"), written
    assert written["Ann's task"] != written["Bob's task"]


def test_members_get_the_designed_refusals_for_operator_actions(world):
    sync = world.ann.call("backlog_sync")
    assert sync.startswith("Error:") and "host operator action" in sync
    linear = parse(world.bob.call("backlog_linear", {"action": "bootstrap_apply", "workspace_alias": "ops",
                                                      "team_id": "T2", "status_mapping": "todo:s1,done:s2"}))
    assert "host operator action" in linear["error"]
    before = revision(world)
    evidence = world.ann.call("backlog_blast_radius", {"task_id": "test-epic-001", "mode": "evidence"})
    assert "host operator action" in evidence, evidence
    assert revision(world) == before


def test_setup_tools_are_refused_or_do_nothing_and_the_revision_is_unchanged(world):
    before = revision(world)
    project_yaml = world.root / ".taskmaster" / "project.yaml"
    yaml_before = project_yaml.read_bytes() if project_yaml.exists() else None
    for tool, arguments in SETUP_TOOLS.items():
        answer = world.ann.call(tool, arguments)
        # the designed refusal (project_init: the manifest is the operator's, security review m-9), or an internal
        # error that names only a log ref
        assert answer.startswith("Error:") and ("cannot run here" in answer or "already exists" in answer
                                                  or "host operator action" in answer
                                                  or re.search(r"internal error in \w+ \(ref [0-9a-f]+\)", answer)), (tool, answer)
        assert "[seq " not in answer, (tool, answer)
    assert revision(world) == before
    assert (project_yaml.read_bytes() if project_yaml.exists() else None) == yaml_before      # project_init(name="Hijack") changed nothing
    assert b"Hijack" not in (project_yaml.read_bytes() if project_yaml.exists() else b"")


def test_a_member_cannot_read_the_hosts_private_tables_by_either_door(world):
    """P-026 closed by team-server-026: MCP backlog_query refuses sessions, linear_queue and projection."""
    for table in ("sessions", "linear_queue", "projection"):
        answer = world.ann.call("backlog_query", {"sql": f"SELECT count(*) FROM {table}"})
        assert "not authorized" in answer, (table, answer)
        read = world.ann.http("POST", "/read/v1/tools", headers={"Content-Type": "application/json"}, json={
            "calls": [{"tool": "backlog_query", "args": {"sql": f"SELECT count(*) FROM {table}"}}]})
        assert "not authorized" in read.text, (table, read.text)
    counted = world.ann.call("backlog_query", {"sql": "SELECT count(*) FROM changes"})
    assert rows(counted)[0][0].isdigit() and int(rows(counted)[0][0]) > 0, counted      # a real count, not an echoed error


def test_the_operator_token_is_refused_off_host_and_an_operator_role_key_gets_no_operator_actions(world):
    off_host = world.operator.http("POST", "/mcp", json={})
    assert off_host.status_code == 403
    assert "only on the host" in off_host.text
    on_host = world.operator.http("POST", "/mcp", json={}, off_host=False)
    assert on_host.status_code not in (401, 403)              # auth passed; the bare body is then the transport's problem
    made = world.operator.cli("serve", "add-user", "carol", "--root", str(world.root), "--role", "operator")
    assert made.returncode == 0, made.stderr
    carol = Member(Machine.create(world.base, "carol"), world.host, made.stdout.strip(), world.evidence)
    try:
        answer = carol.call("backlog_sync")
        assert "host operator action" in answer, answer
        assert "host operator action" in carol.call("backlog_blast_radius", {"task_id": "test-epic-001", "mode": "evidence"})
    finally:
        world.operator.cli("serve", "revoke-user", "carol", "--root", str(world.root))


def test_a_rotated_key_works_and_the_old_one_does_not(world):
    old = world.bob.key
    rotated = world.operator.cli("serve", "rotate-user", "bob", "--root", str(world.root))
    assert rotated.returncode == 0, rotated.stderr
    new = rotated.stdout.strip()
    assert new and new != old
    assert world.bob.http("POST", "/mcp", key=old, json={}).status_code == 401
    status = world.bob.http("POST", "/mcp", key=new, json={}).status_code
    assert status not in (401, 403, 429) and status < 500       # auth passed; the bare body is then the transport's problem
    world.bob.key = new


def test_revoking_a_member_ends_their_open_session_and_their_next_call_is_401(world):
    connection = world.bob.mcp()
    try:
        assert "Dashboard" in connection.call("backlog_status")
        revoked = world.operator.cli("serve", "revoke-user", "bob", "--root", str(world.root))
        assert revoked.returncode == 0, revoked.stderr
        waits.until(lambda: session_ended(connection), 20, "bob's open session to end after the revocation sweep", interval=1.0)
    finally:
        with contextlib.suppress(Exception):          # the client's closing DELETE is itself a 401 now
            connection.close()
    assert world.bob.http("POST", "/mcp", json={}).status_code == 401


def session_ended(connection) -> bool:
    """True only when the server ended the session (401/404/session gone). Anything else that fails (a 429 from the per-key
    burst, a timeout, a transport error) is not "ended": it is raised so the wait reports it instead of passing."""
    try:
        connection.call("backlog_status", timeout=10)
        return False
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        text = str(exc).lower()
        if status in (401, 404) or "401" in text or "unauthorized" in text or "session" in text and ("terminated" in text or "not found" in text):
            return True
        raise
