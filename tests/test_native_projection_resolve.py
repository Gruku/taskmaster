"""User intent: a native store that flags a hand-edited file (N11, D2) must be able to
clear the flag with its own tools (S10, D3): `backlog_resolve_conflict` lists and
compares flagged files, `take="store"` re-exports the store's version and records the
replaced file text, and `take="file"` refuses until the native importer (N13) exists.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins, native_connection

REL = "tasks/test-epic-001.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded notes")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _path(twins, rel=REL):
    return twins.native / ".taskmaster" / rel


def _flag_by_hand_edit(twins):
    path = _path(twins)
    edited = path.read_bytes() + b"\nHand edit the user made.\n"
    path.write_bytes(edited)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
    assert f"export pending: {REL} is flagged" in answer
    return edited


def test_list_and_compare_a_native_flag(twins):
    edited = _flag_by_hand_edit(twins)
    with twins.at(twins.native):
        listing = bs.backlog_resolve_conflict()
        detail = bs.backlog_resolve_conflict(file=REL)
    assert listing.startswith("1 flagged file(s):") and f"- {REL} (task test-epic-001, flagged " in listing
    assert "### File on disk now" in detail and "Hand edit the user made." in detail
    store_version = detail.split('### Store version (what take="store" writes)', 1)[1]
    assert "store side" in store_version and "Hand edit" not in store_version
    assert f'backlog_resolve_conflict(file="{REL}", take="file")' in detail
    assert _path(twins).read_bytes() == edited


def test_take_store_re_exports_the_store_version_and_records_the_replaced_text(twins):
    edited = _flag_by_hand_edit(twins)
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
        assert bs.backlog_resolve_conflict() == "No flagged files."
    assert answer.startswith(f"Resolved {REL}: kept the store version."), answer
    on_disk = _path(twins).read_bytes()
    assert b"store side" in on_disk and b"Hand edit" not in on_disk
    with native_connection(twins.native) as connection:
        before, after = connection.execute(
            "SELECT before,after FROM domain_events WHERE op='resolve' ORDER BY seq DESC LIMIT 1").fetchone()
    assert json.loads(before)["file"] == edited.decode("utf-8")
    assert json.loads(after) == {"file": REL, "took": "store"}
    with twins.at(twins.native):
        later = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="after resolve")
    assert "export pending" not in later
    assert b"after resolve" in _path(twins).read_bytes()


def test_take_file_refuses_and_points_to_the_importer(twins):
    edited = _flag_by_hand_edit(twins)
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="file")
        still = bs.backlog_resolve_conflict()
    assert answer.startswith("Error: ") and "N13" in answer and answer.endswith("Nothing was changed."), answer
    assert 'take="store"' in answer
    assert REL in still
    assert _path(twins).read_bytes() == edited


def test_resolving_a_file_that_is_not_flagged_is_an_error(twins):
    with twins.at(twins.native):
        assert bs.backlog_resolve_conflict(file=REL, take="store") == \
            f"Error: {REL} is not flagged; there is nothing to resolve"
        assert bs.backlog_resolve_conflict(file=REL) == f"Error: {REL} is not flagged."
        assert bs.backlog_resolve_conflict(file=REL, take="both") == \
            "Error: take must be 'file' or 'store', not 'both'"


def test_take_store_clears_a_flag_inherited_from_the_legacy_store(twins):
    path = _path(twins)
    hand_edit = path.read_bytes() + b"\nFlagged before activation.\n"
    path.write_bytes(hand_edit)
    with native_connection(twins.native) as connection:
        connection.execute("INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) "
                           "VALUES(?,?,?,'2026-09-21T00:00:00Z','x',?)", (REL, "task", "test-epic-001", hand_edit))
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
    assert answer.startswith(f"Resolved {REL}: kept the store version."), answer
    on_disk = path.read_bytes()
    assert b"Flagged before activation." not in on_disk and b"seeded notes" in on_disk


def test_take_store_on_backlog_yaml_takes_the_whole_file(twins):
    path = _path(twins, "backlog.yaml")
    path.write_bytes(path.read_bytes() + b"# a hand edit of the index\n")
    with twins.at(twins.native):
        flagged = bs.backlog_update_epic(epic_id="test-epic", field="name", value="Renamed")
        assert "export pending: backlog.yaml is flagged" in flagged
        answer = bs.backlog_resolve_conflict(file="backlog.yaml", take="store")
    assert "every epic and phase entry in backlog.yaml now comes from the store version" in answer
    on_disk = path.read_bytes()
    assert b"# a hand edit of the index" not in on_disk and b"Renamed" in on_disk
