"""Notes (sticky) entity — file helper tests."""
import pytest

from taskmaster_v3 import (
    archive_note,
    list_notes,
    next_note_id,
    note_path,
    read_note,
    update_note,
    write_note,
)


@pytest.fixture()
def bp(tmp_path):
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    p = tm / "backlog.yaml"
    p.write_text("meta:\n  project: test\nepics: []\nphases: []\n", encoding="utf-8")
    return p


def test_write_note_allocates_sequential_ids(bp):
    nid1, path1 = write_note(bp, text="first thought", author="user")
    nid2, _ = write_note(bp, text="second thought", author="claude")
    assert nid1 == "NOTE-001"
    assert nid2 == "NOTE-002"
    assert path1 == note_path(bp, "NOTE-001")
    assert path1.exists()


def test_write_note_frontmatter_and_body(bp):
    nid, _ = write_note(bp, text="remember the milk", author="user", pinned=True)
    fm, body = read_note(bp, nid)
    assert fm["id"] == nid
    assert fm["author"] == "user"
    assert fm["pinned"] is True
    assert fm["archived"] is False
    assert fm["archived_at"] is None
    assert fm["created"].endswith("Z")
    assert fm["updated"] == fm["created"]
    assert body == "remember the milk"


def test_write_note_rejects_empty_text(bp):
    with pytest.raises(ValueError):
        write_note(bp, text="   ", author="user")


def test_write_note_rejects_bad_author(bp):
    with pytest.raises(ValueError):
        write_note(bp, text="x", author="robot")


def test_update_note_text_and_pin(bp):
    nid, _ = write_note(bp, text="v1", author="user")
    fm, body = update_note(bp, nid, text="v2", pinned=True)
    assert body == "v2"
    assert fm["pinned"] is True
    assert fm["updated"] >= fm["created"]
    # author is immutable through update
    fm2, _ = read_note(bp, nid)
    assert fm2["author"] == "user"


def test_archive_note_moves_file(bp):
    nid, live_path = write_note(bp, text="done with this", author="claude")
    fm = archive_note(bp, nid)
    assert fm["archived"] is True
    assert fm["archived_at"] is not None
    assert not live_path.exists()
    archived_path = note_path(bp, nid, archived=True)
    assert archived_path.exists()
    # read_note still finds it in the archive
    fm2, body = read_note(bp, nid)
    assert fm2["archived"] is True
    assert body == "done with this"


def test_archive_note_missing_raises(bp):
    with pytest.raises(FileNotFoundError):
        archive_note(bp, "NOTE-999")


def test_next_note_id_skips_archived(bp):
    nid, _ = write_note(bp, text="a", author="user")
    archive_note(bp, nid)
    assert next_note_id(bp) == "NOTE-002"  # archive still counts


def test_list_notes_pinned_first_then_newest(bp):
    n1, _ = write_note(bp, text="oldest", author="user")
    n2, _ = write_note(bp, text="pinned one", author="user", pinned=True)
    n3, _ = write_note(bp, text="newest", author="claude")
    notes = list_notes(bp)
    assert [n["id"] for n in notes] == [n2, n3, n1]
    assert all("body" in n for n in notes)


def test_list_notes_excludes_archived_by_default(bp):
    n1, _ = write_note(bp, text="keep", author="user")
    n2, _ = write_note(bp, text="drop", author="user")
    archive_note(bp, n2)
    assert [n["id"] for n in list_notes(bp)] == [n1]
    assert {n["id"] for n in list_notes(bp, include_archived=True)} == {n1, n2}
