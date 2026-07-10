"""Tests for the `status` filter parameter on backlog_handover_list.

Handovers written with session_kind="end-of-day" default to status="open".
Handovers written with session_kind="auto-stage" default to status="closed".
The filter must narrow the index by `status` field without reading every file.
"""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402
from taskmaster.taskmaster_v3 import sync_handover_index, write_handover


def _setup(tmp_path, monkeypatch):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-05-09"}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    monkeypatch.setattr(backlog_server, "ROOT", bp.parent)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    return bp


def _resync(bp):
    data = yaml.safe_load(bp.read_text()) or {}
    sync_handover_index(data, bp)
    bp.write_text(yaml.safe_dump(data, sort_keys=False))


def test_list_default_returns_all(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="open work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list()
    assert "open work" in out
    assert "auto bookkeeping" in out


def test_list_filter_by_status_open(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="open work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list(status="open")
    assert "open work" in out
    assert "auto bookkeeping" not in out


def test_list_filter_by_status_closed(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="open work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list(status="closed")
    assert "open work" not in out
    assert "auto bookkeeping" in out


def test_list_invalid_status_returns_error(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = backlog_server.backlog_handover_list(status="garbage")
    assert "Error" in out or "must be" in out


# ── B-039: since= filter uses `date` field, not id date-prefix ──

def test_since_filter_uses_date_field_not_id_prefix(tmp_path, monkeypatch):
    """since= must compare against the `date` field, not the id date-prefix.

    We inject an index entry whose id prefix (2026-05-10) is AFTER the since
    threshold but whose `date` field (2026-05-01) is BEFORE it.  The entry
    must be excluded because `date` < since.

    A second entry has matching id prefix and date (common case) to confirm
    the normal path is unaffected.
    """
    bp = _setup(tmp_path, monkeypatch)
    data = yaml.safe_load(bp.read_text()) or {}

    # Entry whose `date` diverges from its id prefix date.
    # id prefix: 2026-05-10 (>= since=2026-05-05, would PASS if filter used id)
    # date:      2026-05-01 (<  since=2026-05-05, must FAIL the filter)
    data.setdefault("handovers", []).append({
        "id": "2026-05-10-diverged",
        "date": "2026-05-01",
        "tldr": "diverged entry",
        "session_kind": "end-of-day",
        "status": "open",
    })
    # Normal entry: id prefix and date both after the threshold.
    data["handovers"].append({
        "id": "2026-05-20-normal",
        "date": "2026-05-20",
        "tldr": "normal entry",
        "session_kind": "end-of-day",
        "status": "open",
    })
    bp.write_text(yaml.safe_dump(data, sort_keys=False))

    out = backlog_server.backlog_handover_list(since="2026-05-05")
    # The diverged entry's real date is before the threshold -> must be excluded.
    assert "diverged entry" not in out, (
        f"Entry with date before 'since' should be excluded, got: {out!r}"
    )
    # The normal entry passes both the id-prefix and the date check.
    assert "normal entry" in out, (
        f"Entry with date after 'since' must be included, got: {out!r}"
    )


# ── B-040 / tm-audit-007: limit<=0 means "no cap" (unified list convention),
# never a silent truncation to the most-recent single entry. ──

def test_limit_zero_returns_all(tmp_path, monkeypatch):
    """limit=0 returns every handover (no cap), not a single most-recent entry."""
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="first", session_kind="end-of-day")
    write_handover(bp, tldr="second", session_kind="end-of-day")
    write_handover(bp, tldr="third", session_kind="end-of-day")
    _resync(bp)

    out = backlog_server.backlog_handover_list(limit=0)
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) == 3, (
        f"Expected all 3 entries with limit=0 (no cap), got {len(lines)}: {out!r}"
    )


def test_limit_negative_returns_all(tmp_path, monkeypatch):
    """limit<0 also means no cap under the unified convention."""
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="first", session_kind="end-of-day")
    write_handover(bp, tldr="second", session_kind="end-of-day")
    _resync(bp)

    out = backlog_server.backlog_handover_list(limit=-3)
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) == 2, (
        f"Expected all 2 entries with limit=-3 (no cap), got {len(lines)}: {out!r}"
    )


def test_limit_truncates_to_exact_count(tmp_path, monkeypatch):
    """limit=2 with 3 handovers returns exactly 2 entries."""
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="first", session_kind="end-of-day")
    write_handover(bp, tldr="second", session_kind="end-of-day")
    write_handover(bp, tldr="third", session_kind="end-of-day")
    _resync(bp)

    out = backlog_server.backlog_handover_list(limit=2)
    # Each entry is a bullet line starting with '- '.
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) == 2, (
        f"Expected exactly 2 entries with limit=2, got {len(lines)}: {out!r}"
    )
