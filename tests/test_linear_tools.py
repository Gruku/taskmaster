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
from taskmaster import store as _store  # noqa: E402
from tests.entity_helpers import write_tracker  # noqa: E402


# ── Shared fixtures ─────────────────────────────────────────────


def _make_backlog(tmp_path: Path, *, with_tracker: bool = False) -> Path:
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
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


def _make_mapped_linear_yaml(tmp_path: Path) -> None:
    """linear.yaml complete enough that a push actually maps and succeeds.

    `_make_linear_yaml` omits the status mapping, so every push under it fails
    as `error:permanent` — fine for the tools that never push, useless for the
    drain tests that need a real outcome.
    """
    (tmp_path / ".taskmaster" / "linear.yaml").write_text(yaml.safe_dump({
        "workspaces": [{
            "alias": "cm", "team_id": "team-uuid-42",
            "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
            "status_mapping": {
                "todo": "state-todo",
                "in-progress": "state-progress",
                "done": "state-done",
            },
            "priority_mapping": {"critical": 1, "high": 2, "medium": 3, "low": 4},
        }],
        "default_workspace": "cm",
    }))


def _queue(bp: Path) -> list[dict]:
    """The Linear queue as a caller sees it: pending plus parked rows."""
    return _store.open_store(bp).linear_rows(states=("pending", "failed"))


def _seed_queue(bp: Path, items: list[dict]) -> list[int]:
    """Put rows in the store's queue the way a mutating tool would."""
    seqs = []
    with _store.open_store(bp).transaction(tool="test-seed") as tx:
        for item in items:
            seqs.append(tx.linear_enqueue(
                item.get("op", "task_upsert"),
                item["target_id"],
                item.get("tracker_id"),
                {"enqueued_at": item["enqueued_at"]} if item.get("enqueued_at") else None,
            ))
    for seq, item in zip(seqs, items):
        if item.get("state"):
            _store.open_store(bp).linear_mark(
                seq, state=item["state"], error=item.get("last_error"),
            )
    return seqs


def _make_linear_yaml(tmp_path: Path) -> None:
    (tmp_path / ".taskmaster" / "linear.yaml").write_text(yaml.safe_dump({
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
    cfg_path = tmp_path / ".taskmaster" / "linear.yaml"
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
    cfg = yaml.safe_load((tmp_path / ".taskmaster" / "linear.yaml").read_text())
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
    cfg = yaml.safe_load((tmp_path / ".taskmaster" / "linear.yaml").read_text())
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
    tp = tmp_path / ".taskmaster" / "trackers" / "linear-cm-eng-42.md"
    assert tp.exists()

    # Task has tracker_id set (the projection keeps tasks in tasks/<id>.md)
    from taskmaster.taskmaster_v3 import load_v4
    task = load_v4(bp)["epics"][0]["tasks"][0]
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

    from taskmaster.taskmaster_v3 import load_v4
    task = load_v4(bp)["epics"][0]["tasks"][0]
    assert "tracker_id" not in task or not task.get("tracker_id")

    # Tracker file is still on disk
    assert (tmp_path / ".taskmaster" / "trackers" / "linear-cm-eng-1.md").exists()


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

    _seed_queue(bp, [
        {"target_id": "ts-001", "enqueued_at": "2026-01-01T10:00:00Z"},
        {"target_id": "ts-002", "enqueued_at": "2026-01-02T10:00:00Z",
         "state": "failed", "last_error": "auth rejected"},
    ])
    result = json.loads(backlog_server.backlog_linear_status())
    assert result["queue_depth"] == 2
    assert result["permanent_failures"] == 1
    assert result["last_error"] == "auth rejected"
    assert result["oldest_enqueued_at"] == "2026-01-01T10:00:00Z"


# ── backlog_linear_retry ────────────────────────────────────────


def test_retry_drains_all_when_no_target(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_mapped_linear_yaml(tmp_path)
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

    _seed_queue(bp, [{"target_id": "ts-001", "tracker_id": "linear-cm-eng-1"}])

    result = json.loads(backlog_server.backlog_linear_retry())
    assert result["ok"] is True
    assert isinstance(result["counts"], dict)
    assert _queue(bp) == [], "a drained item must not stay queued"


def test_retry_target_id_filters_queue(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_mapped_linear_yaml(tmp_path)
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

    _seed_queue(bp, [
        {"target_id": "ts-001", "tracker_id": "linear-cm-eng-1"},
        {"target_id": "other-task", "enqueued_at": "2026-01-01T10:00:00Z"},
    ])

    result = json.loads(backlog_server.backlog_linear_retry(target_id="ts-001"))
    assert result["ok"] is True

    # other-task should still be queued, and untouched by the scoped drain
    remaining = _queue(bp)
    assert [i["target_id"] for i in remaining] == ["other-task"]
    assert remaining[0]["attempts"] == 0
    assert remaining[0]["last_error"] is None


def test_retry_gives_a_half_exhausted_pending_item_a_fresh_budget(tmp_path, monkeypatch):
    """B-028: `/linear retry` is the operator saying "try this properly again",
    so it clears the attempts a still-pending row has already burned — not only
    the parked flag."""
    bp = _make_backlog(tmp_path, with_tracker=True)
    _make_mapped_linear_yaml(tmp_path)
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="My task", status="todo")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_tok_test")

    import taskmaster.integrations.linear.client as _lc_mod
    monkeypatch.setattr(
        _lc_mod, "LinearClient",
        lambda token, **kw: _client_with_handler(
            lambda req: httpx.Response(500), token=token,
        ),
    )

    seq, = _seed_queue(bp, [{"target_id": "ts-001", "tracker_id": "linear-cm-eng-1"}])
    store = _store.open_store(bp)
    for _ in range(3):
        store.linear_mark(seq, state="pending", error="503 from Linear")
    assert _queue(bp)[0]["attempts"] == 3

    result = json.loads(backlog_server.backlog_linear_retry(target_id="ts-001"))
    assert result["ok"] is True
    # Cleared to 0, then this drain's own failure counted: one, not four.
    assert _queue(bp)[0]["attempts"] == 1


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

    _seed_queue(bp, [
        {"target_id": "ts-001", "tracker_id": "linear-cm-eng-1"},
        {"target_id": "other-task", "enqueued_at": "2026-01-01T10:00:00Z"},
    ])

    # Make the drain explode after the retry has un-parked its targets.
    import taskmaster.integrations.linear.worker as _wmod

    def _boom(*a, **k):
        raise RuntimeError("simulated crash mid-drain")

    monkeypatch.setattr(_wmod, "drain", _boom)

    with pytest.raises(RuntimeError):
        backlog_server.backlog_linear_retry(target_id="ts-001")

    remaining_ids = [i["target_id"] for i in _queue(bp)]
    assert "other-task" in remaining_ids, "other target's item was lost on crash"
    assert "ts-001" in remaining_ids, "retried item should also remain (never drained)"


def test_retry_unparks_permanent_item(tmp_path, monkeypatch):
    """B-028: an explicit /linear retry clears the parked flag so a previously
    permanent failure gets one fresh attempt."""
    bp = _make_backlog(tmp_path, with_tracker=True)
    # The mapped config: the push has to actually succeed once un-parked.
    _make_mapped_linear_yaml(tmp_path)
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

    # A parked (failed) queue row: a routine drain would never look at it again.
    _seed_queue(bp, [
        {"target_id": "ts-001", "tracker_id": "linear-cm-eng-1",
         "enqueued_at": "2026-01-01T10:00:00Z", "state": "failed",
         "last_error": "dead"},
    ])
    assert [r["state"] for r in _queue(bp)] == ["failed"]

    result = json.loads(backlog_server.backlog_linear_retry(target_id="ts-001"))
    assert result["ok"] is True
    # Un-parked and successfully pushed → nothing left for a caller to act on.
    assert _queue(bp) == []
