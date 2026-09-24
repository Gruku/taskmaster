# User intent: N16 needs work counters (lock wait/hold, rows, files, cache hits, export lag, IPC
# startup, output bytes) recorded in a SEPARATE instrumented pass while latency samples stay
# uninstrumented, so this is strictly opt-in and a single module-flag check when off.
"""Opt-in work counters for the native store, coordinator and tool replies.

Enabling
--------
Per process, from the environment at import time::

    TASKMASTER_METRICS=1                 in-memory; read back with `records()`
    TASKMASTER_METRICS=/abs/path.jsonl   JSON lines, one file per process

or programmatically with `enable(path=None)` / `disable()`. The coordinator
service is a child process launched with the client's environment, so an
absolute file path set before the client starts reaches both. A relative
path or an unknown word leaves metrics disabled and warns once on stderr
(a child process has another working directory, so a relative path would
scatter files).

Each process appends to its own file, ``<stem>.<pid><suffix>`` next to the
base path (``m.jsonl`` -> ``m.4242.jsonl``): Windows append mode is not atomic
across processes, and one shared file tore and lost records. Files grow
without bound; the harness owns their lifetime. The in-memory sink keeps the
newest MEMORY_LIMIT records and counts the older ones it dropped (`dropped()`).

When disabled every instrumentation point is one `if metrics.ENABLED:` check
(a module attribute read); nothing is allocated, timed, installed on a
connection or written, and no output changes. Recording never raises into
the instrumented code (a failing sink is dropped silently).

Reading
-------
`load(path)` takes the base path (reads it, if present, and every
``<stem>.<pid><suffix>`` sibling), one exact per-process file, or a glob. It
returns a list of records with `.skipped` (malformed or non-record lines) and
`.files` (what was read). `summarize(records)` groups them by (kind, op) and reports count, sum, min,
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
    rows_written  committed commands only (absent on replay/error, where the
                  rows were rolled back): the `total_changes` delta, i.e. rows
                  inserted, updated or deleted by statements, triggers included.
                  Exact for ordinary tables. A command that touches the search
                  index (fts_documents > 0) also counts the FTS5 shadow-table
                  rows, which vary with segment merges: an approximate figure;
    vm_steps      rows-read proxy: SQLite virtual-machine instructions, sampled
                  by a progress handler every VM_STEP_SAMPLE instructions. It
                  undercounts each statement by < VM_STEP_SAMPLE, so it is a
                  lower bound that grows with rows scanned (full scans show).
                  sqlite3 has no progress-handler getter, so the meter cannot
                  save and restore one: it installs its own and clears it at the
                  end. That is safe because every other handler in the codebase
                  (search, dependency walks, query guards) is set and cleared
                  inside a read call, so none is installed when a command
                  starts; one nested inside a command would end the sampling
                  early (still a lower bound), never break the command;
    receipt_bytes the encoded receipt (the command's reply payload), computed
                  after COMMIT so it is not inside commit_ms or lock_hold_ms;
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

from collections import deque
import glob
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

ENV = "TASKMASTER_METRICS"
VM_STEP_SAMPLE = 32
MEMORY_LIMIT = 200_000

ENABLED = False
_path: Path | None = None
_memory: deque = deque()
_dropped = 0
_stream = None  # (pid, open file) of this process's own file
_lock = threading.Lock()
_local = threading.local()
_warned: set = set()


def enable(path=None) -> None:
    """Record to per-process files beside `path`, or, with None, in memory."""
    global ENABLED, _path
    with _lock:
        _close_stream()
        _path = None if path is None else Path(path).absolute()
        ENABLED = True


def disable() -> None:
    global ENABLED, _path
    with _lock:
        _close_stream()
        ENABLED, _path = False, None


def _close_stream():
    global _stream
    if _stream is not None:
        try:
            _stream[1].close()
        except OSError:
            pass
        _stream = None


def _warn(message):
    if message not in _warned:
        _warned.add(message)
        print(f"taskmaster metrics: {message}", file=sys.stderr)


def configure(environ=None) -> None:
    """Apply `TASKMASTER_METRICS` from `environ` (default `os.environ`)."""
    value = (os.environ if environ is None else environ).get(ENV, "").strip()
    word = value.lower()
    if not value or word in ("0", "false", "off", "no"):
        disable()
    elif word in ("1", "true", "on", "yes", "memory"):
        enable()
    elif os.path.isabs(value):
        enable(value)
    else:
        disable()
        _warn(f"{ENV}={value!r} is neither 1/0 nor an absolute file path; metrics stay disabled")


def process_file(base, pid=None) -> Path:
    """This process's file for base path `base`: `<stem>.<pid><suffix>`."""
    base = Path(base)
    return base.with_name(f"{base.stem}.{os.getpid() if pid is None else pid}{base.suffix}")


def records() -> list[dict]:
    with _lock:
        return list(_memory)


def dropped() -> int:
    """In-memory records discarded because more than MEMORY_LIMIT were kept."""
    return _dropped


def clear() -> None:
    global _dropped
    with _lock:
        _memory.clear()
        _dropped = 0


def emit(kind: str, **fields) -> None:
    global _dropped, _stream
    if not ENABLED:
        return
    pid = os.getpid()
    record = {"kind": kind, "ts": time.time(), "pid": pid, **fields}
    try:
        with _lock:
            if _path is None:
                _memory.append(record)
                while len(_memory) > MEMORY_LIMIT:
                    _memory.popleft()
                    _dropped += 1
                return
            line = json.dumps(record, default=str, separators=(",", ":")) + "\n"
            if _stream is None or _stream[0] != pid:  # a forked child opens its own file
                _stream = (pid, open(process_file(_path, pid), "a", encoding="utf-8"))
            _stream[1].write(line)
            _stream[1].flush()
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
        self.issued = self.acquired = self.committing = self.outcome = None
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
        self.outcome = outcome  # encoded in close(), after the write lock is released
        self.record.update(outcome="committed", commit_seq=outcome.get("commit_seq"),
                           affected=len(outcome.get("affected") or ()), **(outcome.get("work") or {}))

    def replayed(self, outcome):
        self.record.update(outcome="replayed", commit_seq=outcome.get("commit_seq"))

    def failed(self, exc):
        self.outcome = None
        self.record.update(outcome="error", error=type(exc).__name__)
        for name in ("commit_seq", "affected"):
            self.record.pop(name, None)

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
        if record["outcome"] == "committed":
            try:
                record["rows_written"] = self.connection.total_changes - self.changes
            except Exception:  # noqa: BLE001
                pass
            try:
                record["receipt_bytes"] = len(json.dumps(self.outcome, default=str,
                                                         separators=(",", ":")).encode())
            except (TypeError, ValueError):
                pass
        record["vm_steps"] = self.steps * VM_STEP_SAMPLE
        emit("command", **record)


# ── Reader API for the acceptance harness ───────────────────────────────────
class Records(list):
    """`load`'s result: the records, plus `skipped` lines and the `files` read."""
    skipped: int = 0
    files: list


def _files(path) -> list[Path]:
    text = str(path)
    if glob.has_magic(text):
        return sorted(Path(item) for item in glob.glob(text))
    base = Path(path)
    sibling = re.compile(re.escape(base.stem) + r"\.\d+" + re.escape(base.suffix) + "$")
    if re.search(r"\.\d+$", base.stem) and base.exists():
        return [base]  # one exact per-process file
    found = [base] if base.exists() else []
    if base.parent.is_dir():
        found += sorted(item for item in base.parent.iterdir() if sibling.match(item.name))
    return found


def load(path) -> Records:
    """Every record under a base path, one exact file or a glob (see the module docs).

    Records keep file order within a file; files are read in name order. A line
    that is not a JSON object with a `kind` is counted in `.skipped`."""
    found = Records()
    found.files = _files(path)
    for file in found.files:
        with open(file, encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    value = None
                if isinstance(value, dict) and "kind" in value:
                    found.append(value)
                else:
                    found.skipped += 1
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
