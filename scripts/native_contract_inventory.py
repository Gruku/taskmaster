"""Freeze native-migration API contracts without importing/starting the server.

Use --write only when deliberately reviewing a contract change. The default
checks the committed fixture and fails on drift. No project database is opened.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/native_contracts.json"

# Reviewed exceptions to the ordinary targeted-query/transaction-command split.
SPECIAL = {
    "backlog_init": "maintenance", "backlog_migrate_v3": "maintenance",
    "backlog_migrate_v4": "maintenance", "backlog_canonicalize_layout": "maintenance",
    "backlog_backfill_lanes": "maintenance", "backlog_index_status": "maintenance",
    "backlog_validate": "maintenance", "backlog_open_viewer": "host-action",
    "viewer_prefs_set": "host-action",
    "backlog_link": "action-router", "backlog_note": "action-router",
    "backlog_decision": "action-router", "backlog_linear": "action-router",
    "backlog_handover_resync": "synchronization", "backlog_issue_resync": "synchronization",
    # N17: the explicit import of hand edits; its legacy body only answers a no-op.
    "backlog_sync": "synchronization",
    "backlog_batch_update": "composite-command", "backlog_complete_task": "composite-command",
    "backlog_pick_task": "composite-command", "backlog_archive_epic": "composite-command",
    "backlog_advance_phase": "composite-command", "backlog_bug_promote": "composite-command",
    "backlog_handover_create": "composite-command", "backlog_handover_supersede": "composite-command",
    "backlog_linear_probe": "external-query", "backlog_linear_bootstrap_apply": "synchronization",
    "backlog_linear_retry": "synchronization",
    "backlog_link_reconcile": "maintenance",
    # Resolves a legacy projection flag (B-089): it writes through its own store
    # transaction, not `_transactional`, so it is classified here.
    "backlog_resolve_conflict": "maintenance",
    # An explicit import: it commits through the native core, and its legacy body
    # only refuses, so it opens no legacy transaction to be detected by one.
    "backlog_document_import": "simple-command",
}

# Logical owners consumed by N03 DDL/backfill. Unlisted authored keys have an
# explicit extension owner; they are never discarded because an API cannot edit them.
COMMON_OWNERS = {
    "entity_core": "id title status priority archived deleted rev updated_seq",
    "entity_documents": "_body body",
    "declared_links": "links", "path_claims": "anchors location",
}
KIND_OWNERS = {
    "task": {"task_operational": "epic phase order stage lane estimate owner locked_by branch worktree sub_repo component human_action design_change",
             "dependencies": "depends_on", "memberships": "bundle area"},
    "epic": {"epic_operational": "name order phase", "memberships": "area components"},
    "phase": {"phase_operational": "name order start_date target_date"},
    "handover": {"handover_operational": "kind date thread", "memberships": "task_ids"},
    "issue": {"issue_operational": "severity", "memberships": "related_tasks components fixed_in_task duplicate_of"},
    "bug": {"bug_operational": "severity", "memberships": "adopted_into components promoted_to"},
    "decision": {"decision_operational": "resolved_with resolved_in", "memberships": "task_id"},
    "idea": {"memberships": "related_tasks related_issues tags promoted_to"},
    "note": {"note_operational": "author pinned"},
    "area": {}, "tracker": {"tracker_operational": "external_system external_key instance_alias sync_direction last_synced last_pushed synced_hash push_hash"},
    "backlog": {}, "project": {},
}
ALLOWLIST_KINDS = {"ALLOWED_FIELDS": "task", "ALLOWED_EPIC_FIELDS": "epic",
    "ALLOWED_PHASE_FIELDS": "phase", "ALLOWED_AREA_FIELDS": "area",
    "ISSUE_UPDATE_FIELDS": "issue", "BUG_UPDATE_FIELDS": "bug", "IDEA_UPDATE_FIELDS": "idea"}


def ownership(fields):
    result = {}
    for kind, groups in KIND_OWNERS.items():
        explicit = {field: owner for owner, names in COMMON_OWNERS.items() for field in names.split()}
        explicit.update({field: owner for owner, names in groups.items() for field in names.split()})
        fallback = "configuration" if kind in ("backlog", "project") else "entity_extensions"
        for allowlist, target in ALLOWLIST_KINDS.items():
            if kind == target:
                for field in fields[allowlist]:
                    explicit.setdefault(field, fallback)
        result[kind] = {"fields": explicit, "unknown_field_owner": fallback}
    return result


def parse(relative):
    return ast.parse((REPO / relative).read_text(encoding="utf-8"))


def constants(tree):
    values = {}
    def evaluate(node):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return evaluate(node.left) | evaluate(node.right)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return evaluate(node.left) + evaluate(node.right)
        return ast.literal_eval(node)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and node.value is not None:
                try:
                    values[target.id] = evaluate(node.value)
                except (ValueError, TypeError, KeyError):
                    pass
    return values


def json_value(value):
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def signature(node, values):
    def parameter(arg, default, mode):
        result = {"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else None,
                  "mode": mode, "required": default is None}
        if default is not None:
            result["default_expression"] = ast.unparse(default)
            if isinstance(default, ast.Name) and default.id in values:
                result["default_value"] = values[default.id]
            else:
                result["default_value"] = ast.literal_eval(default)
        return result
    args = node.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + args.defaults
    result = [parameter(arg, default, "positional") for arg, default in zip(positional, defaults)]
    result += [parameter(arg, default, "keyword") for arg, default in zip(args.kwonlyargs, args.kw_defaults)]
    return result


def inventory():
    tree = parse("taskmaster/backlog_server.py")
    # The task/epic/phase rule constants live in the shared domain layer that the
    # tools, the viewer and the native core all import, so the inventory reads
    # them from there. Server-local names still win on any overlap.
    values = {**constants(parse("taskmaster/native/domain.py")), **constants(tree)}
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    tools = {}
    for name, node in functions.items():
        exposed = any(ast.unparse(d).startswith("mcp.tool(") for d in node.decorator_list)
        if not exposed:
            continue
        transactional = any(ast.unparse(d).startswith("_transactional(") for d in node.decorator_list)
        entry = {"category": SPECIAL.get(name, "simple-command" if transactional else "targeted-query"),
                 "parameters": signature(node, values),
                 "returns": ast.unparse(node.returns) if node.returns else None,
                 "documentation": ast.get_docstring(node), "transactional": transactional}
        actions = {}
        for child in ast.walk(node):
            if not isinstance(child, ast.If) or not isinstance(child.test, ast.Compare):
                continue
            test = child.test
            if not isinstance(test.left, ast.Name) or test.left.id != "action":
                continue
            if len(test.comparators) != 1 or not isinstance(test.comparators[0], ast.Constant):
                continue
            targets = sorted({call.func.id for stmt in child.body for call in ast.walk(stmt)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id.startswith("backlog_")})
            action = test.comparators[0].value
            actions[action] = []
            for target in targets:
                implementation = functions[target]
                writes = any(ast.unparse(d).startswith("_transactional(") for d in implementation.decorator_list)
                actions[action].append({"function": target, "parameters": signature(implementation, values),
                    "category": SPECIAL.get(target, "simple-command" if writes else "targeted-query")})
        if actions and entry["category"] == "action-router":
            entry["actions"] = actions
        tools[name] = entry
    classified = set(tools) | {target["function"] for entry in tools.values()
        for targets in entry.get("actions", {}).values() for target in targets}
    mutators = {name for name, node in functions.items()
        if any(ast.unparse(d).startswith("_transactional(") for d in node.decorator_list)}
    if missing := mutators - classified:
        raise ValueError(f"Unclassified mutating callers: {sorted(missing)}")
    routes = {}
    route_conditions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("do_"):
            routes[node.name[3:]] = sorted({child.value for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
                and child.value.startswith(("/api/", "/file/"))})
            route_conditions[node.name[3:]] = sorted({ast.unparse(child.test)
                for child in ast.walk(node) if isinstance(child, ast.If)
                and any(isinstance(part, ast.Name) and part.id == "clean_path" for part in ast.walk(child.test))})
    fields = {name: sorted(value) for name, value in values.items()
              if isinstance(value, (set, tuple, list)) and "FIELD" in name and all(isinstance(v, str) for v in value)}
    guard = constants(parse("taskmaster/query_guard.py"))
    lifecycle = {**constants(parse("taskmaster/taskmaster_v3.py")), **values}
    lifecycle = {key: json_value(value) for key, value in lifecycle.items()
        if key in ("LEGAL_STATUS_TRANSITIONS", "LANE_GATES", "VALID_LANES", "VALID_GATES", "VALID_GATE_VERDICTS")
        or key.endswith("_STATUSES") or key in ("VALID_PRIORITIES", "DEFAULT_LIST_LIMIT", "_SEARCH_LIMIT")}
    return {"tools": tools, "http_routes": routes, "route_conditions": route_conditions,
        "field_allowlists": fields,
        "field_ownership": ownership(fields),
        "lifecycle_constants": lifecycle,
        "sql_tables": list(guard["TABLES"]), "sql_timeout_seconds": guard["QUERY_TIMEOUT_S"],
        "query_schema": guard["SCHEMA_SUMMARY"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = inventory()
    if args.write:
        FIXTURE.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    elif result != json.loads(FIXTURE.read_text(encoding="utf-8")):
        raise SystemExit("Native compatibility contract changed; review the change before regenerating the fixture")
    print(f"{len(result['tools'])} tools; {sum(map(len, result['http_routes'].values()))} route patterns; contracts match")


if __name__ == "__main__":
    main()
