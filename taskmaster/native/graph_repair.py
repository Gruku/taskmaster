# User intent: an operator can check a native store's graph SQL tables against the exact
# full oracle and repair any drift through one explicit maintenance operation that reports
# its cost and never runs on a normal command or read (N14 step 6, F1 = A).
"""Native graph verify/repair: `entity_paths`, `links`, `handover_tasks`, `related`.

Commands keep these tables current at commit through `relations.maintain`. This
module is their oracle: it recomputes every row from the canonical documents with
the legacy full-rebuild rules (`relations._paths`/`_links`, the declared-link
mirrors of `Store._close_reverse_links`, one `handover_tasks` row per listed task,
`relations.grouped_weights` for path pairs, one `related` row per handover
co-membership) and compares them as multisets. `verify` reads a snapshot and
writes nothing; `graph.repair` is a command that deletes the spurious rows and
inserts the missing ones inside the writer's single transaction. Derived rows
are not authored state, so a repair appends no domain event.
"""
from collections import Counter
from datetime import datetime, timezone
import time

from taskmaster.paths import as_list
from taskmaster.taskmaster_v3 import REVERSE_TYPE
from . import relations
from .migrate import rows

OPERATIONS = {"graph.repair"}
TABLES = ("entity_paths", "links", "handover_tasks", "related")
COLUMNS = {
    "entity_paths": ("kind", "id", "path", "match_kind", "source"),
    "links": ("src_kind", "src_id", "type", "dst_kind", "dst_id", "derived"),
    "handover_tasks": ("handover_id", "task_id"),
    "related": ("a_kind", "a_id", "b_kind", "b_id", "via", "weight"),
}
EXAMPLES = 10
REPAIRED_AT = "graph_repaired_at"
# When the last repair ran, changed rows or not: display only, never a revision input.
CHECKED_AT = "graph_checked_at"
# How many repairs have changed rows; the hooks' dedupe revision adds it to the
# event high water, since a repair appends no event (see `hook_reads.revision`).
REPAIRS = "graph_repairs"
_CHUNK = 500


def validate(operation, arguments):
    if arguments:
        raise ValueError("graph.repair takes no arguments")


def _documents(snapshot):
    """Every live entity's fields and body, archived ones included, in key order."""
    from .queries import _core_columns
    cores = rows(snapshot.connection, "SELECT " + _core_columns(None) +
                 " FROM entity_core WHERE deleted=0 ORDER BY entity_key")
    for start in range(0, len(cores), _CHUNK):
        yield from snapshot._assemble(cores[start:start + _CHUNK], None, include_body=True)


def expected(snapshot):
    """`{table: Counter(row)}`: the rows a full rebuild derives from the documents."""
    return _derive(snapshot)[0]


def _derive(snapshot):
    connection = snapshot.connection
    paths, handovers, declared, kinds = Counter(), Counter(), set(), {}
    entities = 0
    for entity in _documents(snapshot):
        entities += 1
        kind, ident, doc = entity["kind"], entity["id"], entity["fields"]
        paths.update((kind, ident, *value) for value in relations._paths(doc, entity["body"]))
        for link_type, target in relations._links(kind, doc):
            if target not in kinds:
                kinds[target] = relations._kind_for_id(connection, target)
            declared.add((kind, ident, link_type, kinds[target], target))
        if kind == "handover":
            handovers.update((ident, str(task)) for task in as_list(doc.get("task_ids")))
    links = Counter((*edge, 0) for edge in declared)
    for source_kind, source_id, link_type, target_kind, target_id in declared:
        reverse = REVERSE_TYPE.get(link_type)
        if reverse is not None and (target_kind, target_id, reverse, source_kind, source_id) not in declared:
            links[target_kind, target_id, reverse, source_kind, source_id, 1] = 1
    claims = [(kind, ident, path, match) for (kind, ident, path, match, source), count in paths.items()
              if source in ("anchors", "location") for _ in range(count)]
    related = Counter({(*left, *right, "path", weight): 1
                       for (left, right), weight in relations.grouped_weights(claims).items()})
    members = {}
    for (handover, task), count in sorted(handovers.items()):
        members.setdefault(handover, []).extend([task] * count)
    for tasks in members.values():
        for index, left in enumerate(tasks):
            for right in tasks[index + 1:]:
                related["task", left, "task", right, "handover", 1] += 1
    return {"entity_paths": paths, "links": links, "handover_tasks": handovers, "related": related}, entities


def _actual(connection, table):
    return Counter(tuple(row) for row in connection.execute(f"SELECT {','.join(COLUMNS[table])} FROM {table}"))


def _compare(snapshot):
    started = time.perf_counter()
    oracle, entities = _derive(snapshot)
    tables, differences, compared = {}, {}, 0
    for table in TABLES:
        actual = _actual(snapshot.connection, table)
        missing, spurious = oracle[table] - actual, actual - oracle[table]
        compared += sum(actual.values()) + sum(oracle[table].values())
        differences[table] = (missing, spurious)
        tables[table] = {"rows": sum(actual.values()), "expected": sum(oracle[table].values()),
                         "missing": sum(missing.values()), "spurious": sum(spurious.values()),
                         "examples": {"missing": [list(r) for r in sorted(missing, key=repr)[:EXAMPLES]],
                                      "spurious": [list(r) for r in sorted(spurious, key=repr)[:EXAMPLES]]}}
    report = {"clean": not any(t["missing"] or t["spurious"] for t in tables.values()), "repaired": False,
              "tables": tables, "entities": entities, "rows_compared": compared}
    return report, differences, started


def verify(snapshot):
    """Compare the graph tables with the oracle; reads only."""
    report, _, started = _compare(snapshot)
    report["seconds"] = round(time.perf_counter() - started, 6)
    return report


def _delete(connection, table, row, count):
    where = " AND ".join(f'"{column}" IS ?' for column in COLUMNS[table])
    connection.execute(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT ?)",
                       (*row, count))


def _insert(connection, table, row, count):
    placeholders = ",".join("?" for _ in row)
    connection.executemany(f"INSERT INTO {table}({','.join(COLUMNS[table])}) VALUES({placeholders})",
                           [row] * count)


def repair(snapshot):
    """Replace exactly the differing rows; the caller owns the one writer transaction."""
    report, differences, started = _compare(snapshot)
    connection = snapshot.connection
    for table in TABLES:
        missing, spurious = differences[table]
        for row, count in sorted(spurious.items(), key=repr):
            _delete(connection, table, row, count)
        for row, count in sorted(missing.items(), key=repr):
            _insert(connection, table, row, count)
    report["repaired"] = not report["clean"]
    stamp = datetime.now(timezone.utc).isoformat()
    connection.execute("INSERT INTO native_manifest VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (CHECKED_AT, stamp))
    report["checked_at"] = stamp
    if report["repaired"]:
        # Only a repair that changed rows moves the revision input: a clean one changed nothing.
        repairs = connection.execute("SELECT value FROM native_manifest WHERE key=?", (REPAIRS,)).fetchone()
        connection.executemany("INSERT INTO native_manifest VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               [(REPAIRED_AT, stamp), (REPAIRS, str(int(repairs[0]) + 1 if repairs else 1))])
        report["repaired_at"] = stamp
    report["seconds"] = round(time.perf_counter() - started, 6)
    return report


def apply(transaction, operation, arguments):
    transaction.result = repair(transaction.snapshot)
