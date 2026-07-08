"""Tests for the Linear post-mutation hooks (linear-004 piece 3/3).

Verifies that backlog_add_task / update_task / complete_task / archive_task
enqueue a Linear push when (a) linear.yaml exists for the project and (b)
the task has a `linear-*` tracker_id. Otherwise the hook is a no-op and
the mutation behaves exactly as before.
"""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402
from taskmaster.integrations.linear.worker import read_queue  # noqa: E402
from taskmaster.taskmaster_v3 import write_tracker  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────


def _setup_project(
    tmp_path: Path,
    *,
    with_linear_config: bool = True,
    with_tracker: bool = True,
) -> Path:
    """Lay down backlog.yaml + (optionally) linear.yaml + (optionally) a Tracker
    file for a task with a linear tracker_id. Returns the backlog path."""
    bp = tmp_path / "backlog.yaml"
    task = {
        "id": "ts-001",
        "title": "Test task",
        "status": "todo",
        "priority": "medium",
        "owner": "Vol",
        "tags": ["backend"],
        "tldr": "test",
        "notes": "",
    }
    if with_tracker:
        task["tracker_id"] = "linear-cm-eng-1"
    bp.write_text(yaml.safe_dump({
        "meta": {"updated": "2026-01-01"},
        "epics": [{"id": "ts", "name": "Test", "tasks": [task]}],
        "phases": [],
    }))

    if with_linear_config:
        (tmp_path / "linear.yaml").write_text(yaml.safe_dump({
            "workspaces": [{
                "alias": "cm",
                "team_id": "team-uuid",
                "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
            }],
            "default_workspace": "cm",
        }))

    if with_tracker:
        write_tracker(
            bp,
            external_system="linear",
            instance_alias="cm",
            external_key="ENG-1",
            title="Test task",
            status="todo",
        )
    return bp


# ── Hook behaviour: enqueue ────────────────────────────────────


def test_update_task_enqueues_linear_push_when_synced(tmp_path, monkeypatch):
    bp = _setup_project(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    # Avoid touching PROGRESS.md regen (the helper writes alongside backlog)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    backlog_server.backlog_update_task("ts-001", "priority", "high")

    items = read_queue(bp)
    assert len(items) == 1
    assert items[0]["op"] == "task_upsert"
    assert items[0]["target_id"] == "ts-001"
    assert items[0]["tracker_id"] == "linear-cm-eng-1"


def test_complete_task_enqueues_linear_push(tmp_path, monkeypatch):
    bp = _setup_project(tmp_path)
    # Need an in-progress status first; bypass the in-progress requirement
    # by using update_task to flip status (which also enqueues, but the
    # de-dupe means only one queue item ends up there).
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    backlog_server.backlog_update_task("ts-001", "status", "in-progress")
    backlog_server.backlog_complete_task("ts-001")

    items = read_queue(bp)
    # De-duped on (op, target_id); both mutations hit the same item.
    assert len(items) == 1
    assert items[0]["target_id"] == "ts-001"


def test_archive_task_enqueues_linear_push(tmp_path, monkeypatch):
    bp = _setup_project(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    backlog_server.backlog_archive_task("ts-001", reason="superseded")

    items = read_queue(bp)
    assert len(items) == 1
    assert items[0]["target_id"] == "ts-001"


# ── Hook behaviour: no-op paths ────────────────────────────────


def test_update_task_no_enqueue_when_linear_yaml_missing(tmp_path, monkeypatch):
    """Projects without linear.yaml get zero behavioural change — the hook is
    silently a no-op and no queue file is created."""
    bp = _setup_project(tmp_path, with_linear_config=False, with_tracker=False)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    backlog_server.backlog_update_task("ts-001", "priority", "high")

    assert read_queue(bp) == []
    assert not (tmp_path / "integrations" / "linear-queue.json").exists()


def test_update_task_no_enqueue_when_task_has_no_tracker(tmp_path, monkeypatch):
    """linear.yaml exists, but the task itself isn't linked to a Linear issue
    yet. Mutation goes through; no enqueue."""
    bp = _setup_project(tmp_path, with_tracker=False)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    backlog_server.backlog_update_task("ts-001", "priority", "high")

    assert read_queue(bp) == []


def test_hook_swallows_exceptions(tmp_path, monkeypatch):
    """If the enqueue path raises for any reason, the local mutation must
    still succeed and return success — sync is non-fatal."""
    bp = _setup_project(tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "regenerate_progress_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(backlog_server, "regenerate_context", lambda *a, **k: None)

    # Replace the worker.enqueue import target with one that explodes
    from taskmaster.integrations.linear import worker as _worker

    def boom(*a, **k):
        raise RuntimeError("simulated queue failure")

    monkeypatch.setattr(_worker, "enqueue", boom)

    # Mutation should still succeed
    result = backlog_server.backlog_update_task("ts-001", "priority", "high")
    assert "Updated" in result
    assert "Error" not in result
