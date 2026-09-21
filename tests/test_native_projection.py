"""User intent: the N11 outbox protocol on its own — a store-wide exporter lease with
a generation fence, latest-per-file claims, expired-claim recovery, one ack per job and
an `exported_through` watermark — proven with an injected renderer on a fake clock, so
N12's service can reuse it unchanged.
"""
from __future__ import annotations

import hashlib

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection as outbox
from native_twins import commit_only, make_twins, native_connection


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


class Clock:
    def __init__(self, at=1_000_000.0):
        self.at = at

    def __call__(self):
        return self.at


def render(job):
    """The injected renderer: the entity's identity and revision, nothing else."""
    if job.effect == "delete":
        return None
    return f"{job.id} r{job.entity['revision']} {job.entity['fields'].get('title')}\n".encode()


def _exporter(connection, root, owner, clock, **kwargs):
    return outbox.Exporter(connection, root / ".taskmaster", owner=owner, clock=clock, session=owner, **kwargs)


def _patch(connection, ident, **fields):
    return commit_only(connection, "task.patch", {"id": ident, "set": fields})


def _states(connection, rel):
    return [state for (state,) in connection.execute(
        "SELECT state FROM projection_jobs WHERE file=? ORDER BY commit_seq,job_key", (rel,))]


def _publish_all(exporter, jobs):
    rendered = [(job, render(job)) for job in jobs]
    exporter.intend(rendered)
    return [exporter.publish(job, content) for job, content in rendered]


def test_claim_takes_the_store_wide_lease_and_a_second_exporter_waits(twins):
    clock = Clock()
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Claimed")
        first = _exporter(connection, twins.native, "A", clock)
        jobs = first.claim()
        assert [job.file for job in jobs] == ["tasks/test-epic-001.md"]
        assert _states(connection, "tasks/test-epic-001.md")[-1] == "claimed"
        second = _exporter(connection, twins.native, "B", clock)
        assert second.claim() is None, "a live lease must keep a second exporter out"
        clock.at += outbox.LEASE_SECONDS + 1
        recovered = second.claim()
        assert [(job.key, job.file) for job in recovered] == [(jobs[0].key, jobs[0].file)]
        lease = outbox.lease(connection)
        assert (lease["owner"], lease["generation"]) == ("B", 2)


def test_a_fenced_exporter_records_nothing_after_its_lease_is_taken(twins):
    clock = Clock()
    root = twins.native
    with native_connection(root) as connection:
        _patch(connection, "test-epic-001", title="Fenced")
        before = connection.execute(
            "SELECT content_hash FROM projection WHERE file='tasks/test-epic-001.md'").fetchone()[0]
        stale = _exporter(connection, root, "A", clock)
        (job,) = stale.claim()
        clock.at += outbox.LEASE_SECONDS + 1
        successor = _exporter(connection, root, "B", clock)
        assert successor.claim()
        rendered = [(job, render(job))]
        with pytest.raises(outbox.LeaseLost):
            stale.intend(rendered)
        assert stale.publish(job, rendered[0][1]) == "lost"
        after = connection.execute(
            "SELECT content_hash FROM projection WHERE file='tasks/test-epic-001.md'").fetchone()[0]
        assert after == before
        assert _states(connection, "tasks/test-epic-001.md")[-1] == "claimed"


def test_only_the_latest_job_per_file_is_claimed(twins):
    clock = Clock()
    with native_connection(twins.native) as connection:
        for n in range(3):
            _patch(connection, "test-epic-001", title=f"Edit {n}")
        _patch(connection, "test-epic-002", title="Other")
        jobs = _exporter(connection, twins.native, "A", clock).claim()
        assert sorted(job.file for job in jobs) == ["tasks/test-epic-001.md", "tasks/test-epic-002.md"]
        (latest,) = [job for job in jobs if job.id == "test-epic-001"]
        assert latest.entity["fields"]["title"] == "Edit 2"
        assert _states(connection, "tasks/test-epic-001.md") == ["superseded", "superseded", "claimed"]


def test_each_job_is_acked_on_its_own(twins):
    clock = Clock()
    root = twins.native
    with native_connection(root) as connection:
        commit_only(connection, "batch", {"commands": [
            {"operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": "One"}}},
            {"operation": "task.patch", "arguments": {"id": "test-epic-002", "set": {"title": "Two"}}}]})
        exporter = _exporter(connection, root, "A", clock)
        first, second = exporter.claim()
        rendered = [(first, render(first)), (second, render(second))]
        exporter.intend(rendered)
        assert exporter.publish(*rendered[0]) == "exported"
        # The second file never gets published: its exporter dies here.
        on_disk = (root / ".taskmaster" / first.file).read_bytes()
        recorded = connection.execute("SELECT content_hash FROM projection WHERE file=?", (first.file,)).fetchone()[0]
        assert recorded == hashlib.sha1(on_disk).hexdigest()
        assert _states(connection, first.file)[-1] == "exported"
        assert _states(connection, second.file)[-1] == "claimed"


def test_exported_through_never_passes_a_pending_or_claimed_job(twins):
    clock = Clock()
    root = twins.native
    with native_connection(root) as connection:
        one = _patch(connection, "test-epic-001", title="One")["commit_seq"]
        two = _patch(connection, "test-epic-002", title="Two")["commit_seq"]
        assert outbox.exported_through(connection) < one
        exporter = _exporter(connection, root, "A", clock)
        jobs = exporter.claim()
        rendered = [(job, render(job)) for job in jobs]
        exporter.intend(rendered)
        by_id = {job.id: (job, content) for job, content in rendered}
        exporter.publish(*by_id["test-epic-002"])
        assert outbox.exported_through(connection) < one, "the claimed job at `one` still blocks"
        exporter.publish(*by_id["test-epic-001"])
        assert outbox.exported_through(connection) >= two
        exporter.finish()
        assert outbox.lease(connection)["until"] <= clock.at, "finish releases the lease"


def test_an_expired_claim_is_recovered_with_its_identity_and_order(twins):
    clock = Clock()
    root = twins.native
    with native_connection(root) as connection:
        seq = _patch(connection, "test-epic-001", title="Crashed")["commit_seq"]
        (job,) = _exporter(connection, root, "A", clock).claim()
        clock.at += outbox.LEASE_SECONDS + 1
        successor = _exporter(connection, root, "B", clock)
        (again,) = successor.claim()
        assert (again.key, again.commit_seq) == (job.key, seq)
        assert _publish_all(successor, [again]) == ["exported"]
        assert (root / ".taskmaster" / job.file).read_bytes() == render(job)
