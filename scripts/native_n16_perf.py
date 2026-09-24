# User intent: N16-B before/after evidence on a copied CodeMaestro project, never the
# live one — dashboard read, export classification, the Git generation loop, the backup
# archive and the board DTO — each run against a given code tree (base or current).
"""N16-B performance measurements on a marked, activated copy.

    python scripts/native_n16_perf.py <measure> --project COPY [--code TREE] [--out FILE]

`--code` is the source tree to import (default: this checkout); pass a `git archive`
of the base commit to measure "before". Measures:

- `dashboard`: `reads.tree` / `reads.dashboard_tree` (when present) plus the three
  dashboard renders; `--out` writes the rendered texts for a byte comparison.
- `classify`: `Exporter._classify` over every projection file, cold and warm (the
  fingerprint cache is opt-in: set TASKMASTER_EXPORT_FINGERPRINTS=1 to measure it).
- `generation`: `coordinator.git.generation`'s per-file loop without Git: full
  `observe` against sync-fingerprint hits.
- `archive`: `cutover.archive_projection` plus the manifest's second pass, against a
  one-pass prototype (DEFLATED and STORED). `--out` is the archive directory here.

Read-only except `archive` (archives under `--out`'s directory) and the exporter's
in-memory fingerprints. The board DTO uses `scripts/native_n10_viewer_bench.py`.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
import zipfile


def _args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("measure", choices=("dashboard", "classify", "generation", "archive"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--code", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--repeat", type=int, default=7)
    args = parser.parse_args()
    if not (args.project / ".benchmark-copy").is_file():
        parser.error("refusing a project without the .benchmark-copy marker")
    sys.path.insert(0, str(args.code.resolve()))
    return args


def _median(samples):
    return f"median {statistics.median(samples) * 1000:.0f} ms (min {min(samples) * 1000:.0f}, max {max(samples) * 1000:.0f})"


def dashboard(args, database):
    from taskmaster import backlog_server as bs
    from taskmaster.native.queries import Repository
    from taskmaster.native_routing import reads
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, isolation_level=None)
    existing = (database.parent / "PROGRESS.md").read_text(encoding="utf-8")
    texts = {}
    for name in ("dashboard_tree", "tree"):
        build = getattr(reads, name, None)
        if build is None:
            continue

        def run():
            with Repository(connection).snapshot() as snapshot:
                return build(snapshot)
        data = None
        gc.collect()
        run()
        samples = []
        for _ in range(args.repeat):
            started = time.perf_counter()
            data = run()
            samples.append(time.perf_counter() - started)
        texts[name] = [bs._status_text(data, False), bs._status_text(data, True),
                       bs._render_progress_dashboard(data, existing, [{"text": "p"}])]
        print(f"reads.{name}: {_median(samples)}")
    if len(texts) == 2:
        print("renders identical:", texts["dashboard_tree"] == texts["tree"])
    if args.out:
        args.out.write_text(json.dumps(texts), encoding="utf-8")


def classify(args, database):
    from taskmaster.native import projection as outbox
    backlog = args.project / ".taskmaster"
    connection = sqlite3.connect(database, isolation_level=None)
    exporter = outbox.Exporter(connection, backlog, owner="n16", session="n16", clock=time.time)
    rels = [rel for (rel,) in connection.execute("SELECT file FROM projection ORDER BY file")]
    paths = {rel: exporter._path(rel) for rel in rels}
    contents = {rel: path.read_bytes() for rel, path in paths.items() if path.exists()}
    print(f"{len(contents)} files, {sum(map(len, contents.values())) / 1e6:.1f} MB")
    for label, changed in (("agrees", False), ("agrees", False), ("publish", True), ("publish", True)):
        started = time.perf_counter()
        for rel, data in contents.items():
            exporter._classify(rel, paths[rel], data + b"x" if changed else data)
        print(f"classify, {label}: {(time.perf_counter() - started) * 1000:.0f} ms")


def generation(args, database):
    from taskmaster.coordinator import sync_files
    backlog = args.project / ".taskmaster"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = connection.execute("SELECT file FROM projection WHERE file NOT LIKE 'local/%' ORDER BY file").fetchall()
    scan = sync_files.Scan(backlog, None)
    started = time.perf_counter()
    for (rel,) in rows:
        observed = scan.observe(rel, authored=False, limit=64 * 1024 * 1024)
        if observed is not None:
            sync_files.Digests.of(observed.content)
    full = time.perf_counter() - started
    warm = sync_files.Scan(backlog, scan.entries())
    started = time.perf_counter()
    hits = sum(warm.digests(rel) is not None for (rel,) in rows)
    print(f"{len(rows)} files: full observe {full:.2f} s; fingerprint hits {time.perf_counter() - started:.2f} s "
          f"({hits} hits)")


def archive(args, database):
    from taskmaster.native import cutover
    out = (args.out or args.project / "n16-archives").resolve()
    out.mkdir(parents=True, exist_ok=True)
    backlog = args.project / ".taskmaster"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    def shipped():
        target = out / "shipped.zip"
        cutover.archive_projection(args.project, target, connection)
        return target, cutover.projection_files(args.project, connection)  # write_backup's manifest pass

    def one_pass(compression):
        target = out / f"one-pass-{compression}.zip"
        files = []
        with zipfile.ZipFile(target, "w", compression=compression) as zipped:
            for (rel,) in connection.execute("SELECT file FROM projection ORDER BY file").fetchall():
                try:
                    with open(backlog / rel, "rb") as handle:
                        info = os.fstat(handle.fileno())
                        data = handle.read()
                except (FileNotFoundError, IsADirectoryError, PermissionError):
                    continue
                files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
                entry = zipfile.ZipInfo(rel, date_time=time.localtime(max(info.st_mtime, 315532800))[:6])
                entry.compress_type = compression
                entry.external_attr = 0o644 << 16
                zipped.writestr(entry, data)
        cutover._fsync(target)
        cutover._sha256(target)
        return target, files

    reference = {f["path"]: f["sha256"] for f in cutover.projection_files(args.project, connection)}
    for label, build in (("shipped: archive_projection + manifest pass", shipped),
                         ("one pass, DEFLATED", lambda: one_pass(zipfile.ZIP_DEFLATED)),
                         ("one pass, STORED", lambda: one_pass(zipfile.ZIP_STORED))):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            target, files = build()
            samples.append(time.perf_counter() - started)
        with zipfile.ZipFile(target) as zipped:
            archived = {name: hashlib.sha256(zipped.read(name)).hexdigest() for name in zipped.namelist()}
        manifest = {f["path"]: f["sha256"] for f in files}
        print(f"{label}: {_median(samples)}; {target.stat().st_size / 1e6:.1f} MB; "
              f"archive == manifest == projection_files: {archived == manifest == reference}")


def main():
    args = _args()
    database = (args.project / ".taskmaster" / "local" / "store.db").resolve()
    globals()[args.measure](args, database)


if __name__ == "__main__":
    main()
