from pathlib import Path

PLAYBOOK = Path("plugins/taskmaster/playbooks/end-session/playbook.md")


def test_end_session_mentions_decision_sweep():
    body = PLAYBOOK.read_text(encoding="utf-8")
    assert "backlog_decision_list" in body
    assert "carry forward" in body.lower()
    assert "drop" in body.lower()


def test_end_session_folds_decisions_into_handover_body():
    """b040ac6 dropped the phantom open_decisions / resolved_this_session
    kwargs: the decision sweep results are carried as handover BODY sections
    ("Open decisions" / "Resolved this session"), not handover_create args."""
    body = PLAYBOOK.read_text(encoding="utf-8")
    assert '"Open decisions"' in body
    assert '"Resolved this session"' in body
    ref = PLAYBOOK.parent / "references" / "v3-pre-steps.md"
    assert "no separate `open_decisions` / `resolved_this_session` parameters" \
        in ref.read_text(encoding="utf-8")


def test_start_session_calls_backlog_decision_list():
    """backlog_decision_list must appear in start-session skill (body or deep-mode reference)."""
    skill_dir = Path("plugins/taskmaster/skills/start-session")
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    deep_ref = skill_dir / "references" / "deep-mode.md"
    deep_body = deep_ref.read_text(encoding="utf-8") if deep_ref.exists() else ""
    assert "backlog_decision_list" in body or "backlog_decision_list" in deep_body, (
        "backlog_decision_list must appear in start-session/SKILL.md or references/deep-mode.md"
    )
