"""User intent: on network storage the store cannot open a database and falls
back to reading the projection files. The entity readers moved onto the `_rows`
map in step 3, so that fallback has to carry one — otherwise every bug, issue,
handover, decision, idea, note and area on disk simply disappears from the
tools that list them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store


@pytest.fixture()
def seeded(tm_epic_phase):
    """A project holding one entity of each row-backed kind."""
    bs.backlog_bug_create(title="A real bug", found_in="test-epic-001")
    bs.backlog_idea_create(title="A real idea")
    bs.backlog_decision_create(title="A real decision", options=["one", "two"])
    bs.backlog_note(action="create", text="A real note")
    bs.backlog_area_create(area_id="core", name="Core")
    return tm_epic_phase


def _go_projection_only(root: Path, monkeypatch):
    """Network storage with no usable database: the files are all there is."""
    store.reset_for_tests()
    for name in ("store.db", "store.db-wal", "store.db-shm"):
        (root / ".taskmaster" / "local" / name).unlink(missing_ok=True)
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )


def test_projection_only_reads_still_see_the_entities_on_disk(seeded, monkeypatch):
    root = seeded
    _go_projection_only(root, monkeypatch)

    data = store.open_store(root / ".taskmaster" / "backlog.yaml").load_dict()

    rows = data.get("_rows") or {}
    assert rows, "the projection-only fallback returned no row map at all"
    for kind in ("bug", "idea", "decision", "note", "area"):
        assert rows.get(kind), f"no {kind} rows served from the projection"


def test_projection_only_bug_list_is_not_empty(seeded, monkeypatch):
    _go_projection_only(seeded, monkeypatch)

    output = bs.backlog_bug_list()

    assert "A real bug" in output, output
