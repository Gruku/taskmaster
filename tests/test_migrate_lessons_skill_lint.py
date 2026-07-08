"""Lint checks for the taskmaster:migrate-lessons skill (wrapper + playbook)."""
from pathlib import Path
import re

import yaml
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "migrate-lessons"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "migrate-lessons"


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def test_skill_dir_exists():
    assert SKILL_DIR.exists() and SKILL_DIR.is_dir()


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_playbook_exists():
    assert (PLAYBOOK_DIR / "playbook.md").exists()


def test_wrapper_points_at_playbook():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "../../playbooks/migrate-lessons/playbook.md" in text


def test_frontmatter_has_required_fields():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fm.get("name") == "migrate-lessons"
    assert "description" in fm and isinstance(fm["description"], str)


def test_description_contains_trigger_phrases():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    desc = fm["description"].lower()
    must_have = [
        "lessons",
        "memory",
        "migrate lessons",
        "convert lessons to memory",
        "what happened to lessons",
    ]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description is missing trigger phrases: {missing}"


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["migrate-lessons"]
    actual = body_token_count("migrate-lessons")
    assert actual <= budget, (
        f"playbook is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("migrate-lessons")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"
