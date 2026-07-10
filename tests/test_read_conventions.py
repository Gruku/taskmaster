"""tm-audit-007 — unified MCP read conventions.

Locks the five convention axes so per-family drift can't return:
  1. Slim toggle is named `verbose` everywhere (never `summary`).
  2. Every list caps at `limit` (default 50; 0 = no cap) with an overflow footer.
  3. Error returns use the `Error: ` prefix (not lowercase `error:`).
  4. Every read entity has a SLIM_FIELDS projection (note included).
  5. expand_links is honored in BOTH slim and verbose reads, uniformly.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server as bs  # noqa: E402
from taskmaster.taskmaster_v3 import SLIM_FIELDS  # noqa: E402


def _setup(tmp_path, monkeypatch):
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    bp = tm / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-07-10"}, "epics": []}))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "_backlog_path", lambda: bp)
    return bp


# ── Axis 1: verbose naming ───────────────────────────────────────────────────

def test_idea_list_uses_verbose_not_summary():
    params = inspect.signature(bs.backlog_idea_list).parameters
    assert "verbose" in params, "idea_list must expose `verbose`"
    assert "summary" not in params, "idea_list must not expose the old `summary` toggle"
    assert params["verbose"].default is False, "verbose must default to False (slim)"


def test_idea_list_verbose_includes_body(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    bs.backlog_idea_create(title="Alpha", body="BODY-MARKER-ALPHA")
    slim = bs.backlog_idea_list()
    assert "BODY-MARKER-ALPHA" not in slim
    verbose = bs.backlog_idea_list(verbose=True)
    assert "BODY-MARKER-ALPHA" in verbose


# ── Axis 2: limit + overflow footer ──────────────────────────────────────────

def test_idea_list_overflow_footer_and_limit_zero(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for i in range(5):
        bs.backlog_idea_create(title=f"Idea {i}")
    capped = bs.backlog_idea_list(limit=2)
    assert "…3 more ideas" in capped, capped
    # limit=0 means no cap → all five, no footer.
    allout = bs.backlog_idea_list(limit=0)
    assert "more ideas" not in allout
    assert len([l for l in allout.splitlines() if l.startswith("- ")]) == 5


def test_every_list_tool_takes_limit():
    """Every list surface must accept a `limit` param (footer convention)."""
    list_tools = [
        bs.backlog_list_tasks,
        bs.backlog_issue_list,
        bs.backlog_bug_list,
        bs.backlog_idea_list,
        bs.backlog_handover_list,
        bs.backlog_area_list,
        bs.backlog_note_list,
        bs.backlog_decision_list,
    ]
    for fn in list_tools:
        assert "limit" in inspect.signature(fn).parameters, f"{fn.__name__} missing limit"


def test_list_default_limit_is_unified():
    for fn in (
        bs.backlog_issue_list,
        bs.backlog_bug_list,
        bs.backlog_idea_list,
        bs.backlog_handover_list,
        bs.backlog_area_list,
        bs.backlog_decision_list,
        bs.backlog_note_list,
    ):
        default = inspect.signature(fn).parameters["limit"].default
        assert default == bs.DEFAULT_LIST_LIMIT, f"{fn.__name__} default limit={default}"


# ── Axis 3: error shape ──────────────────────────────────────────────────────

def test_link_query_error_uses_capitalized_prefix(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = bs.backlog_link_query(source="T-999")
    assert out.startswith("Error: "), out


def test_viewer_prefs_set_error_uses_capitalized_prefix(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = bs.viewer_prefs_set("{not valid json")
    assert out.startswith("Error: "), out


# ── Axis 4: note SLIM_FIELDS ─────────────────────────────────────────────────

def test_note_has_slim_projection():
    assert "note" in SLIM_FIELDS
    assert "id" in SLIM_FIELDS["note"]


# ── Axis 5: expand_links uniform across slim and verbose ─────────────────────

def test_issue_expand_links_honored_in_verbose(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    # Seed an epic+task inline so the tldr index has a pill to expand to.
    bp.write_text(yaml.safe_dump({
        "meta": {"updated": "2026-07-10"},
        "epics": [{
            "id": "e1", "name": "Epic One", "status": "planned",
            "tasks": [{"id": "tsk-1", "title": "Task One", "status": "todo",
                       "tldr": "TASK-TLDR-MARKER"}],
        }],
    }))
    created = bs.backlog_issue_create(title="An issue", severity="P2",
                                      evidence="seen twice", related_tasks=["tsk-1"])
    iid = created.split("Issue created:")[1].split("(")[0].strip()

    slim = bs.backlog_issue_get(iid, expand_links=True)
    verbose = bs.backlog_issue_get(iid, verbose=True, expand_links=True)
    assert "TASK-TLDR-MARKER" in slim, slim
    assert "TASK-TLDR-MARKER" in verbose, verbose
    # Without expand_links, verbose shows the bare id, not the tldr.
    plain = bs.backlog_issue_get(iid, verbose=True)
    assert "TASK-TLDR-MARKER" not in plain
