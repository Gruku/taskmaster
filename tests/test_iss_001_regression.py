"""Regression tests for ISS-001.

Skill auto-offers (handover/lesson/issue prompts in start/pick/end-session)
silently skipped when backlog.yaml had v3 content but missing
`schema_version: 3` marker. Two-pronged fix:

1. `_ensure_v3_marker` runs after each v3 entity-write, promoting the marker.
2. `_effective_schema_version` reports v3 when v3 entity content exists,
   so `backlog_status` shows `Schema: v3` and skill gates fire.
"""
from pathlib import Path

import pytest
import yaml


def _write_backlog(tmp_path: Path, body: str) -> Path:
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text(body, encoding="utf-8")
    return bp


@pytest.fixture
def server_at(tmp_path, monkeypatch):
    """Pin the backlog_server module to tmp_path."""
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tmp_path / ".taskmaster" / "missing.json")
    monkeypatch.setattr(backlog_server, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "missing.json")
    return backlog_server


def test_has_v3_content_false_on_empty_v2(server_at):
    assert server_at._has_v3_content({}) is False
    assert server_at._has_v3_content({"meta": {}, "epics": []}) is False


def test_has_v3_content_true_when_any_entity_index_present(server_at):
    assert server_at._has_v3_content({"handovers": [{"id": "HO-001"}]}) is True
    assert server_at._has_v3_content({"issues": [{"id": "ISS-001"}]}) is True
    assert server_at._has_v3_content({"lessons_meta": [{"id": "L-001"}]}) is True


def test_effective_schema_version_promotes_when_content_present(server_at):
    data = {"meta": {}, "epics": [], "handovers": [{"id": "HO-001"}]}
    assert server_at._effective_schema_version(data) == 3


def test_effective_schema_version_keeps_v2_when_truly_empty(server_at):
    data = {"meta": {}, "epics": []}
    assert server_at._effective_schema_version(data) == 2


def test_effective_schema_version_respects_explicit_marker(server_at):
    data = {"meta": {"schema_version": 3}, "epics": []}
    assert server_at._effective_schema_version(data) == 3


def test_ensure_v3_marker_writes_marker_when_missing(server_at, tmp_path):
    bp = _write_backlog(
        tmp_path,
        "meta: {project: test}\n"
        "epics: [{id: e1, name: Epic, tasks: []}]\n",
    )
    server_at._ensure_v3_marker(bp)
    raw = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert raw["meta"]["schema_version"] == 3


def test_ensure_v3_marker_idempotent(server_at, tmp_path):
    bp = _write_backlog(
        tmp_path,
        "meta: {project: test, schema_version: 3}\n"
        "epics: [{id: e1, name: Epic, tasks: []}]\n",
    )
    before = bp.read_text(encoding="utf-8")
    server_at._ensure_v3_marker(bp)
    assert bp.read_text(encoding="utf-8") == before


def test_issue_create_auto_promotes_marker(server_at, tmp_path):
    """Filing an issue on a v2-marker backlog flips the marker to v3."""
    bp = _write_backlog(
        tmp_path,
        "meta: {project: test, updated: '2026-01-01'}\n"
        "epics: [{id: e1, name: Epic, tasks: []}]\n",
    )
    (tmp_path / ".taskmaster" / "issues").mkdir()
    (tmp_path / ".taskmaster" / "tasks").mkdir()

    result = server_at.backlog_issue_create(
        title="Test issue",
        severity="P2",
        impact="Test",
    )
    assert "Issue created" in result, result
    raw = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert raw["meta"]["schema_version"] == 3


def test_backlog_status_emits_schema_line(server_at, tmp_path):
    """Skill gates read this line — pin its format."""
    _write_backlog(
        tmp_path,
        "meta: {project: test, schema_version: 3, updated: '2026-01-01'}\n"
        "epics: [{id: e1, name: Epic, tasks: [], color: '#888'}]\n"
        "phases: []\n"
        "tasks: []\n",
    )
    (tmp_path / ".taskmaster" / "tasks").mkdir()

    out = server_at.backlog_status()
    assert out.splitlines()[0].startswith("**Schema:** v3"), out


def test_backlog_status_schema_line_uses_effective_version(server_at, tmp_path):
    """Backlog with v3 entity content but no marker reports v3."""
    _write_backlog(
        tmp_path,
        "meta: {project: test, updated: '2026-01-01'}\n"
        "epics: [{id: e1, name: Epic, tasks: [], color: '#888'}]\n"
        "phases: []\n"
        "issues: [{id: ISS-001, title: x, severity: P2, status: open}]\n",
    )

    out = server_at.backlog_status()
    assert out.splitlines()[0].startswith("**Schema:** v3"), out
