"""Explicit, instrumented legacy SQL materialization on a private memory DB.

No native private table ever enters this connection. The unchanged authorizer
therefore needs no view-source exemptions (which CTE names could impersonate).
This deliberately expensive compatibility operation is never a normal query.

The statement is untrusted (on a team server, any member's agent writes it). The copy runs here under the
call's deadline; the statement itself runs only in a `query_worker` child process, which is killed at the deadline
and, on Linux, cannot grow past a fixed memory cap, so no statement can take the daemon's memory or cores whatever
SQLite does inside one step (security review I-1, round 2). Stored values are truncated to a fixed size on the copy,
so the child's value limit never depends on what a member stored. The copy itself streams in batches under a fixed
byte budget (SNAPSHOT_MAX_BYTES), so a store that members grew past it is refused rather than copied (round 3).
"""
from contextlib import closing
import sqlite3
import time

from taskmaster import query_guard
from . import query_worker
from .migrate import encode, iter_entities

# Re-exported for callers and tests; the child enforces them (query_worker.run_statement).
VALUE_LENGTH_LIMIT = query_worker.VALUE_LENGTH_LIMIT
VALUE_COPY_MAX = query_worker.VALUE_COPY_MAX
MAX_COLUMNS = query_worker.MAX_COLUMNS
RESULT_MAX_BYTES = query_worker.RESULT_MAX_BYTES
PROGRESS_INSTRUCTIONS = query_worker.PROGRESS_INSTRUCTIONS
# The most the snapshot's values may come to. Every write appends history (a 900 KB note written 100 times made a
# 262 MiB store whose copy took the daemon past MemoryMax), so the copy is refused past this, while it is built. Real
# stores: claude-tools' (617 entities, 1,231 changes) is 7.6 MiB, this repo's 0.1 MiB. The daemon holds at most this
# plus one batch while copying, then the serialized bytes; query_worker's memory note has the whole budget.
SNAPSHOT_MAX_BYTES = query_worker.SNAPSHOT_MAX_BYTES
COPY_BATCH_ROWS = 256
COPY_BATCH_BYTES = 4 * 1024 * 1024


class StoreTooLarge(ValueError):
    """The store's snapshot would pass SNAPSHOT_MAX_BYTES."""


def _interrupted(deadline) -> None:
    if deadline.expired or deadline():
        raise sqlite3.OperationalError("interrupted")


def _value_bytes(value) -> int:
    if isinstance(value, str):
        return len(value) if value.isascii() else len(value.encode("utf-8", "surrogatepass"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return 8


def _too_large() -> StoreTooLarge:
    return StoreTooLarge(
        f"the store is too large for backlog_query on this server: its query snapshot passes "
        f"{SNAPSHOT_MAX_BYTES // 2**20} MiB (every write adds history to it). Use backlog_search, backlog_list_tasks, "
        "backlog_get_task or backlog_changes_since instead, or ask the operator to run the query on the host.")


class _Budget:
    """Rows clipped and counted one at a time, handed on in small batches; past SNAPSHOT_MAX_BYTES it raises before
    the row that crossed it is kept (a batch of 256 history rows of 1.8 MB each would be 460 MB on its own)."""

    def __init__(self):
        self.size = 0

    def batches(self, rows):
        batch, held = [], 0
        for row in rows:
            row = _clipped((row,))[0]
            cost = sum(_value_bytes(v) for v in row)
            self.size += cost
            if self.size > SNAPSHOT_MAX_BYTES:
                raise _too_large()
            batch.append(row)
            held += cost
            if len(batch) >= COPY_BATCH_ROWS or held >= COPY_BATCH_BYTES:
                yield batch
                batch, held = [], 0
        if batch:
            yield batch


def _cursor_rows(cursor):
    # Row by row (the sqlite3 cursor steps one row per next()), never a fetchmany of large rows.
    for row in cursor:
        yield tuple(row)


def _clipped(values) -> list:
    """Rows with every value cut to VALUE_COPY_MAX bytes (UTF-8 for text): a stored value never
    widens what a statement may build, and every row stays readable under the child's fixed limit."""
    out = []
    for row in values:
        if any(isinstance(v, (str, bytes)) and len(v) > VALUE_COPY_MAX // 4 for v in row):
            row = tuple(_clip(v) for v in row)
        out.append(row)
    return out


def _clip(value):
    if isinstance(value, bytes):
        return value[:VALUE_COPY_MAX]
    if isinstance(value, str) and len(value) > VALUE_COPY_MAX // 4:
        data = value.encode("utf-8", "surrogatepass")
        if len(data) > VALUE_COPY_MAX:
            return data[:VALUE_COPY_MAX].decode("utf-8", "ignore")
    return value


def query(source, statement, *, limit=500, timeout=None, authorizer=None, deadline=None):
    from .queries import page_limit
    page_limit(limit)
    statement = query_guard.validate(statement)
    seconds = query_guard.QUERY_TIMEOUT_S if timeout is None else min(float(timeout), query_guard.QUERY_TIMEOUT_S)
    if not 0 < seconds <= query_guard.QUERY_TIMEOUT_S:
        raise ValueError("timeout must be positive and no larger than the SQL deadline")
    counts = {}
    # One deadline for the whole call: the copy is most of the work on a large store, and a statement that waits
    # for its own deadline only after a free copy let one key keep several cores busy (review I-1).
    deadline = deadline or query_guard.Deadline(seconds)
    budget = _Budget()
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as target:
        target.set_progress_handler(deadline, PROGRESS_INSTRUCTIONS)
        target.execute("BEGIN")
        for table in query_guard.TABLES:
            _interrupted(deadline)
            if table == "entity_fts":
                target.execute("CREATE VIRTUAL TABLE entity_fts USING fts5(kind UNINDEXED,id UNINDEXED,title,body,tokenize='porter unicode61')")
                source_rows = _cursor_rows(source.execute("SELECT kind,id,title,body FROM document_search ORDER BY rowid"))
            else:
                info = source.execute(f'PRAGMA table_info("{table}")').fetchall()
                # Preserve column order/types, with no writes/triggers/private data.
                columns = [(r[1], r[2]) for r in info]
                if not columns:
                    raise RuntimeError(f"Missing retained SQL compatibility surface: {table}")
                definitions = ",".join('"' + name.replace('"', '""') + '" ' +
                                       (sql_type if sql_type.upper() in ("TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC") else "BLOB")
                                       for name, sql_type in columns)
                target.execute(f'CREATE TABLE "{table}"({definitions})')
                if table == "entities":
                    source_rows = (tuple(encode(r[name]) if name == "doc" else r[name] for name, _ in columns)
                                   for r in iter_entities(source, check=lambda: _interrupted(deadline)))
                elif table == "changes":
                    names = ",".join('"' + name + '"' for name, _ in columns)
                    source_rows = _cursor_rows(source.execute(f"SELECT {names} FROM domain_events ORDER BY seq"))
                else:
                    source_rows = _cursor_rows(source.execute(f'SELECT * FROM "{table}"'))
            # Batch by batch, under the deadline and the byte budget: the daemon never holds a table, only a batch.
            counts[table] = 0
            for batch in budget.batches(source_rows):
                _interrupted(deadline)
                target.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" for _ in batch[0])})', batch)
                counts[table] += len(batch)
        # The tool's private tables exist, empty, so a statement naming one is
        # refused by the authorizer exactly as on the legacy store rather than
        # failing earlier as a missing table.
        target.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        target.execute("CREATE TABLE projection_base(file TEXT PRIMARY KEY, content BLOB)")
        target.commit()
        # Handed over in a one-item list the pool empties, so the daemon drops its copy once the child has it.
        snapshot = [target.serialize()]
    snapshot_bytes = len(snapshot[0])
    # A caller that reports *why* a statement was refused passes its own authorizer and deadline and reads
    # `denial`/`expired` off them; the child builds its own copies and reports both back.
    authorizer = authorizer or query_guard.Authorizer(query_guard.declared_names(statement))
    _interrupted(deadline)
    remaining = max(0.0, deadline.expires_at - time.monotonic())
    try:
        answer = query_worker.POOL.run(snapshot, statement, limit=limit, readable=authorizer.readable,
                                       declared=authorizer.declared, seconds=remaining,
                                       result_max_bytes=RESULT_MAX_BYTES,
                                       deny_functions=getattr(authorizer, "deny_functions", True))
    except query_worker.WorkerStopped as exc:
        if str(exc) == "interrupted":
            deadline.expired = True
        raise
    if answer.get("denial") and authorizer.denial is None:
        authorizer.denial = answer["denial"]
    if answer.get("expired"):
        deadline.expired = True
    if not answer.get("ok"):
        kind, message = answer.get("type"), str(answer.get("error"))
        if kind == "ValueError":
            raise ValueError(message)
        if kind == "MemoryError":
            raise query_worker.WorkerStopped(f"the query ran out of its {query_worker.memory_cap(snapshot_bytes) // 2**20}"
                                             " MiB memory cap: select fewer or smaller values")
        error = getattr(sqlite3, kind, None) if isinstance(kind, str) else None
        if not (isinstance(error, type) and issubclass(error, sqlite3.Error)):
            error = sqlite3.OperationalError
        raise error(message)
    return {"columns": answer["columns"], "rows": answer["rows"], "truncated": bool(answer["truncated"]),
            "materialized_rows": counts}
