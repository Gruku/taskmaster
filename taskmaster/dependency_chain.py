# User intent: `backlog_dependencies(depth=N)` answers "how far does this chain go"
# with one bounded traversal whose answer is identical on legacy and native stores:
# capped depth, capped output, a deadline, and cycles and truncation said out loud.
"""Bounded transitive dependency traversal: the shared walk result and its rendering.

Each store gathers a `Walk` its own way — the legacy store with `walk()` over its
in-memory task tree (`tree_walk`), the native store level by level with one
indexed query per frontier over the canonical `dependencies` table — and both
render through `lines()`, so a parity difference can only come from gathering,
never from presentation.

Semantics, per direction:
- Distance is the shortest hop count from the root. Hops of distance 1 are the
  existing one-hop answer; the transitive section lists distances 2..depth.
- A hop is followed only from a task reached at a distance below the depth cap.
- Current and archived-but-live tasks are followed, as the one-hop answer lists
  both. A deleted task, or one whose epic is gone, is not a task: upstream it is
  `[missing]` and not followed; downstream it never appears.
- A task whose `depends_on` cannot be read has no upstream hops and is never a
  downstream hop, as in the one-hop answer; upstream it is named as not followed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time

from taskmaster.query_guard import QUERY_TIMEOUT_S

# A chain question stays a chain question: past ten hops the answer is the graph.
MAX_DEPTH = 10
# Listed tasks per direction; the rest are counted, never silently dropped.
MAX_NODES = 200
# The SQL surface's statement deadline, applied to the whole traversal.
DEADLINE_S = QUERY_TIMEOUT_S
DEPTH_ERROR = f"Error: depth must be an integer from 1 to {MAX_DEPTH}"


def depth_error(depth) -> str | None:
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        return DEPTH_ERROR
    return None


class Deadline:
    """Wall-clock budget for one traversal; callable as a sqlite progress handler."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = DEADLINE_S if seconds is None else seconds
        self.expires_at = time.monotonic() + self.seconds
        self.expired = False

    def __call__(self) -> int:
        if time.monotonic() >= self.expires_at:
            self.expired = True
        return 1 if self.expired else 0


@dataclass
class Walk:
    """One direction of a traversal as a store gathered it."""
    root: str
    distance: dict = field(default_factory=dict)   # id -> shortest hops (root: 0)
    edges: set = field(default_factory=set)        # followed (from, to) hops
    missing: set = field(default_factory=set)      # reached ids naming no task
    unreadable: set = field(default_factory=set)   # followed-from tasks with unreadable depends_on
    via: dict = field(default_factory=dict)        # id -> least-id task one hop nearer the root
    timed_out: bool = False

    def hop(self, source, target, level) -> bool:
        """Record a followed hop from a task at `level - 1`; True when it reaches a new id."""
        self.edges.add((source, target))
        if target not in self.distance:
            self.distance[target], self.via[target] = level, source
            return True
        if self.distance[target] == level and source < self.via[target]:
            self.via[target] = source
        return False


def walk(root, depth, neighbours, exists, deadline) -> Walk:
    """Breadth-first walk. `neighbours(id)` answers the hop targets in order, or
    None for an unreadable `depends_on`; `exists(id)` says whether an id is a task."""
    result = Walk(root, {root: 0})
    frontier = [root]
    for level in range(1, depth + 1):
        following = []
        for node in frontier:
            if deadline():
                result.timed_out = True
                return result
            found = neighbours(node)
            if found is None:
                result.unreadable.add(node)
                continue
            for other in found:
                if not result.hop(node, other, level):
                    continue
                if exists(other):
                    following.append(other)
                else:
                    result.missing.add(other)
        frontier = following
    return result


def tree_walk(tasks, root, depth, direction, deadline) -> Walk:
    """`walk` over a loaded task list, the legacy store's way: every task is read
    through the one `depends_on` normaliser, and the first task with an id wins."""
    from taskmaster.native import blockers
    by_id, dependents = {}, {}
    for task in tasks:
        by_id.setdefault(task["id"], task)
    for task in tasks:
        declared = blockers.declared_dependencies(task)
        if not isinstance(declared, blockers.Unknown):
            for ident in dict.fromkeys(declared):
                dependents.setdefault(ident, []).append(task["id"])

    def upstream(ident):
        declared = blockers.declared_dependencies(by_id[ident])
        return None if isinstance(declared, blockers.Unknown) else declared

    def downstream(ident):
        return dependents.get(ident, [])
    return walk(root, depth, upstream if direction == "upstream" else downstream, by_id.__contains__, deadline)


def _first_cycle(result: Walk):
    """The first directed cycle among the followed hops, root first, ids in order."""
    graph = {}
    for source, target in result.edges:
        graph.setdefault(source, set()).add(target)
    state = {}
    starts = [result.root] + sorted(n for n in graph if n != result.root)
    for start in starts:
        if start in state:
            continue
        stack, path = [(start, iter(sorted(graph.get(start, ()))))], [start]
        state[start] = "open"
        while stack:
            node, pending = stack[-1]
            nxt = next(pending, None)
            if nxt is None:
                state[node] = "done"
                stack.pop()
                path.pop()
            elif state.get(nxt) == "open":
                return path[path.index(nxt):] + [nxt]
            elif nxt not in state:
                state[nxt] = "open"
                stack.append((nxt, iter(sorted(graph.get(nxt, ())))))
                path.append(nxt)
    return None


def lines(label: str, result: Walk, depth: int, describe, *, checks: bool) -> list[str]:
    """The transitive section for one direction. `describe(id)` answers
    `(title, status)` for a task; `checks` adds the upstream `[done]`/`[pending]`."""
    span = "2" if depth == 2 else f"2–{depth}"
    heading = f"\n**Transitive {label} (depth {span}):**"
    if result.timed_out:
        return [f"{heading} not completed — traversal exceeded {DEADLINE_S:g} s"]
    reached = sorted((d, n) for n, d in result.distance.items() if d >= 2)
    cycle = _first_cycle(result)
    # The root's own unreadable `depends_on` is already the one-hop answer.
    unfollowed = sorted(result.unreadable - {result.root})
    if not reached and cycle is None and not unfollowed:
        return [f"{heading} none"]
    out = [heading if reached else f"{heading} none"]
    for distance, node in reached[:MAX_NODES]:
        via = result.via[node]
        if node in result.missing:
            text = f"[missing] `{node}` — NOT FOUND"
        else:
            title, status = describe(node)
            check = ("[done] " if status == "done" else "[pending] ") if checks else ""
            text = f"{check}`{node}` — {title} ({status})"
        out.append(f"- [{distance}] {text} ← via `{via}`")
    if unfollowed:
        out.append("Not followed, `depends_on` unreadable: " + ", ".join(f"`{n}`" for n in unfollowed))
    if cycle is not None:
        out.append("Cycle: " + " → ".join(f"`{n}`" for n in cycle))
    if len(reached) > MAX_NODES:
        out.append(f"Truncated: showing {MAX_NODES} of {len(reached)} tasks reached — ask for a smaller depth")
    return out
