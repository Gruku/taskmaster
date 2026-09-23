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
    with twins.at(twins.legacy):
        rebuilt = bs.backlog_index_status(rebuild=True)
        refused = bs.backlog_index_status(verify=True)
    assert rebuilt.startswith("Store: ") and "Graph check" not in rebuilt, rebuilt
    assert refused.startswith("Error: ") and "rebuild=True" in refused, refused
