"""User intent: `backlog_handover_resync` and `backlog_issue_resync` on a native store
pick up hand edits to handover/issue documents by importing exactly those files through
the N13 sync barrier (step 7): a missing file is repaired from the store, never read as
a deletion; an unparseable one is quarantined with its bytes kept; other kinds are left.
"""
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native.queries import Repository
from native_twins import make_twins, native_connection

HANDOVER = "2026-09-17-resync-handover"
H_REL = f"handovers/{HANDOVER}.md"
I_REL = "issues/ISS-001.md"
T_REL = "tasks/test-epic-001.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded notes")
    bs.backlog_handover_create(tldr="Resync handover", task_ids=["test-epic-001"])
    bs.backlog_issue_create(title="Resync issue", severity="P2", evidence="seeded evidence")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed)
    # The copied files were never published by this store: a first resync records
    # their bytes as trusted bases, so a later edit merges instead of flagging.
    with twins.at(twins.native):
        bs.backlog_handover_resync()
        bs.backlog_issue_resync()
    return twins


def _path(twins, rel):
    return twins.native / ".taskmaster" / rel


def _entity(twins, kind, ident):
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        entity = snapshot.get(kind, ident, include_body=True)
    return {"revision": entity["revision"], "fields": entity["fields"], "body": entity["body"]}


def _replace(twins, rel, old, new):
    path = _path(twins, rel)
    content = path.read_bytes()
    assert old.encode() in content
    path.write_bytes(content.replace(old.encode(), new.encode()))
    return path.read_bytes()


def test_handover_resync_imports_a_hand_edited_handover_and_leaves_other_kinds(twins):
    _replace(twins, H_REL, "tldr: Resync handover", "tldr: Edited by hand")
    _replace(twins, T_REL, "title: First", "title: Task edited by hand")
    task = _entity(twins, "task", "test-epic-001")
    with twins.at(twins.native):
        answer = bs.backlog_handover_resync()
    assert answer.startswith("Handover index resynced — 1 entries in `backlog.yaml`."), answer
    assert f"Imported 1 edited file(s): {H_REL}" in answer, answer
    assert _entity(twins, "handover", HANDOVER)["fields"]["tldr"] == "Edited by hand"
    assert _entity(twins, "task", "test-epic-001") == task
    assert b"Task edited by hand" in _path(twins, T_REL).read_bytes()


def test_issue_resync_imports_a_hand_edited_issue(twins):
    _replace(twins, I_REL, "evidence: seeded evidence", "evidence: edited evidence")
    handover = _entity(twins, "handover", HANDOVER)
    with twins.at(twins.native):
        answer = bs.backlog_issue_resync()
    assert answer.startswith("Issue index resynced — 1 entries."), answer
    assert _entity(twins, "issue", "ISS-001")["fields"]["evidence"] == "edited evidence"
    assert _entity(twins, "handover", HANDOVER) == handover


@pytest.mark.parametrize("tool, kind, ident, rel", [
    ("backlog_handover_resync", "handover", HANDOVER, H_REL),
    ("backlog_issue_resync", "issue", "ISS-001", I_REL)])
def test_resync_repairs_a_missing_document_and_never_deletes_its_row(twins, tool, kind, ident, rel):
    before = _entity(twins, kind, ident)
    _path(twins, rel).unlink()
    with twins.at(twins.native):
        answer = getattr(bs, tool)()
    assert f"Repaired 1 missing file(s) from the store: {rel}" in answer, answer
    assert _entity(twins, kind, ident)["fields"] == before["fields"]
    assert _path(twins, rel).exists()


def test_resync_quarantines_an_unparseable_handover_then_takes_the_repaired_file(twins):
    before = _entity(twins, "handover", HANDOVER)
    broken = _replace(twins, H_REL, "tldr: Resync handover", "tldr: [unclosed")
    with twins.at(twins.native):
        answer = bs.backlog_handover_resync()
    assert "Not synchronized" in answer and H_REL in answer and "invalid authored projection" in answer, answer
    assert _entity(twins, "handover", HANDOVER) == before
    assert _path(twins, H_REL).read_bytes() == broken
    with native_connection(twins.native) as connection:
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (H_REL,)).fetchone()[0] == 1
    with twins.at(twins.native):
        again = bs.backlog_handover_resync()
    assert "unchanged quarantined bytes" in again and _path(twins, H_REL).read_bytes() == broken, again
    _replace(twins, H_REL, "tldr: [unclosed", "tldr: Repaired by hand")
    with twins.at(twins.native):
        fixed = bs.backlog_handover_resync()
    assert f"Imported 1 edited file(s): {H_REL}" in fixed, fixed
    assert _entity(twins, "handover", HANDOVER)["fields"]["tldr"] == "Repaired by hand"
    with native_connection(twins.native) as connection:
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (H_REL,)).fetchone()[0] == 0


def test_resync_with_nothing_edited_reports_the_index_and_changes_nothing(twins):
    with native_connection(twins.native) as connection:
        high = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    with twins.at(twins.native):
        answer = bs.backlog_handover_resync()
    assert answer == "Handover index resynced — 1 entries in `backlog.yaml`.", answer
    with native_connection(twins.native) as connection:
        assert connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0] == high


def test_divergent_edit_without_a_trusted_base_is_flagged_and_both_kept(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed)
    before = _entity(twins, "handover", HANDOVER)
    edited = _replace(twins, H_REL, "tldr: Resync handover", "tldr: Edited before any base")
    with twins.at(twins.native):
        answer = bs.backlog_handover_resync()
        detail = bs.backlog_resolve_conflict(file=H_REL)
    assert "Not synchronized" in answer and "no verified prior base" in answer, answer
    assert _entity(twins, "handover", HANDOVER) == before
    assert _path(twins, H_REL).read_bytes() == edited
    assert "Edited before any base" in detail
