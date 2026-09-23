"""User intent: on a native store `backlog_dependencies` and the viewer's dependency
half read the canonical indexed `dependencies` table instead of scanning every task,
and the additive `depth` traversal answers identically on both stores — default
output byte for byte unchanged, bounded, with cycles and truncation said out loud.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import hand_edit_task, make_twins, native_connection


def _set(ident, **fields):
    hand_edit_task(ident, lambda doc: doc.update(fields))


def _legacy_row(ident, assignment, *args):
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db", isolation_level=None)) as db:
        db.execute(f"UPDATE entities SET {assignment} WHERE kind='task' AND id=?", (*args, ident))
    store.reset_for_tests()


def _add(title, epic="test-epic", **kwargs):
    bs.backlog_add_task(title=title, epic=epic, phase="dev", **kwargs)


def _seed_graph():
    """A chain, a duplicate, a self-dependency, a missing id, a cycle, archived
    sources and targets, a deleted task, an orphan and every unreadable shape."""
    bs.backlog_add_epic(epic_id="other", name="Other Epic", done_when="never")
    _add("Root")                                                   # 001
    _add("Two", depends_on="test-epic-001")                        # 002
    _add("Three", depends_on="test-epic-002")                      # 003
    _add("Four", depends_on="test-epic-003")                       # 004
    _add("Five", depends_on="test-epic-004")                       # 005
    _add("Other one", epic="other", depends_on="test-epic-003")    # other-001
    _add("Archived source", depends_on="test-epic-004")            # 006
    _add("Archived target")                                        # 007
    _add("Needs archived", depends_on="test-epic-007")             # 008
    for title in ("Mixed list", "Scalar int", "Empty id", "Empty string", "Null", "Empty map",
                  "Zero", "Bare string", "Doomed", "Needs doomed", "Orphan"):
        _add(title)                                                # 009..019
    for title in ("Cycle a", "Cycle b", "Cycle c"):
        _add(title, epic="other")                                  # other-002..004
    for title in ("K1", "K2", "K3", "K4", "K5", "Diamond top", "Diamond left", "Diamond right",
                  "Diamond bottom"):
        _add(title, epic="other")                                  # other-005..013
    _set("test-epic-003", depends_on=["test-epic-002", "test-epic-001", "test-epic-002"])
    _set("test-epic-004", depends_on=["test-epic-003", "ghost-9"])
    _set("test-epic-005", depends_on=["test-epic-005", "test-epic-004"])
    _set("test-epic-009", depends_on=["test-epic-001", 5])
    _set("test-epic-010", depends_on=7)
    _set("test-epic-011", depends_on=[""])
    _set("test-epic-012", depends_on="")
    _set("test-epic-013", depends_on=None)
    _set("test-epic-014", depends_on={})
    _set("test-epic-015", depends_on=0)
    _set("test-epic-016", depends_on="test-epic-001", order=-1)
    _set("test-epic-017", depends_on=["test-epic-001"])
    _set("test-epic-018", depends_on=["test-epic-017", "test-epic-009"])
    _set("test-epic-019", depends_on=["test-epic-001"], epic="ghost-epic")
    _set("other-002", depends_on=["other-004"])
    _set("other-003", depends_on=["other-002"])
    _set("other-004", depends_on=["other-003", "test-epic-005"])
    clique = [f"other-{n:03d}" for n in range(5, 10)]
    for ident in clique:
        _set(ident, depends_on=[other for other in clique if other != ident])
    _set("other-010", depends_on=["other-011", "other-012"])
    _set("other-011", depends_on=["other-013"])
    _set("other-012", depends_on=["other-013"])
    bs.backlog_archive_task(task_id="test-epic-006", reason="deprecated")
    bs.backlog_archive_task(task_id="test-epic-007", reason="deprecated")
    _legacy_row("test-epic-019", "epic=?", "ghost-epic")
    _legacy_row("test-epic-017", "deleted=1")
    bs.backlog_handover_create(tldr="Handover on three", task_ids=["test-epic-003", "other-001"])
    bs.backlog_issue_create(title="An issue", severity="P1", evidence="seen",
                            related_tasks=["test-epic-003"])


IDS = ([f"test-epic-{n:03d}" for n in range(1, 20)] + [f"other-{n:03d}" for n in range(1, 14)]
       + ["ghost-9", "nope"])


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield make_twins(tmp_path_factory.mktemp("graph"), monkeypatch, _seed_graph)
    finally:
        monkeypatch.undo()


# Every default answer as the pre-N14 code gave it (`43acef1`, both stores agreed),
# generated once from that tree over this seed: the frozen contract, not the new code.
GOLDEN = Path(__file__).parent / "fixtures" / "native_dependencies_depth1_golden.json"


def test_default_output_is_the_frozen_pre_n14_answer(graph):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(IDS)
    for ident in IDS:
        legacy, native = graph.same("backlog_dependencies", task_id=ident)
        assert legacy == native == golden[ident], ident
        assert graph.same("backlog_dependencies", task_id=ident, depth=1) == (golden[ident], golden[ident])


def test_one_hop_covers_archived_duplicate_self_missing_and_unreadable(graph):
    root, _ = graph.same("backlog_dependencies", task_id="test-epic-001")
    unblocks = root.split("**Unblocks (downstream):**")[1]
    # Archived-but-live sources count; deleted, orphaned and unreadable ones do not.
    assert "`test-epic-016`" in unblocks and unblocks.index("`test-epic-016`") < unblocks.index("`test-epic-002`")
    assert "`test-epic-003`" in unblocks and unblocks.count("`test-epic-003`") == 1
    for absent in ("test-epic-017", "test-epic-019", "test-epic-009"):
        assert f"`{absent}`" not in unblocks
    five, _ = graph.same("backlog_dependencies", task_id="test-epic-005")
    assert five.count("`test-epic-005`") == 3  # heading, upstream and downstream of itself
    archived, _ = graph.same("backlog_dependencies", task_id="test-epic-008")
    assert "`test-epic-007` — Archived target (archived)" in archived
    doomed, _ = graph.same("backlog_dependencies", task_id="test-epic-018")
    assert "[missing] `test-epic-017`" in doomed


@pytest.mark.parametrize("depth", [2, 3, 10])
def test_transitive_answers_match_on_both_stores(graph, depth):
    for ident in IDS:
        graph.same("backlog_dependencies", task_id=ident, depth=depth)


def test_depth_extends_the_default_answer_without_changing_it(graph):
    base, _ = graph.same("backlog_dependencies", task_id="test-epic-005")
    deep, _ = graph.same("backlog_dependencies", task_id="test-epic-005", depth=3)
    assert deep.startswith(base + "\n")
    tail = deep[len(base):]
    assert "**Transitive upstream (depth 2–3):**" in tail
    assert "- [2] [pending] `test-epic-003` — Three (todo) ← via `test-epic-004`" in tail
    assert "- [2] [missing] `ghost-9` — NOT FOUND ← via `test-epic-004`" in tail
    assert "- [3] [pending] `test-epic-001` — Root (todo) ← via `test-epic-003`" in tail
    assert "Cycle: `test-epic-005` → `test-epic-005`" in tail
    assert "**Transitive downstream (depth 2–3):**" in tail
    assert "- [2] [pending] `other-003`" not in tail  # downstream lines carry no check mark
    assert "- [2] `other-002` — Cycle a (todo) ← via `other-004`" in tail
    assert "- [3] `other-003` — Cycle b (todo) ← via `other-002`" in tail


def test_cycles_are_reported_and_traversal_terminates(graph):
    answer, _ = graph.same("backlog_dependencies", task_id="other-002", depth=10)
    assert "Cycle: `other-002` → `other-004` → `other-003` → `other-002`" in answer
    assert answer.count("`other-003`") >= 2


def test_unreadable_intermediate_is_named_not_followed(graph):
    # test-epic-009 depends on [test-epic-001, 5]: unreadable, so never an upstream hop.
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-009", depth=3)
    assert "**Transitive upstream (depth 2–3):** none" in answer
    reaching, _ = graph.same("backlog_dependencies", task_id="test-epic-018", depth=3)
    upstream = reaching.split("**Transitive upstream (depth 2–3):**")[1].split("**Transitive downstream")[0]
    assert "Not followed, `depends_on` unreadable: `test-epic-009`" in upstream
    assert "`test-epic-001`" not in upstream


def test_output_cap_truncates_identically(graph, monkeypatch):
    from taskmaster import dependency_chain
    monkeypatch.setattr(dependency_chain, "MAX_NODES", 2)
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-001", depth=10)
    assert "Truncated: showing 2 of " in answer


@pytest.mark.parametrize("depth", [0, -1, 11, True, 2.0, "2"])
def test_invalid_depth_is_refused_identically_by_direct_calls(graph, depth):
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-001", depth=depth)
    assert answer == "Error: depth must be an integer from 1 to 10"


def _call_tool(arguments):
    import asyncio
    from fastmcp.exceptions import ToolError, ValidationError
    try:
        result = asyncio.run(bs.mcp.call_tool("backlog_dependencies", arguments))
    except (ToolError, ValidationError) as exc:
        return "refused: " + str(exc)
    return "".join(block.text for block in result.content)


@pytest.mark.parametrize("depth", [True, "2", 2.0, 0, 11])
def test_mcp_refuses_a_non_integer_depth_rather_than_coercing_it(graph, depth):
    for root in (graph.legacy, graph.native):
        with graph.at(root):
            answer = _call_tool({"task_id": "test-epic-005", "depth": depth})
        if type(depth) is int:
            assert answer == "Error: depth must be an integer from 1 to 10", answer
        else:
            assert answer.startswith("refused: ") and "depth" in answer, answer
            assert "Transitive" not in answer


def test_mcp_depth_matches_the_direct_call(graph):
    for root in (graph.legacy, graph.native):
        with graph.at(root):
            direct = bs.backlog_dependencies(task_id="test-epic-005", depth=3)
            assert _call_tool({"task_id": "test-epic-005", "depth": 3}) == direct
            assert _call_tool({"task_id": "test-epic-005"}) == bs.backlog_dependencies(task_id="test-epic-005")


def test_native_dependencies_never_enumerate_tasks(graph, monkeypatch):
    from taskmaster.native_routing import reads

    def refuse(*_args, **_kwargs):
        raise AssertionError("backlog_dependencies enumerated every task")
    monkeypatch.setattr(reads, "epic_tasks", refuse)
    monkeypatch.setattr(reads, "page", refuse)
    with graph.at(graph.native):
        for depth in (1, 3):
            assert "Unblocks" in bs.backlog_dependencies(task_id="test-epic-003", depth=depth)


def test_every_traversal_statement_probes_an_index_by_equality(graph):
    """The driving table of each step is searched by equality on the ids it is
    handed, and nothing but the id list itself is scanned."""
    from taskmaster.native import dependency_graph
    with native_connection(graph.native) as connection:
        for sql, args, constraint in dependency_graph.explain_statements():
            plan = [row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, args)]
            driving = next(step for step in plan if step.startswith(("SEARCH x ", "SEARCH t ", "SEARCH c ")))
            assert constraint in driving, plan
            scans = [step for step in plan if step.startswith("SCAN")]
            assert scans and all(step.startswith(("SCAN json_each ", "SCAN j ")) for step in scans), plan
        target = next(sql for sql, _args, constraint in dependency_graph.explain_statements()
                      if constraint == "target_id=?")
        plan = [row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + target, ('["x"]',))]
        assert plan[0] == "SEARCH x USING INDEX ix_dependencies_target (target_kind=? AND target_id=?)", plan
        resolve = next(sql for sql, _args, constraint in dependency_graph.explain_statements()
                       if constraint == "public_id=?")
        plan = [row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + resolve, ('["x"]',))]
        assert "SEARCH t USING INDEX sqlite_autoindex_entity_core_1 (kind=? AND public_id=?)" in plan, plan


def test_viewer_related_never_loads_the_task_tree(graph, monkeypatch):
    from taskmaster.native_routing import reads, viewer

    def refuse(*_args, **_kwargs):
        raise AssertionError("the related panel loaded the task tree")
    page = reads.page

    def page_without_tasks(snapshot, kind, **kwargs):
        if kind == "task":
            refuse()
        return page(snapshot, kind, **kwargs)
    monkeypatch.setattr(reads, "tree", refuse)
    monkeypatch.setattr(reads, "page", page_without_tasks)
    with graph.at(graph.native):
        answer = viewer.related(viewer.database(), "test-epic-003")
    assert [row["id"] for row in answer["unblocks"]] == ["test-epic-004", "other-001"]


def _related(graph, root, ident):
    with graph.at(root):
        if root == graph.legacy:
            return bs._load_related_for_task(ident)
        from taskmaster.native_routing import viewer
        return viewer.related(viewer.database(), ident)


def _paths_rooted(answer, root):
    if answer is None:
        return None
    return {key: [{**row, "_path": row["_path"].replace(str(root), "<root>")} if "_path" in row else row
                  for row in rows] if isinstance(rows, list) else rows for key, rows in answer.items()}


def test_viewer_related_panel_matches(graph):
    seen = set()
    for ident in IDS:
        legacy = _paths_rooted(_related(graph, graph.legacy, ident), graph.legacy)
        native = _paths_rooted(_related(graph, graph.native, ident), graph.native)
        assert native == legacy, ident
        if legacy:
            seen.update(key for key in ("handovers", "issues", "dependencies", "unblocks") if legacy[key])
    # Issues carry `related_tasks`, and no tool writes an issue `task_ids`, so the
    # issues list is empty on both stores; it is still compared.
    assert seen == {"handovers", "dependencies", "unblocks"}



# ── Shapes at scale: identical answers, each task expanded once ─────────────


def _native_graph(tasks):
    """An in-memory native store holding just these tasks, all in epic `e`."""
    from taskmaster.native import migrate, schema
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("BEGIN")
    schema.create_schema(connection)
    connection.execute("INSERT INTO entity_core(kind,public_id,revision,last_seq) VALUES('epic','e',1,1)")
    for task in tasks:
        connection.execute("INSERT INTO entity_core(kind,public_id,revision,last_seq,title_json,status_json) "
                           "VALUES('task',?,1,1,?,'\"todo\"')", (task["id"], migrate.encode(task["title"])))
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?",
                                 (task["id"],)).fetchone()[0]
        connection.execute("INSERT INTO task_operational(entity_key,epic_json) VALUES(?,'\"e\"')", (key,))
        migrate._put_relations(connection, key, "task", {"depends_on": task["depends_on"]})
    connection.execute("COMMIT")
    connection.execute("BEGIN")
    return connection


def _tasks(deps):
    return [{"id": ident, "title": ident.upper(), "status": "todo", "depends_on": value}
            for ident, value in deps.items()]


def _clique(k):
    return {f"c{i}": [f"c{j}" for j in range(k) if j != i] for i in range(k)}


def _hub(n):
    deps = {"r": [], "h": [f"a{i}" for i in range(n)]}
    deps.update({f"a{i}": ["r"] for i in range(n)})
    deps.update({f"b{j}": ["h"] for j in range(n)})
    return deps


def _diamonds(levels):
    """Stacked diamonds: every level doubles the paths to the bottom, never the tasks."""
    deps = {"d0": []}
    for level in range(1, levels + 1):
        deps[f"l{level}"] = [f"d{level - 1}"]
        deps[f"r{level}"] = [f"d{level - 1}"]
        deps[f"d{level}"] = [f"l{level}", f"r{level}"]
    return deps


def _star(n):
    deps = {"r": []}
    deps.update({f"t{i}": ["r"] for i in range(n)})
    return deps


SHAPES = {"clique-20": (_clique(20), "c0"), "clique-60": (_clique(60), "c0"), "hub-300": (_hub(300), "r"),
          "hub-300-up": (_hub(300), "h"), "diamonds-12": (_diamonds(12), "d12"),
          "diamonds-12-down": (_diamonds(12), "d0"), "star-2000": (_star(2000), "r"),
          "hub-4000": (_hub(4000), "r"), "clique-250": (_clique(250), "c0")}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("depth", [3, 10])
def test_graph_shapes_answer_identically_well_inside_the_deadline(shape, depth):
    import time
    from taskmaster import dependency_chain
    from taskmaster.native import dependency_graph
    deps, root = SHAPES[shape]
    tasks = _tasks(deps)
    connection = _native_graph(tasks)
    try:
        for direction in ("upstream", "downstream"):
            started = time.perf_counter()
            native = dependency_graph.traverse(connection, root, depth, direction, dependency_chain.Deadline())
            native_s = time.perf_counter() - started
            legacy = dependency_chain.tree_walk(tasks, root, depth, direction, dependency_chain.Deadline())
            assert not native.timed_out and not legacy.timed_out
            assert native_s < 1.0, (shape, direction, native_s)

            def describe(ident):
                return ident.upper(), "todo"
            assert (dependency_chain.lines(direction, native, depth, describe, checks=True)
                    == dependency_chain.lines(direction, legacy, depth, describe, checks=True))
            assert (native.distance, native.edges, native.missing, native.unreadable, native.via) == \
                   (legacy.distance, legacy.edges, legacy.missing, legacy.unreadable, legacy.via)
    finally:
        connection.close()


def test_one_hop_dependents_of_a_star_centre_are_an_index_probe():
    import time
    from taskmaster.native import dependency_graph
    connection = _native_graph(_tasks(_star(2000)))
    try:
        started = time.perf_counter()
        found = dependency_graph.dependents(connection, "r")
        assert time.perf_counter() - started < 0.5
        assert sorted(found) == sorted(f"t{i}" for i in range(2000))
    finally:
        connection.close()


class _Tripwire:
    """A deadline that expires on its first check, recording who checked it."""

    def __init__(self, seconds=None):
        import sys
        self._frame = sys._getframe
        self.expired, self.checked_by = False, None

    def __call__(self):
        if self.checked_by is None:
            self.checked_by = self._frame(1).f_code.co_name
        self.expired = True
        return 1


def test_the_deadline_interrupts_a_running_native_statement():
    from taskmaster import dependency_chain
    from taskmaster.native import dependency_graph
    tasks = _tasks(_star(2000))
    connection = _native_graph(tasks)
    try:
        native_deadline, legacy_deadline = _Tripwire(), _Tripwire()
        native = dependency_graph.traverse(connection, "r", 3, "downstream", native_deadline)
        legacy = dependency_chain.tree_walk(tasks, "r", 3, "downstream", legacy_deadline)
    finally:
        connection.close()
    # The first check came from SQLite's progress handler inside a level's query.
    assert native_deadline.checked_by in ("_dependent_pairs", "_resolve"), native_deadline.checked_by
    assert native.timed_out and legacy.timed_out
    rendered = [dependency_chain.lines("downstream", w, 3, None, checks=False) for w in (native, legacy)]
    assert rendered[0] == rendered[1] == [
        f"\n**Transitive downstream (depth 2–3):** not completed — traversal exceeded {dependency_chain.DEADLINE_S:g} s"]


def test_an_expired_deadline_reads_the_same_through_the_tool(graph, monkeypatch):
    from taskmaster import dependency_chain
    monkeypatch.setattr(dependency_chain, "Deadline", _Tripwire)
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-005", depth=3)
    assert "**Transitive upstream (depth 2–3):** not completed — traversal exceeded 5 s" in answer
    assert "**Transitive downstream (depth 2–3):** not completed — traversal exceeded 5 s" in answer


# ── Intentional difference: an unrelated unreadable `order` ─────────────────


def _seed_bad_order():
    _add("Needed")
    _add("Needs it", depends_on="test-epic-001")
    _add("Unrelated")
    _set("test-epic-003", order=None)
    _legacy_row("test-epic-003", "doc=json_set(doc,'$.order',json('null'))")


def test_native_answers_where_legacy_raises_on_an_unrelated_null_order(tmp_path, monkeypatch):
    """Recorded in the N02 spec: the legacy scan sorts every task and raises on an
    unrelated task's `order: null`; the native reverse lookup never reads it."""
    twins = make_twins(tmp_path, monkeypatch, _seed_bad_order)
    with twins.at(twins.legacy):
        with pytest.raises(TypeError):
            bs.backlog_dependencies(task_id="test-epic-001")
    with twins.at(twins.native):
        answer = bs.backlog_dependencies(task_id="test-epic-001")
    assert "- `test-epic-002` — Needs it (todo)" in answer


def _layered(width):
    deps = {"r": []}
    deps.update({f"a{i:03d}": ["r"] for i in range(width)})
    deps.update({f"b{j:03d}": [f"a{i:03d}" for i in range(width)] for j in range(width)})
    return deps


def test_rendering_a_dense_graph_is_linear_in_its_hops():
    """400 x 400 layers: 160k hops. `via` is recorded while walking, so rendering
    never rescans the hops for each listed task."""
    import time
    from taskmaster import dependency_chain
    walked = dependency_chain.tree_walk(_tasks(_layered(400)), "r", 3, "downstream", dependency_chain.Deadline())
    assert len(walked.edges) == 400 + 400 * 400
    started = time.perf_counter()
    out = dependency_chain.lines("downstream", walked, 3, lambda ident: (ident.upper(), "todo"), checks=False)
    assert time.perf_counter() - started < 1.0
    assert out[1] == "- [2] `b000` — B000 (todo) ← via `a000`"
    assert out[-1] == "Truncated: showing 200 of 400 tasks reached — ask for a smaller depth"


def _fan(listed):
    """Root, one task depending on it, and `listed` tasks two hops away."""
    deps = {"r": [], "m": ["r"]}
    deps.update({f"t{i:03d}": ["m"] for i in range(listed)})
    return deps


@pytest.mark.parametrize("listed", [200, 201])
def test_the_output_cap_boundary(listed):
    from taskmaster import dependency_chain
    from taskmaster.native import dependency_graph
    tasks = _tasks(_fan(listed))
    connection = _native_graph(tasks)
    try:
        native = dependency_graph.traverse(connection, "r", 2, "downstream", dependency_chain.Deadline())
    finally:
        connection.close()
    legacy = dependency_chain.tree_walk(tasks, "r", 2, "downstream", dependency_chain.Deadline())
    rendered = [dependency_chain.lines("downstream", w, 2, lambda i: (i.upper(), "todo"), checks=False)
                for w in (native, legacy)]
    assert rendered[0] == rendered[1]
    assert len([line for line in rendered[0] if line.startswith("- [2] ")]) == 200
    truncation = [line for line in rendered[0] if line.startswith("Truncated")]
    if listed == 200:
        assert truncation == []
    else:
        assert truncation == ["Truncated: showing 200 of 201 tasks reached — ask for a smaller depth"]


class _ExpiresAfter:
    """A deadline with room for exactly `checks` checks."""

    def __init__(self, checks):
        self.left, self.expired = checks, False

    def __call__(self):
        self.left -= 1
        self.expired = self.expired or self.left < 0
        return 1 if self.expired else 0


def test_a_finished_walk_is_never_reported_as_timed_out():
    """r <- a: two levels of work, and room for exactly the checks made while
    work remains; any check after the last level would expire the deadline."""
    from taskmaster import dependency_chain
    from taskmaster.native import dependency_graph
    tasks = _tasks({"r": [], "a": ["r"]})
    connection = _native_graph(tasks)
    try:
        native = dependency_graph.traverse(connection, "r", 5, "downstream", _ExpiresAfter(1))
    finally:
        connection.close()
    legacy = dependency_chain.tree_walk(tasks, "r", 5, "downstream", _ExpiresAfter(2))
    assert not native.timed_out and not legacy.timed_out
    assert native.distance == legacy.distance == {"r": 0, "a": 1}
