"""Smoke test: sum of all skill descriptions <= 2,500 tokens (eager catalog budget)."""
import re
import yaml
from pathlib import Path
from skill_budget_helper import SKILL_BUDGETS, CHARS_PER_TOKEN

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
CATALOG_TOKEN_BUDGET = 2_500


def _description_tokens(skill_name: str) -> int:
    path = SKILLS_ROOT / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return 0
    fm = yaml.safe_load(m.group(1)) or {}
    return len(fm.get("description", "")) // CHARS_PER_TOKEN


def test_eager_catalog_within_budget():
    """Sum of all skill description token-counts must be <= 2,500 tokens."""
    total = sum(_description_tokens(skill) for skill in SKILL_BUDGETS)
    skill_breakdown = {s: _description_tokens(s) for s in SKILL_BUDGETS}
    top = sorted(skill_breakdown.items(), key=lambda x: x[1], reverse=True)[:5]
    assert total <= CATALOG_TOKEN_BUDGET, (
        f"Eager skill catalog is {total} tokens (budget: {CATALOG_TOKEN_BUDGET}). "
        f"Top 5 by size: {top}"
    )


def test_all_skill_bodies_exist():
    """Sanity: every skill in SKILL_BUDGETS has a SKILL.md on disk."""
    missing = [s for s in SKILL_BUDGETS if not (SKILLS_ROOT / s / "SKILL.md").exists()]
    assert not missing, f"SKILL.md missing for: {missing}"
