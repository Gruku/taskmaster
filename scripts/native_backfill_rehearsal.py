"""Rehearse N03 only on a new DB backup of a marked benchmark copy.

Never opens the source with Store (which can import/export files on reads).
Reports only hashes/counts/runtime metadata, with no authored content.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from taskmaster.integrity import check_database
from taskmaster.native.migrate import backfill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-copy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source_copy.resolve(), args.output.resolve()
    if not (source / ".benchmark-copy").is_file():
        parser.error("source must be a marked .benchmark-copy")
    if source == output or source in output.parents or output in source.parents or output.exists():
        parser.error("output must be new and outside the source copy")
    database = source / ".taskmaster/local/store.db"
    if not database.is_file():
        parser.error("source copy has no authoritative database")
    output.mkdir(parents=True)
    target = output / "store.db"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as old:
        with closing(sqlite3.connect(target)) as new:
            old.backup(new)
    # Sidecars are retained verbatim, never recovered or republished by rehearsal.
    for name in ("id-reservations.json",):
        path = database.parent / name
        if path.is_file():
            shutil.copyfile(path, output / name)
    for path in database.parent.glob("export-intent.*.json"):
        shutil.copyfile(path, output / path.name)
    report = {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "python": sys.version, "sqlite": sqlite3.sqlite_version, "processes": 1,
              "storage": "local SQLite backup of existing marked copy", "warmup": 0}
    with closing(sqlite3.connect(target, isolation_level=None)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        started = time.perf_counter()
        report["first"] = backfill(connection)
        report["first_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        report["repeat"] = backfill(connection)
        report["repeat_seconds"] = time.perf_counter() - started
    report["integrity"] = check_database(target)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
