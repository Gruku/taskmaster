"""Regression test for ISS-003.

The v3 migration writes HEAVY_FIELDS (description, notes, docs,
review_instructions) into the per-task .md frontmatter. The reader
(_load_task_full) was only copying ("docs", "review_instructions", ...)
and silently dropped description+notes. This test pins the reader to
return every HEAVY_FIELDS entry that's present in the .md frontmatter.
"""
from pathlib import Path

import pytest


@pytest.fixture
def v3_project(tmp_path, monkeypatch):
    """Set up a minimal v3-shaped project at tmp_path and point the server at it."""
    backlog = tmp_path / ".taskmaster" / "backlog.yaml"
    tasks_dir = tmp_path / ".taskmaster" / "tasks"
    backlog.parent.mkdir()
    tasks_dir.mkdir()
    backlog.write_text(
        "schema_version: 3\n"
        "meta: {project: test}\n"
        "epics: [{id: e1, name: Epic One}]\n"
        "tasks:\n"
        "  - {id: t-001, title: Task one, status: todo, epic: e1}\n",
        encoding="utf-8",
    )
    tasks_dir.joinpath("t-001.md").write_text(
        "---\n"
        "id: t-001\n"
        "title: Task one\n"
        "description: Long description of what this task does.\n"
        "notes: |\n"
        "  Multi-line notes\n"
        "  with actual content from the migration.\n"
        "docs:\n"
        "  spec: docs/spec.md\n"
        "review_instructions: Click the button.\n"
        "---\n",
        encoding="utf-8",
    )

    import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tmp_path / ".claude" / "missing.json")
    return tmp_path


def test_load_task_full_returns_description_and_notes_from_frontmatter(v3_project):
    from backlog_server import _load_task_full

    out = _load_task_full("t-001")
    assert out is not None
    assert out["description"] == "Long description of what this task does."
    assert "Multi-line notes" in out["notes"]
    assert "actual content from the migration" in out["notes"]
    # Sanity: pre-fix fields still work
    assert out["docs"] == {"spec": "docs/spec.md"}
    assert out["review_instructions"] == "Click the button."


def test_load_task_full_covers_all_heavy_fields(v3_project):
    """Future-proofs the reader: every HEAVY_FIELDS entry must round-trip."""
    from backlog_server import _load_task_full
    from taskmaster_v3 import HEAVY_FIELDS

    out = _load_task_full("t-001")
    for field in HEAVY_FIELDS:
        # Every HEAVY field is present in our fixture; reader must return them all.
        assert field in out, f"reader dropped HEAVY field '{field}'"
        assert out[field] not in (None, ""), f"reader returned empty for '{field}'"
