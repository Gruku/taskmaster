# User intent: in a teammate's clone (a marker and a key, no `.taskmaster/`), the edit, merge-gate and merge
# recorder hooks read the team server through the read endpoint, answer as the solo hooks would on the same store,
# keep their state in the user's cache dir, the merge gate fails closed when the server gives no usable answer
# (team-server-008), and none of them creates a local store.
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from serve_helpers import call_tool
from taskmaster.remote.keys import write_key
from taskmaster.remote.locate import server_ref
from test_serve_read_api import root, serve  # noqa: F401  (fixtures)
from test_serve_viewer import git

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"


@pytest.fixture
def clone(tmp_path):
    path = tmp_path / "clone"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "api").mkdir()
    (path / "api" / "model.py").write_text("x = 1\n", encoding="utf-8")
    git(path, "add", "api")
    git(path, "commit", "-q", "-m", "init")
    git(path, "checkout", "-q", "-b", "shared")
    (path / "api" / "model.py").write_text("x = 2\n", encoding="utf-8")
    git(path, "commit", "-q", "-am", "work")
    git(path, "checkout", "-q", "main")
    return path


@pytest.fixture
def teammate(serve, clone, tmp_path):  # noqa: F811
    world = serve()
    (clone / ".taskmaster-server.json").write_text(json.dumps(
        {"format": 1, "host_id": HOST_ID, "mode": "server", "url": world.url}), encoding="utf-8")
    env = dict(os.environ, TASKMASTER_CONFIG_DIR=str(tmp_path / "cfg"), TASKMASTER_CACHE_DIR=str(tmp_path / "cache"))
    env.pop("TASKMASTER_ROOT", None)
    for name in ("TASKMASTER_SERVER_KEY", "TASKMASTER_SERVER_URL"):
        env.pop(name, None)
    write_key(world.url, world.keys["ann"], env)
    world.clone, world.env = clone, env
    world.state = tmp_path / "cache" / "remote" / server_ref(world.url).server_id / "hooks"
    return world


def run_hook(script, world, *, stdin="", args=(), cwd=None, env=None):
    return subprocess.run([sys.executable, str(HOOKS / script), *args], input=stdin, text=True, encoding="utf-8",
                          capture_output=True, cwd=str(cwd or world.clone), env=env or world.env, timeout=60)


def edit(world, rel, session="s1", env=None, root=None):
    target = (root or world.clone) / rel
    payload = {"session_id": session, "cwd": str(root or world.clone), "tool_name": "Edit",
               "tool_input": {"file_path": str(target)}, "tool_response": {"success": True}}
    done = run_hook("edit_resurface.py", world, stdin=json.dumps(payload), cwd=root, env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] if done.stdout else ""


def log_text(world):
    path = world.state / "hook.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_edit_resurface_answers_as_the_solo_hook_does(teammate):
    remote = edit(teammate, "api/model.py")
    solo_env = {k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"}
    solo = edit(teammate, "api/model.py", env=solo_env, root=teammate.root)
    assert remote == solo and remote.startswith("TM: api/model.py → test-epic-004 todo"), (remote, solo)
    assert edit(teammate, "api/model.py") == ""            # deduped on the server's revision
    assert edit(teammate, "api/model.py", session="s2") == remote
    assert (teammate.state / "hook-seen" / "s1.json").exists()
    assert not (teammate.clone / ".taskmaster").exists()


def test_edit_resurface_is_quiet_without_a_key(teammate):
    (Path(teammate.env["TASKMASTER_CONFIG_DIR"]) / "keys").rename(Path(teammate.env["TASKMASTER_CONFIG_DIR"]) / "k")
    assert edit(teammate, "api/model.py") == ""
    assert "no-key" in log_text(teammate) and "taskmaster remote set-key" in log_text(teammate)
    assert not (teammate.clone / ".taskmaster").exists()


def decide(world, src, env=None):
    done = run_hook("merge_gate_decide.py", world, args=(src, str(world.clone)), env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_merge_gate_reads_the_server_and_checks_the_tip_here(teammate):
    assert decide(teammate, "shared") == "BLOCK:test-epic-001: no review-gate has been recorded for this branch."
    tip = subprocess.run(["git", "-C", str(teammate.clone), "rev-parse", "shared"], capture_output=True,
                         text=True, timeout=30).stdout.strip()
    ann = teammate.keys["ann"]
    for tool, args in (("backlog_update_task", {"task_id": "test-epic-001", "field": "lane", "value": "express"}),
                       ("backlog_record_gate", {"task_id": "test-epic-001", "gate": "impl", "status": "done"}),
                       ("backlog_record_gate", {"task_id": "test-epic-001", "gate": "review-gate",
                                                "verdict": "pass", "commit_sha": "0" * 40})):
        assert not call_tool(teammate.url, ann, tool, args).startswith("Error"), (tool, args)
    assert decide(teammate, "shared").startswith("BLOCK:test-epic-001: the review-gate passed at 000000000000")
    call_tool(teammate.url, ann, "backlog_record_gate", {"task_id": "test-epic-001", "gate": "review-gate",
                                                         "verdict": "pass", "commit_sha": tip})
    assert decide(teammate, "shared") == "ALLOW"
    assert decide(teammate, "untracked-branch") == "ALLOW"
    assert not (teammate.clone / ".taskmaster").exists()


def test_the_merge_gate_blocks_on_every_remote_failure(teammate, tmp_path):
    env = dict(teammate.env, TASKMASTER_CONFIG_DIR=str(tmp_path / "nokeys"))
    got = decide(teammate, "shared", env=env)
    assert got.startswith("BLOCK:-: no key for the Taskmaster server at ") and "set-key" in got
    assert "no-key" in log_text(teammate)
    teammate.daemon.request_stop("server goes away")
    teammate.thread.join(30)
    started = time.monotonic()
    got = decide(teammate, "shared")
    assert got.startswith("BLOCK:-: Taskmaster server unreachable; retry or bypass explicitly (")
    assert time.monotonic() - started < 9
    assert "unreachable" in log_text(teammate)


def test_an_ambiguous_branch_blocks_naming_the_candidates(teammate, tmp_path):
    from taskmaster.remote.keys import write_key as store
    env = dict(teammate.env, TASKMASTER_CONFIG_DIR=str(tmp_path / "carol"))
    store(teammate.url, teammate.keys["carol"], env)
    got = decide(teammate, "shared", env=env)
    assert got.startswith("BLOCK:-: several tasks use branch shared (test-epic-001, test-epic-002) and none of them is yours")
    assert "ambiguous" in log_text(teammate) and "test-epic-001, test-epic-002" in log_text(teammate)


def test_against_a_server_that_never_answers_the_gate_answers_within_8_s(teammate, tmp_path):
    import socket
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    held = []

    def hold():                       # accept and never answer
        try:
            for _ in range(4):
                held.append(listener.accept())
        except OSError:
            pass
    threading.Thread(target=hold, daemon=True).start()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
    (teammate.clone / ".taskmaster-server.json").write_text(json.dumps(
        {"format": 1, "host_id": HOST_ID, "mode": "server", "url": url}), encoding="utf-8")
    env = dict(teammate.env, TASKMASTER_CONFIG_DIR=str(tmp_path / "silent"))
    write_key(url, teammate.keys["ann"], env)
    started = time.monotonic()
    got = decide(teammate, "shared", env=env)
    assert got.startswith("BLOCK:-: Taskmaster server unreachable"), got
    assert time.monotonic() - started < 9.5
    listener.close()


def rev(path, ref):
    return subprocess.run(["git", "-C", str(path), "rev-parse", ref], capture_output=True, text=True,
                          check=True).stdout.strip()


def _recorded(world, task="test-epic-001"):
    """What the host's store says about a task's merges: the after-images of its events, read straight from the DB."""
    import sqlite3
    from contextlib import closing
    from taskmaster.coordinator import protocol
    with closing(protocol.connect(world.root, readonly=True)) as connection:
        rows = connection.execute("SELECT after FROM domain_events WHERE kind = 'task' AND id = ? ORDER BY seq",
                                  (task,)).fetchall()
    return " ".join(str(row[0]) for row in rows)


def _stamp(world, branch, target, sha, cwd=None):
    done = run_hook("merge_recorder_stamp.py", world, args=(branch, target, sha, str(cwd or world.clone)))
    assert done.returncode == 0, done.stderr
    return done


def _queue_dir(world):
    return Path(world.env["TASKMASTER_CACHE_DIR"]) / "remote" / server_ref(world.url).server_id / "stamps"


def test_a_stamp_in_a_teammate_clone_records_on_the_host_task_and_creates_no_store(teammate):
    tip = rev(teammate.clone, "shared")
    _stamp(teammate, "shared", "main", tip)
    assert tip in _recorded(teammate)
    assert not (teammate.clone / ".taskmaster").exists()
    assert not _queue_dir(teammate).exists() or not (_queue_dir(teammate) / "merge-stamps-pending.jsonl").exists()


def test_an_older_stamp_is_dropped_by_ancestry_computed_in_the_clone(teammate):
    tip = rev(teammate.clone, "shared")
    base = rev(teammate.clone, "main")
    _stamp(teammate, "shared", "main", tip)
    _stamp(teammate, "shared", "main", base)
    text = _recorded(teammate)
    assert tip in text and base not in text
    assert "superseded" in log_text(teammate)


def test_a_server_that_is_down_queues_the_stamp_and_the_next_stamp_replays_it(teammate):
    tip = rev(teammate.clone, "shared")
    teammate.daemon.request_stop("server goes away")
    teammate.thread.join(30)
    _stamp(teammate, "shared", "main", tip)
    pending = _queue_dir(teammate) / "merge-stamps-pending.jsonl"
    assert pending.is_file() and tip in pending.read_text(encoding="utf-8")
    assert "queued for the server" in log_text(teammate)
    assert not (teammate.clone / ".taskmaster").exists()


def test_a_queued_entry_is_replayed_by_the_next_stamp(teammate):
    first = rev(teammate.clone, "shared")
    queue = _queue_dir(teammate)
    queue.mkdir(parents=True)
    entry = {"branch": "solo-b", "target": "main", "sha": first, "merged_at": "2026-11-07T10:00",
             "repo": str(teammate.clone), "queued_at": time.time()}
    pending = queue / "merge-stamps-pending.jsonl"
    pending.write_text(json.dumps(entry) + chr(10), encoding="utf-8")
    _stamp(teammate, "shared", "main", first)
    assert first in _recorded(teammate, "test-epic-003")
    assert not pending.exists() or pending.read_text(encoding="utf-8").strip() == ""


def test_a_branch_the_ref_check_refuses_is_logged_and_not_queued(teammate):
    _stamp(teammate, "bad branch", "main", rev(teammate.clone, "main"))
    assert "not recording the merge of bad branch" in log_text(teammate)
    assert not (_queue_dir(teammate) / "merge-stamps-pending.jsonl").exists()


def _recorder(world, command, cwd=None):
    payload = {"session_id": "s1", "cwd": str(cwd or world.clone), "tool_name": "Bash",
               "tool_input": {"command": command}, "tool_response": {"exit_code": 0}}
    done = run_hook("merge_recorder.py", world, stdin=json.dumps(payload), cwd=cwd)
    assert done.returncode == 0, done.stderr


def test_the_recorder_stamps_the_merged_branch_not_a_later_command(teammate):
    _recorder(teammate, "git merge shared && git push origin main")
    head = rev(teammate.clone, "HEAD")
    assert head in _recorded(teammate)


def test_an_octopus_merge_stamps_every_branch(teammate):
    head = rev(teammate.clone, "HEAD")
    _recorder(teammate, "git merge shared solo-b")
    assert head in _recorded(teammate, "test-epic-001") and head in _recorded(teammate, "test-epic-003")


def test_a_dash_C_merge_reads_head_in_that_directory(teammate):
    head = rev(teammate.clone, "HEAD")
    _recorder(teammate, f"git -C {teammate.clone.name} merge shared", cwd=teammate.clone.parent)
    assert head in _recorded(teammate)


def test_the_solo_path_never_imports_the_client(tmp_path):
    probe = ("import sys, pathlib, importlib.util as u\n"
             "s = u.spec_from_file_location('g', sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m)\n"
             "print(m.decide('feature/none', pathlib.Path(sys.argv[2])))\n"
             "print(any(n.startswith('taskmaster.remote') for n in sys.modules))\n")
    (tmp_path / ".taskmaster").mkdir()
    done = subprocess.run([sys.executable, "-c", probe, str(HOOKS / "merge_gate_decide.py"), str(tmp_path)],
                          capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
                          env={k: v for k, v in os.environ.items() if k != "TASKMASTER_ROOT"})
    assert done.stdout.split() == ["ALLOW", "False"], (done.stdout, done.stderr)


# ── team-server-011 Step 2: the hooks send this clone's id ──────────────────────────────────────


def _spy_views(monkeypatch):
    from taskmaster.native_routing import hook_reads
    seen = []
    for name in ("merge_gate_view", "merge_stamp_plan"):
        real = getattr(hook_reads, name)

        def wrapper(*args, __real=real, __name=name, **kwargs):
            seen.append((__name, kwargs.get("clone")))
            return __real(*args, **kwargs)
        monkeypatch.setattr(hook_reads, name, wrapper)
    return seen


def test_the_gate_and_the_stamp_send_the_clone_id(teammate, monkeypatch):
    from taskmaster.remote import clone as clone_module
    seen = _spy_views(monkeypatch)
    decide(teammate, "shared")
    mine = clone_module.clone_id(teammate.clone, create=False)
    assert mine and ("merge_gate_view", mine) in seen
    _stamp(teammate, "shared", "main", rev(teammate.clone, "shared"))
    assert ("merge_stamp_plan", mine) in seen


def test_with_no_git_directory_no_clone_is_sent(teammate, monkeypatch):
    import shutil
    seen = _spy_views(monkeypatch)
    nested = teammate.clone / "sub"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    (nested / ".taskmaster-server.json").write_text((teammate.clone / ".taskmaster-server.json").read_text(
        encoding="utf-8"), encoding="utf-8")
    decide_in = run_hook("merge_gate_decide.py", teammate, args=("shared", str(nested)), cwd=nested)
    assert decide_in.returncode == 0
    assert seen and all(clone is None for _name, clone in seen)
    shutil.rmtree(nested, ignore_errors=True)
