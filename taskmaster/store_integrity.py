# User intent: after the B-105 lock-loss fix, a served store on Linux/macOS gets one full integrity_check (quick_check misses
# missing index rows), its verdict kept in a sidecar file beside the store and shown by backlog_store_status; deep=True re-runs it on demand.
"""Policy around `integrity.check_database`: when to run the full check, where its verdict is kept and how it reads.

The check itself is `integrity.check_database` (fresh read-only snapshot, FTS-aware); nothing here reuses a Store connection.
The verdict lives in a sidecar file next to store.db, keyed by the store's creation token, max event seq and file identity (a restored same-token backup must not inherit it), so every process and every status
call sees one answer. It is deliberately not a `meta` row: every `meta` write fires the native_stale_* triggers, which flip an
activated store to "not ready" and would make every native tool refuse.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from taskmaster.integrity import check_database

LOG = logging.getLogger("taskmaster.serve")
MARKER_FILE = "integrity-check.json"
FIX = "b105"
SHOWN_ROWS = 5
ROW_CHARS = 200


def _cap(row: str) -> str:
    row = " ".join(str(row).split())          # SQLite can return a multi-line problem as one row
    return row if len(row) <= ROW_CHARS else row[:ROW_CHARS - 3] + "..."


def marker_path(db_path: Path) -> Path:
    return Path(db_path).parent / MARKER_FILE


def fingerprint(db_path: Path) -> dict | None:
    """What identifies this exact store.db now: creation token, highest event seq and file identity, read in ONE snapshot.

    None when any part is unreadable: an unknown identity never matches a recorded one (a restored backup carries the
    same token as the live store, so the token alone cannot tell them apart; an older copy has a lower seq or another file).
    """
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)) as connection:
            connection.execute("BEGIN")
            token = connection.execute("SELECT value FROM meta WHERE key='creation_token'").fetchone()
            seq = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()
            stat = os.stat(db_path)
            connection.execute("COMMIT")
    except (sqlite3.Error, OSError):
        return None
    if token is None or seq is None or seq[0] is None or not stat.st_ino:
        return None
    return {"store_id": str(token[0]), "seq": int(seq[0]), "ino": stat.st_ino, "dev": stat.st_dev, "size": stat.st_size}


def _matches(record: dict, now: dict | None) -> bool:
    """The record was made for this store.db as it is now (or as an earlier state of it): never on a missing id."""
    if now is None or not record.get("store_id") or record.get("store_id") != now["store_id"]:
        return False
    if record.get("ino") != now["ino"] or record.get("dev") != now["dev"]:
        return False
    recorded = record.get("seq")
    if not isinstance(recorded, int) or now["seq"] < recorded:      # an older copy was put in place
        return False
    return now["seq"] > recorded or record.get("size") == now["size"]  # same seq, other bytes: replaced


def read_marker(db_path: Path) -> dict | None:
    """The stored verdict for THIS store, or None (never checked, unreadable, or recorded for another store.db)."""
    try:
        record = json.loads(marker_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not _matches(record, fingerprint(db_path)):
        return None
    return record


def _write_marker(db_path: Path, record: dict) -> None:
    target = marker_path(db_path)
    temp = target.with_name(f"{MARKER_FILE}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    os.replace(temp, target)


def run_check(db_path: Path) -> dict:
    """Run the full check now, record the verdict and return it. The verdict is a sidecar file, never a store write.

    A store that cannot be opened read-only is not a verdict: the OperationalError propagates and nothing is recorded.
    """
    started = time.monotonic()
    seen = fingerprint(db_path)          # before the check: the verdict only vouches for a store at least this old
    try:
        diagnostics = check_database(db_path)
    except RuntimeError as exc:                      # SQLite older than 3.44: no FTS-aware check to trust
        record = {"result": "unavailable", "detail": [str(exc)]}
    except sqlite3.DatabaseError as exc:             # a fresh connection cannot parse the file (OperationalError: let it out)
        if isinstance(exc, sqlite3.OperationalError):
            raise
        record = {"result": "failed", "detail": [_cap(f"{type(exc).__name__}: {exc}")]}
    else:
        rows = [str(row) for row in diagnostics["integrity"]]
        bad = [row for row in rows if row.lower() != "ok"]
        record = {"result": "failed" if bad else "ok", "detail": [_cap(row) for row in bad[:SHOWN_ROWS]],
                  "foreign_keys": len(diagnostics["foreign_keys"]), "sqlite": diagnostics["sqlite_version"]}
    record.update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), fix=FIX,
                  seconds=round(time.monotonic() - started, 2))
    if seen is not None:                  # unknown identity: no marker, so the next start / status says "not yet checked"
        _write_marker(db_path, {**record, **seen})
    return record


def due(record: dict | None) -> bool:
    """The one-time check is owed until a completed "ok" is recorded (a "failed" one re-runs each start)."""
    return record is None or record.get("result") != "ok"


def startup_check(db_path: Path, on_problem=None) -> dict | None:
    """The served store's one-time check; POSIX only, advice only (never refuses a start). Returns the verdict if it ran."""
    if os.name == "nt" or not due(read_marker(db_path)):
        return None
    LOG.info("integrity_check of %s running (one-time, after the B-105 lock fix)", db_path)
    try:
        record = run_check(db_path)
    except Exception as exc:  # noqa: BLE001 - a check that could not run is not a verdict; the next start retries
        LOG.warning("integrity_check could not run: %s", type(exc).__name__)
        return None
    if record["result"] == "ok":
        LOG.info("integrity_check ok in %.1f s", record["seconds"])
    else:
        LOG.error("integrity_check %s: %s; stop the server and restore the store from the last backup",
                  record["result"].upper(), "; ".join(record["detail"]))
    if on_problem is not None and record["result"] == "failed":
        on_problem(record)                           # the daemon's startup-warnings surface
    return record


def start_background(db_path: Path, on_problem=None) -> threading.Thread | None:
    """Run `startup_check` on a daemon thread so a multi-second check never delays readiness."""
    if os.name == "nt":
        return None
    thread = threading.Thread(target=startup_check, args=(db_path, on_problem), name="taskmaster-integrity-check", daemon=True)
    thread.start()
    return thread


def line(record: dict | None) -> str:
    """The `Integrity:` line of backlog_store_status, from a stored verdict (cheap)."""
    if record is None:
        return "Integrity: not yet checked (full check runs once at serve start on POSIX; backlog_store_status(deep=True) runs it now)"
    when = record.get("at", "unknown time")
    if record.get("result") == "ok":
        return f"Integrity: ok (full check {when}, sqlite {record.get('sqlite', '?')}, {record.get('seconds', '?')} s)"
    if record.get("result") == "unavailable":
        return f"Integrity: not verifiable ({'; '.join(record.get('detail', []))})"
    return (f"Integrity: FAILED at {when}: {'; '.join(record.get('detail', [])) or 'see serve.log'}; "
            "stop the server and restore the store from the last backup")
