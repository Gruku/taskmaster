"""Lint checks for the taskmaster:review-gate skill (wrapper + playbook)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "review-gate"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "review-gate"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_playbook_exists():
    assert (PLAYBOOK_DIR / "playbook.md").exists()


def test_wrapper_points_at_playbook():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "../../playbooks/review-gate/playbook.md" in text


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "review-gate"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("review-gate")
    assert actual <= 1_200, f"playbook is {actual} tokens (budget: 1200)"


def test_description_within_word_budget():
    count = description_word_count("review-gate")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter()
    desc = fm["description"].lower()
    must_have = ["is this ready", "run the review gate", "check my work"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"


def test_description_distinguishes_from_spec_review():
    fm = _read_frontmatter()
    desc = fm["description"].lower()
    assert "implementation" in desc or "code" in desc, (
        "description must clarify this reviews implementation, not spec"
    )


def test_references_exist():
    for ref in ("gate-details.md", "codex-integration.md", "bundle-gate.md"):
        assert (PLAYBOOK_DIR / "references" / ref).exists(), f"missing references/{ref}"


def test_references_not_stubs():
    for ref in (PLAYBOOK_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference stub: {ref}"


def test_playbook_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md must reference at least one references/ file"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"
