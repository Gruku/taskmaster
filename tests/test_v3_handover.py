"""Tests for v3 handover write/validate/supersession plumbing."""
import sys
from pathlib import Path

import pytest
import yaml

# Make `import backlog_server` work the same way other tests in this directory do.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

import backlog_server  # noqa: E402

from taskmaster_v3 import (
    HANDOVER_KINDS,
    HANDOVER_KIND_TO_VIEWER_KIND,
    apply_supersession,
    read_handover,
    write_handover,
)


def test_handover_kinds_match_spec():
    # Plan B adds "task-complete" as a 5th canonical kind eligible for smart auto-close.
    assert set(HANDOVER_KINDS) == {"continuity", "deep-context", "milestone", "auto-stage", "task-complete"}
    # Legacy names are not canonical storage kinds.
    assert "end-of-day" not in HANDOVER_KINDS
    assert "context-handoff" not in HANDOVER_KINDS
    assert "milestone-complete" not in HANDOVER_KINDS
    assert "crash-recovery" not in HANDOVER_KINDS


def test_viewer_kind_mapping_covers_all_storage_kinds():
    for kind in HANDOVER_KINDS:
        assert kind in HANDOVER_KIND_TO_VIEWER_KIND, f"missing mapping for {kind}"


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_write_handover_rejects_unknown_session_kind(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match=r"session_kind.*not-a-real-kind"):
        write_handover(bp, tldr="test", session_kind="not-a-real-kind")


def test_write_handover_accepts_each_known_kind(tmp_path):
    bp = _make_backlog(tmp_path)
    for kind in HANDOVER_KINDS:
        hid, path = write_handover(bp, tldr=f"test {kind}", session_kind=kind)
        assert isinstance(hid, str) and hid
        assert path.exists()


def test_write_handover_records_supersedes_in_frontmatter(tmp_path):
    bp = _make_backlog(tmp_path)
    old_id, _ = write_handover(bp, tldr="old work", session_kind="milestone-complete")
    new_id, _ = write_handover(
        bp, tldr="newer work",
        session_kind="milestone-complete",
        supersedes=old_id,
    )
    fm, _ = read_handover(bp, new_id)
    assert fm["supersedes"] == old_id
    assert fm.get("superseded_by") is None


def test_write_handover_records_branch_and_tip_commit(tmp_path):
    bp = _make_backlog(tmp_path)
    hid, _ = write_handover(
        bp, tldr="branch test",
        branch="feature/taskmaster-v3",
        tip_commit="abc1234",
    )
    fm, _ = read_handover(bp, hid)
    assert fm["branch"] == "feature/taskmaster-v3"
    assert fm["tip_commit"] == "abc1234"


def test_write_handover_omits_optional_fields_when_unset(tmp_path):
    bp = _make_backlog(tmp_path)
    hid, _ = write_handover(bp, tldr="minimal")
    fm, _ = read_handover(bp, hid)
    assert "branch" not in fm
    assert "tip_commit" not in fm
    assert "supersedes" not in fm
    assert "superseded_by" not in fm


def test_apply_supersession_edits_old_file(tmp_path):
    bp = _make_backlog(tmp_path)
    old_id, _ = write_handover(bp, tldr="old work", session_kind="milestone-complete",
                               body="Original body content.")
    new_id, _ = write_handover(bp, tldr="newer work", session_kind="milestone-complete")

    apply_supersession(bp, old_id=old_id, new_id=new_id)

    fm, body = read_handover(bp, old_id)
    assert fm["superseded_by"] == new_id
    assert body.startswith("> **SUPERSEDED")
    assert new_id in body
    # Original body must be preserved after the callout.
    assert "Original body content." in body


def test_apply_supersession_idempotent_on_already_superseded(tmp_path):
    bp = _make_backlog(tmp_path)
    old_id, _ = write_handover(bp, tldr="old", session_kind="milestone-complete")
    mid_id, _ = write_handover(bp, tldr="mid", session_kind="milestone-complete")
    new_id, _ = write_handover(bp, tldr="new", session_kind="milestone-complete")

    apply_supersession(bp, old_id=old_id, new_id=mid_id)
    # Re-applying with a newer id should update the pointer, not stack callouts.
    apply_supersession(bp, old_id=old_id, new_id=new_id)

    fm, body = read_handover(bp, old_id)
    assert fm["superseded_by"] == new_id
    assert body.count("> **SUPERSEDED") == 1
    assert new_id in body                # callout was updated to point at new_id
    assert mid_id not in body            # old pointer was replaced, not stacked


def test_apply_supersession_raises_for_missing_ids(tmp_path):
    bp = _make_backlog(tmp_path)
    hid, _ = write_handover(bp, tldr="existing", session_kind="end-of-day")
    with pytest.raises(FileNotFoundError):
        apply_supersession(bp, old_id=hid, new_id="nonexistent")
    with pytest.raises(FileNotFoundError):
        apply_supersession(bp, old_id="nonexistent", new_id=hid)


def _set_backlog_root(monkeypatch, bp: Path):
    monkeypatch.setattr(backlog_server, "ROOT", bp.parent)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)


def test_backlog_handover_create_with_supersedes(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    out_old = backlog_server.backlog_handover_create(
        tldr="old milestone", session_kind="milestone-complete",
    )
    old_id = out_old.splitlines()[0].split(": ", 1)[1].strip()

    out_new = backlog_server.backlog_handover_create(
        tldr="new milestone",
        session_kind="milestone-complete",
        supersedes=old_id,
    )
    assert "Handover written" in out_new
    new_id = out_new.splitlines()[0].split(": ", 1)[1].strip()

    # Old file should now have superseded_by + callout in body.
    fm, body = read_handover(bp, old_id)
    assert fm.get("superseded_by") == new_id
    assert body.startswith("> **SUPERSEDED")


def test_backlog_handover_supersede_tool(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    out_old = backlog_server.backlog_handover_create(
        tldr="A", session_kind="milestone-complete",
    )
    old_id = out_old.splitlines()[0].split(": ", 1)[1].strip()
    out_new = backlog_server.backlog_handover_create(
        tldr="B", session_kind="milestone-complete",
    )
    new_id = out_new.splitlines()[0].split(": ", 1)[1].strip()

    msg = backlog_server.backlog_handover_supersede(old_id=old_id, new_id=new_id)
    assert old_id in msg and new_id in msg

    fm, _ = read_handover(bp, old_id)
    assert fm["superseded_by"] == new_id
