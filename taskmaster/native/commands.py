"""One native transaction owner. Initial slices: task metadata and sticky notes.

No activation, file I/O, projection rendering or remote calls happen here.
Receipts capture committed outcomes inside the writer transaction. New command
families must use this same owner and the existing domain rules, not fork it.
"""
from copy import deepcopy
import re
import sqlite3

from . import contracts, events, metrics, neighbourhood, receipts, schema, search, relations
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
# These identities are caller-derived, never allocated: the caller owns the name.
NAMED_KINDS = ("task", "epic", "phase", "area", "tracker", "project", "backlog")


def _taken(connection, kind, ident):
    return connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=? UNION ALL "
                              "SELECT 1 FROM entity_core WHERE kind=? AND public_id=? LIMIT 1",
                              (kind, ident, kind, ident)).fetchone() is not None


def _high_water(connection, kind, prefix):
    """Highest number this database has ever handed out under `prefix`.

    The counter is the imported local-state authority; for epic-prefixed task
    ids a brand-new epic has no counter to import, so the live rows and the
    tombstone reservations seed it. Both sources are consulted so a removed id
    is never recycled.
    """
    row = connection.execute("SELECT high_water FROM id_counters WHERE kind=? AND prefix=?", (kind, prefix)).fetchone()
    high = row[0] if row else 0
    if row is not None and kind != "task":
        return high
    if row is None and kind != "task":
        raise RuntimeError(f"{kind} ID high-water was not imported; refusing allocation")
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    for statement in ("SELECT public_id FROM entity_core WHERE kind=? AND public_id LIKE ? ESCAPE '\\'",
                      "SELECT public_id FROM id_reservations WHERE kind=? AND public_id LIKE ? ESCAPE '\\'"):
        for (ident,) in connection.execute(statement, (kind, like)):
            match = pattern.fullmatch(ident)
            if match:
                high = max(high, int(match.group(1)))
    return high


def _reserve_id(connection, kind, doc, requested=None):
    if requested is not None:
        if kind != "project":   # the manifest singleton owns a reserved sentinel id
            contracts._identifier(requested, f"{kind} id")
        if _taken(connection, kind, requested):
            raise ValueError(f"{kind} ID `{requested}` already exists")
        connection.execute("INSERT INTO id_reservations VALUES(?,?)", (kind, requested))
        return requested
    if kind == "task":
        epic = str(doc.get("epic") or "").strip()
        if not epic:
            raise ValueError("task epic is required for id allocation")
        prefix = f"{epic}-"
    elif kind in NAMED_KINDS:
        raise ValueError(f"caller-derived id required for {kind}")
    elif kind == "handover":
        from taskmaster.taskmaster_v3 import make_handover_id
        base = make_handover_id(str(doc["date"]), str(doc["tldr"]))
        ident, suffix = base, 2
        while _taken(connection, kind, ident):
            ident, suffix = f"{base}-{suffix}", suffix + 1
        contracts._identifier(ident, "handover id")
        connection.execute("INSERT INTO id_reservations VALUES(?,?)", (kind, ident))
        return ident
    else:
        prefix = PREFIXES[kind]
    high = _high_water(connection, kind, prefix)
    while True:
        high += 1
        ident = f"{prefix}{high:03d}"
        if not _taken(connection, kind, ident):
            break
    connection.execute("INSERT INTO id_counters VALUES(?,?,?) ON CONFLICT(kind,prefix) DO UPDATE SET high_water=excluded.high_water", (kind, prefix, high))
    connection.execute("INSERT INTO id_reservations VALUES(?,?)", (kind, ident))
    return ident


def projection_path(kind, ident, archived):
    if kind == "project":
        return "project.yaml"
    directories = {"task": "tasks", "note": "notes", "decision": "decisions", "bug": "bugs", "issue": "issues",
                   "idea": "ideas", "handover": "handovers", "epic": "epics", "phase": "phases",
                   "area": "areas", "tracker": "trackers"}
    directory = directories.get(kind)
    if directory is None:
        return None   # the backlog document is rendered as a whole, not per entity
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
        # Entities a write left exactly as stored, with the document the command
        # observed. A reply to a no-op names this value, never a later read.
        self.unchanged = {}
        # Every counter reports work actually issued. A "global rebuild" counter
        # lived here that nothing could increment, so it proved nothing; the
        # absence of graph work is asserted directly against the SQL instead.
        self.counters = {"fts_documents": 0, "path_comparisons": 0, "link_pairs": 0, "handover_pairs": 0}

    def _changed(self, key, kind, ident, before, after, body, operation, *, before_body=None, prior_file=None):
        # Canonical JSON equality distinguishes True from 1 and absent from null.
        fields = {f for f in set(before) | set(after) if (f in before) != (f in after) or encode(before.get(f)) != encode(after.get(f))}
        body_changed = before_body != body
        if not fields and not body_changed and operation != "create":
            self.unchanged[(kind, ident)] = {"kind": kind, "id": ident, "fields": deepcopy(after)}
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
        if file is None:
            return
        if prior_file and prior_file != file:
            projected = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (prior_file,)).fetchone()
            self.connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,expected_hash) VALUES(?,?,?,?,?,?,?)",
                                    (key, entity["revision"], self.seq, prior_file, "delete", encode(entity), projected[0] if projected else None))
        projected = self.connection.execute("SELECT content_hash FROM projection WHERE file=?", (file,)).fetchone()
        self.connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,expected_hash) VALUES(?,?,?,?,?,?,?)",
                                (key, entity["revision"], self.seq, file, "write", encode(entity), projected[0] if projected else None))

    def create(self, kind, doc, body=None, requested_id=None):
        if body is not None and not isinstance(body, str):
            raise ValueError("document body must be text or null")
        doc = deepcopy(doc)
        ident = _reserve_id(self.connection, kind, doc, requested_id)
        # The store stamps the id on every document it creates, the project
        # manifest included (a later replace may drop it, as the tool's put does).
        if kind != "backlog":
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
        if kind not in ("backlog", "project") and after.get("id") != ident:
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

    def import_document(self, kind, ident, section, path, body):
        """Store an external document's prose so a read never needs its file.

        This is not an authored-field change: `entity_core` keeps its revision, and
        the import is recorded as its own domain event so the change feed sees it
        and `imported_seq` is a real sequence. Re-importing identical prose writes
        nothing and appends no event, which is what makes the caller's retry cheap.
        """
        import hashlib
        key, revision = self.connection.execute(
            "SELECT entity_key,revision FROM entity_core WHERE kind=? AND public_id=? AND deleted=0",
            (kind, ident)).fetchone()
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = self.connection.execute(
            "SELECT path,content_hash,imported_seq FROM external_documents WHERE entity_key=? AND section=?",
            (key, section)).fetchone()
        if row is not None and (row[0], row[1]) == (path, content_hash):
            return row[2]
        before = {} if row is None else {"section": section, "path": row[0], "content_hash": row[1]}
        after = {"section": section, "path": path, "content_hash": content_hash}
        self.group, self.seq = events.append(self.connection, self.request, self.group, kind, ident,
                                             "document.import", before, after)
        self.connection.execute(
            "INSERT INTO external_documents VALUES(?,?,?,?,?,?) ON CONFLICT(entity_key,section) DO UPDATE SET "
            "path=excluded.path,body=excluded.body,content_hash=excluded.content_hash,"
            "imported_seq=excluded.imported_seq", (key, section, path, body, content_hash, self.seq))
        self.affected[(kind, ident)] = {"kind": kind, "id": ident, "revision": revision,
                                        "last_seq": self.seq, "fields": {}}
        return self.seq

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
    # Opt-in N16 work counters: one flag check per point when disabled.
    meter = metrics.CommandMeter(connection, request) if metrics.ENABLED else None
    try:
        if meter:
            meter.begin()
        connection.execute("BEGIN IMMEDIATE")
        if meter:
            meter.locked()
        identity = assert_native(connection)
        if cancelled():
            raise CancelledBeforeExecution("cancelled while waiting for admission")
        if request["store_id"] != identity["store_id"]:
            raise Conflict("request targets a different store identity")
        previous = receipts.lookup(connection, request, fingerprint)
        if previous is not None:
            connection.rollback()
            if meter:
                meter.replayed(previous)
            return previous
        for expected in request["expected_revisions"]:
            row = connection.execute("SELECT revision FROM entity_core WHERE kind=? AND public_id=? AND deleted=0", (expected["kind"], expected["id"])).fetchone()
            if row is None or row[0] != expected["revision"]:
                raise Conflict(f"revision conflict for {expected['kind']} {expected['id']}")
        # Backfill builds the native graph indexes; this upgrades a store activated
        # before N14, only once the command is admitted (a lookup when present).
        neighbourhood.ensure_indexes(connection)
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
        unchanged = [item for key, item in transaction.unchanged.items() if key not in transaction.affected]
        if unchanged:
            outcome["unchanged"] = unchanged
        if hasattr(transaction, 'result'):
            outcome['result'] = transaction.result
        receipts.save(connection, request, fingerprint, outcome)
        checkpoint("receipt")
        if transaction.affected:
            _put_manifest(connection, event_high_water=transaction.seq)
        checkpoint("before_commit")
        if meter:
            meter.commit(outcome)
        connection.commit()
        return outcome
    except BaseException as exc:
        connection.rollback()
        if meter:
            meter.failed(exc)
        raise
    finally:
        if meter:
            meter.close()
        connection.execute(f"PRAGMA foreign_keys={int(original_fk)}")
