# User intent: a runner client process with ONE injected lie (N16_FAULT), so the N16 acceptance
# runner's correctness gates are proven to FAIL on it (the review's fault-injection proofs, kept as tests).
"""Invoked by the runner as `python n16_fault_worker.py worker <spec.json>` in place of the real worker."""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import native_n16_acceptance as runner  # noqa: E402
from taskmaster import backlog_server as bs  # noqa: E402

fault = os.environ.get("N16_FAULT", "")
root = Path(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["root"])


def high_water():
    connection = sqlite3.connect(f"{(root / '.taskmaster/local/store.db').as_uri()}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    finally:
        connection.close()


real_update, real_add, real_batch = bs.backlog_update_task, bs.backlog_add_task, bs.backlog_batch_update
calls = {"n": 0}

if fault == "drop_writes":  # acknowledge with an existing sequence, write nothing
    def drop(task_id, field="", value="", **_):
        return f"Updated `{task_id}` field `{field}` → {value} [seq {high_water()}]"
    bs.backlog_update_task = drop
elif fault == "strip_seq":  # real write, answer without the sequence suffix
    def strip(*args, **kwargs):
        return re.sub(r" \[seq \d+\]", "", real_update(*args, **kwargs))
    bs.backlog_update_task = strip
elif fault == "swallow":  # a failure that does not start with "Error"
    bs.backlog_update_task = lambda *args, **kwargs: "Could not update: store unavailable"
elif fault == "fake_noop":  # write nothing, answer as though the value already matched
    def fake_noop(task_id, field="", value="", **_):
        return f"No change to `{task_id}` field `{field}` — already `{value}`"
    bs.backlog_update_task = fake_noop
elif fault == "fake_noop_half":  # every other update writes nothing and answers as though unchanged
    def half(task_id, field="", value="", **kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            return f"No change to `{task_id}` field `{field}` — already `{value}`"
        return real_update(task_id, field, value, **kwargs)
    bs.backlog_update_task = half
elif fault == "fail_first":  # a client's first (warmup) call fails: a cold-start defect
    real_get = bs.backlog_get_task

    def first(real):
        def call(*args, **kwargs):
            calls["n"] += 1
            return "Error: coordinator not ready" if calls["n"] == 1 else real(*args, **kwargs)
        return call
    bs.backlog_update_task, bs.backlog_get_task = first(real_update), first(real_get)
elif fault == "reuse_id":  # creates report an EXISTING task id (distinct per call)
    existing = sorted(p.stem for p in (root / ".taskmaster/tasks").glob("*.md"))

    def reuse(*args, **kwargs):
        answer = real_add(*args, **kwargs)
        calls["n"] += 1
        return re.sub(r"Added `([^`]+)`", f"Added `{existing[(os.getpid() * 7 + calls['n']) % len(existing)]}`", answer)
    bs.backlog_add_task = reuse
elif fault == "partial_composite":  # an invalid composite commits its valid members, then reports an error
    def partial(*args, commands=None, atomic=None, **kwargs):
        if commands and any(c["arguments"]["id"].startswith("n16-missing") for c in commands):
            real_batch(commands=[c for c in commands if not c["arguments"]["id"].startswith("n16-missing")], atomic=True)
            return "Error: unknown task n16-missing"
        return real_batch(*args, commands=commands, atomic=atomic, **kwargs)
    bs.backlog_batch_update = partial
elif fault == "die_before_ready":  # a client that crashes during startup
    sys.exit(7)

sys.exit(runner.worker_main(Path(sys.argv[2])))
