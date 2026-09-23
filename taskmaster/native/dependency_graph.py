# User intent: answer "what does this task unblock, and how far does the chain go"
# on a native store from the canonical indexed `dependencies` table, never by
# scanning every task, with exactly the answer the legacy tree walk gives.
"""Indexed dependency-graph reads over one native snapshot.

Every predicate here is the SQL form of `taskmaster_v3.dependency_ids` over the
canonical rows `_put_relations` writes for `depends_on`, so an id counts as a
dependency exactly when the one normaliser reads it as one:
- a `depends_on` is unreadable when a row is not a string, unless the field is a
  scalar that is falsy (null, false, 0, {}) — which reads as no dependencies;
- a scalar empty string reads as no dependencies, a listed one as the id "".
A task is a *tree task* (what `find_task` finds) when it is not deleted and its
epic exists; archived tasks are tree tasks.
"""
from __future__ import annotations

import json
import sqlite3

from taskmaster import dependency_chain

_ROW = "x.field='depends_on'"
_TARGET = "json_extract(x.value_json,'$')"
_SHAPE = "JOIN field_shapes s ON s.entity_key=x.entity_key AND s.field=x.field"
_ID_ROW = (f"{_ROW} AND json_type(x.value_json)='text' "
           "AND (s.shape='list' OR json_extract(x.value_json,'$')<>'')")
_UNREADABLE_ROW = (f"{_ROW} AND json_type(x.value_json)<>'text' AND NOT (s.shape='scalar' AND ("
                   "json_type(x.value_json) IN ('null','false') "
                   "OR (json_type(x.value_json) IN ('integer','real') AND json_extract(x.value_json,'$')=0) "
                   "OR json(x.value_json)='{}'))")


def _in_tree(alias: str) -> str:
    return (f"EXISTS (SELECT 1 FROM task_operational o JOIN entity_core e ON e.kind='epic' AND e.deleted=0 "
            f"AND e.public_id=json_extract(o.epic_json,'$') WHERE o.entity_key={alias}.entity_key "
            "AND json_type(o.epic_json)='text')")


# Live tasks whose `depends_on` names one of the ids: the reverse index
# `ix_dependencies_target` on (target_kind, target_id), probed once per id. Tree
# membership and readability are per source task, not per row, so they are
# checked once per distinct source (`_admissible`), never correlated per edge.
_CANDIDATES = ("SELECT x.target_id,c.public_id,c.entity_key FROM dependencies x "
               "JOIN entity_core c ON c.entity_key=x.entity_key "
               f"WHERE x.target_kind='task' AND x.target_id IN (SELECT value FROM json_each(?)) AND {_ROW} "
               "AND c.kind='task' AND c.deleted=0")
# Which of the task keys are in the tree (their epic exists).
_IN_TREE = ("SELECT c.entity_key FROM json_each(?) j CROSS JOIN entity_core c ON c.entity_key=j.value "
            f"WHERE {_in_tree('c')}")
# The declared ids of each task, by primary key, in declared order.
_DECLARED = (f"SELECT x.entity_key,{_TARGET} FROM dependencies x {_SHAPE} "
             f"WHERE x.entity_key IN (SELECT value FROM json_each(?)) AND {_ID_ROW} ORDER BY x.entity_key,x.ordinal")
# The tasks among `ids` whose `depends_on` cannot be read.
_UNREADABLE = (f"SELECT DISTINCT x.entity_key FROM dependencies x {_SHAPE} "
               f"WHERE x.entity_key IN (SELECT value FROM json_each(?)) AND {_UNREADABLE_ROW}")
# Which of the ids are tree tasks, by the (kind, public_id) unique index.
# CROSS JOIN pins the ids as the outer loop: the planner otherwise may prefer
# the (kind, status) index and walk every task.
_RESOLVE = ("SELECT t.public_id,t.entity_key FROM json_each(?) j CROSS JOIN entity_core t "
            f"ON t.kind='task' AND t.public_id=j.value WHERE t.deleted=0 AND {_in_tree('t')}")
PROGRESS_INSTRUCTIONS = 10_000


def _ids(values) -> str:
    return json.dumps(list(values))


def _dependent_pairs(connection, ids) -> list:
    """`(target, source)` for every tree task whose readable `depends_on` names a target."""
    candidates = connection.execute(_CANDIDATES, (_ids(ids),)).fetchall()
    keys = _ids(sorted({key for _target, _source, key in candidates}))
    admissible = {key for (key,) in connection.execute(_IN_TREE, (keys,))}
    admissible -= {key for (key,) in connection.execute(_UNREADABLE, (keys,))}
    return [(target, source) for target, source, key in candidates if key in admissible]


def dependents(connection, ident: str) -> list[str]:
    """Ids of the tree tasks that declare `ident` as a dependency, in no order."""
    return list(dict.fromkeys(source for _target, source in _dependent_pairs(connection, [ident])))


def _resolve(connection, ids) -> dict:
    return dict(connection.execute(_RESOLVE, (_ids(ids),)))


def _upstream_level(connection, frontier: dict, level: int, result) -> dict:
    """Follow every frontier task's declared ids at once; answers the next frontier."""
    by_key = {key: ident for ident, key in frontier.items()}
    unreadable = {key for (key,) in connection.execute(_UNREADABLE, (_ids(by_key),))}
    result.unreadable.update(by_key[key] for key in unreadable)
    reached = []
    for key, target in connection.execute(_DECLARED, (_ids(by_key),)):
        if key in unreadable:
            continue
        if result.hop(by_key[key], target, level):
            reached.append(target)
    keys = _resolve(connection, reached) if reached else {}
    result.missing.update(ident for ident in reached if ident not in keys)
    return {ident: keys[ident] for ident in reached if ident in keys}


def _downstream_level(connection, frontier: dict, level: int, result) -> dict:
    reached = {}
    for target, source in _dependent_pairs(connection, frontier):
        if result.hop(target, source, level):
            reached[source] = None
    return reached


def traverse(connection, root: str, depth: int, direction: str, deadline) -> dependency_chain.Walk:
    """One direction of `backlog_dependencies(depth=N)`: a breadth-first walk with
    one indexed query per frontier, so every task is expanded once however many
    paths reach it. The traversal deadline is the connection's progress handler,
    so it interrupts a running statement, and is checked between levels while
    work remains."""
    result = dependency_chain.Walk(root, {root: 0})
    step = _upstream_level if direction == "upstream" else _downstream_level
    connection.set_progress_handler(deadline, PROGRESS_INSTRUCTIONS)
    try:
        frontier = {root: _resolve(connection, [root]).get(root)}
        for level in range(1, depth + 1):
            if not frontier:
                break
            frontier = step(connection, frontier, level, result)
            # Only while work remains: a finished walk is never reported late.
            if frontier and level < depth and deadline():
                break
    except sqlite3.OperationalError:
        if not deadline.expired:
            raise
    finally:
        connection.set_progress_handler(None, 0)
    if deadline.expired:
        return dependency_chain.Walk(root, {root: 0}, timed_out=True)
    return result


def explain_statements():
    """Every traversal statement, with the index constraint its plan must show."""
    ids = _ids(["x", "y"])
    return [(_CANDIDATES, (ids,), "target_id=?"), (_IN_TREE, (_ids([1, 2]),), "rowid=?"), (_DECLARED, (_ids([1, 2]),), "entity_key=?"),
            (_UNREADABLE, (_ids([1, 2]),), "entity_key=?"), (_RESOLVE, (ids,), "public_id=?")]
