"""Selective relation updates match the legacy full-rebuild oracle exactly."""
from collections import Counter
from contextlib import closing
import json
import random
import sqlite3
from types import SimpleNamespace

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
                         links=[{"type": rng.choice(["relates_to", "depends_on", "blocks"]), "target": rng.choice(["T-0", "T-1", "missing"])}])
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
