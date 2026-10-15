from pathlib import Path

PLAYBOOK = Path("playbooks/end-session/playbook.md")


def test_end_session_mentions_decision_sweep():
    body = PLAYBOOK.read_text(encoding="utf-8")
    # tm-audit-020: decision reads route through the action-dispatched tool.
    assert 'backlog_decision(action="list"' in body
    low = body.lower()
    assert "carried forward" in low or "carry forward" in low
    ref = (PLAYBOOK.parent / "references" / "v3-pre-steps.md").read_text(encoding="utf-8").lower()
    assert "carry forward" in ref and "drop" in ref
    # One combined question, with carry-forward as the default for unanswered decisions.
    assert "ask once" in ref and "carried forward" in ref


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
    """The decision-list read must appear in start-session (body or deep-mode ref)."""
    playbook_dir = Path("playbooks/start-session")
    body = (playbook_dir / "playbook.md").read_text(encoding="utf-8")
    deep_ref = playbook_dir / "references" / "deep-mode.md"
    deep_body = deep_ref.read_text(encoding="utf-8") if deep_ref.exists() else ""
    needle = 'backlog_decision(action="list"'
    assert needle in body or needle in deep_body, (
        "decision-list read must appear in start-session/playbook.md or references/deep-mode.md"
    )
