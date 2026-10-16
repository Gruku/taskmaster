"""Lint checks for the taskmaster:end-session skill (wrapper + playbook)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "end-session"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "end-session"


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
    assert "../../playbooks/end-session/playbook.md" in text


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "end-session"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("end-session")
    assert actual <= 1_500, f"playbook is {actual} tokens (budget: 1500)"


def test_description_within_word_budget():
    count = description_word_count("end-session")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_description_contains_key_trigger_phrases():
    fm = _read_frontmatter()
    desc = fm["description"].lower()
    must_have = ["end session", "wrap up", "mark this task done", "save progress"]
    missing = [p for p in must_have if p not in desc]
    assert not missing, f"description missing: {missing}"


def test_references_exist():
    for ref in ("v3-pre-steps.md", "summary-modes.md"):
        assert (PLAYBOOK_DIR / "references" / ref).exists(), f"missing references/{ref}"


def test_references_are_not_stubs_and_carry_their_facts():
    """Each reference exceeds a stub floor and names the calls it exists for."""
    must_name = {
        "v3-pre-steps.md": ["backlog_decision", "backlog_idea_create", "taskmaster:handover"],
        "summary-modes.md": ["backlog_complete_task", "backlog_issue_update", "human_action"],
    }
    for ref, needles in must_name.items():
        text = (PLAYBOOK_DIR / "references" / ref).read_text(encoding="utf-8")
        non_blank = [ln for ln in text.splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"reference stub: {ref}"
        missing = [n for n in needles if n not in text]
        assert not missing, f"{ref} missing: {missing}"


def test_playbook_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md must reference at least one references/ file"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"


def test_playbook_contains_canonical_sentence():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "only correct way" in text.lower() and "backlog_complete_task" in text, (
        "Missing the 'only correct way' sentence naming backlog_complete_task"
    )


def test_unfinished_work_stays_in_progress():
    """"I'm done for today" mid-task must not mark unfinished work done."""
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "Unfinished work stays in progress" in text
    assert "don't call `backlog_complete_task`" in text
    summary = (PLAYBOOK_DIR / "references" / "summary-modes.md").read_text(encoding="utf-8")
    assert "FROM links" in summary and "backlog_query" in summary


def test_playbook_close_blocker_read_and_bundle_members():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert 'backlog_context(focus=<task_id>, scope="task", include=[])' in text
    assert "references/edge-cases.md" in text and "bundle" in text
    edge = (PLAYBOOK_DIR / "references" / "edge-cases.md").read_text(encoding="utf-8")
    assert 'backlog_claim(action="status"' in edge and "members" in edge
    assert "_get_session_bundle" not in text
