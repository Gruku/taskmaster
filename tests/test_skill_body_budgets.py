"""Parametrized token-budget enforcement for every taskmaster skill body."""
import pytest
from skill_budget_helper import (
    SKILL_BUDGETS,
    PLAN_D_OWNED,
    body_token_count,
    skill_md_path,
)


@pytest.mark.parametrize("skill,budget", SKILL_BUDGETS.items())
def test_skill_body_within_budget(skill, budget):
    """SKILL.md must be within token budget after slimming."""
    if skill in PLAN_D_OWNED:
        pytest.xfail(f"{skill} body is owned by Plan D — budget check pending merge")
    path = skill_md_path(skill)
    assert path.exists(), f"SKILL.md missing for skill '{skill}'"
    actual = body_token_count(skill)
    assert actual <= budget, (
        f"skill '{skill}' body is {actual} tokens (budget: {budget}). "
        f"Move deep-walkthrough content to references/<topic>.md."
    )
