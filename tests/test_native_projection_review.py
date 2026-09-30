"""User intent: the seven defects an adversarial review of the N11 outbox confirmed
(probes P1-P8), each pinned as a real test so none can come back: an edit in the
rename window is never overwritten, a move never loses its only copy, a line-ending
flip is not a conflict, a committed command never raises, a caller never returns
while a file its commit touched is stale without saying so, a resolution keeps the
exact bytes it discards, and an inherited quarantine can be cleared natively.
"""
from __future__ import annotations

import base64
from contextlib import closing
import errno
import json
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection as outbox
from taskmaster.native_routing import projection
from tests import native_projection_oracle as runtime
from native_twins import commit_only, make_twins, native_connection, native_database
from test_native_projection_faults import assert_lossless

LIVE, ARCHIVED = "tasks/test-epic-001.md", "tasks/archive/test-epic-001.md"
USER = b"---\ntitle: the user's own bytes\n---\nWritten in the rename window.\n"


class Crash(BaseException):
    pass


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev", notes="seeded")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True)


def _dir(root):
    return root / ".taskmaster"


def _flag(root, rel):
    with native_connection(root) as connection:
        row = connection.execute("SELECT file_content FROM projection_conflict WHERE file=?", (rel,)).fetchone()
    return None if row is None else bytes(row[0])


def _leftovers(root):
    return sorted(p.name for p in _dir(root).rglob("*") if ".aside." in p.name or ".tmp.j" in p.name)


# ── P1: the rename window ───────────────────────────────────────────────────

WRITE_STAGES = ["before_write", "temp_written", "aside", "before_replace"]


@pytest.mark.parametrize("stage", WRITE_STAGES)
def test_an_edit_at_any_point_before_the_install_is_kept_and_flagged(twins, monkeypatch, stage):
    root = twins.native
    path = _dir(root) / LIVE
    fired = []

    def checkpoint(at, rel):
        if at == stage and rel == LIVE and not fired:
            fired.append(at)
            path.write_bytes(USER)

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store revision")
    assert fired, f"checkpoint {stage} never ran"
    assert path.read_bytes() == USER, answer
    assert _flag(root, LIVE) == USER
    assert f"export pending: {LIVE} is flagged" in answer
    assert _leftovers(root) == []


@pytest.mark.parametrize("stage", ["before_write", "aside"])
def test_a_removal_never_deletes_an_edit_made_before_it(twins, monkeypatch, stage):
    root = twins.native
    path = _dir(root) / LIVE
    fired = []

    def checkpoint(at, rel):
        if at == stage and rel == LIVE and not fired:
            fired.append(at)
            path.write_bytes(USER)

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        answer = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert fired, f"checkpoint {stage} never ran"
    assert path.read_bytes() == USER, answer
    assert _flag(root, LIVE) == USER
    assert (_dir(root) / ARCHIVED).exists()
    assert _leftovers(root) == []


CHILD = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from taskmaster.native_routing import projection
    from tests import native_projection_oracle as runtime
    root, stage, rel, edit = sys.argv[1:5]
    path = Path(root) / ".taskmaster" / rel

    def checkpoint(at, file):
        if file != rel:
            return
        if edit == "yes" and at == "temp_written":
            path.write_bytes(b"edited just before the crash\\n")
        if at == stage:
            os._exit(17)

    projection.HOOKS["checkpoint"] = checkpoint
    backlog_dir = Path(root) / ".taskmaster"
    with runtime.open_call(backlog_dir / "local" / "store.db", backlog_dir, "child") as call:
        call.execute("task.update", {"id": "test-epic-001", "field": "notes", "value": "crash revision"})
    os._exit(0)
""")


@pytest.mark.parametrize("variant", ["exception", "exit"])
def test_a_crash_while_the_file_is_aside_is_restored_by_recovery(twins, monkeypatch, variant):
    root = twins.native
    path = _dir(root) / LIVE
    if variant == "exit":
        done = subprocess.run([sys.executable, "-c", CHILD, str(root), "aside", LIVE, "no"],
                              capture_output=True, text=True, timeout=120,
                              env=runtime.child_environment(),
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert done.returncode == 17, done.stderr[-2000:]
        monkeypatch.setitem(projection.HOOKS, "clock", lambda: time.time() + outbox.LEASE_SECONDS + 1)
    else:
        def checkpoint(at, rel):
            if at == "aside" and rel == LIVE:
                raise Crash("crash with the file aside")

        monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
        with runtime.open_call(native_database(root), _dir(root), "parent") as call, pytest.raises(Crash):
            call.execute("task.update", {"id": "test-epic-001", "field": "notes", "value": "crash revision"})
        monkeypatch.setitem(projection.HOOKS, "checkpoint", None)
    assert not path.exists(), "the crash left the file aside"
    with native_connection(root) as connection:
        assert projection.drain(connection, _dir(root), session="recovery") == []
    assert b"crash revision" in path.read_bytes()
    assert_lossless(root)


def test_an_edit_caught_aside_by_a_crash_is_restored_and_flagged(twins, monkeypatch):
    root = twins.native
    path = _dir(root) / LIVE
    done = subprocess.run([sys.executable, "-c", CHILD, str(root), "aside", LIVE, "yes"],
                          capture_output=True, text=True, timeout=120,
                          env=runtime.child_environment(),
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert done.returncode == 17, done.stderr[-2000:]
    assert not path.exists()
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: time.time() + outbox.LEASE_SECONDS + 1)
    with native_connection(root) as connection:
        notices = projection.drain(connection, _dir(root), session="recovery")
    assert path.read_bytes() == b"edited just before the crash\n"
    assert _flag(root, LIVE) == b"edited just before the crash\n"
    assert f"export pending: {LIVE} is flagged" in notices
    assert _leftovers(root) == []


# ── P6, P7: a move keeps its old path until the new one is exported ─────────


def test_a_failed_new_path_write_keeps_the_old_path(twins, monkeypatch):
    root = twins.native
    real = outbox._install

    def refuse(source, target):
        if target.as_posix().endswith(ARCHIVED):
            raise OSError(errno.ENOSPC, "no space left (simulated)")
        return real(source, target)

    monkeypatch.setattr(outbox, "_install", refuse)
    with twins.at(root):
        answer = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert (_dir(root) / LIVE).exists() and not (_dir(root) / ARCHIVED).exists(), answer
    assert f"export pending: {ARCHIVED} — retried on next call" in answer
    with native_connection(root) as connection:
        assert connection.execute("SELECT state FROM projection_jobs WHERE file=? AND effect='delete'",
                                  (LIVE,)).fetchall() == [("pending",)]
    monkeypatch.setattr(outbox, "_install", real)
    with native_connection(root) as connection:
        assert projection.drain(connection, _dir(root), session="next") == []
    assert not (_dir(root) / LIVE).exists() and (_dir(root) / ARCHIVED).exists()
    assert_lossless(root)


def test_a_flagged_new_path_keeps_the_old_path(twins):
    root = twins.native
    stranger = b"a user's own file that happens to sit here\n"
    (_dir(root) / ARCHIVED).parent.mkdir(parents=True, exist_ok=True)
    (_dir(root) / ARCHIVED).write_bytes(stranger)
    with twins.at(root):
        answer = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert f"export pending: {ARCHIVED} is flagged" in answer
    assert (_dir(root) / ARCHIVED).read_bytes() == stranger
    assert (_dir(root) / LIVE).exists(), "the only copy of the task must stay"


# ── P8: a line-ending flip is not a conflict ────────────────────────────────


def test_a_line_ending_flip_updates_cleanly_as_legacy_does(twins):
    for root in (twins.legacy, twins.native):
        path = _dir(root) / LIVE
        data = path.read_bytes()
        path.write_bytes(data.replace(b"\r\n", b"\n") if b"\r\n" in data else data.replace(b"\n", b"\r\n"))
    legacy, native = twins.same("backlog_update_task", task_id="test-epic-001", field="notes", value="after flip")
    assert "flagged" not in native, native
    assert _flag(twins.native, LIVE) is None
    assert (_dir(twins.native) / LIVE).read_bytes() == (_dir(twins.legacy) / LIVE).read_bytes()


# ── P3: a committed command never raises ────────────────────────────────────


def test_a_lease_lost_while_rendering_reports_pending_instead_of_raising(twins, monkeypatch):
    root = twins.native
    real = projection._Render.job
    fired = []

    def slow(self, job):
        if not fired:
            fired.append(True)
            later = time.time() + outbox.LEASE_SECONDS + 5
            monkeypatch.setitem(projection.HOOKS, "clock", lambda: later)
            with native_connection(root) as other:
                projection.drain(other, _dir(root), session="successor")
        return real(self, job)

    monkeypatch.setattr(projection._Render, "job", slow)
    with twins.at(root):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="committed")
    assert isinstance(answer, str) and answer.startswith("Updated"), answer
    assert "export pending" in answer
    assert b"committed" in (_dir(root) / LIVE).read_bytes()


# ── P2: the wait judges the caller's own files ──────────────────────────────


def test_a_waiting_caller_reports_a_stale_backlog_yaml(twins, monkeypatch):
    root = twins.native
    with twins.at(root):
        bs.backlog_handover_create(tldr="h", thread="probe-thread")
    fake = {"at": time.time()}
    monkeypatch.setitem(projection.HOOKS, "clock", lambda: fake["at"])
    monkeypatch.setitem(projection.HOOKS, "sleep", lambda s: fake.__setitem__("at", fake["at"] + s))
    state = {}

    def checkpoint(stage, rel):
        if stage == "before_write" and "commit" not in state:
            with closing(sqlite3.connect(native_database(root), isolation_level=None, timeout=30)) as peer:
                state["commit"] = commit_only(peer, "thread.update",
                                              {"name": "probe-thread", "status": "parked", "reason": "probe"})
        if stage == "acked" and "commit" in state and "notices" not in state:
            with native_connection(root) as other:
                state["notices"] = projection.drain(other, _dir(root), session="peer",
                                                    through=state["commit"]["commit_seq"])

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="x")
    assert "export pending: backlog.yaml — retried on next call" in state["notices"], state


# ── P4: a resolution keeps the exact bytes it discards ──────────────────────


def _resolution(root):
    with native_connection(root) as connection:
        before = connection.execute("SELECT before FROM domain_events WHERE op='resolve' "
                                    "ORDER BY seq DESC LIMIT 1").fetchone()[0]
    return json.loads(before)


def _flag_by_edit(twins, edited):
    (_dir(twins.native) / LIVE).write_bytes(edited)
    with twins.at(twins.native):
        assert "is flagged" in bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")


def test_take_store_records_the_exact_bytes_it_replaces(twins):
    edited = (_dir(twins.native) / LIVE).read_bytes() + b"\nLatin-1 caf\xe9 edit\n"
    _flag_by_edit(twins, edited)
    with twins.at(twins.native):
        assert bs.backlog_resolve_conflict(file=LIVE, take="store").startswith("Resolved")
    before = _resolution(twins.native)
    assert base64.b64decode(before["file_base64"]) == edited
    assert base64.b64decode(before["flagged_base64"]) == edited


def test_take_store_on_a_missing_flagged_file_keeps_what_was_flagged(twins):
    edited = (_dir(twins.native) / LIVE).read_bytes() + b"\nFlagged, then deleted.\n"
    _flag_by_edit(twins, edited)
    (_dir(twins.native) / LIVE).unlink()
    with twins.at(twins.native):
        assert bs.backlog_resolve_conflict(file=LIVE, take="store").startswith("Resolved")
    before = _resolution(twins.native)
    assert before["file"] == edited.decode("utf-8")
    assert base64.b64decode(before["file_base64"]) == edited
    assert base64.b64decode(before["flagged_base64"]) == edited
    assert b"store side" in (_dir(twins.native) / LIVE).read_bytes()


# ── P5: an inherited quarantine can be cleared natively ─────────────────────


def test_an_inherited_quarantine_is_listed_and_cleared_by_take_store(twins):
    root = twins.native
    with native_connection(root) as connection:
        connection.execute("UPDATE projection SET quarantined=1 WHERE file=?", (LIVE,))
    (_dir(root) / LIVE).unlink()
    with twins.at(root):
        held = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="after quarantine")
        assert f"export pending: {LIVE} is quarantined" in held
        assert LIVE in bs.backlog_resolve_conflict()
        resolved = bs.backlog_resolve_conflict(file=LIVE, take="store")
        again = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="again")
    assert resolved.startswith(f"Resolved {LIVE}: kept the store version."), resolved
    assert "export pending" not in again, again
    assert b"again" in (_dir(root) / LIVE).read_bytes()
