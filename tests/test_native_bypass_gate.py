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
    ("backlog_add_task", None): lambda: bs.backlog_add_task(
        title="gate task", epic="test-epic", phase="Development", depends_on="test-epic-001",
        options={"docs": "plan:p.md", "anchors": "a.py"}),
    ("backlog_update_task", None): lambda: (
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="see test-epic-002"),
        bs.backlog_update_task(task_id="test-epic-001", tldr="gate tldr", next_step="gate next")),
    ("backlog_pick_task", None): lambda: bs.backlog_pick_task(task_id="test-epic-001"),
    ("backlog_complete_task", None): lambda: (
        bs.backlog_pick_task(task_id="test-epic-002"),
        bs.backlog_complete_task(task_id="test-epic-002", target_status="in-review", human_action="sign")),
    ("backlog_archive_task", None): lambda: bs.backlog_archive_task(task_id="test-epic-002", reason="wont-fix"),
    ("backlog_record_gate", None): lambda: bs.backlog_record_gate(task_id="test-epic-001", gate="spec", status="done"),
    ("backlog_skip_gate", None): lambda: bs.backlog_skip_gate(task_id="test-epic-001", gate="plan", reason="gate"),
    ("backlog_clear_gate", None): lambda: bs.backlog_clear_gate(task_id="test-epic-001", gate="plan"),
    ("backlog_record_merge", None): lambda: bs.backlog_record_merge(task_id="test-epic-001", rung="develop", sha="abc"),
    ("backlog_set_spec_review", None): lambda: bs.backlog_set_spec_review(
        task_id="test-epic-001", verdict="pass", spec_path="s.md"),
    ("backlog_clear_spec_review", None): lambda: bs.backlog_clear_spec_review(task_id="test-epic-001"),
    ("backlog_task_pipeline", None): lambda: bs.backlog_task_pipeline(task_id="test-epic-001"),
    ("backlog_get_task", None): lambda: [bs.backlog_get_task(task_id="test-epic-001", **kwargs) for kwargs in (
        {}, {"verbose": True}, {"expand_links": True}, {"verbose": True, "expand_links": True},
        {"sections": ["notes"]})],
    ("backlog_list_tasks", None): lambda: bs.backlog_list_tasks(verbose=True, limit=0),
    ("backlog_dependencies", None): lambda: bs.backlog_dependencies(task_id="test-epic-002"),
    ("backlog_next_available", None): lambda: bs.backlog_next_available(include_future_phases=True),
    ("backlog_batch_update", None): lambda: bs.backlog_batch_update(operations=chr(10).join([
        "update test-epic-001 depends_on test-epic-002", "update test-epic-001 phase Development",
        "status test-epic-002 in-progress", "complete test-epic-002", "pick test-epic-001",
        "archive test-epic-002 wont-fix", "update_epic test-epic name Renamed"])),
    ("backlog_add_epic", None): lambda: bs.backlog_add_epic(epic_id="gate-epic", name="Gate", done_when="x"),
    ("backlog_update_epic", None): lambda: [bs.backlog_update_epic(epic_id="test-epic", field=f, value=v) for f, v in (
        ("name", "Renamed"), ("docs", "plan:p.md"), ("components", '{"ui": {"title": "UI", "after": []}}'),
        ("design_status", "locked"), ("status", "active"))],
    ("backlog_archive_epic", None): lambda: bs.backlog_archive_epic(epic_id="test-epic", reason="superseded"),
    ("backlog_epic_status", None): lambda: bs.backlog_epic_status(epic_id="test-epic"),
    ("backlog_add_phase", None): lambda: bs.backlog_add_phase(phase_id="gate-phase", name="Gate phase"),
    ("backlog_update_phase", None): lambda: [bs.backlog_update_phase(phase_id="Development", field=f, value=v) for f, v in (
        ("deliverables", '{"action": "add", "text": "ship"}'), ("docs", "plan:p.md"), ("status", "active"))],
    ("backlog_phase_status", None): lambda: bs.backlog_phase_status(),
    ("backlog_advance_phase", None): lambda: bs.backlog_advance_phase(force=True),
    ("backlog_bug_create", None): lambda: bs.backlog_bug_create(title="Gate bug", found_in="test-epic-001",
                                                              components=["ui"]),
    ("backlog_bug_list", None): lambda: bs.backlog_bug_list(include_archive=True),
    ("backlog_bug_get", None): lambda: [bs.backlog_bug_get(bug_id="B-001"), bs.backlog_bug_get(bug_id="B-001", verbose=True)],
    ("backlog_bug_update", None): lambda: bs.backlog_bug_update(bug_id="B-001", field="components", value="ui, api"),
    ("backlog_bug_archive", None): lambda: (bs.backlog_bug_update(bug_id="B-001", field="status", value="shelved"),
                                            bs.backlog_bug_update(bug_id="B-001", field="fix_commit", value="abc"),
                                            bs.backlog_bug_update(bug_id="B-001", field="status", value="fixed"),
                                            bs.backlog_bug_archive(bug_id="B-001")),
    ("backlog_bug_pattern_scan", None): lambda: bs.backlog_bug_pattern_scan(),
    ("backlog_bug_promote", None): lambda: bs.backlog_bug_promote(
        bug_ids=["B-001"], title="Gate issue", severity="P2", evidence_text="recurs", body="see IDEA-001"),
    ("backlog_issue_create", None): lambda: bs.backlog_issue_create(
        title="Gate issue", severity="P1", evidence="x", body="mentions IDEA-001", related_tasks=["test-epic-001"]),
    ("backlog_issue_list", None): lambda: bs.backlog_issue_list(verbose=True),
    ("backlog_issue_get", None): lambda: [bs.backlog_issue_get(issue_id="ISS-001", **kwargs) for kwargs in (
        {}, {"verbose": True, "expand_links": True}, {"expand_links": True}, {"sections": ["repro"]})],
    ("backlog_issue_update", None): lambda: bs.backlog_issue_update(issue_id="ISS-001", field="body", value="IDEA-001"),
    ("backlog_idea_create", None): lambda: bs.backlog_idea_create(title="Gate idea", body="about ISS-001"),
    ("backlog_idea_list", None): lambda: [bs.backlog_idea_list(), bs.backlog_idea_list(idea_id="IDEA-001")],
    ("backlog_idea_get", None): lambda: [bs.backlog_idea_get(idea_id="IDEA-001", **kwargs) for kwargs in (
        {}, {"verbose": True, "expand_links": True}, {"expand_links": True})],
    ("backlog_idea_update", None): lambda: bs.backlog_idea_update(idea_id="IDEA-001", field="archived", value="true"),
    ("backlog_decision_create", None): lambda: bs.backlog_decision_create(title="Gate decision", options=["a", "b"]),
    ("backlog_decision", "list"): lambda: bs.backlog_decision(action="list", status="all"),
    ("backlog_decision", "get"): lambda: bs.backlog_decision(action="get", decision_id="DEC-001"),
    ("backlog_decision", "update"): lambda: bs.backlog_decision(action="update", decision_id="DEC-001", title="t"),
    ("backlog_decision", "resolve"): lambda: bs.backlog_decision(action="resolve", decision_id="DEC-001",
                                                                 resolved_with=1),
    ("backlog_decision", "drop"): lambda: bs.backlog_decision(action="drop", decision_id="DEC-001", reason="no"),
    ("backlog_handover_create", None): lambda: bs.backlog_handover_create(
        tldr="Gate handover two", task_ids=["test-epic-001"], body="see ISS-001", supersedes="2026-09-17-gate-handover",
        flag_for_review=True, options={"review_reason": "gate"}),
    ("backlog_handover_list", None): lambda: bs.backlog_handover_list(verbose=True, limit=0),
    ("backlog_handover_get", None): lambda: [bs.backlog_handover_get(handover_id="2026-09-17-gate-handover", **kwargs)
                                             for kwargs in ({}, {"verbose": True, "expand_links": True},
                                                            {"expand_links": True})],
    ("backlog_handover_supersede", None): lambda: (
        bs.backlog_handover_create(tldr="Gate successor"),
        bs.backlog_handover_supersede(old_id="2026-09-17-gate-handover", new_id="2026-09-17-gate-successor")),
    ("backlog_handover_update_status", None): lambda: bs.backlog_handover_update_status(
        handover_id="2026-09-17-gate-handover", status="closed", reason="gate"),
    ("backlog_thread_list", None): lambda: bs.backlog_thread_list(include_closed=True),
    ("backlog_thread_resume", None): lambda: [bs.backlog_thread_resume(ref="test-epic"),
                                              bs.backlog_thread_resume(ref="2026-09-17-gate-handover")],
    ("backlog_thread_update", None): lambda: bs.backlog_thread_update(name="test-epic", status="parked"),
    ("backlog_continuity_items", None): lambda: bs.backlog_continuity_items(),
    ("backlog_last_session", None): lambda: bs.backlog_last_session(),
    ("backlog_link", "create"): lambda: bs.backlog_link(action="create", source="IDEA-001", target="ISS-001",
                                                      type="relates_to"),
    ("backlog_link", "remove"): lambda: bs.backlog_link(action="remove", source="ISS-001", target="IDEA-001"),
    ("backlog_link", "query"): lambda: [bs.backlog_link(action="query"),
                                        bs.backlog_link(action="query", source="ISS-001", type="relates_to", depth=2)],
    ("backlog_link", "validate"): lambda: bs.backlog_link(action="validate"),
    ("backlog_area_create", None): lambda: bs.backlog_area_create(area_id="gate-area", name="Gate area"),
    ("backlog_area_list", None): lambda: bs.backlog_area_list(),
    ("backlog_area_get", None): lambda: bs.backlog_area_get(area_id="gate-seed-area"),
    ("backlog_area_update", None): lambda: bs.backlog_area_update(area_id="gate-seed-area", field="anchors",
                                                                  value='["a/**"]'),
    ("viewer_prefs_get", None): lambda: bs.viewer_prefs_get(),
    ("backlog_status", None): lambda: [bs.backlog_status(), bs.backlog_status(verbose=True)],
    ("backlog_blast_radius", None): lambda: [bs.backlog_blast_radius(task_id="test-epic-001"),
                                             bs.backlog_blast_radius(task_id="test-epic-001", structured=True)],
    ("backlog_search", None): lambda: [bs.backlog_search(query="Gate"), bs.backlog_search(query="gate", kinds=["bug"])],
    ("backlog_query", None): lambda: bs.backlog_query(sql="SELECT kind,id FROM entities ORDER BY kind,id"),
    ("backlog_store_status", None): lambda: bs.backlog_store_status(),
    ("backlog_project_init", None): lambda: bs.backlog_project_init(name="Gate project"),
    ("backlog_project_set", None): lambda: bs.backlog_project_set(
        yaml_content="schema_version: 1\nmeta: {name: Gate, slug: gate, kind: app}\n"),
    ("backlog_project_get", None): lambda: (bs.backlog_project_init(name="Gate project"), bs.backlog_project_get())[1],
    ("backlog_project_get_field", None): lambda: bs.backlog_project_get_field(path="meta.name"),
    ("backlog_project_ship_order", None): lambda: bs.backlog_project_ship_order(),
    ("backlog_project_error_trace_ladder", None): lambda: bs.backlog_project_error_trace_ladder(),
    ("backlog_linear", "link"): lambda: bs.backlog_linear(action="link", task_id="test-epic-002", external_key="ENG-9"),
    ("backlog_linear", "unlink"): lambda: bs.backlog_linear(action="unlink", task_id="test-epic-002"),
    ("backlog_linear", "list"): lambda: bs.backlog_linear(action="list"),
    ("backlog_linear", "show"): lambda: bs.backlog_linear(action="show", tracker_id="linear-cm-eng-9"),
    ("backlog_linear", "status"): lambda: bs.backlog_linear(action="status"),
    ("backlog_batch_preview", None): lambda: bs.backlog_batch_preview(
        operations=chr(10).join(["pick test-epic-002", "complete test-epic-001", "status test-epic-001 done"])),
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
        bs.backlog_add_task(title="Gate one", epic="test-epic", phase="dev", notes="seed")
        bs.backlog_add_task(title="Gate two", epic="test-epic", phase="dev", depends_on="test-epic-001")
        bs.backlog_update_task(task_id="test-epic-001", field="lane", value="standard")
        bs.backlog_handover_create(tldr="Gate handover", task_ids=["test-epic-001"])
        bs.backlog_bug_create(title="Seeded gate bug", found_in="test-epic-002")
        bs.backlog_issue_create(title="Seeded gate issue", severity="P2", evidence="x", related_tasks=["test-epic-001"])
        bs.backlog_idea_create(title="Seeded gate idea", body="about ISS-001")
        bs.backlog_decision_create(title="Seeded gate decision", options=["a", "b"])
        bs.backlog_area_create(area_id="gate-seed-area", name="Seeded area")
    twins = make_twins(tmp_path, monkeypatch, seed)
    (twins.native / ".taskmaster" / "linear.yaml").write_text(
        "version: 1\ndefault_workspace: cm\nworkspaces:\n- {alias: cm, team_id: T1, token_env: GATE_TOKEN}\n",
        encoding="utf-8")
    (twins.native / ".taskmaster" / "local" / "PROGRESS.md").write_text(
        "## Changelog\n\n### 2026-09-16 — Gate\n- x\n", encoding="utf-8")
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
            # Machine-local state and configuration the store does not own
            # (Linear workspaces, the layout config) are not the projection.
            return not relative.parts or relative.parts[0] not in ("local", "linear.yaml", "taskmaster.json")

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
    answers = list(answer) if isinstance(answer, (list, tuple)) else [answer]
    # An exercise that is refused never reaches the path it exists to cover.
    assert not [a for a in answers if str(a).startswith("Error")], f"{pair} exercise was refused: {answers}"


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
ALLOWLIST = {
    # The export drain samples existing files once per process to match line endings.
    ("projection.py", "detect_dominant_crlf"),
    # The store-status diagnostic lists databases a recovery moved aside in local/.
    ("overview.py", "listdir"),
}


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
UNROUTED_NORMAL_PATH: set = set()


def test_the_unrouted_normal_path_ledger_is_exact():
    normal = {pair for pair, category in inventory_pairs().items() if category in NORMAL_PATH}
    unrouted = normal - routed_pairs()
    assert sorted(unrouted, key=str) == sorted(UNROUTED_NORMAL_PATH, key=str), (
        f"ledger drift: newly unrouted {sorted(unrouted - UNROUTED_NORMAL_PATH, key=str)}, "
        f"now routed {sorted(UNROUTED_NORMAL_PATH - unrouted, key=str)}")


def test_every_routed_pair_is_a_real_public_tool_action():
    assert routed_pairs() <= set(inventory_pairs())
