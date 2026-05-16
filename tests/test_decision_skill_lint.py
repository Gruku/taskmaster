"""Lint the decision skill: required frontmatter, references, body sections."""
from pathlib import Path
import re
import yaml
import pytest

SKILL = Path("plugins/taskmaster/skills/decision/SKILL.md")


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with --- frontmatter ---"
    return yaml.safe_load(m.group(1))


def test_skill_file_exists():
    assert SKILL.exists()


def test_frontmatter_has_name_and_description():
    fm = parse_frontmatter(SKILL.read_text(encoding="utf-8"))
    assert fm["name"] == "decision"
    assert "decision" in fm["description"].lower()
    # Description must include the canonical trigger phrases.
    desc = fm["description"].lower()
    assert any(p in desc for p in (
        "choose between", "pick an option", "decide on", "open question", "branching path",
    )), f"Skill description missing trigger phrases: {desc}"


def test_body_documents_three_lifecycle_states():
    body = SKILL.read_text(encoding="utf-8")
    for state in ("open", "resolved", "dropped"):
        assert state in body.lower(), f"Skill body missing lifecycle state: {state}"


def test_references_exist():
    assert Path("plugins/taskmaster/skills/decision/references/lifecycle.md").exists()
    assert Path("plugins/taskmaster/skills/decision/references/auto-resolution.md").exists()
    assert Path("plugins/taskmaster/skills/decision/templates/decision-body.md").exists()
