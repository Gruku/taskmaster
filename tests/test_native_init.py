"""User intent: a project created by `backlog_init` is born on the native store and every later
call is served natively, while init on anything that already exists (a legacy store, a native
store, a legacy layout, an orphaned store) behaves exactly as before and writes nothing.
"""
from __future__ import annotations

from contextlib import closing
import re
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native import cutover, fresh
from native_twins import activate_native, is_native, point_server_at, scaffold


@pytest.fixture
def coordinator(monkeypatch):
    """Native writes go to an in-process coordinator, as the routed-tool tests run them."""
    from taskmaster.coordinator import adapter
    from tests.native_coordinator_helpers import compatibility_client
    monkeypatch.setattr(adapter, "Client", compatibility_client)


def _database(root):
    return root / ".taskmaster" / "local" / "store.db"


def _digest(root) -> str:
    store.reset_for_tests()
    with closing(sqlite3.connect(_database(root))) as connection:
        return cutover.domain_digest(connection)


def _files(root) -> dict:
    base = root / ".taskmaster"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(base.rglob("*"))
            if p.is_file() and "local" not in p.relative_to(base).parts} if base.exists() else {}


def test_fresh_init_is_native_and_later_calls_run_natively(tmp_path, monkeypatch, coordinator):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)

    result = bs.backlog_init(project_name="born")
    assert "on the native store" in result, result
    assert ".taskmaster/local/store.db" in result
    assert is_native(root)
    with closing(sqlite3.connect(_database(root))) as connection:
        assert connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone() == ("native",)
        # The legacy bootstrap's own session rows do not survive into the native store.
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)
    for rel in ("taskmaster.json", "backlog.yaml", "tasks", "areas", "handovers", "issues",
                "local/.gitignore", "local/PROGRESS.md"):
        assert (root / ".taskmaster" / rel).exists(), rel

    receipt = re.compile(r"\[seq \d+\]")
    for answer in (bs.backlog_area_create(area_id="core", name="Core"),
                   bs.backlog_add_epic(epic_id="first", name="First", done_when="shipped", area="core"),
                   bs.backlog_add_phase(phase_id="dev", name="Development"),
                   bs.backlog_add_task(title="Do it", epic="first", phase="dev", tldr="done when done",
                                       options={"area": "core"})):
        assert receipt.search(answer), answer
    assert "first-001" in bs.backlog_get_task(task_id="first-001")
    status = bs.backlog_status()
    assert "First" in status and "Development" in status
    assert "Sync complete" in bs.backlog_sync()
    assert is_native(root)
    assert (root / ".taskmaster" / "tasks" / "first-001.md").exists()
    assert (root / ".taskmaster" / "areas" / "core.md").exists()
    assert "Exporter lease" in bs.backlog_store_status()
    # A second init answers through the native refusal and changes nothing.
    assert "already initialized" in bs.backlog_init(project_name="again")


def test_legacy_store_opt_out_keeps_the_legacy_path(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)
    result = bs.backlog_init(project_name="old", legacy_store=True)
    assert "on the legacy store" in result and "Note:" not in result
    assert not is_native(root)
    assert "Created epic" in bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    assert not is_native(root)


def test_legacy_schema_init_never_builds_a_native_store(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)
    result = bs.backlog_init(project_name="v3", schema_version=3)
    assert "on the legacy store" in result
    assert not _database(root).exists()


def test_runtime_without_native_support_falls_back_to_legacy(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)
    monkeypatch.setattr(fresh, "runtime_refusal", lambda: "SQLite lacks FTS5")
    result = bs.backlog_init(project_name="p")
    assert "on the legacy store" in result and "SQLite lacks FTS5" in result
    assert "Created epic" in bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    assert not is_native(root)


def test_failed_activation_leaves_a_working_legacy_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)

    def broken(*args, **kwargs):
        raise RuntimeError("simulated activation failure")
    monkeypatch.setattr(cutover, "activate", broken)
    result = bs.backlog_init(project_name="p")
    assert "on the legacy store" in result and "simulated activation failure" in result
    store.reset_for_tests()
    point_server_at(monkeypatch, root)
    assert "Created epic" in bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    assert not is_native(root)


def test_linked_worktree_resolution_refuses_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    root.mkdir()
    point_server_at(monkeypatch, root)
    main = tmp_path / "main"
    resolution = store.RootResolution(root=main, backlog_path=main / ".taskmaster", source="git-common-dir",
                                      filesystem_warning=None)
    monkeypatch.setattr(store, "resolve_location", lambda *a, **k: resolution)
    result = bs.backlog_init(project_name="p")
    assert result.startswith("Error:") and "linked worktree" in result
    assert not (root / ".taskmaster").exists()


def test_init_on_existing_legacy_project_changes_nothing(tmp_path, monkeypatch):
    root = scaffold(tmp_path / "legacy")
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    before, files = _digest(root), _files(root)
    point_server_at(monkeypatch, root)
    result = bs.backlog_init(project_name="again")
    assert result.startswith("Already initialized"), result
    assert _digest(root) == before and _files(root) == files
    assert not is_native(root)


def test_init_on_existing_native_project_changes_nothing(tmp_path, monkeypatch):
    root = scaffold(tmp_path / "native")
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    store.reset_for_tests()
    activate_native(root)
    before, files = _digest(root), _files(root)
    point_server_at(monkeypatch, root)
    result = bs.backlog_init(project_name="again")
    assert "already initialized" in result and "Nothing was changed" in result
    assert _digest(root) == before and _files(root) == files


@pytest.mark.parametrize("layout", [".claude/backlog.yaml", "backlog.yaml", ".taskmaster/backlog.yaml"])
def test_init_on_legacy_layouts_still_points_to_adoption(tmp_path, monkeypatch, layout):
    root = tmp_path / "proj"
    (root / layout).parent.mkdir(parents=True, exist_ok=True)
    (root / layout).write_text("meta:\n  project: old\nepics: []\nphases: []\n", encoding="utf-8")
    point_server_at(monkeypatch, root)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    result = bs.backlog_init(project_name="again")
    assert result.startswith("Already initialized") and layout.split("/")[-1] in result, result
    if layout.startswith(".claude"):
        assert "backlog_canonicalize_layout" in result
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before
    assert not _database(root).exists()


def test_orphaned_store_without_backlog_is_left_alone(tmp_path, monkeypatch):
    root = scaffold(tmp_path / "orphan")
    point_server_at(monkeypatch, root)
    bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    store.reset_for_tests()
    (root / ".taskmaster" / "backlog.yaml").unlink()
    before = _digest(root)
    point_server_at(monkeypatch, root)
    result = bs.backlog_init(project_name="again")
    assert result.startswith("Already initialized") and "backlog_store_status" in result
    assert not (root / ".taskmaster" / "backlog.yaml").exists()
    assert _digest(root) == before and not is_native(root)
