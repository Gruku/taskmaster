"""Lint checks for the taskmaster:start-session skill.

NOTE: Body budget (1300 tokens) is xfail until Plan D merges — Plan D owns the
start-session SKILL.md restructuring. Description budget check runs immediately.
"""
from pathlib import Path
import re
import yaml
import pytest
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "start-session"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "start-session"
    assert "description" in fm


@pytest.mark.xfail(
    reason="Plan D owns start-session body slim — remove xfail marker when Plan D merges",
    strict=True,
)
def test_body_within_budget():
    actual = body_token_count("start-session")
    assert actual <= 1_300, f"body is {actual} tokens (budget: 1300)"


def test_description_within_word_budget():
    count = description_word_count("start-session")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter()
    desc = fm["description"].lower()
    must_have = ["let's get started", "what should i work on", "show me the backlog"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"
