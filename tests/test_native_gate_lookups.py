# User intent: per-call path resolution for the routing gate should be cheap, without the
# gate ever answering "legacy" for a store that has become native under an open legacy store.
"""The gate's per-call lookups against an opened legacy store."""
from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster import store
from taskmaster.native_routing import gate


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture
def legacy(tmp_path):
    """An opened legacy store with a connection held on this thread."""
    backlog = tmp_path / "project" / ".taskmaster"
    backlog.mkdir(parents=True)
    (backlog / "backlog.yaml").write_text(yaml.safe_dump({
        "version": 4, "project": "gate", "epics": [], "phases": [],
        "meta": {"schema_version": 4, "projection_schema": store.PROJECTION_SCHEMA},
    }), encoding="utf-8")
    store.open_store(backlog_path=backlog, session="gate-legacy")
    store.load_dict(backlog)
    return backlog, gate.database_path(backlog)


def test_legacy_store_answers_legacy(legacy):
    backlog, _database = legacy
    assert gate.native_database(backlog) is None
    assert gate.native_database(backlog / "backlog.yaml") is None


def test_a_native_marker_committed_elsewhere_is_never_answered_legacy(legacy):
    backlog, database = legacy
    with closing(sqlite3.connect(database, isolation_level=None)) as other:
        other.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    # Not a ready native manifest, so the gate refuses; it must not route to legacy.
    with pytest.raises(gate.NativeUnavailable):
        gate.native_database(backlog)


def test_repeated_resolution_of_an_absolute_path_skips_path_resolve(legacy, monkeypatch):
    backlog, _database = legacy
    for spelling in (backlog, backlog / "backlog.yaml"):
        store.resolve_location(spelling)

    def forbidden(_path: Path):
        raise AssertionError("resolved the path again")
    monkeypatch.setattr(store, "_backlog_dir", forbidden)
    for spelling in (backlog, backlog / "backlog.yaml"):
        assert store.resolve_location(spelling).backlog_path == backlog.resolve()
        assert store.opened_store(spelling) is not None
