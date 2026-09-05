"""User intent: the projection scan and the legacy imports must settle. A first
import has to produce `IDEAS.md`, an unchanged project must stop
rescanning itself forever, and a Linear push that legacy state had parked for
good must not silently come back as pending.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _write_project(tmp_path: Path) -> Path:
    tm_dir = tmp_path / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    (tm_dir / "ideas").mkdir(parents=True)
    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "scan-tests", "schema_version": 4},
                "epics": [{"id": "e", "name": "Epic E"}],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return backlog_path


@pytest.fixture()
def scan_store(tmp_path: Path) -> Iterator[tuple[Any, Path]]:
    store.reset_for_tests()
    backlog_path = _write_project(tmp_path)
    opened = store.open_store(backlog_path, session="scan-test-session")
    try:
        yield opened, backlog_path
    finally:
        store.reset_for_tests()


def _write_idea_file(backlog_path: Path, ident: str, title: str) -> Path:
    path = backlog_path.parent / "ideas" / f"{ident}.md"
    v3.write_task_file(path, {"id": ident, "title": title, "status": "exploring"}, "why")
    return path


# -- C12: an imported idea edit regenerates the derived index ----------------


def test_a_first_import_of_ideas_creates_the_derived_index(
    scan_store: tuple[Any, Path],
) -> None:
    """`IDEAS.md` is derived output and nothing but the exporter writes it, so
    on a first v4 import -- where the ideas arrive through the import path and
    never through `create`/`put` -- the index would otherwise never exist."""
    opened, backlog_path = scan_store
    _write_idea_file(backlog_path, "IDEA-001", "Original title")
    with opened.transaction(tool="first-scan"):
        pass

    index = backlog_path.parent / "ideas" / "IDEAS.md"
    assert index.exists(), "the first import left the derived index absent"
    assert "Original title" in index.read_text(encoding="utf-8")


def test_a_later_idea_write_picks_up_a_hand_edit(
    scan_store: tuple[Any, Path],
) -> None:
    """Known gap, pinned so it cannot get worse: a hand edit to an existing
    idea does not refresh the index on its own -- regenerating on every import
    made eight concurrent writers rewrite the shared file under the writer lock
    and starved it -- but the next idea write does pick the edit up."""
    opened, backlog_path = scan_store
    _write_idea_file(backlog_path, "IDEA-001", "Original title")
    with opened.transaction(tool="first-scan"):
        pass
    index = backlog_path.parent / "ideas" / "IDEAS.md"

    _write_idea_file(backlog_path, "IDEA-001", "Edited by hand")
    with opened.transaction(tool="rescan"):
        pass
    with opened.transaction(tool="another-idea") as tx:
        tx.create("idea", {"id": "IDEA-002", "title": "Second", "status": "exploring"})

    text = index.read_text(encoding="utf-8")
    assert "Edited by hand" in text, text
    assert "Second" in text, text


# -- C13: an unchanged project stops rescanning ------------------------------


def test_an_exported_ideas_index_does_not_force_a_scan_forever(
    scan_store: tuple[Any, Path],
) -> None:
    """`IDEAS.md` has a projection row but is not an entity file, so leaving it
    out of the change-detection inventory made the known/actual sets differ on
    every read — a writer transaction and a full scan every two seconds."""
    opened, backlog_path = scan_store
    _write_idea_file(backlog_path, "IDEA-001", "Settled")
    with opened.transaction(tool="first-scan"):
        pass
    assert (backlog_path.parent / "ideas" / "IDEAS.md").exists()

    assert opened._projection_changed_on_disk() is False


# -- C11: legacy parked pushes stay parked -----------------------------------


def test_legacy_permanently_failed_push_imports_as_failed_not_pending(
    scan_store: tuple[Any, Path],
) -> None:
    """A push the legacy queue had given up on carries `permanent: true`.
    Importing it as pending hands it straight back to the next drain and hides
    it from the parked count in `backlog_store_status`."""
    opened, backlog_path = scan_store
    queue = backlog_path.parent / "integrations" / "linear-queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        json.dumps(
            [
                {
                    "op": "push",
                    "target_id": "e-001",
                    "attempts": 5,
                    "last_error": "401 unauthorized",
                    "permanent": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    with opened.transaction(tool="import-legacy-queue"):
        pass

    assert opened.linear_pending(10) == []
    rows = opened.linear_rows()
    assert [row["state"] for row in rows] == ["failed"]
    assert rows[0]["attempts"] == 5
    assert rows[0]["last_error"] == "401 unauthorized"
