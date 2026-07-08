"""Tests for the backlog_linear_* MCP tools (linear-005).

All 8 tools: probe, bootstrap_apply, link, unlink, list, show, status, retry.
Filesystem-only tests use tmp_path + monkeypatch; network tests stub LinearClient
via httpx.MockTransport following the pattern in test_linear_client.py.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402
from taskmaster.integrations.linear.client import LinearClient  # noqa: E402
from taskmaster.integrations.linear.worker import read_queue, _write_queue  # noqa: E402
from taskmaster.taskmaster_v3 import write_tracker  # noqa: E402


# ── Shared fixtures ─────────────────────────────────────────────


def _make_backlog(tmp_path: Path, *, with_tracker: bool = False) -> Path:
    bp = tmp_path / "backlog.yaml"
    task: dict = {
        "id": "ts-001",
        "title": "My task",
        "status": "todo",
        "priority": "medium",
        "owner": "Vol",
        "tags": [],
        "tldr": "short",
        "notes": "",
    }
    if with_tracker:
        task["tracker_id"] = "linear-cm-eng-1"
    bp.write_text(yaml.safe_dump({
        "meta": {"updated": "2026-01-01"},
        "epics": [{"id": "ts", "name": "Test", "tasks": [task]}],
        "phases": [],
    }))
    return bp


def _make_linear_yaml(tmp_path: Path) -> None:
    (tmp_path / "linear.yaml").write_text(yaml.safe_dump({
        "workspaces": [{
            "alias": "cm",
            "team_id": "team-uuid-42",
            "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
        }],
        "default_workspace": "cm",
    }))


def _suppress_hooks(monkeypatch) -> None:
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)


def _client_with_handler(handler, token: str = "lin_api_test") -> LinearClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return LinearClient(token=token, _http_client=http, _sleep=lambda _: None)


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


# ── backlog_linear_probe ────────────────────────────────────────


def test_probe_missing_env_returns_error(monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN_VAR", raising=False)
    result = json.loads(backlog_server.backlog_linear_probe("MISSING_TOKEN_VAR"))
    assert "error" in result
    assert "https://linear.app/settings/api" in result["error"]


def test_probe_returns_teams_and_statuses(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body.get("query", "")
        if "teams" in query and "states" not in query and "users" not in query:
            return _ok({"teams": {"nodes": [{"id": "t1", "name": "Eng", "key": "ENG"}]}})
        if "states" in query:
            return _ok({"team": {"states": {"nodes": [{"id": "s1", "name": "Todo", "type": "unstarted"}]}}})
        if "users" in query:
            return _ok({"users": {"nodes": [{"id": "u1", "name": "Vol", "email": "v@x.com"}]}})
        return _ok({})

    monkeypatch.setenv("MY_LINEAR_TOKEN", "lin_tok_abc")
    # Patch LinearClient at module level to use our mock transport
    real_client_cls = backlog_server.__dict__.get("LinearClient")

    import taskmaster.integrations.linear.client as _lc_mod
    original = _lc_mod.LinearClient

    def fake_client(token, **kwargs):
        return _client_with_handler(handler, token=token)

    monkeypatch.setattr(_lc_mod, "LinearClient", fake_client)
    # backlog_linear_probe imports LinearClient inside the function from integrations.linear.client
    result = json.loads(backlog_server.backlog_linear_probe("MY_LINEAR_TOKEN"))
    monkeypatch.setattr(_lc_mod, "LinearClient", original)

    assert "teams" in result
    assert result["teams"][0]["name"] == "Eng"
    assert result["teams"][0]["statuses"][0]["name"] == "Todo"
    assert result["teams"][0]["users"][0]["name"] == "Vol"


# ── backlog_linear_bootstrap_apply ─────────────────────────────


def test_bootstrap_apply_creates_new_file(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _suppress_hooks(monkeypatch)

    result = json.loads(backlog_server.backlog_linear_bootstrap_apply(
        workspace_alias="cm",
        team_id="team-abc",
        token_env="TASKMASTER_LINEAR_TOKEN_CM",
    ))
    assert result["ok"] is True
    cfg_path = tmp_path / "linear.yaml"
    assert cfg_path.exists()
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["default_workspace"] == "cm"
    assert cfg["workspaces"][0]["alias"] == "cm"


def test_bootstrap_apply_appends_workspace(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _make_linear_yaml(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_bootstrap_apply(
        workspace_alias="prod",
        team_id="team-prod-uuid",
        token_env="TASKMASTER_LINEAR_TOKEN_PROD",
        default_workspace=False,
    ))
    assert result["ok"] is True
    cfg = yaml.safe_load((tmp_path / "linear.yaml").read_text())
    aliases = {ws["alias"] for ws in cfg["workspaces"]}
    assert "cm" in aliases
    assert "prod" in aliases


def test_bootstrap_apply_rejects_duplicate_alias(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _make_linear_yaml(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_bootstrap_apply(
        workspace_alias="cm",
        team_id="team-other",
        token_env="TASKMASTER_LINEAR_TOKEN_CM2",
    ))
    assert "error" in result
    assert "already exists" in result["error"]


def test_bootstrap_apply_parses_status_mapping(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_bootstrap_apply(
        workspace_alias="cm",
        team_id="team-abc",
        token_env="TASKMASTER_LINEAR_TOKEN_CM",
        status_mapping="todo:state-1,in-progress:state-2",
    ))
    assert result["ok"] is True
    cfg = yaml.safe_load((tmp_path / "linear.yaml").read_text())
    ws = cfg["workspaces"][0]
    assert ws["status_mapping"]["todo"] == "state-1"
    assert ws["status_mapping"]["in-progress"] == "state-2"


def test_bootstrap_apply_rejects_invalid_mapping(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_bootstrap_apply(
        workspace_alias="cm",
        team_id="team-abc",
        token_env="TASKMASTER_LINEAR_TOKEN_CM",
        status_mapping="todo:",  # empty right-hand side
    ))
    assert "error" in result


# ── backlog_linear_link ─────────────────────────────────────────


def test_link_creates_tracker_and_sets_tracker_id(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _make_linear_yaml(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _suppress_hooks(monkeypatch)

    result = json.loads(backlog_server.backlog_linear_link("ts-001", "ENG-42"))
    assert result["ok"] is True
    assert result["tracker_id"] == "linear-cm-eng-42"

    # Tracker file exists
    tp = tmp_path / "trackers" / "linear-cm-eng-42.md"
    assert tp.exists()

    # Task has tracker_id set
    data = yaml.safe_load(bp.read_text())
    task = data["epics"][0]["tasks"][0]
    assert task["tracker_id"] == "linear-cm-eng-42"


def test_link_rejects_nonexistent_task(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _make_linear_yaml(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_link("no-such-task", "ENG-1"))
    assert "error" in result


def test_link_rejects_already_linked_task(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_link("ts-001", "ENG-99"))
    assert "error" in result
    assert "already has tracker_id" in result["error"]


# ── backlog_linear_unlink ───────────────────────────────────────


def test_unlink_clears_tracker_id(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _suppress_hooks(monkeypatch)

    result = json.loads(backlog_server.backlog_linear_unlink("ts-001"))
    assert result["ok"] is True
    assert result["unlinked"] == "linear-cm-eng-1"

    data = yaml.safe_load(bp.read_text())
    task = data["epics"][0]["tasks"][0]
    assert "tracker_id" not in task or not task.get("tracker_id")

    # Tracker file is still on disk
    assert (tmp_path / "trackers" / "linear-cm-eng-1.md").exists()


def test_unlink_idempotent_when_no_tracker(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_unlink("ts-001"))
    assert result["ok"] is True
    assert "nothing to unlink" in result["note"]


# ── backlog_linear_list ─────────────────────────────────────────


def test_list_returns_linear_trackers_only(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-10", title="Issue 10", status="todo")
    # Also write a jira tracker to confirm it's excluded
    write_tracker(bp, external_system="jira", instance_alias="jira-cm",
                  external_key="CM-5", title="Jira issue", status="In Progress")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_list())
    trackers = result["trackers"]
    ids = [t["id"] for t in trackers]
    assert "linear-cm-eng-10" in ids
    # jira tracker should not appear
    assert all(t["id"].startswith("linear-") for t in trackers)


def test_list_empty_when_no_trackers(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_list())
    assert result["trackers"] == []


# ── backlog_linear_show ─────────────────────────────────────────


def test_show_returns_tracker_frontmatter(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-7", title="Show test", status="in-progress")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_show("linear-cm-eng-7"))
    assert result["frontmatter"]["id"] == "linear-cm-eng-7"
    assert result["frontmatter"]["title"] == "Show test"


def test_show_404_for_missing_tracker(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_show("linear-cm-no-such"))
    assert "error" in result


# ── backlog_linear_status ───────────────────────────────────────


def test_status_empty_queue(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_status())
    assert result["queue_depth"] == 0
    assert result["permanent_failures"] == 0
    assert result["oldest_enqueued_at"] is None


def test_status_reflects_queue_items(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    _write_queue(bp, [
        {"op": "task_upsert", "target_id": "ts-001", "enqueued_at": "2026-01-01T10:00:00Z",
         "attempts": 0, "last_error": None, "permanent": False},
        {"op": "task_upsert", "target_id": "ts-002", "enqueued_at": "2026-01-02T10:00:00Z",
         "attempts": 3, "last_error": "auth rejected", "permanent": True},
    ])
    result = json.loads(backlog_server.backlog_linear_status())
    assert result["queue_depth"] == 2
    assert result["permanent_failures"] == 1
    assert result["last_error"] == "auth rejected"
    assert result["oldest_enqueued_at"] == "2026-01-01T10:00:00Z"


# ── backlog_linear_retry ────────────────────────────────────────


def test_retry_drains_all_when_no_target(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_tok_test")

    # Stub LinearClient so no real network call happens
    import taskmaster.integrations.linear.client as _lc_mod

    def fake_client_cls(token, **kwargs):
        def handler(req):
            return httpx.Response(200, json={"data": {
                "issueCreate": {"issue": {"id": "lin-id-1", "identifier": "ENG-1"}}
            }})
        return _client_with_handler(handler, token=token)

    monkeypatch.setattr(_lc_mod, "LinearClient", fake_client_cls)

    # Enqueue one item
    from taskmaster.integrations.linear.worker import enqueue
    enqueue(bp, op="task_upsert", target_id="ts-001", tracker_id="linear-cm-eng-1")

    result = json.loads(backlog_server.backlog_linear_retry())
    assert result["ok"] is True
    # Queue should be empty or have only one item
    assert isinstance(result["counts"], dict)


def test_retry_target_id_filters_queue(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_tok_test")

    import taskmaster.integrations.linear.client as _lc_mod

    def fake_client_cls(token, **kwargs):
        def handler(req):
            return httpx.Response(200, json={"data": {
                "issueCreate": {"issue": {"id": "lin-id-1", "identifier": "ENG-1"}}
            }})
        return _client_with_handler(handler, token=token)

    monkeypatch.setattr(_lc_mod, "LinearClient", fake_client_cls)

    # Enqueue two items
    from taskmaster.integrations.linear.worker import enqueue
    enqueue(bp, op="task_upsert", target_id="ts-001", tracker_id="linear-cm-eng-1")
    _write_queue(bp, read_queue(bp) + [
        {"op": "task_upsert", "target_id": "other-task", "tracker_id": None,
         "enqueued_at": "2026-01-01T10:00:00Z", "attempts": 0, "last_error": None}
    ])

    result = json.loads(backlog_server.backlog_linear_retry(target_id="ts-001"))
    assert result["ok"] is True

    # other-task should still be in queue
    remaining = read_queue(bp)
    remaining_ids = [i["target_id"] for i in remaining]
    assert "other-task" in remaining_ids


def test_retry_error_when_no_linear_yaml(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    result = json.loads(backlog_server.backlog_linear_retry())
    assert "error" in result
    assert "linear.yaml" in result["error"]


def test_retry_target_preserves_other_items_when_drain_crashes(tmp_path, monkeypatch):
    """B-029: a target-scoped retry must not destroy other targets' queued items
    if the drain crashes mid-flight. With the old subset-write-then-restore, the
    others were off-disk during the drain and lost on crash."""
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_tok_test")

    import taskmaster.integrations.linear.client as _lc_mod
    monkeypatch.setattr(
        _lc_mod, "LinearClient",
        lambda token, **kw: _client_with_handler(lambda req: _ok({}), token=token),
    )

    from taskmaster.integrations.linear.worker import enqueue
    enqueue(bp, op="task_upsert", target_id="ts-001", tracker_id="linear-cm-eng-1")
    _write_queue(bp, read_queue(bp) + [
        {"op": "task_upsert", "target_id": "other-task", "tracker_id": None,
         "enqueued_at": "2026-01-01T10:00:00Z", "attempts": 0, "last_error": None}
    ])

    # Make the drain explode after the retry has rewritten the full queue.
    import taskmaster.integrations.linear.worker as _wmod

    def _boom(*a, **k):
        raise RuntimeError("simulated crash mid-drain")

    monkeypatch.setattr(_wmod, "drain", _boom)

    with pytest.raises(RuntimeError):
        backlog_server.backlog_linear_retry(target_id="ts-001")

    remaining_ids = [i["target_id"] for i in read_queue(bp)]
    assert "other-task" in remaining_ids, "other target's item was lost on crash"
    assert "ts-001" in remaining_ids, "retried item should also remain (never drained)"


def test_retry_unparks_permanent_item(tmp_path, monkeypatch):
    """B-028: an explicit /linear retry clears the parked flag so a previously
    permanent failure gets one fresh attempt."""
    bp = _make_backlog(tmp_path, with_tracker=True)
    # Config needs a status_mapping so the push can actually succeed once un-parked.
    (tmp_path / "linear.yaml").write_text(yaml.safe_dump({
        "workspaces": [{
            "alias": "cm", "team_id": "team-uuid-42",
            "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
            "status_mapping": {"todo": "state-todo", "in-progress": "state-progress", "done": "state-done"},
            "priority_mapping": {"critical": 1, "high": 2, "medium": 3, "low": 4},
        }],
        "default_workspace": "cm",
    }))
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_tok_test")

    import taskmaster.integrations.linear.client as _lc_mod
    monkeypatch.setattr(
        _lc_mod, "LinearClient",
        lambda token, **kw: _client_with_handler(
            lambda req: _ok({"issueUpdate": {"issue": {"id": "lin-id-1", "identifier": "ENG-1"}}}),
            token=token,
        ),
    )

    # A parked (permanent) item on disk.
    _write_queue(bp, [
        {"op": "task_upsert", "target_id": "ts-001", "tracker_id": "linear-cm-eng-1",
         "enqueued_at": "2026-01-01T10:00:00Z", "attempts": 7,
         "last_error": "dead", "permanent": True}
    ])

    result = json.loads(backlog_server.backlog_linear_retry(target_id="ts-001"))
    assert result["ok"] is True
    # Un-parked and successfully pushed → removed from the queue.
    assert read_queue(bp) == []
