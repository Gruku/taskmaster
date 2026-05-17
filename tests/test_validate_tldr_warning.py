# plugins/taskmaster/tests/test_validate_tldr_warning.py
"""Tests that backlog_validate warns on tasks/entities missing tldr."""
from __future__ import annotations

import yaml


def test_validate_warns_on_missing_tldr(tm_epic_phase):
    """backlog_validate should emit a warning (not an error) for tasks without tldr."""
    import backlog_server

    # Inject a legacy task directly into backlog.yaml (bypassing backlog_add_task
    # which auto-generates tldr — this simulates pre-progressive-disclosure data)
    from pathlib import Path
    bp = Path(tm_epic_phase) / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8"))

    for epic in data.get("epics", []):
        if epic.get("id") == "test-epic":
            epic.setdefault("tasks", []).append({
                "id": "T-no-tldr",
                "title": "Task without tldr",
                "status": "todo",
                "phase": "dev",
            })
            break
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")

    out = backlog_server.backlog_validate()

    assert "T-no-tldr" in out, f"Expected T-no-tldr in output, got:\n{out}"
    assert "tldr" in out.lower(), f"Expected 'tldr' in output, got:\n{out}"
    assert "warning" in out.lower() or "warn" in out.lower(), (
        f"Expected 'warning'/'warn' in output, got:\n{out}"
    )


def test_validate_no_warning_when_all_have_tldr(tm_epic_phase):
    """backlog_validate should NOT produce a warnings section when all tasks have tldr."""
    import backlog_server

    backlog_server.backlog_add_task(
        epic="test-epic",
        task_id="T-has-tldr",
        title="Task with tldr",
        tldr="This is a proper tldr.",
        phase="dev",
    )

    out = backlog_server.backlog_validate()

    # If there's a Warnings section it should only appear for missing-tldr entities
    # In this case there are none, so the section should be absent
    assert "T-has-tldr" not in out or "warning" not in out.lower(), (
        "Unexpected warning for task that has a tldr"
    )
    # More directly: the warning about T-has-tldr should not appear
    if "T-has-tldr" in out and "warning" in out.lower():
        # Extract the warnings section if present
        assert "T-has-tldr" not in out.split("## Warnings")[-1], (
            "Task with proper tldr incorrectly flagged as missing tldr"
        )


def test_validate_all_clear_still_shown_with_warnings(tm_epic_phase):
    """When issues=0 but tldr warnings exist, show 'All clear' plus warnings."""
    import backlog_server
    from pathlib import Path

    bp = Path(tm_epic_phase) / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(bp.read_text(encoding="utf-8"))

    for epic in data.get("epics", []):
        if epic.get("id") == "test-epic":
            epic.setdefault("tasks", []).append({
                "id": "T-legacy-2",
                "title": "Another legacy task",
                "status": "todo",
                "phase": "dev",
            })
            break
    bp.write_text(yaml.safe_dump(data), encoding="utf-8")

    out = backlog_server.backlog_validate()

    # Both "all clear" (for structural issues) and warnings (for tldr) should appear
    assert "all clear" in out.lower(), f"Expected 'all clear' in output:\n{out}"
    assert "warning" in out.lower() or "warn" in out.lower(), (
        f"Expected warning section:\n{out}"
    )
