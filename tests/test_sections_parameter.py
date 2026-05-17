from plugins.taskmaster.taskmaster_v3 import resolve_sections


def test_resolve_task_inline_sections():
    entity = {"id": "T-001", "notes": "Some notes.", "review_instructions": "Run X."}
    out = resolve_sections(entity, kind="task", sections=["notes"], body="ignored")
    assert out == {"notes": "Some notes."}


def test_resolve_task_doc_section_returns_path_marker(tmp_path):
    entity = {"id": "T-001", "docs": {"plan": "docs/plan.md", "spec": "docs/spec.md"}}
    out = resolve_sections(entity, kind="task", sections=["plan"], body="", project_root=tmp_path)
    assert "plan" in out
    assert "docs/plan.md" in out["plan"] or out["plan"] == "(unresolved: docs/plan.md)"


def test_resolve_handover_section_from_body():
    body = "## Decisions\n\nChose A over B.\n\n## Blockers\n\nNeed approval."
    out = resolve_sections({}, kind="handover", sections=["decisions"], body=body)
    assert "Chose A over B" in out["decisions"]
    assert "Blockers" not in out["decisions"]


def test_resolve_unknown_section_raises():
    import pytest
    with pytest.raises(ValueError, match="not a canonical section"):
        resolve_sections({}, kind="task", sections=["bogus"], body="")


def test_resolve_handover_where_id_start_section():
    """Apostrophe in heading text must not break canonical section resolution."""
    body = "## Where I'd start\n\nRead spec then file X.\n\n## Notes\n\nSomething else."
    out = resolve_sections({}, kind="handover", sections=["where_id_start"], body=body)
    assert "Read spec then file X" in out["where_id_start"]
    assert "Something else" not in out["where_id_start"]


def test_resolve_section_strips_other_punctuation():
    """Commas in headings should be stripped during slugification."""
    body = "## Decisions\n\nChose A over B.\n\n## Blockers\n\nNeed approval."
    out = resolve_sections({}, kind="handover", sections=["blockers"], body=body)
    assert "Need approval" in out["blockers"]
