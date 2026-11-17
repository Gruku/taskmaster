# User intent: one member's backlog_query must not take the team server's memory or cores (security review I-1 and
# its re-review): the statement runs in a worker process killed at the deadline and memory-capped on Linux, SQLite's
# limits are fixed (never derived from stored data), value-building functions are refused, the store copy runs under
# the deadline under a fixed snapshot budget (a store members grew past it is refused, not copied), the answer has a
# byte budget, and heavy calls are capped per key and for the whole server.
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from serve_helpers import open_connection
from taskmaster import query_guard
from taskmaster.native import compatibility, query_worker
from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from taskmaster.serve import config, guard
from test_native_migration import legacy  # noqa: F401
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import launch  # noqa: F401

# Runs until backlog_query's deadline (the twohost load module's heavy statement).
HEAVY_SQL = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 1000000000) "
             "SELECT count(*) FROM c")


@pytest.fixture
def snapshot(legacy):  # noqa: F811
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            yield query


# `z` is a 256,000-character constant built from one literal with `||` alone (no denied function, and a literal is
# capped at 64 KiB of SQL): the CTEs are flattened, so every `z` below is a constant expression.
BIG_CTES = ("a(x) AS (SELECT '" + "a" * 1000 + "'), b(y) AS (SELECT " + "||".join(["x"] * 16) + " FROM a), "
            "c(z) AS (SELECT " + "||".join(["y"] * 16) + " FROM b)")
# The re-review's (a): SQLite factors each `z||k` out of the loop into its own register, so every term's values stay
# alive at once although each is far under the value limit. 40 terms grew the daemon by about 236 MiB in 0.3 s (the
# re-review's 900-term form: +685 MiB); kept small so the Windows run, which has no memory cap, stays cheap.
FACTORED = f"WITH {BIG_CTES} SELECT 1 FROM c WHERE " + " AND ".join(f"length(z||{i})" for i in range(40))
# The re-review's (b): one instr() over built values is one VM step of about 0.6 s, so the progress handler never
# runs inside it; ten of them ran about 6 s on a 1 s deadline.
INSTR = (f"WITH {BIG_CTES} SELECT "
         + " + ".join(f"instr(z, substr(z, 1, 127999)||'{i}')" for i in range(10)) + " FROM c")


def _private_bytes():
    """This process's private memory: the daemon's, when the daemon runs in-process (the `launch` fixture)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        info = kernel.K32GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        info(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.PagefileUsage
    with open("/proc/self/status", encoding="ascii") as handle:
        fields = dict(line.split(":", 1) for line in handle if ":" in line)
    return (int(fields["RssAnon"].split()[0]) if "RssAnon" in fields else int(fields["VmRSS"].split()[0])) * 1024


def peak_growth(work):
    """(work's result or exception, the largest growth of this process's private memory while it ran, seconds)."""
    base = _private_bytes()
    peak, stop = [base], threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], _private_bytes())
            time.sleep(0.0005)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    try:
        out = work()
    except Exception as exc:  # noqa: BLE001 - the caller asserts on what came back
        out = exc
    elapsed = time.monotonic() - started
    stop.set()
    sampler.join()
    return out, peak[0] - base, elapsed


@pytest.mark.parametrize("sql", [
    "SELECT length(randomblob(50000000)) AS n",
    "SELECT length(hex(zeroblob(25000000))) AS n",
    "SELECT length(printf('%.*c', 50000000, 'x'))",
    "SELECT length(format('%.*c', 50000000, 'x'))",
    "SELECT length(char(65, 66))",
    "SELECT quote(1)",
    "SELECT unhex('00')",
])
def test_value_building_functions_are_refused_by_name_when_served(snapshot, sql, monkeypatch):
    from taskmaster.coordinator import client
    monkeypatch.setattr(client, "ORIGIN_PROVIDER", lambda: None)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        snapshot.sql(sql)


def test_a_solo_session_keeps_the_value_functions(snapshot):
    # The deny list guards a shared server's memory; solo backlog_query keeps hex, printf and the rest.
    assert snapshot.sql("SELECT hex('ab'), printf('%03d', 7), char(65), quote('x')")["rows"] == [("6162", "007", "A", "'x'")]


def test_a_statement_cannot_build_a_value_past_the_fixed_limit(snapshot):
    sql = ("WITH RECURSIVE r(i, s) AS (SELECT 0, 'x' UNION ALL SELECT i + 1, s || s FROM r WHERE i < 26) "
           "SELECT max(length(s)) FROM r")
    with pytest.raises(ValueError, match=f"string or blob too big.*{query_worker.VALUE_LENGTH_LIMIT} bytes"):
        snapshot.sql(sql)


def test_a_statement_cannot_select_very_many_columns(snapshot):
    sql = "SELECT " + ", ".join(f"'x' AS c{i}" for i in range(compatibility.MAX_COLUMNS + 1))
    with pytest.raises(sqlite3.OperationalError, match="too many columns"):
        snapshot.sql(sql)


def test_stored_values_are_cut_on_the_copy_and_every_table_stays_readable(snapshot, monkeypatch):
    # The value limit is a constant; a stored value wider than the copy's cap is cut, so a member's large note can
    # neither widen what a statement may build (the re-review's 900 KB note) nor become unreadable.
    monkeypatch.setattr(compatibility, "VALUE_COPY_MAX", 64)
    for table in query_guard.TABLES:
        snapshot.sql(f"SELECT * FROM {table} ORDER BY 1")
    widest = snapshot.sql("SELECT max(length(CAST(doc AS BLOB))), max(length(CAST(body AS BLOB))) FROM entities")
    assert max(widest["rows"][0]) == 64, widest


def test_the_value_limit_does_not_follow_the_data():
    source = Path(compatibility.__file__).read_text(encoding="utf-8")
    assert "widest" not in source and "VALUE_HEADROOM" not in source
    assert query_worker.VALUE_COPY_MAX * 2 < query_worker.VALUE_LENGTH_LIMIT


def test_the_store_copy_runs_under_the_deadline(snapshot, monkeypatch):
    real = compatibility.iter_entities

    def slow(connection, **kwargs):
        # A store whose rebuild takes 13 x 0.2 s; the deadline is 0.1 s.
        for _ in range(13):
            if kwargs.get("check"):
                kwargs["check"]()
            time.sleep(0.2)
        yield from real(connection)

    monkeypatch.setattr(compatibility, "iter_entities", slow)
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        snapshot.sql("SELECT 1", timeout=0.1)
    # Stopped inside the copy, not after it (the copy used to run in full before the deadline was armed).
    assert time.monotonic() - started < 1.0


def test_the_answer_has_a_byte_budget(snapshot, monkeypatch):
    monkeypatch.setattr(compatibility, "RESULT_MAX_BYTES", 200)
    with pytest.raises(ValueError, match="answer is larger than"):
        snapshot.sql("SELECT doc FROM entities")
    assert snapshot.sql("SELECT id FROM entities LIMIT 2")["rows"]


def test_sorting_and_set_operations_spill_to_disk_and_stop_near_the_deadline(snapshot):
    # Each step adds a distinct 256 KB value to the UNION's set; at 10,000 steps per deadline check a 5 s deadline
    # ran for about 20 s.
    sql = (f"WITH RECURSIVE {BIG_CTES}, r(i, s) AS (SELECT 0, z FROM c UNION SELECT i + 1, s || i FROM r "
           "WHERE i < 100000) SELECT count(*) FROM r")
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        snapshot.sql(sql, timeout=1.0)
    assert time.monotonic() - started < 3.0


def test_factored_constants_do_not_grow_the_daemon(snapshot):
    out, growth, _ = peak_growth(lambda: snapshot.sql(FACTORED))
    assert growth < 64 * 2**20, f"the daemon grew by {growth >> 20} MiB ({out!r:.200})"
    if sys.platform != "win32":
        # Linux: the worker's address-space cap stops the statement itself (RLIMIT_AS).
        assert isinstance(out, sqlite3.Error) and "memory cap" in str(out), repr(out)[:300]


def test_a_single_long_step_stops_near_the_deadline(snapshot):
    out, _, elapsed = peak_growth(lambda: snapshot.sql(INSTR, timeout=1.0))
    assert isinstance(out, sqlite3.OperationalError) and "interrupt" in str(out), repr(out)[:300]
    # 1 s deadline, the kill's 0.5 s grace, and a worker start: it ran about 6 s before.
    assert elapsed < 3.0, elapsed


def test_a_killed_worker_is_replaced(snapshot):
    with pytest.raises(sqlite3.OperationalError, match="interrupt"):
        snapshot.sql(INSTR, timeout=0.5)
    assert snapshot.sql("SELECT count(*) FROM entities")["rows"] == [(13,)]


def test_legitimate_queries_keep_their_answers(snapshot):
    result = snapshot.sql("SELECT kind, id, json_extract(doc, '$.title') FROM entities ORDER BY kind, id")
    assert result["materialized_rows"]["entities"] == 13 and len(result["rows"]) == 13
    assert snapshot.sql("SELECT id FROM entity_fts WHERE entity_fts MATCH 'run'")["rows"] == [("same",)]
    assert snapshot.sql("SELECT group_concat(id) FROM entities")["rows"][0][0]
    assert snapshot.sql("SELECT x'00ff' AS b, 1.5 AS r, NULL AS n")["rows"] == [(b"\x00\xff", 1.5, None)]
    assert snapshot.sql("SELECT upper(substr(id, 1, 3)), instr(id, '-'), length(id) FROM entities LIMIT 1")["rows"]


# -- per-key and server-wide caps, over real HTTP -------------------------------------------------------------------


def _heavy(world, who, sql=HEAVY_SQL):
    connection = open_connection(world.url, world.keys[who].key)
    try:
        return connection.call("backlog_query", {"sql": sql})
    finally:
        connection.close()


def test_one_key_runs_at_most_its_heavy_calls_and_others_are_not_charged(launch):  # noqa: F811
    world = launch()
    with ThreadPoolExecutor(4) as pool:
        bobs = [pool.submit(_heavy, world, "bob") for _ in range(config.HEAVY_PER_KEY + 1)]
        time.sleep(1.5)                       # bob's calls are running; ann's key has its own allowance
        ann = pool.submit(_heavy, world, "ann", "SELECT count(*) FROM entities")
        answers = [f.result(120) for f in bobs]
        assert "rows" in ann.result(120) and "rate limited" not in ann.result(120)
    refused = [a for a in answers if f"at most {config.HEAVY_PER_KEY} backlog_query" in a and "per key" in a]
    assert len(refused) == 1, answers
    assert all("query exceeded" in a for a in answers if a not in refused), answers


def test_the_read_endpoint_batch_counts_against_the_same_key(launch):  # noqa: F811
    world = launch()
    with ThreadPoolExecutor(2) as pool:
        running = [pool.submit(_heavy, world, "bob") for _ in range(config.HEAVY_PER_KEY)]
        time.sleep(1.5)
        reply = httpx.post(world.url.replace("/mcp", "/read/v1/tools"), timeout=60,
                           headers={"Authorization": f"Bearer {world.keys['bob'].key}"},
                           json={"calls": [{"tool": "backlog_query", "args": {"sql": "SELECT 1"}}]})
        [f.result(120) for f in running]
    result = reply.json()["data"]["results"][0]
    assert result["is_error"] and "per key" in result["text"], result


def test_the_server_runs_a_bounded_number_of_heavy_calls(launch, monkeypatch):  # noqa: F811
    monkeypatch.setattr(guard, "HEAVY_TOTAL", 1)
    monkeypatch.setattr(guard, "HEAVY_QUEUE_SECONDS", 1.0)
    world = launch(users=("ann", "bob"))
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(_heavy, world, "bob")
        time.sleep(1.5)
        second = pool.submit(_heavy, world, "ann", "SELECT 1")
        answers = [first.result(120), second.result(120)]
    assert "query exceeded" in answers[0], answers
    assert "heavy calls for the team" in answers[1], answers
    # Once the running call answers, the slot is free again.
    assert "rate limited" not in _heavy(world, "ann", "SELECT 1")


def test_the_operator_is_not_limited(launch):  # noqa: F811
    world = launch()
    lock = threading.Barrier(3)

    def operator_heavy():
        connection = open_connection(world.url, world.operator)
        try:
            lock.wait(30)
            return connection.call("backlog_query", {"sql": HEAVY_SQL})
        finally:
            connection.close()

    with ThreadPoolExecutor(3) as pool:
        answers = list(pool.map(lambda _: operator_heavy(), range(3)))
    assert not any("rate limited" in a for a in answers), json.dumps(answers)[:500]


def test_a_members_huge_value_is_refused_over_http(launch):  # noqa: F811
    # The review's P4 statement, at the size it ran: it answered 300000000 and grew the daemon by about 300 MB.
    world = launch()
    answer = _heavy(world, "bob", "SELECT length(randomblob(300000000)) AS n")
    assert answer.startswith("Error: not authorized: FUNCTION randomblob"), answer


def test_two_factored_statements_at_once_do_not_grow_the_daemon_over_http(launch):  # noqa: F811
    # The re-review over HTTP: two heavy calls at once took the daemon to +1,366 MiB.
    world = launch()

    def both():
        with ThreadPoolExecutor(2) as pool:
            return [f.result(120) for f in [pool.submit(_heavy, world, who, FACTORED) for who in ("ann", "bob")]]

    answers, growth, _ = peak_growth(both)
    assert growth < 128 * 2**20, f"the daemon grew by {growth >> 20} MiB ({answers!r:.300})"
    assert not any("rate limited" in a for a in answers), answers


def test_the_caps_fit_the_units_memory():
    # query_worker.py's budget: the daemon's copies plus HEAVY_TOTAL capped workers fit MemoryMax=1G with margin.
    unit = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "taskmaster.service"
    assert "MemoryMax=1G" in unit.read_text(encoding="utf-8") and "OOMPolicy=continue" in unit.read_text(encoding="utf-8")
    per_call = (2.2 * compatibility.SNAPSHOT_MAX_BYTES + compatibility.COPY_BATCH_BYTES) + query_worker.WORKER_MEMORY_MAX
    assert 300 * 2**20 + config.HEAVY_TOTAL * per_call < 0.8 * 2**30


def test_a_store_members_grew_is_refused_without_growing_the_daemon(launch):  # noqa: F811
    # Re-review 2: 100 writes of a 900 KB note made every SELECT 1 cost about 1.09 GiB (daemon copy plus worker).
    # Each write adds history (the field before and after), so this many writes pass the snapshot budget.
    world = launch()
    writes = compatibility.SNAPSHOT_MAX_BYTES // (2 * 900_000) + 4
    connection = open_connection(world.url, world.keys["bob"].key)
    try:
        assert "rows" in connection.call("backlog_query", {"sql": "SELECT 1 AS n"})
        for i in range(writes):
            answer = connection.call("backlog_update_task", {"task_id": "test-epic-001", "field": "notes",
                                                             "value": f"{i:04d}" + "y" * 900_000})
            assert not answer.startswith("Error"), answer[:300]
    finally:
        connection.close()
    answer, growth, elapsed = peak_growth(lambda: _heavy(world, "ann", "SELECT 1 AS n"))
    assert "too large for backlog_query on this server" in answer, answer[:300]
    # The copy stops at the budget: the daemon held at most about the budget, never the store's history.
    assert growth < compatibility.SNAPSHOT_MAX_BYTES + 48 * 2**20, f"the daemon grew by {growth >> 20} MiB"
    assert "too large" in _heavy(world, "bob", "SELECT 1 AS n")       # every later call is refused the same way
