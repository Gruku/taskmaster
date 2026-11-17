# User intent: comparing or clearing a flagged file never follows a link or junction to bytes outside the backlog (a teammate could otherwise read host secrets through a swapped flagged path), and a large flagged file can still be cleared with take="store" without losing a later edit (team-server-012 D7, re-review R-M1, Q-M1).
from __future__ import annotations

import json
import os

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.native import projection as native_projection
from native_twins import native_connection
from test_native_projection_resolve import REL, _path, twins  # noqa: F401  (fixture)

MARKER = "CONFLICT-MARKER-7"


def _flag(twins, extra=b"\nHand edit the user made.\n"):
    path = _path(twins)
    edited = path.read_bytes() + extra
    path.write_bytes(edited)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
    assert f"export pending: {REL} is flagged" in answer
    return edited


def _flagged(twins):
    with twins.at(twins.native):
        return REL in bs.backlog_resolve_conflict()


def _resolve_events(twins):
    with native_connection(twins.native) as connection:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT before FROM domain_events WHERE op='resolve' ORDER BY seq")]


def _no_marker_anywhere(twins, *answers):
    assert all(MARKER not in answer for answer in answers), answers
    assert all(MARKER not in json.dumps(before) for before in _resolve_events(twins))


def _outside_dir(tmp_path):
    outside = tmp_path / "outside-tasks"
    outside.mkdir()
    (outside / "test-epic-001.md").write_text(f"---\nid: test-epic-001\n---\n{MARKER}\n", encoding="utf-8")
    return outside


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows reparse points; POSIX is covered by symlinks")
@pytest.mark.parametrize("principal", [None, "a" * 16])
def test_a_junction_on_the_path_is_never_followed(twins, tmp_path, monkeypatch, principal):
    import _winapi
    _flag(twins)
    if principal is not None:
        monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: principal)
    tasks = _path(twins).parent
    real = tasks.with_name("tasks-real")
    tasks.rename(real)
    _winapi.CreateJunction(str(_outside_dir(tmp_path)), str(tasks))
    try:
        with twins.at(twins.native):
            detail = bs.backlog_resolve_conflict(file=REL)
            taken = bs.backlog_resolve_conflict(file=REL, take="store")
        assert "the file can't be read safely: it is a link or junction; compare it on the host" in detail
        assert taken.startswith(f"Error: {REL} can't be read safely (it is a link or junction)"), taken
        _no_marker_anywhere(twins, detail, taken)
    finally:
        os.rmdir(tasks)                                  # removes the junction only, never its target
        real.rename(tasks)
    assert _flagged(twins)


def test_a_directory_at_the_flagged_path_is_refused(twins):
    _flag(twins)
    path = _path(twins)
    path.unlink()
    path.mkdir()
    try:
        with twins.at(twins.native):
            detail = bs.backlog_resolve_conflict(file=REL)
            taken = bs.backlog_resolve_conflict(file=REL, take="store")
        assert "it is not a regular file; compare it on the host" in detail
        assert taken.startswith(f"Error: {REL} can't be read safely (it is not a regular file)"), taken
    finally:
        path.rmdir()
    assert _flagged(twins)


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")


@pytest.mark.parametrize("where", ["outside", "inside"])
def test_a_symlinked_flagged_file_is_refused(twins, tmp_path, where):
    _flag(twins)
    path = _path(twins)
    if where == "outside":
        target = _outside_dir(tmp_path) / "test-epic-001.md"
    else:                                            # a link from one .taskmaster/ file to another
        target = path.with_name("other.md")
        target.write_text(f"{MARKER}\n", encoding="utf-8")
    saved = path.read_bytes()
    path.unlink()
    _symlink(target, path)
    try:
        with twins.at(twins.native):
            detail = bs.backlog_resolve_conflict(file=REL)
            taken = bs.backlog_resolve_conflict(file=REL, take="store")
        assert "it is a link or junction" in detail and taken.startswith("Error:")
        _no_marker_anywhere(twins, detail, taken)
    finally:
        path.unlink()
        path.write_bytes(saved)
    assert _flagged(twins)


def test_a_file_over_the_projection_limit_is_refused(twins, monkeypatch):
    from taskmaster.coordinator import git
    _flag(twins)
    monkeypatch.setattr(git, "MAX_PROJECTION_BYTES", 64)
    with twins.at(twins.native):
        detail = bs.backlog_resolve_conflict(file=REL)
    assert "projection limit; compare it on the host" in detail


def test_a_large_flagged_file_clears_by_hash(twins):
    big = b"\n" + b"x" * (2 * 1024 * 1024) + b"\n"
    edited = _flag(twins, big)
    with twins.at(twins.native):
        assert "x" * 100 in bs.backlog_resolve_conflict(file=REL)       # the compare still shows it
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
        assert bs.backlog_resolve_conflict() == "No flagged files."
    assert answer.startswith(f"Resolved {REL}: kept the store version.") and "too large to inline" in answer, answer
    import base64
    before = _resolve_events(twins)[-1]
    # The replaced bytes are the flagged copy, kept once in flagged_base64 (012 gate M2).
    assert before["file_inlined"] is False and before["file"] is None and before["file_base64"] is None
    assert base64.b64decode(before["flagged_base64"]) == edited and before["file_missing"] is False
    assert b"store side" in _path(twins).read_bytes() and b"x" * 100 not in _path(twins).read_bytes()


def test_a_file_edited_after_flagging_still_resolves_inline_while_it_fits(twins):
    """012 gate M1: between 640 KiB and the envelope's real headroom, changed bytes are inlined, not refused."""
    _flag(twins, b"\n" + b"x" * (650 * 1024) + b"\n")
    _path(twins).write_bytes(_path(twins).read_bytes() + b"\nA later edit worth keeping.\n")
    later = _path(twins).read_bytes()
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
    assert answer.startswith(f"Resolved {REL}: kept the store version.") and "inline" not in answer, answer
    assert _resolve_events(twins)[-1]["file"] == later.decode("utf-8")


def test_a_flagged_ideas_index_compares_and_clears(twins):
    """012 gate I1 (the reviewer's probe): a derived file the exporter flags is read like any other."""
    with twins.at(twins.native):
        bs.backlog_idea_create(title="First idea")
    path = _path(twins, "ideas/IDEAS.md")
    path.write_bytes(path.read_bytes() + b"\nhand edit of the ideas index\n")
    with twins.at(twins.native):
        bs.backlog_idea_create(title="Second idea")
        assert "ideas/IDEAS.md" in bs.backlog_resolve_conflict()
        detail = bs.backlog_resolve_conflict(file="ideas/IDEAS.md")
        taken = bs.backlog_resolve_conflict(file="ideas/IDEAS.md", take="store")
    assert "hand edit of the ideas index" in detail and "can't be read safely" not in detail, detail
    assert taken.startswith("Resolved ideas/IDEAS.md"), taken


def test_a_large_file_edited_after_flagging_is_refused(twins):
    _flag(twins, b"\n" + b"x" * (2 * 1024 * 1024) + b"\n")
    _path(twins).write_bytes(_path(twins).read_bytes() + b"\nA later edit worth keeping.\n")
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
    assert answer.startswith(f"Error: {REL} changed after it was flagged"), answer
    assert b"A later edit worth keeping." in _path(twins).read_bytes()
    assert _flagged(twins)


def test_a_mid_sized_file_still_resolves_inline(twins):
    edited = _flag(twins, b"\n" + b"y" * (600 * 1024) + b"\n")
    with twins.at(twins.native):
        answer = bs.backlog_resolve_conflict(file=REL, take="store")
    assert answer.startswith(f"Resolved {REL}: kept the store version.") and "inline" not in answer, answer
    before = _resolve_events(twins)[-1]
    assert "file_inlined" not in before and before["file"] == edited.decode("utf-8")


@pytest.mark.parametrize("arguments", [
    {"file": REL, "take": "store", "replaced_hash": "a" * 40},
    {"file": REL, "take": "store", "replaced_hash": "a" * 40, "replaced_omitted": False},
    {"file": REL, "take": "store", "replaced_base64": "eA==", "replaced_hash": "a" * 40, "replaced_omitted": True},
])
def test_hash_only_needs_the_explicit_marker(arguments):
    with pytest.raises(ValueError):
        native_projection.validate_resolve(arguments)


def test_hash_only_with_the_marker_validates():
    native_projection.validate_resolve({"file": REL, "take": "store", "replaced_hash": "a" * 40,
                                        "replaced_omitted": True})


def test_a_regular_flagged_file_compares_and_resolves_as_before(twins):
    edited = _flag(twins)
    with twins.at(twins.native):
        assert "Hand edit the user made." in bs.backlog_resolve_conflict(file=REL)
        assert bs.backlog_resolve_conflict(file=REL, take="store").startswith(f"Resolved {REL}")
    assert _resolve_events(twins)[-1]["file"] == edited.decode("utf-8")
