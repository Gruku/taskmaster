"""Storage constraints and required capabilities for the staged native core."""
import json
import sqlite3

import pytest

from taskmaster.native import schema
from taskmaster.native.db import probe_capabilities


def test_schema_is_transactional_and_capabilities_are_available():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    capabilities = probe_capabilities(connection)
    assert capabilities["json"] and capabilities["fts5"]
    connection.execute("BEGIN IMMEDIATE")
    schema.create_schema(connection)
    assert connection.in_transaction
    connection.rollback()
    assert not connection.execute("SELECT name FROM sqlite_schema").fetchall()
    connection.close()


def test_identity_constraints_and_reverse_membership_index():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN")
    schema.create_schema(connection)
    connection.execute("INSERT INTO entity_core(kind,public_id,revision,last_seq) VALUES('task','T-1',1,1)")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO entity_core(kind,public_id,revision,last_seq) VALUES('task','T-1',1,1)")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO entity_extensions VALUES(999,'custom','null')")
    plan = connection.execute("EXPLAIN QUERY PLAN SELECT entity_key FROM memberships WHERE target_kind='task' AND target_id='T-1'").fetchall()
    assert any("ix_memberships_target" in row[3] for row in plan)
    connection.close()


def test_field_owners_match_reviewed_inventory():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures/native_contracts.json").read_text(encoding="utf-8"))
    for kind, entry in fixture["field_ownership"].items():
        for field, owner in entry["fields"].items():
            assert schema.owner(kind, field) == owner, (kind, field)
        assert schema.owner(kind, "unknown_future_field") == entry["unknown_field_owner"]
