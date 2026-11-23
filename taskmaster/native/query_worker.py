# User intent: a member's backlog_query SQL runs in a separate worker process that is killed at the deadline and, on
# Linux, cannot grow past a fixed memory cap, so no statement can take the team server's memory or cores whatever
# SQLite does inside one step (security review I-1, fix round 2).
"""The SQL worker: one short-lived or reused child process per running `backlog_query` statement.

The parent (`native.compatibility.query`) leases a read-only snapshot file (`native.query_snapshots`, one per store
revision, shared by every call) and hands the child its path with the statement. `run_statement` is the one place a
statement is executed, with every SQLite limit, the authorizer and the progress handler; the child calls it, the parent
never runs a statement itself. The child imports only the standard library and `taskmaster.query_guard`, so it starts
in tens of milliseconds and is reused between statements that finished normally; a child that ran past the deadline,
failed, or answered something unexpected is killed.

Wire format, both directions: 4-byte big-endian length, then a JSON object. A request names the snapshot file and
may carry an overlay (the caller's host-table rows, at most OVERLAY_MAX_BYTES). Results never travel as pickle: a child
running hostile SQL must not be able to run code in the daemon by what it answers.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import struct
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    # Started as a script with -I: find the package this file belongs to, never an installed copy. Appended, so
    # nothing beside the package can shadow a standard-library module.
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from taskmaster import query_guard  # noqa: E402

# Every value (string, blob, row record) is at most this long. Fixed, never derived from stored data: a member's large
# note used to raise the limit for everyone (re-review I-1). The copy truncates stored values to VALUE_COPY_MAX (one
# write command's own cap, contracts.MAX_BYTES), so every row stays readable and a sort record of a whole row fits.
VALUE_LENGTH_LIMIT = 4 * 1024 * 1024
VALUE_COPY_MAX = 1024 * 1024
MAX_COLUMNS = 64
MAX_SQL_BYTES = 64 * 1024
# The raw values fetched for one answer; the tool shows 80 characters of each cell (500 rows x 64 columns x 80
# characters is 2.5 MB), so this only refuses selecting many huge values.
RESULT_MAX_BYTES = 4 * 1024 * 1024
# VM steps between cooperative deadline checks; the kill below is what bounds a single long step.
PROGRESS_INSTRUCTIONS = 1_000
# The snapshot's values may come to at most this (native.compatibility refuses the store past it, while copying).
SNAPSHOT_MAX_BYTES = 32 * 1024 * 1024
# The child's address-space cap on Linux (RLIMIT_AS): fixed, because the snapshot is a file it opens read-only with
# mmap off and an 8 MiB page cache, so no store size reaches its memory. The child volunteers for the OOM killer before
# the daemon (oom_score_adj), and the unit's OOMPolicy=continue keeps the server up when the kernel takes one.
#
# Memory budget on the unit (MemoryMax=1G, Linux), at the snapshot limit S = 32 MiB:
#   daemon: the builder streams batches into the file, one build at a time: a 4 MiB batch + 8 MiB cache    ~ 12-27 MiB
#   per heavy call: worker WORKER_MEMORY_BYTES (96) + its answer frame (<= 32) + rows (8)                   ~ 136 MiB
#   serve.config.HEAVY_TOTAL = 2 calls                                                                      = 272 MiB
#   daemon baseline: 207 MiB measured (re-review 2, in-process daemon under pytest); budgeted as              300 MiB
#   total ~ 600 MiB of 1,024. HEAVY_TOTAL stays 2 until the Linux (WSL) lane re-measures with file snapshots.
WORKER_MEMORY_BYTES = 96 * 1024 * 1024
WORKER_MEMORY_MAX = WORKER_MEMORY_BYTES
# What a statement's temp files (a sort or DISTINCT that spills) may grow to, Linux (RLIMIT_FSIZE).
WORKER_TEMP_FILE_MAX = 256 * 1024 * 1024
# The most the host-table overlay may come to (JSON bytes), and the request frame cap that carries it with the
# statement (MAX_SQL_BYTES, escaped up to 6x) and the rest of the envelope.
OVERLAY_MAX_BYTES = 4 * 1024 * 1024
REQUEST_MAX_BYTES = OVERLAY_MAX_BYTES + 1024 * 1024
# Statements running at once; the rest wait for a free slot up to their own deadline.
MAX_CONCURRENT = 4
SNAPSHOT_PAGE_CACHE_KIB = 8 * 1024
# A published snapshot, and no other path, may be opened: <...>/query-snapshots/p-<pid>-<nonce>/r<revision>-<nonce>.db
_SNAPSHOT_NAME = re.compile(r"r\d+-[0-9a-f]{8,32}\.db")
_SNAPSHOT_PROCESS = re.compile(r"p-\d+-[0-9a-f]{8,32}")
# How long past the deadline the parent waits for a child's answer before killing it.
KILL_GRACE_SECONDS = 0.5
# A child answers at most this much (RESULT_MAX_BYTES of values, JSON-escaped up to 6x, plus framing); anything
# larger is treated as a broken child.
MAX_ANSWER_BYTES = 32 * 1024 * 1024
WORKER_MAX_USES = 200
WORKER_MAX_IDLE = 4


class WorkerStopped(sqlite3.OperationalError):
    """The child died or was killed without answering (deadline, memory cap, crash)."""


def load_overlay(connection, overlay, readable):
    """Put the caller's host-table rows into TEMP tables of the same names (they resolve before the empty tables in
    the snapshot). Only a table the caller may read is accepted, whatever the parent sent."""
    for table, data in (overlay or {}).items():
        if table not in query_guard.HOST_PRIVATE_TABLES or table not in readable:
            raise ValueError(f"the overlay may not carry {table}")
        columns = [r[1] for r in connection.execute(f'PRAGMA main.table_info("{table}")')]
        if list(data["columns"]) != columns:
            raise ValueError(f"the overlay for {table} does not match the snapshot's columns")
        quoted = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
        connection.execute(f'CREATE TEMP TABLE "{table}"({quoted})')
        connection.executemany(f'INSERT INTO temp."{table}" VALUES({",".join("?" for _ in columns)})',
                               [[_decode(v) for v in row] for row in data["rows"]])


def run_statement(connection, statement, *, limit, authorizer, deadline, result_max_bytes=RESULT_MAX_BYTES,
                  overlay=None):
    """Run `statement` on `connection` (the private snapshot) under every limit; the answer's columns, rows (at most
    `limit`) and whether more rows existed. Raises sqlite3 errors and ValueError as the tool reports them."""
    # Sorts and DISTINCT/UNION sets spill to a file. Set before the overlay's TEMP tables exist: changing temp_store
    # deletes the temp tables that are already there.
    connection.execute("PRAGMA temp_store=FILE")
    load_overlay(connection, overlay, authorizer.readable)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, VALUE_LENGTH_LIMIT)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, MAX_COLUMNS)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
    connection.set_authorizer(authorizer)
    connection.set_progress_handler(deadline, PROGRESS_INSTRUCTIONS)
    try:
        cursor = connection.execute(statement)
        values, size = [], 0
        while len(values) <= limit:
            row = cursor.fetchone()
            if row is None:
                break
            size += sum(len(v) for v in row if isinstance(v, (str, bytes)))
            if size > result_max_bytes:
                raise ValueError(f"the answer is larger than {result_max_bytes / 2**20:g} MiB: select fewer or "
                                 "narrower columns (each cell is cut to 80 characters anyway) or fewer rows")
            values.append(row)
        return {"columns": [d[0] for d in cursor.description], "rows": values[:limit], "truncated": len(values) > limit}
    except sqlite3.DataError as exc:
        # "string or blob too big": name the limit, so an agent rewrites the statement instead of retrying it.
        raise ValueError(f"{exc}: a value in this query may be at most {VALUE_LENGTH_LIMIT} bytes") from None
    finally:
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)


# ── wire ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def encode_value(value):
    if isinstance(value, bytes):
        return {"b": base64.b64encode(value).decode("ascii")}
    return value


_encode = encode_value


def _decode(value):
    if isinstance(value, dict):
        return base64.b64decode(value["b"])
    return value


def _read_exact(stream, count):
    buffer = bytearray(count)
    view, done = memoryview(buffer), 0
    while done < count:
        read = stream.readinto(view[done:])
        if not read:
            raise EOFError("stream closed")
        done += read
    view.release()
    return buffer


def _read_json(stream, limit):
    size = struct.unpack(">I", _read_exact(stream, 4))[0]
    if size > limit:
        raise ValueError("frame too large")
    return json.loads(_read_exact(stream, size).decode("utf-8"))


def _write_json(stream, payload):
    data = json.dumps(payload).encode("utf-8")
    stream.write(struct.pack(">I", len(data)) + data)
    stream.flush()


# ── child ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def memory_cap(database_bytes=0):
    """The child's address-space cap for one statement: fixed (`database_bytes` is kept for old callers)."""
    return WORKER_MEMORY_MAX


def _cap_memory(soft, hard=None):
    """Lower the address-space cap; returns False where there is none (Windows: the parent's kill is the bound)."""
    try:
        import resource
    except ImportError:
        return False
    hard = resource.getrlimit(resource.RLIMIT_AS)[1] if hard is None else hard
    resource.setrlimit(resource.RLIMIT_AS, (min(soft, hard), hard))
    return True


def _cap_temp_files():
    """A statement's spilling sort fails with "disk I/O error" (the EFBIG from the cap surfaces as SQLITE_IOERR) instead of filling the disk (Linux)."""
    try:
        import resource
        import signal
    except ImportError:
        return
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)       # the write then fails with EFBIG, not a killed child
    hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
    limit = WORKER_TEMP_FILE_MAX if hard == resource.RLIM_INFINITY else min(WORKER_TEMP_FILE_MAX, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def _snapshot_uri(path):
    """The read-only, immutable URI of a published snapshot file; refuses any other path."""
    path = Path(path)
    if not (path.is_absolute() and _SNAPSHOT_NAME.fullmatch(path.name) and _SNAPSHOT_PROCESS.fullmatch(path.parent.name)
            and path.parent.parent.name == "query-snapshots"):
        raise ValueError("not a query snapshot path")
    return path.as_uri() + "?mode=ro&immutable=1"


def _start_child(hard):
    _cap_temp_files()
    if not _cap_memory(hard, hard):
        return
    try:
        with open("/proc/self/oom_score_adj", "w") as handle:     # the kernel kills a query before the daemon
            handle.write("1000")
    except OSError:
        pass


def _serve_one(stdin, stdout):
    request = _read_json(stdin, REQUEST_MAX_BYTES)
    authorizer = query_guard.Authorizer(frozenset(request["declared"]), readable=frozenset(request["readable"]),
                                        deny_functions=bool(request.get("deny_functions", True)))
    deadline = query_guard.Deadline(float(request["seconds"]))
    connection = None
    try:
        try:
            connection = sqlite3.connect(_snapshot_uri(request["snapshot"]), uri=True, isolation_level=None)
            connection.execute("PRAGMA mmap_size=0")                 # the file must not count against RLIMIT_AS
            connection.execute(f"PRAGMA cache_size=-{SNAPSHOT_PAGE_CACHE_KIB}")
            connection.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except (sqlite3.Error, ValueError, OSError) as exc:
            answer = {"ok": False, "type": "SnapshotMissing", "error": str(exc)}
        else:
            result = run_statement(connection, request["statement"], limit=int(request["limit"]),
                                   authorizer=authorizer, deadline=deadline, overlay=request.get("overlay"),
                                   result_max_bytes=min(int(request.get("result_max_bytes", RESULT_MAX_BYTES)),
                                                        RESULT_MAX_BYTES))
            answer = {"ok": True, "columns": result["columns"], "truncated": result["truncated"],
                      "rows": [[_encode(v) for v in row] for row in result["rows"]]}
    except MemoryError:
        answer = {"ok": False, "type": "MemoryError", "error": "out of memory"}
    except (sqlite3.Error, ValueError) as exc:
        answer = {"ok": False, "type": type(exc).__name__, "error": str(exc)}
    finally:
        if connection is not None:
            connection.close()
    # A child whose address space stayed large (a heap that did not shrink back) would fail the next, smaller
    # statement's cap: it answers, then retires.
    retire = _address_space() > WORKER_MEMORY_BYTES // 2
    answer.update(denial=authorizer.denial, expired=deadline.expired, retire=retire)
    _write_json(stdout, answer)
    return not retire


def _address_space():
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def main(argv):
    _start_child(int(argv[1]) if len(argv) > 1 else WORKER_MEMORY_MAX)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            if not _serve_one(stdin, stdout):
                return 0
        except (EOFError, BrokenPipeError):
            return 0
        except MemoryError:
            return 3


# ── parent ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _interpreter():
    # A Windows venv's python.exe is a launcher that starts the real interpreter as its own child, so killing the
    # launcher would leave the statement running. The child needs only the standard library and this package, so the
    # base interpreter serves.
    if sys.platform == "win32":
        base = getattr(sys, "_base_executable", "") or sys.executable
        return base if Path(base).is_file() else sys.executable
    return sys.executable


class _Worker:
    def __init__(self):
        import subprocess
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self.process = subprocess.Popen(
            [_interpreter(), "-I", "-S", str(Path(__file__).resolve()), str(WORKER_MEMORY_MAX)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        self.uses = 0

    def kill(self):
        try:
            self.process.kill()
        except OSError:
            pass
        try:
            self.process.wait(5)
        except Exception:  # noqa: BLE001 - a zombie the OS reaps later; nothing to report
            pass


class WorkerPool:
    """Idle children kept for reuse; a child is taken by one statement at a time."""

    def __init__(self, max_idle=WORKER_MAX_IDLE, max_concurrent=MAX_CONCURRENT):
        self.max_idle = max_idle
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._idle: list[_Worker] = []
        self._lock = threading.Lock()

    def _take(self):
        with self._lock:
            while self._idle:
                worker = self._idle.pop()
                if worker.process.poll() is None:
                    return worker
        return _Worker()

    def _give(self, worker):
        with self._lock:
            if worker.uses < WORKER_MAX_USES and len(self._idle) < self.max_idle and worker.process.poll() is None:
                self._idle.append(worker)
                return
        worker.kill()

    def close(self):
        with self._lock:
            idle, self._idle = self._idle, []
        for worker in idle:
            worker.kill()

    def run(self, snapshot, statement, *, limit, readable, declared, seconds,
            result_max_bytes=RESULT_MAX_BYTES, deny_functions=True, overlay=None):
        """The child's answer dict; raises WorkerStopped when it died or was killed at `seconds` plus the grace, or
        when no slot freed up within `seconds`. `snapshot` is the path of a published snapshot file."""
        started = time.monotonic()
        if not self._slots.acquire(timeout=max(0.0, seconds)):
            raise WorkerStopped("interrupted")
        try:
            return self._run(str(snapshot), statement, limit=limit, readable=readable, declared=declared,
                             seconds=max(0.0, seconds - (time.monotonic() - started)),
                             result_max_bytes=result_max_bytes, deny_functions=deny_functions, overlay=overlay)
        finally:
            self._slots.release()

    def _run(self, snapshot, statement, *, limit, readable, declared, seconds, result_max_bytes, deny_functions,
             overlay):
        worker = self._take()
        worker.uses += 1
        box = {}

        def read():
            try:
                box["answer"] = _read_json(worker.process.stdout, MAX_ANSWER_BYTES)
            except Exception as exc:  # noqa: BLE001 - EOF, a short read or a bad frame: the child is broken
                box["error"] = exc

        reader = threading.Thread(target=read, name="query-worker-reader", daemon=True)
        reader.start()
        try:
            _write_json(worker.process.stdin, {"statement": statement, "limit": limit, "seconds": seconds,
                                               "readable": sorted(readable), "declared": sorted(declared),
                                               "result_max_bytes": result_max_bytes, "snapshot": snapshot,
                                               "overlay": overlay, "deny_functions": bool(deny_functions)})
        except OSError:
            pass                       # the child is gone; the reader reports it
        reader.join(max(0.0, seconds) + KILL_GRACE_SECONDS)
        if reader.is_alive() or "answer" not in box:
            killed = reader.is_alive()
            worker.kill()
            reader.join(5)
            raise WorkerStopped("interrupted" if killed else "the query process stopped without answering "
                                f"(it may have reached its {memory_cap() // 2**20} MiB memory cap)")
        answer = box["answer"]
        if answer.get("ok"):
            answer["rows"] = [tuple(_decode(v) for v in row) for row in answer["rows"]]
        if answer.get("retire") or answer.get("type") == "MemoryError":
            worker.kill()
        else:
            self._give(worker)
        return answer


POOL = WorkerPool()


def _close_pool():
    POOL.close()


import atexit  # noqa: E402

atexit.register(_close_pool)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
