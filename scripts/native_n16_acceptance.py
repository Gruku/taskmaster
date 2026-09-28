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
import ast
import bisect
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
import socket
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
# A write is acknowledged only by a POSITIVE answer: the tool's own success text (or JSON `ok`) and,
# when the op must commit, a commit sequence. Anything else - an unknown sentence, a stripped suffix,
# "(not persisted)" - is a failure, never a silent pass.
SUCCESS = {
    "backlog_update_task": r"^(Updated|No change to) `",
    "backlog_add_task": r"^Added `",
    "backlog_archive_task": r"^Archived `",
    "backlog_link": r"^ok: ",
    # A fresh pick, or a task already in progress that this session now holds.
    "backlog_pick_task": r"^(Picked `[^`]+` .*\(locked to this session\)|Already in progress: `)",
    "backlog_batch_update": "json-ok",
    "backlog_claim": "json-ok",
}
NOT_PERSISTED = "(not persisted)"
# A native write whose value already matched commits nothing and says so explicitly.
UNCHANGED = re.compile(r"^No change to `|→ unchanged \(already `")


def _json(result):
    if isinstance(result, str) and result.lstrip()[:1] == "{":
        try:
            value = json.loads(result)
            return value if isinstance(value, dict) else None
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


def classify(result, *, success=None, commit="none", noop_ok=False, requested=None) -> tuple[bool, str | None]:
    """(ok, error). `success`: a regex the answer must match, "json-ok" for a JSON {"ok": true}
    answer, or None (reads: any non-empty answer that is not an error). `commit`: "required" (the
    answer must carry a commit sequence), "optional" or "none". `requested`: the value the op wrote,
    which an accepted no-op answer must name."""
    payload = _json(result)
    if isinstance(result, str) and result.lstrip().startswith("Error"):
        return False, result.lstrip()[:300]
    if payload is not None and (payload.get("error") or payload.get("ok") is False):
        return False, str(payload.get("error") or payload.get("message") or "ok=false")[:300]
    if result is None or (isinstance(result, str) and not result.strip()):
        return False, "empty answer"
    if isinstance(result, str) and NOT_PERSISTED in result:
        return False, "answer says (not persisted)"
    if isinstance(result, str) and UNCHANGED.search(result):
        # An unchanged value commits nothing and the native tool says so. That is accepted only
        # where a no-op is the expected outcome, never where a commit is required, only without a
        # sequence, and only naming the value this op asked for (the value the store kept at
        # commit); the scenario verifies the stored value separately.
        if not noop_ok or commit == "required" or seq_of(result) is not None:
            return False, "answer says unchanged"
        if requested is None or f"already `{requested}`" not in result:
            return False, "unchanged answer does not name the requested value"
        return True, None
    if success == "json-ok":
        if payload is None or payload.get("ok") is not True:
            return False, "unrecognized answer (expected JSON ok=true)"
    elif success is not None and not (isinstance(result, str) and re.search(success, result, re.S)):
        return False, "unrecognized answer (no success text)"
    if commit == "required" and seq_of(result) is None:
        return False, "acknowledged without a commit sequence"
    return True, None


def seq_of(result) -> int | None:
    if isinstance(result, str):
        found = SEQ.findall(result)
        if found:
            return int(found[-1])
    payload = _json(result)
    if payload is not None:
        for value in (payload.get("seq"), (payload.get("receipt") or {}).get("commit_seq")):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def redact(text) -> str | None:
    """An error for the results file without authored text: its leading type/code and a hash."""
    if text is None:
        return None
    text = str(text)
    head = re.match(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Unavailable|Conflict|Refused)?)(?=[:\s])", text)
    code = head.group(1) if head else "error"
    return f"{code[:40]}#{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:10]}"


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

    def high_water(self):
        with closing(sqlite3.connect(f"{(self.root / '.taskmaster/local/store.db').as_uri()}?mode=ro", uri=True,
                                     timeout=30)) as c:
            return int(c.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])

    def revision_of(self, kind, ident):
        with closing(sqlite3.connect(f"{(self.root / '.taskmaster/local/store.db').as_uri()}?mode=ro", uri=True,
                                     timeout=30)) as c:
            row = c.execute("SELECT revision FROM entity_core WHERE kind=? AND public_id=? AND deleted=0",
                            (kind, ident)).fetchone()
            return row[0] if row else None

    def run(self, op) -> dict:
        kind = op["t"]
        record = {"op": op["label"], "measured": op.get("measured", True), "expect": op.get("expect", "ok"),
                  "warmup": op.get("warmup", False)}
        if kind == "tool":
            kwargs = dict(op["kw"])
            if op.get("archive_created") is not None:
                kwargs["task_id"] = self.created[op["archive_created"]] if op["archive_created"] < len(self.created) else "missing-created"
            if op.get("noop_ok"):  # brackets a no-op answer in store time, outside the timed call
                record["hw_before"] = self.high_water()
            started = time.perf_counter()
            try:
                result = getattr(self.bs(), op["tool"])(**kwargs)
                ok, error = classify(result, success=op.get("success", SUCCESS.get(op["tool"]) if op.get("expect", "ok") != "read" else None),
                                     commit=op.get("commit", "none"), noop_ok=op.get("noop_ok", False),
                                     requested=kwargs.get("value"))
                if ok and isinstance(result, str) and UNCHANGED.search(result):
                    record["noop"] = True
                elif ok and op.get("noop_ok") and op.get("link") and seq_of(result) is None:
                    record["noop"] = True  # the link state already matched: check_noops verifies the store
            except Exception as exc:  # noqa: BLE001 - an exception is an outcome to report
                result, ok, error = None, False, f"{type(exc).__name__}: {exc}"[:300]
            record["ms"] = (time.perf_counter() - started) * 1000
            if record.get("noop"):
                record["hw_after"] = self.high_water()
            record.update(ok=ok, error=error, seq=seq_of(result), bytes=len(result) if isinstance(result, str) else None)
            if ok and op["tool"] == "backlog_add_task":
                found = ADDED.search(result or "")
                if found:
                    record["id"] = found.group(1)
                    self.created.append(found.group(1))
            for key in ("check", "composite", "link"):
                if op.get(key):
                    record[key] = op[key]
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
                          request_id=op["request_id"], scope=op["scope"],
                          conflict=any(e.startswith("Conflict") for e in errors),
                          seq=receipts[0]["commit_seq"] if receipts else None)
            if op.get("check"):
                record["check"] = op["check"]
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
    records = []
    if spec.get("prime"):
        # Imports, snapshot and coordinator handshake: not measured, but kept as a warmup record, so a
        # "first call after process start fails" defect fails no_warmup_errors instead of vanishing.
        primed = worker.run(spec["prime"])
        primed.update(w=spec["worker"], n=-1, t=time.time(), warmup=True)
        records.append(primed)
    Path(spec["ready"]).write_text(str(os.getpid()), encoding="utf-8")
    barrier = Path(spec["barrier"])
    deadline = time.monotonic() + 600
    while not barrier.exists():
        if time.monotonic() > deadline:
            return 3
        time.sleep(0.005)
    stop = Path(spec["stop"]) if spec.get("stop") else None
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
        record.update(w=spec["worker"], n=len(records) - bool(spec.get("prime")), t=time.time())
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


WORK_MARKER = ".n16-run"


def claim_work(work: Path) -> None:
    """Mark --work for this run, or refuse it. Scenarios write into the prepared datasets and the
    seeded op plan repeats its values, so a second run on the same datasets meets its own earlier
    writes (required commits answer "No change") - a false failure, never evidence. Nothing is
    regenerated silently: a used --work is refused and the caller picks a fresh one."""
    work = work.resolve()
    used = [p.name for p in sorted(work.glob("ds-*"))] if work.is_dir() else []
    if (work / WORK_MARKER).exists() or used:
        raise Refused(f"--work {work} already holds a prior run's mutated datasets "
                      f"({', '.join(used) or WORK_MARKER}); use a fresh --work")
    work.mkdir(parents=True, exist_ok=True)
    (work / WORK_MARKER).write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                "pid": os.getpid()}), encoding="utf-8")


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
    tasks, orphans = inventory_tasks(entities)
    words = sorted({w for _, d in tasks[:400] for w in re.findall(r"[A-Za-z]{5,}", str(d.get("title", ""))).__iter__()})
    anchors = sorted({a for _, d in tasks for a in inventory_anchors(d.get("anchors"))})
    return {
        "tasks": [i for i, _ in tasks],
        "task_priority": {i: d.get("priority") for i, d in tasks},
        "task_epic": {i: d.get("epic") for i, d in tasks},
        "epics": sorted({i for (k, i), (d, b, a) in entities.items() if k == "epic" and not a
                         and d.get("status") not in ("archived", "done")}),
        "phases": sorted(i for (k, i), (d, b, a) in entities.items() if k == "phase" and not a),
        "link_sources": sorted(i for (k, i), (d, b, a) in entities.items() if k in ("issue", "handover") and not a
                               and re.match(r"^(ISS-|IDEA-|\d{4}-\d{2}-\d{2}-[a-z0-9\-]+$)", i)),
        "link_targets": sorted(i for (k, i), (d, b, a) in entities.items() if k in ("idea", "issue") and not a),
        "entity_kind": {i: k for (k, i) in entities if k in ("issue", "handover", "idea")},
        "anchors": anchors[:2000] or ["src/n16/a.py", "src/n16/b.py"],
        "words": words[:500] or ["alpha"],
        "counts": {k: sum(1 for (kk, _) in entities if kk == k) for k in {k for k, _ in entities}},
        "notes": [f"{orphans} task(s) without an epic or status excluded: the tools answer 'not found' for them"]
        if orphans else [],
    }


def inventory_tasks(entities) -> tuple[list, int]:
    """Open tasks the tools can resolve, and how many were excluded as orphans: a task file with no
    backlog row has no epic or status, and every tool (legacy too) answers "not found" for it."""
    tasks, orphans = [], 0
    for (kind, ident), (doc, _body, archived) in sorted(entities.items()):
        if kind != "task" or archived or doc.get("status") in ("archived", "done"):
            continue
        if not doc.get("epic") or not doc.get("status"):
            orphans += 1
            continue
        tasks.append((ident, doc))
    return tasks, orphans


def inventory_anchors(stored) -> list:
    """The anchors a planner may sample: only values the tool stores back unchanged.

    The tool splits a written value on commas, strips, and drops blanks, so an anchor that is
    blank or holds a comma (a real one on the CodeMaestro copy is ",") cannot round-trip. Some
    legacy anchors are one stringified Python list: parsed as a whole, never sampled as fragments.
    """
    if isinstance(stored, str):
        text = stored.strip()
        if text.startswith("["):
            try:
                stored = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return []
        else:
            stored = text.split(",")
    if not isinstance(stored, (list, tuple)):
        return []
    items = [item.strip() for item in stored if isinstance(item, str)]
    return [item for item in items if item and not any(ch in item for ch in ",[]\"'")]


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
            for process, log in processes:
                if process.poll() is None:
                    kill_tree(process)
                log.close()
            raise RuntimeError(f"client process(es) died or stalled before the barrier (exits "
                               f"{[p.poll() for p, _ in processes]}); see {scenario_dir}")
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
                     "skipped_cells": [], "verdict": None}

    def check(self, name, ok, **detail):
        self.data["checks"].append({"check": name, "ok": bool(ok), **{k: v for k, v in detail.items() if v is not None}})
        return ok

    def note(self, text):
        self.data["notes"].append(text)

    def precondition(self, reason):
        """The dataset cannot exercise this scenario (e.g. the product rightly refuses it): reported as
        its own verdict, never a pass and never a correctness failure, and never budget evidence."""
        self.data["precondition"] = reason

    def skip(self, cell, reason):
        """A matrix cell this scenario could not exercise: reported under "Not run", never as a pass."""
        self.data["skipped_cells"].append({"cell": cell, "reason": reason})

    def measure(self, label, seconds_values, *, worst=None):
        if seconds_values:
            dist = distribution(seconds_values)
            dist = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in dist.items()}
            if worst is not None:
                dist["worst"] = worst
            self.data["distributions"][label] = dist

    def from_records(self, records, *, samples_required):
        """Latency from SUCCESSFUL measured ops only (for an expected-error op kind, the refusals);
        failures are counted separately and never enter a distribution or a budget row."""
        by_op = {}
        for record in records:
            if record.get("measured", True):
                by_op.setdefault(record["op"], []).append(record)
        for op, items in sorted(by_op.items()):
            expected_error = all(r.get("expect") == "error" for r in items)
            good = [r for r in items if (not r.get("ok")) == expected_error]
            if good:
                worst = max(good, key=lambda r: r["ms"])
                self.measure(op, [r["ms"] / 1000 for r in good],
                             worst={"ms": round(worst["ms"], 3), "worker": worst.get("w"), "n": worst.get("n")})
            errors = [r for r in items if not r.get("ok")]
            unexpected = [r for r in errors if r.get("expect", "ok") not in ("error", "conflict-or-ok")]
            self.data["errors"][op] = {"total": len(errors), "unexpected": len(unexpected), "measured": len(items),
                                       "examples": sorted({redact(r.get("error")) for r in unexpected})[:3]}
            if samples_required and self.data["kind"] == "steady":
                self.check(f"samples[{op}]>={samples_required}", len(good) >= samples_required, measured=len(good))
        return by_op

    def finish(self):
        checks = self.data["checks"]
        if self.data.get("precondition") and all(c["ok"] for c in checks):
            self.data["verdict"] = "precondition"
        elif not checks:
            self.data["verdict"] = "skipped"
            if not self.data["skipped_cells"]:
                self.skip("; ".join(self.data["cells"]) or self.data["scenario"], "no correctness check ran")
        else:
            self.data["verdict"] = "pass" if all(c["ok"] for c in checks) else "fail"
        return self.data


def norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def claim_norm(field, value) -> str:
    """A value as the tool would store it, for comparison with the store: an anchors value is split
    on commas, stripped, and blanks dropped (native `task.update`)."""
    if field == "anchors" and isinstance(value, str):
        return ",".join(item.strip() for item in value.split(",") if item.strip())
    return norm(value)


def value_as_of(connection, kind, ident, field, seq):
    """(found, value): the entity's `field` as of `seq`, from the latest event at or before it that
    set the field (a removal reads as None). found is False when no event ever set it."""
    rows = connection.execute("SELECT fields,after FROM domain_events WHERE kind=? AND id=? AND seq<=? "
                              "ORDER BY seq DESC", (kind, ident, seq))
    for fields, after in rows:
        if field in json.loads(fields or "[]"):
            try:
                after = json.loads(after) if after else {}
            except ValueError:
                after = {}
            return True, (after if isinstance(after, dict) else {}).get(field)
    return False, None


def commit_events(connection, seq):
    """Every event of the commit that holds `seq` (one command commit may write several)."""
    row = connection.execute("SELECT commit_key FROM domain_events WHERE seq=?", (seq,)).fetchone()
    if row is None:
        return None
    if row[0] is None:
        rows = connection.execute("SELECT kind,id,op,fields,after FROM domain_events WHERE seq=?", (seq,))
    else:
        rows = connection.execute("SELECT kind,id,op,fields,after FROM domain_events WHERE commit_key=?", (row[0],))
    events = []
    for kind, ident, op, fields, after in rows:
        try:
            after = json.loads(after) if after else {}
        except ValueError:
            after = {}
        events.append({"kind": kind, "id": ident, "op": op, "fields": json.loads(fields or "[]"),
                       "after": after if isinstance(after, dict) else {}})
    return events


def record_claims(record) -> list:
    """What an acknowledged op says it committed: [(kind, id, field or None, value or None)]."""
    claims = []
    if record.get("check"):
        claims.append(tuple(record["check"]))
    for member in record.get("composite") or ():
        claims.append(tuple(member))
    if record.get("id"):  # a created entity
        claims.append(("task", record["id"], None, None))
    if record.get("target"):  # an archive
        claims.append(("task", record["target"], "status", "archived"))
    if record.get("link"):  # the commit writes links on either end (a create may only repair the inverse)
        claims.append(("*", tuple(record["link"][:2]), "links", None))
    return claims


def check_acks(result: Result, root: Path, records, *, writes_expected=True):
    """No lost ack: the commit at every acknowledged sequence exists AND holds the op's own entity
    and value, and a raw command's durable receipt names that sequence. With writes expected, at
    least one acknowledged write must exist: a run that acknowledged nothing proves nothing."""
    acked = [r for r in records if r.get("ok") and r.get("seq")]
    missing, mismatched, receipts_bad = [], [], []
    with ro(root) as c:
        for r in acked:
            events = commit_events(c, r["seq"])
            if events is None:
                missing.append(r["seq"])
                continue
            for kind, ident, field, value in record_claims(r):
                hit = [e for e in events if (e["id"] in ident if isinstance(ident, tuple) else e["id"] == ident)
                       and kind in ("*", e["kind"])]
                if field is not None and value is not None and hit:
                    # Judge the value AS OF the acked seq, not that commit's diff: a same-value write
                    # after the minute rolls over commits only `last_referenced`, truthfully.
                    found, stored = value_as_of(c, hit[0]["kind"], ident, field, r["seq"])
                    if not found and not value_as_of(c, hit[0]["kind"], ident, field, 1 << 62)[0]:
                        stored = field_values(root, hit[0]["kind"], [ident], field)[ident]  # never evented
                    elif not found:
                        stored = None
                    if claim_norm(field, stored) != claim_norm(field, value):
                        hit = []
                elif field is not None:
                    hit = [e for e in hit if field in e["fields"]]
                if not hit:
                    mismatched.append({"seq": r["seq"], "field": field})
            if r.get("request_id") and r.get("scope"):
                row = c.execute("SELECT commit_seq FROM command_receipts WHERE caller_scope=? AND request_id=?",
                                (r["scope"], r["request_id"])).fetchone()
                if row is None or row[0] != r["seq"]:
                    receipts_bad.append(r["request_id"])
    result.check("no_lost_ack", not missing and not mismatched and not receipts_bad and bool(acked or not writes_expected),
                 acked=len(acked), missing=missing[:10] or None, mismatched=mismatched[:5] or None,
                 receipt_mismatch=len(receipts_bad) or None)
    return acked


def check_noops(result: Result, root: Path, records):
    """A no-op answer carries no sequence, so check_acks never sees it. It is accepted when it replays
    the value this same client last acknowledged for that field (no later write of its own changed
    it); otherwise the store must have held the requested value at some point during the call: as of
    the high-water read before it, or set by an event up to the one read after it."""
    last, unverified, checked = {}, [], 0
    link_noops = [r for r in records if r.get("link") and r.get("noop")]
    ledger = LinkLedger(root, {x for r in link_noops for x in r["link"][:2]}) if link_noops else None
    for r in sorted(records, key=lambda r: (r.get("w"), r.get("n"))):
        if r.get("link") and r.get("noop"):
            checked += 1
            if not link_noop_held_in_store(root, r, ledger):
                unverified.append({"id": r["link"][0], "field": "links", "w": r.get("w"), "n": r.get("n")})
            continue
        check = r.get("check")
        if not check or check[2] is None:
            continue
        key = (r.get("w"), check[0], check[1], check[2])
        if r.get("noop"):
            checked += 1
            if last.get(key) != claim_norm(check[2], check[3]) and not noop_held_in_store(root, r):
                unverified.append({"id": check[1], "field": check[2], "w": r.get("w"), "n": r.get("n")})
                last.pop(key, None)
                continue
            last[key] = claim_norm(check[2], check[3])
        elif r.get("ok") and r.get("seq"):
            last[key] = claim_norm(check[2], check[3])
        else:
            last.pop(key, None)  # a failed write's effect is unknown
    result.check("noop_answers_verified", not unverified, noops=checked, unverified=len(unverified) or None,
                 examples=unverified[:5] or None)


LINK_TYPE = "relates_to"
EVER = 1 << 62


def effective_links(doc: dict, kind: str) -> list:
    """The links the tools read: the stored `links`, or those synthesized from legacy fields."""
    from taskmaster import taskmaster_v3 as v3
    doc = dict(doc)
    v3._fallback_links_if_absent(doc, kind)
    return v3.entity_links(doc)


class LinkLedger:
    """Each entity's effective links after every one of its events (the documents folded from
    domain_events), so a link state can be read as of any sequence."""

    def __init__(self, root: Path, ids):
        ids = sorted(set(ids))
        rows = []
        with ro(root) as c:
            for at in range(0, len(ids), 500):
                part = ids[at:at + 500]
                rows += c.execute("SELECT seq,commit_key,kind,id,fields,after FROM domain_events WHERE id IN "
                                  f"({','.join('?' * len(part))})", part).fetchall()
        docs, self.points = {}, {}
        for seq, key, kind, ident, fields, after in sorted(rows):
            fields = json.loads(fields or "[]")
            try:
                after = json.loads(after) if after else {}
            except ValueError:
                after = {}
            after = after if isinstance(after, dict) else {}
            doc = docs.setdefault(ident, {})
            for field in fields:
                if field in after:
                    doc[field] = after[field]
                else:
                    doc.pop(field, None)
            links = frozenset((l.get("type"), l.get("target")) for l in effective_links(doc, kind) if isinstance(l, dict))
            point = self.points.setdefault(ident, {"seq": [], "links": [], "commit": [], "touched": []})
            point["seq"].append(seq)
            point["links"].append(links)
            point["commit"].append(key if key is not None else f"seq:{seq}")
            point["touched"].append("links" in fields)

    def as_of(self, ident, seq):
        """The entity's effective links as of `seq`, or None when the store held no event of it yet."""
        point = self.points.get(ident)
        at = bisect.bisect_right(point["seq"], seq) - 1 if point else -1
        return point["links"][at] if at >= 0 else None

    def pair_as_of(self, source, target, seq):
        """(source links target, target links back) as of `seq`; None when either end is unknown."""
        forward, backward = self.as_of(source, seq), self.as_of(target, seq)
        if forward is None or backward is None:
            return None
        return (LINK_TYPE, target) in forward, (LINK_TYPE, source) in backward

    def seqs_between(self, ident, low, high):
        return [q for q in (self.points.get(ident) or {"seq": []})["seq"] if low < q <= high]


def link_noop_held_in_store(root: Path, record, ledger=None) -> bool:
    """A link no-op (create of a present link, remove of a missing one) is true only if both ends held
    that state together at some point during the call: as of the high-water read before it, or after
    an event of either end up to the one read after it. An unknown entity vouches for nothing."""
    before, after = record.get("hw_before"), record.get("hw_after")
    if not isinstance(before, int) or not isinstance(after, int):
        return False
    source, target, action = record["link"][:3]
    ledger = ledger or LinkLedger(root, (source, target))
    wanted = (action == "create",) * 2
    points = [before] + sorted(set(ledger.seqs_between(source, before, after) + ledger.seqs_between(target, before, after)))
    return any(ledger.pair_as_of(source, target, point) == wanted for point in points)


def check_link_writes(result: Result, root: Path, records, *, ids, before):
    """Link writes judged on BOTH ends (the tool writes the inverse on the target):
    - link_acks_hold: as of the end of its commit, an acked create has the link and its inverse, an
      acked remove has neither;
    - link_changes_explained: every change to a scenario entity's links after `before` is exactly one
      relates_to pair, made by a commit an op naming that pair (and action) acknowledged - an unacked
      commit that drops or adds a link is a lost update;
    - final_link_state: every create was followed by its remove, so no touched pair is left, either way."""
    link_records = [r for r in records if r.get("link")]
    ledger = LinkLedger(root, set(ids) | {x for r in link_records for x in r["link"][:2]})
    acked = [r for r in link_records if r.get("ok") and r.get("seq")]
    by_commit, wrong = {}, []
    with ro(root) as c:
        for r in acked:
            row = c.execute("SELECT commit_key FROM domain_events WHERE seq=?", (r["seq"],)).fetchone()
            if row is None:
                wrong.append({"seq": r["seq"], "w": r.get("w"), "n": r.get("n")})
                continue
            key = row[0] if row[0] is not None else f"seq:{r['seq']}"
            end = (c.execute("SELECT MAX(seq) FROM domain_events WHERE commit_key=?", (row[0],)).fetchone()[0]
                   if row[0] is not None else r["seq"])
            source, target, action = r["link"][:3]
            by_commit.setdefault(key, set()).add((frozenset((source, target)), action))
            if ledger.pair_as_of(source, target, end) != ((action == "create",) * 2):
                wrong.append({"seq": r["seq"], "w": r.get("w"), "n": r.get("n")})
    result.check("link_acks_hold", not wrong and bool(acked), acked=len(acked), wrong=len(wrong) or None,
                 examples=wrong[:5] or None)
    unexplained, changes = [], 0
    for ident, point in ledger.points.items():
        for at, seq in enumerate(point["seq"]):
            if seq <= before or not point["touched"][at] or at == 0:
                continue
            diff = point["links"][at - 1] ^ point["links"][at]
            if not diff:
                continue
            changes += 1
            (link_type, other), = diff if len(diff) == 1 else ((None, None),)
            action = "create" if (link_type, other) in point["links"][at] else "remove"
            if link_type != LINK_TYPE or (frozenset((ident, other)), action) not in by_commit.get(point["commit"][at], ()):
                unexplained.append({"id": ident, "seq": seq, "changed": len(diff)})
    result.check("link_changes_explained", not unexplained, changes=changes, unexplained=len(unexplained) or None,
                 examples=unexplained[:5] or None)
    pairs = {frozenset(r["link"][:2]): tuple(r["link"][:2]) for r in link_records}
    left = [pair for pair in pairs.values() if (ledger.pair_as_of(*pair, EVER) or (True, True)) != (False, False)]
    result.check("final_link_state", not left, pairs=len(pairs), left=len(left))


def noop_held_in_store(root: Path, record) -> bool:
    before, after = record.get("hw_before"), record.get("hw_after")
    if not isinstance(before, int) or not isinstance(after, int):
        return False
    kind, ident, field, value = record["check"]
    wanted = claim_norm(field, value)
    with ro(root) as c:
        found, stored = value_as_of(c, kind, ident, field, before)
        if found and claim_norm(field, stored) == wanted:
            return True
        for fields, event_after in c.execute("SELECT fields,after FROM domain_events WHERE kind=? AND id=? "
                                             "AND seq>? AND seq<=?", (kind, ident, before, after)):
            if field in json.loads(fields or "[]"):
                try:
                    doc = json.loads(event_after) if event_after else {}
                except ValueError:
                    doc = {}
                if isinstance(doc, dict) and claim_norm(field, doc.get(field)) == wanted:
                    return True
    return False


def check_unexpected(result: Result, records):
    # Warmup ops stay out of latency and of this check, but a failing warmup is a real (cold-start)
    # defect: counted and judged by its own check.
    bad = [r for r in records if not r.get("ok") and not r.get("warmup")
           and r.get("expect", "ok") not in ("error", "conflict-or-ok")]
    result.check("no_unexpected_errors", not bad, count=len(bad),
                 examples=sorted({redact(r.get("error")) for r in bad})[:3] or None)
    warm = [r for r in records if not r.get("ok") and r.get("warmup")
            and r.get("expect", "ok") not in ("error", "conflict-or-ok")]
    result.data["warmup_errors"] = len(warm)
    result.check("no_warmup_errors", not warm, count=len(warm),
                 examples=sorted({redact(r.get("error")) for r in warm})[:3] or None)


def check_workers(result: Result, meta):
    result.check("workers_exited_cleanly", all(code == 0 for code in meta["exits"]), exits=meta["exits"])


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
    """The committed value of each written field is the one the highest acknowledged seq wrote;
    with field writes in the run, at least one field must have been checked."""
    last = {}
    planned = 0
    for r in records:
        check = r.get("check")
        if check:
            planned += 1
        if r.get("ok") and r.get("seq") and check:
            key = (check[0], check[1], check[2])
            if key not in last or r["seq"] > last[key][0]:
                last[key] = (r["seq"], check[3])
    wrong = []
    for (kind, ident, field), (seq, value) in sorted(last.items()):
        current = field_values(root, kind, [ident], field)[ident]
        if claim_norm(field, current) != claim_norm(field, value):
            wrong.append({"id": ident, "field": field, "seq": seq})
    result.check("acked_value_is_final", not wrong and bool(last or not planned), fields=len(last),
                 wrong=wrong[:5] or None)


def commit_count(root: Path) -> int:
    with ro(root) as c:
        return int(c.execute("SELECT COUNT(*) FROM command_commits").fetchone()[0])


def task_ids(root: Path) -> set:
    with ro(root) as c:
        return {row[0] for row in c.execute("SELECT public_id FROM entity_core WHERE kind='task' AND deleted=0")}


# ════════════════════════════════════════════════════════════════════════════
# Op planning
# ════════════════════════════════════════════════════════════════════════════
PRIORITIES = ("low", "medium", "high", "critical")


def text_value(rng, worker, index, tag):
    return f"n16 {tag} w{worker} op{index} {uuid.UUID(int=rng.getrandbits(128)).hex[:10]}"


def disjoint_link_pairs(sources, targets, links, workers) -> list:
    """Every (source, target) pair a disjoint link run may use, in a fixed order the clients stripe.
    Unordered: the tool writes the inverse link on the target, so A->B and B->A are one pair. A pair
    already linked either way is left out (its create would be a no-op). Refused when the dataset
    cannot give each client a pair of its own: overlapping clients would race, which is `same` mode."""
    seen, pairs = set(), []
    for source in sources:
        for target in targets:
            key = frozenset((source, target))
            if source == target or key in seen:
                continue
            seen.add(key)
            if target in (links.get(source) or ()) or source in (links.get(target) or ()):
                continue
            pairs.append((source, target))
    if len(pairs) < workers:
        raise Refused(f"disjoint link needs one unlinked source/target pair per client: dataset has "
                      f"{len(pairs)} for {workers} clients")
    return pairs


def plan_write(kind: str, inv: dict, *, worker: int, workers: int, mode: str, count: int, warmup: int, rng,
               scope: str, current=None) -> list:
    """One client's ops. `current` holds the committed values before the run
    ({"priority"|"anchors"|"phase"|"links": {id: value}}), so every disjoint-entity write changes its
    value and must commit (a sequence is required); same-entity writes race peers and may no-op."""
    tasks = inv["tasks"]
    if not tasks:
        raise Refused("dataset has no open tasks to write")
    current = {k: dict(v) for k, v in (current or {}).items()}
    if mode == "same":
        own = [tasks[0]]
    else:
        # Disjoint: each client owns its own stripe of tasks; a stripe borrowed from a peer is not disjoint.
        if kind not in ("link", "create", "archive") and len(tasks) < workers:
            raise Refused(f"disjoint {kind} needs one task per client: dataset has {len(tasks)} for {workers} clients")
        own = tasks[worker::workers][:max(1, min(50, len(tasks) // max(1, workers)))]
    ops = []
    total = count + warmup
    epics = inv["epics"] or [inv["task_epic"][tasks[0]]]
    epic = epics[0] if mode == "same" else epics[worker % len(epics)]
    phases = inv["phases"]
    sources = inv["link_sources"] or []
    targets = inv["link_targets"] or []
    must = "required" if mode != "same" else "optional"
    if kind == "link" and total % 2:
        total += 1  # every create is followed by its remove: the final state is known
    if kind == "link" and mode != "same":
        if not sources or not targets:
            raise Refused("dataset has no link-capable issue/handover/idea ids")
        mine = disjoint_link_pairs(sources, targets, current.get("links", {}), workers)[worker::workers]
    for index in range(total):
        measured = index >= warmup
        task = own[index % len(own)]
        label = f"write.{kind}"
        base = {"t": "tool", "label": label, "measured": measured, "warmup": not measured}
        if kind in ("meta", "path", "membership"):
            field = {"meta": "priority", "path": "anchors", "membership": "phase"}[kind]
            now = norm(current.get(field, {}).get(task))
            if kind == "meta":
                choices = [v for v in PRIORITIES if v != now]
            elif kind == "path":
                choices = []
                for _ in range(8):
                    candidate = ",".join(sorted(set(rng.sample(inv["anchors"], min(2, len(inv["anchors"]))))))
                    if candidate != now:
                        choices = [candidate]
                        break
            else:
                if not phases:
                    raise Refused("dataset has no phases")
                choices = [v for v in phases + ([""] if len(phases) < 2 else []) if v != now]
            if not choices:
                raise Refused(f"no value different from the current {field}")
            value = choices[(index + worker) % len(choices)]
            current.setdefault(field, {})[task] = value
            ops.append(dict(base, tool="backlog_update_task", commit=must, noop_ok=mode == "same",
                            kw={"task_id": task, "field": field, "value": value}, check=["task", task, field, value]))
        elif kind == "prose":
            value = text_value(rng, worker, index, "prose")
            ops.append(dict(base, tool="backlog_update_task", commit="required",
                            kw={"task_id": task, "field": "next_step", "value": value},
                            check=["task", task, "next_step", value]))
        elif kind == "link":
            action = "create" if index % 2 == 0 else "remove"
            if mode == "same":
                # Peers race on one source and overlapping targets: a create of a link a peer just made (or a
                # remove of one a peer just removed) is a correct no-op, accepted only once the store confirms it.
                if not sources or not targets:
                    raise Refused("dataset has no link-capable issue/handover/idea ids")
                source = sources[0]
                linked = set(current.get("links", {}).get(source) or ())
                free = [t for t in targets if t != source and t not in linked]
                if not free:
                    raise Refused("no unlinked link target")
                target = free[(index // 2 + worker) % len(free)]
            else:
                source, target = mine[(index // 2) % len(mine)]
            ops.append(dict(base, tool="backlog_link", commit=must, noop_ok=mode == "same",
                            kw={"action": action, "source": source, "target": target, "type": "relates_to"},
                            link=[source, target, action]))
        elif kind == "create":
            ops.append(dict(base, tool="backlog_add_task", commit="required",
                            kw={"title": f"n16 create w{worker} c{index}", "epic": epic, "priority": "low",
                                "phase": phases[0] if phases else "", "notes": text_value(rng, worker, index, "create")}))
        elif kind == "archive":
            ops.append(dict(base, tool="backlog_add_task", label="setup.create", measured=False, commit="required",
                            kw={"title": f"n16 archive w{worker} c{index}", "epic": epic, "priority": "low",
                                "phase": phases[0] if phases else ""}))
        elif kind == "composite":
            # Two or three tasks: this client's own (padding from all tasks would borrow a peer's), or in
            # `same` mode the first tasks, which every client contends on.
            group = list(dict.fromkeys([own[(index + k) % len(own)] for k in range(3)] if mode != "same" else tasks[:3]))
            if len(group) < 2:
                raise Refused(f"{mode} composite needs two tasks per client: dataset has {len(tasks)} for {workers} clients")
            # Every client sends invalid composites (one in five, the first at index 2 or its last op).
            invalid = index % 5 == 2 or (total < 3 and index == total - 1)
            commands, checks = [], []
            for member in group:
                value = text_value(rng, worker, index, "composite")
                commands.append({"operation": "task.patch", "arguments": {"id": member, "set": {"next_step": value}}})
                checks.append(["task", member, "next_step", value])
            if invalid:
                commands.append({"operation": "task.patch", "arguments": {"id": f"n16-missing-{worker}-{index}",
                                                                           "set": {"next_step": "x"}}})
            op = dict(base, tool="backlog_batch_update", label="write.composite.invalid" if invalid else label,
                      expect="error" if invalid else "ok", commit="required", kw={"commands": commands, "atomic": True})
            op["composite" if not invalid else "invalid_composite"] = checks
            ops.append(op)
        elif kind == "noop":
            # Only targets whose stored priority is known: a no-op of an invented value is a write.
            known = [t for t in own if norm(current.get("priority", {}).get(t)) in PRIORITIES]
            if not known:
                raise Refused("no noop target with a known priority")
            task = known[index % len(known)]
            value = norm(current["priority"][task])
            ops.append(dict(base, tool="backlog_update_task", noop_ok=True, noop_check=["task", task, "priority", value],
                            kw={"task_id": task, "field": "priority", "value": value}))
        elif kind == "invalid":
            ops.append(dict(base, tool="backlog_update_task", expect="error",
                            kw={"task_id": task, "field": "status", "value": "n16-not-a-status"}))
        elif kind == "retry":
            request = f"n16-retry-{uuid.UUID(int=rng.getrandbits(128)).hex}"
            value = text_value(rng, worker, index, "retry")
            ops.append({"t": "raw", "label": label, "measured": measured, "warmup": not measured, "scope": scope, "request_id": request,
                        "operation": "task.patch", "repeat": 2, "check": ["task", task, "next_step", value],
                        "arguments": {"id": task, "set": {"next_step": value}}})
        elif kind == "cas":
            value = text_value(rng, worker, index, "cas")
            ops.append({"t": "raw", "label": label, "measured": measured, "warmup": not measured, "scope": f"{scope}-w{worker}",
                        "request_id": f"n16-cas-{worker}-{index}-{uuid.UUID(int=rng.getrandbits(128)).hex[:8]}",
                        "operation": "task.patch", "cas": True, "expect": "conflict-or-ok",
                        "check": ["task", task, "next_step", value],
                        "arguments": {"id": task, "set": {"next_step": value}}})
        else:
            raise ValueError(kind)
    if kind == "archive":
        for index in range(total):
            ops.append({"t": "tool", "tool": "backlog_archive_task", "label": "write.archive", "measured": index >= warmup,
                        "commit": "required", "archive_created": index, "kw": {"task_id": "", "reason": "deprecated"}})
    return ops


def plan_read(kind: str, inv: dict, *, count: int, warmup: int, rng, worker=0) -> list:
    tasks = inv["tasks"]
    ops = []
    for index in range(count + warmup):
        measured = index >= warmup
        task = rng.choice(tasks)
        if kind == "details":
            ops.append({"t": "tool", "tool": "backlog_get_task", "label": "read.details", "measured": measured, "warmup": not measured,
                        "kw": {"task_id": task}})
        elif kind == "search":
            ops.append({"t": "tool", "tool": "backlog_search", "label": "read.search", "measured": measured, "warmup": not measured,
                        "kw": {"query": rng.choice(inv["words"])}})
        elif kind == "context":
            ops.append({"t": "tool", "tool": "backlog_context", "label": "read.context", "measured": measured, "warmup": not measured,
                        "kw": {"focus": task, "scope": "task"}})
        elif kind in ("viewer.full", "viewer.unchanged", "viewer.delta"):
            mode = kind.split(".")[1]
            op = {"t": "viewer", "mode": mode, "label": f"read.{kind}", "measured": measured, "warmup": not measured}
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
        check_workers(res, meta)
        res.from_records(records, samples_required=run.required)
        check_unexpected(res, records)
        sizes = sorted(r["bytes"] for r in records if r.get("measured") and r.get("ok") and r.get("bytes") is not None)
        if sizes:
            res.data["output_bytes_p50"] = sizes[len(sizes) // 2]
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
        plan = plan_write("prose", inv, worker=w + 1, workers=writers + 1, mode="disjoint", count=50, warmup=0,
                          rng=run.rng, scope="n16")
        for op in plan:
            op["commit"] = "optional"  # a looping writer replays its values; a replay may be a no-op
            op["noop_ok"] = True
        plans.append(plan)
    records, meta = run_clients(run, ds.root, plans, label="read-during-writes", prime=PRIME, loop_until_first=True)
    res.data["run"] = meta
    reads = [r for r in records if r["op"].startswith("read.")]
    writes = [r for r in records if r["op"].startswith("write.")]
    res.from_records(reads, samples_required=run.required)
    res.from_records(writes, samples_required=0)
    res.data["writes_during"] = len(writes)
    check_workers(res, meta)
    res.check("writes_happened_during_reads", len(writes) > 0 and any(r.get("seq") for r in writes),
              writes=len(writes))
    check_unexpected(res, records)
    check_acks(res, ds.root, writes)
    check_noops(res, ds.root, writes)
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


def current_values(root: Path, inv: dict, kind: str, clients: int) -> dict:
    """The committed values the planner must differ from (earlier scenarios change them)."""
    ids = inv["tasks"][:clients * 50 + 50]
    out = {field: field_values(root, "task", ids, field) for field in ("priority", "anchors", "phase")}
    if kind == "link":
        ids = set(inv["link_sources"]) | set(inv["link_targets"])
        ledger = LinkLedger(root, ids)
        out["links"] = {ident: sorted(target for _, target in ledger.as_of(ident, EVER) or ()) for ident in ids}
    return out


def write_scenario(kind):
    def fn(run: Run, ds: Dataset, clients, mode):
        res = Result(f"write.{kind}", ds.name, clients=clients, mode=mode,
                     cells=[f"Writes: {kind}", f"Clients: {clients} {mode}"])
        inv = ds.inventory()
        current = current_values(ds.root, inv, kind, clients)
        count = per_client(run, clients)
        scope = f"n16-{kind}-{uuid.uuid4().hex[:6]}"
        plans = [plan_write(kind, inv, worker=w, workers=clients, mode=mode, count=count, warmup=run.args.warmup,
                            rng=run.rng, scope=scope, current=current) for w in range(clients)]
        if kind == "retry" and clients > 1:
            # Cross-process duplicates: odd workers replay their even neighbour's request ids concurrently.
            for w in range(1, clients, 2):
                plans[w] = json.loads(json.dumps(plans[w - 1]))
        before, commits_before, tasks_before = high_water(ds.root), commit_count(ds.root), task_ids(ds.root)
        records, meta = run_clients(run, ds.root, plans, label=f"write-{kind}", prime=PRIME)
        after, commits_after, tasks_after = high_water(ds.root), commit_count(ds.root), task_ids(ds.root)
        commits = commits_after - commits_before
        res.data["run"] = meta
        res.data["commits"] = commits
        check_workers(res, meta)
        measured = [r for r in records if r["op"].startswith("write.")]
        res.from_records(measured, samples_required=0)  # the total is checked below (mixed composites)
        res.data["total_measured"] = sum(1 for r in measured if r.get("measured"))
        if run.required:
            res.check(f"samples>={run.required}", res.data["total_measured"] >= run.required,
                      measured=res.data["total_measured"])
        if kind == "cas":
            bad = [r for r in records if not r.get("ok") and not r.get("conflict")]
            conflicts = sum(bool(r.get("conflict")) for r in records)
            res.check("conflicts_are_explicit", not bad, conflicts=conflicts, other_errors=len(bad),
                      examples=sorted({redact(r.get("error")) for r in bad})[:3] or None)
            if clients > 1 and mode == "same":
                res.check("contention_produced_conflicts", conflicts > 0, conflicts=conflicts)
            revisions = [a["revision"] for r in records if r.get("ok") for rc in r.get("receipts", [])
                         for a in rc["affected"] if a.get("revision") is not None]
            res.check("no_revision_committed_twice", len(revisions) == len(set(revisions)), successes=len(revisions))
        else:
            check_unexpected(res, records)
        acked = check_acks(res, ds.root, records, writes_expected=kind not in ("noop", "invalid"))
        check_noops(res, ds.root, records)
        distinct_acked = len({r["seq"] for r in acked})
        if kind == "link":  # every link commit is an acknowledged one: no unacked write slips in
            res.check("commits==distinct_acked_seqs", commits == distinct_acked, commits=commits, acked=distinct_acked)
        elif kind not in ("noop", "invalid", "retry"):
            res.check("commits>=distinct_acked_seqs", commits >= distinct_acked, commits=commits, acked=distinct_acked)
        if kind in ("meta", "prose", "path", "membership"):
            check_last_value(res, ds.root, records)
            res.data["race_noops"] = sum(bool(r.get("noop")) for r in records)
        if kind in ("create", "archive"):
            creates = [r for r in records if r.get("ok") and r["op"] in ("write.create", "setup.create")]
            created = [r["id"] for r in creates if r.get("id")]
            reused = sorted(set(created) & tasks_before)
            res.check("no_reused_id", len(created) == len(set(created)) and not reused and len(created) == len(creates),
                      created=len(created), ok_creates=len(creates), reused_existing=len(reused) or None)
            res.check("task_count_grew_by_ok_creates", len(tasks_after) - len(tasks_before) == len(creates),
                      grew=len(tasks_after) - len(tasks_before), ok_creates=len(creates))
            res.check("created_ids_committed", set(created) <= tasks_after,
                      missing=len(set(created) - tasks_after) or None)
        if kind == "archive":
            targets = [r.get("target") for r in records if r["op"] == "write.archive" and r.get("ok")]
            status = field_values(ds.root, "task", targets, "status")
            res.check("archived_are_archived", bool(targets) and all(v == "archived" for v in status.values()),
                      archived=len(targets), wrong=len([i for i, v in status.items() if v != "archived"]) or None)
        if kind == "link":
            check_link_writes(res, ds.root, records, ids=inv["link_sources"] + inv["link_targets"], before=before)
        if kind == "composite":
            valid_ok = [r for r in records if r["op"] == "write.composite" and r.get("ok")]
            invalid_ops = [op for plan in plans for op in plan if op.get("invalid_composite")]
            invalid_values = [c[3] for op in invalid_ops for c in op["invalid_composite"]]
            res.data["invalid_composites"] = len(invalid_ops)
            res.check("invalid_composites_sent", len(invalid_ops) > 0, invalid=len(invalid_ops))
            leaked = 0
            with ro(ds.root) as c:
                for value in invalid_values:
                    leaked += c.execute("SELECT COUNT(*) FROM domain_events WHERE seq>? AND after LIKE ?",
                                        (before, f"%{value}%")).fetchone()[0]
            res.check("no_partial_composite", leaked == 0 and commits == len(valid_ok), invalid=len(invalid_values),
                      leaked=leaked or None, commits=commits, ok_valid=len(valid_ok))
            refused = [r for r in records if r["op"] == "write.composite.invalid"]
            res.check("invalid_composite_refused", bool(refused) and all(not r.get("ok") for r in refused),
                      refused=len(refused))
        if kind == "noop":
            # An unchanged value commits nothing, except the tool's own `last_referenced` minute bump,
            # which is bookkeeping, not a domain change: recorded, and any other field fails.
            with ro(ds.root) as c:
                touched = [json.loads(row[0] or "[]") for row in c.execute(
                    "SELECT fields FROM domain_events WHERE seq>? AND seq<=?", (before, after))]
            others = sorted({f for fields in touched for f in fields} - {"last_referenced"})
            res.data["noop_bookkeeping_commits"] = len(touched)
            planned = {(op["noop_check"][1], op["noop_check"][3]) for plan in plans for op in plan if op.get("noop_check")}
            values = field_values(ds.root, "task", sorted({t for t, _ in planned}), "priority")
            changed = [t for t, v in planned if norm(values.get(t)) != norm(v)]
            res.check("noop_value_unchanged", bool(planned) and not changed, tasks=len(planned), changed=len(changed) or None)
            res.data["noop_answers_unchanged"] = sum(bool(r.get("noop")) for r in records)
            res.check("noop_changes_no_domain_field", not others, commits=after - before, fields=others or None)
        if kind == "invalid":
            res.check("no_commit", after == before and commits == 0, commits=commits)
            res.check("invalid_refused", bool(measured) and all(not r.get("ok") for r in measured), refused=len(measured))
        if kind == "retry":
            by_request = {}
            for r in records:
                for receipt in r.get("receipts", []):
                    by_request.setdefault(r["request_id"], set()).add(receipt["commit_seq"])
            duplicated = {k: v for k, v in by_request.items() if len(v) != 1}
            res.check("duplicate_requests_one_commit", bool(by_request) and not duplicated, requests=len(by_request),
                      duplicated=len(duplicated))
            res.check("commits_equal_requests", commits == len(by_request), commits=commits, requests=len(by_request))
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
    res = Result("core.command", ds.name, clients=1, cells=["DB command core"])
    target = run.work / "core" / f"{ds.name}-{uuid.uuid4().hex[:6]}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ro(ds.root) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    inv = ds.inventory()
    reads, writes, work, seqs = [], [], [], []
    metrics = metrics_module() if run.instrumented else None
    if metrics is not None:
        metrics.enable(run.metrics_base)
    try:
        core_loop(run, target, inv, reads, writes, work, seqs)
    finally:
        if metrics is not None:
            metrics.disable()
    res.measure("core.read", reads)
    res.measure("core.command", writes)
    res.data["max_work"] = {k: max(w.get(k, 0) for w in work) for k in (work[0] if work else {})}
    if run.required:
        res.check(f"samples>={run.required}", len(writes) >= run.required, measured=len(writes))
    res.check("core_commands_committed", len(writes) > 0 and len(seqs) == len(writes)
              and all(isinstance(q, int) for q in seqs) and len(set(seqs)) == len(seqs), commands=len(seqs))
    target.unlink(missing_ok=True)
    return res


def core_loop(run, target, inv, reads, writes, work, seqs):
    from taskmaster.native.commands import execute
    from taskmaster.native.migrate import encode
    from taskmaster.native.queries import Repository
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
                seqs.append(receipt.get("commit_seq"))


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


SYNC_SETTLE_S = 600  # bounded total for one sync retried under its own id while it answers pending


def sync_retryable(result) -> bool:
    """A pending sync the coordinator asks to be retried under the same id (e.g. its time budget ran
    out); a pending held by quarantined or drifting files is settled, not retryable."""
    return isinstance(result, dict) and result.get("state") == "pending" and any(
        "retry the same sync id" in str(n) for n in result.get("notices") or ())


def sync_settled(client, *, bound=SYNC_SETTLE_S, **kwargs):
    """(result, seconds, attempts): one sync, retried with the SAME request id while it answers a
    retryable pending, until it settles or `bound` seconds pass."""
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    attempts = 0
    while True:
        result = client.sync(caller_scope="n16-sync", request_id=request_id, **kwargs)
        attempts += 1
        if not sync_retryable(result) or time.perf_counter() - started >= bound:
            return result, time.perf_counter() - started, attempts


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
    if run.sync_required:
        res.check(f"samples>={run.sync_required}", len(times) >= run.sync_required, measured=len(times))
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
    times, lost, unsettled, summaries, retried = [], [], [], [], 0
    for index in range(run.args.sync_samples):
        task = inv["tasks"][(index * 7) % len(inv["tasks"])]
        marker = f"n16-external-edit-{uuid.uuid4().hex[:12]}"
        edit_task_file(ds.root, task, marker)
        # A first sync of a fresh copy can exhaust its budget and answer pending: that sync is retried
        # under its own id until it settles; only then is the edit judged.
        result, seconds, attempts = sync_settled(client)
        retried += attempts > 1
        times.append(seconds)
        summaries.append(dict(sync_summary(result), attempts=attempts))
        if marker not in body_of(ds.root, task):
            (unsettled if sync_retryable(result) else lost).append(task)
    res.measure("sync.dirty_one_file", times)
    if run.sync_required:
        res.check(f"samples>={run.sync_required}", len(times) >= run.sync_required, measured=len(times))
    res.data["sync"] = summaries[:3]
    res.data["retried_pending_syncs"] = retried
    res.check("external_edit_imported", not lost and not unsettled, edits=run.args.sync_samples, lost=len(lost),
              unsettled_after_bound=len(unsettled) or None)
    return res


@scenario("sync.conflict", group="sync", kind="check", cells=("Sync: conflict",))
def sync_conflict(run: Run, ds: Dataset, clients, mode):
    """A projection file edited by hand, then the store changes the same entity before any sync: the
    export must flag the file (not overwrite it), and after sync both edits survive or the import
    names the conflict for that file."""
    res = Result("sync.conflict", ds.name, kind="check", clients=1, cells=["Sync: conflict"])
    root = copy_project(run, ds.root, "sync-conflict")
    client = start_coordinator(run, root)
    inv = ds.inventory()
    task = inv["tasks"][3 % len(inv["tasks"])]
    client.flush(high_water(root))
    rel = f"tasks/{task}.md"
    path = root / ".taskmaster" / rel
    marker = f"n16-file-side-{uuid.uuid4().hex[:10]}"
    store_value = f"n16 store side {uuid.uuid4().hex[:10]}"
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + f"\n\n{marker}\n", encoding="utf-8")
    receipt = client.execute(envelope(root, task, store_value))["receipt"]
    flushed = client.flush(receipt["commit_seq"])
    flagged = [n for n in (flushed.get("notices") or []) if rel in str(n)]
    on_disk = path.read_text(encoding="utf-8")
    res.check("export_did_not_overwrite_hand_edit", marker in on_disk and store_value not in on_disk)
    res.check("export_flags_the_file", flushed.get("state") != "exported" and bool(flagged),
              state=flushed.get("state"), flagged=len(flagged))
    result, seconds = sync_once(client)
    summary = sync_summary(result)
    stored = body_of(root, task)
    named = [n for n in (result.get("notices") or []) if rel in str(n)] + \
        [i for i in (result.get("imports") or []) if i.get("file") == rel and i.get("state") == "conflict"]
    res.data["sync"] = summary
    res.check("store_edit_not_lost", store_value in stored)
    res.check("both_edits_kept_or_conflict_named", (store_value in stored and marker in stored) or bool(named),
              file_edit_in_store=marker in stored, named=len(named))
    res.measure("sync.conflict", [seconds])
    stop_coordinator(root)
    return res


def checkout_precondition(baseline, quarantined) -> str | None:
    """Why this copy cannot exercise managed checkouts, or None. Managed Git rightly refuses while
    projections are unsynchronized; when every unresolved path is a file the store had already
    quarantined before the run, that is the dataset's state, not a correctness failure."""
    if not isinstance(baseline, dict) or baseline.get("state") != "refused":
        return None
    if not str(baseline.get("reason", "")).startswith("projections are not synchronized"):
        return None
    unresolved = set((baseline.get("sync") or {}).get("unresolved") or ())
    if not unresolved or not unresolved <= set(quarantined):
        return None
    return (f"managed Git refuses: projections are not synchronized; {len(unresolved)} pre-existing "
            f"quarantined file(s) unresolved")


@scenario("sync.checkout", group="sync", kind="check", cells=("Sync: checkout/worktree",))
def sync_checkout(run: Run, ds: Dataset, clients, mode):
    """Linked worktree edit imported against its own bases; managed checkouts keep store and files
    coherent, and a checked-out file that differs from the published generation is explicit drift
    (N13), released with `take_published` without losing the store's value."""
    res = Result("sync.checkout", ds.name, kind="check", clients=1, cells=["Sync: checkout/worktree"])
    root = copy_project(run, ds.root, "sync-checkout")
    # Snapshot before the coordinator starts: a quarantine that appears during the run is a failure.
    preexisting = quarantined_files(root)
    client = start_coordinator(run, root)
    client.flush(high_water(root))
    started = time.perf_counter()
    baseline = client.git_run(kind="commit", message="n16 baseline", caller_scope="n16-git")
    res.measure("sync.managed_commit", [time.perf_counter() - started])
    unmet = checkout_precondition(baseline, preexisting)
    if unmet:
        res.precondition(unmet)
        stop_coordinator(root)
        return res
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
    """A deleted projection file is not a deletion: the entity stays and the file comes back."""
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
    client.flush(high_water(root))
    deadline = time.monotonic() + 15
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.25)
    present = field_values(root, "task", [task], "title")[task]
    res.data["sync"] = summary
    res.check("store_entity_kept", present not in (None, "<missing>"))
    res.check("file_restored_from_store", path.exists() and f"id: {task}" in path.read_text(encoding="utf-8"),
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


def send_and_drop(root: Path, env: dict) -> None:
    """Send one execute request and close the socket without reading the reply (a client that dies
    after sending). Deterministic, unlike a short timeout that may still receive the answer."""
    from taskmaster.coordinator.client import Client
    from taskmaster.coordinator.protocol import encode
    from taskmaster.native import contracts
    client = Client(root, autostart=False)
    record = client._discovery()
    request, _ = contracts.validate(env)
    body = encode(dict(identity=dict(client.identity, nonce=record["nonce"]), method="execute", envelope=request,
                       visibility="native"))
    head = (f"POST /rpc HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            f"Authorization: Bearer {record['token']}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n")
    sock = socket.create_connection(("127.0.0.1", record["port"]), timeout=5)
    try:
        sock.sendall(head.encode("ascii") + body)
    finally:
        sock.close()


def receipt_seq(root: Path, scope: str, request_id: str):
    with ro(root) as c:
        row = c.execute("SELECT commit_seq FROM command_receipts WHERE caller_scope=? AND request_id=?",
                        (scope, request_id)).fetchone()
    return row[0] if row else None


@scenario("failure.client_disconnect", group="failure", kind="check", cells=("Failure: client disconnect",))
def failure_disconnect(run: Run, ds: Dataset, clients, mode):
    """A client that drops the connection right after sending: the command still commits once, its
    durable receipt exists BEFORE any retry, and the retry with the same id returns that receipt."""
    res = Result("failure.client_disconnect", ds.name, kind="check", clients=1, cells=["Failure: client disconnect"])
    root = copy_project(run, ds.root, "disconnect")
    client = start_coordinator(run, root)
    task = ds.inventory()["tasks"][0]
    value = f"n16 disconnect {uuid.uuid4().hex[:8]}"
    env = envelope(root, task, value)
    send_and_drop(root, env)
    deadline = time.monotonic() + 15
    seq = None
    while seq is None and time.monotonic() < deadline:
        seq = receipt_seq(root, env["caller_scope"], env["request_id"])
        time.sleep(0.05)
    res.check("receipt_exists_before_retry", seq is not None)
    retry = client.execute(env)["receipt"]
    with ro(root) as c:
        receipts = c.execute("SELECT COUNT(*) FROM command_receipts WHERE caller_scope=? AND request_id=?",
                             (env["caller_scope"], env["request_id"])).fetchone()[0]
    res.check("retry_returns_the_same_commit", seq is not None and retry["commit_seq"] == seq and receipts == 1,
              receipts=receipts)
    res.check("dropped_command_committed_its_value", field_values(root, "task", [task], "next_step")[task] == value)
    stop_coordinator(root)
    return res


INJECTED = "n16 injected before commit"


@scenario("failure.commit_error", group="failure", kind="check", cells=("Failure: disk/commit errors",))
def failure_commit_error(run: Run, ds: Dataset, clients, mode):
    """Injected OperationalError at before_commit: the client sees THAT error, nothing is committed and
    no receipt is kept; the same request then commits exactly once."""
    res = Result("failure.commit_error", ds.name, kind="check", clients=1, cells=["Failure: disk/commit errors"])
    root = copy_project(run, ds.root, "commit-error")
    owner = launch_service(run, root, SERVICE_SCRIPT)
    try:
        task = ds.inventory()["tasks"][1 % len(ds.inventory()["tasks"])]
        from taskmaster.coordinator.client import Client
        client = Client(root, autostart=False, timeout=30)
        env = envelope(root, task, f"n16 commit error {uuid.uuid4().hex[:8]}")
        before, commits_before = high_water(root), commit_count(root)
        (root / ".n16-inject-commit-error").write_text("1", encoding="utf-8")
        error = None
        try:
            client.execute(env)
        except Exception as exc:  # noqa: BLE001
            error = exc
        res.check("injected_error_is_explicit", error is not None and INJECTED in str(error),
                  error_type=type(error).__name__ if error else None)
        res.check("nothing_committed_on_error", high_water(root) == before and commit_count(root) == commits_before
                  and receipt_seq(root, env["caller_scope"], env["request_id"]) is None)
        (root / ".n16-inject-commit-error").unlink()
        receipt = client.execute(env)["receipt"]
        again = client.execute(env)["receipt"]
        res.check("retry_commits_once", commit_count(root) == commits_before + 1
                  and again["commit_seq"] == receipt["commit_seq"])
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass
    finally:
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


def blocked_replace_settled(root: Path, flushed, rel, text, value) -> bool:
    """The released export landed: a flush answered, the target file holds the committed value, no
    notice names it, and its own projection rows are settled - no pending, claimed or conflict job,
    and the file is neither quarantined, flagged nor drifting. Notices that name no file ("durable
    jobs remain", "publisher busy") are judged through those rows, never taken as settled."""
    from taskmaster.native import projection
    if not isinstance(flushed, dict) or value not in text:
        return False
    named = re.compile(rf"(?<![\w/.-]){re.escape(rel)}(?![\w/.-])")
    if any(named.search(str(n)) for n in flushed.get("notices") or ()):
        return False
    with ro(root) as c:
        open_jobs = c.execute("SELECT COUNT(*) FROM projection_jobs WHERE file=? AND state IN "
                              "('pending','claimed','conflict')", (rel,)).fetchone()[0]
        return open_jobs == 0 and projection.held_file(c, rel) is None


@scenario("failure.blocked_replace", group="failure", kind="check", cells=("Failure: blocked file replacement",))
def failure_blocked(run: Run, ds: Dataset, clients, mode):
    res = Result("failure.blocked_replace", ds.name, kind="check", clients=1, cells=["Failure: blocked file replacement"])
    if os.name != "nt":
        res.skip("Failure: blocked file replacement", "needs Windows sharing semantics (an open file blocks replace)")
        return res
    root = copy_project(run, ds.root, "blocked")
    client = start_coordinator(run, root)
    client.flush(high_water(root))
    task = ds.inventory()["tasks"][2 % len(ds.inventory()["tasks"])]
    path = root / ".taskmaster/tasks" / f"{task}.md"
    value = f"n16 blocked {uuid.uuid4().hex[:8]}"
    with blocked_export(root, [path]):
        receipt = client.execute(envelope(root, task, value))["receipt"]
        time.sleep(1.0)
        during = client.flush(receipt["commit_seq"])
        on_disk_during = value in path.read_text(encoding="utf-8")
    rel = f"tasks/{task}.md"
    after = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        after = client.flush(receipt["commit_seq"])
        if blocked_replace_settled(root, after, rel, path.read_text(encoding="utf-8"), value):
            break
        time.sleep(0.5)
    res.data["flush_during"] = {k: during.get(k) for k in ("state", "through")} if isinstance(during, dict) else None
    res.data["flush_after"] = {k: after.get(k) for k in ("state", "through")} if isinstance(after, dict) else None
    res.check("commit_acknowledged_while_blocked", receipt.get("commit_seq") is not None)
    res.check("block_held_the_file", not on_disk_during)
    res.check("blocked_export_not_reported_exported", (during or {}).get("state") not in (None, "exported"),
              state=(during or {}).get("state"))
    # Judged on the target file: a whole-projection "exported" never holds while an unrelated file
    # is quarantined (the CodeMaestro copy carries three).
    res.check("export_completes_after_release",
              blocked_replace_settled(root, after, rel, path.read_text(encoding="utf-8"), value),
              state=(after or {}).get("state"))
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
    check_unexpected(res, records)  # the prime and every op not expected to be refused
    res.check("holder_picked", got.get("lease.pick_a", {}).get("ok"), error=redact(got.get("lease.pick_a", {}).get("error")))
    res.check("live_lease_refuses_pick", got.get("lease.pick_b_live", {}).get("ok") is False)
    res.check("live_lease_refuses_release", got.get("lease.release_b_live", {}).get("ok") is False)
    expired = got.get("lease.pick_b_expired", {})
    res.check("expired_lease_pick_names_expiry", expired.get("ok") is False and "expired" in (expired.get("error") or ""))
    res.check("expired_lease_release_frees_it", got.get("lease.release_b_expired", {}).get("ok") is True,
              error=redact(got.get("lease.release_b_expired", {}).get("error")))
    res.check("pick_after_release_succeeds", got.get("lease.pick_b_after_release", {}).get("ok") is True,
              error=redact(got.get("lease.pick_b_after_release", {}).get("error")))
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
        res.skip("History: filtered removals", "no task/epic pair in the dataset")
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
        res.skip("History: store rebuild", "the dataset has no legacy snapshot to rebuild from")
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
    except HandshakeError:
        refused["old_service_protocol"] = True
    except Exception as exc:  # noqa: BLE001 - some other failure is not a protocol refusal
        refused["old_service_protocol"] = False
        res.data["old_protocol_error"] = type(exc).__name__
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
    present = {k: v for k, v in builds.items() if v != "not present"}
    for name in sorted(set(builds) - set(present)):
        res.skip(f"Migration: old build {name}", "build not present on this machine")
    if present:
        res.check("old_builds_refuse", all(v is True for v in present.values()), builds=present)
    res.check("projection_unchanged", not changed, changed=changed[:5] or None)
    return res


@scenario("migration.interrupted_cutover", group="migration", kind="check",
          cells=("Migration: interrupted cutover", "Migration: recovery"))
def migration_interrupted(run: Run, ds: Dataset, clients, mode):
    res = Result("migration.interrupted_cutover", ds.name, kind="check", clients=1,
                 cells=["Migration: interrupted cutover", "Migration: recovery"])
    if ds.legacy is None:
        res.skip("Migration: interrupted cutover/recovery",
                 "the dataset has no legacy snapshot (the source copy is already native); run it on a synthetic dataset")
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
    """One row per applicable distribution. A row is only evidence when its scenario passed its
    correctness checks and it has >= 200 samples; otherwise it says so instead of met/missed."""
    rows = []
    for result in results:
        if result["kind"] != "steady":
            continue
        for label, prefix, limit in BUDGETS:
            for op, dist in result["distributions"].items():
                applies = (prefix == "read" and op.startswith("read.")) or \
                          (prefix == "core" and op == "core.command") or \
                          (prefix == "write" and op.startswith("write.") and not op.endswith(".invalid")
                           and result["scenario"] not in ("write.cas", "write.invalid"))
                if not applies:
                    continue
                if result["verdict"] != "pass":
                    status = "invalid (correctness failed)" if result["verdict"] == "fail" else f"invalid ({result['verdict']})"
                elif dist["samples"] < 200:
                    status = "smoke (<200 samples)"
                else:
                    status = "met" if dist["p95_ms"] < limit else "missed"
                errors = result["errors"].get(op, {})
                rows.append({"budget": label, "dataset": result["dataset"], "scenario": result["scenario"],
                             "clients": result["clients"], "mode": result["mode"], "op": op,
                             "p95_ms": dist["p95_ms"], "limit_ms": limit, "samples": dist["samples"],
                             "errors": errors.get("total", 0), "status": status})
    return rows


def scaling_check(report) -> dict | None:
    """Required output vs unrelated data: the same bounded read must not grow with the dataset. Compares
    each read op's median output bytes on the smallest and largest dataset that ran it."""
    by_op = {}
    for r in report["results"]:
        if r["scenario"] in ("read.details", "read.context") and r["verdict"] == "pass" and r.get("output_bytes_p50"):
            tasks = (report["datasets"].get(r["dataset"], {}).get("counts") or {}).get("task", 0)
            by_op.setdefault(r["scenario"], []).append((tasks, r["dataset"], r["output_bytes_p50"]))
    res = Result("scale.required_output", "cross-dataset", kind="check",
                 cells=["Scaling: required output vs unrelated data"])
    for op, rows in sorted(by_op.items()):
        rows.sort()
        if len(rows) < 2 or rows[0][0] == rows[-1][0]:
            continue
        (small_n, small, small_b), (big_n, big, big_b) = rows[0], rows[-1]
        res.check(f"bounded_output[{op}]", big_b <= 1.5 * small_b, small=f"{small}:{small_b}B", large=f"{big}:{big_b}B",
                  task_ratio=round(big_n / max(1, small_n), 1))
    return res.finish() if res.data["checks"] else None


def markdown(report) -> str:
    smoke = report["meta"].get("smoke")
    lines = [f"# N16 acceptance run {report['meta']['started']}" + (" (SMOKE - not acceptance evidence)" if smoke else ""), "",
             f"Code `{report['meta']['git_sha'][:10]}`, Python {report['meta']['python'].split()[0]}, "
             f"SQLite {report['meta']['sqlite']}, {report['meta']['machine']}. Samples >= {report['meta']['samples_required']} "
             f"per steady-state scenario after {report['meta']['warmup']} warmup ops.", ""]
    failed = [r for r in report["results"] if r["verdict"] not in ("pass", "skipped", "precondition")]
    skipped = [r for r in report["results"] if r["verdict"] == "skipped"]
    unmet = [r for r in report["results"] if r["verdict"] == "precondition"]
    passed = sum(r["verdict"] == "pass" for r in report["results"])
    lines += ["## Correctness", "",
              f"{passed} of {len(report['results'])} scenario runs pass their checks; {len(failed)} fail; "
              f"{len(unmet)} precondition not met; {len(skipped)} skipped (see Not run).", ""]
    if unmet:
        lines += ["Precondition not met (not evidence; not a correctness failure):", ""]
        lines += [f"- {r['dataset']} {r['scenario']}: {r.get('precondition')}" for r in unmet]
        lines.append("")
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
    evidence = sum(b["status"] in ("met", "missed") for b in report["budgets"])
    lines += ["", f"{missed} of {evidence} evidential budget rows missed; {len(report['budgets']) - evidence} rows are "
              "not evidence (failed correctness, skipped or under 200 samples).", ""]
    if report.get("datasets"):
        lines += ["## Datasets and adoption (cold, separate)", ""]
        for name, info in report["datasets"].items():
            lines.append(f"- **{name}**: counts {info.get('counts')}, adoption {info.get('adoption')}"
                         + "".join(f"; {note}" for note in info.get("notes") or ()))
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


def frames_only(trace: str) -> str:
    """A traceback's frame lines and exception type only: messages may echo authored text."""
    lines = [line for line in trace.splitlines() if line.startswith("  File ")]
    last = trace.strip().splitlines()[-1] if trace.strip() else ""
    return "\n".join(lines[-12:] + [last.split(":", 1)[0]])


def execute_scenario(run: Run, ds: Dataset, name: str, clients, mode):
    spec = SCENARIOS[name]
    try:
        result = spec["fn"](run, ds, clients, mode)
        data = result.finish()
    except Refused as refusal:
        result = Result(name, ds.name, kind=spec["kind"], clients=clients, mode=mode, cells=spec["cells"])
        result.skip("; ".join(spec["cells"]), str(refusal))
        data = result.finish()
    except Exception as exc:  # noqa: BLE001 - a crashed scenario is a failed scenario, reported
        import traceback
        data = Result(name, ds.name, kind=spec["kind"], clients=clients, mode=mode).finish()
        data.update(verdict="fail", error=redact(f"{type(exc).__name__}: {exc}"),
                    traceback=frames_only(traceback.format_exc()))
    data["group"] = spec["group"]
    return data


ALWAYS_NOT_RUN = (
    "Migration: old runtime ACTIVE during cutover - not exercised (the cutover's quiesce probes refuse a live "
    "runtime; driving a live 6.0.x server against a copy needs its own design)",
    "Failure: exporter lease expiry - only the claim lease is exercised here (the exporter lease is covered by "
    "the N11 in-process tests)",
    "Failure: disk full - only an injected commit error is exercised",
    "Allocations - RSS per process is recorded, allocation counts are not",
)


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
    if args.sync_samples < 200 and not args.smoke:
        parser.error("sync steady-state scenarios need >= 200 rounds (use --smoke for a smoke run)")
    args.crash_points = [p for p in args.crash_points.split(",") if p]
    run = Run(args)
    run.required = args.samples
    run.sync_required = 0 if args.smoke else 200
    claim_work(run.work)
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
              "datasets": {}, "results": [], "instrumented": [], "skipped": list(ALWAYS_NOT_RUN)}
    if not run.psutil:
        report["skipped"].append("RSS: psutil not importable (pass --psutil-path; it is not a project dependency)")
    for dataset_name in [d for d in args.dataset.split(",") if d]:
        started = time.perf_counter()
        ds = prepare_dataset(run, dataset_name)
        report["datasets"][dataset_name] = {"prepare_s": round(time.perf_counter() - started, 2),
                                            "adoption": ds.info.get("adoption"), "counts": ds.inventory()["counts"],
                                            "notes": ds.inventory().get("notes") or [],
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
    scaling = scaling_check(report)
    if scaling is not None:
        report["results"].append(scaling)
    else:
        report["skipped"].append("Scaling: required output vs unrelated data - needs read.details or read.context "
                                 "passing on two dataset sizes in one run (e.g. --dataset small,10x)")
    for r in report["results"] + report["instrumented"]:
        for cell in r.get("skipped_cells") or ():
            report["skipped"].append(f"{cell['cell']} [{r['dataset']} {r['scenario']}] - {cell['reason']}")
    report["budgets"] = budget_rows(report["results"])
    report["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    results_path = args.results or (run.work / "n16-results.json")
    summary_path = args.summary or results_path.with_suffix(".md")
    results_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    summary_path.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"results": str(results_path), "summary": str(summary_path),
                      "pass": sum(r["verdict"] == "pass" for r in report["results"]),
                      "fail": sum(r["verdict"] == "fail" for r in report["results"]),
                      "skipped": sum(r["verdict"] == "skipped" for r in report["results"]),
                      "precondition": sum(r["verdict"] == "precondition" for r in report["results"]),
                      "budget_missed": sum(b["status"] == "missed" for b in report["budgets"])}, indent=1))
    return exit_status(report["results"] + report["instrumented"])


def exit_status(everything) -> int:
    if any(r["verdict"] == "fail" for r in everything):
        return 1
    if any(r["verdict"] in ("skipped", "precondition") or r.get("skipped_cells") for r in everything):
        return 2  # nothing failed, but part of the selection did not run (or could not): not a clean pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
