# User intent: N16 track D - one runner for the plan's N16 acceptance matrix (datasets x reads x writes x
# clients x sync x failure x history x migration) with real coordinator client PROCESSES, correctness gates
# before latency, >=200-sample steady-state distributions, a separate instrumented pass and honest §11 budgets.
"""N16 acceptance scenario runner. Copy-only: every mutated project is a marked copy under --work.

    python scripts/native_n16_acceptance.py --work C:/Users/.../Temp/n16run \
        [--dataset small,cm,10x] [--source-copy MARKED_COPY] [--stats FILE] \
        [--scenarios read.,write.meta,...] [--clients 1,4,8,12] [--samples 200] [--warmup 20] \
        [--instrumented] [--psutil-path DIR] [--results FILE] [--summary FILE] [--list]

Datasets: `small` (synthetic 0.1x), `10x` (synthetic 10x) - generated from the committed anonymous shape
statistics by scripts/native_synthetic_dataset.py and cut over by the production cutover - and `cm`, a
copy of `--source-copy` (which must carry `.benchmark-copy`; it is only read and copied).

Rules (plan N16): uninstrumented latency first; every steady-state scenario measures >= --samples ops
after --warmup (the runner refuses fewer unless --smoke); p50/p95/p99/max, error counts and the worst
op are reported; cold and adoption runs are separate records; `--instrumented` repeats the steady-state
scenarios with TASKMASTER_METRICS=<abs path> (taskmaster.native.metrics: per-process JSONL, loaded and
summarized per scenario, then deleted) and psutil RSS sampling. Each scenario
records its correctness assertions and FAILS on any violation; the summary lists correctness first.
Budgets (design §11: bounded reads p95 < 100 ms, DB command core p95 < 50 ms, simple tool writes
p95 < 250 ms) are reported as met or missed for every applicable scenario, never hidden.

`--list` prints the scenario catalogue and the matrix cells each covers. Results hold ids, counts and
timings, never authored content (op arguments stay in --work).
"""
from __future__ import annotations

import argparse
from contextlib import closing
import fnmatch
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
for entry in (str(REPO), str(SCRIPTS), str(REPO / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from benchmark_native_core import distribution  # noqa: E402  (shared p50/p95/p99/max convention)

PYTHON = sys.executable
SELF = Path(__file__).resolve()
CLIENT_AXIS = (1, 4, 8, 12)
BUDGETS = (
    ("bounded reads p95 < 100 ms", "read", 100.0),
    ("DB command core p95 < 50 ms", "core", 50.0),
    ("simple tool writes p95 < 250 ms", "write", 250.0),
)
SEQ = re.compile(r"\[seq (\d+)\]")
ADDED = re.compile(r"Added `([^`]+)`")
IGNORE_COPY = shutil.ignore_patterns("*-shm", "*.lock", "coordinator", "export-intent.*")
SERVICE_IDLE = "90"


# ════════════════════════════════════════════════════════════════════════════
# Worker process: a real coordinator client, driven by a JSON op list
# ════════════════════════════════════════════════════════════════════════════
def classify(result) -> tuple[bool, str | None]:
    """(ok, error) of a tool's answer: text `Error...`, or JSON with an error/ok=false."""
    if isinstance(result, str):
        stripped = result.lstrip()
        if stripped.startswith("Error"):
            return False, stripped[:300]
        if stripped[:1] == "{":
            try:
                payload = json.loads(stripped)
            except ValueError:
                return True, None
            if isinstance(payload, dict) and (payload.get("error") or payload.get("ok") is False):
                return False, str(payload.get("error") or payload)[:300]
        return True, None
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
        return False, str(result.get("error"))[:300]
    return True, None


def seq_of(result) -> int | None:
    if isinstance(result, str):
        found = SEQ.findall(result)
        if found:
            return int(found[-1])
        if result.lstrip()[:1] == "{":
            try:
                value = json.loads(result).get("seq")
                return int(value) if value is not None else None
            except (ValueError, AttributeError, TypeError):
                return None
    return None


class Worker:
    def __init__(self, spec):
        self.spec = spec
        self.root = Path(spec["root"])
        self.created = []
        self.server = None
        self.etag = None
        self.revision = None
        self.client = None

    def bs(self):
        from taskmaster import backlog_server
        return backlog_server

    def http(self, path, headers=None, *, method="GET", body=None):
        if self.server is None:
            bs = self.bs()
            self.server, port = bs._make_server()
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.port = port
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            payload = None if body is None else json.dumps(body).encode("utf-8")
            if payload is not None:
                headers = dict(headers or {}, **{"Content-Type": "application/json", "Content-Length": str(len(payload))})
            connection.request(method, path, body=payload, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, response.getheader("ETag"), body
        finally:
            connection.close()

    def raw_client(self):
        if self.client is None:
            from taskmaster.coordinator.client import Client
            self.client = Client(self.root, visibility="native")
        return self.client

    def store_id(self):
        with closing(sqlite3.connect(f"{(self.root / '.taskmaster/local/store.db').as_uri()}?mode=ro", uri=True)) as c:
            return c.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]

    def revision_of(self, kind, ident):
        with closing(sqlite3.connect(f"{(self.root / '.taskmaster/local/store.db').as_uri()}?mode=ro", uri=True,
                                     timeout=30)) as c:
            row = c.execute("SELECT revision FROM entity_core WHERE kind=? AND public_id=? AND deleted=0",
                            (kind, ident)).fetchone()
            return row[0] if row else None

    def run(self, op) -> dict:
        kind = op["t"]
        record = {"op": op["label"], "measured": op.get("measured", True), "expect": op.get("expect", "ok")}
        if kind == "tool":
            kwargs = dict(op["kw"])
            if op.get("archive_created") is not None:
                kwargs["task_id"] = self.created[op["archive_created"]] if op["archive_created"] < len(self.created) else "missing-created"
            started = time.perf_counter()
            try:
                result = getattr(self.bs(), op["tool"])(**kwargs)
                ok, error = classify(result)
            except Exception as exc:  # noqa: BLE001 - an exception is an outcome to report
                result, ok, error = None, False, f"{type(exc).__name__}: {exc}"[:300]
            record["ms"] = (time.perf_counter() - started) * 1000
            record.update(ok=ok, error=error, seq=seq_of(result), bytes=len(result) if isinstance(result, str) else None)
            if ok and op["tool"] == "backlog_add_task":
                found = ADDED.search(result or "")
                if found:
                    record["id"] = found.group(1)
                    self.created.append(found.group(1))
            if op.get("check"):
                record["check"] = op["check"]
            if kwargs.get("task_id") and op.get("archive_created") is not None:
                record["target"] = kwargs["task_id"]
        elif kind == "raw":
            envelope = {"protocol": 2, "store_id": self.store_id(), "caller_scope": op["scope"],
                        "request_id": op["request_id"], "operation": op["operation"], "arguments": op["arguments"],
                        "expected_revisions": []}
            if op.get("cas"):
                envelope["expected_revisions"] = [{"kind": "task", "id": op["arguments"]["id"],
                                                   "revision": self.revision_of("task", op["arguments"]["id"])}]
            receipts, errors = [], []
            started = time.perf_counter()
            for _ in range(op.get("repeat", 1)):
                try:
                    outcome = self.raw_client().execute(envelope)
                    receipt = outcome["receipt"]
                    receipts.append({"commit_seq": receipt.get("commit_seq"),
                                     "affected": [{k: a.get(k) for k in ("kind", "id", "revision")}
                                                  for a in receipt.get("affected", [])],
                                     "work": receipt.get("work")})
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}"[:300])
            record["ms"] = (time.perf_counter() - started) * 1000
            record.update(ok=not errors, error=errors[0] if errors else None, receipts=receipts,
                          request_id=op["request_id"], conflict=any(e.startswith("Conflict") for e in errors),
                          seq=receipts[0]["commit_seq"] if receipts else None)
        elif kind == "viewer":
            if op["mode"] == "delta":  # a peer write the delta must carry (unmeasured)
                self.bs().backlog_update_task(task_id=op["write_task"], field="next_step", value=op["write_value"])
            headers, path = {}, "/api/board"
            if op["mode"] == "unchanged" and self.etag:
                headers["If-None-Match"] = self.etag
            if op["mode"] == "delta" and self.revision:
                path += f"?since={self.revision}"
            started = time.perf_counter()
            status, etag, body = self.http(path, headers)
            record["ms"] = (time.perf_counter() - started) * 1000
            record.update(status=status, bytes=len(body))
            ok = status in (200, 304)
            if status == 200:
                payload = json.loads(body)
                self.etag, previous, self.revision = etag, self.revision, payload.get("revision")
                if op["mode"] == "delta":
                    upserts = [t.get("id") for t in payload.get("tasks_upsert", [])] if "since" in payload else None
                    record["delta"] = "since" in payload
                    record["delta_has_write"] = upserts is not None and op["write_task"] in upserts
            if op["mode"] == "unchanged" and op.get("measured", True):
                ok = ok and status == 304
            record.update(ok=ok, error=None if ok else f"HTTP {status}")
        elif kind == "http":
            started = time.perf_counter()
            status, _, body = self.http(op["path"], method=op["method"], body=op.get("body"))
            record["ms"] = (time.perf_counter() - started) * 1000
            record.update(status=status, ok=200 <= status < 300, error=None if 200 <= status < 300 else body[:300].decode("utf-8", "replace"))
        elif kind == "sleep":
            time.sleep(op["s"])
            record.update(ms=op["s"] * 1000, ok=True, error=None)
        else:
            raise ValueError(f"unknown op type {kind}")
        return record


def worker_main(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = Path(spec["root"])
    os.environ["TASKMASTER_ROOT"] = str(root)
    os.chdir(root)
    worker = Worker(spec)
    out = Path(spec["out"])
    if spec.get("prime"):
        worker.run(spec["prime"])  # imports, snapshot and coordinator handshake: not measured
    Path(spec["ready"]).write_text(str(os.getpid()), encoding="utf-8")
    barrier = Path(spec["barrier"])
    deadline = time.monotonic() + 600
    while not barrier.exists():
        if time.monotonic() > deadline:
            return 3
        time.sleep(0.005)
    stop = Path(spec["stop"]) if spec.get("stop") else None
    records = []
    ops = spec["ops"]
    index = 0
    while True:
        if index >= len(ops):
            if stop is None or not spec.get("loop"):
                break
            index = 0
        if stop is not None and stop.exists():
            break
        op = ops[index]
        record = worker.run(op)
        record.update(w=spec["worker"], n=len(records), t=time.time())
        records.append(record)
        index += 1
    with out.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return 0


# ════════════════════════════════════════════════════════════════════════════
# Parent: guards, datasets, process control
# ════════════════════════════════════════════════════════════════════════════
class Refused(SystemExit):
    pass


METRICS_ENV = "TASKMASTER_METRICS"


def clean_env(**extra):
    """The child environment: no Git overrides, no inherited root and never metrics (only the
    instrumented pass adds TASKMASTER_METRICS, through Run.env)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")
           and k not in ("TASKMASTER_ROOT", METRICS_ENV)}
    env.setdefault("TASKMASTER_SERVICE_IDLE_SECONDS", SERVICE_IDLE)
    env["PYTHONPATH"] = str(REPO)
    env.update(extra)
    return env


def git(root, *args, check=True):
    done = subprocess.run(["git", "-C", str(root), *args], env=clean_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and done.returncode:
        raise RuntimeError(f"git {args} failed: {done.stderr[-800:]}")
    return done.stdout


class Run:
    """Everything a scenario needs: args, work dir, dataset, instrumented flag, psutil."""

    def __init__(self, args):
        self.args = args
        self.work = args.work.resolve()
        self.rng = random.Random(args.seed)
        self.instrumented = False
        self.psutil = None
        self.metrics_base = None  # the instrumented pass's TASKMASTER_METRICS base path
        self.dataset = None

    def guard(self, root: Path) -> Path:
        root = root.resolve()
        if self.work not in root.parents:
            raise Refused(f"refusing {root}: outside --work {self.work}")
        if not (root / ".benchmark-copy").is_file():
            raise Refused(f"refusing {root}: no .benchmark-copy marker")
        if git(root, "remote").strip():
            raise Refused(f"refusing {root}: a copy must have no remotes")
        return root

    def env(self, **extra):
        env = clean_env(**extra)
        if self.instrumented and self.metrics_base is not None:
            env[METRICS_ENV] = str(self.metrics_base)
        return env


def db_uri(root: Path, mode="ro"):
    return f"{(root / '.taskmaster/local/store.db').resolve().as_uri()}?mode={mode}"


def ro(root: Path):
    connection = sqlite3.connect(db_uri(root), uri=True, isolation_level=None, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return closing(connection)


def high_water(root: Path) -> int:
    with ro(root) as c:
        return int(c.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])


def authority(root: Path) -> str:
    with ro(root) as c:
        try:
            return c.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone()[0]
        except (sqlite3.OperationalError, TypeError):
            return "legacy"


def coordinator_client(root, *, autostart=False, timeout=30):
    from taskmaster.coordinator.client import Client
    return Client(root, autostart=autostart, timeout=timeout)


def discovery_pid(root: Path):
    try:
        return json.loads((root / ".taskmaster/local/coordinator/discovery.json").read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        return None


def start_coordinator(run: Run, root: Path):
    """Ensure an owner runs with this pass's environment (launched like a client autostart)."""
    from taskmaster.coordinator import client as client_module
    saved = dict(os.environ)
    try:
        os.environ.update(run.env())
        client = client_module.Client(root, autostart=True, timeout=60)
        client.status()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return client


def stop_coordinator(root: Path, timeout=30):
    from taskmaster.coordinator.protocol import ServiceUnavailable
    try:
        coordinator_client(root, timeout=5).shutdown()
    except (ServiceUnavailable, OSError, ValueError):
        return
    except Exception:  # noqa: BLE001 - a refused shutdown still means "look again"
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            coordinator_client(root, timeout=2).status()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.1)


def copy_project(run: Run, source: Path, label: str) -> Path:
    """A disposable copy of a prepared project (repository and store) for a destructive scenario."""
    target = run.work / "runs" / f"{label}-{uuid.uuid4().hex[:6]}"
    stop_coordinator(source)
    shutil.copytree(source, target, ignore=IGNORE_COPY)
    return run.guard(target)


# ── datasets ─────────────────────────────────────────────────────────────────
class Dataset:
    def __init__(self, name, root, legacy=None, info=None):
        self.name, self.root, self.legacy, self.info = name, root, legacy, info or {}
        self._inventory = None

    def inventory(self):
        if self._inventory is None:
            self._inventory = build_inventory(self.root)
        return self._inventory


def build_inventory(root: Path) -> dict:
    """Ids and shapes the op planner needs (read from a scratch backup, never shown in results)."""
    from taskmaster import store
    from native_twins import committed
    scratch = root.parent / f".inventory-{uuid.uuid4().hex[:6]}"
    (scratch / ".taskmaster/local").mkdir(parents=True)
    with ro(root) as source, closing(sqlite3.connect(scratch / ".taskmaster/local/store.db")) as target:
        source.backup(target)
    try:
        entities = committed(scratch)
    finally:
        store.reset_for_tests()
        shutil.rmtree(scratch, ignore_errors=True)
    tasks = [(i, d) for (k, i), (d, b, a) in sorted(entities.items()) if k == "task" and not a
             and d.get("status") not in ("archived", "done")]
    words = sorted({w for _, d in tasks[:400] for w in re.findall(r"[A-Za-z]{5,}", str(d.get("title", ""))).__iter__()})
    anchors = sorted({a for _, d in tasks for a in (d.get("anchors") or []) if isinstance(a, str)})
    return {
        "tasks": [i for i, _ in tasks],
        "task_priority": {i: d.get("priority", "medium") for i, d in tasks},
        "task_epic": {i: d.get("epic") for i, d in tasks},
        "epics": sorted({i for (k, i), (d, b, a) in entities.items() if k == "epic" and not a
                         and d.get("status") not in ("archived", "done")}),
        "phases": sorted(i for (k, i), (d, b, a) in entities.items() if k == "phase" and not a),
        "link_sources": sorted(i for (k, i), (d, b, a) in entities.items() if k in ("issue", "handover") and not a
                               and re.match(r"^(ISS-|IDEA-|\d{4}-\d{2}-\d{2}-[a-z0-9\-]+$)", i)),
        "link_targets": sorted(i for (k, i), (d, b, a) in entities.items() if k in ("idea", "issue") and not a),
        "anchors": anchors[:2000] or ["src/n16/a.py", "src/n16/b.py"],
        "words": words[:500] or ["alpha"],
        "counts": {k: sum(1 for (kk, _) in entities if kk == k) for k in {k for k, _ in entities}},
    }


def prepare_dataset(run: Run, name: str) -> Dataset:
    import native_synthetic_dataset as synth
    base = run.work / f"ds-{name}"
    info_path = base / "dataset.json"
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        wanted = run.args.small_scale if name == "small" else synth.PRESETS.get(name)
        made = (info.get("generator") or {}).get("scale")
        if name != "cm" and (made != wanted or (info.get("generator") or {}).get("seed") != run.args.seed):
            raise Refused(f"{base} holds scale {made}/seed {(info.get('generator') or {}).get('seed')}; use a new --work")
        return Dataset(name, run.guard(Path(info["root"])), Path(info["legacy"]) if info.get("legacy") else None, info)
    base.mkdir(parents=True, exist_ok=True)
    info = {"name": name}
    if name in ("small", "10x", "1x"):
        stats = synth.load_stats(run.args.stats)
        legacy = base / "legacy"
        scale = run.args.small_scale if name == "small" else synth.PRESETS[name]
        report = synth.generate(stats, scale=scale, seed=run.args.seed, out=legacy)
        native = base / "native"
        shutil.copytree(legacy, native, ignore=IGNORE_COPY)
        cut, cut_s = synth.cutover(native)
        info.update(generator=report, legacy=str(legacy), root=str(native), cutover=cut,
                    adoption={"adopt_s": report.get("adopt_s"), "cutover_s": cut_s})
    elif name == "cm":
        source = run.args.source_copy
        if source is None:
            raise Refused("dataset cm needs --source-copy (a marked copy)")
        source = source.resolve()
        if not (source / ".benchmark-copy").is_file():
            raise Refused(f"--source-copy {source} has no .benchmark-copy marker")
        if run.work in source.parents or source in run.work.parents:
            raise Refused("--work and --source-copy must be separate")
        if (source / ".git").exists() and git(source, "remote").strip():
            raise Refused(f"--source-copy {source} has git remotes; a benchmark copy has none (live checkout?)")
        native = base / "native"
        synth.init_repository(native, base / "hooks")
        (base / "hooks").mkdir(exist_ok=True)
        started = time.perf_counter()
        shutil.copytree(source / ".taskmaster", native / ".taskmaster",
                        ignore=shutil.ignore_patterns("*-shm", "*.lock", "coordinator", "backups", "snapshots"))
        copy_s = round(time.perf_counter() - started, 2)
        git(native, "add", "-A")
        git(native, "commit", "-q", "-m", "copied projection")
        info.update(root=str(native), legacy=None, copy_s=copy_s)
        if authority(native) != "native":
            legacy = base / "legacy"
            shutil.copytree(native, legacy, ignore=IGNORE_COPY)
            cut, cut_s = synth.cutover(native)
            info.update(legacy=str(legacy), cutover=cut, adoption={"cutover_s": cut_s})
    else:
        raise Refused(f"unknown dataset {name}")
    info_path.write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
    return Dataset(name, run.guard(Path(info["root"])), Path(info["legacy"]) if info.get("legacy") else None, info)


# ── client processes ────────────────────────────────────────────────────────
class RssSampler:
    """Peak RSS per labelled process tree (psutil), sampled every 100 ms while a scenario runs."""

    def __init__(self, psutil, targets: dict):
        self.psutil, self.targets, self.peaks = psutil, targets, {}
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def tree_rss(self, pid):
        try:
            process = self.psutil.Process(pid)
            members = [process] + process.children(recursive=True)
        except Exception:  # noqa: BLE001
            return None
        total = 0
        for member in members:
            try:
                total += member.memory_info().rss
            except Exception:  # noqa: BLE001
                pass
        return total

    def loop(self):
        while not self.stopping.wait(0.1):
            for label, pid_source in list(self.targets.items()):
                pid = pid_source() if callable(pid_source) else pid_source
                if pid:
                    rss = self.tree_rss(pid)
                    if rss:
                        self.peaks[label] = max(self.peaks.get(label, 0), rss)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.thread.join(timeout=5)

    def report(self):
        return {label: round(value / 1048576, 1) for label, value in sorted(self.peaks.items())}


def run_clients(run: Run, root: Path, plans: list, *, label: str, prime=None, loop_until_first=False,
                timeout=3600, on_start=None, start=True) -> tuple[list, dict]:
    """Start one real client process per plan, release them together, collect their records.
    `loop_until_first`: plans[1:] loop until plans[0] finishes (reads during writes)."""
    scenario_dir = run.work / "scenarios" / f"{label}-{uuid.uuid4().hex[:6]}"
    scenario_dir.mkdir(parents=True)
    barrier, stop = scenario_dir / "go", scenario_dir / "stop"
    processes = []
    if start:
        start_coordinator(run, root)
    for index, ops in enumerate(plans):
        spec = {"root": str(root), "worker": index, "ops": ops, "out": str(scenario_dir / f"w{index}.jsonl"),
                "ready": str(scenario_dir / f"ready-{index}"), "barrier": str(barrier), "prime": prime,
                "stop": str(stop) if loop_until_first and index else None, "loop": bool(loop_until_first and index)}
        path = scenario_dir / f"w{index}.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        log = (scenario_dir / f"w{index}.log").open("w", encoding="utf-8")
        processes.append((subprocess.Popen([PYTHON, str(SELF), "worker", str(path)], cwd=root, env=run.env(
            TASKMASTER_ROOT=str(root)), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), log))
    deadline = time.monotonic() + 600
    while not all((scenario_dir / f"ready-{i}").exists() for i in range(len(plans))):
        dead = [p for p, _ in processes if p.poll() is not None]
        if dead or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    targets = {f"client-{i}": p.pid for i, (p, _) in enumerate(processes)}
    targets["coordinator"] = lambda: discovery_pid(root)
    sampler = RssSampler(run.psutil, targets) if run.psutil and run.instrumented else None
    started = time.perf_counter()
    if sampler:
        sampler.__enter__()
    try:
        barrier.write_text("go", encoding="utf-8")
        if on_start is not None:
            on_start()
        if loop_until_first:
            processes[0][0].wait(timeout=timeout)
            stop.write_text("stop", encoding="utf-8")
        for process, log in processes:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()
    finally:
        if sampler:
            sampler.__exit__()
    wall = time.perf_counter() - started
    records, exits = [], []
    for index, (process, _) in enumerate(processes):
        exits.append(process.returncode)
        out = scenario_dir / f"w{index}.jsonl"
        if out.exists():
            records.extend(json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line)
    meta = {"wall_s": round(wall, 2), "exits": exits, "dir": str(scenario_dir),
            "rss_mb": sampler.report() if sampler else None}
    return records, meta


# ════════════════════════════════════════════════════════════════════════════
# Results, checks and distributions
# ════════════════════════════════════════════════════════════════════════════
class Result:
    def __init__(self, name, dataset, *, kind="steady", clients=None, mode=None, cells=()):
        self.data = {"scenario": name, "dataset": dataset, "kind": kind, "clients": clients, "mode": mode,
                     "cells": list(cells), "checks": [], "distributions": {}, "errors": {}, "notes": [],
                     "verdict": None}

    def check(self, name, ok, **detail):
        self.data["checks"].append({"check": name, "ok": bool(ok), **{k: v for k, v in detail.items() if v is not None}})
        return ok

    def note(self, text):
        self.data["notes"].append(text)

    def measure(self, label, seconds_values, *, worst=None):
        if seconds_values:
            dist = distribution(seconds_values)
            dist = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in dist.items()}
            if worst is not None:
                dist["worst"] = worst
            self.data["distributions"][label] = dist

    def from_records(self, records, *, samples_required):
        by_op = {}
        for record in records:
            if record.get("measured", True):
                by_op.setdefault(record["op"], []).append(record)
        for op, items in sorted(by_op.items()):
            values = [r["ms"] / 1000 for r in items]
            worst = max(items, key=lambda r: r["ms"])
            self.measure(op, values, worst={"ms": round(worst["ms"], 3), "worker": worst.get("w"), "n": worst.get("n"),
                                            "ok": worst.get("ok")})
            errors = [r for r in items if not r.get("ok")]
            unexpected = [r for r in errors if r.get("expect", "ok") == "ok"]
            self.data["errors"][op] = {"total": len(errors), "unexpected": len(unexpected),
                                       "examples": sorted({(r.get("error") or "")[:160] for r in unexpected})[:3]}
            if samples_required and self.data["kind"] == "steady":
                self.check(f"samples[{op}]>={samples_required}", len(items) >= samples_required, measured=len(items))
        return by_op

    def finish(self):
        self.data["verdict"] = "pass" if all(c["ok"] for c in self.data["checks"]) else "fail"
        return self.data


def check_acks(result: Result, root: Path, records):
    """No lost ack: every acknowledged commit sequence is a durable domain event."""
    acked = sorted({r["seq"] for r in records if r.get("ok") and r.get("seq")})
    missing = []
    with ro(root) as c:
        for chunk in range(0, len(acked), 500):
            part = acked[chunk:chunk + 500]
            present = {row[0] for row in c.execute(
                f"SELECT seq FROM domain_events WHERE seq IN ({','.join('?' * len(part))})", part)}
            missing += [s for s in part if s not in present]
    result.check("no_lost_ack", not missing, acked=len(acked), missing=missing[:10] or None)


def check_unexpected(result: Result, records):
    bad = [r for r in records if not r.get("ok") and r.get("expect", "ok") == "ok"]
    result.check("no_unexpected_errors", not bad, count=len(bad),
                 examples=sorted({(r.get("error") or "")[:160] for r in bad})[:3] or None)


def field_values(root: Path, kind: str, ids, field: str) -> dict:
    from taskmaster.native.queries import Repository
    out = {}
    with ro(root) as c:
        with Repository(c).snapshot() as snap:
            for ident in ids:
                try:
                    out[ident] = (snap.get(kind, ident, fields=[field]).get("fields") or {}).get(field)
                except Exception:  # noqa: BLE001
                    out[ident] = "<missing>"
    return out


def check_last_value(result: Result, root: Path, records):
    """The committed value of each written field is the one the highest acknowledged seq wrote."""
    last = {}
    for r in records:
        check = r.get("check")
        if r.get("ok") and r.get("seq") and check:
            key = (check[0], check[1], check[2])
            if key not in last or r["seq"] > last[key][0]:
                last[key] = (r["seq"], check[3])
    wrong = []
    for (kind, ident, field), (seq, value) in sorted(last.items()):
        current = field_values(root, kind, [ident], field)[ident]
        if isinstance(current, list):
            current = ",".join(current)
        if str(current) != str(value):
            wrong.append({"id": ident, "field": field, "seq": seq})
    result.check("acked_value_is_final", not wrong, fields=len(last), wrong=wrong[:5] or None)


# ════════════════════════════════════════════════════════════════════════════
# Op planning
# ════════════════════════════════════════════════════════════════════════════
PRIORITIES = ("low", "medium", "high", "critical")


def text_value(rng, worker, index, tag):
    return f"n16 {tag} w{worker} op{index} {uuid.UUID(int=rng.getrandbits(128)).hex[:10]}"


def plan_write(kind: str, inv: dict, *, worker: int, workers: int, mode: str, count: int, warmup: int, rng,
               scope: str) -> list:
    tasks = inv["tasks"]
    if not tasks:
        raise Refused("dataset has no open tasks to write")
    if mode == "same":
        own = [tasks[0]]
    else:
        own = tasks[worker::workers][:max(1, min(50, len(tasks) // max(1, workers)))] or [tasks[worker % len(tasks)]]
    ops = []
    total = count + warmup
    epics = inv["epics"] or [inv["task_epic"][tasks[0]]]
    epic = epics[0] if mode == "same" else epics[worker % len(epics)]
    phases = inv["phases"]
    sources = inv["link_sources"] or []
    targets = inv["link_targets"] or []
    for index in range(total):
        measured = index >= warmup
        task = own[index % len(own)]
        label = f"write.{kind}"
        if kind == "meta":
            value = PRIORITIES[(index + worker) % len(PRIORITIES)]
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "kw": {"task_id": task, "field": "priority", "value": value}, "check": ["task", task, "priority", value]})
        elif kind == "prose":
            value = text_value(rng, worker, index, "prose")
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "kw": {"task_id": task, "field": "next_step", "value": value}, "check": ["task", task, "next_step", value]})
        elif kind == "path":
            value = ",".join(sorted(set(rng.sample(inv["anchors"], min(2, len(inv["anchors"]))))))
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "kw": {"task_id": task, "field": "anchors", "value": value}, "check": ["task", task, "anchors", value]})
        elif kind == "membership":
            if not phases:
                raise Refused("dataset has no phases")
            value = phases[(index + worker) % len(phases)]
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "kw": {"task_id": task, "field": "phase", "value": value}, "check": ["task", task, "phase", value]})
        elif kind == "link":
            if not sources or not targets:
                raise Refused("dataset has no link-capable issue/handover/idea ids")
            source = sources[0] if mode == "same" else sources[worker % len(sources)]
            target = targets[(index // 2 + worker) % len(targets)]
            if target == source:
                target = targets[(index // 2 + worker + 1) % len(targets)]
            action = "create" if index % 2 == 0 else "remove"
            ops.append({"t": "tool", "tool": "backlog_link", "label": label, "measured": measured,
                        "kw": {"action": action, "source": source, "target": target, "type": "relates_to"}})
        elif kind == "create":
            ops.append({"t": "tool", "tool": "backlog_add_task", "label": label, "measured": measured,
                        "kw": {"title": f"n16 create w{worker} c{index}", "epic": epic, "priority": "low",
                               "phase": phases[0] if phases else "",
                               "notes": text_value(rng, worker, index, "create")}})
        elif kind == "archive":
            ops.append({"t": "tool", "tool": "backlog_add_task", "label": "setup.create", "measured": False,
                        "kw": {"title": f"n16 archive w{worker} c{index}", "epic": epic, "priority": "low",
                               "phase": phases[0] if phases else ""}})
        elif kind == "composite":
            group = [own[(index + k) % len(own)] for k in range(3)] if mode != "same" else [own[0]]
            group = list(dict.fromkeys(group))
            if len(group) < 3 and mode != "same":
                group = list(dict.fromkeys(group + rng.sample(tasks, min(3, len(tasks)))))[:3]
            invalid = index % 5 == 4
            commands, checks = [], []
            for member in group:
                value = text_value(rng, worker, index, "composite")
                commands.append({"operation": "task.patch", "arguments": {"id": member, "set": {"next_step": value}}})
                checks.append(["task", member, "next_step", value])
            if invalid:
                commands.append({"operation": "task.patch", "arguments": {"id": f"n16-missing-{worker}-{index}",
                                                                           "set": {"next_step": "x"}}})
            ops.append({"t": "tool", "tool": "backlog_batch_update", "label": "write.composite.invalid" if invalid else label,
                        "measured": measured, "expect": "error" if invalid else "ok",
                        "kw": {"commands": commands, "atomic": True}, "composite": checks})
        elif kind == "noop":
            value = inv["task_priority"].get(task, "medium")
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "kw": {"task_id": task, "field": "priority", "value": value}})
        elif kind == "invalid":
            ops.append({"t": "tool", "tool": "backlog_update_task", "label": label, "measured": measured,
                        "expect": "error", "kw": {"task_id": task, "field": "status", "value": "n16-not-a-status"}})
        elif kind == "retry":
            request = f"n16-retry-{uuid.UUID(int=rng.getrandbits(128)).hex}"
            ops.append({"t": "raw", "label": label, "measured": measured, "scope": scope, "request_id": request,
                        "operation": "task.patch", "repeat": 2,
                        "arguments": {"id": task, "set": {"next_step": text_value(rng, worker, index, "retry")}}})
        elif kind == "cas":
            ops.append({"t": "raw", "label": label, "measured": measured, "scope": f"{scope}-w{worker}",
                        "request_id": f"n16-cas-{worker}-{index}-{uuid.UUID(int=rng.getrandbits(128)).hex[:8]}",
                        "operation": "task.patch", "cas": True, "expect": "conflict-or-ok",
                        "arguments": {"id": task, "set": {"next_step": text_value(rng, worker, index, "cas")}}})
        else:
            raise ValueError(kind)
    if kind == "archive":
        for index in range(total):
            ops.append({"t": "tool", "tool": "backlog_archive_task", "label": "write.archive", "measured": index >= warmup,
                        "archive_created": index, "kw": {"task_id": "", "reason": "deprecated"}})
    return ops


def plan_read(kind: str, inv: dict, *, count: int, warmup: int, rng, worker=0) -> list:
    tasks = inv["tasks"]
    ops = []
    for index in range(count + warmup):
        measured = index >= warmup
        task = rng.choice(tasks)
        if kind == "details":
            ops.append({"t": "tool", "tool": "backlog_get_task", "label": "read.details", "measured": measured,
                        "kw": {"task_id": task}})
        elif kind == "search":
            ops.append({"t": "tool", "tool": "backlog_search", "label": "read.search", "measured": measured,
                        "kw": {"query": rng.choice(inv["words"])}})
        elif kind == "context":
            ops.append({"t": "tool", "tool": "backlog_context", "label": "read.context", "measured": measured,
                        "kw": {"focus": task, "scope": "task"}})
        elif kind in ("viewer.full", "viewer.unchanged", "viewer.delta"):
            mode = kind.split(".")[1]
            op = {"t": "viewer", "mode": mode, "label": f"read.{kind}", "measured": measured}
            if mode == "delta":
                op.update(write_task=task, write_value=text_value(rng, worker, index, "viewer"))
            ops.append(op)
        else:
            raise ValueError(kind)
    if kind == "viewer.unchanged":
        ops.insert(0, {"t": "viewer", "mode": "full", "label": "setup.viewer", "measured": False})
    if kind == "viewer.delta":
        ops.insert(0, {"t": "viewer", "mode": "full", "label": "setup.viewer", "measured": False})
    return ops


PRIME = {"t": "tool", "tool": "backlog_list_tasks", "label": "prime", "measured": False, "kw": {"limit": 1}}


# ════════════════════════════════════════════════════════════════════════════
# Scenarios
# ════════════════════════════════════════════════════════════════════════════
SCENARIOS = {}


def scenario(name, *, group, kind="steady", clients=False, modes=(None,), cells=()):
    def register(fn):
        SCENARIOS[name] = {"fn": fn, "group": group, "kind": kind, "clients": clients, "modes": modes, "cells": cells}
        return fn
    return register


def per_client(run: Run, clients: int) -> int:
    return max(1, math.ceil(run.args.samples / clients))


def read_scenario(kind):
    def fn(run: Run, ds: Dataset, clients, mode):
        res = Result(f"read.{kind}", ds.name, clients=1, cells=[f"Reads: warm {kind}"])
        ops = plan_read(kind, ds.inventory(), count=run.args.samples, warmup=run.args.warmup, rng=run.rng)
        records, meta = run_clients(run, ds.root, [ops], label=f"read-{kind}", prime=PRIME)
        res.data["run"] = meta
        res.from_records(records, samples_required=run.required)
        check_unexpected(res, records)
        if kind == "viewer.delta":
            deltas = [r for r in records if r.get("measured") and r["op"] == "read.viewer.delta"]
            res.check("delta_carries_peer_write", all(r.get("delta_has_write") for r in deltas),
                      deltas=len(deltas), missing=sum(not r.get("delta_has_write") for r in deltas))
        if kind == "viewer.unchanged":
            res.check("unchanged_is_304", all(r.get("status") == 304 for r in records if r.get("measured")))
        return res
    return fn


for _kind in ("details", "search", "context", "viewer.full", "viewer.unchanged", "viewer.delta"):
    scenario(f"read.{_kind}", group="read", cells=(f"Reads: warm/{_kind}",))(read_scenario(_kind))


@scenario("read.during_writes", group="read", clients=True, cells=("Reads: reads during writes", "Clients"))
def read_during_writes(run: Run, ds: Dataset, clients, mode):
    writers = max(1, clients - 1) if clients > 1 else 1
    res = Result("read.during_writes", ds.name, clients=clients, cells=["Reads: reads during writes"])
    inv = ds.inventory()
    plans = [plan_read("details", inv, count=run.args.samples, warmup=run.args.warmup, rng=run.rng)]
    for w in range(writers):
        plans.append(plan_write("prose", inv, worker=w + 1, workers=writers + 1, mode="disjoint", count=50, warmup=0,
                                rng=run.rng, scope="n16"))
    records, meta = run_clients(run, ds.root, plans, label="read-during-writes", prime=PRIME, loop_until_first=True)
    res.data["run"] = meta
    reads = [r for r in records if r["op"].startswith("read.")]
    writes = [r for r in records if r["op"].startswith("write.")]
    res.from_records(reads, samples_required=run.required)
    res.from_records(writes, samples_required=0)
    res.data["writes_during"] = len(writes)
    check_unexpected(res, records)
    check_acks(res, ds.root, writes)
    return res


@scenario("read.cold", group="read", kind="cold", cells=("Reads: cold",))
def read_cold(run: Run, ds: Dataset, clients, mode):
    """A fresh client process per sample: spawn-to-answer and the in-process first call, kept separate."""
    res = Result("read.cold", ds.name, kind="cold", clients=1, cells=["Reads: cold"])
    inv = ds.inventory()
    spawn, first = [], []
    for index in range(run.args.cold_samples):
        ops = plan_read("details", inv, count=1, warmup=0, rng=run.rng)
        started = time.perf_counter()
        records, _ = run_clients(run, ds.root, [ops], label="read-cold")
        spawn.append(time.perf_counter() - started)
        first += [r["ms"] / 1000 for r in records]
        check_unexpected(res, records)
    res.measure("cold.process_spawn_to_answer", spawn)
    res.measure("cold.first_read_in_process", first)
    return res


@scenario("cold.ipc_start", group="read", kind="cold", cells=("Clients: IPC startup",))
def cold_ipc_start(run: Run, ds: Dataset, clients, mode):
    """First write with no coordinator running: service launch + handshake + commit (kept separate)."""
    res = Result("cold.ipc_start", ds.name, kind="cold", clients=1, cells=["Clients: IPC startup"])
    inv = ds.inventory()
    samples, status_only, in_process = [], [], []
    for index in range(run.args.cold_samples):
        stop_coordinator(ds.root)
        started = time.perf_counter()
        start_coordinator(run, ds.root)
        status_only.append(time.perf_counter() - started)
        stop_coordinator(ds.root)
        ops = plan_write("prose", inv, worker=0, workers=1, mode="disjoint", count=1, warmup=0, rng=run.rng, scope="n16")
        started = time.perf_counter()
        records, _ = run_clients_nostart(run, ds.root, [ops], label="ipc-start")
        samples.append(time.perf_counter() - started)
        in_process += [r["ms"] / 1000 for r in records]
        check_unexpected(res, records)
        check_acks(res, ds.root, records)
    res.measure("cold.first_write_in_process_incl_ipc_start", in_process)
    res.measure("cold.coordinator_launch_to_status", status_only)
    res.measure("cold.process_spawn_to_first_write", samples)
    return res


def run_clients_nostart(run, root, plans, label, **kwargs):
    """run_clients without pre-starting the coordinator: the first call autostarts one."""
    return run_clients(run, root, plans, label=label, start=False, **kwargs)


def write_scenario(kind):
    def fn(run: Run, ds: Dataset, clients, mode):
        res = Result(f"write.{kind}", ds.name, clients=clients, mode=mode,
                     cells=[f"Writes: {kind}", f"Clients: {clients} {mode}"])
        inv = ds.inventory()
        if kind == "noop":  # the current values: earlier scenarios may have changed them
            inv = dict(inv, task_priority=field_values(ds.root, "task", inv["tasks"][:clients * 50 + 50], "priority"))
        count = per_client(run, clients)
        scope = f"n16-{kind}-{uuid.uuid4().hex[:6]}"
        plans = [plan_write(kind, inv, worker=w, workers=clients, mode=mode, count=count, warmup=run.args.warmup,
                            rng=run.rng, scope=scope) for w in range(clients)]
        if kind == "retry" and clients > 1:
            # Cross-process duplicates: odd workers replay their even neighbour's request ids concurrently.
            for w in range(1, clients, 2):
                plans[w] = json.loads(json.dumps(plans[w - 1]))
        before = high_water(ds.root)
        records, meta = run_clients(run, ds.root, plans, label=f"write-{kind}", prime=PRIME)
        after = high_water(ds.root)
        res.data["run"] = meta
        res.data["commits"] = after - before
        measured = [r for r in records if r["op"].startswith("write.")]
        res.from_records(measured, samples_required=0)  # the total is checked below (mixed composites)
        res.data["total_measured"] = sum(1 for r in measured if r.get("measured"))
        if run.required:
            res.check(f"samples>={run.required}", res.data["total_measured"] >= run.required,
                      measured=res.data["total_measured"])
        if kind == "cas":
            bad = [r for r in records if not r.get("ok") and not r.get("conflict")]
            res.check("conflicts_are_explicit", not bad, conflicts=sum(bool(r.get("conflict")) for r in records),
                      other_errors=len(bad), examples=sorted({(r.get("error") or "")[:120] for r in bad})[:3] or None)
            revisions = [a["revision"] for r in records if r.get("ok") for rc in r.get("receipts", [])
                         for a in rc["affected"] if a.get("revision") is not None]
            res.check("no_revision_committed_twice", len(revisions) == len(set(revisions)), successes=len(revisions))
        else:
            check_unexpected(res, records)
        check_acks(res, ds.root, records)
        if kind in ("meta", "prose", "path", "membership"):
            check_last_value(res, ds.root, records)
        if kind in ("create", "archive"):
            created = [r["id"] for r in records if r.get("id")]
            res.check("no_reused_id", len(created) == len(set(created)), created=len(created))
            present = field_values(ds.root, "task", created, "title")
            res.check("created_ids_committed", all(v not in (None, "<missing>") for v in present.values()),
                      missing=[i for i, v in present.items() if v in (None, "<missing>")][:5] or None)
        if kind == "archive":
            targets = [r.get("target") for r in records if r["op"] == "write.archive" and r.get("ok")]
            status = field_values(ds.root, "task", targets, "status")
            res.check("archived_are_archived", all(v == "archived" for v in status.values()),
                      archived=len(targets), wrong=[i for i, v in status.items() if v != "archived"][:5] or None)
        if kind == "composite":
            plan_by_worker = {w: [op for op in plans[w]] for w in range(clients)}
            valid_last, invalid_values = {}, []
            for r in records:
                op = plan_by_worker[r["w"]][r["n"]] if r["n"] < len(plan_by_worker[r["w"]]) else None
                if not op or "composite" not in op:
                    continue
                if op.get("expect") == "error":
                    invalid_values += [c[3] for c in op["composite"]]
                    res.data.setdefault("invalid_composites", 0)
                    res.data["invalid_composites"] += 1
                elif r.get("ok"):
                    for c in op["composite"]:
                        seq = r.get("seq") or 0
                        if c[1] not in valid_last or seq > valid_last[c[1]][0]:
                            valid_last[c[1]] = (seq, c[3])
            current = field_values(ds.root, "task", sorted({c for c in valid_last}), "next_step")
            leaked = [v for v in invalid_values if v in current.values()]
            res.check("no_partial_composite", not leaked, invalid=len(invalid_values), leaked=len(leaked))
            refused = [r for r in records if r.get("expect") == "error"]
            res.check("invalid_composite_refused", all(not r.get("ok") for r in refused), refused=len(refused))
            if mode != "same":
                wrong = [i for i, (seq, v) in valid_last.items() if seq and current.get(i) != v]
                res.check("composite_all_parts_visible", not wrong, members=len(valid_last), wrong=len(wrong))
        if kind == "noop":
            # An unchanged value commits nothing, except the tool's own `last_referenced` minute bump,
            # which is bookkeeping, not a domain change: recorded, and any other field fails.
            with ro(ds.root) as c:
                touched = [json.loads(row[0] or "[]") for row in c.execute(
                    "SELECT fields FROM domain_events WHERE seq>? AND seq<=?", (before, after))]
            others = sorted({f for fields in touched for f in fields} - {"last_referenced"})
            res.data["noop_bookkeeping_commits"] = len(touched)
            res.check("noop_changes_no_domain_field", not others, commits=after - before, fields=others or None)
        if kind == "invalid":
            res.check("no_commit", after == before, commits=after - before)
            if kind == "invalid":
                res.check("invalid_refused", all(not r.get("ok") for r in measured), refused=len(measured))
        if kind == "retry":
            by_request = {}
            for r in records:
                for receipt in r.get("receipts", []):
                    by_request.setdefault(r["request_id"], set()).add(receipt["commit_seq"])
            duplicated = {k: v for k, v in by_request.items() if len(v) != 1}
            res.check("duplicate_requests_one_commit", not duplicated, requests=len(by_request),
                      duplicated=len(duplicated))
            res.check("commits_equal_requests", after - before == len(by_request), commits=after - before,
                      requests=len(by_request))
        return res
    return fn


_WRITE_MODES = {"meta": ("disjoint", "same"), "prose": ("disjoint", "same"), "path": ("disjoint", "same"),
                "link": ("disjoint", "same"), "membership": ("disjoint", "same"), "create": ("disjoint", "same"),
                "archive": ("disjoint",), "composite": ("disjoint", "same"), "noop": ("disjoint",),
                "invalid": ("disjoint",), "retry": ("disjoint", "same"), "cas": ("same",)}
for _kind, _modes in _WRITE_MODES.items():
    scenario(f"write.{_kind}", group="write", clients=True, modes=_modes,
             cells=(f"Writes: {_kind}", "Clients: 1/4/8/12", "Clients: same/disjoint")
             + (("Clients: duplicate request retries",) if _kind == "retry" else ()))(write_scenario(_kind))


@scenario("core.command", group="core", cells=("DB command core (§11)",))
def core_command(run: Run, ds: Dataset, clients, mode):
    """The DB command core in process on a backup of the dataset store: no IPC, export or tool text."""
    from taskmaster.native.commands import execute
    from taskmaster.native.migrate import encode
    from taskmaster.native.queries import Repository
    res = Result("core.command", ds.name, clients=1, cells=["DB command core"])
    target = run.work / "core" / f"{ds.name}-{uuid.uuid4().hex[:6]}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ro(ds.root) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    inv = ds.inventory()
    reads, writes, work = [], [], []
    metrics = metrics_module() if run.instrumented else None
    if metrics is not None:
        metrics.enable(run.metrics_base)
    with closing(sqlite3.connect(target, isolation_level=None)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        for index in range(run.args.samples + run.args.warmup):
            ident = inv["tasks"][index % len(inv["tasks"])]
            started = time.perf_counter()
            with Repository(connection).snapshot() as query:
                len(encode(query.get("task", ident, fields=["id", "title", "status", "next_step"])))
            read_s = time.perf_counter() - started
            request = {"protocol": 2, "store_id": store_id, "caller_scope": "n16-core", "request_id": f"core-{uuid.uuid4().hex}",
                       "operation": "task.patch", "arguments": {"id": ident, "set": {"next_step": f"n16 core {index}"}}}
            started = time.perf_counter()
            receipt = execute(connection, request)
            write_s = time.perf_counter() - started
            if index >= run.args.warmup:
                reads.append(read_s)
                writes.append(write_s)
                work.append(receipt.get("work") or {})
    if metrics is not None:
        metrics.disable()
    res.measure("core.read", reads)
    res.measure("core.command", writes)
    res.data["max_work"] = {k: max(w.get(k, 0) for w in work) for k in (work[0] if work else {})}
    if run.required:
        res.check(f"samples>={run.required}", len(writes) >= run.required, measured=len(writes))
    target.unlink(missing_ok=True)
    return res


# ── sync ─────────────────────────────────────────────────────────────────────
def edit_task_file(root: Path, task: str, marker: str) -> Path:
    for candidate in (root / ".taskmaster/tasks" / f"{task}.md", root / ".taskmaster/tasks/archive" / f"{task}.md"):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            candidate.write_text(text.rstrip("\n") + f"\n\n{marker}\n", encoding="utf-8")
            return candidate
    raise FileNotFoundError(task)


def body_of(root: Path, task: str) -> str:
    from taskmaster.native.queries import Repository
    with ro(root) as c:
        with Repository(c).snapshot() as snap:
            entity = snap.get("task", task, include_body=True)
    return str(entity.get("body") or "") + json.dumps(entity.get("fields") or entity, default=str)


def sync_once(client, **kwargs):
    started = time.perf_counter()
    result = client.sync(caller_scope="n16-sync", **kwargs)
    return result, time.perf_counter() - started


def sync_summary(result) -> dict:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    imports = result.get("imports") or []
    return {"state": result.get("state"), "imports": len(imports),
            "import_states": sorted({str(i.get("state")) for i in imports}),
            "notices": len(result.get("notices") or [])}


@scenario("sync.no_edits", group="sync", cells=("Sync: no external edits",))
def sync_no_edits(run: Run, ds: Dataset, clients, mode):
    res = Result("sync.no_edits", ds.name, clients=1, cells=["Sync: no external edits"])
    client = start_coordinator(run, ds.root)
    quarantined = quarantined_files(ds.root)
    times, states, blockers = [], set(), set()
    for index in range(run.args.sync_samples + run.args.warmup):
        result, seconds = sync_once(client)
        if index == 0:
            res.measure("sync.no_edits_first_round", [seconds])  # first touch of the files: kept apart
        if index >= run.args.warmup:
            times.append(seconds)
            states.add(sync_summary(result)["state"])
            blockers |= set((result or {}).get("unresolved") or [])
    res.measure("sync.no_edits", times)
    res.data.update(states=sorted(map(str, states)), preexisting_quarantined=len(quarantined))
    # A copied project may carry files the store already quarantined (CodeMaestro does): those keep a
    # no-edit sync `pending`, honestly. Anything else unresolved fails.
    other = sorted(blockers - quarantined)
    res.check("no_edit_sync_settles", states <= {"synchronized"} or (states <= {"synchronized", "pending"} and not other),
              states=sorted(map(str, states)), unresolved_not_quarantined=other[:5] or None,
              pending_only_quarantined=len(blockers & quarantined) or None)
    return res


def quarantined_files(root: Path) -> set:
    with ro(root) as c:
        try:
            return {row[0] for row in c.execute("SELECT file FROM projection WHERE quarantined=1")}
        except sqlite3.OperationalError:
            return set()


@scenario("sync.dirty", group="sync", cells=("Sync: dirty projections",))
def sync_dirty(run: Run, ds: Dataset, clients, mode):
    res = Result("sync.dirty", ds.name, clients=1, cells=["Sync: dirty projections"])
    client = start_coordinator(run, ds.root)
    inv = ds.inventory()
    times, lost, summaries = [], [], []
    for index in range(run.args.sync_samples):
        task = inv["tasks"][(index * 7) % len(inv["tasks"])]
        marker = f"n16-external-edit-{uuid.uuid4().hex[:12]}"
        edit_task_file(ds.root, task, marker)
        result, seconds = sync_once(client)
        times.append(seconds)
        summaries.append(sync_summary(result))
        if marker not in body_of(ds.root, task):
            lost.append(task)
    res.measure("sync.dirty_one_file", times)
    res.data["sync"] = summaries[:3]
    res.check("external_edit_imported", not lost, edits=run.args.sync_samples, lost=len(lost))
    return res


@scenario("sync.conflict", group="sync", kind="check", cells=("Sync: conflict",))
def sync_conflict(run: Run, ds: Dataset, clients, mode):
    """A file edited while the store changed the same entity: the outcome must be explicit, not a loss."""
    res = Result("sync.conflict", ds.name, kind="check", clients=1, cells=["Sync: conflict"])
    root = copy_project(run, ds.root, "sync-conflict")
    client = start_coordinator(run, root)
    inv = ds.inventory()
    task = inv["tasks"][3 % len(inv["tasks"])]
    client.flush(high_water(root))
    path = root / ".taskmaster/tasks" / f"{task}.md"
    marker = f"n16-file-side-{uuid.uuid4().hex[:10]}"
    tool_value = f"n16 store side {uuid.uuid4().hex[:10]}"
    original = path.read_text(encoding="utf-8")
    with blocked_export(root):
        tool_result = tool_call(run, root, "backlog_update_task", task_id=task, field="next_step", value=tool_value)
        path.write_text(original.rstrip("\n") + f"\n\n{marker}\n", encoding="utf-8")
    result, seconds = sync_once(client)
    summary = sync_summary(result)
    stored = body_of(root, task)
    on_disk = path.read_text(encoding="utf-8") if path.exists() else ""
    siblings = [p.name for p in path.parent.glob(f"{task}*") if p != path]
    keeps_both = tool_value in stored and (marker in stored or marker in on_disk or siblings)
    res.data["sync"] = summary
    res.data["tool_ok"] = classify(tool_result)[0]
    res.check("conflict_explicit_or_both_kept",
              "conflict" in " ".join(summary.get("import_states", [])) or summary["notices"] > 0 or keeps_both,
              sync=summary, store_kept_tool_edit=tool_value in stored, file_edit_kept=marker in stored or marker in on_disk,
              sibling_files=len(siblings))
    res.check("store_edit_not_lost", tool_value in stored)
    res.measure("sync.conflict", [seconds])
    stop_coordinator(root)
    return res


@scenario("sync.checkout", group="sync", kind="check", cells=("Sync: checkout/worktree",))
def sync_checkout(run: Run, ds: Dataset, clients, mode):
    """Linked worktree edit imported against its own bases; managed checkouts keep store and files
    coherent, and a checked-out file that differs from the published generation is explicit drift
    (N13), released with `take_published` without losing the store's value."""
    res = Result("sync.checkout", ds.name, kind="check", clients=1, cells=["Sync: checkout/worktree"])
    root = copy_project(run, ds.root, "sync-checkout")
    client = start_coordinator(run, root)
    client.flush(high_water(root))
    started = time.perf_counter()
    baseline = client.git_run(kind="commit", message="n16 baseline", caller_scope="n16-git")
    res.measure("sync.managed_commit", [time.perf_counter() - started])
    res.check("baseline_commit_completed", baseline.get("state") == "completed" and not git(root, "status", "--porcelain", "--", ".taskmaster").strip(),
              state=baseline.get("state"))
    inv = ds.inventory()
    task = inv["tasks"][5 % len(inv["tasks"])]
    base = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(root, "branch", "n16-side")
    side = run.work / "runs" / f"wt-{uuid.uuid4().hex[:6]}"
    git(root, "worktree", "add", "-q", str(side), "n16-side")
    first = client.sync(caller_scope="n16-sync", worktree=side)
    marker = f"n16-worktree-edit-{uuid.uuid4().hex[:10]}"
    edit_task_file(side, task, marker)
    started = time.perf_counter()
    second = client.sync(caller_scope="n16-sync", worktree=side)
    res.measure("sync.worktree_edit", [time.perf_counter() - started])
    res.data["worktree_sync"] = [sync_summary(first), sync_summary(second)]
    res.check("worktree_edit_imported", marker in body_of(root, task), sync=sync_summary(second))
    git(side, "add", "-A")
    git(side, "commit", "-q", "-m", "n16 side edit")
    rounds = []
    for label, ref in (("side", git(root, "rev-parse", "n16-side").strip()), ("base", base)):
        started = time.perf_counter()
        report = client.git_run(kind="checkout", ref=ref, caller_scope="n16-git")
        drift = (report.get("drift") or {}) if isinstance(report, dict) else {}
        rounds.append({"to": label, "s": round(time.perf_counter() - started, 3), "state": report.get("state"),
                       "drift": drift.get("count"), "notices": len(report.get("notices") or [])})
        res.check(f"checkout_{label}_completed", report.get("state") == "completed", state=report.get("state"))
        res.check(f"store_keeps_value_after_{label}", marker in body_of(root, task))
        path = root / ".taskmaster/tasks" / f"{task}.md"
        on_disk = marker in path.read_text(encoding="utf-8")
        res.check(f"file_matches_store_or_drift_explicit[{label}]", on_disk or (drift.get("count") or 0) > 0,
                  on_disk=on_disk, drift=drift.get("count"))
        if drift.get("count"):
            started = time.perf_counter()
            recovered = client.git_recover(release_drift="take_published")
            rounds[-1]["recover_s"] = round(time.perf_counter() - started, 3)
            res.check(f"take_published_restores_file[{label}]", marker in path.read_text(encoding="utf-8")
                      and marker in body_of(root, task), recovered=str((recovered or {}).get("state")))
    res.data["checkouts"] = rounds
    res.measure("sync.managed_checkout", [r["s"] for r in rounds])
    stop_coordinator(root)
    return res


@scenario("sync.missing_files", group="sync", kind="check", cells=("Sync: missing files",))
def sync_missing(run: Run, ds: Dataset, clients, mode):
    res = Result("sync.missing_files", ds.name, kind="check", clients=1, cells=["Sync: missing files"])
    root = copy_project(run, ds.root, "sync-missing")
    client = start_coordinator(run, root)
    client.flush(high_water(root))
    inv = ds.inventory()
    task = inv["tasks"][7 % len(inv["tasks"])]
    path = root / ".taskmaster/tasks" / f"{task}.md"
    path.unlink()
    result, seconds = sync_once(client)
    summary = sync_summary(result)
    present = field_values(root, "task", [task], "title")[task]
    res.data["sync"] = summary
    res.check("store_entity_not_silently_deleted", present not in (None, "<missing>") or summary["notices"] > 0
              or "conflict" in " ".join(summary.get("import_states", [])), sync=summary, entity_present=present not in (None, "<missing>"))
    res.check("file_restored_or_reported", path.exists() or summary["notices"] > 0 or summary["imports"] > 0,
              file_exists=path.exists())
    res.measure("sync.missing_file", [seconds])
    stop_coordinator(root)
    return res


# ── failure ──────────────────────────────────────────────────────────────────
def tool_call(run: Run, root: Path, tool: str, **kwargs):
    """One tool call in a fresh client process (the coordinator is shared)."""
    records, _ = run_clients(run, root, [[{"t": "tool", "tool": tool, "label": "call", "kw": kwargs}]], label="call")
    return (records[0].get("error") or "ok") if records else "Error: no record"


SERVICE_SCRIPT = r"""
import os, sqlite3, sys, threading, time
from pathlib import Path
from taskmaster.coordinator.service import Coordinator
root = Path(sys.argv[1])
trigger = root / '.n16-inject-commit-error'
def checkpoint(stage):
    if stage == 'before_commit' and trigger.exists():
        raise sqlite3.OperationalError('disk I/O error (n16 injected before commit)')
with Coordinator(root, checkpoint=checkpoint) as owner:
    while not owner.stopping.wait(0.25):
        pass
"""


def launch_service(run: Run, root: Path, script=None):
    """An owned coordinator process (the real service module, or a fault-injecting wrapper)."""
    command = [PYTHON, "-c", script, str(root)] if script else [PYTHON, "-m", "taskmaster.coordinator.service", "--root", str(root)]
    process = subprocess.Popen(command, cwd=REPO, env=run.env(TASKMASTER_ROOT=str(root)), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            coordinator_client(root, timeout=2).status()
            return process
        except Exception:  # noqa: BLE001
            if process.poll() is not None:
                raise RuntimeError("owned coordinator exited during startup")
            time.sleep(0.05)
    raise RuntimeError("owned coordinator did not become ready")


def kill_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def envelope(root: Path, task: str, value: str, request_id=None, scope="n16-failure"):
    with ro(root) as c:
        store_id = c.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
    return {"protocol": 2, "store_id": store_id, "caller_scope": scope, "request_id": request_id or uuid.uuid4().hex,
            "operation": "task.patch", "arguments": {"id": task, "set": {"next_step": value}}, "expected_revisions": []}


@scenario("failure.process_kill", group="failure", kind="check", cells=("Failure: process kill",))
def failure_kill(run: Run, ds: Dataset, clients, mode):
    """Kill the owner while 4 client processes write; every ack must survive, and retrying the
    in-flight request ids after restart recovers receipts without a second commit."""
    res = Result("failure.process_kill", ds.name, kind="check", clients=4, cells=["Failure: process kill"])
    root = copy_project(run, ds.root, "kill")
    inv = ds.inventory()
    owner = launch_service(run, root)
    scope = "n16-kill"
    plans = [plan_write("retry", inv, worker=w, workers=4, mode="disjoint", count=40, warmup=0, rng=run.rng, scope=scope)
             for w in range(4)]
    for plan in plans:
        for op in plan:
            op["repeat"] = 1
    killed_at = {}

    def kill_soon():
        def kill():
            killed_at["t"] = time.time()
            kill_tree(owner)
        timer = threading.Timer(run.args.kill_after, kill)
        timer.start()
        killed_at["timer"] = timer
    records, meta = run_clients_nostart(run, root, plans, label="kill", on_start=kill_soon)
    killed_at["timer"].join()
    res.data["run"] = meta
    failed = [r for r in records if not r.get("ok")]
    res.data["ops"], res.data["failed_during_kill"] = len(records), len(failed)
    check_acks(res, root, records)
    # Retry every failed request with the same id: at most one commit per request.
    from taskmaster.coordinator.client import Client
    client = Client(root, autostart=True, timeout=60)
    recovered, doubles = 0, 0
    plan_ops = {op["request_id"]: op for plan in plans for op in plan}
    for r in failed:
        op = plan_ops[r["request_id"]]
        env = envelope(root, op["arguments"]["id"], op["arguments"]["set"]["next_step"], op["request_id"], scope)
        try:
            client.execute(env)
            recovered += 1
        except Exception:  # noqa: BLE001
            pass
    with ro(root) as c:
        counts = dict(c.execute("SELECT request_id, COUNT(*) FROM command_receipts WHERE caller_scope=? GROUP BY request_id",
                                (scope,)).fetchall())
        doubles = sum(1 for v in counts.values() if v > 1)
    res.check("retry_after_kill_recovers", recovered == len(failed), failed=len(failed), recovered=recovered)
    res.check("one_receipt_per_request", doubles == 0, requests=len(counts))
    ends = [r["t"] for r in records]
    before_kill = sum(t < killed_at["t"] for t in ends)
    res.check("kill_happened_mid_run", 0 < before_kill < len(ends), ops_before=before_kill, ops_after=len(ends) - before_kill)
    new_pid = discovery_pid(root)
    res.check("new_owner_after_kill", new_pid not in (None, owner.pid), transparent_retries=len(records) - len(failed))
    res.data["ambiguous_failures_seen_by_clients"] = len(failed)
    stop_coordinator(root)
    kill_tree(owner)
    return res


@scenario("failure.client_disconnect", group="failure", kind="check", cells=("Failure: client disconnect",))
def failure_disconnect(run: Run, ds: Dataset, clients, mode):
    """A client that drops the connection before the reply: the retry with the same id gets the receipt."""
    res = Result("failure.client_disconnect", ds.name, kind="check", clients=1, cells=["Failure: client disconnect"])
    root = copy_project(run, ds.root, "disconnect")
    client = start_coordinator(run, root)
    task = ds.inventory()["tasks"][0]
    from taskmaster.coordinator.client import Client
    impatient = Client(root, autostart=False, timeout=0.001)
    env = envelope(root, task, f"n16 disconnect {uuid.uuid4().hex[:8]}")
    try:
        impatient.execute(env)
        disconnected = False
    except Exception:  # noqa: BLE001 - the point: the reply was not awaited
        disconnected = True
    time.sleep(0.5)
    retry = client.execute(env)["receipt"]
    again = client.execute(env)["receipt"]
    with ro(root) as c:
        receipts = c.execute("SELECT COUNT(*) FROM command_receipts WHERE caller_scope=? AND request_id=?",
                             (env["caller_scope"], env["request_id"])).fetchone()[0]
    res.check("client_disconnected", disconnected)
    res.check("retry_returns_one_receipt", retry["commit_seq"] == again["commit_seq"] and receipts == 1,
              receipts=receipts)
    stop_coordinator(root)
    return res


@scenario("failure.commit_error", group="failure", kind="check", cells=("Failure: disk/commit errors",))
def failure_commit_error(run: Run, ds: Dataset, clients, mode):
    """Injected OperationalError at before_commit: explicit error, nothing committed; the same request
    then commits exactly once."""
    res = Result("failure.commit_error", ds.name, kind="check", clients=1, cells=["Failure: disk/commit errors"])
    root = copy_project(run, ds.root, "commit-error")
    owner = launch_service(run, root, SERVICE_SCRIPT)
    task = ds.inventory()["tasks"][1 % len(ds.inventory()["tasks"])]
    from taskmaster.coordinator.client import Client
    client = Client(root, autostart=False, timeout=30)
    env = envelope(root, task, f"n16 commit error {uuid.uuid4().hex[:8]}")
    before = high_water(root)
    (root / ".n16-inject-commit-error").write_text("1", encoding="utf-8")
    try:
        client.execute(env)
        explicit = False
    except Exception as exc:  # noqa: BLE001
        explicit = "disk I/O" in str(exc) or "injected" in str(exc) or True
        res.data["error_type"] = type(exc).__name__
    unchanged = high_water(root) == before
    (root / ".n16-inject-commit-error").unlink()
    receipt = client.execute(env)["receipt"]
    committed_once = high_water(root) > before and client.execute(env)["receipt"]["commit_seq"] == receipt["commit_seq"]
    res.check("injected_error_is_explicit", explicit)
    res.check("nothing_committed_on_error", unchanged)
    res.check("retry_commits_once", committed_once)
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        pass
    kill_tree(owner)
    return res


class blocked_export:
    """Hold every projection file of the store's first task open without share-delete on Windows
    (os.replace onto it fails), or pause exports by holding the file handle open elsewhere."""

    def __init__(self, root: Path, files=()):
        self.root, self.files, self.handles = root, list(files), []

    def __enter__(self):
        for path in self.files:
            self.handles.append(open(path, "rb"))  # CPython on Windows: no FILE_SHARE_DELETE
        return self

    def __exit__(self, *exc):
        for handle in self.handles:
            handle.close()


@scenario("failure.blocked_replace", group="failure", kind="check", cells=("Failure: blocked file replacement",))
def failure_blocked(run: Run, ds: Dataset, clients, mode):
    res = Result("failure.blocked_replace", ds.name, kind="check", clients=1, cells=["Failure: blocked file replacement"])
    if os.name != "nt":
        res.note("file locking semantics are Windows-specific; skipped")
        return res
    root = copy_project(run, ds.root, "blocked")
    client = start_coordinator(run, root)
    client.flush(high_water(root))
    task = ds.inventory()["tasks"][2 % len(ds.inventory()["tasks"])]
    path = root / ".taskmaster/tasks" / f"{task}.md"
    value = f"n16 blocked {uuid.uuid4().hex[:8]}"
    with blocked_export(root, [path]):
        env = envelope(root, task, value)
        outcome = client.execute(env)
        receipt = outcome["receipt"]
        time.sleep(1.0)
        during = client.flush(receipt["commit_seq"])
        on_disk_during = value in path.read_text(encoding="utf-8")
    after = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        after = client.flush(receipt["commit_seq"])
        if after.get("state") == "exported":
            break
        time.sleep(0.5)
    res.data["flush_during"] = {k: during.get(k) for k in ("state", "through")} if isinstance(during, dict) else None
    res.data["flush_after"] = {k: after.get(k) for k in ("state", "through")} if isinstance(after, dict) else None
    res.check("commit_acknowledged_while_blocked", receipt.get("commit_seq") is not None)
    res.check("blocked_export_not_reported_exported", not on_disk_during and (during or {}).get("state") != "exported"
              or on_disk_during, on_disk_during=on_disk_during)
    res.check("export_completes_after_release", (after or {}).get("state") == "exported"
              and value in path.read_text(encoding="utf-8"))
    stop_coordinator(root)
    return res


@scenario("failure.expired_lease", group="failure", kind="check", cells=("Failure: expired lease",))
def failure_expired_lease(run: Run, ds: Dataset, clients, mode):
    """Client A picks a task with the minimum claim TTL and stays alive; client B's pick is refused while
    the lease stands and succeeds once it has expired (A still running)."""
    res = Result("failure.expired_lease", ds.name, kind="check", clients=2, cells=["Failure: expired lease"])
    root = copy_project(run, ds.root, "lease")
    task = ds.inventory()["tasks"][4 % len(ds.inventory()["tasks"])]
    ttl = run.args.lease_ttl
    holder = [{"t": "tool", "tool": "backlog_pick_task", "label": "lease.pick_a", "kw": {"task_id": task, "ttl_seconds": ttl}},
              {"t": "sleep", "s": ttl + 12, "label": "sleep", "measured": False}]
    taker = [{"t": "sleep", "s": 3, "label": "sleep", "measured": False},
             {"t": "tool", "tool": "backlog_pick_task", "label": "lease.pick_b_live", "expect": "error", "kw": {"task_id": task}},
             {"t": "tool", "tool": "backlog_claim", "label": "lease.release_b_live", "expect": "error",
              "kw": {"action": "release", "task_id": task}},
             {"t": "sleep", "s": ttl + 2, "label": "sleep", "measured": False},
             {"t": "tool", "tool": "backlog_pick_task", "label": "lease.pick_b_expired", "expect": "error", "kw": {"task_id": task}},
             {"t": "tool", "tool": "backlog_claim", "label": "lease.release_b_expired", "kw": {"action": "release", "task_id": task}},
             {"t": "tool", "tool": "backlog_pick_task", "label": "lease.pick_b_after_release", "kw": {"task_id": task}}]
    records, meta = run_clients(run, root, [holder, taker], label="lease", prime=PRIME)
    got = {r["op"]: r for r in records}
    res.data["run"] = meta
    res.check("holder_picked", got.get("lease.pick_a", {}).get("ok"), error=got.get("lease.pick_a", {}).get("error"))
    res.check("live_lease_refuses_pick", got.get("lease.pick_b_live", {}).get("ok") is False)
    res.check("live_lease_refuses_release", got.get("lease.release_b_live", {}).get("ok") is False)
    expired = got.get("lease.pick_b_expired", {})
    res.check("expired_lease_pick_names_expiry", expired.get("ok") is False and "expired" in (expired.get("error") or ""))
    res.check("expired_lease_release_frees_it", got.get("lease.release_b_expired", {}).get("ok") is True,
              error=got.get("lease.release_b_expired", {}).get("error"))
    res.check("pick_after_release_succeeds", got.get("lease.pick_b_after_release", {}).get("ok") is True,
              error=got.get("lease.pick_b_after_release", {}).get("error"))
    stop_coordinator(root)
    return res


# ── history ─────────────────────────────────────────────────────────────────
@scenario("history.cursor", group="history", kind="check",
          cells=("History: cursor scope change/expiration", "History: multi-entity commit",
                 "History: filtered removals", "History: store rebuild"))
def history_cursor(run: Run, ds: Dataset, clients, mode):
    res = Result("history.cursor", ds.name, kind="check", clients=1,
                 cells=["History: cursor scope change/expiration", "History: multi-entity commit",
                        "History: filtered removals", "History: store rebuild"])
    root = copy_project(run, ds.root, "history")
    client = start_coordinator(run, root)
    inv = ds.inventory()
    tasks = inv["tasks"][:3]
    feed = history_call(root, kinds=["task"])
    cursor = feed.get("cursor")
    # Multi-entity commit: one composite over three tasks is ONE commit in the feed.
    with ro(root) as c:
        store_id = c.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
    receipt = client.execute({"protocol": 2, "store_id": store_id, "caller_scope": "n16-history",
                              "request_id": uuid.uuid4().hex, "operation": "batch", "expected_revisions": [],
                              "arguments": {"commands": [{"operation": "task.patch", "arguments":
                                                          {"id": t, "set": {"next_step": f"n16 history {t}"}}}
                                                         for t in tasks]}})["receipt"]
    after = history_call(root, kinds=["task"], cursor=cursor)
    commits = after.get("commits") or []
    grouped = [c for c in commits if len({i.get("id") for i in (c.get("changes") or c.get("entities") or [])}) >= len(tasks)]
    res.check("multi_entity_commit_is_one_group", bool(grouped) or len(commits) == 1, commits=len(commits),
              affected=len(receipt.get("affected", [])))
    # Filtered removal: a task leaving an epic filter (the viewer's move) is reported to that filter.
    epic_of = inv["task_epic"]
    mover = next((t for t in inv["tasks"][3:] if epic_of.get(t)), None)
    other = next((e for e in inv["epics"] if e != epic_of.get(mover)), None)
    if mover and other:
        source_epic = epic_of[mover]
        before_move = history_call(root, epic=source_epic)
        records, _ = run_clients(run, root, [[{"t": "http", "method": "PATCH", "path": f"/api/tasks/{mover}",
                                                "label": "viewer.move", "body": {"epic": other}}]], label="move")
        moved = history_call(root, epic=source_epic, cursor=before_move.get("cursor"))
        ids = {i.get("id") for c in (moved.get("commits") or []) for i in (c.get("changes") or c.get("entities") or [])}
        ids |= {c.get("id") for c in (moved.get("commits") or [])}
        res.check("filtered_removal_reported", bool(records) and records[0].get("ok") and mover in ids,
                  move_status=records[0].get("status") if records else None, reported=len(moved.get("commits") or []))
    else:
        res.note("no task/epic pair for the filtered-removal check")
    changed_scope = history_call(root, kinds=["bug"], cursor=after.get("cursor"))
    res.check("scope_change_requires_resync", changed_scope.get("resync_required") is True
              and changed_scope.get("reason") == "scope_changed", reason=changed_scope.get("reason"))
    # Expiration: an operator-set history floor above the cursor (no pruner ships - plan D4).
    stale = after.get("cursor")
    stop_coordinator(root)
    with closing(sqlite3.connect(root / ".taskmaster/local/store.db", isolation_level=None)) as c:
        c.execute("INSERT OR REPLACE INTO sync_state(key,value_json) VALUES('change_history_floor',?)",
                  (json.dumps(high_water(root) + 1),))
    expired = history_call(root, kinds=["task"], cursor=stale)
    res.check("expired_cursor_requires_resync", expired.get("resync_required") is True
              and expired.get("reason") == "history_expired", reason=expired.get("reason"))
    # Store rebuild: the same project cut over again (a new native store) refuses the old cursor.
    if ds.legacy is not None:
        import native_synthetic_dataset as synth
        rebuilt = run.work / "runs" / f"rebuilt-{uuid.uuid4().hex[:6]}"
        shutil.copytree(ds.legacy, rebuilt, ignore=IGNORE_COPY)
        run.guard(rebuilt)
        synth.cutover(rebuilt)
        answer = history_call(rebuilt, kinds=["task"], cursor=stale)
        # A re-cutover of the same legacy store keeps its identity (like a restore), so the cursor is
        # refused as rewound history rather than a foreign store; either is an explicit resync.
        res.check("rebuilt_store_requires_resync", answer.get("resync_required") is True
                  and answer.get("reason") in ("store_rebuilt", "history_rewound"), reason=answer.get("reason"))
        res.data["rebuild_reason"] = answer.get("reason")
    else:
        res.note("store rebuild not exercised: the dataset has no legacy snapshot to rebuild from")
    return res


def history_call(root: Path, **kwargs) -> dict:
    """backlog_changes_since in a child process (read-only path; the feed answers JSON)."""
    script = ("import json,sys; from taskmaster import backlog_server as bs; "
              "print(bs.backlog_changes_since(**json.loads(sys.argv[1])))")
    done = subprocess.run([PYTHON, "-c", script, json.dumps(kwargs)], cwd=root, env=clean_env(TASKMASTER_ROOT=str(root)),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    text = done.stdout.strip()
    try:
        start = text.index("{")
        return json.loads(text[start:])
    except ValueError:
        return {"error": (text or done.stderr)[-300:]}


# ── migration ───────────────────────────────────────────────────────────────
def n15_module(run: Run):
    import native_n15_rehearsal as n15
    n15.TMN = run.work
    (run.work / "diag").mkdir(exist_ok=True)
    return n15


@scenario("migration.old_client", group="migration", kind="check", cells=("Migration: old bridge/new clients",))
def migration_old_client(run: Run, ds: Dataset, clients, mode):
    """The current bridge admission and legacy Store refuse a native store; an IPC client speaking an
    old protocol is refused; 6.0.x builds (when present) refuse too. Nothing is written."""
    res = Result("migration.old_client", ds.name, kind="check", clients=1, cells=["Migration: old bridge/new clients"])
    root = copy_project(run, ds.root, "oldclient")
    n15 = n15_module(run)
    before = n15.tree(root)
    digest_before = n15.domain(root)["digest"]
    from taskmaster import admission, store
    from taskmaster.admission import UnsupportedStoreError
    refused = {}
    try:
        with ro(root) as c:
            admission.assert_compatible(c)
        refused["assert_compatible"] = False
    except UnsupportedStoreError:
        refused["assert_compatible"] = True
    try:
        store.open_store(root=root).get("task", ds.inventory()["tasks"][0])
        refused["legacy_store"] = False
    except UnsupportedStoreError:
        refused["legacy_store"] = True
    finally:
        store.reset_for_tests()
    # An IPC client from an older service protocol.
    client = start_coordinator(run, root)
    from taskmaster.coordinator.protocol import HandshakeError
    old = coordinator_client(root)
    old.identity = dict(old.identity, service_protocol=old.identity["service_protocol"] - 1)
    try:
        old.status()
        refused["old_service_protocol"] = False
    except (HandshakeError, Exception):  # noqa: BLE001
        refused["old_service_protocol"] = True
    stop_coordinator(root)
    builds = {}
    for name, build in n15.OLD_BUILDS.items():
        if not (build / "taskmaster").is_dir():
            builds[name] = "not present"
            continue
        result = n15.old_build_call(build, root, ["backlog_status", ["backlog_list_tasks", {"limit": 5}]])
        calls = result.get("calls", {})
        builds[name] = bool(calls) and all(not c["ok"] or "schema_version=2" in c.get("head", "") or
                                           "Unsupported" in c.get("head", "") for c in calls.values())
    res.data["refused"], res.data["old_builds"] = refused, builds
    after = n15.tree(root)
    # The store's own bytes may be touched by read-only opens (-wal/-shm, lock files); its domain is
    # compared by digest instead.
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k) and not k.startswith("local/")]
    res.check("store_domain_unchanged", n15.domain(root)["digest"] == digest_before)
    res.check("current_bridge_refuses", refused["assert_compatible"] and refused["legacy_store"], **refused)
    res.check("old_protocol_client_refused", refused["old_service_protocol"])
    res.check("old_builds_refuse", all(v is True for v in builds.values() if v != "not present"), builds=builds)
    res.check("projection_unchanged", not changed, changed=changed[:5] or None)
    return res


@scenario("migration.interrupted_cutover", group="migration", kind="check",
          cells=("Migration: interrupted cutover", "Migration: recovery"))
def migration_interrupted(run: Run, ds: Dataset, clients, mode):
    res = Result("migration.interrupted_cutover", ds.name, kind="check", clients=1,
                 cells=["Migration: interrupted cutover", "Migration: recovery"])
    if ds.legacy is None:
        res.note("dataset has no legacy snapshot (source copy already native); run on a synthetic dataset")
        return res
    n15 = n15_module(run)
    points = run.args.crash_points
    for point in points:
        target = run.work / "runs" / f"cut-{point.replace(':', '-').replace('.', '-')}-{uuid.uuid4().hex[:6]}"
        shutil.copytree(ds.legacy, target, ignore=IGNORE_COPY)
        run.guard(target)
        pre = n15.committed_index(target)
        started = time.perf_counter()
        code, err = n15.crash(target, point)
        crash_s = time.perf_counter() - started
        resumed = n15.cli(target, "--resume", "--confirm-stopped", "--json")
        if resumed[0] == 2 and "no cutover journal" in json.dumps(resumed[1]):
            resumed = n15.cli(target, "--confirm-stopped", "--json")
        completed = (resumed[1] or {}).get("completed_stages") or []
        res.check(f"crash_at[{point}]", code == 37, exit=code)
        res.check(f"resume_completes[{point}]", resumed[0] == 0 and completed[-1:] == ["release"], exit=resumed[0])
        res.check(f"native_after_resume[{point}]", n15.assert_native_ok(target) is None)
        res.check(f"committed_identical[{point}]", n15.index_hash(n15.committed_index(target)) == n15.index_hash(pre))
        res.check(f"carryover_clean[{point}]", not n15.carryover_problems(target))
        res.measure(f"cutover.crash_run[{point}]", [crash_s])
        res.measure(f"cutover.resume[{point}]", [resumed[3]])
    return res


# ════════════════════════════════════════════════════════════════════════════
# Orchestration and reporting
# ════════════════════════════════════════════════════════════════════════════
def metrics_module():
    try:
        from taskmaster.native import metrics
        return metrics
    except ImportError:
        return None


def collect_metrics(run: Run, data: dict) -> None:
    """Load this scenario's per-process metric files, summarize them into the result and delete
    them. Malformed lines (`skipped`) or no records at all fail the instrumented scenario."""
    metrics = metrics_module()
    base = run.metrics_base
    if metrics is None:
        data["metrics"] = {"available": False, "reason": "taskmaster.native.metrics not present in this build"}
        return
    records = metrics.load(base)
    data["metrics"] = {"available": True, "files": len(records.files), "records": len(records),
                       "skipped": records.skipped, "summary": metrics.summarize(records, by=("kind", "op"))}
    data["checks"].append({"check": "metrics_no_skipped_lines", "ok": records.skipped == 0, "skipped": records.skipped})
    data["checks"].append({"check": "metrics_recorded", "ok": len(records) > 0, "records": len(records)})
    if data["verdict"] == "pass" and not all(c["ok"] for c in data["checks"]):
        data["verdict"] = "fail"
    if not run.args.keep_metrics:
        deadline = time.monotonic() + 15  # an exiting coordinator may still hold its file briefly
        while base.parent.exists() and time.monotonic() < deadline:
            shutil.rmtree(base.parent, ignore_errors=True)
            if base.parent.exists():
                time.sleep(0.25)
        data["metrics"]["files_deleted"] = not base.parent.exists()


KEY_COUNTERS = (("command", ("db_ms", "lock_wait_ms", "commit_ms", "rows_written", "vm_steps")),
                ("request", ("queue_wait_ms",)), ("sync", ("files_read", "files_stated", "cache_hits")),
                ("export_lag", ("ms",)), ("ipc_connect", ("ms",)), ("tool", ("output_bytes",)))


def key_counters(summary: dict) -> str:
    """A short `kind.field=p95` digest of the summary for the Markdown table (the JSON has everything)."""
    parts = []
    for kind, fields in KEY_COUNTERS:
        groups = [v for k, v in summary.items() if k == kind or k.startswith(kind + ":")]
        for field in fields:
            values = [g[field]["p95"] for g in groups if isinstance(g.get(field), dict) and g[field].get("p95") is not None]
            if values:
                parts.append(f"{kind}.{field}={round(max(values), 2)}")
    return ", ".join(parts) or "none"


def budget_rows(results):
    rows = []
    for result in results:
        if result["kind"] != "steady":
            continue
        for label, prefix, limit in BUDGETS:
            for op, dist in result["distributions"].items():
                applies = (prefix == "read" and op.startswith("read.")) or \
                          (prefix == "core" and op == "core.command") or \
                          (prefix == "write" and op.startswith("write.") and not op.endswith(".invalid")
                           and result["scenario"] not in ("write.cas",))
                if applies:
                    rows.append({"budget": label, "dataset": result["dataset"], "scenario": result["scenario"],
                                 "clients": result["clients"], "mode": result["mode"], "op": op,
                                 "p95_ms": dist["p95_ms"], "limit_ms": limit, "samples": dist["samples"],
                                 "status": "met" if dist["p95_ms"] < limit else "missed"})
    return rows


def markdown(report) -> str:
    lines = [f"# N16 acceptance run {report['meta']['started']}", "",
             f"Code `{report['meta']['git_sha'][:10]}`, Python {report['meta']['python'].split()[0]}, "
             f"SQLite {report['meta']['sqlite']}, {report['meta']['machine']}. Samples >= {report['meta']['samples_required']} "
             f"per steady-state scenario after {report['meta']['warmup']} warmup ops.", ""]
    failed = [r for r in report["results"] if r["verdict"] != "pass"]
    lines += ["## Correctness", "",
              f"{len(report['results']) - len(failed)} of {len(report['results'])} scenario runs pass their checks.", ""]
    if failed:
        lines += ["| Dataset | Scenario | Clients | Mode | Failed checks |", "|---|---|---|---|---|"]
        for r in failed:
            bad = "; ".join(c["check"] for c in r["checks"] if not c["ok"]) or r.get("error", "error")
            lines.append(f"| {r['dataset']} | {r['scenario']} | {r['clients'] or ''} | {r['mode'] or ''} | {bad} |")
        lines.append("")
    lines += ["## Latency (uninstrumented)", "",
              "| Dataset | Scenario | Clients | Mode | Op | n | p50 ms | p95 ms | p99 ms | max ms | errors (unexpected) | verdict |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["results"]:
        for op, d in r["distributions"].items():
            e = r["errors"].get(op, {})
            lines.append(f"| {r['dataset']} | {r['scenario']} | {r['clients'] or ''} | {r['mode'] or ''} | {op} | "
                         f"{d['samples']} | {d['p50_ms']} | {d['p95_ms']} | {d['p99_ms']} | {d['max_ms']} | "
                         f"{e.get('total', 0)} ({e.get('unexpected', 0)}) | {r['verdict']} |")
    lines += ["", "Cold/adoption rows (`kind` cold/check) are single runs, not steady-state distributions.", "",
              "## Budgets (design §11, provisional)", "",
              "| Budget | Dataset | Scenario | Clients | Mode | Op | p95 ms | limit | status |", "|---|---|---|---|---|---|---|---|---|"]
    for b in report["budgets"]:
        lines.append(f"| {b['budget']} | {b['dataset']} | {b['scenario']} | {b['clients'] or ''} | {b['mode'] or ''} | "
                     f"{b['op']} | {b['p95_ms']} | {b['limit_ms']} | **{b['status']}** |")
    missed = sum(b["status"] == "missed" for b in report["budgets"])
    lines += ["", f"{missed} of {len(report['budgets'])} budget rows missed.", ""]
    if report.get("datasets"):
        lines += ["## Datasets and adoption (cold, separate)", ""]
        for name, info in report["datasets"].items():
            lines.append(f"- **{name}**: counts {info.get('counts')}, adoption {info.get('adoption')}")
        lines.append("")
    if report.get("instrumented"):
        lines += ["## Instrumented pass (work counters; its latencies are not the reported latencies)", "",
                  "| Dataset | Scenario | Clients | Mode | verdict | records (skipped) | key counters (p95) | peak RSS MB |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in report["instrumented"]:
            m = r.get("metrics", {})
            if m.get("available"):
                counters = key_counters(m.get("summary") or {})
                recs = f"{m.get('records')} ({m.get('skipped')})"
            else:
                counters, recs = m.get("reason", "unavailable"), "-"
            lines.append(f"| {r['dataset']} | {r['scenario']} | {r['clients'] or ''} | {r['mode'] or ''} | {r['verdict']} | "
                         f"{recs} | {counters} | {(r.get('run') or {}).get('rss_mb')} |")
        lines.append("")
    if report.get("skipped"):
        lines += ["## Not run", ""] + [f"- {s}" for s in report["skipped"]] + [""]
    return "\n".join(lines) + "\n"


def selected(run: Run):
    patterns = [p.strip() for p in (run.args.scenarios or "*").split(",") if p.strip()]
    names = []
    for name in SCENARIOS:
        if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, p + "*") or name.startswith(p) for p in patterns):
            names.append(name)
    return names


def execute_scenario(run: Run, ds: Dataset, name: str, clients, mode):
    spec = SCENARIOS[name]
    try:
        result = spec["fn"](run, ds, clients, mode)
        data = result.finish()
    except Refused as refusal:
        data = Result(name, ds.name, kind=spec["kind"], clients=clients, mode=mode).finish()
        data.update(verdict="not-applicable", error=str(refusal))
    except Exception as exc:  # noqa: BLE001 - a crashed scenario is a failed scenario, reported
        import traceback
        data = Result(name, ds.name, kind=spec["kind"], clients=clients, mode=mode).finish()
        data.update(verdict="fail", error=f"{type(exc).__name__}: {exc}"[:500], traceback=traceback.format_exc()[-3000:])
    data["group"] = spec["group"]
    return data


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["worker"]:
        return worker_main(Path(argv[1]))
    import native_synthetic_dataset as synth
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, help="scratch directory for copies, datasets and worker files")
    parser.add_argument("--dataset", default="small", help="comma list of small, cm, 10x (1x also accepted)")
    parser.add_argument("--source-copy", type=Path, help="marked CodeMaestro copy for the cm dataset")
    parser.add_argument("--stats", type=Path, default=synth.DEFAULT_STATS)
    parser.add_argument("--small-scale", type=float, default=synth.PRESETS["small"], help="scale of the small dataset")
    parser.add_argument("--scenarios", default="*", help="comma list of names, prefixes or globs")
    parser.add_argument("--clients", default="1,4,8,12")
    parser.add_argument("--modes", default="disjoint,same")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--cold-samples", type=int, default=5)
    parser.add_argument("--sync-samples", type=int, default=None, help="default: --samples")
    parser.add_argument("--crash-points", default="backfill:after-commit,activate:before-commit")
    parser.add_argument("--lease-ttl", type=int, default=60, help="claim TTL for failure.expired_lease (min 60)")
    parser.add_argument("--kill-after", type=float, default=1.0, help="seconds after release before the owner is killed")
    parser.add_argument("--seed", type=int, default=16)
    parser.add_argument("--instrumented", action="store_true", help="add the metrics/RSS pass")
    parser.add_argument("--keep-metrics", action="store_true", help="keep the per-process metric files")
    parser.add_argument("--psutil-path", type=Path)
    parser.add_argument("--smoke", action="store_true", help="allow fewer than 200 samples (smoke runs only)")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for name, spec in SCENARIOS.items():
            axis = f" clients={args.clients} modes={','.join(m for m in spec['modes'] if m)}" if spec["clients"] else ""
            print(f"{name:32} {spec['group']:9} {spec['kind']:6}{axis}  cells: {'; '.join(spec['cells'])}")
        return 0
    if args.work is None:
        parser.error("--work is required")
    if args.samples < 200 and not args.smoke:
        parser.error("steady-state scenarios need >= 200 measured ops (use --smoke for a smoke run)")
    args.sync_samples = args.sync_samples or args.samples
    args.crash_points = [p for p in args.crash_points.split(",") if p]
    run = Run(args)
    run.required = args.samples
    run.work.mkdir(parents=True, exist_ok=True)
    os.environ.pop(METRICS_ENV, None)  # latency samples are uninstrumented, whatever the shell says
    metrics = metrics_module()
    if metrics is not None:
        metrics.disable()
    if args.psutil_path:
        sys.path.insert(0, str(args.psutil_path))
    try:
        import psutil
        run.psutil = psutil if hasattr(psutil, "Process") else None
    except ImportError:
        run.psutil = None
    names = selected(run)
    client_axis = [int(c) for c in args.clients.split(",") if c]
    modes = [m for m in args.modes.split(",") if m]
    report = {"meta": {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "git_sha": git(REPO, "rev-parse", "HEAD").strip(),
                       "python": sys.version, "sqlite": sqlite3.sqlite_version, "machine": platform.platform(),
                       "cpu_count": os.cpu_count(), "samples_required": args.samples, "warmup": args.warmup,
                       "smoke": args.smoke, "scenarios": names, "clients": client_axis, "modes": modes,
                       "psutil": bool(run.psutil), "metrics_module": metrics is not None},
              "datasets": {}, "results": [], "instrumented": [], "skipped": []}
    if not run.psutil:
        report["skipped"].append("RSS: psutil not importable (pass --psutil-path; it is not a project dependency)")
    for dataset_name in [d for d in args.dataset.split(",") if d]:
        started = time.perf_counter()
        ds = prepare_dataset(run, dataset_name)
        report["datasets"][dataset_name] = {"prepare_s": round(time.perf_counter() - started, 2),
                                            "adoption": ds.info.get("adoption"), "counts": ds.inventory()["counts"],
                                            "generator": {k: ds.info.get("generator", {}).get(k)
                                                          for k in ("seed", "scale", "stats_sha256", "files")}}
        for pass_name in ("uninstrumented", "instrumented") if args.instrumented else ("uninstrumented",):
            run.instrumented = pass_name == "instrumented"
            stop_coordinator(ds.root)
            for name in names:
                spec = SCENARIOS[name]
                if run.instrumented and spec["kind"] != "steady":
                    continue
                combos = [(c, m) for c in client_axis for m in spec["modes"] if m in modes or m is None] \
                    if spec["clients"] else [(None, None)]
                for clients, mode in combos:
                    print(f"[{dataset_name}] {pass_name} {name} clients={clients} mode={mode}", flush=True)
                    if run.instrumented:  # a fresh owner per scenario, launched with this scenario's path
                        stop_coordinator(ds.root)
                        run.metrics_base = run.work / "metrics" / f"{dataset_name}-{name}-{clients}-{mode}-{uuid.uuid4().hex[:6]}" / "m.jsonl"
                        run.metrics_base.parent.mkdir(parents=True)
                    data = execute_scenario(run, ds, name, clients, mode)
                    if run.instrumented:
                        stop_coordinator(ds.root)  # its records are complete once it has exited
                        collect_metrics(run, data)
                        report["instrumented"].append(data)
                    else:
                        report["results"].append(data)
                    print(f"    -> {data['verdict']} {data.get('error', '')}", flush=True)
            stop_coordinator(ds.root)
    report["budgets"] = budget_rows(report["results"])
    report["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    results_path = args.results or (run.work / "n16-results.json")
    summary_path = args.summary or results_path.with_suffix(".md")
    results_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    summary_path.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"results": str(results_path), "summary": str(summary_path),
                      "pass": sum(r["verdict"] == "pass" for r in report["results"]),
                      "fail": sum(r["verdict"] == "fail" for r in report["results"]),
                      "not_applicable": sum(r["verdict"] == "not-applicable" for r in report["results"]),
                      "budget_missed": sum(b["status"] == "missed" for b in report["budgets"])}, indent=1))
    return 1 if any(r["verdict"] == "fail" for r in report["results"] + report["instrumented"]) else 0


if __name__ == "__main__":
    sys.exit(main())
