"""Domain event groups share the command's writer transaction."""
from datetime import datetime, timezone
from . import schema
from .migrate import encode


def columns(connection) -> bool:
    """True once the store is at schema 5 (domain_events has actor and entity_key)."""
    return {"actor", "entity_key"} <= {row[1] for row in connection.execute("PRAGMA table_info(domain_events)")}


def _entity_key(connection, kind, ident, known):
    if kind in schema.SINGLETON_KINDS:
        return None
    if known is not None:
        return known
    row = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()
    return None if row is None else row[0]


def append(connection, request, group, kind, ident, operation, before, after, *, entity_key=None, extended=None):
    if group is None:
        group = connection.execute("INSERT INTO command_commits(operation) VALUES(?)", (request["operation"],)).lastrowid
    if extended is None:
        extended = columns(connection)
    values = (datetime.now(timezone.utc).isoformat(), request["caller_scope"], request["operation"], kind, ident, operation,
              encode(sorted(set(before) | set(after))), encode(before), encode(after), group)
    if extended:
        actor = request.get("actor")
        seq = connection.execute(
            "INSERT INTO domain_events(ts,session,tool,kind,id,op,fields,before,after,commit_key,actor,entity_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            values + (None if actor is None else encode(actor), _entity_key(connection, kind, ident, entity_key))).lastrowid
    else:
        seq = connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op,fields,before,after,commit_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 values).lastrowid
    connection.execute("UPDATE command_commits SET first_seq=COALESCE(first_seq,?),final_seq=? WHERE commit_key=?", (seq, seq, group))
    return group, seq
