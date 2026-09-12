"""One native transaction owner. Initial slices: task metadata and sticky notes.

No activation, file I/O, projection rendering or remote calls happen here.
Receipts capture committed outcomes inside the writer transaction. New command
families must use this same owner and the existing domain rules, not fork it.
"""
from copy import deepcopy
import sqlite3

from . import contracts, events, receipts, schema, search, relations
from .contracts import Conflict, CancelledBeforeExecution  # public exceptions
from .db import assert_native
from .migrate import encode, _put_entity, _put_manifest, _put_relations
from .queries import Snapshot


def _write_field(connection, key, kind, field, value, *, remove=False):
    owner = schema.owner(kind, field)
    encoded = None if remove else encode(value)
    if owner == "entity_core" or owner in schema.OPERATIONS:
        connection.execute(f'UPDATE {owner} SET "{field}_json"=? WHERE entity_key=?', (encoded, key))
    elif owner in ("entity_extensions", "configuration"):
        if remove:
            connection.execute(f"DELETE FROM {owner} WHERE entity_key=? AND field=?", (key, field))
        else:
            connection.execute(f"INSERT INTO {owner} VALUES(?,?,?) ON CONFLICT(entity_key,field) DO UPDATE SET value_json=excluded.value_json", (key, field, encoded))
    elif owner == "entity_documents":
        connection.execute(f'UPDATE entity_documents SET "{field}_json"=? WHERE entity_key=?', (encoded, key))
    elif owner in schema.RELATIONS:
        connection.execute(f"DELETE FROM {owner} WHERE entity_key=? AND field=?", (key, field))
        connection.execute("DELETE FROM field_shapes WHERE entity_key=? AND field=?", (key, field))
        if not remove:
            _put_relations(connection, key, kind, {field: value})
    else:
        raise ValueError(f"field {field} requires a relation/document command")


PREFIXES = {"note": "NOTE-", "bug": "B-", "issue": "ISS-", "decision": "DEC-", "idea": "IDEA-"}


def _reserve_id(connection, kind, doc):
    if kind == "handover":
        from taskmaster.taskmaster_v3 import make_handover_id
        base = make_handover_id(str(doc["date"]), str(doc["tldr"]))
        ident, suffix = base, 2
        while connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=? UNION ALL "
                                 "SELECT 1 FROM entity_core WHERE kind=? AND public_id=? LIMIT 1", (kind, ident, kind, ident)).fetchone():
            ident, suffix = f"{base}-{suffix}", suffix + 1
        contracts._identifier(ident, "handover id")
        connection.execute("INSERT INTO id_reservations VALUES(?,?)", (kind, ident))
        return ident
    prefix = PREFIXES[kind]
    row = connection.execute("SELECT high_water FROM id_counters WHERE kind=? AND prefix=?", (kind, prefix)).fetchone()
    if row is None:
        raise RuntimeError(f"{kind} ID high-water was not imported; refusing allocation")
    high = row[0]
    while True:
        high += 1
        ident = f"{prefix}{high:03d}"
        if not connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=? UNION ALL "
                                  "SELECT 1 FROM entity_core WHERE kind=? AND public_id=? LIMIT 1", (kind, ident, kind, ident)).fetchone():
            break
    connection.execute("INSERT INTO id_counters VALUES(?,?,?) ON CONFLICT(kind,prefix) DO UPDATE SET high_water=excluded.high_water", (kind, prefix, high))
    connection.execute("INSERT INTO id_reservations VALUES(?,?)", (kind, ident))
    return ident


def projection_path(kind, ident, archived):
    directories = {"task": "tasks", "note": "notes", "decision": "decisions", "bug": "bugs", "issue": "issues", "idea": "ideas", "handover": "handovers"}
    directory = directories[kind]
    if archived and kind in ("task", "bug", "issue"):
        directory += "/archive"
    elif archived and kind == "note":
        directory += "/_archive"
    elif archived and kind == "handover":
        from datetime import datetime, timezone
        year = ident[:4] if ident[:4].isdigit() else str(datetime.now(timezone.utc).year)
        directory += f"/_archive/{year}"
    return f"{directory}/{ident}.md"


class Transaction:
    def __init__(self, connection, request, identity):
        self.connection, self.request = connection, request
        self.snapshot = Snapshot(connection, identity)
        self.group = None
        self.seq = int(identity["event_high_water"])
        self.affected = {}
        self.counters = {"fts_documents": 0, "global_graph_rebuilds": 0, "path_comparisons": 0, "link_pairs": 0, "handover_pairs": 0}

    def _changed(self, key, kind, ident, before, after, body, operation, *, before_body=None, prior_file=None):
        # Canonical JSON equality distinguishes True from 1 and absent from null.
        fields = {f for f in set(before) | set(after) if (f in before) != (f in after) or encode(before.get(f)) != encode(after.get(f))}
        body_changed = before_body != body
        if not fields and not body_changed and operation != "create":
            return
        event_before = {f: before[f] for f in fields if f in before}
        event_after = {f: after[f] for f in fields if f in after}
        if body_changed and operation != "create":
            event_before["_body"], event_after["_body"] = before_body, body
        self.group, self.seq = events.append(self.connection, self.request, self.group, kind, ident, operation,
                                            event_before, event_after)
        self.connection.execute("UPDATE entity_core SET revision=revision+?,last_seq=? WHERE entity_key=?", (int(operation != "create"), self.seq, key))
        before_search = None if operation == "create" else search.inputs(before, before_body)
        self.counters["fts_documents"] += int(search.maintain(self.connection, key, kind, ident, before_search, search.inputs(after, body)))
        relation_counts = relations.maintain(self.connection, kind, ident, before if operation != "create" else None, after,
                                             before_body=before_body, after_body=body)
        for name, count in relation_counts.items():
            self.counters[name] += count
        revision, last_seq, archived, deleted = self.connection.execute(
            "SELECT revision,last_seq,archived,deleted FROM entity_core WHERE entity_key=?", (key,)).fetchone()
        entity = {"kind": kind, "id": ident, "revision": revision, "last_seq": last_seq,
                  "archived": bool(archived), "deleted": bool(deleted), "fields": deepcopy(after), "body": body}
        self.affected[(kind, ident)] = {"kind": kind, "id": ident, "revision": entity["revision"], "last_seq": self.seq,
                                       "fields": deepcopy(after)}
        file = projection_path(kind, ident, entity["archived"])
        if prior_file and prior_file != file:
            projected = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (prior_file,)).fetchone()
            self.connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,expected_hash) VALUES(?,?,?,?,?,?,?)",
                                    (key, entity["revision"], self.seq, prior_file, "delete", encode(entity), projected[0] if projected else None))
        projected = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (file,)).fetchone()
        self.connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,expected_hash) VALUES(?,?,?,?,?,?,?)",
                                (key, entity["revision"], self.seq, file, "write", encode(entity), projected[0] if projected else None))

    def create(self, kind, doc, body=None):
        if body is not None and not isinstance(body, str):
            raise ValueError("document body must be text or null")
        doc = deepcopy(doc)
        ident = _reserve_id(self.connection, kind, doc)
        doc["id"] = ident
        key = _put_entity(self.connection, {"kind": kind, "id": ident, "doc": doc, "body": body,
                                          "rev": 1, "updated_seq": self.seq, "archived": int(bool(doc.get("archived"))),
                                          "deleted": 0, "epic": doc.get("epic"), "status": doc.get("status")})
        _put_relations(self.connection, key, kind, doc)
        for table in schema.RELATIONS:
            self.connection.execute(f"UPDATE {table} SET target_key=? WHERE target_kind=? AND target_id=? AND target_key IS NULL", (key, kind, ident))
        self._changed(key, kind, ident, {}, doc, body, "create")
        return ident

    def replace(self, kind, ident, after, body, *, before_entity=None):
        if after.get("id") != ident:
            raise ValueError("entity identity is immutable")
        if body is not None and not isinstance(body, str):
            raise ValueError("document body must be text or null")
        entity = before_entity if before_entity is not None else self.snapshot.get(kind, ident, include_body=True)
        before = entity["fields"]
        key = self.connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()[0]
        for field in set(before) | set(after):
            if (field in before) != (field in after) or encode(before.get(field)) != encode(after.get(field)):
                _write_field(self.connection, key, kind, field, after.get(field), remove=field not in after)
        if entity["body"] != body:
            self.connection.execute("UPDATE entity_documents SET body=? WHERE entity_key=?", (body, key))
        if before.get("archived") != after.get("archived"):
            self.connection.execute("UPDATE entity_core SET archived=? WHERE entity_key=?", (int(bool(after.get("archived"))), key))
        if before.get("epic") != after.get("epic") or before.get("status") != after.get("status"):
            self.connection.execute("UPDATE compatibility_entity_state SET epic=?,status=? WHERE entity_key=?", (after.get("epic"), after.get("status"), key))
        self._changed(key, kind, ident, before, after, body, "update", before_body=entity["body"],
                      prior_file=projection_path(kind, ident, entity["archived"]))

    def apply(self, operation, arguments):
        if operation == "batch":
            for item in arguments["commands"]:
                self.apply(item["operation"], item["arguments"])
        elif operation == "task.patch":
            ident = arguments["id"]
            entity = self.snapshot.get("task", ident, include_body=True)
            before, after = entity["fields"], deepcopy(entity["fields"])
            after.update(arguments.get("set", {}))
            for field in arguments.get("remove", []):
                after.pop(field, None)
            if "tldr" in arguments.get("set", {}):
                after.pop("tldr_autogen", None)
            self.replace("task", ident, after, entity["body"], before_entity=entity)
        elif operation == "note.create":
            from taskmaster.taskmaster_v3 import build_note_doc
            doc, body = build_note_doc(**arguments)
            self.create("note", doc, body)
        else:
            from . import lifecycle
            lifecycle.apply(self, operation, arguments)


def execute(connection: sqlite3.Connection, envelope, *, cancelled=lambda: False, checkpoint=lambda stage: None):
    request, fingerprint = contracts.validate(envelope)
    if connection.in_transaction:
        raise RuntimeError("command requires its own transaction")
    if cancelled():
        raise CancelledBeforeExecution("cancelled before execution")
    assert_native(connection)
    original_fk = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys=ON")
    # These connections belong to the native service; acknowledgement is FULL.
    connection.execute("PRAGMA synchronous=FULL")
    try:
        connection.execute("BEGIN IMMEDIATE")
        identity = assert_native(connection)
        if cancelled():
            raise CancelledBeforeExecution("cancelled while waiting for admission")
        if request["store_id"] != identity["store_id"]:
            raise Conflict("request targets a different store identity")
        previous = receipts.lookup(connection, request, fingerprint)
        if previous is not None:
            connection.rollback()
            return previous
        for expected in request["expected_revisions"]:
            row = connection.execute("SELECT revision FROM entity_core WHERE kind=? AND public_id=? AND deleted=0", (expected["kind"], expected["id"])).fetchone()
            if row is None or row[0] != expected["revision"]:
                raise Conflict(f"revision conflict for {expected['kind']} {expected['id']}")
        checkpoint("admitted")
        transaction = Transaction(connection, request, identity)
        try:
            transaction.apply(request["operation"], request["arguments"])
        finally:
            transaction.snapshot.active = False
        checkpoint("mutated")
        outcome = {"operation": request["operation"], "request_id": request.get("request_id"), "store_id": identity["store_id"],
                   "affected": list(transaction.affected.values()), "commit_seq": transaction.seq,
                   "projection_state": "pending" if transaction.affected else "unchanged", "work": transaction.counters}
        receipts.save(connection, request, fingerprint, outcome)
        checkpoint("receipt")
        if transaction.affected:
            _put_manifest(connection, event_high_water=transaction.seq)
        checkpoint("before_commit")
        connection.commit()
        return outcome
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={int(original_fk)}")
