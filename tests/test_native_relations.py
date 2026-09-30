"""Selective relation updates match the legacy full-rebuild oracle exactly."""
from collections import Counter
from contextlib import closing
import json
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest

from taskmaster import store
from taskmaster.native.migrate import backfill
from taskmaster.native.relations import maintain, grouped_weights


def derived(connection):
    return {table: Counter(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
            for table in ("entity_paths", "links", "handover_tasks", "related")}


def test_selective_derived_matches_full_oracle_under_domain_and_random_edits():
    rng = random.Random(1701)
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as oracle, closing(sqlite3.connect(":memory:", isolation_level=None)) as native:
        oracle.row_factory = sqlite3.Row
        oracle.executescript(store.SCHEMA_SQL)
        oracle.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "test")])
        state = {}
        for kind, ident in [("task", f"T-{i}") for i in range(5)] + [("handover", "H-1"), ("handover", "H-2")]:
            doc = {"id": ident, "title": ident}
            oracle.execute("INSERT INTO entities VALUES(?,?,NULL,NULL,0,0,?,NULL,1,1)", (kind, ident, json.dumps(doc)))
            state[kind, ident] = (doc, None, False)
        oracle.backup(native)
        backfill(native)
        fake_store = store.Store.__new__(store.Store)
        choices = ["src/a.py", "src/B.py", "src/*", "src/?.py", "*", "src/[aB].py", "docs/x", "SRC/a.py"]
        for _ in range(80):
            kind, ident = rng.choice(list(state))
            before, before_body, before_deleted = state[kind, ident]
            after = dict(before, anchors=[rng.choice(choices) for _ in range(rng.randrange(4))],
                         location=[rng.choice(choices) for _ in range(rng.randrange(3))],
                         links=[{"type": rng.choice(["relates_to", "depends_on", "blocks"]), "target": rng.choice(["T-0", "T-1", "missing", "H-1"])}])
            if kind == "handover":
                after["task_ids"] = [rng.choice(["T-0", "T-1", "T-2"]) for _ in range(rng.randrange(5))]
            deleted = rng.randrange(8) == 0
            body = "See `src/code.py` for details" if rng.randrange(2) else None
            oracle.execute("UPDATE entities SET doc=?,body=?,deleted=? WHERE kind=? AND id=?", (json.dumps(after), body, deleted, kind, ident))
            fake_store._refresh_derived(SimpleNamespace(connection=oracle, _derived_keys={(kind, ident)}))
            # Resolution uses the current native core lifecycle, never old doc rows.
            native.execute("UPDATE entity_core SET deleted=? WHERE kind=? AND public_id=?", (deleted, kind, ident))
            maintain(native, kind, ident, before if not before_deleted else None, after if not deleted else None,
                     before_body=before_body, after_body=body)
            assert derived(native) == derived(oracle), (kind, ident)
            # Both incremental paths also equal the legacy full rebuild, so a link
            # written before its target (H-1 deleted, then live) cannot drift.
            with closing(sqlite3.connect(':memory:', isolation_level=None)) as full:
                full.row_factory = sqlite3.Row
                oracle.backup(full)
                everything = {(r[0], r[1]) for r in full.execute('SELECT kind,id FROM entities')}
                fake_store._refresh_derived(SimpleNamespace(connection=full, _derived_keys=everything))
                assert derived(full) == derived(oracle), ('full rebuild', kind, ident)
            state[kind, ident] = (after, body, deleted)


def test_metadata_only_maintenance_executes_no_graph_sql():
    connection = sqlite3.connect(":memory:")
    statements = []
    connection.set_trace_callback(statements.append)
    before = {"id": "T-1", "anchors": ["src/*.py"], "title": "Before"}
    after = dict(before, title="After", next_step="Continue")
    counts = maintain(connection, "task", "T-1", before, after)
    assert counts["path_comparisons"] == 0
    assert statements == []
    connection.close()


def test_grouped_prefix_oracle_matches_naive_duplicate_glob_weights():
    import fnmatch
    rng = random.Random(9301)
    for _ in range(100):
        values = [("task", f"T-{rng.randrange(5)}", rng.choice(["src/a", "src/*", "src/[ab]", "*", "SRC/a"]), rng.choice(["glob", "exact"])) for _ in range(25)]
        expected = Counter()
        for i, (kind, ident, path, match) in enumerate(values):
            for rk, ri, rp, rm in values[i + 1:]:
                if (kind, ident) != (rk, ri) and (path == rp or (match == "glob" and fnmatch.fnmatchcase(rp, path)) or (rm == "glob" and fnmatch.fnmatchcase(path, rp))):
                    expected[tuple(sorted(((kind, ident), (rk, ri))))] += 1
        assert grouped_weights(values) == expected


# ── N14: indexed candidate discovery and the canonical neighbourhood ──

def _paths_connection(indexed=True):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.executescript(store.SCHEMA_SQL)
    if indexed:
        from taskmaster.native.neighbourhood import ensure_indexes
        ensure_indexes(connection)
    return connection


def _seed_paths(connection, docs):
    """`docs` is {(kind, id): anchors}; `related` is seeded from the full oracle."""
    for (kind, ident), anchors in docs.items():
        maintain_rows = [(kind, ident, path, match, "anchors") for path, match in
                         ((a, "glob" if ("*" in a or "?" in a) else "exact") for a in anchors)]
        connection.executemany("INSERT INTO entity_paths VALUES(?,?,?,?,?)", maintain_rows)
    connection.execute("DELETE FROM related WHERE via='path'")
    connection.executemany("INSERT INTO related VALUES(?,?,?,?,'path',?)",
                           [(*a, *b, w) for (a, b), w in _oracle(connection).items()])


def _oracle(connection):
    return grouped_weights(connection.execute(
        "SELECT kind,id,path,match_kind FROM entity_paths WHERE source IN ('anchors','location')").fetchall())


def _path_related(connection):
    return Counter({((a, b), (c, d)): w for a, b, c, d, w in
                    connection.execute("SELECT a_kind,a_id,b_kind,b_id,weight FROM related WHERE via='path'")})


def _anchor_edit_work(unrelated, prose=0):
    """`(path_comparisons, SQLite VM steps)` of one anchor edit."""
    connection = _paths_connection()
    docs = {("task", f"T-{i}"): anchors for i, anchors in enumerate(
        [["src/app/main.py"], ["src/app/*.py"], ["src/**"], ["src/app/util.py", "docs/x.md"], ["docs/*"]])}
    # Unrelated anchored documents: literal and glob claims in a disjoint tree.
    docs.update({("task", f"U-{n}"): [f"zz/m{n % 97}/f{n}.py", f"zz/m{n % 89}/**"] for n in range(unrelated)})
    _seed_paths(connection, docs)
    # Prose mentions under the edited tree never pair, so they must never be walked.
    connection.executemany("INSERT INTO entity_paths VALUES('note',?,?,'exact','prose')",
                           [(f"P-{n}", f"src/app/p{n}.py") for n in range(prose)])
    before = {"id": "T-9", "anchors": ["src/app/old.py"]}
    connection.execute("INSERT INTO entity_paths VALUES('task','T-9','src/app/old.py','exact','anchors')")
    after = {"id": "T-9", "anchors": ["src/app/main.py", "src/app/*.py", "src/app/main.py"]}
    steps = [0]

    def tick():
        steps[0] += 1
        return 0
    connection.set_progress_handler(tick, 1)
    counts = maintain(connection, "task", "T-9", before, after)
    connection.set_progress_handler(None, 1)
    assert _path_related(connection) == _oracle(connection)
    return counts["path_comparisons"], steps[0]


def test_anchor_edit_work_is_bounded_by_candidates_not_unrelated_paths():
    """Neither the counter nor the SQLite work of an anchor edit grows with 2,000
    unrelated anchored documents plus 20,000 prose mentions under the edited tree."""
    (small, small_steps), (large, large_steps) = _anchor_edit_work(0), _anchor_edit_work(2000, prose=20000)
    assert small > 0
    assert large == small, (small, large)
    assert large_steps <= small_steps * 1.2, (small_steps, large_steps)


ADVERSARIAL = ["src/a.py", "src/B.py", "SRC/a.py", "src/*", "src/*.py", "src/?.py", "*", "*.py", "**",
               "src/[aB].py", "src/[aB]*.py", "src/[!a]*", "[s]rc/*", "src/", "src/**", "src/a?py",
               "src/*/", "src/a.py/", "s*", "sr?/*", "src/[", "src/[*", "docs/x", "docs/X*",
               # SQLite text functions stop at NUL, so the SQL literal prefix is shorter than Python's.
               "\x00q*", "\x00q", "src/\x00*", "src/\x00a.py", "s\x00?"]


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "unindexed-reader"])
def test_indexed_path_neighbourhood_matches_full_oracle_on_adversarial_globs(indexed):
    """Character classes, case, glob-matching-glob, prefix-less globs and duplicate anchors,
    with the native indexes and without them (a reader of a store no writer has upgraded)."""
    rng = random.Random(1414)
    connection = _paths_connection(indexed)
    state = {("task", f"T-{i}"): None for i in range(4)} | {("bug", f"B-{i}"): None for i in range(3)}
    for step in range(300):
        kind, ident = rng.choice(list(state))
        before = state[kind, ident]
        after = {"id": ident, "anchors": [rng.choice(ADVERSARIAL) for _ in range(rng.randrange(5))]}
        if kind == "bug":
            after = {"id": ident, "location": [rng.choice(ADVERSARIAL) for _ in range(rng.randrange(4))]}
        if rng.randrange(10) == 0:
            after = None
        maintain(connection, kind, ident, before, after)
        assert _path_related(connection) == _oracle(connection), (step, kind, ident, after)
        state[kind, ident] = after


def _related_neighbours(connection, kind, ident):
    """The legacy answer: every `related` row touching the entity, aggregated, self excluded."""
    found = Counter()
    for a_kind, a_id, b_kind, b_id, via, weight in connection.execute("SELECT * FROM related"):
        for (mine, other) in ((((a_kind, a_id)), (b_kind, b_id)), (((b_kind, b_id)), (a_kind, a_id))):
            if mine == (kind, ident) and other != (kind, ident):
                found[(*other, via)] += weight
    return found


def test_canonical_neighbourhood_matches_related_on_both_stores_over_seeded_edits():
    from taskmaster.native import neighbourhood
    from taskmaster.native.commands import _write_field
    rng = random.Random(4242)
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as oracle, closing(sqlite3.connect(":memory:", isolation_level=None)) as native:
        oracle.row_factory = sqlite3.Row
        oracle.executescript(store.SCHEMA_SQL)
        oracle.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "test")])
        state = {}
        for kind, ident in [("task", f"T-{i}") for i in range(5)] + [("task", "5"), ("bug", "B-1"), ("handover", "H-1"), ("handover", "H-2")]:
            doc = {"id": ident, "title": ident}
            oracle.execute("INSERT INTO entities VALUES(?,?,NULL,NULL,0,0,?,NULL,1,1)", (kind, ident, json.dumps(doc)))
            state[kind, ident] = (doc, False)
        oracle.backup(native)
        backfill(native)
        fake_store = store.Store.__new__(store.Store)
        for step in range(80):
            kind, ident = rng.choice(list(state))
            before, before_deleted = state[kind, ident]
            after = dict(before, anchors=[rng.choice(ADVERSARIAL) for _ in range(rng.randrange(4))])
            if kind == "handover":
                # A YAML `task_ids: [5]` entry is an integer; `related` still pairs task "5".
                after["task_ids"] = [rng.choice(["T-0", "T-1", "T-2", "T-3", 5, "5"]) for _ in range(rng.randrange(5))]
            deleted = rng.randrange(8) == 0
            oracle.execute("UPDATE entities SET doc=?,deleted=? WHERE kind=? AND id=?", (json.dumps(after), deleted, kind, ident))
            fake_store._refresh_derived(SimpleNamespace(connection=oracle, _derived_keys={(kind, ident)}))
            key = native.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?", (kind, ident)).fetchone()[0]
            native.execute("UPDATE entity_core SET deleted=? WHERE entity_key=?", (deleted, key))
            if "task_ids" in after:
                _write_field(native, key, kind, "task_ids", after["task_ids"])
            maintain(native, kind, ident, before if not before_deleted else None, after if not deleted else None)
            state[kind, ident] = (after, deleted)
            for (k, i), (_, gone) in state.items():
                if not gone:
                    expected = _related_neighbours(oracle, k, i)
                    assert _related_neighbours(native, k, i) == expected, (step, k, i)
                    assert neighbourhood.neighbours(native, k, i) == expected, (step, k, i)


def test_a_very_long_anchor_is_linear_and_matches_the_oracle():
    """30,000-character claims: discovery must not build a bound per prefix (quadratic)."""
    long = ("a/" * 15000)[:30000]
    connection = _paths_connection()
    _seed_paths(connection, {
        ("task", "T-1"): [long],                           # exact equality
        ("task", "T-2"): [long[:29990] + "*"],             # a glob sharing a 29,990-character literal
        ("task", "T-3"): [long[:15000] + "X*"],            # a long shared prefix that is not a literal prefix
        ("task", "T-4"): [long + "?"],                      # literal equals the whole path, still no match
        ("task", "T-5"): ["*"], ("task", "T-6"): ["a/*"]})
    after = {"id": "T-9", "anchors": [long, long[:20000] + "*"]}
    started = time.perf_counter()
    maintain(connection, "task", "T-9", None, after)
    elapsed = time.perf_counter() - started
    assert _path_related(connection) == _oracle(connection)
    assert elapsed < 1.0, elapsed


def test_non_string_handover_task_ids_are_neighbours_on_both_sides():
    """`task_ids: [5]` pairs task "5" in `related`; the neighbourhood must see it from both ends."""
    from taskmaster.native import neighbourhood
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(store.SCHEMA_SQL)
        connection.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "test")])
        docs = {("task", "5"): {}, ("task", "T-1"): {}, ("handover", "H-1"): {"task_ids": [5, "T-1"]}}
        for (kind, ident), doc in docs.items():
            connection.execute("INSERT INTO entities VALUES(?,?,NULL,NULL,0,0,?,NULL,1,1)", (kind, ident, json.dumps(dict(doc, id=ident))))
        store.Store.__new__(store.Store)._refresh_derived(SimpleNamespace(connection=connection, _derived_keys=set(docs)))
        connection.row_factory = None
        backfill(connection)
        for ident, other in (("5", "T-1"), ("T-1", "5")):
            assert neighbourhood.neighbours(connection, "task", ident) == _related_neighbours(connection, "task", ident) \
                == Counter({("task", other, "handover"): 1}), ident


def test_unresolved_link_targets_keep_the_task_fallback_on_both_stores():
    """Pinned: an unresolved link target is recorded as kind `task` by legacy and native alike.
    The canonical `target_kind` applies the same precedence and fallback, so no change is answer-compatible."""
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as oracle, closing(sqlite3.connect(":memory:", isolation_level=None)) as native:
        oracle.row_factory = sqlite3.Row
        oracle.executescript(store.SCHEMA_SQL)
        oracle.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "test")])
        doc = {"id": "B-1", "title": "B-1"}
        oracle.execute("INSERT INTO entities VALUES('bug','B-1',NULL,NULL,0,0,?,NULL,1,1)", (json.dumps(doc),))
        oracle.backup(native)
        backfill(native)
        after = dict(doc, links=[{"type": "relates_to", "target": "NOWHERE-1"}])
        oracle.execute("UPDATE entities SET doc=? WHERE kind='bug' AND id='B-1'", (json.dumps(after),))
        store.Store.__new__(store.Store)._refresh_derived(SimpleNamespace(connection=oracle, _derived_keys={("bug", "B-1")}))
        maintain(native, "bug", "B-1", doc, after)
        for connection in (oracle, native):
            rows = [tuple(r) for r in connection.execute("SELECT src_kind,src_id,type,dst_kind,dst_id,derived FROM links ORDER BY derived")]
            assert rows[0] == ("bug", "B-1", "relates_to", "task", "NOWHERE-1", 0), rows
        assert derived(native)["links"] == derived(oracle)["links"]


def test_candidate_queries_use_the_native_partial_indexes():
    from taskmaster.native import neighbourhood as nb
    connection = _paths_connection()
    plans = {sql: " ".join(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, args)) for sql, args in (
        (nb._ROWS + " AND path=?", ("x",)),
        (nb._ROWS + " AND path>=? AND path<?", ("a", "b")),
        (f"SELECT {nb.LITERAL} {nb._GLOBS} AND {nb.LITERAL}<=? ORDER BY {nb.LITERAL} DESC LIMIT 1", ("x",)),
        (f"SELECT kind {nb._GLOBS} AND {nb.LITERAL}=?", ("x",)))}
    assert [("ix_entity_paths_structural" in p, "ix_entity_paths_glob_literal" in p) for p in plans.values()] == \
        [(True, False), (True, False), (False, True), (False, True)], plans


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "unindexed-reader"])
def test_a_nul_in_a_glob_does_not_double_count(indexed):
    """SQL `substr`/`instr` stop at NUL; each claim must still be examined exactly once."""
    connection = _paths_connection(indexed)
    maintain(connection, "task", "A", None, {"id": "A", "anchors": ["\x00q*"]})
    maintain(connection, "task", "B", None, {"id": "B", "anchors": ["*"]})
    assert _path_related(connection) == _oracle(connection) == Counter({(("task", "A"), ("task", "B")): 1})
