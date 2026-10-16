"""Lint checks for the taskmaster:init-taskmaster skill (wrapper + playbook)."""
from pathlib import Path
import re
import yaml
from skill_budget_helper import body_token_count, description_word_count

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "init-taskmaster"
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "playbooks" / "init-taskmaster"


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
    assert "../../playbooks/init-taskmaster/playbook.md" in text


def test_frontmatter_required_fields():
    fm = _read_frontmatter()
    assert fm.get("name") == "init-taskmaster"
    assert "description" in fm


def test_body_within_budget():
    actual = body_token_count("init-taskmaster")
    assert actual <= 1_200, f"playbook is {actual} tokens (budget: 1200)"


def test_description_within_word_budget():
    count = description_word_count("init-taskmaster")
    assert count <= 60, f"description is {count} words (budget: 60)"


def test_playbook_states_tool_only_writes():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "backlog_init" in text and "backlog_*" in text
    assert "hand edits" in text, "playbook must say the backlog is written only through the tools"


def test_playbook_checks_root_before_first_write():
    # The server roots at its start directory and the first write creates the store there.
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    probe = text.index("backlog_store_status")
    assert probe < text.index("backlog_init(project_name)")
    for token in ("no backlog found at", "TASKMASTER_ROOT", "linked worktree", "git worktree list"):
        assert token in text, token


def test_playbook_routes_legacy_backlogs_to_adoption():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    for token in ("taskmaster:migrate-v3", "backlog_canonicalize_layout", "no store yet"):
        assert token in text, token


def test_playbook_keeps_user_checkpoints():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "Analyze project" in text and "Clean start" in text
    assert "backlog_project_init(name)" in text
    assert "only after the user approves" in text


def test_playbook_names_verified_call_signatures():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    for call in ("backlog_area_create(area_id, name)", "backlog_add_epic(epic_id, name, done_when",
                 "backlog_add_phase(phase_id, name)", "backlog_add_task(title, epic, phase"):
        assert call in text, call


def test_playbook_says_what_to_commit_and_leaves_commit_to_user():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert ".taskmaster/local/" in text and ".gitignore" in text
    assert "user's call" in text


def test_playbook_describes_the_native_store_init_creates():
    # backlog_init creates a native store; its files are committed with managed Git, and the
    # legacy store is an explicit opt-out or a reported fallback.
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    assert "native store" in text and "backlog_sync()" in text
    assert "legacy_store=true" in text and "`Note:`" in text
    assert "managed Git command" in text and "../end-session/references/edge-cases.md" in text
    assert (PLAYBOOK_DIR.parent / "end-session" / "references" / "edge-cases.md").exists()
    # The managed commit carries only projection files; the config needs plain Git.
    assert "`taskmaster.json`, which it leaves out, with plain Git" in text
    assert "overwritten or quarantined" not in text, "legacy-store wording for hand edits"


def test_analysis_mode_names_verified_call_signatures():
    text = (PLAYBOOK_DIR / "references" / "analysis-mode.md").read_text(encoding="utf-8")
    for call in ("backlog_area_create(area_id, name)", "backlog_add_epic(epic_id, name, done_when",
                 "backlog_add_phase(phase_id, name)", "backlog_add_task(title, epic, phase"):
        assert call in text, call


def test_references_exist():
    assert (PLAYBOOK_DIR / "references" / "analysis-mode.md").exists()


def test_references_not_stubs():
    for ref in (PLAYBOOK_DIR / "references").iterdir():
        non_blank = [ln for ln in ref.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_blank) > 5, f"reference stub: {ref}"


def test_analysis_mode_reuses_check_todos_scan():
    text = (PLAYBOOK_DIR / "references" / "analysis-mode.md").read_text(encoding="utf-8")
    assert "scan-flow.md" in text


def test_playbook_links_resolve():
    text = (PLAYBOOK_DIR / "playbook.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(references/[A-Za-z0-9_-]+\.md)`", text)
    assert refs, "playbook.md must reference at least one references/ file"
    missing = [r for r in refs if not (PLAYBOOK_DIR / r).exists()]
    assert not missing, f"unresolved: {missing}"
