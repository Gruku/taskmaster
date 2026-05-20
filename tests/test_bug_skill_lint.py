"""Lint check for taskmaster:bug skill structure."""
from pathlib import Path
import re


SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "bug"


def test_skill_md_exists():
    assert (SKILL_DIR / "SKILL.md").exists()


def test_skill_frontmatter_has_name_and_description():
    txt = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"^name:\s*bug\s*$", txt, re.MULTILINE)
    assert re.search(r"^description:\s*\".+\"\s*$", txt, re.MULTILINE)


def test_description_includes_key_triggers():
    txt = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for phrase in [
        "log a bug",
        "this is a bug",
        "track this defect",
        "I found a bug",
        "shelve this for later",
        "list open bugs",
        "promote B-",
    ]:
        assert phrase in txt, f"missing trigger: {phrase!r}"


def test_references_files_exist():
    assert (SKILL_DIR / "references" / "entry-point-flows.md").exists()
    assert (SKILL_DIR / "references" / "bug-vs-issue.md").exists()
    assert (SKILL_DIR / "templates" / "bug-body.md").exists()


def test_skill_lists_five_entry_points():
    txt = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for ep in ("log-bug", "offer-on-explicit-finding", "disposition", "update-status", "triage-review"):
        assert ep in txt, f"missing entry point: {ep}"
