"""A future authoritative schema must never be rebuilt by an older client."""
from contextlib import closing
import sqlite3
import importlib.util
from pathlib import Path

import pytest

from taskmaster import store
from native_oracles import projection_hashes


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("operation", ["open", "read", "write", "derived", "connection", "queue", "config", "cache", "diagnostic"])
@pytest.mark.parametrize("marker,value", [
    ("schema_version", "999"), ("minimum_client_protocol", "999"),
    ("migration_state", "migrating"), ("schema_version", "not-an-integer"),
])
def test_future_or_migrating_store_refused_without_mutation(tmp_path, warm, operation, marker, value):
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    (tm / "backlog.yaml").write_text(
        "version: 4\nmeta: {schema_version: 4}\nepics: []\nphases: []\n", encoding="utf-8")
    opened = store.open_store(backlog_path=tm)
    if not warm:
        store.reset_for_tests()
    with closing(sqlite3.connect(opened.db_path, isolation_level=None)) as peer:
        peer.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (marker, value))
        peer.execute("CREATE TABLE future_only(payload TEXT)")
        peer.execute("INSERT INTO future_only VALUES('unprojected committed state')")
        before = list(peer.iterdump())
        files = projection_hashes(tm)
        reservations = (tm / "local/id-reservations.json").read_bytes()
        with pytest.raises(RuntimeError, match="(?i)unsupported|migration"):
            if operation == "open":
                store.open_store(backlog_path=tm)
            elif operation == "read":
                opened.load_dict()
            elif operation == "write":
                with opened.transaction(tool="must-not-enter"):
                    pytest.fail("Unsupported transaction admitted")
            elif operation == "derived":
                opened.rebuild_derived()
            elif operation == "queue":
                opened.linear_rows()
            elif operation == "config":
                opened.update_root_config("linear.yaml", lambda _: {"changed": True})
            elif operation == "cache":
                opened.write_local_cache("meta.json", b"changed")
            elif operation == "diagnostic":
                opened.read_only_status()
            else:
                opened.connection
        assert list(peer.iterdump()) == before
        assert projection_hashes(tm) == files
        assert (tm / "local/id-reservations.json").read_bytes() == reservations
        assert not list(tm.glob("local/store.db.corrupt*"))


def test_writer_lock_rechecks_fence_on_previously_admitted_connection(tmp_path):
    from taskmaster.admission import assert_compatible
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    (tm / "backlog.yaml").write_text(
        "version: 4\nmeta: {schema_version: 4}\nepics: []\nphases: []\n", encoding="utf-8")
    opened = store.open_store(backlog_path=tm)
    connection = opened.connection
    assert_compatible(connection)
    with closing(sqlite3.connect(opened.db_path, isolation_level=None)) as peer:
        peer.execute("INSERT INTO meta VALUES('migration_state','migrating')")
    with pytest.raises(RuntimeError, match="migration"):
        opened._begin_immediate(connection)
    assert not connection.in_transaction


@pytest.mark.parametrize("hook", ["merge_gate_decide", "edit_resurface"])
def test_system_hooks_refuse_unknown_schema_before_entity_queries(tmp_path, hook):
    filename = Path(__file__).resolve().parents[1] / "hooks" / (hook + ".py")
    spec = importlib.util.spec_from_file_location("admission_" + hook, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.executescript("CREATE TABLE meta(key PRIMARY KEY,value);"
            "INSERT INTO meta VALUES('schema_version','999');")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Unsupported"):
        module._connect_ro(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("entry", ["status", "note", "query", "search", "viewer", "linear"])
def test_public_entrypoints_cannot_write_future_store(tmp_taskmaster, entry):
    from taskmaster import backlog_server as server
    opened = store.open_store(backlog_path=tmp_taskmaster / ".taskmaster")
    with closing(sqlite3.connect(opened.db_path, isolation_level=None)) as peer:
        peer.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        before = list(peer.iterdump())
        functions = {
            "status": lambda: server.backlog_status(),
            "note": lambda: server.backlog_note_create(text="must not persist"),
            "query": lambda: server.backlog_query(sql="SELECT * FROM entities"),
            "search": lambda: server.backlog_search(query="task"),
            "viewer": lambda: server._load_task_full_identified("core-001"),
            "linear": lambda: opened.linear_claim(owner="future-client"),
        }
        try:
            result = functions[entry]()
        except RuntimeError as exc:
            assert "Unsupported" in str(exc)
        else:
            assert "Unsupported" in str(result)
        assert list(peer.iterdump()) == before
