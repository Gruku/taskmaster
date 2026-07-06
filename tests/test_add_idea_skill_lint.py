"""Structural lint for the taskmaster:add-idea skill (wrapper + playbook)."""
from pathlib import Path
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_PATH = Path(__file__).parent.parent / "skills" / "add-idea" / "SKILL.md"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "add-idea"
PLAYBOOK_DIR = Path(__file__).parent.parent / "playbooks" / "add-idea"
PLAYBOOK_PATH = PLAYBOOK_DIR / "playbook.md"


def test_skill_file_exists():
    assert SKILL_PATH.exists()


def test_playbook_exists():
    assert PLAYBOOK_PATH.exists()


def test_wrapper_points_at_playbook():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "../../playbooks/add-idea/playbook.md" in text


def test_skill_has_frontmatter():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: add-idea" in text
    assert "description:" in text


def test_playbook_calls_backlog_idea_create():
    text = PLAYBOOK_PATH.read_text(encoding="utf-8")
    assert "backlog_idea_create" in text


def test_playbook_documents_slash_form():
    text = PLAYBOOK_PATH.read_text(encoding="utf-8")
    assert "/add-idea" in text


def test_playbook_documents_optional_flags():
    text = PLAYBOOK_PATH.read_text(encoding="utf-8")
    for flag in ("--tags", "--status", "--related-task"):
        assert flag in text


def test_playbook_announces_id_on_commit():
    text = PLAYBOOK_PATH.read_text(encoding="utf-8")
    # playbook must instruct the model to report the IDEA-NNN id back to the user
    assert "Logged as IDEA" in text or "IDEA-NNN" in text


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["add-idea"]
    actual = body_token_count("add-idea")
    assert actual <= budget, (
        f"playbook is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("add-idea")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"


def test_references_exist():
    assert (PLAYBOOK_DIR / "references" / "slash-form.md").exists(), "missing references/slash-form.md"


def test_playbook_links_resolve():
    import re
    text = PLAYBOOK_PATH.read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved links: {missing}"
