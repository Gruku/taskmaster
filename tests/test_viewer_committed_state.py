# User intent: the viewer must never show a task the store no longer holds, never accept a
# write whose If-Match was already outrun by another agent, and never let a PATCH orphan a
# task or fork it into a second copy under a new id.
"""Viewer reads and writes against committed state (fix wave F4, F5, F7)."""
from __future__ import annotations

import threading
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
        "project": "viewer",
        "meta": {"project": "viewer", "schema_version": 4},
        "epics": [
            {"id": "core", "name": "Core", "status": "active", "done_when": "n/a"},
            {"id": "other", "name": "Other", "status": "active", "done_when": "n/a"},
        ],
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
    bs.backlog_add_task("Target", "core", phase="dev")
    return backlog_path


def _committed_tasks(backlog_path: Path) -> dict[str, dict]:
    store.reset_for_tests()
    data = store.load_dict(backlog_path)
    return {
        task["id"]: task
        for epic in data.get("epics") or []
        for task in epic.get("tasks") or []
    }


# ── F5: detail comes from the store, never from a stale projection file ────


def test_task_detail_ignores_a_stale_projection_file(project):
    """A commit whose export could not land still shows its committed prose."""
    import os

    target = project / "tasks" / "core-001.md"
    before = target.read_text(encoding="utf-8")
    real_replace = os.replace

    def held_replace(src, dst, **kwargs):
        if Path(dst) == target:
            raise PermissionError(13, "held open by a peer", str(dst))
        return real_replace(src, dst, **kwargs)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "replace", held_replace)
        result = bs.backlog_update_task("core-001", "notes", "committed notes")
    assert not result.startswith("Error"), result
    assert target.read_text(encoding="utf-8") == before, "the export unexpectedly landed"
    assert f"tasks/core-001.md" in store.status(project).dirty_files

    detail = bs._load_task_full("core-001")

    assert detail["notes"] == "committed notes", (
        "task detail read the stale projection file instead of committed state"
    )


def test_task_detail_and_its_etag_come_from_one_snapshot(project):
    detail, etag = bs._load_task_full_identified("core-001")

    assert detail["id"] == "core-001"
    assert etag == bs._viewer_etag()


# ── F4: the precondition is evaluated inside the write transaction ─────────


def test_a_stale_if_match_is_refused_inside_the_write_transaction(project):
    """The precondition is now evaluated against committed state at write time."""
    stale = bs._viewer_etag()
    bs.backlog_update_task("core-001", "title", "Peer title")

    with pytest.raises(bs.ViewerPreconditionFailed) as exc:
        bs._viewer_update_task("core-001", {"title": "Viewer title"}, if_match=stale)

    assert exc.value.current_etag == bs._viewer_etag()
    assert _committed_tasks(project)["core-001"]["title"] == "Peer title"


def test_a_commit_between_the_check_and_the_write_is_not_overwritten(
    project, monkeypatch
):
    """The real race: a peer commits after the handler reads the current ETag.

    The ETag read is forced to publish a value that a peer immediately outruns.
    Before the fix the handler compared against that pre-peer value outside any
    transaction, accepted the write and erased the peer's title.
    """
    real_etag = bs._viewer_etag
    peer: dict[str, threading.Thread] = {}

    def racing_etag():
        value = real_etag()
        if "thread" not in peer:
            thread = threading.Thread(
                target=bs.backlog_update_task,
                args=("core-001", "title", "Peer title"),
            )
            thread.start()
            peer["thread"] = thread
            # Outside a transaction (the old check) this completes and the value
            # returned below is already stale; inside one it queues behind us.
            thread.join(timeout=2.0)
        return value

    if_match = real_etag()
    monkeypatch.setattr(bs, "_viewer_etag", racing_etag)

    base = project
    try:
        bs._viewer_update_task("core-001", {"title": "Viewer title"}, if_match=if_match)
    except bs.ViewerPreconditionFailed:
        pass
    finally:
        monkeypatch.setattr(bs, "_viewer_etag", real_etag)
        peer["thread"].join(timeout=30)

    assert _committed_tasks(base)["core-001"]["title"] == "Peer title", (
        "the viewer write overwrote a peer commit that landed after its check"
    )


def test_a_matching_if_match_still_commits(project):
    task = bs._viewer_update_task(
        "core-001", {"title": "Renamed"}, if_match=bs._viewer_etag()
    )

    assert task["title"] == "Renamed"
    assert _committed_tasks(project)["core-001"]["title"] == "Renamed"


def test_archive_refuses_a_stale_precondition(project):
    stale = bs._viewer_etag()
    bs.backlog_update_task("core-001", "branch", "feature/peer")

    with pytest.raises(bs.ViewerPreconditionFailed):
        bs._viewer_archive_task("core-001", if_match=stale)

    assert _committed_tasks(project)["core-001"]["status"] == "todo"


# ── F7: a PATCH can neither orphan a task nor fork it ──────────────────────


def test_patch_cannot_clear_the_epic(project):
    with pytest.raises(bs.ViewerWriteRejected) as exc:
        bs._viewer_update_task("core-001", {"epic": None})

    assert "epic" in exc.value.errors
    assert _committed_tasks(project)["core-001"]["epic"] == "core"


def test_patch_cannot_change_the_task_id(project):
    with pytest.raises(bs.ViewerWriteRejected) as exc:
        bs._viewer_update_task("core-001", {"id": "core-999"})

    assert "id" in exc.value.errors
    tasks = _committed_tasks(project)
    assert "core-999" not in tasks
    assert "core-001" in tasks


def test_patch_cannot_move_a_task_to_an_unknown_epic(project):
    with pytest.raises(bs.ViewerWriteRejected) as exc:
        bs._viewer_update_task("core-001", {"epic": "nope"})

    assert "epic" in exc.value.errors


def test_a_legal_epic_move_reparents_the_task_exactly_once(project):
    bs._viewer_update_task("core-001", {"epic": "other"})

    store.reset_for_tests()
    data = store.load_dict(project)
    holders = [
        epic["id"]
        for epic in data["epics"]
        for task in epic.get("tasks") or []
        if task["id"] == "core-001"
    ]
    assert holders == ["other"], holders
    row = next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks") or []
        if task["id"] == "core-001"
    )
    assert row["epic"] == "other"
