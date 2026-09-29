"""Bounded, atomic application of coordinator-prepared projection observations.

No file reads, parsing, rendering or process work occurs in this module. The
coordinator observes and rechecks disk; this command fences that observation
against the database snapshot and retains external bytes before changing state.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from taskmaster.projection_parse import BACKLOG_ID, PROJECT_ID, classify
from . import checkouts, events, projection, schema
from .contracts import Conflict, _identifier
from .migrate import encode
from .sync_merge import protect_local

OPERATIONS = {"sync.apply", "sync.begin", "sync.finish", "sync.job"}
# `backlog_sync` job records (coordinator/sync_jobs.py): one row per job id, written when the
# job starts ("running") and when it ends; an ended record is immutable.
JOB_PREFIX = "sync.job."
JOB_STATES = {"running", "complete", "incomplete", "failed"}
MAX_ROWS = 100
MAX_FILES = 10000
OPERATION_PREFIX = "sync.operation."
MODES = {"apply", "conflict", "quarantine", "repair", "observe"}
_FIELDS = {"file", "mode", "rows", "reason", "observed_base64", "observed_hash", "expected_manifest"}
_MANIFEST_COLUMNS = "kind,id,content_hash,dirty,quarantined,exported_seq,quarantine_hash"


def _checkout_id(value):
    return isinstance(value, str) and re.fullmatch(r"wt-[0-9a-f]{24}", value) is not None


def manifest_token(connection, rel):
    """Stable precondition over publication, held bytes and the trusted base."""
    record = connection.execute(f"SELECT {_MANIFEST_COLUMNS} FROM projection WHERE file=?", (rel,)).fetchone()
    base = connection.execute("SELECT content FROM projection_base WHERE file=?", (rel,)).fetchone()
    conflict = None
    if projection._has_conflict_table(connection):
        conflict = connection.execute("SELECT kind,id,file_hash,flagged_at FROM projection_conflict WHERE file=?", (rel,)).fetchone()
    state = {"projection": None if record is None else list(record),
             "base": None if base is None or base[0] is None else hashlib.sha256(bytes(base[0])).hexdigest(),
             "conflict": None if conflict is None else list(conflict)}
    return hashlib.sha256(encode(state).encode("utf-8")).hexdigest()


def observed_bytes(arguments):
    raw = arguments["observed_base64"]
    return None if raw is None else base64.b64decode(raw, validate=True)


def validate_input(value):
    if not isinstance(value, dict) or set(value) - {"checkout"} != {"import_files", "through", "files", "take_file"}:
        raise ValueError("invalid sync input")
    if "checkout" in value and not _checkout_id(value["checkout"]):
        raise ValueError("invalid sync checkout")
    if type(value["import_files"]) is not bool or type(value["take_file"]) is not bool:
        raise ValueError("sync import_files/take_file must be boolean")
    if type(value["through"]) is not int or not 0 <= value["through"] < 2 ** 63:
        raise ValueError("flush_through must be a non-negative sequence")
    files = value["files"]
    if files is not None:
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
            raise ValueError(f"sync files must contain 1 to {MAX_FILES} paths")
        identities = set()
        for rel in files:
            key = classify(rel)
            if key in identities:
                raise ValueError("sync files contain duplicate entity identities")
            identities.add(key)
    if value["take_file"] and (not value["import_files"] or files is None or len(files) != 1):
        raise ValueError("take_file requires exactly one explicitly named imported file")


def operation_state(connection, scope):
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (OPERATION_PREFIX + scope,)).fetchone()
    return None if row is None else json.loads(row[0])


def job_record(connection, ident):
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (JOB_PREFIX + ident,)).fetchone()
    return None if row is None else json.loads(row[0])


def validate(operation, arguments):
    if operation == "sync.job":
        record = arguments.get("record")
        if (set(arguments) != {"id", "record"} or not isinstance(arguments["id"], str)
                or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", arguments["id"])):
            raise ValueError("sync.job requires a job id and record")
        if not isinstance(record, dict) or record.get("state") not in JOB_STATES:
            raise ValueError("invalid sync job record")
        return
    if operation == "sync.begin":
        if set(arguments) != {"input"}:
            raise ValueError("sync.begin requires input")
        validate_input(arguments["input"])
        return
    if operation == "sync.finish":
        result = arguments.get("result")
        if set(arguments) != {"result"} or not isinstance(result, dict) or result.get("state") != "synchronized":
            raise ValueError("only a successful sync can be durably completed")
        if type(result.get("through")) is not int or result["through"] < 0:
            raise ValueError("invalid completed sync sequence")
        return
    if set(arguments) - {"checkout"} != _FIELDS:
        raise ValueError("sync.apply requires file, mode, rows, reason, observed bytes/hash and expected_manifest")
    kind, ident = classify(arguments["file"])
    mode = arguments["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("invalid sync mode")
    if "checkout" in arguments:
        # A linked checkout's observation fences its own base; it never repairs or
        # observes the main checkout's manifest.
        if not _checkout_id(arguments["checkout"]):
            raise ValueError("invalid sync checkout")
        if mode not in {"apply", "conflict", "quarantine"}:
            raise ValueError("a linked checkout import is apply, conflict or quarantine")
    if not isinstance(arguments["reason"], str) or len(arguments["reason"]) > 4096:
        raise ValueError("sync reason must be bounded text")
    token = arguments["expected_manifest"]
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("invalid projection manifest precondition")
    raw, digest = arguments["observed_base64"], arguments["observed_hash"]
    if (raw is None) != (digest is None) or (raw is None) != (mode == "repair"):
        raise ValueError("only missing-file repair omits observed bytes/hash")
    if raw is not None:
        if not isinstance(raw, str) or not isinstance(digest, str):
            raise ValueError("observed bytes/hash must be text")
        try:
            content = observed_bytes(arguments)
        except (ValueError, binascii.Error):
            raise ValueError("observed_base64 is not base64") from None
        if hashlib.sha1(content).hexdigest() != digest:
            raise ValueError("observed_hash does not match observed bytes")
    rows = arguments["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError(f"sync.apply accepts at most {MAX_ROWS} entity rows")
    if rows and mode not in {"apply", "conflict"}:
        raise ValueError("only accepted imports may carry entity changes")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"kind", "id", "revision", "fields", "body"}:
            raise ValueError("invalid import row")
        row_kind, row_id = row["kind"], row["id"]
        if not isinstance(row_kind, str) or row_kind not in schema.KINDS:
            raise ValueError("invalid import entity kind")
        if kind == "backlog":
            if row_kind == "task":
                raise ValueError("backlog import cannot replace task rows; tasks are owned by their task documents")
            if row_kind not in {"backlog", "epic", "phase"}:
                raise ValueError("backlog import cannot replace indexed narrative rows")
            if row_kind == "backlog" and row_id != BACKLOG_ID:
                raise ValueError("invalid backlog singleton")
        elif (row_kind, row_id) != (kind, ident or PROJECT_ID):
            raise ValueError("import entity does not match its file identity")
        if row_kind not in {"backlog", "project"}:
            _identifier(row_id, "import id")
        key = (row_kind, row_id)
        if key in seen:
            raise ValueError("duplicate import entity")
        seen.add(key)
        if type(row["revision"]) is not int or row["revision"] < 0:
            raise ValueError("invalid import revision")
        if not isinstance(row["fields"], dict) or not all(isinstance(key, str) for key in row["fields"]):
            raise ValueError("import fields must be an object with string keys")
        if row_kind not in {"backlog", "project"} and row["fields"].get("id") != row_id:
            raise ValueError("import document identity is immutable")
        if row["body"] is not None and not isinstance(row["body"], str):
            raise ValueError("import body must be text or null")


def _base(connection, rel, kind, ident, content, digest):
    connection.execute(
        "INSERT INTO projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq) "
        "VALUES(?,?,?,?,NULL,?,1,0,NULL) ON CONFLICT(file) DO UPDATE SET kind=excluded.kind,id=excluded.id,"
        "content_hash=excluded.content_hash,mtime=NULL,size=excluded.size,dirty=1,quarantined=0,"
        "quarantine_mtime=NULL,quarantine_size=NULL,quarantine_hash=NULL,exported_seq=NULL",
        (rel, kind, ident, digest, len(content)))
    connection.execute("INSERT INTO projection_base(file,content) VALUES(?,?) "
                       "ON CONFLICT(file) DO UPDATE SET content=excluded.content", (rel, content))


def _advance_counter(connection, kind, ident, fields):
    from .commands import PREFIXES
    prefix = PREFIXES.get(kind)
    if kind == "task" and fields.get("epic"):
        prefix = str(fields["epic"]) + "-"
    match = re.fullmatch(re.escape(prefix) + r"(\d+)", ident) if prefix else None
    if match:
        high = int(match.group(1))
        if high >= 2 ** 63 - 1:
            raise ValueError("imported numeric ID exhausts the allocator range")
        connection.execute("INSERT INTO id_counters(kind,prefix,high_water) VALUES(?,?,?) "
                           "ON CONFLICT(kind,prefix) DO UPDATE SET high_water=MAX(high_water,excluded.high_water)",
                           (kind, prefix, high))


def _queue_entity(transaction, kind, ident, rel):
    from .commands import projection_path
    connection = transaction.connection
    if kind == "backlog":
        connection.execute("UPDATE projection SET exported_seq=NULL,dirty=1 WHERE file=?", (rel,))
        return
    ident = ident or PROJECT_ID
    try:
        entity = transaction.snapshot.get(kind, ident, include_body=True, include_deleted=True)
    except KeyError:
        return
    key = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()[0]
    target = None if entity["deleted"] else projection_path(kind, ident, entity["archived"])
    files = [(rel, "delete")] if target != rel else []
    if target is not None:
        files.append((target, "write"))
    for file, effect in files:
        record = connection.execute("SELECT content_hash FROM projection WHERE file=?", (file,)).fetchone()
        connection.execute("INSERT INTO projection_jobs(entity_key,revision,commit_seq,file,effect,input_json,expected_hash) "
                           "VALUES(?,?,?,?,?,?,?)", (key, entity["revision"], transaction.seq, file, effect,
                                                     encode(entity), record[0] if record else None))
    connection.execute("UPDATE projection_jobs SET state='pending',lease_owner=NULL,lease_until=NULL "
                       "WHERE state='conflict' AND (entity_key=? OR file=?)", (key, rel))


def apply(transaction, operation, arguments):
    connection = transaction.connection
    if operation == "sync.job":
        ident, record = arguments["id"], arguments["record"]
        before = job_record(connection, ident)
        if before is not None and before["state"] != "running" and before != record:
            raise Conflict("an ended sync job record is immutable")
        connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (JOB_PREFIX + ident, encode(record)))
        transaction.result = {"id": ident, "state": record["state"]}
        return
    if operation in {"sync.begin", "sync.finish"}:
        scope = transaction.request["caller_scope"]
        state = operation_state(connection, scope)
        if operation == "sync.begin":
            if state is not None and state["input"] != arguments["input"]:
                raise Conflict("sync operation id reused with different input")
            state = state or {"input": arguments["input"], "state": "active"}
        else:
            if state is None:
                raise Conflict("sync operation was not begun")
            if state["state"] == "complete" and state["result"] != arguments["result"]:
                raise Conflict("completed sync result is immutable")
            state = dict(state, state="complete", result=arguments["result"])
        connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (OPERATION_PREFIX + scope, encode(state)))
        transaction.result = state
        return
    rel, mode = arguments["file"], arguments["mode"]
    kind, ident = classify(rel)
    linked = arguments.get("checkout")
    if linked is not None:
        if checkouts.token(connection, linked, rel) != arguments["expected_manifest"]:
            raise Conflict(f"checkout base changed while parsing {rel}")
    elif manifest_token(connection, rel) != arguments["expected_manifest"]:
        raise Conflict(f"projection manifest changed while parsing {rel}")
    current = {}
    # Revalidate every row before applying any. Rollback also covers allocation,
    # search/relationship maintenance, events, jobs and the durable receipt.
    for row in arguments["rows"]:
        key = row["kind"], row["id"]
        try:
            entity = transaction.snapshot.get(*key, include_body=True, include_deleted=True)
        except KeyError:
            entity = None
        if entity is not None and entity["deleted"]:
            raise Conflict(f"import cannot resurrect tombstone: {key[0]} {key[1]}")
        if (entity["revision"] if entity else 0) != row["revision"]:
            raise Conflict(f"revision changed while parsing {key[0]} {key[1]}")
        if entity is None and connection.execute("SELECT 1 FROM id_reservations WHERE kind=? AND public_id=?", key).fetchone():
            raise Conflict(f"import cannot reuse reserved tombstone: {key[0]} {key[1]}")
        current[key] = entity
    content = observed_bytes(arguments)
    digest = arguments["observed_hash"]
    if mode == "observe":
        record = connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
        if record is None or projection.held_file(connection, rel):
            raise Conflict(f"observed bytes do not match a trusted unheld projection: {rel}")
        if record[0] != digest:
            # D7: bytes equal to the trusted base up to line endings (Git's eol conversion)
            # become the recorded published bytes; nothing else may be observed.
            base = connection.execute("SELECT content FROM projection_base WHERE file=?", (rel,)).fetchone()
            base = None if base is None or base[0] is None else bytes(base[0])
            if (base is None or hashlib.sha1(base).hexdigest() != record[0]
                    or projection._lf(base) != projection._lf(content)):
                raise Conflict(f"observed bytes do not match a trusted unheld projection: {rel}")
            connection.execute("UPDATE projection SET content_hash=?,size=?,mtime=NULL WHERE file=?",
                               (digest, len(content), rel))
        connection.execute("INSERT INTO projection_base(file,content) VALUES(?,?) "
                           "ON CONFLICT(file) DO UPDATE SET content=excluded.content", (rel, content))
        transaction.result = {"file": rel, "state": "observed"}
        return

    # Preserve every observed external version in the event's transaction. A
    # future flag can replace the current conflict row but cannot erase history.
    event_id = ident or (BACKLOG_ID if kind == "backlog" else PROJECT_ID)
    flagged = None
    if linked is None and projection._has_conflict_table(connection):
        flagged = connection.execute("SELECT file_content FROM projection_conflict WHERE file=?", (rel,)).fetchone()
    transaction.group, transaction.seq = events.append(
        connection, transaction.request, transaction.group, kind, event_id, "sync.apply",
        {"file_base64": arguments["observed_base64"], "file_hash": digest,
         "flagged_base64": None if flagged is None else base64.b64encode(flagged[0]).decode("ascii")},
        dict({"file": rel, "mode": mode, "reason": arguments["reason"]}, **({"checkout": linked} if linked else {})))
    if linked is not None:
        # The linked checkout's base/hold changes atomically with the import; the
        # main checkout's manifest, flags and quarantine are not this file's.
        if mode == "apply":
            checkouts.set_base(connection, linked, rel, content)
            checkouts.set_hold(connection, linked, rel, None)
        else:
            checkouts.set_hold(connection, linked, rel, "conflict" if mode == "conflict" else "quarantined", digest)
    elif mode in {"apply", "repair"}:
        if projection._has_conflict_table(connection):
            connection.execute("DELETE FROM projection_conflict WHERE file=?", (rel,))
        if mode == "apply":
            _base(connection, rel, kind, ident, content, digest)
        else:
            connection.execute("UPDATE projection SET quarantined=0,quarantine_hash=NULL,quarantine_mtime=NULL,"
                               "quarantine_size=NULL,dirty=1,exported_seq=NULL WHERE file=?", (rel,))
    for row in arguments["rows"]:
        key = row["kind"], row["id"]
        before = current[key]
        fields = protect_local(key[0], row["fields"], before["fields"] if before else None)
        if before is None:
            if key[0] == "backlog":
                raise Conflict("missing backlog authority cannot be created by file import")
            transaction.create(key[0], fields, row["body"], requested_id=key[1])
            _advance_counter(connection, key[0], key[1], fields)
        else:
            transaction.replace(*key, fields, row["body"], before_entity=before)
    if linked is not None:
        if mode == "apply":
            _queue_entity(transaction, kind, ident, rel)
    elif mode == "conflict":
        # The bytes parsed, so a quarantine recorded for them no longer holds; the
        # conflict flag alone keeps the file.
        connection.execute("UPDATE projection SET quarantined=0,quarantine_hash=NULL,quarantine_mtime=NULL,"
                           "quarantine_size=NULL WHERE file=? AND quarantined=1", (rel,))
        projection.ensure_conflict_table(connection)
        connection.execute("INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) "
                           "VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?) ON CONFLICT(file) DO UPDATE SET "
                           "kind=excluded.kind,id=excluded.id,flagged_at=excluded.flagged_at,"
                           "file_hash=excluded.file_hash,file_content=excluded.file_content", (rel, kind, ident, digest, content))
    elif mode == "quarantine":
        connection.execute("INSERT INTO projection(file,kind,id,content_hash,dirty,quarantined,quarantine_hash,quarantine_size) "
                           "VALUES(?,?,?,'',1,1,?,?) ON CONFLICT(file) DO UPDATE SET quarantined=1,"
                           "quarantine_hash=excluded.quarantine_hash,quarantine_size=excluded.quarantine_size,quarantine_mtime=NULL",
                           (rel, kind, ident, digest, len(content)))
    else:
        _queue_entity(transaction, kind, ident, rel)
    transaction.affected.setdefault((kind, event_id), {"kind": kind, "id": event_id, "revision": 0,
                                                       "last_seq": transaction.seq, "fields": {}})
    state = {"apply": "accepted", "repair": "repair_pending", "quarantine": "quarantined", "conflict": "conflict"}[mode]
    transaction.result = {"file": rel, "state": state, "reason": arguments["reason"]}
