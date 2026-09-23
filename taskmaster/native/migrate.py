"""Transactional additive backfill from the existing database authority.

This is an explicit development/migration primitive, never called on Store open.
It does not activate native writes or touch projected files or local sidecars.
The future cutover coordinator owns backup, process exclusion and sidecar import.
"""
from collections import Counter
import hashlib
import json
import sqlite3

from taskmaster.admission import UnsupportedStoreError, assert_compatible
from . import neighbourhood, schema
from .db import manifest, probe_capabilities

STAGES = ("admitted", "schema", "entities", "relations", "history", "search", "verified")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def rows(connection, sql, args=()):
    cursor = connection.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def legacy_entities(connection):
    result = rows(connection, "SELECT kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq FROM entities ORDER BY kind,id")
    for row in result:
        row["doc"] = json.loads(row["doc"])
        if not isinstance(row["doc"], dict):
            raise ValueError(f"Invalid non-object document for {row['kind']} {row['id']}")
    return result


def _put_manifest(connection, **values):
    connection.executemany("INSERT INTO native_manifest VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(key, str(value)) for key, value in values.items()])


def _invalidation_triggers(connection):
    # Every supported legacy domain writer makes a verified staging snapshot
    # unreadable in that same commit. Peer snapshots already admitted may finish.
    for table in ("entities", "changes", "meta"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER native_stale_{table}_{operation.lower()} AFTER {operation} ON {table} "
                "BEGIN UPDATE native_manifest SET value='stale' WHERE key='state'; END")


def _reset(connection):
    for table in (*schema.RELATIONS, "field_shapes", *schema.OPERATIONS,
                  "entity_documents", "entity_extensions", "configuration", "compatibility_entity_state", "document_search", "domain_events"):
        connection.execute(f"DELETE FROM {table}")
    # Keep interned identities and AUTOINCREMENT high-water marks across retries.
    # Physical removal is outside the legacy domain contract; reject it below.


def _put_entity(connection, row):
    kind, ident, doc = row["kind"], row["id"], row["doc"]
    if kind not in schema.KINDS:
        raise ValueError(f"Unclassified entity kind: {kind}")
    connection.execute(
        "INSERT INTO entity_core(kind,public_id,revision,last_seq,archived,deleted) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(kind,public_id) DO UPDATE SET revision=excluded.revision,last_seq=excluded.last_seq,archived=excluded.archived,deleted=excluded.deleted",
        (kind, ident, row["rev"], row["updated_seq"], row["archived"], row["deleted"]))
    key = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()[0]
    assignments = ",".join(f'"{field}_json"=?' for field in schema.CORE_FIELDS)
    connection.execute(f"UPDATE entity_core SET {assignments} WHERE entity_key=?",
                       [encode(doc[f]) if f in doc else None for f in schema.CORE_FIELDS] + [key])
    connection.execute("INSERT INTO entity_documents(entity_key,body,body_json,_body_json) VALUES(?,?,?,?)",
                       (key, row["body"], encode(doc["body"]) if "body" in doc else None,
                        encode(doc["_body"]) if "_body" in doc else None))
    connection.execute("INSERT INTO compatibility_entity_state VALUES(?,?,?)", (key, row["epic"], row["status"]))
    for table, fields in schema.OPERATIONS.items():
        if table in schema.KINDS[kind]:
            names = ",".join(f'"{f}_json"' for f in fields)
            connection.execute(f"INSERT INTO {table}(entity_key,{names}) VALUES({','.join('?' for _ in range(len(fields)+1))})",
                               [key] + [encode(doc[f]) if f in doc else None for f in fields])
    for field, value in doc.items():
        owner = schema.owner(kind, field)
        if owner in ("configuration", "entity_extensions"):
            connection.execute(f"INSERT INTO {owner} VALUES(?,?,?)", (key, field, encode(value)))
    return key


TARGET_KINDS = {
    "depends_on": "task", "task_ids": "task", "related_tasks": "task", "task_id": "task",
    "fixed_in_task": "task", "adopted_into": "task", "related_issues": "issue", "area": "area", "duplicate_of": "issue",
}


def _reference(connection, field, value, source_kind=None):
    ident = value.get("target") if field == "links" and isinstance(value, dict) else value
    if not isinstance(ident, str) or not ident or (field not in TARGET_KINDS and field not in ("links", "promoted_to", "duplicate_of")):
        return None, None, None
    kind = TARGET_KINDS.get(field)
    if field == "promoted_to":
        kind = "issue" if source_kind == "bug" else "task" if source_kind == "idea" else None
    if kind is not None:
        found = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=? AND deleted=0", (kind, ident)).fetchone()
        return kind, ident, found[0] if found else None
    # Match the existing ID-only resolution precedence explicitly.
    found = connection.execute("SELECT kind,entity_key FROM entity_core WHERE public_id=? AND deleted=0 "
                               "ORDER BY CASE kind WHEN 'task' THEN 0 WHEN 'issue' THEN 1 ELSE 2 END,kind LIMIT 1", (ident,)).fetchone()
    return (found[0], ident, found[1]) if found else ("task", ident, None)


def _put_relations(connection, key, kind, doc):
    for field, value in doc.items():
        table = schema.owner(kind, field)
        if table not in schema.RELATIONS:
            continue
        shape = "list" if isinstance(value, list) else "scalar"
        connection.execute("INSERT INTO field_shapes VALUES(?,?,?)", (key, field, shape))
        for ordinal, item in enumerate(value if shape == "list" else [value]):
            target_kind, target_id, target_key = _reference(connection, field, item, kind)
            path_key, value_json = None, encode(item)
            if table == "path_claims" and isinstance(item, str):
                connection.execute("INSERT OR IGNORE INTO paths(path) VALUES(?)", (item,))
                path_key = connection.execute("SELECT path_key FROM paths WHERE path=?", (item,)).fetchone()[0]
                if any(c in item for c in "*?["):
                    connection.execute("INSERT OR IGNORE INTO glob_patterns VALUES(?)", (path_key,))
                value_json = None
            connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?,?)",
                               (key, field, ordinal, value_json, target_kind, target_id, target_key, path_key))


def reconstruct_entities(connection):
    """Explicit full-snapshot equivalence oracle, never a normal query path."""
    result = []
    for core in rows(connection, "SELECT * FROM entity_core ORDER BY kind,public_id"):
        key, kind = core["entity_key"], core["kind"]
        doc = {f: json.loads(core[f + "_json"]) for f in schema.CORE_FIELDS if core[f + "_json"] is not None}
        documents = rows(connection, "SELECT * FROM entity_documents WHERE entity_key=?", (key,))[0]
        for field in ("body", "_body"):
            if documents[field + "_json"] is not None:
                doc[field] = json.loads(documents[field + "_json"])
        for table, fields in schema.OPERATIONS.items():
            if table in schema.KINDS[kind]:
                values = rows(connection, f"SELECT * FROM {table} WHERE entity_key=?", (key,))[0]
                doc.update({f: json.loads(values[f + "_json"]) for f in fields if values[f + "_json"] is not None})
        for table in ("configuration", "entity_extensions"):
            doc.update({r[0]: json.loads(r[1]) for r in connection.execute(f"SELECT field,value_json FROM {table} WHERE entity_key=?", (key,))})
        for field, shape in connection.execute("SELECT field,shape FROM field_shapes WHERE entity_key=?", (key,)):
            table = schema.owner(kind, field)
            items = connection.execute(f"SELECT r.value_json,p.path FROM {table} r LEFT JOIN paths p USING(path_key) "
                                       "WHERE r.entity_key=? AND r.field=? ORDER BY ordinal", (key, field)).fetchall()
            values = [json.loads(item[0]) if item[0] is not None else item[1] for item in items]
            doc[field] = values if shape == "list" else values[0]
        compat = rows(connection, "SELECT epic,status FROM compatibility_entity_state WHERE entity_key=?", (key,))[0]
        result.append(dict(kind=kind, id=core["public_id"], **compat, archived=core["archived"], deleted=core["deleted"],
                           doc=doc, body=documents["body"], rev=core["revision"], updated_seq=core["last_seq"]))
    return result


def _backfill_search(connection):
    for kind, ident, title, body in connection.execute("SELECT kind,id,title,body FROM entity_fts"):
        row = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()
        if row is None:
            raise ValueError(f"Orphan legacy FTS document: {kind} {ident}")
        connection.execute("INSERT OR IGNORE INTO document_search_keys(entity_key) VALUES(?)", (row[0],))
        key = connection.execute("SELECT document_key FROM document_search_keys WHERE entity_key=?", (row[0],)).fetchone()[0]
        connection.execute("INSERT INTO document_search(rowid,kind,id,title,body) VALUES(?,?,?,?,?)", (key, kind, ident, title, body))


def _verify(connection, source):
    native = reconstruct_entities(connection)
    if encode(native) != encode(source):
        raise ValueError("Native backfill entity equivalence failed")
    old_events = [tuple(r) for r in connection.execute("SELECT * FROM changes ORDER BY seq")]
    new_events = [tuple(r) for r in connection.execute("SELECT seq,ts,session,tool,kind,id,op,fields,before,after FROM domain_events ORDER BY seq")]
    if old_events != new_events:
        raise ValueError("Native backfill event equivalence failed")
    old_fts = Counter(tuple(r) for r in connection.execute("SELECT kind,id,title,body FROM entity_fts"))
    new_fts = Counter(tuple(r) for r in connection.execute("SELECT kind,id,title,body FROM document_search"))
    if old_fts != new_fts:
        raise ValueError("Native backfill FTS equivalence failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Native backfill foreign key integrity failed")
    connection.execute("INSERT INTO document_search(document_search) VALUES('integrity-check')")
    return hashlib.sha256(encode(source).encode("utf-8")).hexdigest()


def repair_graph_for_activation(connection):
    """Bring the inherited graph tables to the full oracle, once, as native takes over.

    The legacy store's `entity_paths`/`links`/`handover_tasks`/`related` rows arrive
    as they are, including any drift its incremental maintenance left, and a store
    activated from them must verify clean. Those rows belong to legacy until the
    authority switches, and backfill is a repeatable staging step, so the repair runs
    only inside the activation transaction, after `authority` has been set to
    `native` there: a crash rolls the switch and the repair back together.
    """
    from . import graph_repair
    from .queries import Snapshot
    if not connection.in_transaction:
        raise RuntimeError("the activation graph repair must run inside the activation transaction")
    state = dict(connection.execute("SELECT key,value FROM native_manifest WHERE key IN "
                                    "('authority','store_id','event_high_water')"))
    if state.get("authority") != "native":
        raise UnsupportedStoreError("the graph tables belong to legacy until native becomes the authority")
    neighbourhood.ensure_indexes(connection)
    report = graph_repair.repair(Snapshot(connection, state))
    return {"repaired": report["repaired"], "rows_compared": report["rows_compared"], "seconds": report["seconds"],
            "differences": sum(t["missing"] + t["spurious"] for t in report["tables"].values())}


def backfill(connection: sqlite3.Connection, *, checkpoint=lambda stage: None) -> dict:
    """Atomically stage, compare, and mark verified; reject any active caller tx.

    No second writable authority is exposed. A later legacy domain commit
    invalidates the staging marker. Re-running refreshes it with stable keys.
    On any exception (including cancellation), rollback the entire operation.
    """
    if connection.in_transaction:
        raise RuntimeError("backfill requires its own transaction")
    assert_compatible(connection)
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest'").fetchone():
        manifest(connection, allow_prior_staging=True)  # Refuse future schemas before writes.
    probe_capabilities(connection)
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert_compatible(connection)  # Recheck after waiting for the writer.
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        if meta.get("schema_version") != "1" or not meta.get("creation_token"):
            raise UnsupportedStoreError("Backfill requires an initialized schema-1 authority with store identity")
        checkpoint("admitted")
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest'").fetchone():
            previous = manifest(connection, allow_prior_staging=True)
            if previous.get("store_id") != meta["creation_token"]:
                raise UnsupportedStoreError("Staging store identity does not match authority")
            schema.upgrade_staging(connection, int(previous["schema_version"]))
            _reset(connection)
        else:
            schema.create_schema(connection)
            _invalidation_triggers(connection)
        _put_manifest(connection, schema_version=schema.VERSION, protocol=schema.PROTOCOL,
                      authority="legacy", state="building", store_id=meta["creation_token"])
        checkpoint("schema")
        source = legacy_entities(connection)
        # Keys are allocated in legacy row order: epics and phases are exported in
        # key order, as legacy exports them in rowid order (backlog.yaml order,
        # then creation). Comparison and digest still use the id-ordered source.
        rank = {identity: n for n, identity in enumerate(connection.execute("SELECT kind,id FROM entities ORDER BY rowid"))}
        inserted = sorted(source, key=lambda row: rank[(row["kind"], row["id"])])
        keys = [_put_entity(connection, row) for row in inserted]
        if connection.execute("SELECT COUNT(*) FROM entity_core").fetchone()[0] != len(source):
            raise ValueError("Legacy identities were physically removed; restore tombstones before refreshing staging")
        checkpoint("entities")
        for key, row in zip(keys, inserted):
            _put_relations(connection, key, row["kind"], row["doc"])
        checkpoint("relations")
        connection.execute("INSERT INTO domain_events(seq,ts,session,tool,kind,id,op,fields,before,after) SELECT * FROM changes")
        high_water = max(connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0],
                         (connection.execute("SELECT seq FROM sqlite_sequence WHERE name='changes'").fetchone() or (0,))[0])
        connection.execute("DELETE FROM sqlite_sequence WHERE name='domain_events'")
        connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('domain_events',?)", (high_water,))
        checkpoint("history")
        _backfill_search(connection)
        checkpoint("search")
        # Transparent native-only graph indexes (N14): no schema version change.
        neighbourhood.ensure_indexes(connection)
        digest = _verify(connection, source)
        _put_manifest(connection, state="verified", source_digest=digest, event_high_water=high_water,
                      entity_count=len(source), capabilities="json,fts5,transactional-ddl,stable-keys")
        checkpoint("verified")
        connection.commit()
        return {"state": "verified", "entities": len(source), "event_high_water": high_water, "source_digest": digest}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")
