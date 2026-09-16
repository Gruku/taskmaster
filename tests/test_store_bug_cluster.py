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
