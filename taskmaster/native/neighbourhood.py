# User intent: a path or handover edit pays only for the claims that can actually pair
# with it, and the edit hook reads the same canonical neighbourhood `related` records.
"""Indexed path-candidate discovery and the canonical entity neighbourhood.

Standard library only: the edit hook imports this under the system interpreter.
The pairing rule is the frozen `related` contract: two structural path claims
pair when their texts are equal or either one is a glob that `fnmatchcase`
matches against the other's text (so a glob can match a glob). A pair weighs
once per matching claim pair, duplicates included; prose never pairs.

Candidates come from `ix_entity_paths_path` alone. A glob can only match text
that starts with the glob's literal prefix (everything before its first
`*`, `?` or `[`), so the globs that can match a text are found by, for every
prefix of that text, the range of rows that continue it with a wildcard; the
rows a glob can match are the range under its own literal prefix. A prefix-less
glob (`*.py`) genuinely can match anything, so it examines every row.
"""
from collections import Counter
import fnmatch
import json
import re

_WILDCARD = re.compile(r"[*?\[]")
_ROWS = ("SELECT e.rowid,e.kind,e.id,e.path,e.match_kind FROM entity_paths e "
         "WHERE e.source IN ('anchors','location') AND NOT(e.kind=? AND e.id=?)")
# Each (low, high) bound is one index range; the bounds table drives the join.
_RANGES = ("SELECT e.rowid,e.kind,e.id,e.path,e.match_kind FROM json_each(?) b "
           "JOIN entity_paths e ON e.path>=json_extract(b.value,'$[0]') AND e.path<json_extract(b.value,'$[1]') "
           "WHERE e.source IN ('anchors','location') AND NOT(e.kind=? AND e.id=?)")


def _after(prefix):
    """The least string above every string starting with `prefix`, or None for no bound."""
    while prefix:
        code = ord(prefix[-1]) + 1
        code = 0xE000 if 0xD800 <= code < 0xE000 else code
        if code <= 0x10FFFF:
            return prefix[:-1] + chr(code)
        prefix = prefix[:-1]
    return None


def pairs(path, match, other_path, other_match):
    return (path == other_path or (match == "glob" and fnmatch.fnmatchcase(other_path, path))
            or (other_match == "glob" and fnmatch.fnmatchcase(path, other_path)))


def candidates(connection, kind, ident, path, match):
    """Every structural claim of another entity that can pair with (path, match)."""
    found = {}
    for row in connection.execute(_ROWS + " AND e.path=?", (kind, ident, path)):
        found[row[0]] = row[1:]
    # Globs whose literal prefix is a prefix of `path`: the wildcard follows it.
    bounds = [(path[:i] + low, path[:i] + high) for i in range(len(path) + 1)
              for low, high in (("*", "+"), ("?", "@"), ("[", "\\"))]
    if match == "glob":
        literal = _WILDCARD.split(path, maxsplit=1)[0]
        high = _after(literal)
        if high is None:
            for row in connection.execute(_ROWS, (kind, ident)):
                found[row[0]] = row[1:]
        else:
            bounds.append((literal, high))
    for row in connection.execute(_RANGES, (json.dumps(bounds), kind, ident)):
        found[row[0]] = row[1:]
    return list(found.values())


def path_weights(connection, kind, ident, own):
    """`(Counter{(kind, id): weight}, candidates examined)` for `own` (path, match) claims."""
    weights, examined = Counter(), 0
    for path, match in own:
        found = candidates(connection, kind, ident, path, match)
        examined += len(found)
        for other_kind, other_id, other_path, other_match in found:
            if pairs(path, match, other_path, other_match):
                weights[other_kind, other_id] += 1
    return weights, examined


def neighbours(connection, kind, ident):
    """`Counter{(kind, id, via): weight}` equal to the `related` rows touching the entity.

    Self-pairs (a task listed twice in one handover) are excluded: an entity is
    not its own neighbour. Handover multiplicity, stored in `related` as repeated
    rows, is summed into the weight.
    """
    own = connection.execute("SELECT path,match_kind FROM entity_paths WHERE kind=? AND id=? "
                             "AND source IN ('anchors','location')", (kind, ident)).fetchall()
    found = Counter({(k, i, "path"): w for (k, i), w in path_weights(connection, kind, ident, own)[0].items()})
    if kind == "task":
        handovers = connection.execute(
            "SELECT DISTINCT c.public_id FROM memberships m JOIN entity_core c USING(entity_key) "
            "WHERE m.target_kind='task' AND m.target_id=? AND m.field='task_ids' AND c.kind='handover' AND c.deleted=0",
            (ident,)).fetchall()
        for (handover,) in handovers:
            members = Counter(task for (task,) in connection.execute(
                "SELECT task_id FROM handover_tasks WHERE handover_id=?", (handover,)))
            for task, count in members.items():
                if task != ident:
                    found["task", task, "handover"] += members[ident] * count
    return +found
