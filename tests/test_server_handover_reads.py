"""End-to-end tests for backlog_handover_get, backlog_handover_latest, and
backlog_handover_list (all three read tools) invoked directly through the
backlog_server module — same style as test_v3_handover.py.
"""
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402

from taskmaster.taskmaster_v3 import (
    sync_handover_index as _sync_handover_index,
    write_handover,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_backlog(tmp_path: Path) -> Path:
    """Create a minimal v2 backlog.yaml + handovers/ directory."""
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def _set_backlog_root(monkeypatch, bp: Path):
    """Redirect backlog_server's ROOT and _backlog_path to tmp_path."""
    monkeypatch.setattr(backlog_server, "ROOT", bp.parent)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)


def _sync_and_save(bp: Path):
    """Sync the handover index from disk and persist it into backlog.yaml."""
    raw = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
    _sync_handover_index(raw, bp)
    bp.write_text(yaml.safe_dump(raw, sort_keys=False))


# ── backlog_handover_get ───────────────────────────────────────────────────────


def test_handover_get_returns_frontmatter_and_body(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    hid, _ = write_handover(bp, tldr="did the thing", body="Long explanation here.", session_kind="end-of-day")

    # verbose=True reproduces the full frontmatter+body output
    result = backlog_server.backlog_handover_get(hid, verbose=True)
    assert "---" in result, "Expected YAML fence in response"
    assert "did the thing" in result
    assert "Long explanation here." in result


def test_handover_get_falls_back_to_archive(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    hid, path = write_handover(bp, tldr="archived context", session_kind="context-handoff")

    # Move the file under _archive/2026/ to simulate aging.
    archive_dir = bp.parent / "handovers" / "_archive" / "2026"
    archive_dir.mkdir(parents=True)
    archived_path = archive_dir / path.name
    path.rename(archived_path)

    result = backlog_server.backlog_handover_get(hid)
    assert "archived context" in result


def test_handover_get_returns_not_found_for_unknown_id(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    result = backlog_server.backlog_handover_get("2099-01-01-does-not-exist")
    assert "Handover not found" in result


# ── backlog_handover_latest ────────────────────────────────────────────────────


def test_handover_latest_returns_newest_handover_summary(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    write_handover(bp, tldr="april first work", session_kind="end-of-day",
                   when="2026-04-01")
    write_handover(bp, tldr="april fifteenth work", session_kind="context-handoff",
                   when="2026-04-15")
    write_handover(bp, tldr="may first work", next_action="review PR",
                   session_kind="milestone-complete", when="2026-05-01",
                   task_ids=["TASK-99"])
    _sync_and_save(bp)

    result = backlog_server.backlog_handover_latest()
    # Now returns deprecated notice + latest open handover
    assert "may first work" in result
    assert "review PR" in result
    assert "TASK-99" in result
    assert "milestone" in result  # normalized from "milestone-complete"


def test_handover_latest_returns_empty_message_when_no_handovers(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    result = backlog_server.backlog_handover_latest()
    # Deprecated tool returns notice + "No open handovers." when index is empty
    assert "No open handovers" in result or "No backlog" in result or "No handovers" in result


def test_handover_latest_breaks_same_day_ties_by_creation_time(tmp_path, monkeypatch):
    """Two handovers on the same date should order by `created`, not slug alpha.

    Regression: prior behavior sorted handover ids alphabetically, so on a
    same-day collision the slug that sorted later beat the one written later
    in time. The fix records `created` on every handover and sorts by it.
    """
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    # Slug "regenerated-..." sorts AFTER "asset-studio-..." alphabetically,
    # but is written FIRST chronologically. The actual latest is the second
    # write (asset-studio).
    write_handover(bp, tldr="regenerated mcp tool catalog",
                   session_kind="end-of-day", when="2026-05-08")
    write_handover(bp, tldr="asset-studio modal polish",
                   session_kind="context-handoff", when="2026-05-08")
    _sync_and_save(bp)

    result = backlog_server.backlog_handover_latest()
    assert "asset-studio modal polish" in result
    assert "regenerated mcp tool catalog" not in result


# ── backlog_handover_list ──────────────────────────────────────────────────────


def test_handover_list_default_returns_recent_entries(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    dates = ["2026-03-01", "2026-03-15", "2026-04-01", "2026-04-15", "2026-05-01"]
    written_ids = []
    for d in dates:
        hid, _ = write_handover(bp, tldr=f"work on {d}", session_kind="end-of-day", when=d)
        written_ids.append(hid)

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list()
    for hid in written_ids:
        assert hid in result, f"Expected {hid} in result"

    # Verify newest-first ordering: 2026-05-01 entry should appear before 2026-03-01 entry.
    newest = next(h for h in written_ids if "2026-05-01" in h)
    oldest = next(h for h in written_ids if "2026-03-01" in h)
    assert result.index(newest) < result.index(oldest)


def test_handover_list_filters_by_task_id(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    hid1, _ = write_handover(bp, tldr="task1 work A", session_kind="end-of-day",
                              task_ids=["TASK-1"], when="2026-04-01")
    hid2, _ = write_handover(bp, tldr="task1 work B", session_kind="end-of-day",
                              task_ids=["TASK-1"], when="2026-04-02")
    hid3, _ = write_handover(bp, tldr="task2 work", session_kind="end-of-day",
                              task_ids=["TASK-2"], when="2026-04-03")

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(task_id="TASK-1")
    assert hid1 in result
    assert hid2 in result
    assert hid3 not in result


def test_handover_list_filters_by_session_kind(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    hid_eod, _ = write_handover(bp, tldr="eod session", session_kind="continuity",
                                  when="2026-04-01")
    hid_ctx, _ = write_handover(bp, tldr="context handoff", session_kind="deep-context",
                                  when="2026-04-02")
    hid_ms, _ = write_handover(bp, tldr="milestone done", session_kind="milestone",
                                 when="2026-04-03")

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(session_kind="deep-context")
    assert hid_ctx in result
    assert hid_eod not in result
    assert hid_ms not in result


def test_handover_list_filters_by_since_date(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    hid_march, _ = write_handover(bp, tldr="march work", session_kind="end-of-day",
                                   when="2026-03-01")
    hid_april, _ = write_handover(bp, tldr="april work", session_kind="end-of-day",
                                   when="2026-04-15")
    hid_may, _ = write_handover(bp, tldr="may work", session_kind="end-of-day",
                                  when="2026-05-01")

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(since="2026-04-01")
    assert hid_april in result
    assert hid_may in result
    assert hid_march not in result


def test_handover_list_combines_filters(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    # TASK-1 + deep-context: should appear
    hid_match1, _ = write_handover(bp, tldr="match 1", session_kind="deep-context",
                                    task_ids=["TASK-1"], when="2026-04-01")
    hid_match2, _ = write_handover(bp, tldr="match 2", session_kind="deep-context",
                                    task_ids=["TASK-1"], when="2026-04-02")
    # TASK-1 but wrong kind: should NOT appear
    hid_wrong_kind, _ = write_handover(bp, tldr="wrong kind", session_kind="continuity",
                                        task_ids=["TASK-1"], when="2026-04-03")
    # right kind but wrong task: should NOT appear
    hid_wrong_task, _ = write_handover(bp, tldr="wrong task", session_kind="deep-context",
                                        task_ids=["TASK-2"], when="2026-04-04")

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(task_id="TASK-1", session_kind="deep-context")
    assert hid_match1 in result
    assert hid_match2 in result
    assert hid_wrong_kind not in result
    assert hid_wrong_task not in result


def test_handover_list_respects_limit_after_filters(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    # Write 8 handovers all with TASK-1.
    ids = []
    for i in range(8):
        day = str(i + 1).zfill(2)
        hid, _ = write_handover(bp, tldr=f"entry {i}", session_kind="end-of-day",
                                  task_ids=["TASK-1"], when=f"2026-05-{day}")
        ids.append(hid)

    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(task_id="TASK-1", limit=3)
    # Exactly 3 entry lines (bullets); an overflow footer notes the rest.
    lines = [l for l in result.splitlines() if l.startswith("- ")]
    assert len(lines) == 3, f"Expected 3 entry lines, got {len(lines)}: {result!r}"
    assert "5 more handovers" in result, f"Expected overflow footer, got: {result!r}"

    # The 3 returned should be the newest 3 (index is newest-first).
    # Newest 3 from 8: entries 5,6,7 → 2026-05-06, 2026-05-07, 2026-05-08
    newest_three = ids[-3:]
    for hid in newest_three:
        assert hid in result, f"Expected newest entry {hid} in result"


def test_handover_list_empty_when_no_matches(tmp_path, monkeypatch):
    bp = _make_backlog(tmp_path)
    _set_backlog_root(monkeypatch, bp)

    write_handover(bp, tldr="some work", session_kind="end-of-day", task_ids=["TASK-A"])
    _sync_and_save(bp)

    result = backlog_server.backlog_handover_list(task_id="DOES-NOT-EXIST")
    assert "No handovers" in result and "filter" in result.lower()
