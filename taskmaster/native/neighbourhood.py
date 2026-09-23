# User intent: a path or handover edit pays only for the claims that can actually pair
# with it, and the edit hook reads the same canonical neighbourhood `related` records.
"""Indexed path-candidate discovery and the canonical entity neighbourhood.

Standard library only: the edit hook imports this under the system interpreter.
The pairing rule is the frozen `related` contract: two structural path claims
pair when their texts are equal or either one is a glob that `fnmatchcase`
matches against the other's text (so a glob can match a glob). A pair weighs
once per matching claim pair, duplicates included; prose never pairs.

A glob can only match text starting with its literal prefix (everything before
its first `*`, `?` or `[`). So the claims a glob can match lie in one range of
the structural path index, and the globs that can match a text are those whose
literal prefix is a prefix of it: found by a descending walk of the literal-
prefix index that jumps straight to the next possible prefix, never by building
a bound for every prefix (that is quadratic in the path length). A prefix-less
glob (`*.py`) genuinely can match anything, so it visits every structural claim.

`ensure_indexes` adds the four native-only indexes; the native writer creates
them, never a reader or the legacy store. Without them every query here is
still exact, only unindexed.
"""
from collections import Counter
import fnmatch
import os

_STRUCTURAL = "source IN ('anchors','location')"
# The literal prefix as SQL; the index below is on this exact expression.
LITERAL = "substr(path,1,min(" + ",".join(f"coalesce(nullif(instr(path,'{c}'),0),1073741824)" for c in "*?[") + ")-1)"
INDEXES = (
    f"CREATE INDEX IF NOT EXISTS ix_entity_paths_structural ON entity_paths(path) WHERE {_STRUCTURAL}",
    f"CREATE INDEX IF NOT EXISTS ix_entity_paths_glob_literal ON entity_paths({LITERAL}) WHERE match_kind='glob' AND {_STRUCTURAL}",
    "CREATE INDEX IF NOT EXISTS ix_handover_tasks_task ON handover_tasks(task_id,handover_id)",
    # Resolving a link target's kind by id alone (`relations._kind_for_id`).
    "CREATE INDEX IF NOT EXISTS ix_entity_core_public_id ON entity_core(public_id,deleted,kind)",
)
_ROWS = f"SELECT rowid,kind,id,path,match_kind FROM entity_paths WHERE {_STRUCTURAL}"
_GLOBS = f"FROM entity_paths WHERE match_kind='glob' AND {_STRUCTURAL}"
_NAMES = ("ix_entity_paths_structural", "ix_entity_paths_glob_literal", "ix_handover_tasks_task",
          "ix_entity_core_public_id")


def ensure_indexes(connection):
    """Create any missing native graph index; once present, one catalogue lookup."""
    present = connection.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='index' AND name IN ("
                                 + ",".join("?" for _ in _NAMES) + ")", _NAMES).fetchone()[0]
    if present != len(_NAMES):
        # The id index needs the native core table; a bare graph-table database
        # (the relation tests' fixtures) still gets the path indexes.
        core = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='entity_core'").fetchone()
        for statement in INDEXES:
            if core or " ON entity_core(" not in statement:
                connection.execute(statement)


def _after(prefix):
    """The least string above every string starting with `prefix`, or None for no bound."""
    while prefix:
        code = ord(prefix[-1]) + 1
        code = 0xE000 if 0xD800 <= code < 0xE000 else code
        if code <= 0x10FFFF:
            return prefix[:-1] + chr(code)
        prefix = prefix[:-1]
    return None


def _literal(pattern):
    return pattern[:min((i for i in (pattern.find(c) for c in "*?[") if i >= 0), default=len(pattern))]


def pairs(path, match, other_path, other_match):
    return (path == other_path or (match == "glob" and fnmatch.fnmatchcase(other_path, path))
            or (other_match == "glob" and fnmatch.fnmatchcase(path, other_path)))


def candidates(connection, path, match):
    """`(claims that can pair with (path, match), index entries visited)`, own claims included.

    The three discoveries overlap, and SQL's literal prefix can be shorter than
    Python's (SQLite text functions stop at NUL), so claims are keyed by rowid:
    each claim, duplicates included, is examined exactly once.
    """
    found = {}

    def take(sql, args):
        rows = connection.execute(sql, args).fetchall()
        found.update((row[0], row[1:]) for row in rows)
        return len(rows)
    visited = take(_ROWS + " AND path=?", (path,))
    # Globs whose literal prefix is a prefix of `path`. Invariant: every such
    # literal not yet taken is <= bound (< bound when `strict`).
    bound, strict = path, False
    while True:
        row = connection.execute(f"SELECT {LITERAL} {_GLOBS} AND {LITERAL}{'<' if strict else '<='}? "
                                 f"ORDER BY {LITERAL} DESC LIMIT 1", (bound,)).fetchone()
        visited += 1
        if row is None:
            break
        literal = row[0]
        if path.startswith(literal):
            visited += take(f"SELECT rowid,kind,id,path,match_kind {_GLOBS} AND {LITERAL}=?", (literal,))
            bound, strict = literal, True
        else:
            bound, strict = path[:len(os.path.commonprefix([literal, path]))], False
    if match == "glob":
        literal = _literal(path)
        high = _after(literal)
        visited += take(_ROWS + (" AND path>=? AND path<?" if high else " AND path>=?"), (literal, high) if high else (literal,))
    return list(found.values()), visited


def path_weights(connection, kind, ident, own):
    """`(Counter{(kind, id): weight}, index entries visited)` for `own` (path, match) claims."""
    weights, visited = Counter(), 0
    for path, match in own:
        found, cost = candidates(connection, path, match)
        visited += cost
        for other_kind, other_id, other_path, other_match in found:
            if (other_kind, other_id) != (kind, ident) and pairs(path, match, other_path, other_match):
                weights[other_kind, other_id] += 1
    return weights, visited


def neighbours(connection, kind, ident):
    """`Counter{(kind, id, via): weight}` equal to the `related` rows touching the entity.

    Self-pairs (a task listed twice in one handover) are excluded: an entity is
    not its own neighbour. Handover multiplicity, stored in `related` as repeated
    rows, is summed into the weight. Co-members come from `handover_tasks`, the
    source `related` itself is built from, so non-string task ids pair alike.
    """
    own = connection.execute("SELECT path,match_kind FROM entity_paths WHERE kind=? AND id=? "
                             f"AND {_STRUCTURAL}", (kind, ident)).fetchall()
    found = Counter({(k, i, "path"): w for (k, i), w in path_weights(connection, kind, ident, own)[0].items()})
    if kind == "task":
        handovers = connection.execute(
            "SELECT DISTINCT h.handover_id FROM handover_tasks h JOIN entity_core c "
            "ON c.kind='handover' AND c.public_id=h.handover_id AND c.deleted=0 WHERE h.task_id=?", (ident,)).fetchall()
        for (handover,) in handovers:
            members = Counter(task for (task,) in connection.execute(
                "SELECT task_id FROM handover_tasks WHERE handover_id=?", (handover,)))
            for task, count in members.items():
                if task != ident:
                    found["task", task, "handover"] += members[ident] * count
    return +found
