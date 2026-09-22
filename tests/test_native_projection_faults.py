"""User intent: prove the N11 outbox recovers from a crash at every protocol boundary
(§5.2): each cell is injected twice, as an in-process exception and as `os._exit` in a
subprocess followed by recovery in this process, and after recovery every file holds
the newest revision, losslessly, with no false flag. Plus the two interleavings the
plan names — a newer database edit and an external file edit landing while an old
job renders — forced by checkpoints, and one real two-process run.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native import commands
from taskmaster.native import projection as outbox
from taskmaster.native_routing import projection
from tests import native_projection_oracle as runtime
from native_twins import commit_only, make_twins, native_connection, native_database

LIVE, ARCHIVED = "tasks/test-epic-001.md", "tasks/archive/test-epic-001.md"


class Crash(BaseException):
    """An injected crash: BaseException, so no handler on the way out can swallow it."""


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True)


def _dir(root):
    return root / ".taskmaster"


# ── Oracles ─────────────────────────────────────────────────────────────────


def _entity(connection, kind, ident):
    connection.execute("BEGIN")
    try:
        from taskmaster.native.queries import Snapshot
        from taskmaster.native import db
        snapshot = Snapshot(connection, db.manifest(connection, authorities=("native",)))
        try:
            return snapshot.get(kind, ident, include_body=True)
        finally:
            snapshot.active = False
    finally:
        connection.rollback()


def assert_lossless(root):
    """Every exported entity file parses back, through the legacy parser, to the
    entity it was rendered from; `backlog.yaml` matches a fresh render by document;
    nothing is pending, claimed, conflicted or flagged; no stray temp is left."""
    backlog_dir = _dir(root)
    with native_connection(root) as connection:
        assert outbox.flagged_files(connection) == ()
        assert connection.execute("SELECT file,state FROM projection_jobs WHERE state IN "
                                  "('pending','claimed','conflict')").fetchall() == []
        records = connection.execute("SELECT file,input_json FROM projection_jobs WHERE state='exported'").fetchall()
        for rel, payload in records:
            entity = json.loads(payload)
            current = _entity(connection, entity["kind"], entity["id"])
            path = backlog_dir / rel
            recorded = connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
            content, oracle = store.render_entity_file(entity["kind"], current["fields"], current.get("body"))
            if recorded is None:
                assert not path.exists(), f"{rel} should be absent (tombstone)"
                continue
            data = path.read_bytes()
            assert hashlib.sha1(data).hexdigest() == recorded[0], f"{rel}: record disagrees with disk"
            doc, body = store.Store._parse_entity_text(None, entity["kind"], data.decode("utf-8"))
            wanted_doc, wanted_body = oracle
            assert doc == store._clean_doc(dict(wanted_doc)), f"{rel} lost authored fields"
            assert body == ((wanted_body or "").removesuffix("\n") or None), f"{rel} lost its body"
        render = projection._Render(connection, backlog_dir)
        connection.execute("BEGIN")
        try:
            from taskmaster.native.queries import Snapshot
            from taskmaster.native import db
            snapshot = Snapshot(connection, db.manifest(connection, authorities=("native",)))
            try:
                fresh = render.backlog(snapshot)
            finally:
                snapshot.active = False
        finally:
            connection.rollback()
    assert yaml.safe_load((backlog_dir / "backlog.yaml").read_bytes()) == yaml.safe_load(fresh)
    assert [p.name for p in backlog_dir.rglob("*.tmp.j*")] == []


def _recover(root, monkeypatch, *, skip_lease=False):
    """A fresh drain, as the next tool call makes one; past a dead exporter's lease when asked."""
    monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
    if skip_lease:
        monkeypatch.setitem(projection.HOOKS, "clock", lambda: time.time() + outbox.LEASE_SECONDS + 1)
    with native_connection(root) as connection:
        notices = projection.drain(connection, _dir(root), session="recovery")
        through = outbox.exported_through(connection)
        high = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
    assert notices == [], notices
    assert through >= high
    return notices


# ── The crash harness ───────────────────────────────────────────────────────

CHILD = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from taskmaster.native_routing import projection
    from tests import native_projection_oracle as runtime
    root, stage, target, operation, arguments = sys.argv[1:6]

    def checkpoint(at, rel):
        if at == stage and target in ("*", rel):
            os._exit(17)

    projection.HOOKS["checkpoint"] = checkpoint
    backlog_dir = Path(root) / ".taskmaster"
    with runtime.open_call(backlog_dir / "local" / "store.db", backlog_dir, "child") as call:
        call.execute(operation, json.loads(arguments))
    os._exit(0)
""")


def _crash(root, monkeypatch, variant, stage, target, operation, arguments):
    """Run one command whose export dies at `stage` for file `target` ("*": any)."""
    if variant == "exit":
        done = subprocess.run([sys.executable, "-c", CHILD, str(root), stage, target, operation,
                               json.dumps(arguments)], capture_output=True, text=True, timeout=120,
                              env=runtime.child_environment(),
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert done.returncode == 17, (done.returncode, done.stderr[-2000:])
        return

    def checkpoint(at, rel):
        if at == stage and target in ("*", rel):
            raise Crash(f"crash at {stage} {rel}")

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with runtime.open_call(native_database(root), _dir(root), "parent") as call:
        with pytest.raises(Crash):
            call.execute(operation, arguments)
    monkeypatch.setitem(projection.HOOKS, "checkpoint", None)


def _states(root, rel):
    with native_connection(root) as connection:
        return [s for (s,) in connection.execute(
            "SELECT state FROM projection_jobs WHERE file=? ORDER BY commit_seq,job_key", (rel,))]


def _record(root, rel):
    with native_connection(root) as connection:
        row = connection.execute("SELECT content_hash FROM projection WHERE file=?", (rel,)).fetchone()
    return None if row is None else row[0]


def _sha(root, rel):
    return hashlib.sha1((_dir(root) / rel).read_bytes()).hexdigest()


VARIANTS = ("exception", "exit")
PATCH = ("task.update", {"id": "test-epic-001", "field": "notes", "value": "crash revision"})


def _patched(root):
    return b"crash revision" in (_dir(root) / LIVE).read_bytes()


# ── §5.2, one cell per test ────────────────────────────────────────────────


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_before_the_db_commit_leaves_nothing_and_a_retry_executes_once(twins, variant):
    root = twins.native
    before = (_dir(root) / LIVE).read_bytes()
    with native_connection(root) as connection:
        store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
        high = connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0]
        jobs = connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0]
    envelope = {"protocol": 2, "store_id": store_id, "caller_scope": "tests", "request_id": "once",
                "operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": "Once"}},
                "expected_revisions": []}
    if variant == "exit":
        child = textwrap.dedent("""
            import json, os, sqlite3, sys
            from taskmaster.native import commands
            connection = sqlite3.connect(sys.argv[1], isolation_level=None)
            commands.execute(connection, json.loads(sys.argv[2]),
                             checkpoint=lambda stage: os._exit(17) if stage == "before_commit" else None)
            os._exit(0)
        """)
        done = subprocess.run([sys.executable, "-c", child, str(native_database(root)), json.dumps(envelope)],
                              capture_output=True, text=True, timeout=120,
                              env=runtime.child_environment(),
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert done.returncode == 17, done.stderr[-2000:]
    else:
        with native_connection(root) as connection, pytest.raises(Crash):
            def checkpoint(stage):
                if stage == "before_commit":
                    raise Crash("before commit")
            commands.execute(connection, envelope, checkpoint=checkpoint)
    with native_connection(root) as connection:
        assert connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0] == high
        assert connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0] == jobs
        first = commands.execute(connection, envelope)
        again = commands.execute(connection, envelope)
        assert first == again
        assert connection.execute("SELECT COUNT(*) FROM domain_events WHERE seq>?", (high,)).fetchone()[0] == 1
    assert (_dir(root) / LIVE).read_bytes() == before


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_after_the_commit_before_the_claim(twins, monkeypatch, variant):
    root = twins.native
    _crash(root, monkeypatch, variant, "claim", "", *PATCH)
    assert _states(root, LIVE)[-1] == "pending" and not _patched(root)
    _recover(root, monkeypatch)
    assert _patched(root)
    assert_lossless(root)


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_after_the_claim_before_the_temp_write(twins, monkeypatch, variant):
    root = twins.native
    _crash(root, monkeypatch, variant, "before_write", LIVE, *PATCH)
    assert _states(root, LIVE)[-1] == "claimed" and not _patched(root)
    if variant == "exit":
        # The dead exporter's lease is live: a caller times out with the pending notice.
        fake = {"at": time.time()}
        monkeypatch.setitem(projection.HOOKS, "clock", lambda: fake["at"])
        monkeypatch.setitem(projection.HOOKS, "sleep", lambda s: fake.__setitem__("at", fake["at"] + s))
        with native_connection(root) as connection:
            notices = projection.drain(connection, _dir(root), session="waiter", through=10 ** 9)
        assert notices == [f"export pending: {LIVE} — retried on next call"]
    _recover(root, monkeypatch, skip_lease=variant == "exit")
    assert _patched(root)
    assert_lossless(root)


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_after_the_temp_write_before_the_replace(twins, monkeypatch, variant):
    root = twins.native
    foreign = _dir(root) / "tasks" / "test-epic-001.md.tmp.someone-else"
    foreign.write_bytes(b"another writer's temp\n")
    _crash(root, monkeypatch, variant, "temp_written", LIVE, *PATCH)
    strays = sorted(p.name for p in (_dir(root) / "tasks").glob("test-epic-001.md.tmp.j*"))
    assert len(strays) == 1 and not _patched(root)
    _recover(root, monkeypatch, skip_lease=variant == "exit")
    assert _patched(root)
    assert foreign.read_bytes() == b"another writer's temp\n", "only this job's temp may be touched"
    foreign.unlink()
    assert_lossless(root)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("stage", ["replaced", "ack_manifest"])
def test_crash_after_the_replace_before_or_during_the_ack(twins, monkeypatch, variant, stage):
    root = twins.native
    old_record = _record(root, LIVE)
    _crash(root, monkeypatch, variant, stage, LIVE, *PATCH)
    assert _patched(root), "the file has the new bytes"
    assert _record(root, LIVE) == old_record != _sha(root, LIVE), "the record still has the old hash"
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append(str(b)), real(a, b))[1])
    _recover(root, monkeypatch, skip_lease=variant == "exit")
    assert not [p for p in replaced if p.endswith("test-epic-001.md")], "own bytes are acked, not rewritten"
    assert _record(root, LIVE) == _sha(root, LIVE)
    assert_lossless(root)


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_after_the_ack_before_retention(twins, monkeypatch, variant):
    root = twins.native
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="earlier")
    _crash(root, monkeypatch, variant, "retention", "", *PATCH)
    assert _states(root, LIVE) == ["exported", "exported"] and _patched(root)
    _recover(root, monkeypatch, skip_lease=variant == "exit")
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="crash revision, again")
    assert _states(root, LIVE) == ["exported"]
    assert_lossless(root)


@pytest.mark.parametrize("variant", VARIANTS)
def test_crash_between_a_moves_new_write_and_old_removal(twins, monkeypatch, variant):
    root = twins.native
    _crash(root, monkeypatch, variant, "acked", ARCHIVED, "task.archive", {"id": "test-epic-001", "reason": "wont-fix"})
    assert (_dir(root) / LIVE).exists() and (_dir(root) / ARCHIVED).exists(), "two copies, never none"
    _recover(root, monkeypatch, skip_lease=variant == "exit")
    assert not (_dir(root) / LIVE).exists() and (_dir(root) / ARCHIVED).exists()
    with native_connection(root) as connection:
        assert outbox.export_record(connection, LIVE)["tombstone"]
    assert_lossless(root)


# ── The two interleavings ───────────────────────────────────────────────────


def _peer_patch(root, **fields):
    with closing(sqlite3.connect(native_database(root), isolation_level=None, timeout=30)) as peer:
        return commit_only(peer, "task.patch", {"id": "test-epic-001", "set": fields})


def test_a_newer_db_edit_while_an_old_job_renders_publishes_after_it(twins, monkeypatch):
    root = twins.native
    landed = []

    def checkpoint(stage, rel):
        if stage == "temp_written" and rel == LIVE and not landed:
            landed.append(_peer_patch(root, title="Newer revision"))

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="older revision")
    assert b"older revision" in (_dir(root) / LIVE).read_bytes()
    _recover(root, monkeypatch)
    final = (_dir(root) / LIVE).read_bytes()
    assert b"Newer revision" in final and b"older revision" in final
    assert_lossless(root)


def test_a_paused_exporter_past_its_lease_cannot_install_over_its_successor(twins, monkeypatch):
    """§2.3(4): the old exporter passed its fence and set the file aside, paused, lost
    its lease, and a successor published the newer revision. Before the review fix
    (P1) it then replaced anyway, leaving a transient stale file; now the successor's
    recovery puts the aside file back, and the install never overwrites a file, so
    the stale bytes never land."""
    root = twins.native
    stale_clock = {"at": time.time()}
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: stale_clock["at"])
    successor = {}

    def checkpoint(stage, rel):
        if stage == "before_replace" and rel == LIVE and not successor:
            successor["seq"] = _peer_patch(root, title="Successor revision")["commit_seq"]
            later = stale_clock["at"] + outbox.LEASE_SECONDS + 1
            monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
            monkeypatch.setitem(projection.HOOKS, "clock", lambda: later)
            with native_connection(root) as connection:
                assert projection.drain(connection, _dir(root), session="successor") == []
            successor["bytes"] = (_dir(root) / LIVE).read_bytes()
            monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="stale revision")
    assert b"Successor revision" in successor["bytes"]
    assert (_dir(root) / LIVE).read_bytes() == successor["bytes"], "the stale exporter installed over its successor"
    assert "export pending" in answer, answer
    assert [p.name for p in (_dir(root) / "tasks").iterdir() if ".aside." in p.name or ".tmp.j" in p.name] == []
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: time.time() + 10 * outbox.LEASE_SECONDS)
    _recover(root, monkeypatch)
    assert (_dir(root) / LIVE).read_bytes() == successor["bytes"]
    assert_lossless(root)


def test_an_external_edit_while_an_old_job_renders_is_flagged_and_kept(twins, monkeypatch):
    root = twins.native
    path = _dir(root) / LIVE
    edited = {}

    def checkpoint(stage, rel):
        if stage == "temp_written" and rel == LIVE and not edited:
            edited["bytes"] = path.read_bytes() + b"\nEdited while the export rendered.\n"
            path.write_bytes(edited["bytes"])

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store revision")
    assert f"export pending: {LIVE} is flagged" in answer
    assert path.read_bytes() == edited["bytes"]
    with native_connection(root) as connection:
        kept = connection.execute("SELECT file_content FROM projection_conflict WHERE file=?", (LIVE,)).fetchone()[0]
    assert bytes(kept) == edited["bytes"]
    assert not list((_dir(root) / "tasks").glob("test-epic-001.md.tmp.*"))
    monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="title", value="Newest while flagged")
        resolved = bs.backlog_resolve_conflict(file=LIVE, take="store")
    assert resolved.startswith(f"Resolved {LIVE}"), resolved
    final = path.read_bytes()
    assert b"Newest while flagged" in final and b"Edited while the export rendered." not in final
    assert_lossless(root)


# ── One real two-process run ────────────────────────────────────────────────

WRITER = textwrap.dedent("""
    import sys
    from pathlib import Path
    from tests import native_projection_oracle as runtime
    root, name, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
    backlog_dir = Path(root) / ".taskmaster"
    for n in range(rounds):
        with runtime.open_call(backlog_dir / "local" / "store.db", backlog_dir, name) as call:
            call.execute("task.update", {"id": "test-epic-001", "field": "notes", "value": f"{name} {n}"})
            call.execute("task.update", {"id": f"test-epic-00{2 if name == 'b' else 1}", "field": "title",
                                         "value": f"{name} title {n}"})
""")


def test_two_processes_exporting_one_store_converge_without_a_flag(twins, monkeypatch):
    root = twins.native
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, str(root), name, "12"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=runtime.child_environment(),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for name in ("a", "b")]
    for writer in writers:
        _out, err = writer.communicate(timeout=600)
        assert writer.returncode == 0, err[-3000:]
    _recover(root, monkeypatch)
    assert_lossless(root)
