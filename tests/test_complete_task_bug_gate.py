"""backlog_complete_task gates on open Bugs linked via found_in."""
import json
import urllib.request
from pathlib import Path

import pytest

from tests.test_server_api import running_server  # noqa: F401


def _post(base: str, path: str, payload: dict) -> dict:
    url = f"{base}{path}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _setup_task(tmp_path: Path, task_id: str = "test-epic-001") -> str:
    """Ensure PROGRESS.md exists, then create an epic/phase/task and pick it.

    The running_server fixture creates .taskmaster/backlog.yaml but not
    PROGRESS.md (required by regenerate_progress_dashboard).  We create it
    here so backlog_add_* calls that call _mutate_and_save() succeed.
    """
    progress = tmp_path / ".taskmaster" / "PROGRESS.md"
    if not progress.exists():
        progress.write_text("## Changelog\n", encoding="utf-8")

    from taskmaster import backlog_server as _bs

    # Add epic + phase; ignore "already exists" errors so the helper is
    # reusable across tests that share tmp_path (they don't, but defensive).
    r_epic = _bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    if "Error" in r_epic and "already" not in r_epic.lower():
        raise AssertionError(f"add_epic failed: {r_epic}")

    r_phase = _bs.backlog_add_phase(phase_id="dev", name="Development")
    if "Error" in r_phase and "already" not in r_phase.lower():
        raise AssertionError(f"add_phase failed: {r_phase}")

    r_task = _bs.backlog_add_task(title="Demo Task", epic="test-epic", phase="dev", priority="medium", options={"task_id": task_id})
    assert "Error" not in r_task, f"add_task failed: {r_task}"

    # complete_task requires in-progress / in-review / blocked
    r_pick = _bs.backlog_pick_task(task_id)
    assert "Error" not in r_pick, f"pick_task failed: {r_pick}"

    # Spec A gate guard: lane'd tasks can't reach `done` until required gates
    # are satisfied. These tests exercise the BUG gate, not the gate pipeline,
    # so skip every required gate for the assigned lane to isolate that concern.
    _satisfy_gates(task_id)
    return task_id


def _satisfy_gates(task_id: str) -> None:
    """Skip every required gate for a task's lane (audited skip is always allowed)."""
    from taskmaster import backlog_server as _bs
    from taskmaster.taskmaster_v3 import required_gates as _required_gates
    data = _bs._load()
    found = _bs._find_task(data, task_id)
    assert found is not None
    task, _ = found
    lane = task.get("lane")
    if not lane:
        return
    for gate in _required_gates(lane):
        _bs.backlog_skip_gate(task_id, gate, "test setup — isolating bug gate")


def test_complete_task_blocks_when_open_bug_exists(running_server, tmp_path):
    """Refuse to complete a task that has at least one open linked Bug."""
    base, _ = running_server
    task_id = _setup_task(tmp_path, "test-epic-001")

    # Create an open bug linked to this task via found_in
    bug = _post(base, "/api/bugs", {
        "title": "blocking bug",
        "found_in": task_id,
        "discovered_by": "user",
    })
    assert bug["id"].startswith("B-"), f"Unexpected bug response: {bug}"

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_complete_task(task_id=task_id)

    # Should mention open or bug in the refusal message
    assert "open" in out.lower() or "bug" in out.lower(), (
        f"Expected refusal message mentioning open bug, got: {out!r}"
    )

    # Task must NOT have transitioned to done
    data = _bs._load()
    found = _bs._find_task(data, task_id)
    assert found is not None
    task, _ = found
    assert task.get("status") != "done", (
        f"Task should NOT be done when open bugs remain, status={task.get('status')!r}"
    )


def test_complete_task_succeeds_when_no_open_bugs(running_server, tmp_path):
    """Complete a task with no linked bugs — should succeed normally."""
    base, _ = running_server  # noqa: F841 — server needed for fixture setup
    task_id = _setup_task(tmp_path, "test-epic-002")

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_complete_task(task_id=task_id)

    assert "error" not in out.lower(), f"Unexpected error: {out!r}"
    assert "blocked" not in out.lower(), f"Unexpected block: {out!r}"

    # Confirm status is done
    data = _bs._load()
    found = _bs._find_task(data, task_id)
    assert found is not None
    task, _ = found
    assert task.get("status") == "done", (
        f"Expected done, got {task.get('status')!r}"
    )


def test_complete_task_gate_is_case_insensitive(running_server, tmp_path):
    """B-025: a bug filed with found_in in a different case must still gate the task.
    found_in='TEST-EPIC-005' (uppercase) must block completing 'test-epic-005'."""
    base, _ = running_server
    task_id = _setup_task(tmp_path, "test-epic-005")

    bug = _post(base, "/api/bugs", {
        "title": "uppercase-found-in bug",
        "found_in": task_id.upper(),
        "discovered_by": "user",
    })
    assert bug["id"].startswith("B-"), f"Unexpected bug response: {bug}"

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_complete_task(task_id=task_id)

    assert "open" in out.lower() or "bug" in out.lower(), (
        f"Expected case-insensitive gate to block, got: {out!r}"
    )
    data = _bs._load()
    task, _ = _bs._find_task(data, task_id)
    assert task.get("status") != "done", (
        f"Task should not be done despite case-mismatched open bug, status={task.get('status')!r}"
    )


def test_complete_task_archives_fixed_bugs(running_server, tmp_path):
    """Fixed bugs linked via found_in are moved to archive/ post-transition."""
    base, _ = running_server
    task_id = _setup_task(tmp_path, "test-epic-003")

    # Create bug then mark it fixed
    bug = _post(base, "/api/bugs", {
        "title": "fixed bug",
        "found_in": task_id,
        "discovered_by": "user",
    })
    bug_id = bug["id"]

    _post(base, f"/api/bugs/{bug_id}", {"status": "fixed", "fix_commit": "abc123"})

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_complete_task(task_id=task_id)
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"

    # Bug file should have been moved to archive/
    active = tmp_path / ".taskmaster" / "bugs" / f"{bug_id}.md"
    archived = tmp_path / ".taskmaster" / "bugs" / "archive" / f"{bug_id}.md"
    assert archived.exists(), f"Expected archive at {archived}"
    assert not active.exists(), f"Expected active file {active} to be gone"


def test_complete_task_does_not_archive_open_or_shelved_bugs(running_server, tmp_path):
    """Completing with shelved+fixed bugs — only fixed gets archived; shelved stays active."""
    base, _ = running_server
    task_id = _setup_task(tmp_path, "test-epic-004")

    fixed = _post(base, "/api/bugs", {
        "title": "fixed one",
        "found_in": task_id,
        "discovered_by": "user",
    })
    shelved = _post(base, "/api/bugs", {
        "title": "shelved one",
        "found_in": task_id,
        "discovered_by": "user",
    })

    _post(base, f"/api/bugs/{fixed['id']}", {"status": "fixed", "fix_commit": "def456"})
    _post(base, f"/api/bugs/{shelved['id']}", {"status": "shelved"})

    from taskmaster import backlog_server as _bs
    out = _bs.backlog_complete_task(task_id=task_id)
    assert "error" not in out.lower(), f"Unexpected error: {out!r}"

    # fixed bug archived
    assert (tmp_path / ".taskmaster" / "bugs" / "archive" / f"{fixed['id']}.md").exists()
    # shelved bug stays active
    assert (tmp_path / ".taskmaster" / "bugs" / f"{shelved['id']}.md").exists()
