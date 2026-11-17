# User intent: prove the fence end to end through the real stdio MCP server (teammate clone, subdirectory sessions,
# latch after the marker is gone, no viewer port) and through the CLIs, hook validator and add-user text
# (team-server-009 Steps 4-5).
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm

from test_server_fence import HOST_ID, PLUGIN_ROOT, URL, git, mark, project, repo, run_python  # noqa: F401

SERVER = PLUGIN_ROOT / "backlog_server.py"


async def _call(cwd: Path, calls, root_env=None):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = {k: v for k, v in os.environ.items() if k not in ("TASKMASTER_ROOT", "PYTHONPATH")}
    if root_env:
        env["TASKMASTER_ROOT"] = str(root_env)
    transport = StdioTransport(sys.executable, [str(SERVER)], env=env, cwd=str(cwd))
    out = []
    async with Client(transport, timeout=90) as client:
        for tool, args in calls:
            result = await client.call_tool(tool, args, raise_on_error=False)
            out.append(result.content[0].text if result.content else "")
    return out


def call_server(cwd, calls, root_env=None):
    return asyncio.run(_call(cwd, calls, root_env))


TOOLS = [("backlog_init", {}), ("backlog_status", {}), ("backlog_list_tasks", {}), ("backlog_open_viewer", {})]


def tmroots(path: Path) -> list[Path]:
    return [p for p in path.rglob(".taskmaster") if ".git" not in p.parts]


def test_teammate_clone_every_tool_refuses_and_creates_nothing(repo):
    mark(repo)
    answers = call_server(repo, TOOLS)
    assert all("Taskmaster server" in a for a in answers), answers
    assert tmroots(repo) == []
    assert sm.latch_path(repo).exists()        # first refusal latched the clone


def test_subdirectory_sessions_meet_the_marker_at_the_repo_root(repo):
    mark(repo)
    deeper = repo / "sub" / "deeper"
    assert all("Taskmaster server" in a for a in call_server(deeper, TOOLS))
    other = repo.parent
    assert all("Taskmaster server" in a for a in call_server(other, TOOLS, root_env=repo / "sub"))
    assert tmroots(repo) == []


def test_the_latch_answers_once_the_marker_is_gone(repo):
    mark(repo)
    call_server(repo, [("backlog_status", {})])
    (repo / sm.MARKER_NAME).unlink()
    [answer] = call_server(repo, [("backlog_status", {})])
    assert "has seen Taskmaster server mode" in answer and "clear-latch" in answer


def test_host_clone_store_bytes_are_untouched_and_no_viewer_port_is_bound(repo):
    root = project(repo)
    store_dir = root / ".taskmaster" / "local"
    (store_dir / "store.db").write_bytes(b"not a real store")
    mark(repo)

    def digest():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*"))
                if p.is_file() and ".git" not in p.parts}

    before = digest()
    answers = call_server(repo, TOOLS)
    assert all("Taskmaster server" in a for a in answers), answers
    assert digest() == before


def test_no_viewer_port_is_bound(repo):
    mark(repo)
    code = ("import socket, pathlib\n"
            "from taskmaster import backlog_server as bs\n"
            f"bs.ROOT = pathlib.Path({str(repo)!r})\n"
            "port = bs._project_port()\n"
            "bs._start_viewer_server()\n"
            "s = socket.socket(); s.bind(('127.0.0.1', port)); print('FREE')\n")
    result = run_python(code)
    assert "FREE" in result.stdout, result.stdout + result.stderr


# -- Step 5: CLIs and the pre-commit validator -----------------------------------------------------

def test_coordinator_status_and_stop_in_server_mode(repo, monkeypatch, capsys):
    from taskmaster.coordinator import control_cli
    root = project(repo)
    mark(repo)
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened a store"))
    assert control_cli.main(["status", "--root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "server-mode" and report["host_lease_held"] is False and report["owner_held"] is False
    assert control_cli.main(["stop", "--root", str(root)]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "refused"
    assert not (root / ".taskmaster" / "local" / "coordinator").exists()


def test_git_cli_answers_not_managed_in_a_server_clone(repo, capsys, monkeypatch):
    """team-server-011 D8: status exits 0, the others exit 2, and no coordinator client is ever built."""
    from taskmaster.coordinator import client as client_module
    from taskmaster.coordinator import git_cli
    root = project(repo)
    mark(repo)
    monkeypatch.setattr(client_module, "Client", lambda *a, **k: pytest.fail("constructed a coordinator client"))
    for command, code in ((["status"], 0), (["commit", "-m", "x"], 2), (["checkout", "main"], 2), (["recover"], 2)):
        assert git_cli.main(["--root", str(root), *command]) == code
        answer = json.loads(capsys.readouterr().out)
        assert answer["state"] == "not-managed" and "plain git" in answer["detail"] and "taskmaster-state" in answer["detail"]
        assert answer["checkout"] == str(root)


def test_pre_commit_check_refuses_staged_projections_without_opening_the_store(repo, monkeypatch):
    from taskmaster.coordinator import git_hook
    import sqlite3
    root = project(repo)
    mark(repo)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened a store"))
    ok, text = git_hook.check(root)
    assert ok and "no projection files staged" in text
    (root / ".taskmaster" / "tasks").mkdir(parents=True)
    (root / ".taskmaster" / "tasks" / "x.yaml").write_text("id: x\n", encoding="utf-8")
    git("add", "-f", ".taskmaster/tasks/x.yaml", cwd=root)
    ok, text = git_hook.check(root)
    assert not ok and "server mode" in text


def test_add_user_skips_the_profile_check_in_server_mode(repo, capsys):
    from taskmaster.serve import cli
    root = project(repo)
    mark(repo)
    code = cli.main(["add-user", "ada", "--root", str(root)])
    out = capsys.readouterr()
    assert "profile check skipped: server mode" in out.out + out.err, (code, out)


# -- team-server-014 Step 2 (D14): the validator in server mode allows deletions ---------------------

def _tracked_tasks(repo):
    root = project(repo)
    (root / ".taskmaster" / "tasks").mkdir(parents=True)
    for name in ("a", "b"):
        (root / ".taskmaster" / "tasks" / f"{name}.yaml").write_text(f"id: {name}\n", encoding="utf-8")
    git("add", "-f", ".taskmaster/tasks", cwd=root)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "tracked", cwd=root)
    return root


def test_server_mode_staged_deletions_under_taskmaster_pass(repo):
    from taskmaster.coordinator import git_hook
    root = _tracked_tasks(repo)
    mark(repo)
    git("rm", "-r", "-q", "--cached", ".taskmaster", cwd=root)
    (root / "code.txt").write_text("resolved\n", encoding="utf-8")
    git("add", "code.txt", cwd=root)
    ok, text = git_hook.check(root)
    assert ok and "no projection files staged" in text, text


def test_server_mode_staged_additions_and_changes_are_refused_with_the_untrack_advice(repo):
    from taskmaster.coordinator import git_hook
    root = _tracked_tasks(repo)
    mark(repo)
    (root / ".taskmaster" / "tasks" / "a.yaml").write_text("id: changed\n", encoding="utf-8")
    git("add", ".taskmaster/tasks/a.yaml", cwd=root)
    ok, text = git_hook.check(root)
    assert not ok and "git rm -r --cached .taskmaster" in text and "git restore --staged" not in text
    git("restore", "--staged", ".taskmaster", cwd=root)
    (root / ".taskmaster" / "tasks" / "c.yaml").write_text("id: c\n", encoding="utf-8")
    git("add", "-f", ".taskmaster/tasks/c.yaml", cwd=root)
    ok, text = git_hook.check(root)
    assert not ok and "git rm -r --cached .taskmaster" in text


def test_server_mode_deleting_one_file_while_adding_another_is_still_refused(repo):
    from taskmaster.coordinator import git_hook
    root = _tracked_tasks(repo)
    mark(repo)
    git("rm", "-q", "--cached", ".taskmaster/tasks/a.yaml", cwd=root)
    (root / ".taskmaster" / "tasks" / "c.yaml").write_text("id: c\n", encoding="utf-8")
    git("add", "-f", ".taskmaster/tasks/c.yaml", cwd=root)
    ok, text = git_hook.check(root)
    assert not ok and "server mode" in text


def test_solo_mode_still_refuses_unmanaged_staged_deletions(repo, monkeypatch):
    from taskmaster.coordinator import git_hook
    root = _tracked_tasks(repo)
    seen = []
    real = git_hook._git
    monkeypatch.setattr(git_hook, "_git", lambda r, *a, **k: seen.append(a) or real(r, *a, **k))
    git("rm", "-q", "--cached", ".taskmaster/tasks/a.yaml", cwd=root)
    (root / ".taskmaster" / "local" / "store.db").write_bytes(b"x")
    ok, text = git_hook.check(root)
    assert not ok and "server mode" not in text
    assert not any(arg.startswith("--diff-filter") for call in seen for arg in call)
