# User intent: let the agent run arbitrary SELECTs over the backlog store without ever
# being able to write to it. Two independent gates — a textual guard on the statement and an
# sqlite3 authorizer on the connection — so a bypass of one is still stopped by the other.
from __future__ import annotations

import re
import sqlite3
import time

# `LIMIT` caps rows, not work: a cross join or a runaway recursive CTE can burn minutes
# inside one MCP call. The progress handler aborts the statement past this deadline.
QUERY_TIMEOUT_S = 5.0
# VM instructions between progress-handler calls. Small enough to notice the deadline
# promptly, large enough that the callback is not the bottleneck.
PROGRESS_INSTRUCTIONS = 10_000

# The tables `backlog_query` may read, named here rather than imported from
# `store` so the guard has no dependency on the module it guards. `meta` (store
# bookkeeping) and `projection_base` (whole file bodies as blobs) are absent on
# purpose: neither answers a backlog question and the second dumps a document per
# row. `tests/test_backlog_query.py` pins this list against the live schema.
TABLES = (
    "entities", "changes", "projection", "sessions", "linear_queue",
    "entity_paths", "links", "related", "handover_tasks", "entity_fts",
)

# The schema summary appended to every error, so a failed query self-corrects.
SCHEMA_SUMMARY = (
    "entities(kind,id,epic,status,archived,deleted,doc,body,rev,updated_seq)"
    "  -- doc is JSON: json_extract(doc,'$.title')\n"
    "entity_paths(kind,id,path,match_kind,source)"
    " links(src_kind,src_id,type,dst_kind,dst_id,derived)\n"
    "related(a_kind,a_id,b_kind,b_id,via,weight) handover_tasks(handover_id,task_id)"
    " entity_fts(kind,id,title,body)\n"
    "changes(seq,ts,session,tool,kind,id,op,fields,before,after)"
    " sessions(session,pid,host,started,last_seen,cwd,current_tool)\n"
    "projection(file,kind,id,content_hash,mtime,size,dirty,quarantined,exported_seq)\n"
    "linear_queue(seq,op,target_id,tracker_id,payload,state,attempts,last_error,"
    "claimed_by,claimed_at)"
)

# FTS5 keeps its own shadow tables; a MATCH query reads them and sqlite_master directly.
_FTS_SHADOW = tuple(f"entity_fts_{suffix}" for suffix in
                    ("data", "idx", "docsize", "config", "content"))
READABLE_TABLES = frozenset(TABLES) | frozenset(_FTS_SHADOW) | {"sqlite_master"}

FORBIDDEN = re.compile(
    r"\b(ATTACH|DETACH|PRAGMA|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


def _mask(sql: str) -> str:
    """Blank out comments and single-quoted string contents, preserving length.

    Everything that survives is code, so the keyword and semicolon rules below
    cannot be dodged by hiding a statement in a comment or a literal.
    """
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            out[i] = " "
            i += 1
            while i < n:
                if sql[i] == "'":
                    # A doubled quote is an escaped quote: stay inside the literal.
                    if i + 1 < n and sql[i + 1] == "'":
                        out[i] = out[i + 1] = " "
                        i += 2
                        continue
                    out[i] = " "
                    i += 1
                    break
                out[i] = " "
                i += 1
        elif ch == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                out[i] = " "
                i += 1
        elif ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            stop = n if end == -1 else end + 2
            for j in range(i, stop):
                out[j] = " "
            i = stop
        else:
            i += 1
    return "".join(out)


# A CTE or window name in `<name> [(cols)] AS (`. SQLite reports a read of a CTE as
# SQLITE_READ on the CTE's own name, indistinguishable in shape from a read of a
# table-valued function, so the allowlist has to learn the names the query declares.
_DECLARED = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^()]*\))?\s+AS\s*\(", re.IGNORECASE)


def declared_names(sql: str) -> frozenset[str]:
    """Names the query itself introduces via `WITH ... AS (` or `WINDOW ... AS (`.

    A declared name is honoured only for a read SQLite reports against no schema
    (see `Authorizer._SCHEMAS`). Treating it as readable everywhere was the hole:
    a CTE name shadows a schema object for unqualified references, but
    `main.<name>` still reaches the real table, so declaring `projection_base`
    and then selecting `main.projection_base` read a blocked table.
    """
    return frozenset(m.group(1) for m in _DECLARED.finditer(_mask(sql)))


def validate(sql: str) -> str:
    """Return `sql` normalized for execution, or raise ValueError naming the broken rule."""
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    if not text:
        raise ValueError("empty query: pass one SELECT or WITH ... SELECT statement")

    masked = _mask(text)
    first = re.match(r"\s*([A-Za-z_]+)", masked)
    keyword = (first.group(1) if first else "").upper()
    if keyword not in ("SELECT", "WITH"):
        raise ValueError(
            f"only SELECT (or WITH ... SELECT) is allowed; this query starts with {keyword or '?'}"
        )
    if ";" in masked:
        raise ValueError("only one statement is allowed; found ';' outside a string literal")
    hit = FORBIDDEN.search(masked)
    if hit:
        raise ValueError(f"forbidden keyword {hit.group(1).upper()}: this tool is read-only")
    return text


# sqlite3 reuses low integers for both authorizer actions and return codes
# (SQLITE_COPY is 0, same as SQLITE_OK), so the reverse map is built from the
# authorizer action names only — never from a blanket scan of the module.
_ACTION_NAMES = {
    getattr(sqlite3, name): name[len("SQLITE_"):]
    for name in (
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_DELETE", "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW", "SQLITE_INSERT", "SQLITE_PRAGMA", "SQLITE_READ", "SQLITE_SELECT",
        "SQLITE_TRANSACTION", "SQLITE_UPDATE", "SQLITE_ATTACH", "SQLITE_DETACH",
        "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE", "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_VTABLE", "SQLITE_FUNCTION", "SQLITE_SAVEPOINT", "SQLITE_COPY",
        "SQLITE_RECURSIVE",
    )
    if hasattr(sqlite3, name)
}


class Authorizer:
    """Callable sqlite3 authorizer: read the store tables, nothing else.

    Instantiate one per query, passing that query's `declared_names`. It remembers
    the first thing it denied so the tool can say *what* was refused — SQLite's own
    "not authorized" carries no object.
    """

    # A read SQLite attributes to one of these is a read of a real schema table,
    # never of a name the query introduced: a CTE or window name is reported with
    # no schema at all. The fixed allowlist is the only rule inside them.
    _SCHEMAS = frozenset({"main", "temp"})

    def __init__(self, declared: frozenset[str] = frozenset()) -> None:
        self.readable = READABLE_TABLES
        self.declared = frozenset(declared)
        self.denial: str | None = None

    def __call__(self, action: int, arg1, arg2, dbname, source) -> int:  # noqa: ARG002
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            # SQLITE_RECURSIVE is the step of a `WITH RECURSIVE` CTE, not a write.
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if arg1 in self.readable:
                return sqlite3.SQLITE_OK
            # `WITH projection_base AS (...) SELECT * FROM main.projection_base`
            # read the blocked table: the declared name went into the allowlist
            # and the schema was ignored, so the explicit `main.` qualifier
            # named the real table while the allowance meant for the CTE let it
            # through. Declared names authorize nothing inside a real schema.
            if dbname not in self._SCHEMAS and arg1 in self.declared:
                return sqlite3.SQLITE_OK
        # FTS5 issues `PRAGMA data_version` internally on every MATCH; it only reads a
        # counter. `validate` already rejects a user-written PRAGMA, so this can only
        # come from inside the virtual table.
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and not arg2:
            return sqlite3.SQLITE_OK
        if self.denial is None:
            name = _ACTION_NAMES.get(action, str(action))
            self.denial = f"{name} {arg1}".strip() if arg1 else name
        return sqlite3.SQLITE_DENY


class Deadline:
    """Callable sqlite3 progress handler that aborts the statement once time is up."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.expires_at = time.monotonic() + seconds
        self.expired = False

    def __call__(self) -> int:
        if time.monotonic() >= self.expires_at:
            self.expired = True
            return 1  # non-zero aborts the running statement
        return 0

    @property
    def message(self) -> str:
        return f"query exceeded {self.seconds:g} s"
