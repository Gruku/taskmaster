# User intent: over real HTTP, any tool, today's or a new one, reaches an admin coordinator method only for the operator on the host, and never from a thread that lost its request.
from __future__ import annotations

import json
import sys
import threading
from contextlib import closing
from pathlib import Path

import pytest

from serve_helpers import call_tool
from taskmaster.coordinator import allowlist, protocol
from taskmaster.coordinator.client import Client
from test_native_service_build import root  # noqa: F401
from test_serve_allowlist import ARGS, args_for
from test_serve_principals_e2e import launch, serve_log  # noqa: F401

TOOL = "t005_reach"
BOOTSTRAP = {"action": "bootstrap_apply", "workspace_alias": "ops", "team_id": "T2",
             "status_mapping": "todo:state-1,done:state-2"}


def high_water(root_):
    with closing(protocol.connect(root_, readonly=True)) as connection:
        return connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]


@pytest.fixture
def world(launch):  # noqa: F811
    served = launch()
    inner = sys.modules["taskmaster.backlog_server"].mcp._inner

    def reach(key: str, threaded: bool = False) -> str:
        """Test-only: call one coordinator method through the daemon's own client."""
        def attempt():
            method, arguments = args_for(key, served.root)
            try:
                Client(served.root, autostart=False).call(method, **arguments)
                return "passed:ok"
            except Exception as exc:  # noqa: BLE001 - the answer names what happened
                return "refused" if "host operator action" in str(exc) else f"passed:{type(exc).__name__}"
        if not threaded:
            return attempt()
        out = []
        thread = threading.Thread(target=lambda: out.append(attempt()))
        thread.start()
        thread.join(60)
        return out[0] if out else "timeout"
    inner.tool(reach, name=TOOL)
    yield served
    inner.local_provider.remove_tool(TOOL)


def _linear(world_, token, **arguments):
    return json.loads(call_tool(world_.url, token, "backlog_linear", dict(BOOTSTRAP, **arguments)))


def test_a_member_is_refused_linear_over_http(world):
    ann = world.keys["ann"].key
    assert "host operator action" in _linear(world, ann, token_env="")["error"]
    assert "host operator action" in _linear(world, ann, action="probe", token_env="SECRET_X")["error"]
    assert not (world.root / ".taskmaster/linear.yaml").exists()


def test_the_operator_bootstraps_over_loopback(world):
    refused = _linear(world, world.operator, token_env="OPS_LINEAR_TOKEN")
    assert refused == {"error": allowlist.LINEAR_TOKEN_ENV_REFUSED}
    assert not (world.root / ".taskmaster/linear.yaml").exists()
    answer = _linear(world, world.operator, token_env="")
    assert answer["ok"] is True, answer
    text = (world.root / ".taskmaster/linear.yaml").read_text(encoding="utf-8")
    assert "token_env: TASKMASTER_LINEAR_TOKEN" in text


@pytest.mark.parametrize("key", sorted(ARGS))
def test_any_tool_reaching_an_admin_method_needs_the_operator(world, key):
    ann = world.keys["ann"].key
    assert call_tool(world.url, ann, TOOL, {"key": key}) == "refused"
    assert call_tool(world.url, ann, TOOL, {"key": key, "threaded": True}) == "refused"
    assert call_tool(world.url, world.operator, TOOL, {"key": key}).startswith("passed:")
    # A thread the tool starts has no request context: refused even inside an operator request (D4).
    assert call_tool(world.url, world.operator, TOOL, {"key": key, "threaded": True}) == "refused"


def test_remote_methods_pass_for_everyone(world, monkeypatch):
    monkeypatch.setitem(ARGS, "status", {})
    for token in (world.keys["ann"].key, world.operator):
        for threaded in (False, True):
            assert call_tool(world.url, token, TOOL, {"key": "status", "threaded": threaded}) == "passed:ok"


def test_refusals_survive_a_member_flood_without_side_effects(world):
    before = high_water(world.root)
    for index in range(20):
        token = world.keys["ann" if index % 2 else "bob"].key
        assert call_tool(world.url, token, TOOL, {"key": "linear_bootstrap"}) == "refused"
    assert not (world.root / ".taskmaster/linear.yaml").exists()
    assert high_water(world.root) == before
    assert call_tool(world.url, world.keys["ann"].key, "backlog_list_tasks")


def test_only_the_client_posts_to_the_coordinator():
    package = Path(__file__).resolve().parents[1] / "taskmaster"
    hits = sorted(path.relative_to(package).as_posix() for path in package.rglob("*.py")
                  if "'/rpc'" in path.read_text(encoding="utf-8") or '"/rpc"' in path.read_text(encoding="utf-8"))
    assert set(hits) <= {"coordinator/client.py", "coordinator/service.py", "serve/config.py"}, hits
    assert "coordinator/client.py" in hits


def test_serve_log_names_refusals_without_arguments(world):
    ann = world.keys["ann"]
    assert call_tool(world.url, ann.key, TOOL, {"key": "git_run"}) == "refused"
    log = serve_log(world.root)
    assert f"refused git_run in the serve process for key {ann.id}" in log
    assert "not-a-kind" not in log
    assert all(user.secret not in log for user in world.keys.values())


# ── Step 7 (Part B): host files are not readable through a task's docs path ──

def _git_work_tree(root_, outside):
    import os
    from test_serve_viewer import git
    (root_ / "docs").mkdir(exist_ok=True)
    (root_ / "docs" / "spec.md").write_text("TRACKED-SPEC\n", encoding="utf-8")
    (root_ / ".gitignore").write_text(".taskmaster/local/\n", encoding="utf-8")
    try:
        os.symlink(os.path.join("..", ".env"), root_ / "docs" / "env")
        links = True
    except (OSError, NotImplementedError):
        links = False
    git(root_, "init", "-q")
    git(root_, "-c", "core.symlinks=true", "add", "docs", ".gitignore")
    git(root_, "-c", "core.symlinks=true", "commit", "-q", "-m", "init")
    (root_ / ".env").write_text("ENV-MARKER-1\n", encoding="utf-8")
    outside.write_text("OUTSIDE-MARKER-4\n", encoding="utf-8")
    return links


def test_a_member_cannot_read_host_files_through_docs(world, tmp_path):
    outside = tmp_path / "outside-secret.txt"
    links = _git_work_tree(world.root, outside)
    ann = world.keys["ann"].key
    paths = [str(outside), ".env"] + (["docs/env"] if links else [])
    for path in paths:
        updated = call_tool(world.url, ann, "backlog_update_task",
                            {"task_id": "test-epic-001", "field": "docs", "value": f"spec:{path}"})
        assert "Updated" in updated, updated
        answer = call_tool(world.url, ann, "backlog_get_task", {"task_id": "test-epic-001", "sections": ["spec"]})
        assert "(refused:" in answer and "MARKER" not in answer, (path, answer)
    call_tool(world.url, ann, "backlog_update_task",
              {"task_id": "test-epic-001", "field": "docs", "value": "spec:docs/spec.md"})
    assert "TRACKED-SPEC" in call_tool(world.url, ann, "backlog_get_task",
                                       {"task_id": "test-epic-001", "sections": ["spec"]})


# ── team-server-012 Step 1: a member cannot import host files by syncing them ─

def test_a_member_cannot_sync_an_untracked_host_note(world):
    """005 review finding 2's trigger, over HTTP: an untracked host note never reaches the store for a member."""
    import time
    ann = world.keys["ann"].key
    assert "NOTE-001" in call_tool(world.url, ann, "backlog_note", {"action": "create", "text": "seed text"})
    exported = world.root / ".taskmaster" / "notes" / "NOTE-001.md"
    deadline = time.monotonic() + 30
    while not exported.exists():
        assert time.monotonic() < deadline, "NOTE-001 was never exported"
        time.sleep(0.05)
    note = exported.read_text(encoding="utf-8").replace("NOTE-001", "NOTE-900").replace("seed text", "NOTE-MARKER-9")
    (world.root / ".taskmaster" / "notes" / "NOTE-900.md").write_text(note, encoding="utf-8")
    for arguments in ({"files": ["notes/NOTE-900.md"]}, {}):
        answer = call_tool(world.url, ann, "backlog_sync", arguments)
        assert answer == f"Error: {allowlist.HOST_SYNC_REFUSED}", answer
    assert "NOTE-MARKER-9" not in call_tool(world.url, ann, "backlog_note", {"action": "list"})
    assert "NOTE-MARKER-9" not in call_tool(world.url, ann, "backlog_note", {"action": "get", "note_id": "NOTE-900"})
    answer = call_tool(world.url, world.operator, "backlog_sync", {"files": ["notes/NOTE-900.md"]})
    assert not answer.startswith("Error:"), answer
    assert "NOTE-MARKER-9" in call_tool(world.url, ann, "backlog_note", {"action": "get", "note_id": "NOTE-900"})


# ── gate finding M3: role "operator" on a member key is not the operator ─────

def test_a_member_key_with_role_operator_is_refused(world):
    from taskmaster.serve.principals import add_user
    key, record, _ = add_user(world.root, "ops", role="operator")
    assert record.role == "operator" and record.key_id != "operator"
    assert call_tool(world.url, key, TOOL, {"key": "git_run"}) == "refused"
    assert call_tool(world.url, key, TOOL, {"key": "linear_bootstrap", "threaded": False}) == "refused"
    assert "host operator action" in _linear(world, key, token_env="")["error"]
