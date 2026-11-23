# User intent: prove the plugin's hooks, the CLI and older builds all work against the project's one local server (local-server-004):
# reads see its writes, writes land through its in-process coordinator, nothing starts a second coordinator, a refused or
# other-build write is shown rather than lost quietly, and an old build is told plainly to restart its session.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.ownership import OwnershipUnavailable
from taskmaster.coordinator.protocol import HandshakeError, build_identity, check_handshake
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve.daemon import SERVE_RETIRE_REFUSED_LOCAL
from test_native_service_build import root  # noqa: F401,E402
from test_serve_local import local  # noqa: F401,E402

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MANIFEST = {"schema_version": 1, "meta": {"name": "Hooks", "slug": "hooks", "kind": "app"},
            "conventions": {"policies": {"review_gate_required_for_merge": True}}}
OLDER = {"version": "0.0.1", "digest": "1" * 32}
NEWER = {"version": "99.0.0", "digest": "2" * 32}


def hook_env(root, home) -> dict:
    return {**os.environ, "PYTHONPATH": str(REPO), "TASKMASTER_ROOT": str(root), "HOME": str(home),
            "USERPROFILE": str(home), "CLAUDE_PLUGIN_ROOT": str(REPO)}


def run_hook(script, payload, root, home, *args):
    return subprocess.run([sys.executable, str(HOOKS / script), *args], input=json.dumps(payload), text=True,
                          encoding="utf-8", capture_output=True, cwd=str(root), env=hook_env(root, home), timeout=90,
                          creationflags=NO_WINDOW)


def bash_payload(root, command, **extra):
    return {"session_id": "s1", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command}, **extra}


def coordinator_pid(root) -> int:
    return json.loads((Path(root) / ".taskmaster/local/coordinator/discovery.json").read_text())["pid"]


def init_git(root) -> str:
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True,
                              creationflags=NO_WINDOW).stdout.strip()
    git("init")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "T")
    (Path(root) / "README.md").write_text("x")
    git("add", "README.md")
    git("commit", "-m", "init")
    return git("rev-parse", "HEAD")


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def seeded(local, root):  # noqa: F811
    """A running local server whose store holds a gated task on a branch and an anchored bug, all written through it."""
    world = local()
    bridge = world.bridge("sess-a")
    bridge.call("backlog_project_set", {"yaml_content": yaml.safe_dump(MANIFEST)})
    bridge.call("backlog_update_task", {"task_id": "test-epic-001", "field": "branch", "value": "feature/x"})
    bridge.call("backlog_bug_create", {"title": "Seeded bug", "found_in": "test-epic-001", "location": ["api/model.py"]})
    world.session = bridge
    world.pid = coordinator_pid(root)
    return world


# -- reads see the server's writes -------------------------------------------------------------------------------------

def test_the_merge_gate_reads_what_the_server_wrote_and_follows_its_next_write(seeded, root, home):  # noqa: F811
    merge = bash_payload(root, "git merge feature/x")
    blocked = run_hook("merge_gate.py", merge, root, home)
    assert blocked.returncode == 2 and "test-epic-001" in blocked.stdout + blocked.stderr, (blocked.stdout, blocked.stderr)
    seeded.session.call("backlog_update_task", {"task_id": "test-epic-001", "field": "lane", "value": "express"})
    seeded.session.call("backlog_record_gate", {"task_id": "test-epic-001", "gate": "impl", "status": "done"})
    seeded.session.call("backlog_record_gate", {"task_id": "test-epic-001", "gate": "review-gate", "verdict": "pass"})
    allowed = run_hook("merge_gate.py", merge, root, home)
    assert allowed.returncode == 0, (allowed.stdout, allowed.stderr)
    assert coordinator_pid(root) == seeded.pid


def test_edit_resurfacing_reads_what_the_server_wrote(seeded, root, home):  # noqa: F811
    edit = {"session_id": "s1", "cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": str(root / "api/model.py")},
            "tool_response": {"success": True}}
    done = run_hook("edit_resurface.py", edit, root, home)
    assert done.returncode == 0, done.stderr
    assert "B-001" in json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


# -- writes land through the server's coordinator, and nothing starts another ----------------------------------------------

def test_the_merge_recorder_writes_through_the_servers_coordinator(seeded, root, home):  # noqa: F811
    init_git(root)
    assert "merge_gate_state:** master" not in seeded.session.call("backlog_get_task", {"task_id": "test-epic-001"})
    done = run_hook("merge_recorder.py", bash_payload(root, "git merge feature/x", tool_response={"exit_code": 0}), root, home)
    assert done.returncode == 0 and done.stdout.strip() == "", (done.stdout, done.stderr)
    task = seeded.session.call("backlog_get_task", {"task_id": "test-epic-001"})
    assert "**merge_gate_state:** master" in task, task        # the stamp landed through the server's coordinator
    assert not (root / ".taskmaster/local/merge-stamps-pending.jsonl").exists()
    assert coordinator_pid(root) == seeded.pid


def test_a_write_the_server_refuses_as_another_build_is_shown_not_dropped(seeded, root, home, caplog):  # noqa: F811
    init_git(root)
    coordinator = seeded.daemon.coordinator
    real = coordinator.build
    coordinator.build = NEWER                       # the server now runs "another build" than the hook's
    try:
        with caplog.at_level("WARNING", logger="taskmaster.serve"):
            done = run_hook("merge_recorder.py", bash_payload(root, "git merge feature/x", tool_response={"exit_code": 0}),
                            root, home)
    finally:
        coordinator.build = real
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    assert "taskmaster merge recorder:" in shown["systemMessage"] and "refused" in shown["systemMessage"]
    assert shown["hookSpecificOutput"]["additionalContext"] == shown["systemMessage"]
    assert "refused" in (root / ".taskmaster/local/hook.log").read_text()
    assert (root / ".taskmaster/local/merge-stamps-pending.jsonl").exists()       # queued, so the merge is not lost
    assert any("refused" in record.getMessage() for record in caplog.records), [r.getMessage() for r in caplog.records]


def test_the_visible_prefix_matches_between_the_recorder_and_its_stamp():
    sys.path.insert(0, str(HOOKS))
    import merge_recorder
    import merge_recorder_stamp
    assert merge_recorder.VISIBLE_PREFIX == merge_recorder_stamp.VISIBLE_PREFIX


def test_hooks_with_no_store_traffic_run_against_a_live_server(seeded, root, home):  # noqa: F811
    approve = {"session_id": "s1", "tool_name": "AskUserQuestion", "tool_input": {},
               "tool_response": {"answers": {"[merge-gate feature/x] ok?": "Approve"}}}
    assert run_hook("taskmaster_merge_approve.py", approve, root, home).returncode == 0
    assert (home / ".claude" / "taskmaster-merge-approve-s1").is_file()
    submodule = run_hook("worktree_submodule_init.py", bash_payload(root, "echo hi", tool_response={"exit_code": 0}), root, home)
    assert submodule.returncode == 0 and submodule.stdout.strip() == ""
    bash = shutil.which("bash")
    if bash:
        started = subprocess.run([bash, str(HOOKS / "session-start.sh")], capture_output=True, text=True, cwd=str(root),
                                 env=hook_env(root, home), timeout=60, creationflags=NO_WINDOW)
        assert json.loads(started.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert coordinator_pid(root) == seeded.pid


def test_nothing_starts_a_second_coordinator_while_a_local_server_runs(seeded, root, monkeypatch):  # noqa: F811
    launched = []
    monkeypatch.setattr(client_module, "_launch", lambda r: launched.append(r) or pytest.fail("a coordinator was launched"))
    client = client_module.Client(root)
    client.autostart = True                                  # what a stdio server or the CLI has
    assert client.call("status")
    with pytest.raises(OwnershipUnavailable):
        Coordinator(root).start()                            # the service entry point cannot take the store either
    assert launched == [] and coordinator_pid(root) == seeded.pid


def test_the_cli_reaches_the_servers_in_process_coordinator(seeded, root, home):  # noqa: F811
    done = subprocess.run([sys.executable, str(REPO / "taskmaster_cli.py"), "coordinator", "status", "--root", str(root)],
                          capture_output=True, text=True, env=hook_env(root, home), timeout=90, creationflags=NO_WINDOW)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert coordinator_pid(root) == seeded.pid


# -- an old build is told plainly to restart its session -----------------------------------------------------------------

def test_an_older_client_build_gets_the_restart_message_from_the_server_handshake():
    identity, nonce, build = {"root": "x"}, "n", {"version": "7.6.0", "digest": "a" * 32}
    with pytest.raises(HandshakeError) as caught:
        check_handshake({**identity, "nonce": nonce, "build": OLDER}, identity, nonce, build)
    assert str(caught.value).startswith("taskmaster was upgraded: restart this session"), str(caught.value)
    assert caught.value.may_have_committed is False
    with pytest.raises(HandshakeError) as same_release:     # same release, other code: the old text
        check_handshake({**identity, "nonce": nonce, "build": {"version": "7.6.0", "digest": "b" * 32}}, identity, nonce, build)
    assert "upgraded" not in str(same_release.value)


def test_an_older_stdio_server_or_cli_gets_the_restart_message_from_a_running_local_server(seeded, root):  # noqa: F811
    client = client_module.Client(root)
    client.build = OLDER
    with pytest.raises(HandshakeError) as caught:
        client.call("status")
    assert str(caught.value).startswith("taskmaster was upgraded: restart this session"), str(caught.value)


def test_a_newer_client_is_not_told_to_stop_the_local_server_with_systemctl(seeded):
    answer = seeded.daemon.coordinator.retire(NEWER, {})
    assert answer["state"] == "refused" and answer["reason"] == SERVE_RETIRE_REFUSED_LOCAL
    assert "systemctl" not in answer["reason"]
