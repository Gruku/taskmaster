"""Selective derived relations with the exact grouped/prefix repair oracle.

Public legacy graph tables remain the single current derived representation.
Metadata edits whose graph inputs do not change issue no graph SQL at all.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
import fnmatch
from itertools import combinations
import re

from taskmaster.paths import as_list, extract_prose_paths, normalize_location, normalize_task_anchor
from taskmaster.taskmaster_v3 import REVERSE_TYPE, legacy_links_to_typed


def grouped_weights(values):
    """Exact full path-graph oracle, ported from the measured N00 harness."""
    groups = defaultdict(Counter)
    for kind, ident, path, match in values:
        groups[path, match][kind, ident] += 1
    keys = sorted(groups)
    by_path = defaultdict(list)
    for i, (path, _) in enumerate(keys):
        by_path[path].append(i)
    matches = set()
    for indexes in by_path.values():
        matches.update(combinations(indexes, 2))
    ordered_paths = sorted(by_path)
    for i, (pattern, match) in enumerate(keys):
        if match != "glob":
            continue
        literal = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
        start = bisect_left(ordered_paths, literal)
        for position in range(start, len(ordered_paths)):
            path = ordered_paths[position]
            if not path.startswith(literal):
                break
            if fnmatch.fnmatchcase(path, pattern):
                for j in by_path[path]:
                    if i != j:
                        matches.add(tuple(sorted((i, j))))
    weights = Counter()
    for counts in groups.values():
        for left, right in combinations(sorted(counts), 2):
            weights[left, right] += counts[left] * counts[right]
    for i, j in matches:
        for left, left_count in groups[keys[i]].items():
            for right, right_count in groups[keys[j]].items():
                if left != right:
                    weights[tuple(sorted((left, right)))] += left_count * right_count
    return weights


def _paths(doc, body):
    if doc is None:
        return []
    values = [(path, match, "anchors") for path, match in
              (normalize_task_anchor(str(a), doc.get("sub_repo")) for a in as_list(doc.get("anchors")))]
    located = set()
    for location in as_list(doc.get("location")):
        value = normalize_location(str(location))
        located.add(value)
        values.append((value, "exact", "location"))
    values.extend((value, "exact", "prose") for value in extract_prose_paths(str(body or "")) if value not in located)
    return values


def _links(kind, doc):
    if doc is None:
        return set()
    return {(str(link.get("type") or "relates_to"), str(link["target"])) for link in legacy_links_to_typed(doc, kind)
            if isinstance(link, dict) and link.get("target")}


def _kind_for_id(connection, ident):
    row = connection.execute("SELECT kind FROM entity_core WHERE public_id=? AND deleted=0 "
                             "ORDER BY CASE kind WHEN 'task' THEN 0 WHEN 'issue' THEN 1 ELSE 2 END,kind LIMIT 1", (ident,)).fetchone()
    return row[0] if row else "task"


def _path_neighborhood(connection, kind, ident, new_paths):
    connection.execute("DELETE FROM related WHERE via='path' AND ((a_kind=? AND a_id=?) OR (b_kind=? AND b_id=?))", (kind, ident, kind, ident))
    own = [(path, match) for path, match, source in new_paths if source in ("anchors", "location")]
    if not own:
        return 0
    peers = connection.execute("SELECT kind,id,path,match_kind FROM entity_paths WHERE source IN ('anchors','location') AND NOT(kind=? AND id=?)", (kind, ident)).fetchall()
    weights = Counter()
    for path, match in own:
        for rk, ri, rp, rm in peers:
            if path == rp or (match == "glob" and fnmatch.fnmatchcase(rp, path)) or (rm == "glob" and fnmatch.fnmatchcase(path, rp)):
                weights[tuple(sorted(((kind, ident), (rk, ri))))] += 1
    connection.executemany("INSERT INTO related VALUES(?,?,?,?,?,?)",
                           [(a[0], a[1], b[0], b[1], "path", weight) for (a, b), weight in weights.items()])
    return len(own) * len(peers)


def _declared_links(connection, kind, ident, new_links):
    old = connection.execute("SELECT dst_kind,dst_id FROM links WHERE src_kind=? AND src_id=? AND derived=0", (kind, ident)).fetchall()
    pairs = {tuple(sorted(((kind, ident), (k, i)))) for k, i in old}
    connection.execute("DELETE FROM links WHERE src_kind=? AND src_id=? AND derived=0", (kind, ident))
    for link_type, target_id in sorted(new_links):
        target_kind = _kind_for_id(connection, target_id)
        connection.execute("INSERT OR IGNORE INTO links VALUES(?,?,?,?,?,0)", (kind, ident, link_type, target_kind, target_id))
        pairs.add(tuple(sorted(((kind, ident), (target_kind, target_id)))))
    for left, right in sorted(pairs):
        where = "((src_kind=? AND src_id=? AND dst_kind=? AND dst_id=?) OR (src_kind=? AND src_id=? AND dst_kind=? AND dst_id=?))"
        args = (*left, *right, *right, *left)
        connection.execute(f"DELETE FROM links WHERE derived=1 AND {where}", args)
        declared = connection.execute(f"SELECT src_kind,src_id,type,dst_kind,dst_id FROM links WHERE derived=0 AND {where}", args).fetchall()
        for sk, si, link_type, dk, di in declared:
            reverse = REVERSE_TYPE.get(link_type)
            if reverse is not None and not connection.execute("SELECT 1 FROM links WHERE src_kind=? AND src_id=? AND type=? AND dst_kind=? AND dst_id=?", (dk, di, reverse, sk, si)).fetchone():
                connection.execute("INSERT INTO links VALUES(?,?,?,?,?,1)", (dk, di, reverse, sk, si))
    return len(pairs)


def _handover_memberships(connection, ident, before, after):
    affected = {tuple(sorted(pair)) for values in (before, after) for pair in combinations(values, 2)}
    connection.execute("DELETE FROM handover_tasks WHERE handover_id=?", (ident,))
    connection.executemany("INSERT INTO handover_tasks VALUES(?,?)", [(ident, task) for task in after])
    for left, right in sorted(affected):
        connection.execute("DELETE FROM related WHERE via='handover' AND a_kind='task' AND a_id=? AND b_kind='task' AND b_id=?", (left, right))
        counts = connection.execute("SELECT handover_id,SUM(task_id=?),SUM(task_id=?) FROM handover_tasks WHERE task_id IN (?,?) GROUP BY handover_id", (left, right, left, right)).fetchall()
        multiplicity = sum(a * b if left != right else a * (a - 1) // 2 for _, a, b in counts)
        connection.executemany("INSERT INTO related VALUES('task',?,'task',?,'handover',1)", ((left, right) for _ in range(multiplicity)))
    return len(affected)


def maintain(connection, kind, ident, before, after, *, before_body=None, after_body=None):
    """Inputs are canonical affected documents, with None representing deletion."""
    counts = {"global_graph_rebuilds": 0, "path_comparisons": 0, "link_pairs": 0, "handover_pairs": 0}
    old_paths, new_paths = _paths(before, before_body), _paths(after, after_body)
    if old_paths != new_paths:
        connection.execute("DELETE FROM entity_paths WHERE kind=? AND id=?", (kind, ident))
        connection.executemany("INSERT INTO entity_paths VALUES(?,?,?,?,?)", [(kind, ident, *value) for value in new_paths])
        if Counter(value for value in old_paths if value[2] != "prose") != Counter(value for value in new_paths if value[2] != "prose"):
            counts["path_comparisons"] = _path_neighborhood(connection, kind, ident, new_paths)
    old_links, new_links = _links(kind, before), _links(kind, after)
    if old_links != new_links:
        counts["link_pairs"] = _declared_links(connection, kind, ident, new_links)
    if kind == "handover":
        old_tasks = [str(v) for v in as_list((before or {}).get("task_ids"))]
        new_tasks = [str(v) for v in as_list((after or {}).get("task_ids"))]
        if old_tasks != new_tasks:
            counts["handover_pairs"] = _handover_memberships(connection, ident, old_tasks, new_tasks)
    return counts
