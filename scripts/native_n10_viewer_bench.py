"""N10 server measurements on marked copies only; no live writes or activation.

The pre-N10 baseline reproduces aef3edc's _serve_json builder using the current
safe store runtime. It is a builder/encoding comparison, not old-release latency.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from benchmark_store import snapshot, bind
from native_twins import activate_native
from taskmaster import backlog_server as bs, store, viewer_board, viewer_detail
from taskmaster.native_routing import viewer, projection


def point(root):
    root = root.resolve()
    store.reset_for_tests()
    projection.reset_for_tests()
    os.environ["TASKMASTER_ROOT"] = str(root)
    bind(root)
    bs._HANDOVER_STATUS_BACKFILL_RAN = True
    db = root / ".taskmaster/local/store.db"
    with closing(sqlite3.connect(db)) as connection:
        native = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    return db if native else None


def old_payload(database):
    if database:
        from taskmaster.native_routing.reads import NativeRows
        data, _ = viewer.snapshot(database)
        data["_rows"] = NativeRows(None)
    else:
        data, _ = bs._load_snapshot()
    data.setdefault("meta", {})["_version"] = bs.VERSION
    if not isinstance(data.get("tasks"), list):
        data["tasks"] = [dict(t, epic=t.get("epic", e["id"])) for e in data.get("epics", []) for t in e.get("tasks", [])]
    data["phases"] = sorted(data.get("phases", []), key=lambda p: p.get("order") if p.get("order") is not None else 999)
    return data


def encode(payload):
    return b"" if payload is None else json.dumps(payload, default=str).encode("utf-8")


def sample(call):
    start = time.perf_counter()
    payload, spans = call()
    built = time.perf_counter()
    body = encode(payload)
    return dict(build_ms=(built - start) * 1000,
                encode_ms=(time.perf_counter() - built) * 1000,
                bytes=len(body), **{f"server_{k}_ms": v for k, v in spans.items()})


def measure(call, repeat):
    cold = sample(call)
    warm = [sample(call) for _ in range(repeat)]
    return {"first_call": cold, "warm_median": {k: statistics.median(r[k] for r in warm) for k in cold},
            "warm_samples": warm}


def body(response):
    return response[1], response[3]


def clean_board(board):
    return {k: v for k, v in board.items() if k not in ("revision", "cursor", "resync")}


def benchmark(root, repeat):
    database = point(root)
    result = {}
    result["old_builder"] = measure(lambda: (old_payload(database), {}), repeat)
    result["compatibility"] = measure(lambda: body(viewer_board.compatibility(database)), repeat)
    result["board"] = measure(lambda: body(viewer_board.response(database)), repeat)
    board = viewer_board.response(database)[1]
    result["task_count"] = len(board["tasks"])
    archived = [t for t in board["tasks"] if t.get("status") == "archived"]
    result["archived_task_count"] = len(archived)
    result["archived_encoded_bytes"] = len(encode(archived))
    revision = board["revision"]
    result["idle"] = measure(lambda: body(viewer_board.response(database, since=revision)), repeat)
    task = next(t for t in board["tasks"] if t.get("status") not in ("archived", "done"))
    result["detail"] = measure(lambda: (viewer_detail.read(task["id"], database), {}), repeat)
    before = clean_board(board)
    # Mutations are guarded by bind() and the copy marker; never name source here.
    changed = bs.backlog_update_task(task_id=task["id"], field="title", value=task["title"] + " [N10 copy benchmark]")
    if str(changed).startswith("Error"):
        raise RuntimeError(str(changed))
    result["delta"] = measure(lambda: body(viewer_board.response(database, since=revision)), repeat)
    delta = viewer_board.response(database, since=revision)[1]
    assert delta.get("since") == revision and len(delta["tasks_upsert"]) == 1
    store.reset_for_tests()
    projection.reset_for_tests()
    return result, before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--serve-copy", type=Path)
    parser.add_argument("--port", type=int, default=8873)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--browser-output", type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    if args.serve_copy:
        copy_root = args.serve_copy.resolve()
        database = point(copy_root)
        if args.baseline:
            # Only the historical frontend and pure response builder are used.
            # The Windows liveness repair remains in the serving runtime.
            static = copy_root.parent / "baseline-static"
            static.mkdir(exist_ok=False)
            archive = subprocess.check_output(["git", "archive", "--format=zip", "aef3edc", "viewer"], cwd=REPO)
            with zipfile.ZipFile(io.BytesIO(archive)) as files:
                for entry in files.infolist():
                    target = (static / entry.filename).resolve()
                    if static not in target.parents:
                        raise ValueError("unsafe archive path")
                files.extractall(static)
            bs.SCRIPT_DIR = static
            def historical(handler):
                handler._send_json(200, old_payload(database))
            bs.ViewerHandler._serve_json = historical
        server, port = bs._make_server(host="127.0.0.1", port=args.port)
        try:
            if args.browser_output:
                output = args.browser_output.resolve()
                allowed = (REPO / 'test-results').resolve()
                if allowed not in output.parents:
                    raise ValueError('browser output must be under test-results')
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                try:
                    subprocess.run([args.node, str(REPO / 'scripts/native_n10_browser_bench.mjs'),
                                    f'--url=http://127.0.0.1:{port}', f'--output={output}',
                                    f'--baseline={str(args.baseline).lower()}'], check=True, **flags)
                finally:
                    server.shutdown()
                    worker.join()
            else:
                server.serve_forever()
        finally:
            server.server_close()
        return
    if not args.source or not args.output:
        parser.error("--source and --output are required")
    output = args.output.resolve()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO))
    legacy = snapshot(args.source, output / "legacy")
    point(legacy)
    # Reconcile only the copy, then clone that exact reconciled store for parity.
    viewer_board.response()
    store.reset_for_tests()
    native = output / "native/project"
    shutil.copytree(legacy, native)
    activate_native(native)
    result = {"source_sha": sha, "dirty_worktree": dirty, "python": platform.python_version(),
              "sqlite": sqlite3.sqlite_version, "repeat": args.repeat,
              "baseline": "aef3edc builder, current safe store runtime; no old-runtime timing claim",
              "cold_definition": "first builder call after binding, not OS cache eviction",
              "copy_db_sha256": hashlib.sha256((legacy / ".taskmaster/local/store.db").read_bytes()).hexdigest()}
    boards = {}
    for mode, root in (("legacy", legacy), ("native", native)):
        result[mode], boards[mode] = benchmark(root, args.repeat)
        (output / "server.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({mode: {k: v.get("warm_median") for k, v in result[mode].items() if isinstance(v, dict)}}), flush=True)
    result["dto_twin_equal"] = boards["legacy"] == boards["native"]
    (output / "server.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["dto_twin_equal"], "CodeMaestro compact DTO twins differ"


if __name__ == "__main__":
    main()
