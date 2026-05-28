import json
from backlog_server import (backlog_add_epic, backlog_add_task, backlog_update_task,
                            backlog_update_epic, backlog_epic_status)

def _setup(tm_epic_phase):
    backlog_update_epic("test-epic", "components",
                        json.dumps({"core": {"title": "Core"}}))
    for tid, status in [("E-1", "done"), ("E-2", "in-progress"), ("E-3", "todo")]:
        backlog_add_task(epic="test-epic", task_id=tid, title=tid, phase="dev")
        backlog_update_task(tid, "component", "core")
        backlog_update_task(tid, "status", status)

def test_epic_status_shows_counts_and_components(tm_epic_phase):
    _setup(tm_epic_phase)
    out = backlog_epic_status("test-epic")
    assert "test-epic" in out
    assert "1/3" in out                      # done/total progress
    assert "Core" in out or "core" in out    # component line
    assert "Components" in out

def test_epic_status_unknown(tm_epic_phase):
    out = backlog_epic_status("ghost")
    assert "Error" in out and "ghost" in out

def test_epic_status_attention_list(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="A-1", title="blocked one", phase="dev")
    backlog_update_task("A-1", "status", "blocked")
    backlog_update_task("A-1", "blockers", "waiting on CDN creds")
    out = backlog_epic_status("test-epic")
    assert "Attention" in out
    assert "A-1" in out and "CDN creds" in out

def test_epic_status_no_attention_when_clean(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="C-1", title="fine", phase="dev")
    out = backlog_epic_status("test-epic")
    assert "Attention" not in out
