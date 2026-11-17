# User intent: the per-tool argument table for the untrusted-text marker sweep, so every registered tool is exercised
# against teammates' seeded text, and a new tool can't ship without a row or a stated skip (team-server-027 Step 10).
from __future__ import annotations

# Tools that never run against a member's server session; each with its reason.
SKIPS = {
    "backlog_init": "host-only: refused in server mode",
    "backlog_migrate_v3": "host-only: refused in server mode",
    "backlog_migrate_v4": "host-only: refused in server mode",
    "backlog_canonicalize_layout": "host-only: refused in server mode",
    "backlog_sync": "host-only: refused in server mode",
    "backlog_resolve_conflict": "host-only: refused in server mode",
    "backlog_handover_resync": "host-only: refused in server mode",
    "backlog_issue_resync": "host-only: refused in server mode",
    "backlog_backfill_lanes": "host-only: one-time layout migration",
    "backlog_linear": "refused in server mode (Linear sync is host-side)",
    "backlog_project_init": "host-only: refused in server mode",
    "viewer_prefs_get": "viewer preferences, not stored teammate text",
    "viewer_prefs_set": "viewer preferences, not stored teammate text",
}

# Tools whose rows never show the marker, each with the reason; every one still runs (against Ann's entities where it
# takes one) and fails the sweep if the marker shows. Every other tool with rows must show the marker in at least one
# row, so a row that reads nothing (a bad argument, an unseeded body) can't pass as coverage.
NO_PROSE = {
    "backlog_store_status": "store and index state only",
    "backlog_index_status": "store and index state only",
    "backlog_record_gate": "ids, verdict enums, hex SHAs",
    "backlog_skip_gate": "ids, verdict enums",
    "backlog_clear_gate": "ids, verdict enums",
    "backlog_set_spec_review": "ids, verdict enums",
    "backlog_clear_spec_review": "ids, verdict enums",
    "backlog_record_merge": "ids, labels, hex SHAs",
    "backlog_claim": "claim state and session ids",
    "backlog_open_viewer": "a URL",
    "backlog_advance_phase": "phase state",
    "backlog_validate": "store validation counts",
    "backlog_changes_since": "commit metadata: seqs, operations, kinds, ids and field names",
    "backlog_bug_promote": "a receipt naming ids; the copied components are read back by the issue_get row",
    "backlog_update_task": "a receipt echoing the field and value the caller wrote",
    "backlog_batch_update": "a receipt echoing the caller's own lines and values",
    "backlog_add_epic": "a receipt for the caller's own new entity",
    "backlog_add_phase": "a receipt for the caller's own new entity",
    "backlog_area_create": "a receipt for the caller's own new entity",
    "backlog_bug_create": "a receipt for the caller's own new entity",
    "backlog_decision_create": "a receipt for the caller's own new entity",
    "backlog_idea_create": "a receipt for the caller's own new entity",
    "backlog_issue_create": "a receipt for the caller's own new entity",
    "backlog_area_update": "a receipt naming the id and the field",
    "backlog_bug_update": "a receipt naming the id and enum fields",
    "backlog_issue_update": "a receipt naming the id and enum fields",
    "backlog_update_phase": "a receipt naming the id, the field and the caller's value",
    "backlog_thread_update": "echoes the caller's own status and reason",
    "backlog_handover_supersede": "a receipt naming handover ids",
    "backlog_project_set": "the manifest path and a receipt",
    "backlog_link": "link rows: ids and link types only (a link's note is never shown)",
    "backlog_handover_create": "a receipt for the caller's own handover: ids, paths, the caller's tldr",
    "backlog_handover_update_status": "a receipt naming the handover id and the status enum",
    "backlog_document_import": "a receipt naming the entity, its sections and their import state",
    "backlog_bug_archive": "a receipt naming the bug id",
}

# Tools that show teammates' text only transformed (lowercased tokens), so the marker itself can't appear: the row
# counts as observing when this token shows, and only inside frames naming Ann.
TRANSFORMED = {"backlog_bug_pattern_scan": "tm-inject-7f3a"}

# Router parameters given as plain strings (no enum in the schema) and the values each must be exercised with.
MODES = {
    "backlog_blast_radius": {"mode": ("predictive", "evidence")},
    "backlog_bug_pattern_scan": {"mode": ("all", "open_only", "end_of_task")},
}

# Enum values of a router parameter that the sweep doesn't run, each with its reason: {(tool, param): {value: why}}.
ROUTER_SKIPS: dict = {}

# Rows whose answer names an id later rows need: tool -> (world.ids key, pattern on the unframed answer).
CAPTURE = {"backlog_bug_promote": ("promoted", r"(ISS-\d+)")}
