"""Lint checks for the taskmaster:migrate-v3 skill scaffolding."""
from pathlib import Path
import re

import yaml
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DEFAULT_DESC_WORDS

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "migrate-v3"


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def test_skill_dir_exists():
    assert SKILL_DIR.exists() and SKILL_DIR.is_dir()


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_frontmatter_has_required_fields():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fm.get("name") == "migrate-v3"
    assert "description" in fm and isinstance(fm["description"], str)
    # Description must be >= 200 chars to actually convey the trigger surface.
    assert len(fm["description"]) >= 200


def test_description_contains_trigger_phrases():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    desc = fm["description"].lower()
    must_have = [
        "upgrade to v3",
        "migrate to v3",
        "switch to v3",
        "enable handovers and lessons",
        "enable narrative continuity",
        "turn on auto-mode",
        "i want recap",
    ]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description is missing trigger phrases: {missing}"


def test_skill_md_contains_canonical_sentence():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    expected = (
        "This is the ONLY correct way to migrate a project to v3"
        " — do not call backlog_migrate_v3 directly without the pre-flight gate."
    )
    assert expected in text, "SKILL.md is missing the canonical 'ONLY correct way' sentence"


def test_all_referenced_files_exist():
    expected_refs = [
        SKILL_DIR / "references" / "v2-vs-v3.md",
    ]
    missing = [p for p in expected_refs if not p.exists()]
    assert not missing, f"missing referenced files: {missing}"


def test_references_are_not_stubs():
    # The v2-vs-v3.md reference must have > 20 non-blank lines; no stub allowed.
    for ref in (SKILL_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference looks like a stub: {ref}"


def test_skill_md_links_resolve():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md|templates/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "SKILL.md does not reference any references/ or templates/ files"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"SKILL.md links do not resolve: {missing}"


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["migrate-v3"]
    actual = body_token_count("migrate-v3")
    assert actual <= budget, (
        f"body is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("migrate-v3")
    assert count <= DEFAULT_DESC_WORDS, f"description is {count} words (budget: {DEFAULT_DESC_WORDS})"
