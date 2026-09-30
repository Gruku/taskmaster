"""User intent: the second adversarial review of the N11 aside/install mechanism
(probes R1-R8), pinned as real tests. Undoing a failed publication never raises out
of a committed command and never leaves the file aside; recorded aside and temp
names are forgotten only once their files are gone; two exporter generations never
share a temp or aside name; and a waiting caller's derived file is judged by its own
export watermark, so a later commit cannot hide the caller's change. The kill-at-
every-step, move, repeated-kill and exact-bytes probes are kept as regressions.
"""
from __future__ import annotations

import base64
from contextlib import closing
import errno
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection as outbox
from taskmaster.native_routing import projection
from tests.native_projection_oracle import child_environment
from native_twins import commit_only, make_twins, native_connection, native_database

LIVE, ARCHIVED = "tasks/test-epic-001.md", "tasks/archive/test-epic-001.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True)


def _dir(root):
    return root / ".taskmaster"


def _strays(root):
    return sorted(p.relative_to(_dir(root)).as_posix() for p in _dir(root).rglob("*")
                  if ".aside." in p.name or ".tmp." in p.name)


def _flags(root):
    with native_connection(root) as connection:
        return outbox.flagged_files(connection)


def _aside_keys(root):
    with native_connection(root) as connection:
        return [r[0] for r in connection.execute("SELECT key FROM sync_state WHERE key LIKE 'projection.aside.%'")]


# ── R6: undo never raises, and restores the aside file first ────────────────


def test_a_failed_undo_reports_pending_and_leaves_the_file_in_place(twins, monkeypatch):
    root = twins.native
    path = _dir(root) / LIVE
    real_rename, real_unlink = os.rename, pathlib.Path.unlink

    def rename(source, target):
        if ".tmp." in str(source):
            raise PermissionError(errno.EACCES, "sharing violation on temp (simulated)")
        return real_rename(source, target)

    def unlink(self, missing_ok=False):
        if ".tmp." in self.name:
            raise PermissionError(errno.EACCES, "sharing violation on temp (simulated)")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    monkeypatch.setattr(outbox.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(outbox, "_RETRY_SECONDS", 0.0)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store rev")
    assert answer.startswith("Updated"), answer
    assert f"export pending: {LIVE} — retried on next call" in answer
    assert path.exists() and b"store rev" not in path.read_bytes(), "the file must be back, not aside"
    monkeypatch.setattr(os, "rename", real_rename)
    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-002", field="title", value="next call")
    assert b"store rev" in path.read_bytes()
    assert _strays(root) == [] and _flags(root) == ()


# ── R5: aside names are forgotten only once their files are gone ────────────


def _failing_aside_unlink(monkeypatch, times):
    real = pathlib.Path.unlink
    hits = []

    def unlink(self, missing_ok=False):
        if ".aside." in self.name and len(hits) < times:
            hits.append(self.name)
            raise PermissionError(errno.EACCES, "sharing violation (simulated)")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    return real, hits


def test_a_transient_aside_unlink_failure_is_retried(twins, monkeypatch):
    root = twins.native
    real, hits = _failing_aside_unlink(monkeypatch, times=1)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store rev")
    assert hits and "export pending" not in answer, answer
    assert _strays(root) == [] and _aside_keys(root) == []


def test_an_aside_that_could_not_be_removed_is_kept_on_record_until_it_is(twins, monkeypatch):
    root = twins.native
    monkeypatch.setattr(outbox.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(outbox, "_RETRY_SECONDS", 0.0)
    real, hits = _failing_aside_unlink(monkeypatch, times=10 ** 6)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store rev")
    assert hits
    assert any(".aside." in name for name in _strays(root))
    assert _aside_keys(root) == [f"projection.aside.{LIVE}"], "the orphan must stay findable"
    monkeypatch.setattr(pathlib.Path, "unlink", real)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-002", field="title", value="later")
    assert _strays(root) == [] and _aside_keys(root) == []
    assert b"store rev" in (_dir(root) / LIVE).read_bytes() and _flags(root) == ()


def test_two_generations_never_share_a_temp_or_aside_name(twins):
    clock = {"at": 1_000_000.0}
    with native_connection(twins.native) as connection:
        commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": "Shared job"}})
        first = outbox.Exporter(connection, _dir(twins.native), owner="A", session="A", clock=lambda: clock["at"])
        (job,) = first.claim()
        clock["at"] += outbox.LEASE_SECONDS + 1
        second = outbox.Exporter(connection, _dir(twins.native), owner="B", session="B", clock=lambda: clock["at"])
        (again,) = second.claim()
        assert again.key == job.key
        assert first._tag(job) != second._tag(again)
        assert first._aside_name(LIVE) != second._aside_name(LIVE)
        second.release()


# ── R8: the wait judges the derived file's own watermark ────────────────────


def test_a_later_commit_to_the_same_input_row_cannot_hide_the_callers_change(twins, monkeypatch):
    root = twins.native
    with twins.at(root):
        bs.backlog_handover_create(tldr="h", thread="probe-thread")
    fake = {"at": time.time()}
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: fake["at"])
    monkeypatch.setitem(projection.HOOKS, "sleep", lambda s: fake.__setitem__("at", fake["at"] + s))
    state = {}

    def peer(status):
        with closing(sqlite3.connect(native_database(root), isolation_level=None, timeout=30)) as connection:
            return commit_only(connection, "thread.update", {"name": "probe-thread", "status": status, "reason": "r"})

    def checkpoint(stage, rel):
        if stage == "before_write" and "caller" not in state:
            state["caller"] = peer("parked")
            state["later"] = peer("closed")
        if stage == "acked" and "caller" in state and "notices" not in state:
            with native_connection(root) as other:
                state["notices"] = projection.drain(other, _dir(root), session="peer",
                                                    through=state["caller"]["commit_seq"])

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="x")
    assert "export pending: backlog.yaml — retried on next call" in state["notices"], state


def test_an_up_to_date_derived_file_does_not_hold_a_caller(twins, monkeypatch):
    """A caller whose commit touched no derived input must not wait on backlog.yaml."""
    root = twins.native
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="settled")
    with native_connection(root) as connection:
        seq = commit_only(connection, "task.patch", {"id": "test-epic-002", "set": {"title": "Unrelated"}})["commit_seq"]
        assert projection.drain(connection, _dir(root), session="t") == []
        assert projection._behind(connection, seq) == []


# ── Regressions: kill at every step, moves, repeated kills, exact bytes ─────

CHILD = textwrap.dedent("""
    import json, os, sys, time
    from pathlib import Path
    from taskmaster.native_routing import projection
    from tests import native_projection_oracle as runtime
    root, stage, target, operation, arguments, newcomer, offset = sys.argv[1:8]
    backlog_dir = Path(root) / ".taskmaster"

    def checkpoint(at, rel):
        if at == stage and target in ("*", rel):
            if newcomer != "-":
                (backlog_dir / rel).write_bytes(newcomer.encode())
            os._exit(17)

    projection.HOOKS["checkpoint"] = checkpoint
    projection.HOOKS["clock"] = lambda: time.time() + float(offset)
    with runtime.open_call(backlog_dir / "local" / "store.db", backlog_dir, "child") as call:
        call.execute(operation, json.loads(arguments))
    os._exit(0)
""")

PATCH = ("task.update", {"id": "test-epic-001", "field": "notes", "value": "crash revision"})
ARCHIVE = ("task.archive", {"id": "test-epic-001", "reason": "wont-fix"})


def _kill(root, stage, target, operation, arguments, newcomer="-", offset=0.0):
    done = subprocess.run([sys.executable, "-c", CHILD, str(root), stage, target, operation,
                           json.dumps(arguments), newcomer, str(offset)], capture_output=True, text=True, timeout=120,
                          env=child_environment(),
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert done.returncode == 17, done.stderr[-2000:]


def _recover(root, monkeypatch):
    monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: time.time() + 1000 * outbox.LEASE_SECONDS)
    with native_connection(root) as connection:
        return projection.drain(connection, _dir(root), session="recovery")


@pytest.mark.parametrize("stage", ["before_write", "temp_written", "aside", "before_replace", "replaced", "ack_manifest"])
@pytest.mark.parametrize("newcomer", ["-", "USER NEWCOMER\n"])
def test_a_kill_at_every_step_recovers_and_never_loses_a_newcomer(twins, monkeypatch, stage, newcomer):
    root = twins.native
    path = _dir(root) / LIVE
    _kill(root, stage, LIVE, *PATCH, newcomer=newcomer)
    _recover(root, monkeypatch)
    if newcomer == "-":
        assert b"crash revision" in path.read_bytes() and _flags(root) == ()
    else:
        with native_connection(root) as connection:
            kept = [bytes(r[0]) for r in connection.execute("SELECT file_content FROM projection_conflict")]
        assert path.read_bytes() == newcomer.encode() or newcomer.encode() in kept
    assert _strays(root) == []


@pytest.mark.parametrize("stage", ["aside", "removed", "acked"])
def test_a_kill_during_a_move_recovers_to_one_copy(twins, monkeypatch, stage):
    root = twins.native
    _kill(root, stage, "*", *ARCHIVE)
    _recover(root, monkeypatch)
    assert (_dir(root) / ARCHIVED).exists() and not (_dir(root) / LIVE).exists()
    assert _flags(root) == () and _strays(root) == []


def test_repeated_kills_with_the_file_aside_recover_to_the_last_revision(twins, monkeypatch):
    root = twins.native
    for n in range(4):
        _kill(root, "aside", LIVE, "task.update", {"id": "test-epic-001", "field": "notes", "value": f"rev {n}"},
              offset=(outbox.LEASE_SECONDS + 1) * (n + 1))
    _recover(root, monkeypatch)
    assert b"rev 3" in (_dir(root) / LIVE).read_bytes()
    assert _flags(root) == () and _strays(root) == []


def test_resolve_keeps_bytes_no_text_encoding_can_carry(twins):
    root = twins.native
    path = _dir(root) / LIVE
    edited = path.read_bytes() + b"\nLatin-1 caf\xe9 \r lone CR \x00 nul\n"
    path.write_bytes(edited)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
        assert path.read_bytes() == edited
        assert bs.backlog_resolve_conflict(file=LIVE, take="store").startswith("Resolved")
    with native_connection(root) as connection:
        before = json.loads(connection.execute(
            "SELECT before FROM domain_events WHERE op='resolve' ORDER BY seq DESC LIMIT 1").fetchone()[0])
    assert base64.b64decode(before["file_base64"]) == edited
    assert base64.b64decode(before["flagged_base64"]) == edited
