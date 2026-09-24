# User intent: N16 track C - prove the opt-in work counters are right on small known scenarios,
# that the disabled path records nothing and changes no output, and that it costs ~nothing.
"""Opt-in metrics: command, sync, coordinator and export counters; disabled path; overhead."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import timeit

import pytest

from taskmaster.native import metrics
from taskmaster.native.commands import execute, Conflict
from test_native_commands import native, envelope  # noqa: F401
from test_native_migration import legacy  # noqa: F401


@contextmanager
def recording(path=None):
    metrics.clear()
    metrics.enable(path)
    try:
        yield
    finally:
        metrics.disable()


def kinds(kind):
    return [record for record in metrics.records() if record["kind"] == kind]


def connect(path):
    return closing(sqlite3.connect(path, isolation_level=None))


def table_state(path):
    with connect(path) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'document_search%'")]
        state = {}
        for name in names:
            # Wall-clock stamps differ between any two runs; every other column must match.
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')
                       if row[1] not in CLOCK_COLUMNS]
            listed = ",".join(f'"{column}"' for column in columns)
            state[name] = sorted(map(repr, connection.execute(f'SELECT {listed} FROM "{name}"')))
        return state


CLOCK_COLUMNS = {"ts", "created_at", "expires_at", "updated_at", "committed_at"}


# ── facility ─────────────────────────────────────────────────────────────────
def test_disabled_by_default_and_emits_nothing(tmp_path):
    assert not metrics.ENABLED
    metrics.clear()
    metrics.emit("command", op="x")
    with metrics.scope("sync") as record:
        metrics.add("files_read")
    assert metrics.records() == [] and record == {}


def test_environment_selects_memory_file_or_off(tmp_path):
    target = tmp_path / "m.jsonl"
    try:
        metrics.configure({metrics.ENV: "1"})
        assert metrics.ENABLED and metrics._path is None
        metrics.configure({metrics.ENV: str(target)})
        assert metrics.ENABLED and metrics._path == target
        metrics.configure({metrics.ENV: "0"})
        assert not metrics.ENABLED
        metrics.configure({})
        assert not metrics.ENABLED
    finally:
        metrics.disable()


def test_a_child_process_records_to_the_environment_file(tmp_path):
    target = tmp_path / "child.jsonl"
    environment = dict(os.environ, **{metrics.ENV: str(target)})
    subprocess.run([sys.executable, "-c", "from taskmaster.native import metrics; metrics.emit('probe', n=3)"],
                   check=True, env=environment, cwd=Path(__file__).resolve().parents[1])
    [record] = metrics.load(target)
    assert record["kind"] == "probe" and record["n"] == 3 and record["pid"] != os.getpid()


def test_scopes_nest_and_load_and_summarize_round_trip(tmp_path):
    target = tmp_path / "m.jsonl"
    with recording(target):
        with metrics.scope("outer", op="a") as outer:
            metrics.add("files_read", 2)
            with metrics.scope("inner", op="b"):
                metrics.add("files_read")
        for value in (1, 2, 3, 4):
            metrics.emit("command", op="task.patch", rows_written=value)
    target.open("a", encoding="utf-8").write("not json\n")
    loaded = metrics.load(target)
    assert [r["kind"] for r in loaded] == ["inner", "outer", "command", "command", "command", "command"]
    assert outer["files_read"] == 3 and loaded[0]["files_read"] == 1
    summary = metrics.summarize(loaded)
    rows = summary["command:task.patch"]["rows_written"]
    assert summary["command:task.patch"]["count"] == 4
    assert (rows["sum"], rows["min"], rows["max"], rows["p50"], rows["p95"], rows["p99"]) == (10, 1, 4, 2, 4, 4)
    assert "ms" in summary["outer:a"]


# ── commands ─────────────────────────────────────────────────────────────────
def test_metadata_edit_counts_zero_graph_work_and_its_exact_rows(native):
    with recording(), connect(native) as connection:
        receipt = execute(connection, envelope())
    [record] = kinds("command")
    assert record["op"] == "task.patch" and record["request_id"] == "request-1" and record["outcome"] == "committed"
    assert record["commit_seq"] == receipt["commit_seq"] and record["affected"] == 1
    assert {name: record[name] for name in receipt["work"]} == receipt["work"]
    assert record["path_comparisons"] == record["link_pairs"] == record["handover_pairs"] == 0
    assert record["fts_documents"] == 0
    # The field row, commit group (insert + seq update), domain event, revision bump,
    # projection job, receipt and manifest high-water: eight rows, no graph/FTS rows.
    assert record["rows_written"] == 8
    assert record["vm_steps"] > 0 and record["receipt_bytes"] > 0
    assert 0 <= record["lock_wait_ms"] < record["db_ms"] and record["lock_hold_ms"] > 0
    assert record["commit_ms"] <= record["lock_hold_ms"]


def test_replay_and_error_are_recorded_with_their_outcome(native):
    with recording(), connect(native) as connection:
        execute(connection, envelope())
        execute(connection, envelope())
        with pytest.raises(Conflict):
            execute(connection, envelope(key="stale", expected=[{"kind": "task", "id": "same", "revision": 1}]))
    first, replay, error = kinds("command")
    assert replay["outcome"] == "replayed" and replay["commit_seq"] == first["commit_seq"]
    assert replay["rows_written"] == 0
    assert error["outcome"] == "error" and error["error"] == "Conflict" and error["request_id"] == "stale"


def test_lock_wait_is_the_time_a_held_writer_blocks_begin_immediate(native):
    errors = []

    def command():
        try:
            with connect(native) as connection:
                execute(connection, envelope())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    with connect(native) as holder, recording():
        holder.execute("BEGIN IMMEDIATE")
        thread = threading.Thread(target=command)
        thread.start()
        time.sleep(0.4)
        holder.rollback()
        released = threading.Event()
        released.set()
        thread.join()
    assert not errors
    [record] = kinds("command")
    assert released.is_set()
    assert record["lock_wait_ms"] >= 300
    assert record["lock_hold_ms"] < record["lock_wait_ms"]
    assert record["db_ms"] == pytest.approx(record["lock_wait_ms"] + record["lock_hold_ms"])


def copy_store(source, target):
    with connect(source) as src, connect(target) as dst:
        src.backup(dst)
    return target


def test_disabled_path_records_nothing_and_changes_no_output(native, tmp_path):
    twin = copy_store(native, tmp_path / "twin.db")
    metrics.clear()
    with connect(native) as connection:
        plain = execute(connection, envelope())
    assert metrics.records() == []
    with recording(), connect(twin) as connection:
        measured = execute(connection, envelope())
    assert kinds("command")
    assert measured == plain
    assert table_state(native) == table_state(twin)


# ── coordinator: sync IO, requests, IPC and export lag ────────────────────────
from taskmaster.coordinator.client import Client  # noqa: E402
from taskmaster.coordinator.service import Coordinator  # noqa: E402
from test_native_service import root, request  # noqa: E402,F401


def test_second_sync_hits_the_fingerprint_cache_and_reads_fewer_files(root):
    from test_native_sync_perf import age_projection
    age_projection(root)
    with Coordinator(root), recording():
        client = Client(root, autostart=False)
        first = client.sync(request_id="first")
        second = client.sync(request_id="second")
        assert first["state"] == second["state"] == "synchronized", (first, second)
    cold, warm = kinds("sync")
    assert (cold["request_id"], warm["request_id"]) == ("first", "second")
    assert cold["state"] == warm["state"] == "synchronized"
    assert cold["files_selected"] == warm["files_selected"] > 0
    selected = cold["files_selected"]
    # Cold: every file misses and is read in full once; the completion check then hits the
    # fingerprints that read recorded. Warm: both lookups hit, nothing is read or parsed.
    assert (cold["cache_misses"], cold["files_read"], cold["cache_hits"]) == (selected, selected, selected)
    assert cold["bytes_read"] > 0 and cold["files_stated"] > 0 and cold["directories_listed"] > 0
    assert (warm["cache_misses"], warm["files_read"], warm["bytes_read"], warm["files_parsed"]) == (0, 0, 0, 0)
    assert warm["cache_hits"] == 2 * selected
    assert warm["ms"] > 0


def test_coordinator_records_queue_service_ipc_and_client_connect(root):
    with Coordinator(root), recording():
        client = Client(root, autostart=False)
        reply = client.execute(request(client, key="measured"))
    [command] = kinds("command")
    [queued] = kinds("request")
    assert command["request_id"] == queued["request_id"] == "measured" and queued["op"] == "task.patch"
    assert queued["outcome"] == "ok" and queued["queue_wait_ms"] >= 0 and queued["admission_wait_ms"] >= 0
    assert queued["service_ms"] >= command["db_ms"]
    executes = [record for record in kinds("ipc") if record["method"] == "execute"]
    assert [record["request_id"] for record in executes] == ["measured"]
    assert executes[0]["status"] == 200 and executes[0]["reply_bytes"] > len(json.dumps(reply["receipt"])) > 0
    assert all(record["method"] == "status" for record in kinds("ipc") if record not in executes)
    connects = kinds("ipc_connect")
    assert connects and not any(record["launched"] for record in connects) and all(r["ms"] > 0 for r in connects)


def test_export_records_the_watermark_and_each_commits_lag(root):
    with Coordinator(root) as owner, recording():
        client = Client(root, autostart=False)
        receipt = client.execute(request(client, key="lagged"))["receipt"]
        assert client.flush(receipt["commit_seq"])["state"] == "exported"
        deadline = time.monotonic() + 10
        while not kinds("export_lag") and time.monotonic() < deadline:
            time.sleep(0.05)
    exports = kinds("export")
    assert exports and all(record["source"] in ("background", "flush") for record in exports)
    last = exports[-1]
    assert last["exported_seq"] >= receipt["commit_seq"] and last["lag_seqs"] == last["commit_seq"] - last["exported_seq"]
    [lag] = kinds("export_lag")
    assert lag["commit_seq"] == receipt["commit_seq"] and lag["ms"] >= 0
    assert sum(record["published_commits"] for record in exports) == 1
    assert not owner.commit_clock


def test_disabled_coordinator_records_nothing(root):
    metrics.clear()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.execute(request(client, key="quiet"))
        client.sync(request_id="quiet-sync")
    assert metrics.records() == [] and owner.commit_clock == {}


# ── overhead ─────────────────────────────────────────────────────────────────
# Flag checks one command pays when metrics are off (execute's meter check and its
# five `if meter` tests, the writer's dequeue check, the IPC handler's check).
DISABLED_CHECKS_PER_COMMAND = 8


def test_disabled_overhead_is_negligible_on_the_command_hot_path(native):
    import statistics
    assert not metrics.ENABLED
    with connect(native) as connection:
        durations = []
        for index in range(40):
            started = time.perf_counter()
            execute(connection, envelope(key=f"bench-{index}", args={"id": "same", "set": {"next_step": f"s{index}"}}))
            durations.append(time.perf_counter() - started)
    command = statistics.median(durations[5:])
    per_check = min(timeit.repeat("if metrics.ENABLED: pass\nif meter: pass", number=200_000, repeat=5,
                                  globals={"metrics": metrics, "meter": None})) / 200_000
    overhead = DISABLED_CHECKS_PER_COMMAND * per_check
    assert overhead < command * 0.001, (overhead, command)
