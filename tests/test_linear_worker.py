"""Tests for the Linear sync worker (linear-004 piece 2/3).

The worker reads state from disk, builds payloads via the mapper, calls
the Linear client, and writes back the Tracker file on success. The queue
file is the source of truth for "what still needs pushing" — drain on a
restored backup is meaningful because the queue persists.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.integrations.linear.client import LinearAPIError, LinearClient  # noqa: E402
from taskmaster.integrations.linear.worker import (  # noqa: E402
    MAX_ATTEMPTS,
    drain,
    enqueue,
    push_task,
    queue_path,
    read_queue,
)
from taskmaster.taskmaster_v3 import write_tracker  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────


def _make_backlog(tmp_path: Path, *, task_id: str = "linear-001", tracker_id: str | None = None) -> Path:
    bp = tmp_path / "backlog.yaml"
    task = {
        "id": task_id,
        "title": "Some task",
        "status": "in-progress",
        "priority": "high",
        "owner": "Volodymyr",
        "tags": ["backend"],
    }
    if tracker_id:
        task["tracker_id"] = tracker_id
    bp.write_text(yaml.safe_dump({
        "meta": {"updated": "2026-01-01"},
        "epics": [{"id": "test-epic", "name": "Test", "tasks": [task]}],
    }))
    return bp


def _make_config() -> dict:
    return {
        "workspaces": [{
            "alias": "cm",
            "team_id": "team-uuid",
            "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
            "status_mapping": {
                "todo": "state-todo",
                "in-progress": "state-progress",
                "in-review": "state-review",
                "done": "state-done",
            },
            "priority_mapping": {"critical": 1, "high": 2, "medium": 3, "low": 4},
            "user_mapping": {"Volodymyr": "user-vol"},
            "label_config": {"tag_to_label_id": {"backend": "label-backend"}},
        }],
        "default_workspace": "cm",
    }


def _make_client(handler) -> LinearClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return LinearClient(token="test-tok", _http_client=http, _sleep=lambda _: None)


def _backlog_data(bp: Path) -> dict:
    with bp.open() as f:
        return yaml.safe_load(f)


# ── Queue file basics ──────────────────────────────────────────


def test_queue_path_lives_under_integrations(tmp_path):
    bp = tmp_path / "backlog.yaml"
    assert queue_path(bp) == bp.parent / "integrations" / "linear-queue.json"


def test_read_queue_returns_empty_when_missing(tmp_path):
    bp = tmp_path / "backlog.yaml"
    assert read_queue(bp) == []


def test_enqueue_creates_queue_file_and_appends_item(tmp_path):
    bp = _make_backlog(tmp_path)
    enqueue(bp, op="task_upsert", target_id="linear-001", tracker_id="linear-cm-eng-1")
    items = read_queue(bp)
    assert len(items) == 1
    assert items[0]["op"] == "task_upsert"
    assert items[0]["target_id"] == "linear-001"
    assert items[0]["tracker_id"] == "linear-cm-eng-1"


def test_enqueue_dedupes_same_target_same_op(tmp_path):
    """Multiple mutations on the same task before drain should result in
    ONE queue entry — the drain re-reads the latest task state, so a
    stack of pending mutations is wasted work."""
    bp = _make_backlog(tmp_path)
    enqueue(bp, op="task_upsert", target_id="linear-001")
    enqueue(bp, op="task_upsert", target_id="linear-001")
    enqueue(bp, op="task_upsert", target_id="linear-001")
    assert len(read_queue(bp)) == 1


def test_enqueue_keeps_different_targets_separate(tmp_path):
    bp = _make_backlog(tmp_path)
    enqueue(bp, op="task_upsert", target_id="linear-001")
    enqueue(bp, op="task_upsert", target_id="linear-002")
    assert len(read_queue(bp)) == 2


# ── push_task: skip paths ──────────────────────────────────────


def test_push_task_skips_when_task_has_no_tracker_id(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id=None)
    client = _make_client(lambda r: httpx.Response(500))  # would fail if called
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "skipped:no_tracker"


def test_push_task_skips_when_tracker_id_is_not_linear(tmp_path):
    """A task with a jira tracker_id is not our concern."""
    bp = _make_backlog(tmp_path, tracker_id="jira-codemaestro-cm-101")
    client = _make_client(lambda r: httpx.Response(500))
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "skipped:no_tracker"


def test_push_task_skips_when_task_not_in_backlog(tmp_path):
    bp = _make_backlog(tmp_path)
    client = _make_client(lambda r: httpx.Response(500))
    result = push_task(bp, "ghost-task", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "skipped:not_found"


def test_push_task_skips_when_push_hash_unchanged(tmp_path):
    """If the tracker already records a push_hash matching what we'd push,
    no API call. This is the token-economy core: a no-op TM update doesn't
    burn an HTTP round-trip."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    # Pre-write a tracker with a matching push_hash
    from taskmaster.integrations.linear.mapper import compute_push_hash, tm_task_to_linear_payload
    task = _backlog_data(bp)["epics"][0]["tasks"][0]
    cfg = _make_config()
    payload = tm_task_to_linear_payload(task, cfg["workspaces"][0], linear_issue_id="ENG-1")
    push_h = compute_push_hash(payload)
    write_tracker(
        bp,
        external_system="linear",
        instance_alias="cm",
        external_key="ENG-1",
        title="x",
        status="In Progress",
        last_pushed="2026-05-19T10:00:00Z",
        push_hash=push_h,
    )

    handler_calls = {"n": 0}

    def handler(request):
        handler_calls["n"] += 1
        return httpx.Response(200, json={"data": {}})

    client = _make_client(handler)
    result = push_task(bp, "linear-001", client, cfg, backlog_data=_backlog_data(bp))
    assert result["status"] == "skipped:unchanged"
    assert handler_calls["n"] == 0


# ── push_task: success path ────────────────────────────────────


def test_push_task_updates_linear_and_writes_tracker(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    # Pre-write tracker (without push_hash, so push will happen)
    write_tracker(
        bp,
        external_system="linear",
        instance_alias="cm",
        external_key="ENG-1",
        title="old title",
        status="Todo",
    )

    def handler(request):
        return httpx.Response(200, json={
            "data": {"issueUpdate": {"issue": {"id": "iss-uuid", "identifier": "ENG-1"}}},
        })

    client = _make_client(handler)
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "ok"

    # Tracker should now record the push
    from taskmaster.taskmaster_v3 import read_tracker
    fm, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm["push_hash"]  # set
    assert fm["last_pushed"]  # set
    assert fm["title"] == "Some task"  # refreshed from task


# ── push_task: error paths ─────────────────────────────────────


def test_push_task_returns_permanent_on_unmapped_status(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    # Corrupt the task so mapper fails
    data = _backlog_data(bp)
    data["epics"][0]["tasks"][0]["status"] = "exotic-status"
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    client = _make_client(lambda r: httpx.Response(200, json={"data": {}}))
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=data)
    assert result["status"] == "error:permanent"
    assert "status_mapping" in result["reason"]


def test_push_task_returns_transient_on_5xx(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    client = _make_client(lambda r: httpx.Response(500))
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "error:transient"


def test_push_task_returns_permanent_on_401(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    client = _make_client(lambda r: httpx.Response(401))
    result = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "error:permanent"


# ── drain ──────────────────────────────────────────────────────


def test_drain_removes_successful_items_from_queue(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    enqueue(bp, op="task_upsert", target_id="linear-001")

    def handler(request):
        return httpx.Response(200, json={
            "data": {"issueUpdate": {"issue": {"id": "iss-uuid", "identifier": "ENG-1"}}},
        })

    client = _make_client(handler)
    counts = drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert counts["ok"] == 1
    assert read_queue(bp) == []


def test_drain_keeps_failed_items_in_queue(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    enqueue(bp, op="task_upsert", target_id="linear-001")

    client = _make_client(lambda r: httpx.Response(500))
    counts = drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert counts["transient"] == 1
    remaining = read_queue(bp)
    assert len(remaining) == 1
    assert remaining[0]["attempts"] == 1
    assert remaining[0]["last_error"]


def test_drain_no_op_when_queue_empty(tmp_path):
    bp = _make_backlog(tmp_path)
    client = _make_client(lambda r: httpx.Response(500))
    counts = drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert counts == {"ok": 0, "skipped": 0, "transient": 0, "permanent": 0, "unknown": 0}


# ── B-027: error classification from the structured flag, not substrings ──


def test_push_task_classifies_transient_even_with_403_in_message(tmp_path):
    """A transient error whose message happens to contain '403' (IP octet,
    trace id) must stay transient — not be misread as a permanent 403 auth
    failure via substring matching."""
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")

    class _RaisingClient:
        def issue_upsert(self, team_id, payload):
            raise LinearAPIError("network error: cannot reach 10.0.0.403:443", permanent=False)

    result = push_task(bp, "linear-001", _RaisingClient(), _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "error:transient", result


def test_push_task_classifies_permanent_from_flag(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")

    class _RaisingClient:
        def issue_upsert(self, team_id, payload):
            raise LinearAPIError("token rejected", permanent=True)

    result = push_task(bp, "linear-001", _RaisingClient(), _make_config(), backlog_data=_backlog_data(bp))
    assert result["status"] == "error:permanent", result


# ── B-028: park after MAX_ATTEMPTS; parked items never re-hit the API ──


def test_drain_parks_transient_after_max_attempts_and_stops_calling_api(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="x", status="x")
    enqueue(bp, op="task_upsert", target_id="linear-001")

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    client = _make_client(handler)

    for _ in range(MAX_ATTEMPTS):
        drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))

    item = read_queue(bp)[0]
    assert item["permanent"] is True
    assert item["attempts"] == MAX_ATTEMPTS

    calls_before = calls["n"]
    counts = drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert calls["n"] == calls_before, "parked item must not re-issue the API call"
    assert counts["permanent"] == 1
    assert len(read_queue(bp)) == 1  # kept for /linear status visibility


# ── B-030: keep not_found; never silently drop an unknown status ──


def test_drain_keeps_not_found_item_in_queue(tmp_path):
    """skipped:not_found may be a stale snapshot — keep the pending push."""
    bp = _make_backlog(tmp_path)  # 'ghost' is not in the backlog
    enqueue(bp, op="task_upsert", target_id="ghost")
    client = _make_client(lambda r: httpx.Response(200, json={"data": {}}))
    counts = drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert counts["skipped"] == 1
    assert len(read_queue(bp)) == 1  # not dropped


def test_drain_keeps_item_on_unrecognized_status(tmp_path, monkeypatch):
    import taskmaster.integrations.linear.worker as _w
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    enqueue(bp, op="task_upsert", target_id="linear-001")
    monkeypatch.setattr(_w, "push_task", lambda *a, **k: {"status": "weird:unknown"})
    client = _make_client(lambda r: httpx.Response(200, json={"data": {}}))
    counts = _w.drain(bp, client, _make_config(), backlog_data=_backlog_data(bp))
    assert counts["unknown"] == 1
    assert len(read_queue(bp)) == 1


# ── B-031: persist the returned UUID and use it for subsequent updates ──


def test_push_persists_returned_uuid_and_uses_it_for_next_update(tmp_path):
    bp = _make_backlog(tmp_path, tracker_id="linear-cm-eng-1")
    write_tracker(bp, external_system="linear", instance_alias="cm",
                  external_key="ENG-1", title="old", status="Todo")

    sent_ids: list = []

    def handler(request):
        body = json.loads(request.content)
        sent_ids.append(body["variables"].get("id"))
        return httpx.Response(200, json={"data": {
            "issueUpdate": {"issue": {"id": "iss-uuid-123", "identifier": "ENG-1"}}}})

    client = _make_client(handler)

    # First push: no stored UUID yet → falls back to the human external_key.
    r1 = push_task(bp, "linear-001", client, _make_config(), backlog_data=_backlog_data(bp))
    assert r1["status"] == "ok"
    assert sent_ids[0] == "ENG-1"

    from taskmaster.taskmaster_v3 import read_tracker
    fm, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm["linear_issue_id"] == "iss-uuid-123"

    # Second push with a changed task → hash differs → addresses by stored UUID.
    data = _backlog_data(bp)
    data["epics"][0]["tasks"][0]["title"] = "new title"
    r2 = push_task(bp, "linear-001", client, _make_config(), backlog_data=data)
    assert r2["status"] == "ok"
    assert sent_ids[1] == "iss-uuid-123"


# ── B-032: forbid hyphenated workspace aliases at validate time ──


def test_validate_linear_config_rejects_hyphenated_alias():
    from taskmaster.taskmaster_v3 import _validate_linear_config
    cfg = {"workspaces": [{"alias": "cm-eng", "team_id": "t", "token_env": "TOK"}]}
    with pytest.raises(ValueError, match="must not contain"):
        _validate_linear_config(cfg)
