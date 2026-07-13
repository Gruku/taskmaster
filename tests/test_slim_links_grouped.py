"""Plan C Task 13: slim `_get` views emit grouped `links:` block."""
from __future__ import annotations

from pathlib import Path
import yaml

from taskmaster import backlog_server as bs


def _seed_two_tasks(tmp_taskmaster: Path) -> None:
    from taskmaster import taskmaster_v3 as v3

    backlog_path = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    data["epics"] = [{
        "id": "e1", "name": "E", "tasks": [
            {"id": "T-001", "title": "First", "tldr": "First task.", "status": "todo", "epic": "e1", "order": 1.0},
            {"id": "T-002", "title": "Second", "tldr": "Second task.", "status": "todo", "epic": "e1", "order": 2.0},
        ],
    }]
    v3.save_v4(backlog_path, data)

def test_slim_get_task_groups_links_by_type(tmp_taskmaster):
    _seed_two_tasks(tmp_taskmaster)
    bs.backlog_issue_create(title="Bug", severity="P1", tldr="Auth bug.",
                            impact="fixture evidence.", body="repro steps")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="ISS-001", type="fixes")

    out = bs.backlog_get_task("T-001")
    # Grouped block must be present with both types.
    assert "depends_on" in out
    assert "T-002" in out
    assert "fixes" in out
    assert "ISS-001" in out


def test_slim_get_task_no_expanded_tldrs_by_default(tmp_taskmaster):
    _seed_two_tasks(tmp_taskmaster)
    bs.backlog_issue_create(title="Bug", severity="P1", tldr="Auth bug.",
                            impact="fixture evidence.", body="repro steps")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="ISS-001", type="fixes")

    out = bs.backlog_get_task("T-001")
    # Default mode shows bare IDs, not the target tldrs.
    assert "Second task." not in out
    assert "Auth bug." not in out


def test_expand_links_swaps_ids_for_pills(tmp_taskmaster):
    _seed_two_tasks(tmp_taskmaster)
    bs.backlog_issue_create(title="Bug", severity="P1", tldr="Auth bug.",
                            impact="fixture evidence.", body="repro steps")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-001", target="ISS-001", type="fixes")

    out = bs.backlog_get_task("T-001", expand_links=True)
    # Expanded mode includes target tldrs.
    assert "Second task." in out
    assert "Auth bug." in out
