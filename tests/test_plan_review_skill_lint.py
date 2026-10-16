# User intent: plan-review had no lint coverage; pin its wrapper, description budget and the calls it
# exists to carry, mirroring spec-review, so the gate prompt cannot regress silently.
"""Lint checks for the taskmaster:plan-review skill (wrapper + playbook)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "plan-review"
PLAYBOOK_DIR = ROOT / "playbooks" / "plan-review"


def _read_frontmatter() -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return yaml.safe_load(m.group(1)) or {} if m else {}


def test_wrapper_points_at_playbook():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "../../playbooks/plan-review/playbook.md" in text
    assert (PLAYBOOK_DIR / "playbook.md").exists()


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "plan-review"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("plan-review")
    assert actual <= 1_000, f"playbook is {actual} tokens (budget: 1000)"


def test_description_within_word_budget():
    count = description_word_count("plan-review")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    desc = _read_frontmatter()["description"].lower()
    missing = [p for p in ("review this plan", "plan review") if p not in desc]
    assert not missing, f"description missing: {missing}"


def test_playbook_names_the_gate_calls():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "backlog_record_gate" in text
    assert '"plan-review"' in text
    assert "backlog_skip_gate" in text


def test_playbook_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(\.\./[A-Za-z0-9_-]+/references/[A-Za-z0-9_-]+\.md|references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md must link spec-review's codex reference"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"
