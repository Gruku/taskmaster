# User intent: call every tool the server registers once, with the smallest arguments its schema allows (fake ids for anything that writes), so the leak scan covers the whole tool list a member can reach (015 S10 coverage sweep, review I4).
"""Minimal-valid-argument generation from a tool's JSON input schema."""
from __future__ import annotations

READ_PREFIXES = ("backlog_get_", "backlog_list_", "backlog_status", "backlog_context", "backlog_search", "backlog_query",
                 "backlog_task_pipeline", "backlog_continuity_items", "backlog_handover_list", "backlog_handover_get",
                 "backlog_issue_list", "backlog_issue_get", "backlog_bug_list", "backlog_bug_get", "backlog_idea_list",
                 "backlog_idea_get", "backlog_area_list", "backlog_area_get", "backlog_epic_status", "backlog_phase_status",
                 "backlog_dependencies", "backlog_thread_list", "backlog_store_status", "backlog_index_status",
                 "backlog_last_session", "backlog_next_available", "backlog_project_get", "viewer_prefs_get",
                 "backlog_project_ship_order", "backlog_project_error_trace_ladder", "backlog_changes_since",
                 "backlog_validate", "backlog_document", "backlog_blast_radius", "backlog_batch_preview")


def is_reader(tool: str) -> bool:
    return tool.startswith(READ_PREFIXES)


def value_for(tool: str, name: str, schema: dict, *, real: bool):
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        picked = next((o for o in options if o.get("type") != "null"), options[0])
        return value_for(tool, name, picked, real=real)
    kind = schema.get("type")
    if kind == "string":
        lowered = name.lower()
        if lowered == "sql":
            return "SELECT 1"
        if lowered.endswith("id") or lowered in ("task", "thread", "id", "ref"):
            return "test-epic-001" if real else "zz-999"
        if lowered in ("epic", "epic_id"):
            return "test-epic" if real else "zz-epic"
        if lowered in ("phase", "phase_id"):
            return "dev" if real else "zz-phase"
        return "probe"
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "probe"


def minimal_arguments(tool: str, input_schema: dict) -> dict:
    """The required properties only; ids point at real seeded entities for readers and at fake ones for writers."""
    properties = input_schema.get("properties", {})
    real = is_reader(tool)
    return {name: value_for(tool, name, properties.get(name, {}), real=real)
            for name in input_schema.get("required", [])}
