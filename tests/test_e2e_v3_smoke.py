"""End-to-end smoke tests for the v3 plugin surface.

Covers the programmatic portion of the dogfood walkthrough described in
v3-release-005: fresh-project init → v3 migration → typical write/read flows.
Manual portions (plugin install, browser viewer, AskUserQuestion) are excluded.

Each test is hermetic: tmp_path + ROOT/CONFIG monkeypatching, auto-cleaned by pytest.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# backlog_server imports fastmcp at module load; mock it if not already done so
# the function objects remain directly callable without a running MCP server.
if "backlog_server" not in sys.modules:
    def _passthrough_tool():
        def decorator(fn):
            return fn
        return decorator
    fake_fastmcp = MagicMock()
    fake_fastmcp.FastMCP.return_value.tool = _passthrough_tool
    sys.modules.setdefault("fastmcp", fake_fastmcp)

from taskmaster import backlog_server  # noqa: E402
from taskmaster import taskmaster_v3 as v3  # noqa: E402


# ── Setup helpers ──────────────────────────────────────────────────────────────


def _redirect(monkeypatch, tmp_path: Path, bp: Path | None = None) -> Path:
    """Patch backlog_server so ROOT and all path resolution point to tmp_path.

    Returns the backlog path that will be used.
    """
    if bp is None:
        bp = tmp_path / ".taskmaster" / "backlog.yaml"
    cfg = tmp_path / ".taskmaster" / "taskmaster.json"
    legacy_cfg = tmp_path / ".claude" / "taskmaster.json"
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", cfg)
    monkeypatch.setattr(backlog_server, "LEGACY_CONFIG_PATH", legacy_cfg)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    monkeypatch.setattr(backlog_server, "_progress_path", lambda: bp.parent / "PROGRESS.md")
    return bp


def _v3_backlog_with_epic_and_task(tmp_path: Path) -> tuple[Path, str, str]:
    """Write a minimal v3 backlog with 1 epic + 1 task; return (bp, epic_id, task_id)."""
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    epic_id = "smoke"
    task_id = "smoke-001"
    data = {
        "meta": {"project": "smoke-test", "schema_version": 3, "updated": "2026-05-05"},
        "context": {},
        "epics": [
            {
                "id": epic_id,
                "name": "Smoke Epic",
                "status": "active",
                "tasks": [
                    {
                        "id": task_id,
                        "title": "First smoke task",
                        "status": "todo",
                        "priority": "medium",
                        "phase": "dev",
                        "created": "2026-05-05T10:00",
                        "last_referenced": "2026-05-05T10:00",
                        "notes": "",
                    }
                ],
            }
        ],
        "phases": [{"id": "dev", "name": "Dev", "status": "active", "order": 1}],
        "handovers": [],
        "issues": [],
    }
    v3.save_v3(bp, data)
    # Write PROGRESS.md so complete_task / pick_task don't error
    progress = bp.parent / "PROGRESS.md"
    progress.write_text("# smoke-test Progress\n\n## Dashboard\n\n---\n\n## Changelog\n", encoding="utf-8")
    return bp, epic_id, task_id


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_fresh_project_init_v2_then_migrate_to_v3(tmp_path, monkeypatch):
    """Init → v2 backlog → add epic + tasks → migrate → idempotent migrate."""
    monkeypatch.chdir(tmp_path)
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    _redirect(monkeypatch, tmp_path, bp)

    # 1. Init with schema v2
    result = backlog_server.backlog_init(project_name="smoke-test", location="tracked", schema_version=2)
    assert "Initialized" in result
    assert bp.exists()
    raw = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert raw["meta"]["schema_version"] == 2

    # 2. Add a phase (required for tasks), then an epic and 3 tasks via MCP tools
    backlog_server.backlog_add_phase(phase_id="dev", name="Dev")
    backlog_server.backlog_add_epic(epic_id="core", name="Core Features", done_when="all 3 tasks ship")
    for i, title in enumerate(["Task Alpha", "Task Beta", "Task Gamma"], 1):
        r = backlog_server.backlog_add_task(title=title, epic="core", priority="medium", phase="dev")
        assert "Error" not in r

    raw2 = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert len(raw2["epics"][0]["tasks"]) == 3

    # 3. Migrate to v3 — must redirect _backlog_path to same .taskmaster/backlog.yaml
    r3 = backlog_server.backlog_migrate_v3()
    assert "Migrated v2" in r3 or "v3" in r3.lower()
    assert v3.detect_schema_version(yaml.safe_load(bp.read_text(encoding="utf-8"))) == v3.SCHEMA_V3

    # 4. Idempotent: second migration returns "already on v3" path
    r4 = backlog_server.backlog_migrate_v3()
    assert "already" in r4.lower() or "v3" in r4.lower()


def test_fresh_v3_init_skips_migration(tmp_path, monkeypatch):
    """backlog_init with schema_version=3 → immediately v3, per-task files on add_task."""
    monkeypatch.chdir(tmp_path)
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    _redirect(monkeypatch, tmp_path, bp)

    result = backlog_server.backlog_init(project_name="smoke-v3", location="tracked", schema_version=3)
    assert "Initialized" in result
    assert bp.exists()
    assert v3.detect_schema_version(yaml.safe_load(bp.read_text(encoding="utf-8"))) == v3.SCHEMA_V3

    backlog_server.backlog_add_phase(phase_id="dev", name="Dev")
    backlog_server.backlog_add_epic(epic_id="feat", name="Features", done_when="auth refactor ships")
    r = backlog_server.backlog_add_task(
        title="Auth refactor",
        epic="feat",
        priority="high",
        notes="Detailed notes that become a per-task file.",
        phase="dev",
    )
    assert "Error" not in r

    # The task should exist; v3 backlog should still detect as v3
    data = v3.load_v3(bp)
    assert v3.detect_schema_version(data) == v3.SCHEMA_V3
    tasks = data["epics"][0]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Auth refactor"


def test_v3_handover_create_read_list(tmp_path, monkeypatch):
    """Create handover → get → latest → list filtered by task_id."""
    monkeypatch.chdir(tmp_path)
    bp, _epic_id, task_id = _v3_backlog_with_epic_and_task(tmp_path)
    _redirect(monkeypatch, tmp_path, bp)

    # 1. Create
    result = backlog_server.backlog_handover_create(
        tldr="Smoke session complete",
        next_action="Resume auth work",
        task_ids=[task_id],
        session_kind="end-of-day",
        body="## Decisions\n- chose X\n",
    )
    assert "Handover written" in result
    hid = result.split("\n")[0].split(": ", 1)[1].strip()

    # 2. Get — frontmatter + body (verbose=True to include body content)
    got = backlog_server.backlog_handover_get(hid, verbose=True)
    assert "Smoke session complete" in got
    assert "chose X" in got
    assert "---" in got

    # 3. Thread resume — tldr and next_action surface via the newest handover
    latest = backlog_server.backlog_thread_resume(hid)
    assert "Smoke session complete" in latest
    assert "Resume auth work" in latest

    # 4. List filtered by task_id
    listed = backlog_server.backlog_handover_list(task_id=task_id)
    assert hid in listed


def test_v3_issue_lifecycle(tmp_path, monkeypatch):
    """Create issue → get → update status → fixed requires fixed_in_task → list filters."""
    monkeypatch.chdir(tmp_path)
    bp, _epic_id, task_id = _v3_backlog_with_epic_and_task(tmp_path)
    _redirect(monkeypatch, tmp_path, bp)

    # 1. Create
    result = backlog_server.backlog_issue_create(
        title="login broken",
        severity="P1",
        impact="users locked out",
        components=["auth"],
    )
    assert "Issue created" in result
    iss_id = result.split(":")[1].strip().split()[0]  # "ISS-001"

    # 2. Get — slim view has title; verbose=True for YAML fences
    got = backlog_server.backlog_issue_get(iss_id)
    assert "login broken" in got
    got_verbose = backlog_server.backlog_issue_get(iss_id, verbose=True)
    assert "---" in got_verbose

    # 3. Update status to investigating
    up = backlog_server.backlog_issue_update(iss_id, "status", "investigating")
    assert "Error" not in up
    assert "investigating" in up

    # 4. Fixed WITHOUT fixed_in_task → error
    err = backlog_server.backlog_issue_update(iss_id, "status", "fixed")
    assert "Error" in err or "error" in err.lower()

    # 5. Fixed WITH fixed_in_task → success + resolved date stamped
    backlog_server.backlog_issue_update(iss_id, "fixed_in_task", task_id)
    ok = backlog_server.backlog_issue_update(iss_id, "status", "fixed")
    assert "Error" not in ok
    fm, _ = v3.read_issue(bp, iss_id)
    assert fm["status"] == "fixed"
    assert fm.get("resolved")

    # 6. issue_list(status="open") → fixed issue excluded
    listed = backlog_server.backlog_issue_list(status="open")
    assert iss_id not in listed


def test_v3_pick_complete_full_lifecycle(tmp_path, monkeypatch):
    """pick_task → in-progress + started stamped → complete_task → done + PROGRESS entry."""
    monkeypatch.chdir(tmp_path)
    bp, _epic_id, task_id = _v3_backlog_with_epic_and_task(tmp_path)
    _redirect(monkeypatch, tmp_path, bp)

    # 1. Pick
    picked = backlog_server.backlog_pick_task(task_id)
    assert "Error" not in picked
    assert task_id in picked

    data = v3.load_v3(bp)
    task, _ = v3.load_v3(bp)["epics"][0]["tasks"][0], None
    task = data["epics"][0]["tasks"][0]
    assert task["status"] == "in-progress"
    assert task.get("started")

    # 2. Complete
    completed = backlog_server.backlog_complete_task(
        task_id=task_id,
        session_title="Smoke",
        done="implemented X",
        decisions="None",
        issues="None",
        target_status="done",
    )
    assert "Error" not in completed
    assert "Completed" in completed or task_id in completed

    data2 = v3.load_v3(bp)
    task2 = data2["epics"][0]["tasks"][0]
    assert task2["status"] == "done"
    assert task2.get("completed")

    # 3. PROGRESS.md should have a session entry
    progress = bp.parent / "PROGRESS.md"
    if progress.exists():
        text = progress.read_text(encoding="utf-8")
        assert "Smoke" in text or "smoke" in text.lower()


def test_e2e_ideas_full_lifecycle(tmp_path, monkeypatch):
    """Capture → list → filter → archive → promote round-trip via the MCP wrappers."""
    monkeypatch.chdir(tmp_path)
    bp, _epic_id, _task_id = _v3_backlog_with_epic_and_task(tmp_path)
    _redirect(monkeypatch, tmp_path, bp)

    # Sharp capture (path C — auto-log style)
    out1 = backlog_server.backlog_idea_create(
        title="Per-task spike budgets",
        body="track effort vs estimate",
        status="exploring",
        tags=["perf"],
        created_by="Claude",
    )
    assert "IDEA-001" in out1

    # Fuzzy capture (simulating end-session committing an <idea-candidate>)
    out2 = backlog_server.backlog_idea_create(
        title="Auto-tag from git diff",
        body="link ideas to recent files",
        status="candidate",
        created_by="Claude",
    )
    assert "IDEA-002" in out2

    # Listing returns both
    listed = backlog_server.backlog_idea_list()
    assert "IDEA-002" in listed
    assert "IDEA-001" in listed

    # Filter by status
    only_candidate = backlog_server.backlog_idea_list(status="candidate")
    assert "IDEA-002" in only_candidate
    assert "IDEA-001" not in only_candidate

    # Archive idea 1
    arch = backlog_server.backlog_idea_update("IDEA-001", "archived", "true")
    assert "IDEA-001" in arch
    listed_default = backlog_server.backlog_idea_list()
    assert "IDEA-001" not in listed_default  # archived excluded by default
    listed_with_arch = backlog_server.backlog_idea_list(archived=True)
    assert "IDEA-001" in listed_with_arch
    assert "IDEA-002" in listed_with_arch

    # Promote idea 2
    backlog_server.backlog_idea_update("IDEA-002", "promoted_to", "T-XYZ")
    prom = backlog_server.backlog_idea_update("IDEA-002", "archived", "true")
    assert "IDEA-002" in prom

    # Verify on disk
    idea2_text = (tmp_path / ".taskmaster" / "ideas" / "IDEA-002.md").read_text()
    assert "promoted_to: T-XYZ" in idea2_text
    assert "archived: true" in idea2_text

    # IDEAS.md index reflects archive marks
    idx = (tmp_path / ".taskmaster" / "ideas" / "IDEAS.md").read_text()
    assert "~~Per-task spike budgets~~" in idx
    assert "~~Auto-tag from git diff~~" in idx
