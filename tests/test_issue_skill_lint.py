"""Lint checks for the taskmaster:issue skill scaffolding."""
from pathlib import Path
import re

import yaml
from skill_budget_helper import body_token_count, description_word_count, SKILL_BUDGETS, DESCRIPTION_WORD_OVERRIDES, DEFAULT_DESC_WORDS

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "issue"


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
    assert fm.get("name") == "issue"
    assert "description" in fm and isinstance(fm["description"], str)
    # Description must be >= 200 chars to actually convey the trigger surface.
    assert len(fm["description"]) >= 200


def test_description_contains_trigger_phrases():
    fm = _read_frontmatter(SKILL_DIR / "SKILL.md")
    desc = fm["description"].lower()
    must_have = [
        "log an issue",
        "this is an issue",
        "file an issue",
        "promote to issue",
        "is this an issue",
        "list open issues",
        "triage issues",
        "mark issue fixed",
        "close iss-",
    ]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description is missing trigger phrases: {missing}"


def test_skill_md_contains_canonical_sentence():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    expected = (
        "This is the ONLY correct way to write or transition a project Issue"
        " — do not call `backlog_issue_create` or `backlog_issue_update` directly."
    )
    assert expected in text, "SKILL.md is missing the canonical 'ONLY correct way' sentence"


def test_skill_md_documents_all_entry_points():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    must_have = [
        "log-issue",
        "promote-from-bug",
        "update-status",
        "close-on-task-complete",
        "triage-review",
    ]
    missing = [p for p in must_have if p not in text]
    assert not missing, f"SKILL.md missing entry-point slugs: {missing}"


def test_flag_from_conversation_is_gone():
    p = SKILL_DIR / "references" / "entry-point-flows.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    assert "flag-from-conversation" not in txt, \
        "flag-from-conversation must be deleted per bug-tier redesign"


def test_issue_bar_reference_exists():
    assert (SKILL_DIR / "references" / "issue-bar.md").exists()
    # severity-heuristics.md must be gone (renamed)
    assert not (SKILL_DIR / "references" / "severity-heuristics.md").exists()


def test_all_referenced_files_exist():
    expected_refs = [
        SKILL_DIR / "references" / "issue-bar.md",
        SKILL_DIR / "references" / "lifecycle.md",
        SKILL_DIR / "references" / "auto-extraction.md",
        SKILL_DIR / "templates" / "issue-body.md",
    ]
    missing = [p for p in expected_refs if not p.exists()]
    assert not missing, f"missing referenced files: {missing}"


def test_references_are_not_stubs():
    # Each reference > 20 non-blank lines; each template > 5 (per spec).
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


def test_skill_body_within_budget():
    budget = SKILL_BUDGETS["issue"]
    actual = body_token_count("issue")
    assert actual <= budget, (
        f"body is {actual} tokens (budget: {budget}) — move deep content to references/"
    )


def test_description_within_word_budget():
    count = description_word_count("issue")
    budget = DESCRIPTION_WORD_OVERRIDES.get("issue", DEFAULT_DESC_WORDS)
    assert count <= budget, f"description is {count} words (budget: {budget})"
