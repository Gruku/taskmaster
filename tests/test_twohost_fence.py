# User intent: a member's clone and the host clone must never grow a second, local store: with the real daemon up and down, a stdio Taskmaster start, the coordinator CLI and a stray local session all refuse and point at the server, deleting the marker leaves the latch refusing, and only the explicit clear-latch frees a clone (team-server-015 S6, Step 5).
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from twohost.fixtures import team
from twohost.member import local_stores, make_clone, run_git, stdio_calls

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(300)]

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "backlog_server.py"
MARKER = ".taskmaster-server.json"
TOOLS = [("backlog_init", {}), ("backlog_status", {}), ("backlog_list_tasks", {})]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    try:
        with team(tmp_path_factory.mktemp("s6") / "run", patch) as the_team:
            make_clone(the_team.ann, the_team.host.url)
            yield the_team
    finally:
        patch.undo()


def test_a_stdio_start_in_a_members_clone_refuses_every_tool_creates_nothing_and_latches(world):
    clone = world.ann.machine.root
    answers = stdio_calls(world.ann.machine.env, clone, TOOLS)
    assert all("Taskmaster server" in a for a in answers), answers
    assert local_stores(clone) == []
    assert (clone / ".git" / "taskmaster-server.json").exists()          # the first refusal latched the clone
    assert world.host.url.split("/mcp")[0] in "".join(answers), answers          # the refusal names the server to use


def test_the_coordinator_cli_in_a_members_clone_reports_server_mode_and_opens_no_store(world):
    done = world.ann.cli("coordinator", "status", "--root", str(world.ann.machine.root))
    assert done.returncode == 0, (done.stdout, done.stderr)
    report = json.loads(done.stdout)
    assert report["state"] == "server-mode" and report["owner_held"] is False, report
    assert local_stores(world.ann.machine.root) == []


def test_deleting_the_marker_leaves_the_latch_refusing_and_only_clear_latch_frees_the_clone(world):
    clone = world.ann.machine.root
    (clone / MARKER).unlink()
    [answer] = stdio_calls(world.ann.machine.env, clone, [("backlog_status", {})])
    assert "has seen Taskmaster server mode" in answer and "clear-latch" in answer, answer
    assert local_stores(clone) == []
    cleared = world.ann.cli("serve", "marker", "clear-latch", "--root", str(clone))
    assert cleared.returncode == 0, (cleared.stdout, cleared.stderr)
    assert not (clone / ".git" / "taskmaster-server.json").exists()


def test_a_stray_local_session_in_the_host_clone_is_refused_while_the_daemon_runs_and_after_it_stops(world):
    host_root = world.root
    run_git(world.operator.machine, host_root, "init", "-q", "-b", "main")
    marked = world.operator.cli("serve", "marker", "write", "--root", str(host_root), "--url", world.host.url)
    assert marked.returncode == 0, (marked.stdout, marked.stderr)
    before = (host_root / ".taskmaster" / "local" / "store.db").stat().st_size
    while_up = stdio_calls(world.operator.machine.env, host_root, [("backlog_status", {}), ("backlog_add_task", {
        "title": "stray", "epic": "test-epic", "phase": "dev"})])
    assert all("Taskmaster server" in a for a in while_up), while_up
    assert "Added" not in "".join(while_up)
    world.host.stop()
    after_stop = stdio_calls(world.operator.machine.env, host_root, [("backlog_status", {}), ("backlog_add_task", {
        "title": "stray", "epic": "test-epic", "phase": "dev"})])
    assert all("Taskmaster server" in a for a in after_stop), after_stop
    assert "Added" not in "".join(after_stop)
    assert (host_root / ".taskmaster" / "local" / "store.db").stat().st_size == before   # nothing wrote a stray task
