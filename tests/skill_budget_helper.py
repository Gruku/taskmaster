"""Shared helpers for skill token-budget lint tests."""
from pathlib import Path
import re

# Approximate tokens = chars ÷ 4 (within ±15% of tiktoken cl100k).
CHARS_PER_TOKEN = 4

# Budget table: (skill_name, body_token_budget)
# start-session and pick-task bodies are owned by Plan D; budgets listed here
# but their tests are marked xfail until Plan D merges.
SKILL_BUDGETS: dict[str, int] = {
    "taskmaster":      800,
    "start-session":   1_300,   # Plan D owns body — lint-check only
    "pick-task":       1_300,   # Plan D owns body — lint-check only
    "end-session":     1_500,
    "handover":        1_300,
    "issue":           1_300,
    "lesson":          1_300,
    "auto-task":       1_500,
    "review-gate":     1_200,
    "spec-review":     1_300,
    "auto-epic":       1_200,
    "auto-phase":      1_200,
    "init-taskmaster": 1_200,
    "migrate-v3":      1_200,
    "check-todos":     1_200,
    "add-idea":        1_200,
}

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

# Skills whose body budget is owned by Plan D — lint runs but is xfail until D merges.
# Both start-session and pick-task merged: bodies slimmed by Plan D.
PLAN_D_OWNED: set[str] = set()

# Default description word budget.
DEFAULT_DESC_WORDS = 60

# Per-skill overrides for skills whose trigger phrases genuinely cannot fit in 60 words.
DESCRIPTION_WORD_OVERRIDES: dict[str, int] = {
    "issue": 120,  # bar-tier (recurring/systemic/outstanding) explanation + triggers + bug-routing fallback
}


def skill_md_path(skill_name: str) -> Path:
    return SKILLS_ROOT / skill_name / "SKILL.md"


def body_token_count(skill_name: str) -> int:
    """Return approximate token count for a skill's SKILL.md (full file)."""
    path = skill_md_path(skill_name)
    text = path.read_text(encoding="utf-8")
    return len(text) // CHARS_PER_TOKEN


def description_word_count(skill_name: str) -> int:
    """Return word count of the `description` frontmatter field."""
    path = skill_md_path(skill_name)
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return 0
    import yaml
    fm = yaml.safe_load(m.group(1)) or {}
    desc = fm.get("description", "")
    return len(desc.split())
