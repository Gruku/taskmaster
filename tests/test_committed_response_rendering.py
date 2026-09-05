# User intent: a tool must not tell a user their write failed when it committed. The
# "(not persisted)" marker exists to catch real losses, so it must fire only on real ones.
"""Committed-state response rendering has no false negatives (fix wave F8)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    for sub in ("tasks", "local"):
        (backlog_path / sub).mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "rendering",
        "meta": {"project": "rendering", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
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
    bs.backlog_add_task(
        "Target", "core", phase="dev", options={"anchors": "src/a.py,src/b.py"}
    )
    return backlog_path


def _committed_task(backlog_path: Path) -> dict:
    store.reset_for_tests()
    data = store.load_dict(backlog_path)
    return next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks") or []
        if task["id"] == "core-001"
    )


@pytest.mark.parametrize("field", ["anchors", "phase", "release"])
def test_clearing_a_removable_field_is_not_reported_as_a_failure(project, field):
    if field == "release":
        bs.backlog_update_task("core-001", "release", "v1.0.0")

    result = bs.backlog_update_task("core-001", field, "none")

    assert bs.NOT_PERSISTED not in result, result
    assert field not in _committed_task(project)


def test_a_later_writer_does_not_make_a_committed_write_look_lost(project, monkeypatch):
    """The response renders this writer's own commit, not a later re-read."""
    real_load = bs._load
    fired = {"done": False}

    def racing_load():
        # Only outside a transaction, i.e. exactly where the response used to be
        # re-read after the commit had already landed.
        if not fired["done"] and bs._active_tx() is None:
            fired["done"] = True
            bs.backlog_update_task("core-001", "branch", "feature/later")
        return real_load()

    monkeypatch.setattr(bs, "_load", racing_load)
    result = bs.backlog_update_task("core-001", "branch", "feature/mine")
    monkeypatch.setattr(bs, "_load", real_load)

    assert bs.NOT_PERSISTED not in result, result
    assert "feature/mine" in result, result


def test_a_write_that_really_did_not_land_is_still_reported(project):
    """The marker must still fire when committed state disagrees with the write."""
    assert (
        bs._committed_field_display({}, "core-001", "branch", "feature/x")
        == bs.NOT_PERSISTED
    )
    assert (
        bs._committed_field_display(
            {("task", "core-001"): {"branch": "feature/other"}},
            "core-001",
            "branch",
            "feature/x",
        )
        == bs.NOT_PERSISTED
    )
    assert (
        bs._committed_field_display(
            {("task", "core-001"): {"branch": "feature/x"}},
            "core-001",
            "branch",
            "feature/x",
        )
        == "feature/x"
    )
