from pathlib import Path

SKILL = Path("plugins/taskmaster/skills/end-session/SKILL.md")


def test_end_session_mentions_decision_sweep():
    body = SKILL.read_text(encoding="utf-8")
    assert "backlog_decision_list" in body
    assert "carry forward" in body.lower()
    assert "drop" in body.lower()


def test_end_session_passes_open_decisions_to_handover_write():
    body = SKILL.read_text(encoding="utf-8")
    assert "open_decisions" in body
    assert "resolved_this_session" in body


def test_start_session_calls_backlog_decision_list():
    body = Path("plugins/taskmaster/skills/start-session/SKILL.md").read_text(encoding="utf-8")
    assert "backlog_decision_list" in body
