"""Lint checks for the taskmaster:init-taskmaster skill."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "init-taskmaster"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "init-taskmaster"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("init-taskmaster")
    assert actual <= 1_200, f"body is {actual} tokens (budget: 1200)"


def test_description_within_word_budget():
    count = description_word_count("init-taskmaster")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_skill_md_contains_critical_note():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "CRITICAL" in text and "backlog_init" in text, (
        "CRITICAL note about not writing backlog.yaml directly must be present"
    )


def test_references_exist():
    assert (SKILL_DIR / "references" / "analysis-mode.md").exists()


def test_references_not_stubs():
    for ref in (SKILL_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference stub: {ref}"


def test_skill_md_links_resolve():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "SKILL.md must reference at least one references/ file"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"
