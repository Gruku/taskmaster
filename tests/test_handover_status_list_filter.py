"""Tests for the `status` filter parameter on backlog_handover_list.

Handovers written with session_kind="end-of-day" default to status="todo".
Handovers written with session_kind="auto-stage" default to status="done".
The filter must narrow the index by `status` field without reading every file.
"""
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

import backlog_server  # noqa: E402
from taskmaster_v3 import sync_handover_index, write_handover


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
    write_handover(bp, tldr="todo work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list()
    assert "todo work" in out
    assert "auto bookkeeping" in out


def test_list_filter_by_status_todo(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="todo work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list(status="todo")
    assert "todo work" in out
    assert "auto bookkeeping" not in out


def test_list_filter_by_status_done(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="todo work", session_kind="end-of-day")
    write_handover(bp, tldr="auto bookkeeping", session_kind="auto-stage")
    _resync(bp)

    out = backlog_server.backlog_handover_list(status="done")
    assert "todo work" not in out
    assert "auto bookkeeping" in out


def test_list_invalid_status_returns_error(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = backlog_server.backlog_handover_list(status="garbage")
    assert "Error" in out or "must be" in out
