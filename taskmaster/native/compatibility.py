"""Explicit, instrumented legacy SQL materialization on a private memory DB.

No native private table ever enters this connection. The unchanged authorizer
therefore needs no view-source exemptions (which CTE names could impersonate).
This deliberately expensive compatibility operation is never a normal query.
"""
from contextlib import closing
import sqlite3

from taskmaster import query_guard
from .migrate import encode, reconstruct_entities


def query(source, statement, *, limit=500, timeout=None, authorizer=None, deadline=None):
    from .queries import page_limit
    page_limit(limit)
    statement = query_guard.validate(statement)
    seconds = query_guard.QUERY_TIMEOUT_S if timeout is None else min(float(timeout), query_guard.QUERY_TIMEOUT_S)
    if not 0 < seconds <= query_guard.QUERY_TIMEOUT_S:
        raise ValueError("timeout must be positive and no larger than the SQL deadline")
    counts = {}
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as target:
        target.execute("BEGIN")
        for table in query_guard.TABLES:
            if table == "entity_fts":
                target.execute("CREATE VIRTUAL TABLE entity_fts USING fts5(kind UNINDEXED,id UNINDEXED,title,body,tokenize='porter unicode61')")
                values = [tuple(r) for r in source.execute("SELECT kind,id,title,body FROM document_search ORDER BY rowid")]
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
                    values = [tuple(encode(r[name]) if name == "doc" else r[name] for name, _ in columns)
                              for r in reconstruct_entities(source)]
                elif table == "changes":
                    names = ",".join('"' + name + '"' for name, _ in columns)
                    values = [tuple(r) for r in source.execute(f"SELECT {names} FROM domain_events ORDER BY seq")]
                else:
                    values = [tuple(r) for r in source.execute(f'SELECT * FROM "{table}"')]
            counts[table] = len(values)
            if values:
                target.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" for _ in values[0])})', values)
        # The tool's private tables exist, empty, so a statement naming one is
        # refused by the authorizer exactly as on the legacy store rather than
        # failing earlier as a missing table.
        target.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        target.execute("CREATE TABLE projection_base(file TEXT PRIMARY KEY, content BLOB)")
        target.commit()
        # A caller that reports *why* a statement was refused passes its own
        # authorizer and deadline and reads `denial`/`expired` off them.
        authorizer = authorizer or query_guard.Authorizer(query_guard.declared_names(statement))
        target.set_authorizer(authorizer)
        deadline = deadline or query_guard.Deadline(seconds)
        target.set_progress_handler(deadline, query_guard.PROGRESS_INSTRUCTIONS)
        try:
            cursor = target.execute(statement)
            values = cursor.fetchmany(limit + 1)
            return {"columns": [d[0] for d in cursor.description], "rows": values[:limit],
                    "truncated": len(values) > limit, "materialized_rows": counts}
        finally:
            target.set_authorizer(None)
            target.set_progress_handler(None, 0)
