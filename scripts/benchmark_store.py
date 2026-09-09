"""Benchmark real tool handlers on an isolated copy, never on the source.

Run with the repository Python: scripts/benchmark_store.py --source PROJECT
--output test-results/store-benchmark-TIMESTAMP. Output must not already exist.
SQLite backup includes committed WAL data; projection files are copied separately,
so a live source is not an atomic database-plus-files snapshot. First-open timing
includes reconciliation and is reported separately from steady-state timings.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import closing, contextmanager
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import re
import shutil
import sqlite3
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from taskmaster.integrity import check_database


def snapshot(source: Path, output: Path) -> Path:
    source = source.resolve()
    output = output.resolve()
    if output == source or source in output.parents:
        raise ValueError("Output must be outside the source project")
    output.mkdir(parents=True, exist_ok=False)
    root = output / "project"
    src = source / ".taskmaster"
    dst = root / ".taskmaster"
    shutil.copytree(src, dst, ignore=lambda p, names: ["local"] if Path(p) == src else [])
    local = dst / "local"
    local.mkdir()
    for name in ("PROGRESS.md", "id-reservations.json"):
        if (src / "local" / name).exists():
            shutil.copy2(src / "local" / name, local / name)
    with closing(sqlite3.connect((src / "local/store.db").as_uri() + "?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(local / "store.db")) as copy:
            live.backup(copy)
    (root / ".benchmark-copy").write_text(str(source), encoding="utf-8")
    return root


def bind(root: Path):
    root = root.resolve()
    if not (root / ".benchmark-copy").is_file():
        raise ValueError("Refusing to benchmark without isolated-copy marker")
    os.chdir(root)
    from taskmaster import backlog_server as server, store
    server.ROOT = root
    server.CONFIG_PATH = root / ".taskmaster/taskmaster.json"
    server.LEGACY_CONFIG_PATH = root / ".claude/taskmaster.json"
    if server._backlog_path().resolve() != root / ".taskmaster/backlog.yaml":
        raise ValueError("Copied configuration redirects outside the benchmark backlog")
    # Also verify the actual resolver before invoking a mutating handler.
    resolved = store._resolve_for(root / ".taskmaster", None)
    if resolved.root.resolve() != root:
        raise ValueError(f"Unexpected store root: {resolved.root}")
    return server, store


def call(server, name, **kwargs):
    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def timed(fn):
    start = time.perf_counter()
    try:
        result = str(fn())
        return {"seconds": time.perf_counter() - start, "ok": not result.startswith("Error"), "result": result}
    except Exception as exc:
        return {"seconds": time.perf_counter() - start, "ok": False, "error": repr(exc)}


def handover(server, tag):
    return call(server, "backlog_handover_create", tldr=f"BENCHMARK {tag}",
                next_action="Discard isolated benchmark copy after analysis",
                body="## Measurement\n\nSynthetic handover for isolated performance testing.\n" * 20,
                session_kind="context-handoff", thread=f"benchmark-{tag}")


def worker(root: str, tag: str, ready: str):
    server, store = bind(Path(root))
    # Prepare outside the timed contention window, as long-lived MCP servers do.
    warmup = timed(lambda: call(server, "backlog_status"))
    Path(ready + ".ready").touch()
    deadline = time.monotonic() + 180
    while not Path(ready).exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Start barrier")
        time.sleep(0.02)
    result = timed(lambda: handover(server, tag))
    result["warmup_seconds"] = warmup["seconds"]
    store.close_thread_connection()
    return result


def diagnose(root: Path, output: Path, skip_related: bool = False):
    """Low-overhead wall timings; nested spans overlap and must not be summed."""
    from functools import wraps
    root = root.resolve()
    server, store = bind(root)
    call(server, "backlog_status")
    spans = {}
    rebuild_related = store.Store._rebuild_related
    if skip_related:
        # Counterfactual only: never a production optimization. Equivalence is
        # checked below for this exact synthetic no-links workload.
        store.Store._rebuild_related = staticmethod(lambda connection: None)

    def record(name, seconds):
        spans.setdefault(name, []).append(seconds)

    def wrap(owner, name):
        original = getattr(owner, name)
        @wraps(original)
        def measured(*a, **kw):
            start = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                record(name, time.perf_counter() - start)
        setattr(owner, name, measured)

    for name in ("_rebuild_related", "_close_reverse_links"):
        wrap(store.Store, name)
        setattr(store.Store, name, staticmethod(getattr(store.Store, name)))

    for name in ("_scan_projection", "_load_cached_dict_from_connection", "_apply_dict_diff",
                 "_refresh_derived", "_export_touched", "_export_backlog", "_regenerate_progress_if_due",
                 "_flush_reservations", "_try_register_session", "_load_dict_from_connection"):
        wrap(store.Store, name)
    for name in ("_projection_schema", "_flatten_backlog_dict"):
        wrap(store, name)
    for name in ("_handover_create_in_tx", "_mutate_and_save", "_auto_link_entity"):
        wrap(server, name)
    original_mutex = store.Store._writer_mutex
    @contextmanager
    def mutex(self, **kw):
        start = time.perf_counter()
        with original_mutex(self, **kw):
            acquired = time.perf_counter()
            record("writer_wait", acquired - start)
            try:
                yield
            finally:
                record("writer_held", time.perf_counter() - acquired)
    store.Store._writer_mutex = mutex
    measurements = []
    for i in range(3):
        spans.clear()
        result = timed(lambda: handover(server, f"diagnostic-{time.time_ns()}"))
        result["spans"] = {k: {"calls": len(v), "seconds": sum(v)} for k, v in spans.items()}
        result["skip_related_counterfactual"] = skip_related
        connection = store.open_store(root / ".taskmaster").connection
        path_count = connection.execute("SELECT count(*) FROM entity_paths WHERE source IN ('anchors','location')").fetchone()[0]
        result["relationship_path_rows"] = path_count
        result["path_pair_candidates"] = path_count * (path_count - 1) // 2
        if skip_related:
            before = sorted(tuple(r) for r in connection.execute("SELECT a_kind,a_id,b_kind,b_id,via,weight FROM related"))
            connection.execute("BEGIN IMMEDIATE")
            try:
                rebuild_related(connection)
                after = sorted(tuple(r) for r in connection.execute("SELECT a_kind,a_id,b_kind,b_id,via,weight FROM related"))
                result["related_matches_full_rebuild"] = before == after
                result["related_rows"] = len(after)
            finally:
                connection.rollback()
        measurements.append(result)
        output.write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
    store.close_thread_connection()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--diagnose-copy", type=Path)
    parser.add_argument("--skip-related", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--writers", type=int, default=4)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.diagnose_copy:
        diagnose(args.diagnose_copy, output, args.skip_related)
        return
    if args.skip_related:
        parser.error("--skip-related requires --diagnose-copy")
    if not args.source:
        parser.error("--source is required unless --diagnose-copy is used")
    start = time.perf_counter()
    root = snapshot(args.source, output)
    report = {"source": str(args.source.resolve()), "copy": str(root),
              "snapshot_seconds": time.perf_counter() - start, "python": sys.version,
              "source_hashes": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (REPO / "taskmaster/store.py", REPO / "taskmaster/backlog_server.py")}}

    def save():
        (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    server, store = bind(root)
    report["first_open"] = timed(lambda: call(server, "backlog_status"))
    print("first_open", report["first_open"]["seconds"], flush=True)
    save()
    report["reads"] = {}
    for name in ("backlog_status", "backlog_list_tasks", "backlog_handover_list", "backlog_store_status"):
        samples = [timed(lambda: call(server, name)) for _ in range(args.repeat)]
        report["reads"][name] = [{k: v for k, v in s.items() if k != "result"} for s in samples]
        print(name, [round(s["seconds"], 3) for s in samples], flush=True)
        save()
    report["sequential_writes"] = []
    for i in range(args.repeat):
        sample = timed(lambda: handover(server, f"serial-{i}"))
        report["sequential_writes"].append(sample)
        print("write", i, round(sample["seconds"], 3), sample["ok"], flush=True)
        save()
    profiler = cProfile.Profile()
    profiler.enable()
    report["profiled_write"] = timed(lambda: handover(server, "profile"))
    profiler.disable()
    profiler.dump_stats(str(output / "handover.prof"))
    with (output / "handover-profile.txt").open("w", encoding="utf-8") as stream:
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative").print_stats(65)
        stats.sort_stats("tottime").print_stats(35)
    save()
    store.close_thread_connection()
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.writers) as pool:
        starts = [output / f"start-{i}" for i in range(args.writers)]
        futures = [pool.submit(worker, str(root), f"parallel-{i}", str(starts[i])) for i in range(args.writers)]
        deadline = time.monotonic() + 180
        while not all(Path(str(p) + ".ready").exists() for p in starts):
            for f in futures:
                if f.done():
                    f.result()
            if time.monotonic() > deadline:
                raise TimeoutError("Workers did not become ready")
            time.sleep(0.05)
        for p in starts:
            p.touch()
        report["concurrent_writes"] = [f.result(timeout=180) for f in futures]
    save()
    results = report["sequential_writes"] + [report["profiled_write"]] + report["concurrent_writes"]
    ids = [re.search(r"Handover written: (\S+)", r.get("result", "")) for r in results]
    ids = [m.group(1) for m in ids if m]
    with sqlite3.connect(root / ".taskmaster/local/store.db") as db:
        report["database_check"] = check_database(root / ".taskmaster/local/store.db")
        report["integrity"] = report["database_check"]["integrity"][0]
        report["entity_counts"] = dict(db.execute("SELECT kind,count(*) FROM entities WHERE deleted=0 GROUP BY kind"))
        report["verified_ids"] = [ident for ident in ids if db.execute(
            "SELECT 1 FROM entities WHERE kind='handover' AND id=? AND deleted=0", (ident,)).fetchone()]
    report["all_writes_preserved"] = len(ids) == len(results) == len(set(ids)) == len(report["verified_ids"])
    report["all_successful_writes_preserved"] = len(ids) == sum(r["ok"] for r in results) == len(set(ids)) == len(report["verified_ids"])
    report["failed_attempts"] = sum(not r["ok"] for r in results)
    report["projected_ids"] = [ident for ident in ids if list((root / ".taskmaster/handovers").rglob(f"{ident}.md"))]
    report["total_seconds"] = time.perf_counter() - start
    save()
    print("concurrent", [(round(s["seconds"], 3), s["ok"]) for s in report["concurrent_writes"]], flush=True)
    print("preserved", report["all_writes_preserved"], "integrity", report["integrity"], flush=True)


if __name__ == "__main__":
    main()
