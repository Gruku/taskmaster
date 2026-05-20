"""Plan C Task 13: slim `_get` views emit grouped `links:` block."""
from __future__ import annotations

from pathlib import Path
import yaml

import backlog_server as bs


def _seed_two_tasks(tmp_taskmaster: Path) -> None:
    """Use the tmp_taskmaster fixture and add two seeded tasks via the v3 API."""
    backlog_path = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    data = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    data["epics"] = [{
        "id": "e1", "name": "E", "tasks": [
            {"id": "T-001", "title": "First", "tldr": "First task.", "status": "todo"},
            {"id": "T-002", "title": "Second", "tldr": "Second task.", "status": "todo"},
        ],
    }]
    backlog_path.write_text(yaml.safe_dump(data), encoding="utf-8")


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
