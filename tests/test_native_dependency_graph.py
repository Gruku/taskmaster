"""User intent: on a native store `backlog_dependencies` and the viewer's dependency
half read the canonical indexed `dependencies` table instead of scanning every task,
and the additive `depth` traversal answers identically on both stores — default
output byte for byte unchanged, bounded, with cycles and truncation said out loud.
"""
from __future__ import annotations

from contextlib import closing
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
    bs.backlog_archive_task(task_id="test-epic-006", reason="deprecated")
    bs.backlog_archive_task(task_id="test-epic-007", reason="deprecated")
    _legacy_row("test-epic-019", "epic=?", "ghost-epic")
    _legacy_row("test-epic-017", "deleted=1")


IDS = [f"test-epic-{n:03d}" for n in range(1, 20)] + ["other-001", "other-002", "other-003",
                                                        "other-004", "ghost-9", "nope"]


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield make_twins(tmp_path_factory.mktemp("graph"), monkeypatch, _seed_graph)
    finally:
        monkeypatch.undo()


def test_default_output_is_unchanged_and_depth_one_is_the_default(graph):
    for ident in IDS:
        legacy, native = graph.same("backlog_dependencies", task_id=ident)
        assert graph.same("backlog_dependencies", task_id=ident, depth=1) == (legacy, native)
        assert "Transitive" not in legacy


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


def test_deadline_is_reported_identically(graph, monkeypatch):
    from taskmaster import dependency_chain
    monkeypatch.setattr(dependency_chain, "DEADLINE_S", 0.0)
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-005", depth=3)
    assert "not completed — traversal exceeded 0 s" in answer


@pytest.mark.parametrize("depth", [0, -1, 11, True, 2.0, "2"])
def test_invalid_depth_is_refused_identically(graph, depth):
    answer, _ = graph.same("backlog_dependencies", task_id="test-epic-001", depth=depth)
    assert answer == "Error: depth must be an integer from 1 to 10"


def test_native_dependencies_never_enumerate_tasks(graph, monkeypatch):
    from taskmaster.native_routing import reads

    def refuse(*_args, **_kwargs):
        raise AssertionError("backlog_dependencies enumerated every task")
    monkeypatch.setattr(reads, "epic_tasks", refuse)
    monkeypatch.setattr(reads, "page", refuse)
    with graph.at(graph.native):
        for depth in (1, 3):
            assert "Unblocks" in bs.backlog_dependencies(task_id="test-epic-003", depth=depth)


def test_reverse_lookups_use_the_target_index(graph):
    from taskmaster.native import dependency_graph
    with native_connection(graph.native) as connection:
        for sql, args in dependency_graph.explain_statements():
            plan = " ".join(row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, args))
            assert "ix_dependencies_target" in plan, plan
            assert "SCAN x" not in plan and "SCAN dependencies" not in plan, plan


def _related(graph, root, ident):
    with graph.at(root):
        if root == graph.legacy:
            return bs._load_related_for_task(ident)
        from taskmaster.native_routing import viewer
        return viewer.related(viewer.database(), ident)


def test_viewer_dependency_half_matches(graph):
    for ident in IDS:
        legacy, native = _related(graph, graph.legacy, ident), _related(graph, graph.native, ident)
        if legacy is None:
            assert native is None, ident
            continue
        for key in ("dependencies", "unblocks"):
            assert native[key] == legacy[key], (ident, key)

