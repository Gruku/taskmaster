"""Read-only, snapshot-consistent diagnostics of committed SQLite state.

Never reuse a Store connection here: SQLite 3.45.3 and 3.47.1 can report a
malformed FTS5 inverted index after a peer commit if that connection previously
checked the index. A new connection avoids that retained virtual-table state.
This module does not classify errors as recoverable or rebuild anything.
"""
from contextlib import closing
from pathlib import Path
import sqlite3


def check_database(path: Path) -> dict:
    """Check committed state, not another connection's uncommitted changes.

    Ordinary read-only WAL access is intentional: immutable=1 would miss peer
    WAL commits. One explicit read transaction covers both checks. Fetch every
    diagnostic, not just the first error. Missing/unreadable files raise.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as connection:
        connection.execute("BEGIN")
        try:
            return {
                "integrity": [row[0] for row in connection.execute("PRAGMA integrity_check")],
                "foreign_keys": [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")],
                "sqlite_version": sqlite3.sqlite_version,
            }
        finally:
            connection.rollback()
