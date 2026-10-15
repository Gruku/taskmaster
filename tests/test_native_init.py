"""User intent: a project created by `backlog_init` is born on the native store and every later
call is served natively, while init on anything that already exists (a legacy store, a native
store, a legacy layout, an orphaned store) behaves exactly as before and writes nothing.
"""
from __future__ import annotations

from contextlib import closing
import json
import re
import sqlite3
import subprocess
import threading

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native import cutover, fresh
from native_twins import activate_native, is_native, point_server_at, scaffold


@pytest.fixture(autouse=True)
def coordinator(monkeypatch):
    """Native writes (and init's warm-up) go to an in-process coordinator, as the routed-tool
    tests run them, so no test leaves a detached service process behind."""
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


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "protocol.file.allow=always",
                    *args], cwd=cwd, check=True, capture_output=True)


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "initial")
    return path


@pytest.mark.parametrize("legacy", [False, True])
def test_real_linked_worktree_refuses_before_writing(tmp_path, monkeypatch, legacy):
    main = _repo(tmp_path / "main")
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(worktree))
    point_server_at(monkeypatch, worktree)
    result = bs.backlog_init(project_name="p", legacy_store=legacy)
    assert result.startswith("Error:") and "linked Git worktree" in result and str(main.name) in result, result
    assert not (worktree / ".taskmaster").exists() and not (main / ".taskmaster").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_real_submodule_refuses_before_writing(tmp_path, monkeypatch, legacy):
    _repo(tmp_path / "sub")
    super_ = _repo(tmp_path / "super")
    _git(super_, "submodule", "add", "-q", str(tmp_path / "sub"), "mod")
    module = super_ / "mod"
    before = sorted(p.as_posix() for p in (super_ / ".git").rglob("*"))
    point_server_at(monkeypatch, module)
    result = bs.backlog_init(project_name="p", legacy_store=legacy)
    assert result.startswith("Error:") and "submodule" in result and "linked" not in result, result
    assert not (module / ".taskmaster").exists()
    assert sorted(p.as_posix() for p in (super_ / ".git").rglob("*")) == before


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


def _fail_once(monkeypatch, module, name):
    real = getattr(module, name)

    def failing(*args, **kwargs):
        monkeypatch.setattr(module, name, real)
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(module, name, failing)


@pytest.mark.parametrize("point", ["cache-write", "backfill", "activate"])
def test_rerun_finishes_an_interrupted_init(tmp_path, monkeypatch, point):
    root = tmp_path / "proj"
    root.mkdir()
    point_server_at(monkeypatch, root)
    from taskmaster.native import migrate
    # Each point leaves a different store behind: none, a schema-1 store with verified staging,
    # and a schema-1 store whose activation transaction rolled back.
    _fail_once(monkeypatch, *{"cache-write": (bs, "_write_local_meta_cache"), "backfill": (migrate, "backfill"),
                              "activate": (cutover, "activate")}[point])
    first = bs.backlog_init(project_name="p")
    assert "on the legacy store" in first and "Run `backlog_init` again" in first, first
    assert not _database(root).exists() or not is_native(root)
    store.reset_for_tests()
    point_server_at(monkeypatch, root)
    second = bs.backlog_init(project_name="p")
    assert second.startswith("Finished initializing") and "on the native store" in second, second
    assert is_native(root)
    assert "[seq" in bs.backlog_area_create(area_id="core", name="Core")


def test_rerun_never_converts_an_opted_out_or_used_store(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    point_server_at(monkeypatch, root)
    bs.backlog_init(project_name="p", legacy_store=True)
    assert json.loads((root / ".taskmaster" / "taskmaster.json").read_text(encoding="utf-8"))["store"] == "legacy"
    store.reset_for_tests()
    point_server_at(monkeypatch, root)
    assert bs.backlog_init(project_name="p").startswith("Already initialized")
    assert not is_native(root)

    used = tmp_path / "used"
    used.mkdir()
    point_server_at(monkeypatch, used)
    _fail_once(monkeypatch, cutover, "activate")
    bs.backlog_init(project_name="p")
    bs.backlog_add_epic(epic_id="e", name="E", done_when="x")
    before = _digest(used)
    point_server_at(monkeypatch, used)
    answer = bs.backlog_init(project_name="p")
    assert "has been used since" in answer and "cutover" in answer, answer
    assert _digest(used) == before and not is_native(used)


def test_held_files_keep_a_store_from_counting_as_fresh(tmp_path, monkeypatch):
    root = tmp_path / "held"
    root.mkdir()
    point_server_at(monkeypatch, root)
    bs.backlog_init(project_name="p", legacy_store=True)
    store.reset_for_tests()
    assert fresh.state(_database(root)) == "fresh"
    with closing(sqlite3.connect(_database(root))) as connection:
        connection.execute("UPDATE projection SET quarantined=1 WHERE file='backlog.yaml'")
        connection.commit()
    assert fresh.state(_database(root)) == "used"


def test_escape_hatch_after_a_failed_warm_up(tmp_path, monkeypatch):
    from tests.native_coordinator_helpers import close_owned
    root = tmp_path / "hatch"
    root.mkdir()
    point_server_at(monkeypatch, root)
    monkeypatch.setattr(fresh, "warm_up", lambda *a, **k: "coordinator startup unavailable")
    result = bs.backlog_init(project_name="p")
    assert "on the native store" in result and "coordinator did not start" in result
    assert "backlog_init(legacy_store=True)" in result and "store.db" in result
    close_owned()
    store.reset_for_tests()
    for suffix in ("", "-wal", "-shm"):
        (root / ".taskmaster" / "local" / f"store.db{suffix}").unlink(missing_ok=True)
    point_server_at(monkeypatch, root)
    answer = bs.backlog_init(project_name="p", legacy_store=True)
    assert answer.startswith("Finished initializing") and "legacy store" in answer, answer
    assert not is_native(root)
    assert json.loads((root / ".taskmaster" / "taskmaster.json").read_text(encoding="utf-8"))["store"] == "legacy"
    assert "Created epic" in bs.backlog_add_epic(epic_id="e", name="E", done_when="x")


def test_a_failure_after_activation_still_reports_native(tmp_path, monkeypatch):
    root = tmp_path / "late"
    root.mkdir()
    point_server_at(monkeypatch, root)
    real = fresh.activate_fresh

    def late(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("failed after commit")
    monkeypatch.setattr(fresh, "activate_fresh", late)
    result = bs.backlog_init(project_name="p")
    assert "on the native store" in result and "legacy" not in result, result
    assert is_native(root)


# The loser's exclusive `open('x')` of backlog.yaml fails with FileExistsError and writes
# nothing, but the bypass guard judges the attempt before the open and would refuse it.
@pytest.mark.allow_projection_bypass
def test_concurrent_inits_write_once(tmp_path, monkeypatch):
    root = tmp_path / "race"
    root.mkdir()
    point_server_at(monkeypatch, root)
    gate = threading.Barrier(2, timeout=30)
    real = fresh.runtime_refusal

    def both_past_the_checks():
        gate.wait()
        return real()
    monkeypatch.setattr(fresh, "runtime_refusal", both_past_the_checks)
    answers = []

    def init(name):
        try:
            answers.append(bs.backlog_init(project_name=name))
        except Exception as exc:  # noqa: BLE001 -- surfaced in the assertion
            answers.append(f"raised {exc!r}")
    threads = [threading.Thread(target=init, args=(name,)) for name in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert len(answers) == 2, answers
    winners = [a for a in answers if a.startswith("Initialized")]
    losers = [a for a in answers if "another init created" in a]
    assert len(winners) == 1 and len(losers) == 1, answers
    assert "on the native store" in winners[0]
    winner = "alpha" if "**alpha**" in winners[0] else "beta"
    assert f"project: {winner}" in (root / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8")
    assert is_native(root)
