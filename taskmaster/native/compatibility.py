"""Legacy SQL surface for `backlog_query`: built once per store revision into a read-only snapshot file, then queried.

No native private table ever enters the snapshot. The unchanged authorizer therefore needs no view-source
exemptions (which CTE names could impersonate). `build` is the deliberately expensive part and runs once per
revision under `query_snapshots` (single flight, own deadline); `query` only leases that file.

The statement is untrusted (on a team server, any member's agent writes it). It runs only in a `query_worker` child
process, which opens the snapshot read-only, is killed at the deadline and, on Linux, cannot grow past a fixed memory
cap, so no statement can take the daemon's memory or cores whatever SQLite does inside one step (security review I-1,
round 2). Stored values are truncated to a fixed size on the copy, so the child's value limit never depends on what a
member stored. The copy streams in batches under a fixed byte budget (SNAPSHOT_MAX_BYTES), so a store that members
grew past it is refused rather than copied (round 3). The host tables (sessions, linear_queue, projection) are built
empty: a caller allowed to read them gets their current rows as a per-call overlay (see `_overlay`), a member's worker
never receives them at all (team-server-034 design, 4.1).
"""
from contextlib import closing
import json
import sqlite3
import time

from taskmaster import query_guard
from . import query_snapshots, query_worker
from .migrate import encode, iter_entities

# Re-exported for callers and tests; the child enforces them (query_worker.run_statement).
VALUE_LENGTH_LIMIT = query_worker.VALUE_LENGTH_LIMIT
VALUE_COPY_MAX = query_worker.VALUE_COPY_MAX
MAX_COLUMNS = query_worker.MAX_COLUMNS
RESULT_MAX_BYTES = query_worker.RESULT_MAX_BYTES
PROGRESS_INSTRUCTIONS = query_worker.PROGRESS_INSTRUCTIONS
# The most the snapshot's values may come to. Every write appends history (a 900 KB note written 100 times made a
# 262 MiB store whose copy took the daemon past MemoryMax), so the copy is refused past this, while it is built. Real
# stores: claude-tools' (617 entities, 1,231 changes) is 7.6 MiB, this repo's 0.1 MiB. The copy streams into a file, so
# it now bounds build time and disk (not memory); query_worker's memory note has the whole budget.
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


HOST_TABLES = tuple(t for t in query_guard.TABLES if t in query_guard.HOST_PRIVATE_TABLES)
# What the per-call overlay (the host tables' rows) may come to, as JSON on the pipe.
OVERLAY_MAX_BYTES = query_worker.OVERLAY_MAX_BYTES


def build(source, path, deadline) -> dict:
    """Write the legacy SQL surface of `source` (inside the caller's read transaction) to a new database file at
    `path`; the rows copied per table. The host tables are created and left empty."""
    counts = {}
    budget = _Budget()
    with closing(sqlite3.connect(path, isolation_level=None)) as target:
        # A throwaway file: no journal, no syncs, a small fixed cache (the daemon holds a batch, never a table).
        target.execute("PRAGMA journal_mode=OFF")
        target.execute("PRAGMA synchronous=OFF")
        target.execute("PRAGMA cache_size=-8192")
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
                if table in HOST_TABLES:
                    # Change without a store revision (heartbeats, export bookkeeping), and host-private: never
                    # in the shared file, so the file is neither stale nor a place a member's worker could read them.
                    counts[table] = 0
                    continue
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
    return counts


def _overlay(source, statement, authorizer):
    """The current rows of the host tables this call may read and its statement names, read in the caller's own
    read transaction; None when there are none. A caller whose readable set excludes a host table (every member of a
    team server) gets nothing, so no host row ever reaches its worker. Names are matched anywhere in the statement
    text, case-insensitively: a superset of the tables it can reach, because SQLite has no way to name a table but
    by its name, and a needless overlay costs under 10 ms."""
    lowered = statement.lower()
    wanted = [t for t in HOST_TABLES if t in authorizer.readable and t in lowered]
    if not wanted:
        return None
    overlay = {}
    size = 0
    for table in wanted:
        cursor = source.execute(f'SELECT * FROM "{table}"')
        rows = overlay[table] = {"columns": [d[0] for d in cursor.description], "rows": []}
        for row in cursor:
            encoded = [query_worker.encode_value(v) for v in row]
            # Refuse while reading, by encoded bytes: a huge table is never fully loaded (the frame cap is on this JSON).
            size += len(json.dumps(encoded).encode("utf-8")) + 1
            if size > OVERLAY_MAX_BYTES:
                break
            rows["rows"].append(encoded)
        if size > OVERLAY_MAX_BYTES:
            break
    if size > OVERLAY_MAX_BYTES or len(json.dumps(overlay).encode("utf-8")) > OVERLAY_MAX_BYTES:
        raise ValueError(f"the host tables (sessions, linear_queue, projection) are too large for backlog_query: "
                         f"over {OVERLAY_MAX_BYTES // 2**20} MiB. Use backlog_search, backlog_list_tasks, "
                         "backlog_get_task or backlog_changes_since instead, or ask the operator to run the query on the host.")
    return overlay


def query(source, statement, *, limit=500, timeout=None, authorizer=None, deadline=None):
    from .queries import page_limit
    page_limit(limit)
    statement = query_guard.validate(statement)
    seconds = query_guard.QUERY_TIMEOUT_S if timeout is None else min(float(timeout), query_guard.QUERY_TIMEOUT_S)
    if not 0 < seconds <= query_guard.QUERY_TIMEOUT_S:
        raise ValueError("timeout must be positive and no larger than the SQL deadline")
    # One deadline for the whole call: waiting for a snapshot build counts against it, like the statement itself.
    deadline = deadline or query_guard.Deadline(seconds)
    # A caller that reports *why* a statement was refused passes its own authorizer and deadline and reads
    # `denial`/`expired` off them; the child builds its own copies and reports both back.
    authorizer = authorizer or query_guard.Authorizer(query_guard.declared_names(statement))
    snapshots = query_snapshots.for_source(source)
    entry = snapshots.acquire(source, deadline)
    try:
        counts = dict(entry.counts)
        overlay = _overlay(source, statement, authorizer)
        for table, data in (overlay or {}).items():
            counts[table] = len(data["rows"])
        _interrupted(deadline)
        remaining = max(0.0, deadline.expires_at - time.monotonic())
        try:
            answer = query_worker.POOL.run(entry.path, statement, limit=limit, readable=authorizer.readable,
                                           declared=authorizer.declared, seconds=remaining,
                                           result_max_bytes=RESULT_MAX_BYTES, overlay=overlay,
                                           deny_functions=getattr(authorizer, "deny_functions", True))
        except query_worker.WorkerStopped as exc:
            if str(exc) == "interrupted":
                deadline.expired = True
            raise
    finally:
        snapshots.release(entry)
    if answer.get("denial") and authorizer.denial is None:
        authorizer.denial = answer["denial"]
    if answer.get("expired"):
        deadline.expired = True
    if not answer.get("ok"):
        kind, message = answer.get("type"), str(answer.get("error"))
        if kind == "SnapshotMissing":
            snapshots.drop(entry)
            raise query_snapshots.SnapshotUnavailable("the query snapshot was not readable; retry the query")
        if kind == "ValueError":
            raise ValueError(message)
        if kind == "MemoryError":
            raise query_worker.WorkerStopped(f"the query ran out of its {query_worker.memory_cap() // 2**20}"
                                             " MiB memory cap: select fewer or smaller values")
        error = getattr(sqlite3, kind, None) if isinstance(kind, str) else None
        if not (isinstance(error, type) and issubclass(error, sqlite3.Error)):
            error = sqlite3.OperationalError
        raise error(message)
    return {"columns": answer["columns"], "rows": answer["rows"], "truncated": bool(answer["truncated"]),
            "materialized_rows": counts}
