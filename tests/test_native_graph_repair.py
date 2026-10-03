"""User intent: an operator can check a native store's graph SQL tables against the
exact full oracle and repair any drift through one explicit maintenance operation,
which reports its cost and never runs on a normal command or read (N14 step 6).
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import graph_repair, relations
from taskmaster.native.queries import Repository
from native_twins import make_twins, native_connection

TABLES = graph_repair.TABLES


def _seed():
    bs.backlog_add_task(title="Alpha", epic="test-epic", phase="dev", options={"anchors": "src/a.py,src/*.py"})
    bs.backlog_add_task(title="Beta", epic="test-epic", phase="dev", depends_on="test-epic-001",
                        options={"anchors": "src/a.py,docs/[ab].md"})
    bs.backlog_add_task(title="Gamma", epic="test-epic", phase="dev", options={"anchors": "docs/a.md,SRC/a.py"})
    bs.backlog_issue_create(title="Seeded issue", severity="P2", evidence="see `src/b.py`", location=["src/a.py"],
                            related_tasks=["test-epic-001"])
    bs.backlog_handover_create(tldr="First handover", task_ids=["test-epic-001", "test-epic-002", "test-epic-003"])
    bs.backlog_handover_create(tldr="Second handover", task_ids=["test-epic-001", "test-epic-002"])
    bs.backlog_idea_create(title="Seeded idea", body="about ISS-001")
    linked = bs.backlog_link(action="create", source="ISS-001", target="IDEA-001", type="relates_to")
    assert "error" not in linked.lower(), linked


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _actual(connection):
    return {table: Counter(tuple(row) for row in connection.execute(
        f"SELECT {','.join(graph_repair.COLUMNS[table])} FROM {table}")) for table in TABLES}


def _verify(root):
    with native_connection(root) as connection:
        with Repository(connection).snapshot() as snapshot:
            return graph_repair.verify(snapshot)


def _oracle(root):
    with native_connection(root) as connection:
        with Repository(connection).snapshot() as snapshot:
            return graph_repair.expected(snapshot)


def _tables(root):
    with native_connection(root) as connection:
        return _actual(connection)


def test_the_oracle_equals_the_legacy_full_rebuild(twins):
    """The repair oracle is the legacy full rebuild, computed from native documents."""
    with twins.at(twins.legacy):
        bs.backlog_index_status(rebuild=True)
    with closing(sqlite3.connect(twins.legacy / ".taskmaster" / "local" / "store.db")) as connection:
        legacy = _actual(connection)
    oracle = _oracle(twins.native)
    assert oracle == legacy
    assert oracle["related"] and oracle["links"] and oracle["handover_tasks"] and oracle["entity_paths"]
    assert any(row[4] == "handover" for row in oracle["related"])
    assert any(row[5] == 1 for row in oracle["links"]), "derived mirrors are part of the oracle"


def test_a_healthy_native_store_verifies_clean(twins):
    report = _verify(twins.native)
    assert report["clean"] and not report["repaired"]
    assert all(report["tables"][t]["missing"] == report["tables"][t]["spurious"] == 0 for t in TABLES)
    assert report["rows_compared"] >= sum(sum(c.values()) for c in _tables(twins.native).values())
    assert report["seconds"] >= 0 and report["entities"] > 0
    with twins.at(twins.native):
        text = bs.backlog_index_status(verify=True)
    assert "Graph check: clean" in text, text


def _corrupt(root):
    with native_connection(root) as connection:
        # related: one row deleted, one spurious, one wrong weight.
        path_rows = connection.execute(
            "SELECT rowid,* FROM related WHERE via='path' ORDER BY a_id,b_id").fetchall()
        assert len(path_rows) >= 2
        connection.execute("DELETE FROM related WHERE rowid=?", (path_rows[0][0],))
        connection.execute("UPDATE related SET weight=weight+5 WHERE rowid=?", (path_rows[1][0],))
        connection.execute("INSERT INTO related VALUES('task','test-epic-003','task','ghost','path',1)")
        # links: a derived mirror deleted, a spurious declared link added.
        mirror = connection.execute("SELECT rowid FROM links WHERE derived=1 LIMIT 1").fetchone()
        connection.execute("DELETE FROM links WHERE rowid=?", mirror)
        connection.execute("INSERT INTO links VALUES('task','test-epic-001','blocks','task','ghost',0)")
        # handover_tasks: a membership deleted, a spurious one added.
        member = connection.execute("SELECT rowid FROM handover_tasks LIMIT 1").fetchone()
        connection.execute("DELETE FROM handover_tasks WHERE rowid=?", member)
        connection.execute("INSERT INTO handover_tasks VALUES('ghost-handover','test-epic-001')")
        # entity_paths: a spurious claim.
        connection.execute("INSERT INTO entity_paths VALUES('task','test-epic-001','ghost/*','glob','anchors')")


def test_verify_reports_each_corruption_and_changes_nothing(twins):
    healthy = _tables(twins.native)
    _corrupt(twins.native)
    corrupted = _tables(twins.native)
    report = _verify(twins.native)
    assert not report["clean"] and not report["repaired"]
    tables = report["tables"]
    assert (tables["related"]["missing"], tables["related"]["spurious"]) == (2, 2)
    assert (tables["links"]["missing"], tables["links"]["spurious"]) == (1, 1)
    assert (tables["handover_tasks"]["missing"], tables["handover_tasks"]["spurious"]) == (1, 1)
    assert (tables["entity_paths"]["missing"], tables["entity_paths"]["spurious"]) == (0, 1)
    assert ["task", "test-epic-003", "task", "ghost", "path", 1] in tables["related"]["examples"]["spurious"]
    assert _tables(twins.native) == corrupted != healthy
    with twins.at(twins.native):
        text = bs.backlog_index_status(verify=True)
    assert "Graph check: 9 differences" in text and "not repaired" in text, text
    assert _tables(twins.native) == corrupted


def test_repair_restores_exact_oracle_equality_and_is_idempotent(twins):
    healthy = _tables(twins.native)
    _corrupt(twins.native)
    with twins.at(twins.native):
        text = bs.backlog_index_status(rebuild=True)
    assert "Graph check: 9 differences, repaired" in text, text
    assert "Rebuilt: never" not in text, text
    repaired = _tables(twins.native)
    assert repaired == _oracle(twins.native) == healthy
    assert _verify(twins.native)["clean"]
    with native_connection(twins.native) as connection:
        events = connection.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0]
    with twins.at(twins.native):
        again = bs.backlog_index_status(rebuild=True)
    assert "Graph check: clean" in again, again
    assert _tables(twins.native) == repaired
    with native_connection(twins.native) as connection:
        # A repair changes derived rows only: it is not a domain change.
        assert connection.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0] == events


def test_repair_is_one_atomic_transaction(twins, monkeypatch):
    _corrupt(twins.native)
    corrupted = _tables(twins.native)
    real = graph_repair._insert

    def fail_after_first(connection, table, row, count):
        real(connection, table, row, count)
        raise ValueError("crash mid-repair")
    monkeypatch.setattr(graph_repair, "_insert", fail_after_first)
    with twins.at(twins.native):
        text = bs.backlog_index_status(rebuild=True)
    assert text.startswith("Error"), text
    assert _tables(twins.native) == corrupted


def test_repair_is_admitted_as_its_own_command_only():
    from taskmaster.native import contracts
    contracts.validate_operation("graph.repair", {})
    with pytest.raises(ValueError):
        contracts.validate_operation("graph.repair", {"tables": ["related"]})
    with pytest.raises(ValueError):
        contracts.validate_operation("batch", {"commands": [{"operation": "graph.repair", "arguments": {}}]})


def test_normal_commands_and_reads_never_run_the_oracle(twins, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the full graph oracle ran on a normal path")
    for name in ("expected", "verify", "repair"):
        monkeypatch.setattr(graph_repair, name, forbidden)
    monkeypatch.setattr(relations, "grouped_weights", forbidden)
    with twins.at(twins.native):
        answers = [
            bs.backlog_add_task(title="Delta", epic="test-epic", phase="dev", options={"anchors": "src/*"}),
            bs.backlog_update_task(task_id="test-epic-001", field="anchors", value="lib/x.py"),
            bs.backlog_handover_create(tldr="Third handover", task_ids=["test-epic-003", "test-epic-004"]),
            bs.backlog_link(action="remove", source="ISS-001", target="IDEA-001"),
            bs.backlog_get_task(task_id="test-epic-001"),
            bs.backlog_status(),
            bs.backlog_query(sql="SELECT COUNT(*) FROM related"),
            bs.backlog_index_status(),
        ]
    assert not [a for a in answers if str(a).startswith("Error")], answers


def test_normal_commands_keep_the_tables_equal_to_the_oracle(twins):
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-002", field="anchors", value="src/*")
        bs.backlog_handover_create(tldr="Third handover", task_ids=["test-epic-003", "test-epic-003"])
    assert _verify(twins.native)["clean"]


def test_legacy_rebuild_is_unchanged_and_verify_is_native_only(twins):
    """A legacy store has no verifier: `verify` reports the counts, says nothing was
    compared, and changes nothing, with or without `rebuild`."""
    with twins.at(twins.legacy):
        rebuilt = bs.backlog_index_status(rebuild=True)
        before = _legacy_links(twins.legacy)
        answers = [bs.backlog_index_status(verify=True), bs.backlog_index_status(rebuild=True, verify=True)]
    assert rebuilt.startswith("Store: ") and "Graph check" not in rebuilt, rebuilt
    for answer in answers:
        assert answer.startswith("Store: ") and "not compared" in answer and "Nothing was changed" in answer, answer
        assert "rebuild=True" in answer and "Graph check" not in answer, answer
    assert _legacy_links(twins.legacy) == before


def _legacy_links(root):
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        return sorted(connection.execute("SELECT * FROM links").fetchall())


# -- Link-before-target (review finding 1) --


def test_links_written_before_their_target_exists_match_the_full_rebuild_at_commit(twins):
    """A link recorded while its target was missing takes the target's kind when the
    target is created, on both stores, so incremental rows equal `rebuild_derived`."""
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            assert "Error" not in bs.backlog_idea_create(
                title="Early idea", related_tasks=["ISS-002", "B-001", "ISS-999"])
            assert "Error" not in bs.backlog_issue_create(title="Later issue", severity="P2", evidence="x")
            assert "Error" not in bs.backlog_bug_create(title="Later bug", severity="P2")
    incremental = _legacy_links(twins.legacy)
    assert incremental == _legacy_links(twins.native), "legacy and native rows diverged"
    assert _verify(twins.native)["clean"]
    with twins.at(twins.legacy):
        bs.backlog_index_status(rebuild=True)
    assert _legacy_links(twins.legacy) == incremental, "legacy incremental rows differ from rebuild_derived"
    kinds = {row[4]: row[3] for row in incremental if row[1] == "IDEA-002" and row[5] == 0}
    assert kinds == {"ISS-002": "issue", "B-001": "bug", "ISS-999": "task"}, kinds
    mirrors = {(row[0], row[1]) for row in incremental if row[5] == 1 and row[4] == "IDEA-002"}
    assert ("issue", "ISS-002") in mirrors and ("bug", "B-001") in mirrors
    assert not [row for row in incremental if row[5] == 1 and row[4] == "IDEA-002" and row[0] == "task"
                and row[1] in ("ISS-002", "B-001")], "a mirror under the fallback kind survived"


def test_an_unresolved_link_target_keeps_the_task_fallback_on_both_stores(twins):
    """Step 2's promise: a target that never exists is recorded as `task`, the legacy
    answer, and the full oracle agrees."""
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            assert "Error" not in bs.backlog_idea_create(title="Orphan idea", related_tasks=["ISS-404"])
    for root in (twins.legacy, twins.native):
        rows = [row for row in _legacy_links(root) if row[4] == "ISS-404"]
        assert rows and all(row[3] == "task" for row in rows), rows
    assert _legacy_links(twins.legacy) == _legacy_links(twins.native)
    assert _verify(twins.native)["clean"]


def test_link_before_a_task_target(twins):
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            assert "Error" not in bs.backlog_idea_create(title="Task idea", related_tasks=["test-epic-004"])
            assert "Error" not in bs.backlog_add_task(title="Delta", epic="test-epic", phase="dev")
    assert _legacy_links(twins.legacy) == _legacy_links(twins.native)
    assert _verify(twins.native)["clean"]


# -- Backfill repair (review finding 2) --


def test_activation_repairs_graph_drift_inherited_from_the_legacy_store(tmp_path, monkeypatch):
    from native_twins import native_database

    def drifted():
        _seed()
        from taskmaster import store
        store.reset_for_tests()
        with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db", isolation_level=None)) as c:
            c.execute("DELETE FROM related WHERE rowid IN (SELECT rowid FROM related LIMIT 2)")
            c.execute("INSERT INTO links VALUES('task','test-epic-001','blocks','task','ghost',0)")
            c.execute("INSERT INTO handover_tasks VALUES('ghost-handover','test-epic-001')")
        store.reset_for_tests()
    twins = make_twins(tmp_path, monkeypatch, drifted)
    assert _verify(twins.native)["clean"]
    with closing(sqlite3.connect(native_database(twins.native))) as connection:
        assert connection.execute("SELECT value FROM native_manifest WHERE key='graph_repaired_at'").fetchone()


def _legacy_db(path, *, drift=False):
    import json
    from taskmaster import store
    connection = sqlite3.connect(path, isolation_level=None)
    connection.executescript(store.SCHEMA_SQL)
    connection.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "t")])
    connection.execute("INSERT INTO entities VALUES('task','T-1',NULL,NULL,0,0,?,NULL,1,1)",
                       (json.dumps({"id": "T-1", "anchors": ["src/a.py"]}),))
    connection.execute("INSERT INTO entities VALUES('task','T-2',NULL,NULL,0,0,?,NULL,1,1)",
                       (json.dumps({"id": "T-2", "anchors": ["src/*"]}),))
    if drift:
        connection.execute("INSERT INTO links VALUES('task','T-1','blocks','task','ghost',0)")
    return connection


def _raw_graph(connection):
    return {table: connection.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid").fetchall() for table in TABLES}


def _begin_activation(connection):
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")


def test_backfill_on_a_legacy_authority_store_leaves_the_graph_tables_byte_identical(tmp_path):
    """Backfill is a repeatable staging step while legacy owns these rows: it must not
    rewrite them (review round 2, finding 2). The repair belongs to activation."""
    from taskmaster.native.migrate import backfill
    with closing(_legacy_db(tmp_path / "s.db", drift=True)) as connection:
        before = _raw_graph(connection)
        result = backfill(connection)
        again = backfill(connection)
        assert _raw_graph(connection) == before
        assert "graph_repair" not in result and "graph_repair" not in again
        assert connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0] == "legacy"


def test_the_activation_repair_runs_only_as_native_becomes_the_authority(tmp_path):
    from taskmaster.admission import UnsupportedStoreError
    from taskmaster.native import migrate
    with closing(_legacy_db(tmp_path / "s.db", drift=True)) as connection:
        migrate.backfill(connection)
        with pytest.raises(RuntimeError):
            migrate.repair_graph_for_activation(connection)
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(UnsupportedStoreError):
            migrate.repair_graph_for_activation(connection)
        connection.rollback()
        _begin_activation(connection)
        report = migrate.repair_graph_for_activation(connection)
        connection.commit()
        # No derived rows existed: two entity_paths rows and one related row were
        # missing, and the injected link was spurious.
        assert report["repaired"] and report["differences"] == 4, report
        assert report["rows_compared"] > 0 and report["seconds"] >= 0
        assert _actual(connection)["related"] == Counter({("task", "T-1", "task", "T-2", "path", 1): 1})


def test_a_crashed_activation_repair_rolls_back_and_resumes(tmp_path, monkeypatch):
    from taskmaster.native import migrate
    with closing(_legacy_db(tmp_path / "s.db", drift=True)) as connection:
        migrate.backfill(connection)
        before = _raw_graph(connection)

        def crash(*args, **kwargs):
            raise RuntimeError("crash mid-activation")
        with monkeypatch.context() as local:
            local.setattr(graph_repair, "_insert", crash)
            _begin_activation(connection)
            with pytest.raises(RuntimeError):
                migrate.repair_graph_for_activation(connection)
            connection.rollback()
        assert _raw_graph(connection) == before
        assert connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0] == "legacy"
        _begin_activation(connection)
        assert migrate.repair_graph_for_activation(connection)["repaired"]
        connection.commit()


# -- Tie-break between kinds sharing an id (review round 2, finding 1) --


@pytest.mark.parametrize("first", ["phase", "epic"])
def test_an_id_that_is_both_an_epic_and_a_phase_resolves_alike_on_both_stores(twins, first):
    make = {"phase": lambda: bs.backlog_add_phase(phase_id="shared", name="Shared"),
            "epic": lambda: bs.backlog_add_epic(epic_id="shared", name="Shared", done_when="x")}
    order = [first, "epic" if first == "phase" else "phase"]
    for root in (twins.legacy, twins.native):
        with twins.at(root):
            assert "Error" not in bs.backlog_idea_create(title="Before", related_tasks=["shared"])
            for kind in order:
                make[kind]()
            assert "Error" not in bs.backlog_idea_create(title="After", related_tasks=["shared"])
    incremental = _legacy_links(twins.legacy)
    assert incremental == _legacy_links(twins.native), "legacy and native rows diverged"
    assert {row[3] for row in incremental if row[4] == "shared" and row[5] == 0} == {"epic"}
    assert _verify(twins.native)["clean"]
    with twins.at(twins.legacy):
        bs.backlog_index_status(rebuild=True)
    assert _legacy_links(twins.legacy) == incremental


def test_one_batch_creating_entities_that_link_to_each_other_verifies_clean(twins):
    from native_twins import commit_only
    with native_connection(twins.native) as connection:
        commit_only(connection, "batch", {"commands": [
            {"operation": "idea.create", "arguments": {"title": "one", "related_tasks": ["IDEA-003", "ISS-002"]}},
            {"operation": "issue.create", "arguments": {"title": "iss", "severity": "P2", "evidence": "x",
                                                        "related_tasks": ["IDEA-003", "ISS-003"]}},
            {"operation": "idea.create", "arguments": {"title": "two", "related_tasks": ["IDEA-002"]}},
            {"operation": "issue.create", "arguments": {"title": "iss2", "severity": "P2", "evidence": "x"}},
        ]})
    assert _verify(twins.native)["clean"]


# -- Indexed kind resolution (review round 2, finding 3) --


def _plans(connection, call):
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        call()
    finally:
        connection.set_trace_callback(None)
    return [" ".join(str(row[-1]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
            for sql in statements if sql.lstrip().upper().startswith("SELECT")]


def test_kind_resolution_and_the_incoming_link_lookup_are_indexed(twins):
    from taskmaster import store
    from taskmaster.taskmaster_v3 import LINK_ENDPOINT_KINDS, LINKS_TO_ID_SQL
    with twins.at(twins.legacy):
        bs.backlog_status()   # opens the legacy store, which creates its indexes
    for root, resolve, index in ((twins.legacy, store.Store._kind_for_id, "ix_entities_id"),
                                 (twins.native, relations._kind_for_id, "ix_entity_core_public_id")):
        with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
            plans = _plans(connection, lambda: resolve(connection, "test-epic-001"))
            assert plans and all(index in plan and "SCAN" not in plan for plan in plans), (root.name, plans)
            plan = " ".join(str(row[-1]) for row in connection.execute(
                "EXPLAIN QUERY PLAN " + LINKS_TO_ID_SQL, (*LINK_ENDPOINT_KINDS, "x")))
            assert "ix_links_dst" in plan and "SCAN" not in plan, plan


# -- Hook revision and the repair stamp (review finding 3) --


def _hook_revision(root):
    from taskmaster.native_routing import hook_reads
    with native_connection(root) as connection:
        return hook_reads.revision(connection)


def _stamp(root):
    with native_connection(root) as connection:
        row = connection.execute("SELECT value FROM native_manifest WHERE key='graph_repaired_at'").fetchone()
        return row[0] if row else None


def test_a_row_changing_repair_moves_the_hook_revision(twins):
    before, stamp = _hook_revision(twins.native), _stamp(twins.native)
    _corrupt(twins.native)
    with twins.at(twins.native):
        bs.backlog_index_status(rebuild=True)
    assert _hook_revision(twins.native) > before
    assert _stamp(twins.native) != stamp


def test_a_clean_repair_neither_stamps_nor_moves_the_hook_revision(twins):
    before, stamp = _hook_revision(twins.native), _stamp(twins.native)
    with twins.at(twins.native):
        text = bs.backlog_index_status(rebuild=True)
    assert "Graph check: clean" in text
    assert _hook_revision(twins.native) == before
    assert _stamp(twins.native) == stamp
    # The report still shows when this check ran (review round 2, finding 4).
    with native_connection(twins.native) as connection:
        checked = connection.execute("SELECT value FROM native_manifest WHERE key='graph_checked_at'").fetchone()
    assert checked and f"Rebuilt: {checked[0]}" in text, text
