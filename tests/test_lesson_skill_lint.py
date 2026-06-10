"""Lint checks for the taskmaster:lesson skill scaffolding."""
from pathlib import Path
import re

import yaml
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "lesson"


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_has_required_fields():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fm.get("name") == "lesson"
    assert "description" in fm and isinstance(fm["description"], str)
    # Description must be >= 200 chars to actually convey the trigger surface.
    assert len(fm["description"]) >= 200


def test_description_contains_all_trigger_phrases():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    desc = fm["description"].lower()
    # tm-audit-021 trimmed semantic duplicates ('learn this lesson',
    # 'memorize this') from the always-loaded description; the remaining
    # phrases are the canonical trigger surface.
    must_have = [
        "remember this",
        "save as a lesson",
        "this keeps happening",
        "we always do x here",
        "we got burned by this last time",
        "promote candidate to lesson",
        "review lesson candidates",
        "flag this session for retro",
    ]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description is missing trigger phrases: {missing}"


def test_all_referenced_files_exist():
    expected_refs = [
        SKILL_DIR / "references" / "marker-format.md",
        SKILL_DIR / "references" / "auto-extraction.md",
        SKILL_DIR / "references" / "reinforce-flows.md",
        SKILL_DIR / "references" / "promotion-decay.md",
        SKILL_DIR / "references" / "session-retro.md",
        SKILL_DIR / "templates" / "lesson-body.md",
    ]
    missing = [p for p in expected_refs if not p.exists()]
    assert not missing, f"missing referenced files: {missing}"


def test_references_are_not_stubs():
    # Each reference > 20 non-blank lines; template > 5 (per spec §13).
    for ref in (SKILL_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference looks like a stub: {ref}"
    for tpl in (SKILL_DIR / "templates").iterdir():
        non_blank = [ln for ln in tpl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"template looks like a stub: {tpl}"


def test_skill_md_links_resolve():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md|templates/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "SKILL.md does not reference any references/ or templates/ files"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"SKILL.md links do not resolve: {missing}"


def test_skill_md_documents_all_five_entry_points():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    must_have = [
        "write-from-context",
        "write-from-candidate",
        "reinforce-immediate",
        "reinforce-sweep",
        "session-retro",
    ]
    missing = [p for p in must_have if p not in text]
    assert not missing, f"SKILL.md missing entry-point names: {missing}"


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["lesson"]
    actual = body_token_count("lesson")
    assert actual <= budget, (
        f"body is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("lesson")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"
