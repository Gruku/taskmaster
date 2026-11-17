# User intent: record, per tool call, which stored entities an agent's answer read and who wrote their prose, so the
# server can mark teammates' text as untrusted data (team-server-027). Core module: no serve imports, no-ops in solo.
"""Provenance for framing (design D2): the per-call ledger, the SQLite read guard, and the authors query."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import sqlite3
import threading

from . import events, schema

UNATTRIBUTED = "~unattributed"
IMPORTED = "~imported"
REPOSITORY = "~repository"

# Fields the domain validates to a fixed vocabulary or whitespace-free pattern (design 3.3). Membership is
# proved by tests/test_provenance.py; a new field is prose until someone proves otherwise. Membership alone never
# exempts an event from authorship: the stored value must also have a structured shape (`_PROSE_EVENT`), because
# not every write path validates (batch lines, the viewer, the commands form; 027 fix round 1, C1).
STRUCTURED_FIELDS = frozenset(
    "id status priority archived deleted rev updated_seq epic phase order stage lane depends_on bundle area".split())
# Starting-list fields left out because the proof (tests/test_provenance_structured.py) found or could not show a
# refusal of free text: locked_by (task.update accepts any text), and kind, severity, date, start_date, target_date,
# task_ids, related_tasks, related_issues, pinned (unproven; they stay prose, which only over-frames).
# Columns a read may touch outside an accounted reader without making the call unaccounted (design D2.3).
STRUCTURED_COLUMNS = frozenset(
    "entity_key kind public_id revision last_seq archived deleted seq ordinal field shape target_kind target_id "
    "target_key path_key commit_key first_seq final_seq".split())
# Store identity and version facts (ids, versions, states): never stored prose.
STRUCTURED_TABLES = frozenset({"meta", "native_manifest"})
# (table, column) pairs that hold only a file path or other identifier.
STRUCTURED_TABLE_COLUMNS = frozenset({("projection_conflict", "file")})
# Events whose `tool` or `op` brought text in from somewhere else (design 3.2). The migration's own bootstrap
# import is pre-team history (D6), not an importer's text.
IMPORT_OPERATIONS = frozenset({"sync.apply", "document.import", "linear.settle"})


@dataclass(frozen=True)
class CopyRule:
    """An operation that copies another entity's text into the entity it writes (design 3.5)."""
    name: str
    kind: str                 # kind of the written entity
    trigger: str              # a key of the event's `after` that marks the operation
    fields: tuple
    sources: object           # after -> [(kind, id)]


COPY_RULES = (
    CopyRule("bug.promote", "issue", "promoted_from", ("components",),
             lambda after: [("bug", i) for i in (after.get("promoted_from") or []) if isinstance(i, str)]),
)


@dataclass
class Ledger:
    reader: str
    tool: str = ""
    opened: bool = False
    entities: set = field(default_factory=set)
    external: set = field(default_factory=set)
    unaccounted: list = field(default_factory=list)
    refused: bool = False


_LEDGER: ContextVar = ContextVar("taskmaster_provenance_ledger", default=None)
_local = threading.local()


def bind(ledger: Ledger):
    return _LEDGER.set(ledger)


def unbind(token) -> None:
    _LEDGER.reset(token)


def current():
    return _LEDGER.get()


def note_entities(cores) -> None:
    ledger = _LEDGER.get()
    if ledger is None:
        return
    for core in cores:
        try:
            ledger.entities.add((core["kind"], core["public_id"], core["entity_key"]))
        except (KeyError, TypeError, IndexError):
            ledger.unaccounted.append("noted entity without identity")


def note_entity(kind, ident, entity_key=None) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.entities.add((kind, ident, entity_key))


def note_receipt(receipt) -> None:
    ledger = _LEDGER.get()
    if ledger is None or not isinstance(receipt, dict):
        return
    for item in receipt.get("affected") or []:
        if isinstance(item, dict) and item.get("kind") and item.get("id"):
            ledger.entities.add((item["kind"], item["id"], None))


def note_opened() -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.opened = True


def note_external(token: str) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.external.add(token)


def note_unaccounted(why: str) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.unaccounted.append(why)


def note_refused() -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.refused = True


@contextmanager
def accounted():
    """Reads inside this block return text the ledger accounts for; the guard doesn't flag them."""
    if _LEDGER.get() is None:
        yield
        return
    _local.depth = getattr(_local, "depth", 0) + 1
    try:
        yield
    finally:
        _local.depth -= 1


def is_prose_column(table: str, column: str) -> bool:
    if (not column or column in STRUCTURED_COLUMNS or table in STRUCTURED_TABLES
            or (table, column) in STRUCTURED_TABLE_COLUMNS or table.startswith(("sqlite_", "pragma_"))):
        return False
    if column.endswith("_json") and column[:-5] in STRUCTURED_FIELDS and table != "entity_extensions":
        return False
    return True


def guard(action, arg1, arg2, dbname, source):
    """SQLite authorizer: never denies; records a prose column read outside an accounted reader."""
    if action == sqlite3.SQLITE_READ:
        ledger = _LEDGER.get()
        if ledger is not None and not getattr(_local, "depth", 0) and is_prose_column(arg1 or "", arg2 or ""):
            ledger.unaccounted.append(f"{arg1}.{arg2}")
    return sqlite3.SQLITE_OK


def install_guard(connection) -> None:
    connection.set_authorizer(guard)


# ── authors ────────────────────────────────────────────────────────────────

# A structured field's stored value counts as structured only when it is null, a boolean, a number, or text of at most
# 64 characters drawn from [A-Za-z0-9._/-] (an id, slug, enum or branch-like label), or a list of those. Anything
# else (spaces, quotes, tags, objects, nested lists, long text) is prose, whatever path wrote it.
# SQLite's length() and GLOB stop at an embedded NUL, so "b-1\x00<sentence>" would pass as a 3-char token; the byte
# length must equal the character length (ASCII only, no NUL) before the other two checks can be trusted.
_SHAPE_OK = ("(v.type IN ('null','true','false','integer','real') OR (v.type='text' "
             "AND length(CAST(v.value AS BLOB))=length(v.value) AND length(v.value)<=64 "
             "AND v.value NOT GLOB '*[^A-Za-z0-9._/-]*'))")


def _prose_event(placeholders: str) -> str:
    """SQL: the event `d` changed a prose field, or stored a non-structured value in a structured one."""
    path = """'$."' || f.value || '"'"""
    return (f"EXISTS (SELECT 1 FROM json_each(d.fields) f WHERE CASE WHEN f.value IN ({placeholders}) THEN "
            f"(json_type(d.after, {path})='object' OR EXISTS (SELECT 1 FROM json_each(d.after, {path}) v "
            f"WHERE NOT {_SHAPE_OK})) ELSE 1 END)")


def _prose_placeholders():
    fields = sorted(STRUCTURED_FIELDS)
    return ",".join("?" for _ in fields), fields


def _event_tokens(seq, handle, tool, op, pre_team):
    owner, cutoff = pre_team
    if tool in IMPORT_OPERATIONS or op in IMPORT_OPERATIONS:
        return IMPORTED
    if cutoff is not None and seq <= cutoff:
        return owner or UNATTRIBUTED
    return handle if handle else UNATTRIBUTED


def _scan_events(connection, entities, pre_team):
    """{(kind, id): ([tokens per prose event], [copy-rule hits])} for the given entities."""
    extended = events.columns(connection)
    placeholders, structured = _prose_placeholders()
    found: dict = {}
    # Singletons (backlog, project) are noted with their core key but their events keep entity_key NULL (M1).
    by_key = [e[2] is not None and extended and e[0] not in schema.SINGLETON_KINDS for e in entities]
    keyed = [e for e, k in zip(entities, by_key) if k]
    others = [e for e, k in zip(entities, by_key) if not k]
    base = ("SELECT DISTINCT d.kind, d.id, d.seq, " + ("json_extract(d.actor,'$.handle')" if extended else "NULL")
            + ", d.tool, d.op, d.fields, CASE WHEN d.kind='issue' THEN d.after END FROM domain_events d WHERE ")
    prose = " AND " + _prose_event(placeholders)
    rows = []
    for start in range(0, len(keyed), 500):
        chunk = keyed[start:start + 500]
        rows += connection.execute(base + f"d.entity_key IN ({','.join('?' for _ in chunk)})" + prose,
                                   [e[2] for e in chunk] + structured).fetchall()
    for kind, ident, _key in others:
        rows += connection.execute(base + "d.kind=? AND d.id=?" + prose, [kind, ident] + structured).fetchall()
    for kind, ident, seq, handle, tool, op, fields, after in rows:
        entry = found.setdefault((kind, ident), ([], []))
        token = _event_tokens(seq, handle if extended else None, tool, op, pre_team) if extended else UNATTRIBUTED
        entry[0].append(token)
        if after:
            try:
                after_doc = json.loads(after)
                touched = set(json.loads(fields))
            except (ValueError, TypeError):
                continue
            for rule in COPY_RULES:
                if (kind == rule.kind and isinstance(after_doc, dict) and rule.trigger in after_doc
                        and touched & set(rule.fields)):
                    entry[1].extend(rule.sources(after_doc))
    return found


def authors_of(connection, entities, *, reader: str, pre_team=(None, None)) -> set:
    """The foreign-author tokens of the prose of `entities` ((kind, id, entity_key|None) tuples)."""
    entities = sorted(set(entities), key=lambda e: (e[0], e[1]))
    found = _scan_events(connection, entities, pre_team)
    tokens: set = set()
    sources: set = set()
    for kind, ident, _key in entities:
        events_for, copies = found.get((kind, ident), ([], []))
        if not events_for:
            tokens.add(UNATTRIBUTED)
        tokens.update(events_for)
        sources.update(copies)
    if sources:
        known = {(k, i) for k, i, _ in entities}
        extra = [(k, i, None) for k, i in sources if (k, i) not in known]
        resolved = connection.execute  # resolve keys for the copy sources
        withkeys = []
        for k, i, _ in extra:
            row = resolved("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (k, i)).fetchone()
            withkeys.append((k, i, row[0] if row else None))
        for kind, ident, _key in withkeys:
            events_for, _copies = _scan_events(connection, [(kind, ident, _key)], pre_team).get((kind, ident), ([], []))
            if not events_for:
                tokens.add(UNATTRIBUTED)
            tokens.update(events_for)
    tokens.discard(reader)
    return tokens


class StoreAuthors:
    """Store-wide prose authors, an incremental cache keyed by (store_id, owner, cutoff) and advanced by max(seq)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key = None
        self._seq = 0
        self._tokens: set = set()

    def authors(self, connection, *, store_id, reader, pre_team=(None, None)) -> set:
        extended = events.columns(connection)
        placeholders, structured = _prose_placeholders()
        key = (store_id, pre_team[0], pre_team[1])
        with self._lock:
            high = connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]
            if key != self._key or high < self._seq:
                self._key, self._seq, self._tokens = key, 0, set()
            if high > self._seq:
                query = ("SELECT DISTINCT d.seq, " + ("json_extract(d.actor,'$.handle')" if extended else "NULL")
                         + ", d.tool, d.op FROM domain_events d WHERE d.seq>? AND d.seq<=? AND "
                         + _prose_event(placeholders))
                for seq, handle, tool, op in connection.execute(query, [self._seq, high] + structured):
                    self._tokens.add(_event_tokens(seq, handle, tool, op, pre_team) if extended else UNATTRIBUTED)
                self._seq = high
            tokens = set(self._tokens)
        tokens.discard(reader)
        return tokens
