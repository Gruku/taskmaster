"""backlog_handover_update_status sets all four status fields, validates the
enum, and is idempotent."""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

import backlog_server  # noqa: E402
from taskmaster_v3 import read_handover, write_handover


def _make_backlog(tmp_path, monkeypatch):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-05-09"}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    monkeypatch.setattr(backlog_server, "ROOT", bp.parent)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    return bp


def test_update_status_sets_all_four_fields(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, monkeypatch)
    hid, _ = write_handover(bp, tldr="needs attention", session_kind="end-of-day")

    result = backlog_server.backlog_handover_update_status(hid, "in-progress", reason="working on it")
    assert "in-progress" in result

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "in-progress"
    assert fm["status_user_set"] is True
    assert fm["status_reason"] == "working on it"
    assert fm["status_changed"]  # ISO timestamp


def test_update_status_rejects_invalid_enum(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, monkeypatch)
    hid, _ = write_handover(bp, tldr="t", session_kind="end-of-day")
    result = backlog_server.backlog_handover_update_status(hid, "garbage")
    assert "Error" in result or "must be" in result


def test_update_status_unknown_id_returns_error(tmp_path, monkeypatch):
    _make_backlog(tmp_path, monkeypatch)
    result = backlog_server.backlog_handover_update_status("2099-01-01-nope", "done")
    assert "not found" in result.lower()


def test_update_status_reason_is_optional(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path, monkeypatch)
    hid, _ = write_handover(bp, tldr="t", session_kind="end-of-day")
    backlog_server.backlog_handover_update_status(hid, "done")
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"
    assert "status_reason" not in fm


def test_update_status_empty_reason_preserves_previous(tmp_path, monkeypatch):
    """Passing reason='' on a follow-up update keeps any existing
    status_reason intact (the if-reason guard is intentional, not a bug)."""
    bp = _make_backlog(tmp_path, monkeypatch)
    hid, _ = write_handover(bp, tldr="t", session_kind="end-of-day")
    backlog_server.backlog_handover_update_status(hid, "in-progress", reason="initial")
    backlog_server.backlog_handover_update_status(hid, "done")  # no reason passed
    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "done"
    assert fm["status_reason"] == "initial"  # preserved, not cleared
