"""Structural lint for the taskmaster:add-idea skill."""
from pathlib import Path

SKILL_PATH = Path(__file__).parent.parent / "skills" / "add-idea" / "SKILL.md"


def test_skill_file_exists():
    assert SKILL_PATH.exists()


def test_skill_has_frontmatter():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: add-idea" in text
    assert "description:" in text


def test_skill_calls_backlog_idea_create():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "backlog_idea_create" in text


def test_skill_documents_slash_form():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "/add-idea" in text


def test_skill_documents_optional_flags():
    text = SKILL_PATH.read_text(encoding="utf-8")
    for flag in ("--tags", "--status", "--related-task"):
        assert flag in text


def test_skill_announces_id_on_commit():
    text = SKILL_PATH.read_text(encoding="utf-8")
    # Skill must instruct the model to report the IDEA-NNN id back to the user
    assert "Logged as IDEA" in text or "IDEA-NNN" in text
