"""N03 additive staging schema and explicit, lossless field ownership.

JSON text columns use SQL NULL for absence and JSON 'null' for explicit null.
Operational fields are independently addressable columns, never a second doc.
Legacy tables remain the only writer authority until the later adapter cutover.
"""
import sqlite3

VERSION = 3
PROTOCOL = 2
COMMON = {
    "entity_core": "id title status priority archived deleted rev updated_seq",
    "entity_documents": "_body body",
    "declared_links": "links", "path_claims": "anchors location",
}
KINDS = {
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
    "area": {},
    "tracker": {"tracker_operational": "external_system external_key instance_alias sync_direction last_synced last_pushed synced_hash push_hash"},
    "backlog": {}, "project": {},
}
OPERATIONS = {table: fields.split() for groups in KINDS.values()
              for table, fields in groups.items() if table.endswith("_operational")}
CORE_FIELDS = COMMON["entity_core"].split()
RELATIONS = ("dependencies", "memberships", "declared_links", "path_claims")


def owner(kind: str, field: str) -> str:
    for groups in (KINDS.get(kind, {}), COMMON):
        for table, names in groups.items():
            if field in names.split():
                return table
    return "configuration" if kind in ("backlog", "project") else "entity_extensions"


def json_column(field: str) -> str:
    # Names only ever originate in this module's fixed field inventory.
    return f'"{field}_json" TEXT CHECK("{field}_json" IS NULL OR json_valid("{field}_json"))'


DDL = [
    "CREATE TABLE native_manifest(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE entity_core(entity_key INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
    "public_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0), last_seq INTEGER NOT NULL, "
    "archived INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, "
    + ", ".join(json_column(f) for f in CORE_FIELDS) + ", UNIQUE(kind,public_id))",
    "CREATE INDEX ix_entity_core_changed ON entity_core(last_seq,entity_key)",
    "CREATE INDEX ix_entity_core_kind_status ON entity_core(kind,json_extract(status_json,'$'),archived,deleted,public_id)",
    "CREATE TABLE entity_documents(entity_key INTEGER PRIMARY KEY REFERENCES entity_core(entity_key), "
    "body TEXT, format TEXT NOT NULL DEFAULT 'markdown', " + json_column("body") + ", " + json_column("_body") + ")",
    "CREATE TABLE external_documents(entity_key INTEGER NOT NULL REFERENCES entity_core(entity_key), section TEXT NOT NULL, "
    "path TEXT NOT NULL, body TEXT NOT NULL, content_hash TEXT NOT NULL, imported_seq INTEGER NOT NULL, PRIMARY KEY(entity_key,section))",
    # These two old query caches are retained only for exact migration diagnostics.
    "CREATE TABLE compatibility_entity_state(entity_key INTEGER PRIMARY KEY REFERENCES entity_core(entity_key), epic TEXT, status TEXT)",
    "CREATE TABLE entity_extensions(entity_key INTEGER NOT NULL REFERENCES entity_core(entity_key), "
    "field TEXT NOT NULL, value_json TEXT NOT NULL CHECK(json_valid(value_json)), PRIMARY KEY(entity_key,field))",
    "CREATE TABLE configuration(entity_key INTEGER NOT NULL REFERENCES entity_core(entity_key), "
    "field TEXT NOT NULL, value_json TEXT NOT NULL CHECK(json_valid(value_json)), PRIMARY KEY(entity_key,field))",
    "CREATE TABLE field_shapes(entity_key INTEGER NOT NULL REFERENCES entity_core(entity_key), "
    "field TEXT NOT NULL, shape TEXT NOT NULL CHECK(shape IN ('scalar','list')), PRIMARY KEY(entity_key,field))",
    "CREATE TABLE paths(path_key INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE)",
    "CREATE TABLE glob_patterns(path_key INTEGER PRIMARY KEY REFERENCES paths(path_key))",
    "CREATE TABLE command_commits(commit_key INTEGER PRIMARY KEY AUTOINCREMENT, first_seq INTEGER, final_seq INTEGER, operation TEXT NOT NULL)",
    "CREATE TABLE domain_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session TEXT NOT NULL, "
    "tool TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, op TEXT NOT NULL, fields TEXT, before TEXT, after TEXT, "
    "commit_key INTEGER REFERENCES command_commits(commit_key))",
    "CREATE INDEX ix_domain_events_entity ON domain_events(kind,id,seq)",
    "CREATE TABLE command_receipts(store_id TEXT NOT NULL, caller_scope TEXT NOT NULL, request_id TEXT NOT NULL, "
    "payload_hash TEXT NOT NULL, outcome_json TEXT NOT NULL CHECK(json_valid(outcome_json)), "
    "commit_seq INTEGER NOT NULL, expires_at TEXT, PRIMARY KEY(store_id,caller_scope,request_id))",
    "CREATE TABLE projection_jobs(job_key INTEGER PRIMARY KEY AUTOINCREMENT, entity_key INTEGER REFERENCES entity_core(entity_key), "
    "revision INTEGER NOT NULL, commit_seq INTEGER NOT NULL, file TEXT NOT NULL, effect TEXT NOT NULL, "
    "input_json TEXT NOT NULL CHECK(json_valid(input_json)), expected_hash TEXT, state TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(state IN ('pending','claimed','exported','superseded','conflict')), lease_owner TEXT, lease_until REAL)",
    "CREATE INDEX ix_projection_jobs_pending ON projection_jobs(state,commit_seq,job_key)",
    "CREATE TABLE sync_state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL CHECK(json_valid(value_json)))",
    "CREATE TABLE id_reservations(kind TEXT NOT NULL, public_id TEXT NOT NULL, PRIMARY KEY(kind,public_id))",
    "CREATE TABLE id_counters(kind TEXT NOT NULL, prefix TEXT NOT NULL, high_water INTEGER NOT NULL CHECK(high_water>=0), PRIMARY KEY(kind,prefix))",
    "CREATE TABLE document_search_keys(document_key INTEGER PRIMARY KEY AUTOINCREMENT, entity_key INTEGER NOT NULL UNIQUE REFERENCES entity_core(entity_key))",
    "CREATE VIRTUAL TABLE document_search USING fts5(kind UNINDEXED,id UNINDEXED,title,body,tokenize='porter unicode61')",
]
for table, fields in OPERATIONS.items():
    DDL.append(f"CREATE TABLE {table}(entity_key INTEGER PRIMARY KEY REFERENCES entity_core(entity_key), "
               + ", ".join(json_column(f) for f in fields) + ")")
DDL += [
    "CREATE INDEX ix_task_epic ON task_operational(json_extract(epic_json,'$'),entity_key)",
    "CREATE INDEX ix_task_phase ON task_operational(json_extract(phase_json,'$'),entity_key)",
    "CREATE INDEX ix_handover_thread ON handover_operational(json_extract(thread_json,'$'),entity_key)",
]
for table in RELATIONS:
    DDL.extend([
        f"CREATE TABLE {table}(entity_key INTEGER NOT NULL, field TEXT NOT NULL, ordinal INTEGER NOT NULL, "
        "value_json TEXT CHECK(value_json IS NULL OR json_valid(value_json)), "
        "target_kind TEXT, target_id TEXT, target_key INTEGER REFERENCES entity_core(entity_key), "
        "path_key INTEGER REFERENCES paths(path_key), "
        "PRIMARY KEY(entity_key,field,ordinal), "
        "CHECK((value_json IS NOT NULL AND path_key IS NULL) OR (value_json IS NULL AND path_key IS NOT NULL)), "
        "FOREIGN KEY(entity_key,field) REFERENCES field_shapes(entity_key,field))",
        f"CREATE INDEX ix_{table}_target ON {table}(target_kind,target_id,entity_key)",
        f"CREATE INDEX ix_{table}_target_key ON {table}(target_key,entity_key)",
    ])
DDL.append("CREATE INDEX ix_path_claims_path ON path_claims(path_key,entity_key)")

# Local-only authority stays in these existing tables; do not create writable
# duplicate queues, sessions or projection bases. Later outbox code reuses them.
RETAINED_TABLES = ("projection", "projection_base", "sessions", "linear_queue", "meta")


def create_schema(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction. Never executescript (it implicitly commits)."""
    if not connection.in_transaction:
        raise RuntimeError("schema creation requires an owned transaction")
    for statement in DDL:
        connection.execute(statement)


def upgrade_staging(connection: sqlite3.Connection, version: int) -> None:
    """The supported older staging schemas; caller owns the atomic backfill."""
    if not connection.in_transaction:
        raise RuntimeError("staging upgrade requires an owned transaction")
    if version == VERSION:
        return
    if version not in (1, 2) or VERSION != 3:
        raise RuntimeError("unsupported staging upgrade")
    if version < 2:
        for statement in DDL:
            if any(statement.startswith(f"CREATE TABLE {name}(") for name in ("external_documents", "id_reservations", "id_counters")):
                connection.execute(statement)
    # v2 -> v3 moves handover task membership out of the extension bag into the
    # typed membership table. There is no DDL change; the caller's re-backfill
    # rewrites every relation, and that is what relocates the existing rows.
