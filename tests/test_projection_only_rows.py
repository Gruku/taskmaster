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


# ── the payload and the ETag describe one revision ───────────────────────────


def test_a_file_replaced_mid_read_is_not_served_under_the_new_etag(
    seeded, monkeypatch
):
    """The payload was read first and the identity stated separately, so a file
    replaced between the two came back as old content stamped with the new
    revision's ETag — which a client caches and then passes as its own
    `If-Match` while overwriting a peer."""
    root = seeded
    _go_projection_only(root, monkeypatch)
    opened = store.open_store(root / ".taskmaster" / "backlog.yaml")

    reads = {"n": 0}
    real_rows = opened._entity_rows_from_projection

    def edit_between(*args, **kwargs):
        rows = real_rows(*args, **kwargs)
        reads["n"] += 1
        if reads["n"] == 1:
            # A peer replaces a file exactly while this read is in flight.
            bug = next((root / ".taskmaster" / "bugs").glob("*.md"))
            bug.write_text(
                bug.read_text(encoding="utf-8") + "\nedited by a peer\n",
                encoding="utf-8",
            )
        return rows

    monkeypatch.setattr(opened, "_entity_rows_from_projection", edit_between)

    _data, token, _seq = opened.load_dict_with_identity()

    assert reads["n"] > 1, "the read was not retried after the projection moved"
    settled, _seq = opened._projection_identity()
    assert token == settled, (
        "the payload was stamped with an identity it does not describe"
    )


def test_a_quiet_projection_is_read_once(seeded, monkeypatch):
    """The retry must not cost a second full read when nothing is changing."""
    root = seeded
    _go_projection_only(root, monkeypatch)
    opened = store.open_store(root / ".taskmaster" / "backlog.yaml")

    reads = {"n": 0}
    real_rows = opened._entity_rows_from_projection

    def counted(*args, **kwargs):
        reads["n"] += 1
        return real_rows(*args, **kwargs)

    monkeypatch.setattr(opened, "_entity_rows_from_projection", counted)

    opened.load_dict_with_identity()

    assert reads["n"] == 1, reads
