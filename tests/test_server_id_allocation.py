# User intent: a new task must never be handed an id the store already holds — the
# filesystem scan that used to allocate ids saw only the current checkout, so a linked
# worktree could reuse a live id and the compatibility write-back would silently replace
# that task's notes and gates while reporting "Added".
"""Task id allocation comes from authoritative store state (fix wave F2)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    (backlog_path / "tasks").mkdir(parents=True, exist_ok=True)
    (backlog_path / "local").mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "id-allocation",
        "meta": {"project": "id-allocation", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


def _point_server_at(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", root / ".taskmaster" / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")


def _tasks(data: dict) -> dict[str, dict]:
    return {
        task["id"]: task
        for epic in data.get("epics") or []
        for task in epic.get("tasks") or []
    }


@pytest.fixture()
def lagging_worktree(tmp_path, monkeypatch):
    """A linked worktree whose committed task files stop one id short of the store."""
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-b", "main", str(main))
    _git("config", "user.email", "id-tests@example.invalid", cwd=main)
    _git("config", "user.name", "Id Tests", cwd=main)
    _seed(main)
    _git("add", "-A", cwd=main)
    _git("commit", "-m", "seed", cwd=main)

    _point_server_at(monkeypatch, main)
    assert not bs.backlog_add_task("First", "core", phase="dev").startswith("Error")
    _git("add", "-A", cwd=main)
    _git("commit", "-m", "core-001", cwd=main)

    linked = tmp_path / "linked"
    _git("worktree", "add", "-b", "feature/lagging", str(linked), cwd=main)

    # core-002 lands in the store (and in the main checkout) only after the
    # worktree was branched, so the worktree's tasks/ directory still stops at
    # core-001 — exactly the state the old filesystem allocator misread.
    assert not bs.backlog_add_task("Second", "core", phase="dev").startswith("Error")
    assert not bs.backlog_update_task("core-002", "notes", "hard-won notes").startswith(
        "Error"
    )
    assert not (linked / ".taskmaster" / "tasks" / "core-002.md").exists()

    store.reset_for_tests()
    _point_server_at(monkeypatch, linked)
    return main, linked


def test_add_task_from_a_lagging_worktree_never_reuses_a_live_id(lagging_worktree):
    main, _linked = lagging_worktree

    result = bs.backlog_add_task("Third", "core", phase="dev")

    assert "core-003" in result, result
    store.reset_for_tests()
    tasks = _tasks(store.load_dict(main / ".taskmaster"))
    assert set(tasks) >= {"core-001", "core-002", "core-003"}
    assert tasks["core-002"]["title"] == "Second"
    assert tasks["core-002"]["notes"] == "hard-won notes", (
        "the new task overwrote the live task instead of taking a fresh id"
    )


def test_viewer_create_from_a_lagging_worktree_takes_the_store_wide_next_id(
    lagging_worktree,
):
    main, _linked = lagging_worktree

    new_id = bs._viewer_create_task({"epic": "core", "title": "Viewer third"})

    assert new_id == "core-003", new_id
    store.reset_for_tests()
    tasks = _tasks(store.load_dict(main / ".taskmaster"))
    assert tasks["core-002"]["notes"] == "hard-won notes"
    assert tasks["core-003"]["title"] == "Viewer third"


def test_viewer_create_never_reuses_a_tombstoned_id(tmp_path, monkeypatch):
    """A deleted task's id is gone for good; the old in-dict scan handed it back."""
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    _point_server_at(monkeypatch, root)
    bs.backlog_add_task("First", "core", phase="dev")
    bs.backlog_add_task("Second", "core", phase="dev")
    with store.transaction(tool="test:delete", backlog_path=backlog_path) as tx:
        tx.delete("task", "core-002")

    new_id = bs._viewer_create_task({"epic": "core", "title": "After the tombstone"})

    assert new_id == "core-003", new_id
    store.reset_for_tests()
    tasks = _tasks(store.load_dict(backlog_path))
    assert "core-002" not in tasks
    assert tasks["core-003"]["title"] == "After the tombstone"


def test_add_task_refuses_a_caller_supplied_id_the_store_already_holds(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    _seed(root)
    _point_server_at(monkeypatch, root)
    bs.backlog_add_task("First", "core", phase="dev")

    result = bs.backlog_add_task(
        "Clash", "core", phase="dev", options={"task_id": "core-001"}
    )

    assert result.startswith("Error"), result
    assert "already exists" in result


def test_a_duplicate_id_in_the_compatibility_dict_is_refused(tmp_path, monkeypatch):
    """The write-back must raise on a duplicate rather than collapse it to an update."""
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    _point_server_at(monkeypatch, root)
    bs.backlog_add_task("First", "core", phase="dev")

    data = store.load_dict(backlog_path)
    epic = data["epics"][0]
    epic["tasks"].append(dict(epic["tasks"][0], title="Impostor"))

    with pytest.raises(ValueError, match="appears twice"):
        store._flatten_backlog_dict(data)
