"""User intent: the five defects filed alongside B-082 (B-083..B-087) must each
have a test that actually reproduces them — real contention for the lock bugs,
real counting for the waste bugs — so the store's wait, export and read paths
stay honest about what they skip, retry and copy.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from taskmaster import store as store_mod
from taskmaster.taskmaster_v3 import render_frontmatter


def _build_projection(tmp_path: Path, tasks: int = 3) -> Path:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tm_dir / "backlog.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "cluster-tests", "schema_version": 4},
                "epics": [
                    {
                        "id": "core",
                        "name": "Core",
                        "status": "in-progress",
                        "phase": "build",
                    }
                ],
                "phases": [{"id": "build", "name": "Build", "status": "in-progress"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for index in range(1, tasks + 1):
        (tm_dir / "tasks" / f"core-{index:03d}.md").write_text(
            render_frontmatter(
                {
                    "id": f"core-{index:03d}",
                    "title": f"Task {index}",
                    "status": "todo",
                    "epic": "core",
                    "order": float(index),
                },
                "## Notes\n\nIntact.",
            ),
            encoding="utf-8",
        )
    return tm_dir


@contextmanager
def _lock_held_elsewhere(backlog_path: Path):
    """Hold the cross-process writer mutex from another thread's own Store."""
    holder = store_mod.open_store(backlog_path=backlog_path)
    entered = threading.Event()
    release = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            with holder._writer_mutex():
                entered.set()
                release.wait(30)
        except BaseException as exc:  # pragma: no cover - surfaced below
            failure.append(exc)
            entered.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert entered.wait(10), "lock holder never started"
    if failure:
        raise failure[0]
    try:
        yield
    finally:
        release.set()
        thread.join(10)


# ── B-086: a read-side scan skipped because the store is busy ────────────────


def test_busy_read_scan_does_not_burn_the_throttle_and_is_counted(tmp_path):
    backlog_path = _build_projection(tmp_path)
    reader = store_mod.open_store(backlog_path=backlog_path)
    reader.load_dict()

    # A hand edit nobody has adopted yet: every read from here has real work.
    (backlog_path / "tasks" / "core-001.md").write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Edited by hand",
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            "## Notes\n\nHand edit.",
        ),
        encoding="utf-8",
    )
    reader._last_read_scan_clock = None

    with _lock_held_elsewhere(backlog_path):
        reader.load_dict()
        assert reader.read_scan_skips == 1
        # Back-to-back, inside the 2 s throttle window: a skipped attempt must
        # not consume the window, or a continuously busy store never adopts.
        reader.load_dict()
        assert reader.read_scan_skips == 2

    reader.load_dict()
    assert reader.read_scan_skips == 0
    titles = {
        task["id"]: task["title"]
        for epic in reader.load_dict()["epics"]
        for task in epic.get("tasks", [])
    }
    assert titles["core-001"] == "Edited by hand"


def test_repeated_busy_read_scans_are_reported_by_store_status(tmp_path):
    backlog_path = _build_projection(tmp_path)
    reader = store_mod.open_store(backlog_path=backlog_path)
    reader.load_dict()
    (backlog_path / "tasks" / "core-002.md").write_text(
        render_frontmatter(
            {
                "id": "core-002",
                "title": "Also edited",
                "status": "todo",
                "epic": "core",
                "order": 2.0,
            },
            "## Notes\n\nHand edit.",
        ),
        encoding="utf-8",
    )
    reader._last_read_scan_clock = None

    with _lock_held_elsewhere(backlog_path):
        with pytest.warns(RuntimeWarning, match="hand edits"):
            for _ in range(store_mod.READ_SCAN_SKIP_WARN_AFTER):
                reader.load_dict()
        status = store_mod.read_only_status(backlog_path)
        assert status.read_scan_skips >= store_mod.READ_SCAN_SKIP_WARN_AFTER


# ── B-085: an export that can never land while its target is quarantined ─────


@contextmanager
def _count_renders(monkeypatch):
    """Count `_export_entity_row` calls per (kind, id) across transactions."""
    counted: list[tuple[str, str]] = []
    original = store_mod.Store._export_entity_row

    def spy(self, tx, row):
        counted.append((row["kind"], row["id"]))
        return original(self, tx, row)

    monkeypatch.setattr(store_mod.Store, "_export_entity_row", spy)
    yield counted


def _quarantine_task_file(backlog_path: Path, task_id: str) -> None:
    (backlog_path / "tasks" / f"{task_id}.md").write_text(
        "---\n: : not yaml [\n---\nbroken\n", encoding="utf-8"
    )


def _retitle(store_obj, task_id: str, title: str) -> None:
    with store_obj.transaction(tool="test-write") as tx:
        tx.put(
            "task",
            task_id,
            {
                "id": task_id,
                "title": title,
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            body="## Notes\n\nStore side.",
        )


def test_writes_to_a_quarantined_entity_render_once_not_once_per_write(
    tmp_path, monkeypatch
):
    backlog_path = _build_projection(tmp_path)
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    _quarantine_task_file(backlog_path, "core-001")
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()

    log_path = backlog_path / "local" / "store.log"
    with _count_renders(monkeypatch) as rendered:
        for index in range(5):
            _retitle(store_obj, "core-001", f"Store title {index}")
            store_obj._last_read_scan_clock = None

    # The target cannot accept any of these renders and has not changed between
    # them, so rendering the document five times produces five identical
    # suppressions and nothing else.
    assert rendered.count(("task", "core-001")) == 1
    suppressed = [
        line for line in log_path.read_text(encoding="utf-8").splitlines()
        if "export suppressed" in line
    ]
    assert len(suppressed) == 1


def test_a_stuck_export_is_named_by_store_status(tmp_path):
    backlog_path = _build_projection(tmp_path)
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    _quarantine_task_file(backlog_path, "core-001")
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()
    _retitle(store_obj, "core-001", "Store title")

    status = store_mod.read_only_status(backlog_path)
    assert status.stuck_exports == ("tasks/core-001.md",)
    from backlog_server import _render_store_report

    assert "tasks/core-001.md" in _render_store_report(status).split("Stuck exports")[1]


def test_a_repaired_file_lets_the_stranded_export_land(tmp_path):
    backlog_path = _build_projection(tmp_path)
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    _quarantine_task_file(backlog_path, "core-001")
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()
    _retitle(store_obj, "core-001", "Survives the quarantine")
    assert store_mod.read_only_status(backlog_path).stuck_exports

    # Repairing the file by hand clears the quarantine; the export the store
    # has been holding has to land rather than be overwritten by the repair.
    (backlog_path / "tasks" / "core-001.md").write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Repaired by hand",
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            "## Notes\n\nRepaired.",
        ),
        encoding="utf-8",
    )
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()

    assert store_mod.read_only_status(backlog_path).stuck_exports == ()
    on_disk = (backlog_path / "tasks" / "core-001.md").read_text(encoding="utf-8")
    assert "Survives the quarantine" in on_disk
