# User intent: measure the team server under the load the user set (50-70 concurrent agents each claiming, writing and reading, plus one key holding its in-flight limit of deadline-length queries), on the shipped unit's limits, and record latencies, errors, claim correctness and the daemon's CPU use; the number of agents and the duration are parameters, so the full run is a parameter change (team-server-015 S10 load, review I6, R1).
"""Agent-swarm and single-key heavy-query probes against a running host."""
from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import serve_helpers
from taskmaster.serve.principals import add_user

from . import procid
from .member import OFF_HOST_XFF

HEAVY_SQL = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 1000000000) "
             "SELECT count(*) FROM c")            # runs until backlog_query's QUERY_TIMEOUT_S deadline


@dataclass
class Recorder:
    lock: threading.Lock = field(default_factory=threading.Lock)
    latencies: dict[str, list[float]] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    error_texts: dict[str, int] = field(default_factory=dict)      # what an unclassed "Error..." answer said, so evidence names the cause
    notes: list[str] = field(default_factory=list)

    def timed(self, op: str, call):
        started = time.monotonic()
        try:
            text = call()
        except Exception as exc:  # noqa: BLE001 - a transport failure is data, classed by its type and status
            took = time.monotonic() - started
            code = getattr(getattr(exc, "response", None), "status_code", None)
            self.add(op, took, "throttled" if code == 429 else f"exception:{type(exc).__name__}" + (f":{code}" if code else ""))
            return None
        took = time.monotonic() - started
        kind = None
        if text.startswith("Error"):
            kind = "locked" if "locked by another session" in text else "rate" if "rate limited" in text else "error"
            if kind == "error":
                with self.lock:
                    self.error_texts[text[:160]] = self.error_texts.get(text[:160], 0) + 1
        self.add(op, took, kind)
        return text

    def add(self, op: str, took: float, error: str | None = None) -> None:
        with self.lock:
            self.latencies.setdefault(op, []).append(took)
            if error:
                key = f"{op}:{error}"
                self.errors[key] = self.errors.get(key, 0) + 1

    def summary(self) -> dict:
        def pct(values, q):
            ordered = sorted(values)
            return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 4)
        return {op: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 4),
                     "mean": round(statistics.fmean(v), 4)} for op, v in sorted(self.latencies.items())}


def make_agents(root, count: int):
    """`count` member keys, `agent-NN`, minted the way the operator would."""
    return [(f"agent-{i:02d}", add_user(root, f"agent-{i:02d}", name=f"Agent {i}", emails=[f"agent{i}@twohost.invalid"])[0])
            for i in range(count)]


def _bump(counts: dict, recorder: "Recorder", name: str, by: int = 1) -> None:
    with recorder.lock:                      # 60 threads: an unlocked += loses updates (015 gate M6)
        counts[name] += by


def swarm(host, root, *, agents: int, seconds: float, think: float = 0.3, recorder: Recorder | None = None) -> dict:
    """Each agent holds one MCP connection and loops add-task, pick (claim), read, update, release for `seconds`;
    then all agents race to pick one shared task, exactly one may win. Returns the swarm's counts and consistency."""
    recorder = recorder or Recorder()
    keys = make_agents(root, agents)
    connections = [serve_helpers.open_connection(host.url, key, headers=OFF_HOST_XFF) for _, key in keys]
    owned: dict[str, str] = {}                  # task id -> handle, from each agent's own successful picks
    counts = {"cycles": 0, "claims": 0, "writes": 0, "reads": 0}
    stop_at = time.monotonic() + seconds
    shared_barrier = threading.Barrier(agents)
    pause = threading.Event()                   # never set: wait() is the think-time pause

    def cycle(index: int):
        handle, _ = keys[index]
        connection = connections[index]
        while time.monotonic() < stop_at:
            added = recorder.timed("write:add_task", lambda: connection.call(
                "backlog_add_task", {"title": f"{handle} work", "epic": "test-epic", "phase": "dev"}))
            task = None
            if added:
                import re
                found = re.search(r"`(test-epic-\d+)`", added)
                task = found.group(1) if found else None
            if task:
                _bump(counts, recorder, "writes")
                picked = recorder.timed("claim:pick", lambda: connection.call("backlog_pick_task", {"task_id": task}))
                if picked and picked.startswith("Picked"):
                    owned[task] = handle
                    _bump(counts, recorder, "claims")
                recorder.timed("read:get_task", lambda: connection.call("backlog_get_task", {"task_id": task}))
                recorder.timed("write:update_task", lambda: connection.call(
                    "backlog_update_task", {"task_id": task, "field": "priority", "value": "high"}))
                recorder.timed("claim:release", lambda: connection.call(
                    "backlog_claim", {"action": "release", "task_id": task}))
            recorder.timed("read:list_tasks", lambda: connection.call("backlog_list_tasks", {"limit": 20}))
            recorder.timed("read:query", lambda: connection.call(
                "backlog_query", {"sql": "SELECT count(*) FROM entities WHERE kind='task'"}))
            _bump(counts, recorder, "reads", 3)
            _bump(counts, recorder, "cycles")
            pause.wait(think)                   # an agent thinks between steps; 0 measures raw throughput

    def contest(index: int):
        connection = connections[index]
        shared_barrier.wait(60)
        text = recorder.timed("claim:contested_pick", lambda: connection.call("backlog_pick_task", {"task_id": "test-epic-001"}))
        return text or ""

    started_cpu, started = procid.cpu_seconds_tree(int(host.ready["pid"])), time.monotonic()
    try:
        with ThreadPoolExecutor(agents) as pool:
            list(pool.map(cycle, range(agents)))
            winners = [t for t in pool.map(contest, range(agents)) if t.startswith("Picked")]
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception as exc:  # noqa: BLE001
                recorder.notes.append(f"close: {type(exc).__name__}")
    wall = time.monotonic() - started
    ended_cpu = procid.cpu_seconds_tree(int(host.ready["pid"]))
    return {"agents": agents, "seconds": seconds, "wall_seconds": round(wall, 2), **counts,
            "ops_per_second": round(sum(len(v) for v in recorder.latencies.values()) / wall, 1),
            "think_seconds": think, "contested_pick_winners": len(winners), "contested_task": "test-epic-001",
            "throttled": sum(v for k, v in recorder.errors.items() if k.endswith(":throttled")), "latency": recorder.summary(), "errors": recorder.errors,
            "daemon_cpu_seconds_per_wall_second": (round((ended_cpu - started_cpu) / wall, 3)
                                                   if None not in (started_cpu, ended_cpu) else None),
            "notes": recorder.notes}


def heavy_single_key(host, key: str, bystander_key: str, *, calls: int, seconds_of_bystander: float = 8.0) -> dict:
    """One key sends `calls` deadline-length backlog_query calls at once (the server runs HEAVY_PER_KEY of them and
    refuses the rest, security review I-1); meanwhile a different key's light read is timed. Returns the daemon's CPU use, the heavy calls' answers and the bystander's latency."""
    recorder = Recorder()
    connections = [serve_helpers.open_connection(host.url, key, headers=OFF_HOST_XFF) for _ in range(calls)]
    bystander = serve_helpers.open_connection(host.url, bystander_key, headers=OFF_HOST_XFF)
    answers: list[str] = []
    pid = int(host.ready["pid"])
    started_cpu, started = procid.cpu_seconds_tree(pid), time.monotonic()
    stop_probe = threading.Event()

    def probe():
        while not stop_probe.is_set():
            recorder.timed("bystander:list_tasks", lambda: bystander.call("backlog_list_tasks", {"limit": 5}))
            stop_probe.wait(0.2)

    prober = threading.Thread(target=probe, daemon=True)
    prober.start()
    try:
        with ThreadPoolExecutor(calls) as pool:
            answers = list(pool.map(lambda c: recorder.timed("heavy:query", lambda: c.call(
                "backlog_query", {"sql": HEAVY_SQL}, timeout=120)) or "", connections))
        stop_probe.wait(1.0)
    finally:
        stop_probe.set()
        prober.join(30)
        for connection in [*connections, bystander]:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
    wall = time.monotonic() - started
    ended_cpu = procid.cpu_seconds_tree(pid)
    return {"heavy_calls": calls, "wall_seconds": round(wall, 2), "latency": recorder.summary(),
            "errors": recorder.errors, "error_texts": recorder.error_texts, "timed_out_answers": sum("timed out" in a.lower() or "timeout" in a.lower() for a in answers),
            "daemon_cpu_seconds_per_wall_second": (round((ended_cpu - started_cpu) / wall, 3)
                                                   if None not in (started_cpu, ended_cpu) else None),
            "sample_answer": (answers[0] if answers else "")[:200]}
