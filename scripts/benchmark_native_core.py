"""Measure N04-N06 on a disposable backup of a marked benchmark copy.

The test-only native activation here is NOT an operator cutover command or N15
acceptance. It owns a newly created output DB, starts no service/Linear worker,
and leaves projection jobs pending. Never pass a live project as --source-copy.
"""
import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from taskmaster.integrity import check_database
from taskmaster.native.commands import execute
from taskmaster.native.migrate import backfill, encode
from taskmaster.native.queries import Repository


def distribution(values):
    ordered = sorted(values)
    return {"samples": len(values), **{name: ordered[max(0, math.ceil(p * len(values)) - 1)] * 1000
                                     for name, p in (("p50_ms", .5), ("p95_ms", .95), ("p99_ms", .99), ("max_ms", 1))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-copy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=200)
    args = parser.parse_args()
    source, output = args.source_copy.resolve(), args.output.resolve()
    if not (source / ".benchmark-copy").is_file():
        parser.error("source must have a .benchmark-copy marker")
    if output.exists() or source == output or source in output.parents or output in source.parents:
        parser.error("output must be new and separate from the marked copy")
    if args.samples < 200:
        parser.error("at least 200 measured samples are required")
    old_db = source / ".taskmaster/local/store.db"
    if not old_db.is_file() or list(old_db.parent.glob("export-intent.*.json")):
        parser.error("source requires an existing DB with no pending file recovery intents")
    reserved_path = old_db.parent / "id-reservations.json"
    reserved = json.loads(reserved_path.read_text(encoding="utf-8")) if reserved_path.exists() else {}
    output.mkdir(parents=True)
    target = output / "store.db"
    with closing(sqlite3.connect(old_db.as_uri() + "?mode=ro", uri=True)) as source_connection:
        with closing(sqlite3.connect(target)) as copy:
            source_connection.backup(copy)
    report = {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "code_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO / "taskmaster/native").glob("*.py"))},
              "python": sys.version, "sqlite": sqlite3.sqlite_version, "processes": 1, "warmup": 20,
              "storage": "local isolated SQLite backup, WAL, synchronous=FULL", "scope": "DB core plus JSON encoding; no IPC, file export or model/network latency"}
    with closing(sqlite3.connect(target, isolation_level=None)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        report["backfill"] = backfill(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO id_reservations SELECT kind,public_id FROM entity_core")
        connection.executemany("INSERT OR IGNORE INTO id_reservations VALUES(?,?)", [(kind, ident) for kind, ids in reserved.items() for ident in ids])
        for kind, prefix in {"note": "NOTE-", "bug": "B-", "issue": "ISS-", "decision": "DEC-", "idea": "IDEA-"}.items():
            matches = [re.fullmatch(re.escape(prefix) + r"(\d+)", row[0]) for row in connection.execute("SELECT public_id FROM id_reservations WHERE kind=?", (kind,))]
            connection.execute("INSERT INTO id_counters VALUES(?,?,?)", (kind, prefix, max((int(m[1]) for m in matches if m), default=0)))
        connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        connection.execute("INSERT OR REPLACE INTO meta VALUES('minimum_client_protocol','2')")
        connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1')")
        connection.commit()
        store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        ident = connection.execute("SELECT public_id FROM entity_core WHERE kind='task' AND archived=0 AND deleted=0 ORDER BY public_id LIMIT 1").fetchone()[0]
        reads, writes, work, read_bytes, write_bytes = [], [], [], [], []
        for index in range(args.samples + 20):
            started = time.perf_counter()
            with Repository(connection).snapshot() as query:
                item = query.get("task", ident, fields=["id", "title", "status", "next_step"])
                read_size = len(encode(item).encode())
            read_time = time.perf_counter() - started
            request = {"protocol": 2, "store_id": store_id, "caller_scope": "isolated-benchmark", "request_id": f"edit-{index}",
                       "operation": "task.patch", "arguments": {"id": ident, "set": {"next_step": f"isolated measurement {index}"}}}
            started = time.perf_counter()
            receipt = execute(connection, request)
            write_size = len(encode(receipt).encode())
            write_time = time.perf_counter() - started
            if index >= 20:
                reads.append(read_time)
                writes.append(write_time)
                work.append(receipt["work"])
                read_bytes.append(read_size)
                write_bytes.append(write_size)
        report.update(read=distribution(reads), write=distribution(writes), max_read_bytes=max(read_bytes), max_write_bytes=max(write_bytes),
                      max_work={name: max(w[name] for w in work) for name in work[0]},
                      pending_projection_jobs=connection.execute("SELECT COUNT(*) FROM projection_jobs WHERE state='pending'").fetchone()[0])
    report["integrity"] = check_database(target)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
