# plugins/taskmaster/tests/test_validate_tldr_warning.py
"""Tests that backlog_validate warns on tasks/entities missing tldr."""
from __future__ import annotations

import yaml


def _write_missing_tldr(backlog_path, task_id: str, title: str) -> None:
    from taskmaster.taskmaster_v3 import write_task_file

    write_task_file(
        backlog_path.parent / "tasks" / f"{task_id}.md",
        {
            "id": task_id,
            "title": title,
            "status": "todo",
            "phase": "dev",
            "epic": "test-epic",
            "order": 999.0,
        },
        "",
    )


def test_validate_warns_on_missing_tldr(tm_epic_phase):
    """backlog_validate should emit a warning (not an error) for tasks without tldr."""
    from taskmaster import backlog_server

    from pathlib import Path

    bp = Path(tm_epic_phase) / ".taskmaster" / "backlog.yaml"
    _write_missing_tldr(bp, "T-no-tldr", "Task without tldr")

    out = backlog_server.backlog_validate()

    assert "T-no-tldr" in out, f"Expected T-no-tldr in output, got:\n{out}"
    assert "tldr" in out.lower(), f"Expected 'tldr' in output, got:\n{out}"
    assert "warning" in out.lower() or "warn" in out.lower(), (
        f"Expected 'warning'/'warn' in output, got:\n{out}"
    )


def test_validate_no_warning_when_all_have_tldr(tm_epic_phase):
    """backlog_validate should NOT produce a warnings section when all tasks have tldr."""
    from taskmaster import backlog_server

    backlog_server.backlog_add_task(epic="test-epic", title="Task with tldr", tldr="This is a proper tldr.", phase="dev", options={"task_id": "T-has-tldr"})

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
    from taskmaster import backlog_server
    from pathlib import Path

    bp = Path(tm_epic_phase) / ".taskmaster" / "backlog.yaml"
    _write_missing_tldr(bp, "T-legacy-2", "Another legacy task")

    out = backlog_server.backlog_validate()

    # Both "all clear" (for structural issues) and warnings (for tldr) should appear
    assert "all clear" in out.lower(), f"Expected 'all clear' in output:\n{out}"
    assert "warning" in out.lower() or "warn" in out.lower(), (
        f"Expected warning section:\n{out}"
    )
