# plugins/taskmaster/tests/test_list_tasks_limit.py
"""tm-audit-022: backlog_list_tasks default limit + overflow footer.

Unfiltered backlog_list_tasks must not dump the whole backlog — default
cap of 50 rows, overflow footer pointing at filters, limit=0 escape
hatch, and active (in-progress/in-review) tasks sorted first so the
truncated view leads with what matters.
"""
from __future__ import annotations

from taskmaster.backlog_server import backlog_add_task, backlog_list_tasks


def _add_tasks(n: int, prefix: str = "T") -> list[str]:
    ids = []
    for i in range(n):
        tid = f"{prefix}-{i:03d}"
        backlog_add_task(
            epic="test-epic",
            task_id=tid,
            title=f"Task {i}",
            phase="dev",
        )
        ids.append(tid)
    return ids


def test_default_caps_at_50_with_overflow_footer(tm_epic_phase):
    _add_tasks(55)
    out = backlog_list_tasks()
    row_count = sum(1 for line in out.splitlines() if line.startswith("- "))
    assert row_count == 50, f"expected 50 rows, got {row_count}"
    assert "5 more" in out
    assert "limit=0" in out


def test_no_footer_when_under_limit(tm_epic_phase):
    _add_tasks(3)
    out = backlog_list_tasks()
    row_count = sum(1 for line in out.splitlines() if line.startswith("- "))
    assert row_count == 3
    assert "more" not in out
    assert "limit=0" not in out


def test_limit_zero_returns_all(tm_epic_phase):
    _add_tasks(55)
    out = backlog_list_tasks(limit=0)
    row_count = sum(1 for line in out.splitlines() if line.startswith("- "))
    assert row_count == 55
    assert "limit=0" not in out


def test_explicit_limit_respected(tm_epic_phase):
    _add_tasks(10)
    out = backlog_list_tasks(limit=4)
    row_count = sum(1 for line in out.splitlines() if line.startswith("- "))
    assert row_count == 4
    assert "6 more" in out


def test_active_tasks_sort_before_todo_and_done(tm_epic_phase):
    _add_tasks(4)
    # Seed statuses directly — transition legality (gates) is not under test here.
    from taskmaster import backlog_server as bs

    data = bs._load()
    statuses = {"T-001": "done", "T-002": "in-progress", "T-003": "in-review"}
    for t in data["epics"][0]["tasks"]:
        if t["id"] in statuses:
            t["status"] = statuses[t["id"]]
    bs._save(data)
    out = backlog_list_tasks()
    rows = [line for line in out.splitlines() if line.startswith("- ")]
    order = [r.split("`")[1] for r in rows]
    assert order.index("T-002") < order.index("T-003") < order.index("T-000")
    assert order.index("T-000") < order.index("T-001"), "done must sort last"


def test_truncated_header_shows_total(tm_epic_phase):
    _add_tasks(55)
    out = backlog_list_tasks()
    assert "55 tasks" in out.splitlines()[0]
