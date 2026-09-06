"""User intent: a permanently broken file must not grow `local/store.log`.

The 5.3 real-backlog run left four files quarantined for good (one carrying git
conflict markers, three with unreadable frontmatter). Every warm tool call
re-scanned them, failed to parse them again, and appended the same reason to
`store.log` — an unbounded log on a project that will never repair those files.
A reason belongs in the log when it is new or when it changed, not once per read.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import render_frontmatter


def _write_v4_projection(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "store-tests", "schema_version": 4},
                "context": {"focus": "derived-only"},
                "epics": [
                    {
                        "id": "core",
                        "name": "Store core",
                        "status": "in-progress",
                        "phase": "build",
                    }
                ],
                "phases": [{"id": "build", "name": "Build", "status": "in-progress"}],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    task_path = tm_dir / "tasks" / "core-001.md"
    task_path.write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Alpha",
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            "## Notes\n\nIntact.",
        ),
        encoding="utf-8",
    )
    return backlog_path, task_path


@pytest.fixture()
def store_api():
    from taskmaster import store

    store.reset_for_tests()
    yield store
    store.reset_for_tests()


def _log_lines(store_api, backlog_path: Path, needle: str) -> list[str]:
    log = store_api.db_path(backlog_path).parent / "store.log"
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if needle in line]


def test_a_permanent_quarantine_is_logged_once_not_once_per_read(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine-log")
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")

    for index in range(6):
        with opened.transaction(tool=f"warm-read-{index}"):
            pass

    assert "tasks/core-001.md" in opened.status().quarantined_files
    lines = _log_lines(store_api, backlog_path, "quarantined tasks/core-001.md")
    assert len(lines) == 1, lines


def test_a_changed_quarantine_reason_is_logged_again(tmp_path, store_api):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine-log")

    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="first-break"):
        pass
    with opened.transaction(tool="still-broken"):
        pass
    task_path.write_text(
        "<<<<<<< HEAD\n---\nid: core-001\n---\n=======\nother\n>>>>>>> theirs\n",
        encoding="utf-8",
    )
    with opened.transaction(tool="second-break"):
        pass
    with opened.transaction(tool="still-broken-differently"):
        pass

    lines = _log_lines(store_api, backlog_path, "quarantined tasks/core-001.md")
    assert len(lines) == 2, lines


def test_a_repaired_then_rebroken_file_is_logged_again(tmp_path, store_api):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine-log")
    valid = task_path.read_text(encoding="utf-8")

    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="break-once"):
        pass
    task_path.write_text(valid, encoding="utf-8")
    with opened.transaction(tool="repair"):
        pass
    assert opened.status().quarantined_files == ()
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="break-again"):
        pass

    lines = _log_lines(store_api, backlog_path, "quarantined tasks/core-001.md")
    assert len(lines) == 2, lines


def test_an_unparseable_project_yaml_is_logged_once(tmp_path, store_api):
    """`project.yaml` never gets a projection row when it fails to parse, so it
    took the same re-log path on every scan."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="project-log")
    (backlog_path.parent / "project.yaml").write_text(
        "- not: a\n- mapping\n", encoding="utf-8"
    )

    for index in range(5):
        with opened.transaction(tool=f"warm-read-{index}"):
            pass

    lines = _log_lines(store_api, backlog_path, "quarantined project.yaml")
    assert len(lines) == 1, lines


def test_a_reason_is_not_marked_logged_when_the_log_cannot_be_written(
    tmp_path, store_api
):
    """A signature that outlives the line it stands for silences that reason for
    good. An unwritable `store.log` must leave the reason still owed."""
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="log-failure")
    log = store_api.db_path(backlog_path).parent / "store.log"
    if log.exists():
        log.unlink()
    # A directory at the path makes the append fail the way an unwritable file
    # does, without depending on how the platform enforces permissions.
    log.mkdir()

    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="read-while-log-is-unwritable"):
        pass
    assert "tasks/core-001.md" in opened.status().quarantined_files

    log.rmdir()
    with opened.transaction(tool="read-once-the-log-works"):
        pass

    lines = _log_lines(store_api, backlog_path, "quarantined tasks/core-001.md")
    assert len(lines) == 1, lines
