"""Lint checks for the taskmaster:handover skill scaffolding."""
from pathlib import Path
import re

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "handover"


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
    expected_refs = [
        SKILL_DIR / "references" / "session-kinds.md",
        SKILL_DIR / "references" / "tier-selection.md",
        SKILL_DIR / "references" / "auto-extraction.md",
        SKILL_DIR / "references" / "supersession.md",
        SKILL_DIR / "templates" / "light.md",
        SKILL_DIR / "templates" / "standard.md",
        SKILL_DIR / "templates" / "full.md",
    ]
    missing = [p for p in expected_refs if not p.exists()]
    assert not missing, f"missing referenced files: {missing}"


def test_references_are_not_stubs():
    # Each reference / template should be > 20 non-blank lines --
    # a one-line stub means Task 7/8 wasn't completed.
    for ref in (SKILL_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 20, f"reference looks like a stub: {ref}"
    for tpl in (SKILL_DIR / "templates").iterdir():
        non_blank = [ln for ln in tpl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"template looks like a stub: {tpl}"


def test_skill_md_links_resolve():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    # Find all relative references like `references/foo.md` or `templates/bar.md`.
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md|templates/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "SKILL.md does not reference any references/ or templates/ files"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"SKILL.md links do not resolve: {missing}"
