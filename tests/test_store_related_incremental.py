# User intent: a write on a large backlog must not re-pair every path row in the project,
# yet the `related` table (read by backlog_query and the edit-resurface hook) must stay
# exactly what a full rebuild would produce -- same edges, same weights, no duplicates.
from __future__ import annotations

import fnmatch
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import store  # noqa: E402

RELATED_COLUMNS = "a_kind,a_id,b_kind,b_id,via,weight"


def full_rebuild_oracle(connection: sqlite3.Connection) -> Counter:
    """The 6.0.2 `_rebuild_related`, frozen, as a multiset of `related` rows.

    Kept independent of the store so a change to the store's own full-rebuild
    branch cannot move the reference along with it.
    """
    paths = connection.execute(
        "SELECT kind,id,path,match_kind FROM entity_paths "
        "WHERE source IN ('anchors','location') ORDER BY kind,id,path"
    ).fetchall()
    weights: dict = {}
    for index, left in enumerate(paths):
        left_key = (left[0], left[1])
        for right in paths[index + 1 :]:
            right_key = (right[0], right[1])
            if left_key == right_key:
                continue
            if (
                left[2] == right[2]
                or (left[3] == "glob" and fnmatch.fnmatchcase(right[2], left[2]))
                or (right[3] == "glob" and fnmatch.fnmatchcase(left[2], right[2]))
            ):
                pair = tuple(sorted((left_key, right_key)))
                weights[pair] = weights.get(pair, 0) + 1
    expected = Counter(
        (a[0], a[1], b[0], b[1], "path", weight) for (a, b), weight in weights.items()
    )
    by_handover: dict[str, list[str]] = {}
    for handover_id, task_id in connection.execute(
        "SELECT handover_id,task_id FROM handover_tasks ORDER BY handover_id,task_id"
    ):
        by_handover.setdefault(handover_id, []).append(task_id)
    for task_ids in by_handover.values():
        for index, left in enumerate(task_ids):
            for right in task_ids[index + 1 :]:
                low, high = sorted((left, right))
                expected[("task", low, "task", high, "handover", 1)] += 1
    return expected


def related_rows(connection: sqlite3.Connection) -> Counter:
    return Counter(
        tuple(row) for row in connection.execute(f"SELECT {RELATED_COLUMNS} FROM related")
    )


def describe_difference(expected: Counter, got: Counter) -> str:
    missing = list((expected - got).items())[:5]
    extra = list((got - expected).items())[:5]
    return f"missing={missing} extra={extra}"


# ── Direct: `_rebuild_related` against the frozen full rebuild ──
#
# The table contents are generated rather than derived from documents, so the
# cases reach shapes documents rarely do: globs matching globs, one entity with
# several rows matching the same partner, identical paths from two sources,
# prose rows that must be ignored, and the same id under two kinds.

_EXACT = [
    "src/a.py", "src/b.py", "src/b/c.py", "src/b/d.py", "docs/x.md", "README.md",
    "src/*.py", "src/[ab].py",  # exact rows whose text happens to look like a glob
]
_GLOB = [
    "src/**", "src/*", "src/*.py", "src/?.py", "src/b/*", "*", "**/c.py", "docs/*",
    "src/[ab].py", "src/b/**",
]
_KINDS = ("task", "bug", "issue")
_IDS = tuple(f"E-{n}" for n in range(9))
_TASK_IDS = tuple(f"T-{n}" for n in range(6))
DIRECT_SEEDS = range(600)
DIRECT_STEPS = 12


def _schema_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    store.Store._execute_schema(connection)
    return connection


def _random_path_rows(rng: random.Random, key: tuple[str, str]) -> list[tuple]:
    rows = []
    for _ in range(rng.choice((0, 1, 1, 2, 2, 3, 4))):
        source = rng.choice(("anchors", "anchors", "location", "prose"))
        if source == "anchors" and rng.random() < 0.45:
            rows.append((*key, rng.choice(_GLOB), "glob", source))
        else:
            rows.append((*key, rng.choice(_EXACT), "exact", source))
    if rows and rng.random() < 0.2:
        # The same path reached twice (anchor + location) weighs twice.
        kind, ident, path, match_kind, _ = rows[0]
        rows.append((kind, ident, path, match_kind, "location" if match_kind == "exact" else "anchors"))
    return rows


def _replace_rows(connection: sqlite3.Connection, key: tuple[str, str], rows: list[tuple]) -> None:
    connection.execute("DELETE FROM entity_paths WHERE kind=? AND id=?", key)
    connection.executemany(
        "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)", rows
    )


def _replace_handover(connection: sqlite3.Connection, rng: random.Random, handover_id: str) -> None:
    connection.execute("DELETE FROM handover_tasks WHERE handover_id=?", (handover_id,))
    task_ids = rng.sample(_TASK_IDS, rng.choice((0, 1, 2, 3, 4)))
    connection.executemany(
        "INSERT INTO handover_tasks(handover_id,task_id) VALUES(?,?)",
        [(handover_id, task_id) for task_id in task_ids],
    )


def _run_direct_case(seed: int) -> tuple[int, int]:
    """Returns (incremental refreshes checked, full fallbacks checked)."""
    rng = random.Random(seed)
    connection = _schema_connection()
    keys = [(kind, ident) for kind in _KINDS for ident in _IDS]
    live = rng.sample(keys, rng.randint(4, 14))
    for key in live:
        _replace_rows(connection, key, _random_path_rows(rng, key))
    for handover in ("H-0", "H-1", "H-2"):
        _replace_handover(connection, rng, handover)
    store.Store._rebuild_related(connection)
    assert related_rows(connection) == full_rebuild_oracle(connection), f"seed {seed} initial"
    incremental = fallback = 0
    for step in range(DIRECT_STEPS):
        if rng.random() < 0.12:
            touched = set(rng.sample(keys, rng.randint(len(keys) // 2, len(keys))))
        else:
            touched = set(rng.sample(keys, rng.choice((1, 1, 1, 2, 2, 3))))
        for key in touched:
            roll = rng.random()
            if roll < 0.2:
                _replace_rows(connection, key, [])  # delete / never had paths
            elif roll < 0.3:
                pass  # touched, paths unchanged
            else:
                _replace_rows(connection, key, _random_path_rows(rng, key))
        if rng.random() < 0.3:
            handover = rng.choice(("H-0", "H-1", "H-2", "H-3"))
            _replace_handover(connection, rng, handover)
            touched.add(("handover", handover))
        path_count = connection.execute(
            "SELECT COUNT(*) FROM entity_paths WHERE source IN ('anchors','location')"
        ).fetchone()[0]
        touched_count = sum(
            connection.execute(
                "SELECT COUNT(*) FROM entity_paths WHERE kind=? AND id=? "
                "AND source IN ('anchors','location')",
                key,
            ).fetchone()[0]
            for key in touched
        )
        if 2 * touched_count >= path_count:
            fallback += 1
        else:
            incremental += 1
        store.Store._rebuild_related(connection, touched)
        expected = full_rebuild_oracle(connection)
        got = related_rows(connection)
        assert got == expected, (
            f"seed {seed} step {step} touched {sorted(touched)}: "
            + describe_difference(expected, got)
        )
    return incremental, fallback


def test_incremental_related_matches_full_rebuild_over_seeded_edit_sequences():
    incremental = fallback = 0
    for seed in DIRECT_SEEDS:
        done, full = _run_direct_case(seed)
        incremental += done
        fallback += full
    # Both branches must actually be exercised, or equivalence proves nothing.
    assert incremental > 5 * fallback > 0, (incremental, fallback)


def test_a_touched_key_leaves_no_stale_edge_on_its_partner_side():
    """The edge is stored once, sorted, so the touched key may sit in either column."""
    connection = _schema_connection()
    early, late = ("bug", "A-1"), ("task", "Z-1")
    _replace_rows(connection, early, [(*early, "src/a.py", "exact", "location")])
    _replace_rows(connection, late, [(*late, "src/a.py", "exact", "anchors")])
    store.Store._rebuild_related(connection)
    filler = [(("note", f"N-{n}"), f"docs/{n}.md") for n in range(8)]
    for key, path in filler:
        _replace_rows(connection, key, [(*key, path, "exact", "anchors")])
    store.Store._rebuild_related(connection, {key for key, _ in filler})
    for touched in (early, late):
        _replace_rows(connection, touched, [(*touched, "docs/elsewhere.md", "exact", "anchors")])
        store.Store._rebuild_related(connection, {touched})
        assert related_rows(connection) == full_rebuild_oracle(connection), touched
        _replace_rows(connection, touched, [(*touched, "src/a.py", "exact", "anchors")])
        store.Store._rebuild_related(connection, {touched})
        assert related_rows(connection) == full_rebuild_oracle(connection), touched


def test_two_touched_keys_that_match_each_other_weigh_each_row_pair_once():
    connection = _schema_connection()
    left, right = ("task", "L-1"), ("task", "R-1")
    filler = [(("note", f"N-{n}"), f"docs/{n}.md") for n in range(12)]
    for key, path in filler:
        _replace_rows(connection, key, [(*key, path, "exact", "anchors")])
    store.Store._rebuild_related(connection)
    _replace_rows(connection, left, [
        (*left, "src/a.py", "exact", "anchors"), (*left, "src/**", "glob", "anchors"),
    ])
    _replace_rows(connection, right, [
        (*right, "src/a.py", "exact", "location"), (*right, "src/*", "glob", "anchors"),
    ])
    store.Store._rebuild_related(connection, {left, right})
    assert related_rows(connection) == full_rebuild_oracle(connection)
    # exact==exact, src/** covers src/a.py, src/** covers src/*, src/* covers src/a.py
    assert related_rows(connection)[("task", "L-1", "task", "R-1", "path", 4)] == 1


# ── Latency guard ──


def test_one_touched_key_does_not_compare_every_pair_of_path_rows(monkeypatch):
    """CodeMaestro's 3,857 path rows made every write run 2.2M fnmatch calls.

    Counted, not timed: a refresh for one entity must scale with the row count,
    never with its square.
    """
    connection = _schema_connection()
    rows = []
    for n in range(2000):
        key = ("task", f"T-{n}")
        rows.append((*key, f"src/m{n % 97}/f{n}.py", "exact", "anchors"))
        rows.append((*key, f"src/m{n % 89}/**", "glob", "anchors"))
    connection.executemany(
        "INSERT INTO entity_paths(kind,id,path,match_kind,source) VALUES(?,?,?,?,?)", rows
    )
    store.Store._rebuild_related(connection)
    calls = 0
    real = fnmatch.fnmatchcase

    def counting(name, pattern):
        nonlocal calls
        calls += 1
        return real(name, pattern)

    monkeypatch.setattr(fnmatch, "fnmatchcase", counting)
    key = ("task", "T-7")
    _replace_rows(connection, key, [
        (*key, "src/m3/new.py", "exact", "anchors"), (*key, "src/m5/**", "glob", "anchors"),
    ])
    store.Store._rebuild_related(connection, {key})
    assert calls <= 4 * len(rows), calls
    monkeypatch.setattr(fnmatch, "fnmatchcase", real)
    assert related_rows(connection) == full_rebuild_oracle(connection)


# ── End to end: real transactions, real documents ──

_ANCHORS = ("src/a.py", "src/", "src/*.py", "src/b/", "src/b/c.py", "docs/*", "src/?.py", "lib.py")
_LOCATIONS = ("src/a.py:10", "src/b/c.py", "docs/x.md:3", "lib.py", "api/src/a.py")
E2E_SEEDS = range(24)
E2E_TRANSACTIONS = 14


def _random_bug_paths(rng: random.Random, doc: dict) -> None:
    doc["anchors"] = rng.sample(_ANCHORS, rng.choice((0, 1, 1, 2, 3)))
    doc["location"] = rng.sample(_LOCATIONS, rng.choice((0, 1, 1, 2)))
    if rng.random() < 0.25:
        doc["sub_repo"] = "api"
    else:
        doc.pop("sub_repo", None)


@pytest.mark.parametrize("seed", list(E2E_SEEDS))
def test_related_after_real_writes_matches_the_full_rebuild(tmp_taskmaster, seed):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bs._load()
    rng = random.Random(seed)
    live: set[str] = set()
    archived: set[str] = set()
    handovers: list[str] = []
    counter = 0
    for step in range(E2E_TRANSACTIONS):
        with store.transaction(tool=f"related-e2e-{seed}-{step}") as tx:
            for _ in range(rng.choice((1, 1, 1, 2, 3))):
                roll = rng.random()
                if roll < 0.3 or len(live) < 2:
                    counter += 1
                    doc = {"id": f"B-{counter:03d}", "title": f"bug {counter}", "status": "open"}
                    _random_bug_paths(rng, doc)
                    live.add(tx.create("bug", doc, body="", requested_id=doc["id"]))
                elif roll < 0.6:
                    ident = rng.choice(sorted(live))
                    doc = tx.get("bug", ident)
                    _random_bug_paths(rng, doc)
                    tx.put("bug", ident, doc)
                elif roll < 0.7:
                    ident = rng.choice(sorted(live))
                    if ident in archived:
                        tx.unarchive("bug", ident)
                        archived.discard(ident)
                    else:
                        tx.archive("bug", ident)
                        archived.add(ident)
                elif roll < 0.8:
                    ident = rng.choice(sorted(live))
                    tx.delete("bug", ident)
                    live.discard(ident)
                    archived.discard(ident)
                elif roll < 0.9 or not handovers:
                    doc = {
                        "id": f"2026-09-{len(handovers) + 1:02d}-h",
                        "date": "2026-09-17",
                        "tldr": "handover",
                        "next_action": "none",
                        "status": "open",
                        "task_ids": rng.sample(_TASK_IDS, rng.choice((0, 2, 3))),
                    }
                    handovers.append(tx.create("handover", doc, body=""))
                else:
                    ident = rng.choice(handovers)
                    doc = tx.get("handover", ident)
                    doc["task_ids"] = rng.sample(_TASK_IDS, rng.choice((0, 1, 2, 4)))
                    tx.put("handover", ident, doc)
        connection = bs._store().connection
        expected = full_rebuild_oracle(connection)
        got = related_rows(connection)
        assert got == expected, f"seed {seed} step {step}: " + describe_difference(expected, got)
    connection = bs._store().connection
    before = related_rows(connection)
    assert sum(before.values()) > 0, f"seed {seed} produced no edges to compare"
    bs._store().rebuild_derived()
    assert related_rows(bs._store().connection) == before, f"seed {seed} after rebuild_derived"
