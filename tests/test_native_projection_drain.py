"""User intent: the synchronous compatibility drain on the N11 outbox (S6) — every
command's files still on disk, byte-identical, before the call returns, but with no
database lock held across file I/O, a bounded wait behind another exporter, and the
PROGRESS seam the changelog track fills in.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection as outbox
from taskmaster.native_routing import progress, projection
from native_twins import commit_only, make_twins, native_connection, native_database


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed, engine_oracle=True)


class FakeTime:
    def __init__(self):
        self.at = 1_000_000.0
        self.slept = 0.0

    def clock(self):
        return self.at

    def sleep(self, seconds):
        self.slept += seconds
        self.at += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setitem(projection.HOOKS, "clock", fake.clock)
    monkeypatch.setitem(projection.HOOKS, "sleep", fake.sleep)
    return fake


def _foreign_lease(root):
    with native_connection(root) as connection:
        connection.execute("INSERT INTO sync_state VALUES(?,?)", (
            outbox.EXPORTER_KEY, '{"owner": "elsewhere", "generation": 7, "until": 9e12}'))


def test_a_peer_command_commits_while_the_drain_is_mid_fsync(twins, monkeypatch):
    committed = []

    def checkpoint(stage, rel):
        if stage == "temp_written" and rel == "tasks/test-epic-001.md" and not committed:
            with closing(sqlite3.connect(native_database(twins.native), isolation_level=None, timeout=0)) as peer:
                peer.execute("PRAGMA busy_timeout=0")
                committed.append(commit_only(peer, "task.patch", {"id": "test-epic-002", "set": {"title": "Peer"}}))

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="mid fsync")
    assert committed and committed[0]["affected"], answer
    # The peer's job is picked up by the next drain, like any command's.
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="next call")
    assert b"Peer" in (twins.native / ".taskmaster" / "tasks" / "test-epic-002.md").read_bytes()


def test_the_wait_behind_another_exporter_times_out_with_the_pending_notice(twins, fake_time):
    _foreign_lease(twins.native)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="waits")
    assert "export pending: tasks/test-epic-001.md — retried on next call" in answer
    assert projection.WAIT_SECONDS - 0.1 <= fake_time.slept < projection.WAIT_SECONDS + 1
    assert b"waits" not in (twins.native / ".taskmaster" / "tasks" / "test-epic-001.md").read_bytes()


def test_the_wait_ends_when_another_exporter_exports_through_the_callers_commit(twins, fake_time, monkeypatch):
    done = []

    def sleep(seconds):
        fake_time.sleep(seconds)
        if done:
            return
        # The other exporter's lease is live throughout: it claims under a fresh
        # generation of its own name, exports everything and keeps the lease, so
        # only the watermark can end the caller's wait.
        with native_connection(twins.native) as connection:
            exporter = outbox.Exporter(connection, twins.native / ".taskmaster", owner="elsewhere",
                                       session="elsewhere", clock=fake_time.clock)
            jobs = exporter.claim()
            render = projection._Render(connection, twins.native / ".taskmaster")
            rendered = [(job, render.job(job)) for job in jobs]
            exporter.intend(rendered)
            for job, content in rendered:
                exporter.publish(job, content)
        done.append(True)

    _foreign_lease(twins.native)
    monkeypatch.setitem(projection.HOOKS, "sleep", sleep)
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="exported elsewhere")
    assert "export pending" not in answer, answer
    assert b"exported elsewhere" in (twins.native / ".taskmaster" / "tasks" / "test-epic-001.md").read_bytes()
    assert fake_time.slept < projection.WAIT_SECONDS


def test_the_drain_calls_the_progress_seam_once(twins, monkeypatch):
    calls = []
    monkeypatch.setattr(progress, "export", lambda connection, backlog_dir, session, *, through=None, wait=True:
                        calls.append((session, through, wait)) or [])
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="seam")
    assert len(calls) == 1
    # The caller's commit reaches the seam, so it waits only for its own paragraphs.
    assert calls[0][1] is not None and calls[0][2] is True


def test_a_job_that_renders_the_same_bytes_performs_no_replace(twins, monkeypatch):
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="once")
    replaced = []
    real = os.replace

    def counting(source, target):
        replaced.append(str(target))
        return real(source, target)

    monkeypatch.setattr(os, "replace", counting)
    with native_connection(twins.native) as connection:
        # The file's export record, re-queued: its render is byte-identical to the file.
        connection.execute("UPDATE projection_jobs SET state='pending' WHERE file='tasks/test-epic-001.md'")
        assert projection.drain(connection, twins.native / ".taskmaster", session="t") == []
        assert connection.execute("SELECT state FROM projection_jobs WHERE file='tasks/test-epic-001.md'"
                                  ).fetchall() == [("exported",)]
    assert replaced == []


def test_the_drain_holds_no_database_lock_across_file_io(twins, monkeypatch):
    seen = []

    def checkpoint(stage, rel):
        if stage in ("before_write", "temp_written", "replaced"):
            with closing(sqlite3.connect(native_database(twins.native), isolation_level=None, timeout=0)) as peer:
                peer.execute("BEGIN IMMEDIATE")
                peer.rollback()
            seen.append(stage)

    monkeypatch.setitem(projection.HOOKS, "checkpoint", checkpoint)
    with twins.at(twins.native):
        bs.backlog_update_task(task_id="test-epic-001", field="notes", value="no lock")
    assert {"before_write", "temp_written", "replaced"} <= set(seen)
