# User intent: force a decision for every write operation and routed adapter about whether it copies one entity's text
# into another, so a copy can't silently turn a teammate's prose into the writer's "own" text (team-server-027 §3.5).
from pathlib import Path
import re

from taskmaster.native import provenance as pv, workflow

ROOT = Path(__file__).resolve().parent.parent / "taskmaster"

COPY_RULE = "copy-rule"
VALIDATED = "validated"
# Operations: a mark, or no-copy with the reason. Read from each handler in native/workflow.py, native/commands.py and
# native/lifecycle.py and the adapters that issue them.
OPERATIONS = {
    "bug.promote": COPY_RULE,       # issue components copied from the source bugs (CopyRule bug.promote)
    "task.merge": VALIDATED,        # merge_status keys / merge_gate_state copy ladder labels; labels are branch-like (D8)
    "task.gate": VALIDATED,         # commit_sha is hex only (D8)
    "link.create": "no-copy: the inverse link is written by the same actor on the target; ids only",
    "link.remove": "no-copy: removes link ids",
    "project.set": "no-copy: writes the caller's own document",
    "batch": "no-copy: applies its items, each audited on its own",
    "task.patch": "no-copy: applies the caller's own set/remove",
    "note.create": "no-copy: the caller's own text",
    "note.update": "no-copy: the caller's own text", "note.archive": "no-copy: flag only",
    "document.import": "no-copy: an import, attributed as ~imported",
    "sync.apply": "no-copy: an import, attributed as ~imported",
    "graph.repair": "no-copy: repairs ids and links",
    "linear.claim": "no-copy: outbox state", "linear.settle": "no-copy: tracker fields, attributed as ~imported",
    "linear.link": "no-copy: tracker ids", "linear.unlink": "no-copy: tracker ids",
}
NO_COPY_REASON = "no-copy: writes only the caller's own arguments"

ADAPTERS = {
    "records": "copy-rule: bug.promote reads the source bugs' components",
    "merge_stamps": "validated: task.merge from the recorder, rung from the ladder (D8)",
    "gate": "validated: record_gate commit_sha is hex only (D8)",
}


def routed_operations():
    found = set()
    for path in (ROOT / "native_routing").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r"_run\(call, \"([a-z_]+\.[a-z_]+)\"", text))
        found |= set(re.findall(r"(?:\.execute|_run)\(\s*\"([a-z_]+\.[a-z_]+|batch)\"", text))
    return found


def test_every_operation_is_marked_or_declared_no_copy():
    known = set(workflow.OPERATIONS) | routed_operations() | {"batch", "task.patch", "note.create", "sync.apply"}
    unmarked = sorted(op for op in known if op not in OPERATIONS and op not in
                      PLAIN_NO_COPY)
    assert unmarked == [], f"decide copy, validated or no-copy for: {unmarked}"


def test_copy_rule_marks_have_a_rule():
    assert {rule.name for rule in pv.COPY_RULES} == {op for op, mark in OPERATIONS.items() if mark == COPY_RULE}


def test_every_adapter_is_marked():
    modules = {p.stem for p in (ROOT / "native_routing").glob("*.py") if p.stem != "__init__"}
    unmarked = sorted(m for m in modules if m not in ADAPTERS and m not in PLAIN_ADAPTERS)
    assert unmarked == [], f"decide copy or no-copy for adapters: {unmarked}"


# Operations and adapters confirmed no-copy: they pass only the caller's own arguments (reasons per group).
PLAIN_NO_COPY = {
    "task.create", "task.update", "task.pick", "task.complete", "task.archive", "task.gate_skip", "task.gate_clear",
    "task.spec_review", "task.spec_review_clear", "task.claim_renew", "task.claim_release", "task.batch_line",
    "task.viewer_create", "task.viewer_update", "task.viewer_archive",
    "epic.create", "epic.update", "epic.archive", "epic.batch_line", "phase.create", "phase.update", "phase.advance",
    "area.create", "area.update", "thread.update",
    "handover.create", "handover.supersede", "handover.status", "bug.create", "bug.update", "bug.archive",
    "issue.create", "issue.update", "idea.create", "idea.update", "decision.create", "decision.resolve",
    "decision.drop", "decision.update",
}
PLAIN_ADAPTERS = {
    "batch", "changes", "claims", "conflicts", "context", "derived", "documents", "epics_phases", "handovers",
    "hook_reads", "links_areas", "notes", "overview", "progress", "projection", "reads", "registry", "resync",
    "runtime", "tasks", "viewer",
}
