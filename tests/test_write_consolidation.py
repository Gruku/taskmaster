"""Coverage for the tm-audit-020 write-surface consolidation.

Verifies the action-dispatched merged tools (backlog_linear / backlog_note /
backlog_link / backlog_decision) route to the right implementation, that the
field/value param-collapse on the *_update tools behaves like
backlog_update_task, and that add_task / handover_create honour the options dict.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs


# ── MCP surface shape ─────────────────────────────────────────────────────────

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _list_tool_names() -> list[str]:
    spec = importlib.util.spec_from_file_location("bs_surface", _PLUGIN_ROOT / "backlog_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tools = asyncio.run(mod.mcp.list_tools())
    return sorted(t.name for t in tools)


def test_merged_tools_present_and_old_verbs_gone():
    names = _list_tool_names()
    # New consolidated entry points exist.
    for merged in ("backlog_linear", "backlog_note", "backlog_link", "backlog_decision"):
        assert merged in names, f"{merged} not registered"
    # decision_create stays a distinct tool (heavy schema kept separate).
    assert "backlog_decision_create" in names
    # The per-verb registrations collapsed away.
    dead = [
        "backlog_linear_probe", "backlog_linear_link", "backlog_linear_list",
        "backlog_note_create", "backlog_note_list", "backlog_note_archive",
        "backlog_link_create", "backlog_link_query", "backlog_link_validate",
        "backlog_decision_list", "backlog_decision_get", "backlog_decision_resolve",
    ]
    for d in dead:
        assert d not in names, f"{d} should no longer be a registered tool"


# ── backlog_note dispatcher ───────────────────────────────────────────────────

def test_note_dispatcher_roundtrip(tmp_taskmaster):
    out = bs.backlog_note(action="create", text="alpha")
    assert "NOTE-001" in out
    bs.backlog_note(action="create", text="beta", pinned=True)
    listing = bs.backlog_note(action="list")
    assert "NOTE-001" in listing and "NOTE-002" in listing
    got = bs.backlog_note(action="get", note_id="NOTE-001")
    assert "alpha" in got
    bs.backlog_note(action="update", note_id="NOTE-001", text="alpha2")
    assert "alpha2" in bs.backlog_note(action="get", note_id="NOTE-001")
    arch = bs.backlog_note(action="archive", note_id="NOTE-001")
    assert "archived" in arch.lower()
    assert "NOTE-001" not in bs.backlog_note(action="list")


# ── backlog_link dispatcher ───────────────────────────────────────────────────

@pytest.fixture
def link_dir(tmp_path, monkeypatch):
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo"},
            {"id": "T-002", "title": "Second", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    return d


def test_link_dispatcher_create_query_remove(link_dir):
    out = bs.backlog_link(action="create", source="T-001", target="T-002", type="depends_on")
    assert "ok" in out.lower()
    edges = json.loads(bs.backlog_link(action="query", source="T-001"))
    assert any(e["target"] == "T-002" and e["type"] == "depends_on" for e in edges)
    val = json.loads(bs.backlog_link(action="validate"))
    assert val["asymmetric"] == []
    rem = bs.backlog_link(action="remove", source="T-001", target="T-002")
    assert "removed" in rem.lower()


def test_link_dispatcher_unknown_action(link_dir):
    out = bs.backlog_link(action="bogus")  # type: ignore[arg-type]
    assert "unknown action" in out.lower()


# ── backlog_decision dispatcher (create stays separate) ───────────────────────

def test_decision_dispatcher_lifecycle(tmp_taskmaster):
    created = bs.backlog_decision_create(title="Pick a lane", options=["a", "b"], recommendation=1)
    assert "DEC-001" in created
    listing = bs.backlog_decision(action="list")
    assert "DEC-001" in listing
    got = bs.backlog_decision(action="get", decision_id="DEC-001")
    assert "Pick a lane" in got
    bs.backlog_decision(action="update", decision_id="DEC-001", title="Pick the lane")
    assert "Pick the lane" in bs.backlog_decision(action="get", decision_id="DEC-001")
    resolved = bs.backlog_decision(action="resolve", decision_id="DEC-001", resolved_with=2)
    assert "resolved" in resolved.lower()


def test_decision_dispatcher_drop(tmp_taskmaster):
    bs.backlog_decision_create(title="Throwaway", options=["x", "y"])
    dropped = bs.backlog_decision(action="drop", decision_id="DEC-001", reason="obsolete")
    assert "dropped" in dropped.lower()


def test_decision_resolve_requires_resolved_with(tmp_taskmaster):
    bs.backlog_decision_create(title="Needs pick", options=["x", "y"])
    out = bs.backlog_decision(action="resolve", decision_id="DEC-001")
    assert "resolved_with" in out


# ── backlog_linear dispatcher ─────────────────────────────────────────────────

def test_linear_dispatcher_probe_missing_token(tmp_taskmaster, monkeypatch):
    monkeypatch.delenv("NO_SUCH_LINEAR_TOKEN", raising=False)
    out = json.loads(bs.backlog_linear(action="probe", token_env="NO_SUCH_LINEAR_TOKEN"))
    assert "error" in out and "NO_SUCH_LINEAR_TOKEN" in out["error"]


def test_linear_dispatcher_list_empty(tmp_taskmaster):
    out = json.loads(bs.backlog_linear(action="list"))
    assert out == {"trackers": []}


def test_linear_dispatcher_unknown_action(tmp_taskmaster):
    out = json.loads(bs.backlog_linear(action="bogus"))  # type: ignore[arg-type]
    assert "unknown action" in out["error"].lower()


# ── field/value param collapse: issue_update ──────────────────────────────────

def test_issue_update_field_value_status_lifecycle(tm_epic_phase):
    task = bs.backlog_add_task(title="fix it", epic="test-epic", phase="dev")
    task_id = task.split("`")[1]
    iss = bs.backlog_issue_create(title="Recurring crash", severity="P1", impact="recurs across sessions")
    iid = next(t for t in iss.replace("\n", " ").split() if t.startswith("ISS-"))
    # fixed without fixed_in_task → rejected
    err = bs.backlog_issue_update(iid, "status", "fixed")
    assert "error" in err.lower()
    # set companion first, then status → accepted (merged-state validation)
    bs.backlog_issue_update(iid, "fixed_in_task", task_id)
    ok = bs.backlog_issue_update(iid, "status", "fixed")
    assert "Error" not in ok
    from taskmaster.taskmaster_v3 import read_issue
    fm, _ = read_issue(tm_epic_phase / ".taskmaster" / "backlog.yaml", iid)
    assert fm["status"] == "fixed" and fm.get("resolved")


def test_issue_update_rejects_unknown_field(tm_epic_phase):
    iss = bs.backlog_issue_create(title="x", severity="P2", impact="systemic")
    iid = next(t for t in iss.replace("\n", " ").split() if t.startswith("ISS-"))
    out = bs.backlog_issue_update(iid, "bogus", "y")
    assert "not allowed" in out


def test_issue_update_list_field_csv(tm_epic_phase):
    iss = bs.backlog_issue_create(title="x", severity="P2", impact="systemic")
    iid = next(t for t in iss.replace("\n", " ").split() if t.startswith("ISS-"))
    bs.backlog_issue_update(iid, "components", "viewer, mcp-server")
    from taskmaster.taskmaster_v3 import read_issue
    fm, _ = read_issue(tm_epic_phase / ".taskmaster" / "backlog.yaml", iid)
    assert fm["components"] == ["viewer", "mcp-server"]


# ── field/value param collapse: bug_update ────────────────────────────────────

def test_bug_update_field_value_fix_lifecycle(tmp_taskmaster):
    bs.backlog_bug_create(title="Off-by-one in parser loop")
    # fixed without fix_commit → rejected
    err = bs.backlog_bug_update("B-001", "status", "fixed")
    assert "error" in err.lower()
    bs.backlog_bug_update("B-001", "fix_commit", "abc1234")
    ok = bs.backlog_bug_update("B-001", "status", "fixed")
    assert "status=fixed" in ok


def test_bug_update_rejects_bad_status(tmp_taskmaster):
    bs.backlog_bug_create(title="Some defect here")
    out = bs.backlog_bug_update("B-001", "status", "nonsense")
    assert "status must be one of" in out


# ── field/value param collapse: idea_update ───────────────────────────────────

def test_idea_update_archived_bool_parse(tmp_taskmaster):
    bs.backlog_idea_create(title="archive me")
    out = bs.backlog_idea_update("IDEA-001", "archived", "true")
    assert "IDEA-001" in out
    from taskmaster.taskmaster_v3 import read_idea
    fm, _ = read_idea(tmp_taskmaster / ".taskmaster" / "backlog.yaml", "IDEA-001")
    assert fm.get("archived") is True


def test_idea_update_archived_rejects_non_bool(tmp_taskmaster):
    bs.backlog_idea_create(title="x")
    out = bs.backlog_idea_update("IDEA-001", "archived", "maybe")
    assert "true" in out and "false" in out


# ── options-dict collapse: add_task ───────────────────────────────────────────

def test_add_task_options_dict(tm_epic_phase):
    out = bs.backlog_add_task(
        title="Deep task", epic="test-epic", phase="dev",
        options={"task_id": "test-epic-042", "stage": "3", "anchors": "src/**,docs/**"},
    )
    assert "test-epic-042" in out
    from taskmaster.backlog_server import _find_task, _load
    task, _ = _find_task(_load(), "test-epic-042")
    assert task["stage"] == 3
    assert task["anchors"] == ["src/**", "docs/**"]


def test_add_task_options_bad_stage(tm_epic_phase):
    out = bs.backlog_add_task(
        title="x", epic="test-epic", phase="dev", options={"stage": "notanint"},
    )
    assert "stage must be an integer" in out


# ── options-dict collapse: handover_create ────────────────────────────────────

def test_handover_create_options_git_context(tmp_taskmaster):
    out = bs.backlog_handover_create(
        tldr="wrap up",
        options={"branch": "feature/x", "tip_commit": "deadbee"},
    )
    assert "Handover written" in out
    from taskmaster.taskmaster_v3 import read_handover, list_handover_ids
    hid = list_handover_ids(tmp_taskmaster / ".taskmaster" / "backlog.yaml")[0]
    fm, _ = read_handover(tmp_taskmaster / ".taskmaster" / "backlog.yaml", hid)
    assert fm["branch"] == "feature/x"
    assert fm["tip_commit"] == "deadbee"
