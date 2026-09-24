# User intent: N16 needs work counters (lock wait/hold, rows, files, cache hits, export lag, IPC
# startup, output bytes) recorded in a SEPARATE instrumented pass while latency samples stay
# uninstrumented, so this is strictly opt-in and a single module-flag check when off.
"""Opt-in work counters for the native store, coordinator and tool replies.

Enabling
--------
Per process, from the environment at import time::

    TASKMASTER_METRICS=1                 in-memory; read back with `records()`
    TASKMASTER_METRICS=/abs/path.jsonl   one JSON object per line, appended

or programmatically with `enable(path=None)` / `disable()`. The coordinator
service is a child process launched with the client's environment, so an
absolute file path set before the client starts reaches both. Several
processes may append to one file; every record carries its `pid`.

When disabled every instrumentation point is one `if metrics.ENABLED:` check
(a module attribute read); nothing is allocated, timed, installed on a
connection or written, and no output changes. Recording never raises into
the instrumented code (a failing sink is dropped silently).

Reading
-------
`load(path)` returns the records of a JSONL file (malformed lines skipped);
`summarize(records)` groups them by (kind, op) and reports count, sum, min,
max and p50/p95/p99 of every numeric field.

Record schema
-------------
Every record: ``kind`` (str), ``ts`` (Unix seconds, float), ``pid`` (int).
Durations are milliseconds (float, ``*_ms``); counts are ints.

``command`` -- one per `commands.execute` call (the native writer transaction)
    op, request_id, caller_scope; outcome ``committed`` | ``replayed`` | ``error``
    (+ ``error``: exception type name); commit_seq, affected (entities changed);
    lock_wait_ms  BEGIN IMMEDIATE issued -> acquired (exact wall time);
    lock_hold_ms  acquired -> COMMIT/ROLLBACK returned (exact wall time);
    commit_ms     the COMMIT call alone (fsync included);
    db_ms         lock_wait_ms + lock_hold_ms: wall time inside the database
                  transaction, Python domain logic included (a proxy: SQL time
                  is not separated from the Python between statements);
    rows_written  `sqlite3.Connection.total_changes` delta (exact rows inserted,
                  updated or deleted by statements, triggers included; an FTS
                  write counts at the virtual table, not its shadow tables);
    vm_steps      rows-read proxy: SQLite virtual-machine instructions, sampled
                  by a progress handler every VM_STEP_SAMPLE instructions. It
                  undercounts each statement by < VM_STEP_SAMPLE, so it is a
                  lower bound that grows with rows scanned (full scans show);
    receipt_bytes the encoded receipt (the command's reply payload);
    fts_documents, path_comparisons, link_pairs, handover_pairs
                  the transaction's own graph/FTS work counters (exact).
``request`` -- one per command the coordinator writer runs
    op, request_id, caller_scope, outcome; queue_wait_ms (enqueued -> dequeued),
    admission_wait_ms (dequeued -> execution starts: pause gate, execution lock
    and store fence), service_ms (the execute call).
``ipc`` -- one per coordinator HTTP request, service side
    method, request_id, status, handler_ms, reply_bytes (exact body bytes).
``ipc_connect`` -- client side, one per `Client` call's readiness check
    ms (entry -> a ready service answered `status`), launched (bool: this call
    started the service process; then ms is the IPC startup latency).
``sync`` -- one per coordinator sync
    request_id, caller_scope, state, ms; files_selected; files_stated (lstat
    calls actually issued); files_read and bytes_read (full observed reads);
    files_parsed (projection parses); cache_hits / cache_misses (fingerprint
    cache lookups in the sync's `Scan`); directories_listed.
``export`` -- one per projection drain in the coordinator
    source (``background`` | ``flush``), through, commit_seq (committed high
    water when the drain began), exported_before / exported_seq (the export
    watermark before and after), lag_seqs (commit_seq - exported_seq),
    drain_ms, notices, published_commits (commits newly covered).
``export_lag`` -- one per commit newly covered by the export watermark
    commit_seq, ms (commit returned in the writer -> the drain that published
    it returned; only commits this coordinator process wrote).
``tool`` -- one per MCP tool call (outermost wrapper)
    op (tool name), request_id (when passed), ms, output_bytes (UTF-8 bytes of
    a text reply, else of its JSON encoding).
``http`` -- one per viewer JSON reply: method, path, status, output_bytes, and
    the reply's Server-Timing entries as ``<name>_ms``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading
import time

ENV = "TASKMASTER_METRICS"
VM_STEP_SAMPLE = 32

ENABLED = False
_path: Path | None = None
_memory: list[dict] = []
_lock = threading.Lock()
_local = threading.local()


def enable(path=None) -> None:
    """Record to `path` (appended JSONL) or, with None, in memory."""
    global ENABLED, _path
    with _lock:
        _path = None if path is None else Path(path).absolute()
        ENABLED = True


def disable() -> None:
    global ENABLED, _path
    with _lock:
        ENABLED, _path = False, None


def configure(environ=None) -> None:
    """Apply `TASKMASTER_METRICS` from `environ` (default `os.environ`)."""
    value = (os.environ if environ is None else environ).get(ENV, "").strip()
    if not value or value.lower() in ("0", "false", "off", "no"):
        disable()
    elif value.lower() in ("1", "true", "on", "yes", "memory"):
        enable()
    else:
        enable(value)


def records() -> list[dict]:
    with _lock:
        return list(_memory)


def clear() -> None:
    with _lock:
        _memory.clear()


def emit(kind: str, **fields) -> None:
    if not ENABLED:
        return
    record = {"kind": kind, "ts": time.time(), "pid": os.getpid(), **fields}
    try:
        with _lock:
            if _path is None:
                _memory.append(record)
                return
            line = json.dumps(record, default=str, separators=(",", ":")) + "\n"
            with open(_path, "a", encoding="utf-8") as stream:
                stream.write(line)
    except Exception:  # noqa: BLE001 - instrumentation never breaks the measured code
        pass


def add(name: str, n: int = 1) -> None:
    """Add to `name` in every scope open on this thread (none: a no-op)."""
    for record in getattr(_local, "stack", ()):
        record[name] = record.get(name, 0) + n


class _Scope:
    __slots__ = ("kind", "record", "start")

    def __init__(self, kind, fields):
        self.kind, self.record = kind, dict(fields)

    def __enter__(self):
        stack = getattr(_local, "stack", None)
        if stack is None:
            stack = _local.stack = []
        stack.append(self.record)
        self.start = time.perf_counter()
        return self.record

    def __exit__(self, kind, exc, tb):
        self.record["ms"] = (time.perf_counter() - self.start) * 1000
        if exc is not None:
            self.record.setdefault("outcome", "error")
            self.record["error"] = type(exc).__name__
        stack = _local.stack
        stack.remove(self.record)
        emit(self.kind, **self.record)
        return False


class _NullScope:
    def __enter__(self):
        return {}

    def __exit__(self, *exc):
        return False


_NULL = _NullScope()


def scope(kind: str, **fields):
    """A context whose `add` counters and elapsed `ms` are emitted as one record."""
    return _Scope(kind, fields) if ENABLED else _NULL


class CommandMeter:
    """The `command` record of one `commands.execute` call (see the schema)."""

    def __init__(self, connection, request):
        self.connection = connection
        self.record = {"op": request.get("operation"), "request_id": request.get("request_id"),
                       "caller_scope": request.get("caller_scope"), "outcome": "error"}
        self.steps = 0
        self.issued = self.acquired = self.committing = None
        self.changes = connection.total_changes
        self.done = False

    def _tick(self):
        self.steps += 1
        return 0

    def begin(self):
        self.connection.set_progress_handler(self._tick, VM_STEP_SAMPLE)
        self.issued = time.perf_counter()

    def locked(self):
        self.acquired = time.perf_counter()

    def commit(self, outcome):
        self.committing = time.perf_counter()
        self.record.update(outcome="committed", commit_seq=outcome.get("commit_seq"),
                           affected=len(outcome.get("affected") or ()), **(outcome.get("work") or {}))
        try:
            self.record["receipt_bytes"] = len(json.dumps(outcome, default=str, separators=(",", ":")).encode())
        except (TypeError, ValueError):
            pass

    def replayed(self, outcome):
        self.record.update(outcome="replayed", commit_seq=outcome.get("commit_seq"))

    def failed(self, exc):
        self.record.update(outcome="error", error=type(exc).__name__)

    def close(self):
        if self.done:
            return
        self.done = True
        end = time.perf_counter()
        try:
            self.connection.set_progress_handler(None, 0)
        except Exception:  # noqa: BLE001
            pass
        record = self.record
        if self.issued is not None:
            acquired = self.acquired if self.acquired is not None else end
            record["lock_wait_ms"] = (acquired - self.issued) * 1000
            record["lock_hold_ms"] = (end - acquired) * 1000 if self.acquired is not None else 0.0
            record["db_ms"] = (end - self.issued) * 1000
        if self.committing is not None:
            record["commit_ms"] = (end - self.committing) * 1000
        try:
            record["rows_written"] = self.connection.total_changes - self.changes
        except Exception:  # noqa: BLE001
            pass
        record["vm_steps"] = self.steps * VM_STEP_SAMPLE
        emit("command", **record)


# ── Reader API for the acceptance harness ───────────────────────────────────
def load(path) -> list[dict]:
    """Every record of a JSONL metrics file, in order; malformed lines skipped."""
    found = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and "kind" in value:
                found.append(value)
    return found


def percentile(values, q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100) of `values`; None when empty."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = min(len(ordered), max(1, math.ceil(len(ordered) * q / 100)))
    return ordered[rank - 1]


def summarize(records, *, by=("kind", "op"), percentiles=(50, 95, 99)) -> dict:
    """`{group: {"count": n, field: {count,sum,min,max,p50,...}}}` over numeric fields.

    The group key joins the `by` fields present on a record with ":" (e.g.
    ``command:task.patch``, ``export``). `ts` and `pid` are not summarized;
    booleans are counted as 0/1."""
    groups: dict[str, dict] = {}
    for record in records:
        key = ":".join(str(record[name]) for name in by if record.get(name) is not None)
        group = groups.setdefault(key, {"count": 0, "values": {}})
        group["count"] += 1
        for name, value in record.items():
            if name in ("ts", "pid") or name in by or not isinstance(value, (int, float)):
                continue
            group["values"].setdefault(name, []).append(float(value))
    summary = {}
    for key, group in groups.items():
        entry = {"count": group["count"]}
        for name, values in group["values"].items():
            stats = {"count": len(values), "sum": sum(values), "min": min(values), "max": max(values)}
            for q in percentiles:
                stats[f"p{q:g}"] = percentile(values, q)
            entry[name] = stats
        summary[key] = entry
    return summary


configure()
