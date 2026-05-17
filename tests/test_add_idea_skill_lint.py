"""Structural lint for the taskmaster:add-idea skill."""
from pathlib import Path
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_PATH = Path(__file__).parent.parent / "skills" / "add-idea" / "SKILL.md"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "add-idea"


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


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["add-idea"]
    actual = body_token_count("add-idea")
    assert actual <= budget, (
        f"body is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("add-idea")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"


def test_references_exist():
    assert (SKILL_DIR / "references" / "slash-form.md").exists(), "missing references/slash-form.md"


def test_skill_md_links_resolve():
    import re
    text = SKILL_PATH.read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"unresolved links: {missing}"
