"""Lint checks for the taskmaster:auto-phase skill."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "auto-phase"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "auto-phase"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("auto-phase")
    assert actual <= 1_200, f"body is {actual} tokens (budget: 1200)"


def test_description_within_word_budget():
    count = description_word_count("auto-phase")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_skill_md_contains_confirm_step():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "AskUserQuestion" in text, "confirm step must be present"


def test_skill_md_contains_auto_start_call():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "backlog_auto_start" in text, "seed call must be present"


def test_references_exist():
    for ref in ("loop-protocol.md", "failure-aggregation.md"):
        assert (SKILL_DIR / "references" / ref).exists(), f"missing references/{ref}"


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
