"""Lint checks for the taskmaster:handover skill scaffolding."""
from pathlib import Path
import re

import yaml
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "handover"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "handover"


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_playbook_exists():
    assert (PLAYBOOK_DIR / "playbook.md").exists()


def test_wrapper_points_at_playbook():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "../../playbooks/handover/playbook.md" in text


def test_frontmatter_has_required_fields():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fm.get("name") == "handover"
    assert "description" in fm and isinstance(fm["description"], str)
    # Description must be >= 100 chars to actually convey the trigger surface.
    assert len(fm["description"]) >= 100


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    desc = fm["description"].lower()
    must_have = [
        "write a handover",
        "wrap up",
        "for tomorrow",
        "context handoff",
        "before compaction",
    ]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description is missing trigger phrases: {missing}"


def test_all_referenced_files_exist():
    # After 6→4 simplification: tier-selection.md removed; light/standard/full → body.md only.
    expected_refs = [
        PLAYBOOK_DIR / "references" / "session-kinds.md",
        PLAYBOOK_DIR / "references" / "auto-extraction.md",
        PLAYBOOK_DIR / "references" / "supersession.md",
        PLAYBOOK_DIR / "templates" / "body.md",
    ]
    missing = [p for p in expected_refs if not p.exists()]
    assert not missing, f"missing referenced files: {missing}"
    # Old files must not exist.
    removed = [
        PLAYBOOK_DIR / "templates" / "light.md",
        PLAYBOOK_DIR / "templates" / "standard.md",
        PLAYBOOK_DIR / "templates" / "full.md",
    ]
    present = [p for p in removed if p.exists()]
    assert not present, f"old template files should be removed: {present}"


def test_references_are_not_stubs():
    # Each reference should be > 5 non-blank lines.
    # session-kinds.md is intentionally concise after 6→4 simplification.
    for ref in (PLAYBOOK_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"reference looks like a stub: {ref}"
    for tpl in (PLAYBOOK_DIR / "templates").iterdir():
        non_blank = [ln for ln in tpl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"template looks like a stub: {tpl}"


def test_skill_md_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    # Find all relative references like `references/foo.md` or `templates/bar.md`.
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md|templates/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md does not reference any references/ or templates/ files"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"playbook.md links do not resolve: {missing}"


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["handover"]
    actual = body_token_count("handover")
    assert actual <= budget, (
        f"playbook is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("handover")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"
