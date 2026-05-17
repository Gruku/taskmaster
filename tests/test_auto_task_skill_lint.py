"""Lint checks for the taskmaster:auto-task skill."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "auto-task"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "auto-task"
    assert "description" in fm


def test_body_within_budget():
    # auto-task body budget is 1500.
    actual = body_token_count("auto-task")
    assert actual <= 1_500, f"body is {actual} tokens (budget: 1500)"


def test_description_within_word_budget():
    count = description_word_count("auto-task")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_skill_md_links_resolve():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"
