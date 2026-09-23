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


def _readable(key: str) -> str:
    return f"NOT EXISTS (SELECT 1 FROM dependencies x {_SHAPE} WHERE x.entity_key={key} AND {_UNREADABLE_ROW})"


def _in_tree(alias: str) -> str:
    return (f"EXISTS (SELECT 1 FROM task_operational o JOIN entity_core e ON e.kind='epic' AND e.deleted=0 "
            f"AND e.public_id=json_extract(o.epic_json,'$') WHERE o.entity_key={alias}.entity_key "
            "AND json_type(o.epic_json)='text')")


def _tree_key(ident: str) -> str:
    return (f"(SELECT t.entity_key FROM entity_core t WHERE t.kind='task' AND t.public_id={ident} "
            f"AND t.deleted=0 AND {_in_tree('t')})")


# Tree tasks whose readable `depends_on` names the id: the reverse index, not a scan.
_DEPENDENTS = ("SELECT DISTINCT c.public_id FROM dependencies x JOIN entity_core c ON c.entity_key=x.entity_key "
               f"WHERE x.target_kind='task' AND x.target_id=? AND {_ROW} "
               f"AND c.kind='task' AND c.deleted=0 AND {_in_tree('c')} AND {_readable('c.entity_key')}")

# Rows (id, entity_key or NULL when missing, hops, reached-from). UNION dedups
# whole rows, so a cycle stops at the depth cap and the row count is bounded by
# hops x depth.
_UPSTREAM = ("WITH RECURSIVE walk(id,key,depth,parent) AS (SELECT ?,?,0,NULL UNION "
             f"SELECT json_extract(x.value_json,'$'),{_tree_key(_TARGET)},w.depth+1,w.id "
             f"FROM walk w JOIN dependencies x ON x.entity_key=w.key {_SHAPE} "
             f"WHERE w.key IS NOT NULL AND w.depth<? AND {_ID_ROW} AND {_readable('w.key')}) "
             "SELECT id,key,depth,parent FROM walk")
_DOWNSTREAM = ("WITH RECURSIVE walk(id,key,depth,parent) AS (SELECT ?,?,0,NULL UNION "
               "SELECT c.public_id,c.entity_key,w.depth+1,w.id FROM walk w "
               f"JOIN dependencies x ON x.target_kind='task' AND x.target_id=w.id AND {_ROW} "
               "JOIN entity_core c ON c.entity_key=x.entity_key "
               f"WHERE w.depth<? AND c.kind='task' AND c.deleted=0 AND {_in_tree('c')} "
               f"AND {_readable('c.entity_key')}) "
               "SELECT id,key,depth,parent FROM walk")
_UNREADABLE = (f"SELECT DISTINCT x.entity_key FROM dependencies x {_SHAPE} "
               f"WHERE x.entity_key IN (SELECT value FROM json_each(?)) AND {_UNREADABLE_ROW}")


def dependents(connection, ident: str) -> list[str]:
    """Ids of the tree tasks that declare `ident` as a dependency, in no order."""
    return [row[0] for row in connection.execute(_DEPENDENTS, (ident,))]


def tree_key(connection, ident: str):
    return connection.execute("SELECT " + _tree_key("?"), (ident,)).fetchone()[0]


def traverse(connection, root: str, depth: int, direction: str, deadline) -> dependency_chain.Walk:
    """One direction of `backlog_dependencies(depth=N)` as a recursive CTE, under
    the traversal deadline as the connection's progress handler."""
    result = dependency_chain.Walk(root, {root: 0})
    sql = _UPSTREAM if direction == "upstream" else _DOWNSTREAM
    connection.set_progress_handler(deadline, 10_000)
    try:
        values = connection.execute(sql, (root, tree_key(connection, root), depth)).fetchall()
    except sqlite3.OperationalError:
        if not deadline.expired:
            raise
        values = []
    finally:
        connection.set_progress_handler(None, 0)
    if deadline():
        result.timed_out = True
        return result
    keys = {}
    for ident, key, hops, parent in values:
        if hops < result.distance.get(ident, hops + 1):
            result.distance[ident] = hops
        if parent is not None:
            result.edges.add((parent, ident))
        if key is None:
            result.missing.add(ident)
        else:
            keys[ident] = key
    if direction == "upstream":
        followed = {keys[n]: n for n, hops in result.distance.items() if hops < depth and n in keys}
        for (key,) in connection.execute(_UNREADABLE, (json.dumps(sorted(followed)),)):
            result.unreadable.add(followed[key])
    return result


def explain_statements():
    """The reverse lookups, for a test to hold to the target index."""
    return [(_DEPENDENTS, ("x",)), (_DOWNSTREAM, ("x", 1, 2))]
