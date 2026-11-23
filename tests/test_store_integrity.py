# User intent: pin team-server-029 - a served store gets one full integrity_check after the B-105 fix, the verdict is kept in a
# marker file beside the store and shown by backlog_store_status, a failed verdict re-checks each start, and deep=True is operator-only on a team server.
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store_integrity as si
from taskmaster.native.claims import Requester
from test_native_service_build import root  # noqa: F401

posix_only = pytest.mark.skipif(os.name == "nt", reason="the startup check is POSIX-only by design")


@pytest.fixture
def db(root):  # noqa: F811
    return root / ".taskmaster/local/store.db"


def corrupt_copy(db, tmp_path):
    """A copy of the store whose first index root page is overwritten with zeros."""
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page = connection.execute("PRAGMA page_size").fetchone()[0]
        index_root = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE type='index' AND rootpage>1 AND sql IS NOT NULL ORDER BY rootpage"
        ).fetchone()[0]
    copy = tmp_path / "damaged.db"
    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(copy)) as target:
        source.backup(target)       # not a plain file copy: this process holds SQLite connections (B-105 pattern)
    with open(copy, "r+b") as handle:
        handle.seek((index_root - 1) * page)
        handle.write(b"\0" * page)
    return copy


def test_a_clean_store_records_ok_and_the_line_says_so(db):
    assert si.read_marker(db) is None and "not yet checked" in si.line(None)
    record = si.run_check(db)
    assert record["result"] == "ok" and record["fix"] == "b105"
    stored = si.read_marker(db)
    assert stored["result"] == "ok" and stored["store_id"] and stored["seq"] >= 0 and stored["ino"] and si.line(stored).startswith("Integrity: ok (full check ")


def test_a_damaged_store_records_failed_with_the_first_rows(db, tmp_path):
    damaged = corrupt_copy(db, tmp_path)
    record = si.run_check(damaged)
    assert record["result"] == "failed" and record["detail"]
    assert "FAILED" in si.line(record)


def test_a_verdict_recorded_for_another_store_is_ignored(db):
    si.run_check(db)
    marker = si.marker_path(db)
    marker.write_text(marker.read_text(encoding="utf-8").replace(si.read_marker(db)["store_id"], "another-store"),
                      encoding="utf-8")
    assert si.read_marker(db) is None


def _add_event(db):
    with closing(sqlite3.connect(db, isolation_level=None)) as connection:
        connection.execute("INSERT INTO domain_events(seq,ts,session,tool,kind,id,op,fields,before,after) "
                           "SELECT MAX(seq)+1,ts,session,tool,kind,id,op,fields,before,after FROM domain_events")


def _backup(db, target):
    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)


def test_a_restored_older_same_token_backup_is_checked_again(db, tmp_path):
    older = tmp_path / "older.db"
    _backup(db, older)
    _add_event(db)
    si.run_check(db)
    assert not si.due(si.read_marker(db))
    with closing(sqlite3.connect(older)) as source, closing(sqlite3.connect(db)) as live:   # restore in place: same inode
        source.backup(live)
    assert si.due(si.read_marker(db))


def test_the_verdict_survives_later_writes_but_not_a_replaced_file(db, tmp_path):
    si.run_check(db)
    _add_event(db)
    assert not si.due(si.read_marker(db))                     # a live store moving on keeps its verdict
    marker = si.marker_path(db)
    record = si.read_marker(db)
    marker.write_text(marker.read_text(encoding="utf-8").replace(f'"ino": {record["ino"]}', '"ino": 1'), encoding="utf-8")
    assert si.read_marker(db) is None                         # another file identity


def test_an_unknown_identity_never_matches(db, monkeypatch):
    si.run_check(db)
    monkeypatch.setattr(si, "fingerprint", lambda path: None)
    assert si.read_marker(db) is None
    record = {"store_id": None, "seq": 1, "ino": 1, "dev": 1, "size": 1}
    assert not si._matches(record, {"store_id": None, "seq": 1, "ino": 1, "dev": 1, "size": 1})


def test_failure_detail_is_capped_per_row(db, monkeypatch):
    monkeypatch.setattr(si, "check_database", lambda p: {"integrity": ["x\ny " * 400] + ["bad"] * 9, "foreign_keys": [], "sqlite_version": "3"})
    record = si.run_check(db)
    assert len(record["detail"]) == si.SHOWN_ROWS and all(len(row) <= si.ROW_CHARS and "\n" not in row for row in record["detail"])


@posix_only
def test_a_failed_check_reaches_the_startup_warnings(db, monkeypatch):
    monkeypatch.setattr(si, "check_database", lambda p: {"integrity": ["bad"], "foreign_keys": [], "sqlite_version": "3"})
    seen = []
    si.startup_check(db, seen.append)
    assert seen and seen[0]["result"] == "failed"


def test_due_is_owed_until_an_ok_is_recorded():
    assert si.due(None) and si.due({"result": "failed"}) and si.due({"result": "unavailable"})
    assert not si.due({"result": "ok"})


@posix_only
def test_the_startup_check_runs_once_then_is_skipped(db):
    first = si.startup_check(db)
    assert first is not None and first["result"] == "ok"
    assert si.startup_check(db) is None


@posix_only
def test_a_failed_verdict_is_rechecked_at_the_next_start(db):
    si._write_marker(db, {"result": "failed", "detail": ["old"], "at": "2026-11-22T00:00:00+00:00"})
    again = si.startup_check(db)
    assert again is not None and again["result"] == "ok"
    assert si.read_marker(db)["result"] == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only assertion")
def test_the_startup_check_never_runs_on_windows(db):
    assert si.startup_check(db) is None and si.start_background(db) is None and si.read_marker(db) is None


def test_store_status_shows_the_stored_line_and_deep_runs_the_check(root, db, monkeypatch):  # noqa: F811
    assert "Integrity: not yet checked" in bs.backlog_store_status()
    out = bs.backlog_store_status(deep=True)
    assert "Integrity: ok (full check" in out and "foreign_key_check rows: 0" in out
    assert "Integrity: ok (full check" in bs.backlog_store_status()      # the deep run updated the stored verdict


def test_a_member_cannot_run_deep_on_a_team_server(root, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bs, "_session_requester", lambda: Requester("bob", False))
    out = bs.backlog_store_status(deep=True)
    assert "host operator action" in out and "Integrity: not yet checked" in out
    assert si.read_marker(db) is None


def test_an_operator_can_run_deep_on_a_team_server(root, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bs, "_session_requester", lambda: Requester("op", True))
    assert "Integrity: ok (full check" in bs.backlog_store_status(deep=True)
