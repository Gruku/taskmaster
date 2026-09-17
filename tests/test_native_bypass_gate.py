"""User intent: mechanically forbid native-routed tools from reaching the legacy
store, its whole-backlog dict or a projection scan (N08 bypass gate), and ratchet
the count of public tools that still have no native route down to zero.

Three checks, each verified to fail on the thing it guards:
- dynamic: every routed tool/action runs on a native store with the legacy
  entry points and projection-directory reads rigged to raise;
- static: no routing module names a legacy load/transaction/scan entry point
  outside the explicit export allowlist;
- ledger: the unrouted normal-path inventory is an exact, reviewed list.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sys

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native_routing import registry
from native_twins import make_twins

ROUTING_DIR = Path(bs.__file__).resolve().parent / "native_routing"
EXPORT_FILE = ROUTING_DIR / "projection.py"
INVENTORY = Path(__file__).resolve().parent / "fixtures" / "native_contracts.json"


class BypassViolation(BaseException):
    """BaseException so no adapter `except Exception` can swallow it."""


# ── Dynamic gate ────────────────────────────────────────────────────────────

EXERCISES = {
    ("backlog_note", "create"): lambda: bs.backlog_note(action="create", text="gate note"),
    ("backlog_note", "list"): lambda: bs.backlog_note(action="list", include_archived=True),
    ("backlog_note", "get"): lambda: bs.backlog_note(action="get", note_id="NOTE-001"),
    ("backlog_note", "update"): lambda: bs.backlog_note(action="update", note_id="NOTE-001", text="edited"),
    ("backlog_note", "archive"): lambda: bs.backlog_note(action="archive", note_id="NOTE-001"),
}


def routed_pairs() -> set:
    registry._load_families()
    pairs = set()
    for tool in registry.ADAPTERS:
        if tool in registry.ACTIONS:
            pairs.update((tool, action) for action in registry.ACTIONS[tool])
        else:
            pairs.add((tool, None))
    return pairs


def _in_export(frame) -> bool:
    while frame is not None:
        if Path(frame.f_code.co_filename).resolve() == EXPORT_FILE:
            return True
        frame = frame.f_back
    return False


@pytest.fixture
def rigged(tmp_path, monkeypatch):
    """A native project whose legacy entry points and projection reads raise."""
    def seed():
        bs.backlog_note(action="create", text="seeded")
    twins = make_twins(tmp_path, monkeypatch, seed)
    project = twins.native
    backlog_dir = (project / ".taskmaster").resolve()
    violations = []

    def forbid(label):
        def refuse(*args, **kwargs):
            violations.append(label)
            raise BypassViolation(label)
        return refuse

    with twins.at(project):
        for owner, name in [(bs, "_load"), (bs, "_load_snapshot"), (bs, "_store"), (bs, "_store_for"),
                            (bs, "_transaction"), (bs, "_store_tx"),
                            (store, "load_dict"), (store, "transaction_dict"), (store, "transaction"),
                            (store, "open_store"), (store.Store, "load_dict"),
                            (store.Store, "load_dict_with_identity"), (store.Store, "transaction_dict"),
                            (store.Store, "transaction")]:
            monkeypatch.setattr(owner, name, forbid(f"{getattr(owner, '__name__', owner)}.{name}"))

        def projection_path(target) -> bool:
            try:
                path = Path(target).resolve()
                relative = path.relative_to(backlog_dir)
            except (TypeError, ValueError, OSError):
                return False
            return not relative.parts or relative.parts[0] != "local"

        def guard(label, real, *, method=False, write_ok=False):
            def check(target, args, kwargs):
                mode = args[0] if args else kwargs.get("mode", "r")
                if write_ok and any(flag in str(mode) for flag in "wax+"):
                    return
                if projection_path(target) and not _in_export(sys._getframe(2)):
                    violations.append(f"{label} {target}")
                    raise BypassViolation(f"{label} {target}")
            if method:
                def wrapper(self, *args, **kwargs):
                    check(self, args, kwargs)
                    return real(self, *args, **kwargs)
            else:
                def wrapper(target=".", *args, **kwargs):
                    check(target, args, kwargs)
                    return real(target, *args, **kwargs)
            return wrapper

        monkeypatch.setattr(os, "listdir", guard("os.listdir", os.listdir))
        monkeypatch.setattr(os, "scandir", guard("os.scandir", os.scandir))
        monkeypatch.setattr(os, "walk", guard("os.walk", os.walk))
        for name in ("iterdir", "glob", "rglob", "read_text", "read_bytes"):
            monkeypatch.setattr(Path, name, guard(f"Path.{name}", getattr(Path, name), method=True))
        monkeypatch.setattr(Path, "open", guard("Path.open", Path.open, method=True, write_ok=True))
        yield violations


def test_every_routed_tool_has_a_gate_exercise():
    assert set(EXERCISES) == routed_pairs(), (
        "a routed tool/action without a bypass-gate exercise (or a stale exercise)")


@pytest.mark.parametrize("pair", sorted(EXERCISES, key=str), ids=lambda p: f"{p[0]}.{p[1]}")
def test_routed_tool_never_reaches_the_legacy_store_or_scans_the_projection(rigged, pair):
    answer = EXERCISES[pair]()
    assert rigged == [], f"{pair} bypassed the native core: {rigged}"
    assert "not yet routed" not in str(answer)


def test_the_rig_catches_a_legacy_read_and_a_projection_scan(rigged):
    with pytest.raises(BypassViolation):
        bs._load()
    with pytest.raises(BypassViolation):
        os.listdir(Path.cwd() / ".taskmaster" / "notes")
    with pytest.raises(BypassViolation):
        (Path.cwd() / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8")
    assert len(rigged) == 3
    # Machine-local state is not the projection and stays readable.
    (Path.cwd() / ".taskmaster" / "local" / "PROGRESS.md").read_text(encoding="utf-8")


def test_the_rig_catches_a_legacy_tool_body_run_on_a_native_store(rigged):
    with pytest.raises(BypassViolation):
        bs.backlog_note_list.__wrapped__() if hasattr(bs.backlog_note_list, "__wrapped__") else bs.backlog_note_list()


# ── Static gate ─────────────────────────────────────────────────────────────

FORBIDDEN_NAMES = {
    "_load", "_load_snapshot", "load_dict", "load_dict_with_identity", "transaction_dict",
    "_transaction", "_transactional", "open_store", "_store", "_store_for", "_store_tx",
    "_tx_rows", "_tx_doc", "_dict_rows", "_dict_row", "_mutate_and_save",
    "glob", "rglob", "iterdir", "listdir", "scandir", "walk", "detect_dominant_crlf",
}
# (module, name): explicit export/maintenance operations the gate permits.
ALLOWLIST = {("projection.py", "detect_dominant_crlf")}


def _references(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.ImportFrom):
            yield from (alias.name for alias in node.names)


def test_no_routing_module_names_a_legacy_entry_point():
    offenders = sorted({(path.name, name) for path in ROUTING_DIR.glob("*.py")
                        for name in _references(path) if name in FORBIDDEN_NAMES} - ALLOWLIST)
    assert offenders == []


def test_the_static_scan_sees_a_forbidden_reference(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("from taskmaster import backlog_server as bs\n\ndef f():\n    return bs._load()\n",
                      encoding="utf-8")
    assert "_load" in set(_references(sample))


# ── Fallback ledger ─────────────────────────────────────────────────────────

NORMAL_PATH = {"targeted-query", "simple-command", "composite-command"}


def inventory_pairs() -> dict:
    tools = json.loads(INVENTORY.read_text(encoding="utf-8"))["tools"]
    pairs = {}
    for name, entry in tools.items():
        if entry.get("actions"):
            for action, targets in entry["actions"].items():
                pairs[(name, action)] = targets[0]["category"]
        else:
            pairs[(name, None)] = entry["category"]
    return pairs


# The reviewed list of normal-path tools/actions with no native route yet. Routing
# a family removes its entries here; the N08 exit is an empty set.
UNROUTED_NORMAL_PATH = {
    ("backlog_add_epic", None), ("backlog_add_phase", None), ("backlog_add_task", None),
    ("backlog_advance_phase", None), ("backlog_archive_epic", None), ("backlog_archive_task", None),
    ("backlog_area_create", None), ("backlog_area_get", None), ("backlog_area_list", None),
    ("backlog_area_update", None), ("backlog_batch_preview", None), ("backlog_batch_update", None),
    ("backlog_blast_radius", None), ("backlog_bug_archive", None), ("backlog_bug_create", None),
    ("backlog_bug_get", None), ("backlog_bug_list", None), ("backlog_bug_pattern_scan", None),
    ("backlog_bug_promote", None), ("backlog_bug_update", None), ("backlog_clear_gate", None),
    ("backlog_clear_spec_review", None), ("backlog_complete_task", None),
    ("backlog_continuity_items", None), ("backlog_decision", "drop"), ("backlog_decision", "get"),
    ("backlog_decision", "list"), ("backlog_decision", "resolve"), ("backlog_decision", "update"),
    ("backlog_decision_create", None), ("backlog_dependencies", None), ("backlog_epic_status", None),
    ("backlog_get_task", None), ("backlog_handover_create", None), ("backlog_handover_get", None),
    ("backlog_handover_list", None), ("backlog_handover_supersede", None),
    ("backlog_handover_update_status", None), ("backlog_idea_create", None), ("backlog_idea_get", None),
    ("backlog_idea_list", None), ("backlog_idea_update", None), ("backlog_issue_create", None),
    ("backlog_issue_get", None), ("backlog_issue_list", None), ("backlog_issue_update", None),
    ("backlog_last_session", None), ("backlog_linear", "link"), ("backlog_linear", "list"),
    ("backlog_linear", "show"), ("backlog_linear", "status"), ("backlog_linear", "unlink"),
    ("backlog_link", "create"), ("backlog_link", "query"), ("backlog_link", "remove"),
    ("backlog_link", "validate"), ("backlog_list_tasks", None), ("backlog_next_available", None),
    ("backlog_phase_status", None), ("backlog_pick_task", None),
    ("backlog_project_error_trace_ladder", None), ("backlog_project_get", None),
    ("backlog_project_get_field", None), ("backlog_project_init", None), ("backlog_project_set", None),
    ("backlog_project_ship_order", None), ("backlog_query", None), ("backlog_record_gate", None),
    ("backlog_record_merge", None), ("backlog_search", None), ("backlog_set_spec_review", None),
    ("backlog_skip_gate", None), ("backlog_status", None), ("backlog_store_status", None),
    ("backlog_task_pipeline", None), ("backlog_thread_list", None), ("backlog_thread_resume", None),
    ("backlog_thread_update", None), ("backlog_update_epic", None), ("backlog_update_phase", None),
    ("backlog_update_task", None), ("viewer_prefs_get", None),
}


def test_the_unrouted_normal_path_ledger_is_exact():
    normal = {pair for pair, category in inventory_pairs().items() if category in NORMAL_PATH}
    unrouted = normal - routed_pairs()
    assert sorted(unrouted, key=str) == sorted(UNROUTED_NORMAL_PATH, key=str), (
        f"ledger drift: newly unrouted {sorted(unrouted - UNROUTED_NORMAL_PATH, key=str)}, "
        f"now routed {sorted(UNROUTED_NORMAL_PATH - unrouted, key=str)}")


def test_every_routed_pair_is_a_real_public_tool_action():
    assert routed_pairs() <= set(inventory_pairs())
