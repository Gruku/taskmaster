from plugins.taskmaster.taskmaster_v3 import slim_entity


def test_slim_task():
    full = {
        "id": "T-001", "title": "Refactor auth",
        "tldr": "Refactor the middleware.",
        "next_step": "Write failing test first",
        "status": "in-progress", "priority": "high",
        "depends_on": ["T-002"],
        "related_issues": ["ISS-007"],
        "related_lessons": ["L-003"],
        "docs": {"plan": "docs/plan.md", "spec": "docs/spec.md"},
        "notes": "long notes content here",
        "review_instructions": "lots of detail here",
        "_body": "## Why\n\nLots of body text.",
    }
    slim = slim_entity(full, kind="task")
    assert slim["id"] == "T-001"
    assert slim["tldr"] == "Refactor the middleware."
    assert slim["next_step"] == "Write failing test first"
    assert slim["status"] == "in-progress"
    assert slim["depends_on"] == ["T-002"]
    assert slim["docs_available"] == ["plan", "spec"]
    assert "notes" not in slim
    assert "review_instructions" not in slim
    assert "_body" not in slim


def test_slim_issue():
    full = {
        "id": "ISS-007", "title": "Auth fails",
        "tldr": "Auth crashes on Friday.",
        "severity": "P1", "status": "open",
        "impact": "3 customers blocked",
        "components": ["auth"],
        "related_tasks": ["T-001"],
        "_body": "Repro steps...",
    }
    slim = slim_entity(full, kind="issue")
    assert slim["severity"] == "P1"
    assert slim["tldr"] == "Auth crashes on Friday."
    assert "_body" not in slim


def test_slim_lesson():
    full = {
        "id": "L-001", "title": "Atomic writes",
        "tldr": "Use atomic_write() everywhere.",
        "kind": "pattern", "tier": "core",
        "reinforce_count": 3,
        "files": ["*.py"],
        "_body": "## Why\n\n...",
    }
    slim = slim_entity(full, kind="lesson")
    assert slim["kind"] == "pattern"
    assert slim["tier"] == "core"
    assert "_body" not in slim


def test_slim_handover():
    full = {
        "id": "HND-012",
        "tldr": "Auth refactor — next: backfill migration.",
        "next_action": "Run backfill on staging",
        "task_ids": ["T-001"],
        "session_kind": "context-handoff",
        "status": "open",
        "flag_reason": "needs decision on approach",
        "_body": "## Decisions\n\n...",
    }
    slim = slim_entity(full, kind="handover")
    assert slim["next_action"] == "Run backfill on staging"
    assert slim["status"] == "open"
    assert slim["flag_reason"] == "needs decision on approach"
    assert "_body" not in slim


def test_slim_task_includes_open_handovers():
    full = {
        "id": "T-001", "title": "Refactor auth",
        "tldr": "Refactor the middleware.",
        "status": "in-progress",
    }
    slim = slim_entity(full, kind="task", open_handovers=["HND-012"])
    assert slim["open_handovers"] == ["HND-012"]


def test_slim_task_omits_open_handovers_when_empty():
    full = {"id": "T-001", "title": "X", "tldr": "T.", "status": "todo"}
    slim = slim_entity(full, kind="task", open_handovers=None)
    assert "open_handovers" not in slim


def test_slim_entity_rejects_unknown_kind():
    import pytest
    with pytest.raises(ValueError, match="Unknown entity kind"):
        slim_entity({"id": "X"}, kind="bogus")
