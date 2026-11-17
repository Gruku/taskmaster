# User intent: one fixed twin scenario (legacy store and its native copy) for the hook read views, so the
# answers the hooks gave before team-server-007 moved their reads (captured as golden JSON) stay the answers after.
from __future__ import annotations

import json
from pathlib import Path

import yaml

from taskmaster import backlog_server as bs

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "hook_views" / "golden.json"
MANIFEST = {
    "schema_version": 1,
    "meta": {"name": "Hooks", "slug": "hooks", "kind": "app"},
    "conventions": {"policies": {"review_gate_required_for_merge": True,
                                 "merge_targets": [{"label": "qa", "branches": ["qa-line"]}]}},
}
PATHS = ("api/model.py", "api/other.py", "api/third.py", "web/none.py", "docs/a.md")
BRANCHES = ("feature/late", "feature/seeded", "feature/untracked")
TARGETS = ("qa-line", "master")


def seed():
    bs.backlog_add_task(title="Anchored task", epic="test-epic", phase="dev", options={"anchors": "api/model.py"})
    bs.backlog_add_task(title="Gated task", epic="test-epic", phase="dev")
    bs.backlog_update_task(task_id="test-epic-002", field="branch", value="feature/seeded")
    bs.backlog_bug_create(title="Seeded bug", found_in="test-epic-001", location=["api/model.py"])


def after_activation(twins):
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(MANIFEST))
    twins.same("backlog_add_task", title="Late task", epic="test-epic", phase="dev", options={"anchors": "api/**"})
    twins.same("backlog_update_task", task_id="test-epic-003", field="branch", value="feature/late")
    twins.same("backlog_issue_create", title="Late issue", severity="P2", evidence="x",
               location=["api/model.py"], related_tasks=["test-epic-001"])
    twins.same("backlog_bug_update", bug_id="B-001", field="status", value="shelved")


def checkpoints(twins):
    """Yield a label after each step; every label is compared on both stores."""
    yield "initial"
    twins.same("backlog_add_task", title="Path neighbour", epic="test-epic", phase="dev",
               options={"anchors": "api/other.py"})
    twins.same("backlog_add_task", title="Closed neighbour", epic="test-epic", phase="dev",
               options={"anchors": "api/third.py"})
    twins.same("backlog_update_task", task_id="test-epic-005", field="status", value="archived")
    twins.same("backlog_handover_create", tldr="Pairing", task_ids=["test-epic-001", "test-epic-002"])
    yield "neighbours"
    twins.same("backlog_update_task", task_id="test-epic-003", field="lane", value="express")
    twins.same("backlog_record_gate", task_id="test-epic-003", gate="impl", status="done")
    twins.same("backlog_record_gate", task_id="test-epic-003", gate="review-gate", verdict="fail")
    yield "gate-fail"
    twins.same("backlog_record_gate", task_id="test-epic-003", gate="review-gate", verdict="pass")
    yield "gate-pass"
    off = dict(MANIFEST, conventions={"policies": {"review_gate_required_for_merge": False}})
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(off))
    yield "policy-off"


def load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))
