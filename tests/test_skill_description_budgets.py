"""Description word-count enforcement: every skill description ≤ 60 words (or per-skill override)."""
import pytest
from skill_budget_helper import (
    SKILL_BUDGETS,
    DEFAULT_DESC_WORDS,
    DESCRIPTION_WORD_OVERRIDES,
    description_word_count,
    skill_md_path,
)

DESCRIPTION_WORD_BUDGET = DEFAULT_DESC_WORDS


@pytest.mark.parametrize("skill", SKILL_BUDGETS.keys())
def test_skill_description_within_budget(skill):
    """frontmatter description must be within word budget."""
    path = skill_md_path(skill)
    assert path.exists(), f"SKILL.md missing for '{skill}'"
    budget = DESCRIPTION_WORD_OVERRIDES.get(skill, DESCRIPTION_WORD_BUDGET)
    count = description_word_count(skill)
    assert count <= budget, (
        f"skill '{skill}' description is {count} words (budget: {budget}). "
        f"Trim to: 1 sentence what-it-does, 1 sentence triggers, 1 sentence hard rule."
    )
