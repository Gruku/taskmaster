# tests/test_human_action.py
"""tm 4.5.0 human-gate: human_action field + in-review enforcement."""
import re
from taskmaster import backlog_server as _bs


def _t(lane="express"):
    tid = re.search(r"[a-z0-9-]+-\d{3}",
                    _bs.backlog_add_task("s", epic="test-epic", phase="dev", priority="medium")).group(0)
    _bs.backlog_update_task(tid, "lane", lane)
    return tid


def test_human_action_field_updatable_and_persisted(tm_epic_phase):
    tid = _t()
    out = _bs.backlog_update_task(tid, "human_action", "add OPENAI_API_KEY to .env")
    assert "Error" not in out
    task, _ = _bs._find_task(_bs._load(), tid)
    assert task["human_action"] == "add OPENAI_API_KEY to .env"


def test_human_action_visible_in_get_task(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "human_action", "rotate the deploy token")
    out = _bs.backlog_get_task(tid)
    assert "rotate the deploy token" in out


def test_human_action_visible_in_list_tasks(tm_epic_phase):
    tid = _t()
    _bs.backlog_update_task(tid, "human_action", "install the CUDA driver")
    out = _bs.backlog_list_tasks()
    assert "install the CUDA driver" in out
