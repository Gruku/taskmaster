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
elif fault == "fail_first_any":  # the process's first call of ANY tool fails - the unmeasured prime included
    def first_call_fails(real):
        def call(*args, **kwargs):
            calls["n"] += 1
            return "Error: coordinator not ready" if calls["n"] == 1 else real(*args, **kwargs)
        return call
    for name in ("backlog_list_tasks", "backlog_update_task", "backlog_get_task"):
        setattr(bs, name, first_call_fails(getattr(bs, name)))
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
elif fault == "drop_link":  # acknowledge a link write with an existing sequence, link nothing
    def drop_link(action="", source="", target="", type="relates_to", **_):
        return f"ok: linked {source} -[{type}]-> {target} [seq {high_water()}]"
    bs.backlog_link = drop_link
elif fault == "fake_link_noop":  # link nothing, answer as though the link state already matched
    def fake_link_noop(action="", source="", target="", type="relates_to", **_):
        if action == "remove":
            return f"ok: no-op (no links from {source} to {target})"
        return f"ok: linked {source} -[{type}]-> {target} (no-op, link already present)"
    bs.backlog_link = fake_link_noop
elif fault == "clobber_link":  # each create is acked truthfully, then an unacked commit removes it
    real_link = bs.backlog_link

    def clobber(action="", source="", target="", type="relates_to", **kwargs):
        answer = real_link(action=action, source=source, target=target, type=type, **kwargs)
        if action == "create":
            real_link(action="remove", source=source, target=target, type=type)
        return answer
    bs.backlog_link = clobber
elif fault == "skip_inverse":  # creates commit the source side only, in process, with a real sequence
    import uuid
    from taskmaster.native import workflow
    from taskmaster.native.commands import execute
    real_link, real_inverse = bs.backlog_link, workflow._write_inverse

    def forward_only(transaction, target_kind, target, *, source, link_type, remove=False, fallback=False):
        if remove:
            real_inverse(transaction, target_kind, target, source=source, link_type=link_type, remove=True,
                         fallback=fallback)
    workflow._write_inverse = forward_only

    def skip_inverse(action="", source="", target="", type="relates_to", **kwargs):
        if action != "create":
            return real_link(action=action, source=source, target=target, type=type, **kwargs)
        connection = sqlite3.connect(root / ".taskmaster/local/store.db", isolation_level=None, timeout=60)
        try:
            store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
            receipt = execute(connection, {"protocol": 2, "store_id": store_id, "caller_scope": "n16-fault",
                                           "request_id": uuid.uuid4().hex, "operation": "link.create",
                                           "arguments": {"source": source, "target": target, "type": type,
                                                         "note": ""}, "expected_revisions": []})
        finally:
            connection.close()
        return f"ok: linked {source} -[{type}]-> {target} [seq {receipt['commit_seq']}]"
    bs.backlog_link = skip_inverse
elif fault == "keep_last":  # the client's last remove commits, then the store's link rows come back, no event
    real_link = bs.backlog_link
    removes = {"left": sum(1 for op in json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["ops"]
                           if op.get("tool") == "backlog_link" and op["kw"].get("action") == "remove")}

    def keep_last(action="", source="", target="", type="relates_to", **kwargs):
        if action != "remove":
            return real_link(action=action, source=source, target=target, type=type, **kwargs)
        removes["left"] -= 1
        if removes["left"]:
            return real_link(action=action, source=source, target=target, type=type, **kwargs)
        connection = sqlite3.connect(root / ".taskmaster/local/store.db", timeout=60, isolation_level=None)
        try:
            saved = connection.execute(
                "SELECT d.* FROM declared_links d JOIN entity_core e ON e.entity_key=d.entity_key WHERE d.field='links' "
                "AND ((e.public_id=? AND d.target_id=?) OR (e.public_id=? AND d.target_id=?))",
                (source, target, target, source)).fetchall()
            answer = real_link(action=action, source=source, target=target, type=type, **kwargs)
            for row in saved:
                connection.execute(f"INSERT OR REPLACE INTO declared_links VALUES({','.join('?' * len(row))})", row)
        finally:
            connection.close()
        return answer
    bs.backlog_link = keep_last
elif fault == "die_before_ready":  # a client that crashes during startup
    sys.exit(7)

sys.exit(runner.worker_main(Path(sys.argv[2])))
