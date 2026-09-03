# User intent: let the agent run arbitrary SELECTs over the derived backlog index without ever
# being able to write to it. Two independent gates — a textual guard on the statement and an
# sqlite3 authorizer on the connection — so a bypass of one is still stopped by the other.
from __future__ import annotations

import re
import sqlite3

from taskmaster.index import TABLES

# The schema summary appended to every error, so a failed query self-corrects.
SCHEMA_SUMMARY = (
    "entities(id,kind,status,title,epic,phase,lane,repo,priority,created,updated,archived,file)\n"
    "entity_paths(entity_id,path,match_kind,source) links(src,type,dst,derived)\n"
    "handovers(id,thread,tldr,next_action,session_kind,branch,tip_commit,supersedes)\n"
    "handover_tasks(handover_id,task_id) related(a,b,via,weight) entity_fts(id,kind,title,body)"
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


def authorizer(action: int, arg1, arg2, dbname, source) -> int:  # noqa: ARG001
    """sqlite3 authorizer: allow reads of the index tables and functions, deny the rest."""
    if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in READABLE_TABLES else sqlite3.SQLITE_DENY
    # FTS5 issues `PRAGMA data_version` internally on every MATCH; it only reads a
    # counter. `validate` already rejects a user-written PRAGMA, so this can only
    # come from inside the virtual table.
    if action == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and not arg2:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
