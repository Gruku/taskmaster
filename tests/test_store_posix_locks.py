# User intent: B-105 regression - prove deterministically that the store's own raw reads of store.db (the header check,
# the forensic backup copy) never drop the POSIX locks SQLite holds in this process; dropping them let a peer process
# delete -wal/-shm under live connections and corrupt the database on Linux and macOS.
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest

from taskmaster import store

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fcntl lock semantics; Windows locks are per handle")

# SQLite's unix VFS takes SHARED as a read lock on 510 bytes starting at PENDING_BYTE + 2 (0x40000002).
_PROBE = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 510, 0x40000002)
    print("free")
except OSError:
    print("held")
"""


def _peer_sees(db) -> str:
    return subprocess.run(
        [sys.executable, "-c", _PROBE, str(db)], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def wal_connection(tmp_path):
    db = tmp_path / "store.db"
    connection = sqlite3.connect(db, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL").fetchone()
    connection.execute("CREATE TABLE t(x)")
    connection.execute("SELECT * FROM t").fetchall()
    yield db, connection
    connection.close()
    store.reset_for_tests()


def test_header_read_keeps_sqlite_shared_lock(wal_connection):
    db, _ = wal_connection
    assert _peer_sees(db) == "held"
    for _ in range(3):
        assert store._read_sqlite_header(db) == store.SQLITE_HEADER
    assert _peer_sees(db) == "held"


def test_raw_backup_copy_keeps_sqlite_shared_lock(wal_connection, tmp_path):
    db, _ = wal_connection
    store._copy_raw_file(db, tmp_path / "copy.db")
    assert (tmp_path / "copy.db").read_bytes()[:16] == store.SQLITE_HEADER
    assert _peer_sees(db) == "held"


def test_a_forked_child_can_read_the_header_while_the_parent_held_the_raw_read_lock(wal_connection):
    # The B-105 review found a forked child deadlocked on its first header read when a parent
    # thread held _RAW_READ_FDS_LOCK at the fork; the fork hook must hand the child a fresh lock.
    db, _ = wal_connection
    store._read_sqlite_header(db)
    with store._RAW_READ_FDS_LOCK:
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                import threading
                done = threading.Event()

                def read():
                    if store._read_sqlite_header(db) == store.SQLITE_HEADER:
                        done.set()
                worker = threading.Thread(target=read, daemon=True)
                worker.start()
                code = 0 if done.wait(5) else 2
            finally:
                os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
