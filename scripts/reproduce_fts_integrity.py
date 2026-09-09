"""SQLite-only FTS diagnostic matrix; creates disposable synthetic databases.

Run with each supported Python interpreter. No application imports, private
fixture, index repair, or database-version assumption is required.
"""
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


def scenario(directory, intervention, journal, peer_process):
    path = directory / "store.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as retained:
        retained.execute("PRAGMA journal_mode=" + journal)
        retained.executescript("""
            CREATE VIRTUAL TABLE entity_fts USING fts5(body);
            CREATE TABLE related(a, b);
            INSERT INTO entity_fts VALUES('original document');
        """)
        initial = retained.execute("PRAGMA quick_check").fetchall()
        statement = "INSERT INTO entity_fts VALUES('peer document')"
        if peer_process:
            subprocess.run([sys.executable, "-c",
                "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],isolation_level=None);"
                "c.execute(sys.argv[2]);c.close()", str(path), statement], check=True)
        else:
            with closing(sqlite3.connect(path, isolation_level=None)) as peer:
                peer.execute(statement)
        if intervention == "rollback":
            retained.execute("BEGIN IMMEDIATE")
            retained.execute("INSERT INTO related VALUES('a','b')")
            retained.rollback()
        elif intervention == "snapshot":
            retained.execute("BEGIN")
        elif intervention == "checkpoint":
            retained.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        elif intervention == "fts_read":
            retained.execute("SELECT rowid FROM entity_fts LIMIT 1").fetchall()
        retained_result = retained.execute("PRAGMA integrity_check").fetchall()
        retained.rollback()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as fresh:
            fresh.execute("BEGIN")
            fresh_result = fresh.execute("PRAGMA integrity_check").fetchall()
            matches = fresh.execute("SELECT rowid FROM entity_fts WHERE entity_fts MATCH 'peer'").fetchall()
        return {"journal": journal, "peer_process": peer_process,
                "intervention": intervention, "initial": initial,
                "retained": retained_result, "fresh": fresh_result, "matches": matches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = []
    for journal in ("WAL", "DELETE"):
        for peer_process in (False, True):
            for intervention in ("none", "rollback", "snapshot", "checkpoint", "fts_read"):
                with tempfile.TemporaryDirectory() as directory:
                    results.append(scenario(Path(directory), intervention, journal, peer_process))
    report = {"python": sys.version, "sqlite": sqlite3.sqlite_version, "results": results}
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    if any(row["fresh"] != [("ok",)] or row["matches"] != [(2,)] for row in results):
        raise SystemExit("Fresh snapshot or committed search invariant failed")


if __name__ == "__main__":
    main()
