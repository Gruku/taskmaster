# User intent: linking an idea to a task must not quietly revert whatever another agent
# committed on that task a moment earlier — the inverse-link write used to replace the
# whole task document it had read outside any transaction.
"""Auto-link's inverse sync shares one transaction with its read (fix wave F3)."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    for sub in ("tasks", "local", "ideas"):
        (backlog_path / sub).mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "auto-link",
        "meta": {"project": "auto-link", "schema_version": 4},
        "epics": [{"id": "T", "name": "Tasks", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", backlog_path / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    bs.backlog_add_task("Target", "T", phase="dev")
    return backlog_path


def _task(backlog_path: Path, task_id: str) -> dict:
    store.reset_for_tests()
    data = store.load_dict(backlog_path)
    return next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks") or []
        if task["id"] == task_id
    )


def test_a_concurrent_task_update_survives_idea_auto_linking(project, monkeypatch):
    """A peer commit landing while the link is being written must not be reverted."""
    backlog_path = project
    real_read = v3._ENTITY_IO["read"]
    racer: dict[str, threading.Thread] = {}
    reads = {"count": 0}

    def racing_read(bp, kind, task_id):
        document = real_read(bp, kind, task_id)
        # The lost-update window is the inverse-sync read (the second one), not
        # the existence check that precedes it.
        if task_id == "T-001":
            reads["count"] += 1
        if task_id == "T-001" and reads["count"] == 2 and "thread" not in racer:
            thread = threading.Thread(
                target=bs.backlog_update_task,
                args=("T-001", "branch", "feature/concurrent"),
            )
            thread.start()
            racer["thread"] = thread
            # Before the fix this read held no transaction, so the peer commits
            # here and the stale write-back below erases it.  With the fix the
            # peer queues behind our writer lock and lands afterwards instead.
            thread.join(timeout=2.0)
        return document

    monkeypatch.setitem(v3._ENTITY_IO, "read", racing_read)

    bs.backlog_idea_create(title="Link it", body="This relates to T-001.")

    racer["thread"].join(timeout=30)
    assert not racer["thread"].is_alive()

    task = _task(backlog_path, "T-001")
    assert task.get("branch") == "feature/concurrent", (
        "the inverse-link write reverted a concurrently committed branch"
    )
    targets = {link["target"] for link in v3.entity_links(task)}
    assert any(target.startswith("IDEA-") for target in targets), task.get("links")
