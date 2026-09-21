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


# ── S3: expected-base verification (§2.4) ──────────────────────────────────


def _run(connection, root, clock, owner="A"):
    exporter = _exporter(connection, root, owner, clock)
    jobs = exporter.claim()
    outcomes = {job.file: outcome for job, outcome in zip(jobs, _publish_all(exporter, jobs))}
    exporter.finish()
    return outcomes, exporter


def _flag_row(connection, rel):
    row = connection.execute("SELECT kind,id,file_hash,file_content FROM projection_conflict WHERE file=?",
                             (rel,)).fetchone()
    return None if row is None else (row[0], row[1], row[2], bytes(row[3]))


def test_verify_publishes_when_the_disk_matches_the_record(twins):
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Matches")
        outcomes, _ = _run(connection, twins.native, Clock())
        assert outcomes == {"tasks/test-epic-001.md": "exported"}
        assert _flag_row(connection, "tasks/test-epic-001.md") is None


def test_verify_repairs_a_missing_file_that_has_a_record(twins):
    path = twins.native / ".taskmaster" / "tasks" / "test-epic-001.md"
    path.unlink()
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Repaired")
        outcomes, _ = _run(connection, twins.native, Clock())
    assert outcomes == {"tasks/test-epic-001.md": "exported"}
    assert b"Repaired" in path.read_bytes()


def test_verify_publishes_a_new_file_with_no_record(twins):
    with native_connection(twins.native) as connection:
        commit_only(connection, "task.create", {"title": "Brand new", "epic": "test-epic", "phase": "dev"})
        outcomes, _ = _run(connection, twins.native, Clock())
    assert outcomes == {"tasks/test-epic-003.md": "exported"}


def test_verify_flags_a_file_that_appeared_where_nothing_was_written(twins):
    path = twins.native / ".taskmaster" / "tasks" / "test-epic-003.md"
    stranger = b"---\ntitle: someone else's file\n---\n"
    path.write_bytes(stranger)
    with native_connection(twins.native) as connection:
        commit_only(connection, "task.create", {"title": "Brand new", "epic": "test-epic", "phase": "dev"})
        outcomes, exporter = _run(connection, twins.native, Clock())
        assert outcomes == {"tasks/test-epic-003.md": "flagged"}
        assert _flag_row(connection, "tasks/test-epic-003.md")[3] == stranger
        assert _states(connection, "tasks/test-epic-003.md") == ["conflict"]
    assert path.read_bytes() == stranger
    assert "export pending: tasks/test-epic-003.md is flagged" in exporter.warnings


def test_verify_recognizes_its_own_bytes_after_a_crash_between_replace_and_ack(twins):
    rel = "tasks/test-epic-001.md"
    clock = Clock()
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Own bytes")

        def crash(stage, file):
            if stage == "replaced":
                raise KeyboardInterrupt("exporter died after the replace")

        dying = _exporter(connection, twins.native, "A", clock, checkpoint=crash)
        (job,) = dying.claim()
        dying.intend([(job, render(job))])
        with pytest.raises(KeyboardInterrupt):
            dying.publish(job, render(job))
        clock.at += outbox.LEASE_SECONDS + 1
        outcomes, _ = _run(connection, twins.native, clock, owner="B")
        assert outcomes == {rel: "exported"}
        assert _flag_row(connection, rel) is None


def test_verify_flags_an_external_edit_and_keeps_both_byte_for_byte(twins):
    rel = "tasks/test-epic-001.md"
    path = twins.native / ".taskmaster" / rel
    edited = path.read_bytes().replace(b"\n", b"\r\n") + b"\r\nHand edit \xe2\x80\x94 kept.\r\n"
    path.write_bytes(edited)
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Store side")
        outcomes, _ = _run(connection, twins.native, Clock())
        assert outcomes == {rel: "flagged"}
        kind, ident, digest, content = _flag_row(connection, rel)
        assert (kind, ident, content, digest) == ("task", "test-epic-001", edited, hashlib.sha1(edited).hexdigest())
        assert connection.execute("SELECT dirty FROM projection WHERE file=?", (rel,)).fetchone()[0] == 1
    assert path.read_bytes() == edited


def test_verify_never_consults_the_jobs_expected_hash(twins):
    rel = "tasks/test-epic-001.md"
    path = twins.native / ".taskmaster" / rel
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Garbage expected hash")
        connection.execute("UPDATE projection_jobs SET expected_hash='not-a-hash' WHERE state='pending'")
        outcomes, _ = _run(connection, twins.native, Clock())
        assert outcomes == {rel: "exported"}
        edited = path.read_bytes() + b"edit\n"
        path.write_bytes(edited)
        _patch(connection, "test-epic-001", title="Expected hash names the edit")
        connection.execute("UPDATE projection_jobs SET expected_hash=? WHERE state='pending'",
                           (hashlib.sha1(edited).hexdigest(),))
        outcomes, _ = _run(connection, twins.native, Clock())
        assert outcomes == {rel: "flagged"}


def test_a_file_that_already_holds_the_render_is_acked_without_a_rewrite(twins, monkeypatch):
    import os
    rel = "tasks/test-epic-001.md"
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Agreed")
        exporter = _exporter(connection, twins.native, "A", Clock())
        (job,) = exporter.claim()
        (twins.native / ".taskmaster" / rel).write_bytes(render(job))
        replaced = []
        real = os.replace
        monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append(b), real(a, b)))
        exporter.intend([(job, render(job))])
        assert exporter.publish(job, render(job)) == "exported"
        assert replaced == []
        assert _flag_row(connection, rel) is None


@pytest.mark.parametrize("rel,kind", [("backlog.yaml", "backlog"), ("ideas/IDEAS.md", "ideas-index")])
def test_verify_covers_the_derived_files(twins, rel, kind):
    path = twins.native / ".taskmaster" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with native_connection(twins.native) as connection:
        exporter = _exporter(connection, twins.native, "A", Clock())
        exporter.claim()
        exporter.intend([(rel, b"first render\n")])
        assert exporter.publish_derived(rel, kind, b"first render\n", 7) == "exported"
        path.write_bytes(b"hand edit of a derived file\n")
        exporter.intend([(rel, b"second render\n")])
        assert exporter.publish_derived(rel, kind, b"second render\n", 8) == "flagged"
        assert _flag_row(connection, rel) == (kind, None, hashlib.sha1(b"hand edit of a derived file\n").hexdigest(),
                                              b"hand edit of a derived file\n")
    assert path.read_bytes() == b"hand edit of a derived file\n"


# ── S4: moves, tombstones and retention (§2.6) ─────────────────────────────

LIVE, ARCHIVED = "tasks/test-epic-001.md", "tasks/archive/test-epic-001.md"


def _archive(connection, archived=True):
    """Move the task between its live and archive paths in one committed transaction.

    No public operation unarchives a task, so this drives the command transaction
    owner directly, exactly as a command's `replace` would.
    """
    from taskmaster.native.commands import Transaction
    from taskmaster.native.db import assert_native
    from taskmaster.native.migrate import _put_manifest
    connection.execute("BEGIN IMMEDIATE")
    try:
        identity = assert_native(connection)
        transaction = Transaction(connection, {"operation": "test.move", "caller_scope": "tests"}, identity)
        entity = transaction.snapshot.get("task", "test-epic-001", include_body=True)
        after = dict(entity["fields"])
        if archived:
            after["archived"] = True
        else:
            after.pop("archived", None)
        transaction.replace("task", "test-epic-001", after, entity["body"], before_entity=entity)
        transaction.snapshot.active = False
        _put_manifest(connection, event_high_water=transaction.seq)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _exists(root, rel):
    return (root / ".taskmaster" / rel).exists()


def test_a_move_writes_the_new_path_before_removing_the_old(twins):
    order = []
    with native_connection(twins.native) as connection:
        _archive(connection)
        exporter = _exporter(connection, twins.native, "A", Clock(),
                             checkpoint=lambda stage, rel: order.append((stage, rel)) if rel else None)
        jobs = exporter.claim()
        assert [(job.file, job.effect) for job in jobs] == [(ARCHIVED, "write"), (LIVE, "delete")]
        assert jobs[0].moved_from == LIVE
        _publish_all(exporter, jobs)
        exporter.finish()
        record = outbox.export_record(connection, LIVE)
    acked = [rel for stage, rel in order if stage == "acked"]
    assert acked == [ARCHIVED, LIVE]
    assert not _exists(twins.native, LIVE) and _exists(twins.native, ARCHIVED)
    assert record["effect"] == "delete" and record["tombstone"]


def test_archive_unarchive_and_rearchive_coalesce_to_the_last_move(twins):
    with native_connection(twins.native) as connection:
        _archive(connection)
        _archive(connection, archived=False)
        _archive(connection)
        outcomes, _ = _run(connection, twins.native, Clock())
    assert outcomes == {ARCHIVED: "exported", LIVE: "exported"}
    assert not _exists(twins.native, LIVE) and _exists(twins.native, ARCHIVED)


def test_a_crash_between_the_new_write_and_the_old_removal_heals_on_the_next_drain(twins):
    clock = Clock()
    with native_connection(twins.native) as connection:
        _archive(connection)

        def crash(stage, rel):
            if stage == "acked" and rel == ARCHIVED:
                raise KeyboardInterrupt("died between the two halves of a move")

        exporter = _exporter(connection, twins.native, "A", clock, checkpoint=crash)
        jobs = exporter.claim()
        with pytest.raises(KeyboardInterrupt):
            _publish_all(exporter, jobs)
        assert _exists(twins.native, LIVE) and _exists(twins.native, ARCHIVED), "two copies, never none"
        clock.at += outbox.LEASE_SECONDS + 1
        outcomes, _ = _run(connection, twins.native, clock, owner="B")
    assert outcomes == {LIVE: "exported"}
    assert not _exists(twins.native, LIVE) and _exists(twins.native, ARCHIVED)


def test_a_tombstone_removes_what_a_recovered_stale_job_recreated(twins):
    clock = Clock()
    with native_connection(twins.native) as connection:
        _patch(connection, "test-epic-001", title="Stale revision")
        stale = _exporter(connection, twins.native, "A", clock)
        (old,) = stale.claim()
        stale.intend([(old, render(old))])
        clock.at += outbox.LEASE_SECONDS + 1
        _archive(connection)
        outcomes, _ = _run(connection, twins.native, clock, owner="B")
        assert outcomes == {ARCHIVED: "exported", LIVE: "exported"}
        # The paused exporter passed its lease check before B took over and now
        # replaces anyway (§2.3(4)): the id is back at its old path, stale.
        stale._owns = lambda: True
        assert stale.publish(old, render(old)) == "lost"
        assert _exists(twins.native, LIVE)
        outcomes, _ = _run(connection, twins.native, clock, owner="C")
        assert outcomes == {LIVE: "exported"}
        assert _flag_row(connection, LIVE) is None
    assert not _exists(twins.native, LIVE)


def test_a_file_that_appears_over_a_tombstone_is_flagged(twins):
    with native_connection(twins.native) as connection:
        _archive(connection)
        _run(connection, twins.native, Clock())
        stranger = b"someone recreated this by hand\n"
        (twins.native / ".taskmaster" / LIVE).write_bytes(stranger)
        _archive(connection, archived=False)
        outcomes, _ = _run(connection, twins.native, Clock(2_000_000.0))
        assert outcomes[LIVE] == "flagged"
        assert _flag_row(connection, LIVE)[3] == stranger


def test_retention_keeps_one_export_record_per_file(twins):
    with native_connection(twins.native) as connection:
        clock = Clock()
        for batch in range(100):
            for n in range(10):
                _patch(connection, "test-epic-001", title=f"Edit {batch}-{n}")
            clock.at += 1
            _run(connection, twins.native, clock)
        rows = connection.execute("SELECT state FROM projection_jobs WHERE file=?", (LIVE,)).fetchall()
    assert rows == [("exported",)]


def test_retention_runs_after_the_ack_and_the_next_ack_catches_up(twins):
    with native_connection(twins.native) as connection:
        for n in range(3):
            _patch(connection, "test-epic-001", title=f"Edit {n}")

        def crash(stage, rel):
            if stage == "retention":
                raise KeyboardInterrupt("died before retention")

        exporter = _exporter(connection, twins.native, "A", Clock(), checkpoint=crash)
        jobs = exporter.claim()
        _publish_all(exporter, jobs)
        with pytest.raises(KeyboardInterrupt):
            exporter.finish()
        assert _states(connection, LIVE) == ["superseded", "superseded", "exported"]
        _patch(connection, "test-epic-001", title="Next")
        _run(connection, twins.native, Clock(2_000_000.0))
        assert _states(connection, LIVE) == ["exported"]


# ── S5: one hold for claim, status and resolve (§2.5) ──────────────────────


def _hand_edit(root, rel=LIVE):
    path = root / ".taskmaster" / rel
    edited = path.read_bytes() + b"\nHand edit.\n"
    path.write_bytes(edited)
    return edited


def test_held_names_flagged_and_quarantined_files(twins):
    with native_connection(twins.native) as connection:
        assert outbox.held(connection, "task", "test-epic-001") == []
        _hand_edit(twins.native)
        _patch(connection, "test-epic-001", title="Flag me")
        _run(connection, twins.native, Clock())
        assert outbox.held(connection, "task", "test-epic-001") == [(LIVE, "flagged")]
        connection.execute("UPDATE projection SET quarantined=1 WHERE file='tasks/test-epic-002.md'")
        assert outbox.held(connection, "task", "test-epic-002") == [("tasks/test-epic-002.md", "quarantined")]
        assert outbox.flagged_files(connection) == (LIVE,)


def test_a_held_entity_is_not_claimed_across_claims_and_recovery(twins):
    clock = Clock()
    with native_connection(twins.native) as connection:
        edited = _hand_edit(twins.native)
        _patch(connection, "test-epic-001", title="Flag me")
        _run(connection, twins.native, clock)
        for n in range(2):
            _patch(connection, "test-epic-001", title=f"While held {n}")
        exporter = _exporter(connection, twins.native, "A", clock)
        assert exporter.claim() == []
        assert f"export pending: {LIVE} is flagged" in exporter.warnings
        assert _states(connection, LIVE)[-3:] == ["conflict", "superseded", "pending"]
        clock.at += outbox.LEASE_SECONDS + 1
        assert _exporter(connection, twins.native, "B", clock).claim() == []
        assert _states(connection, LIVE)[-1] == "pending"
        assert connection.execute("SELECT lease_owner FROM projection_jobs WHERE state='pending'").fetchall() == [(None,)]
    assert (twins.native / ".taskmaster" / LIVE).read_bytes() == edited


def test_a_held_entity_moves_neither_path(twins):
    with native_connection(twins.native) as connection:
        edited = _hand_edit(twins.native)
        _patch(connection, "test-epic-001", title="Flag me")
        _run(connection, twins.native, Clock())
        _archive(connection)
        outcomes, _ = _run(connection, twins.native, Clock(2_000_000.0))
    assert outcomes == {}
    assert (twins.native / ".taskmaster" / LIVE).read_bytes() == edited
    assert not _exists(twins.native, ARCHIVED)


def test_a_held_job_does_not_hold_back_exported_through(twins):
    with native_connection(twins.native) as connection:
        _hand_edit(twins.native)
        _patch(connection, "test-epic-001", title="Flag me")
        _run(connection, twins.native, Clock())
        _patch(connection, "test-epic-001", title="Held")
        seq = _patch(connection, "test-epic-002", title="Free")["commit_seq"]
        _run(connection, twins.native, Clock(2_000_000.0))
        assert outbox.exported_through(connection) >= seq


def test_store_status_lists_a_native_flag_and_its_stuck_job(twins):
    with native_connection(twins.native) as connection:
        _hand_edit(twins.native)
        _patch(connection, "test-epic-001", title="Flag me")
        _run(connection, twins.native, Clock())
    with twins.at(twins.native):
        report = bs.backlog_store_status()
    flagged = report.split("Flagged", 1)[1].split("\n", 2)
    assert LIVE in "".join(flagged[:2]), report
    stuck = report.split("Stuck", 1)[1].split("\n", 2) if "Stuck" in report else [""]
    assert LIVE in "".join(stuck[:2]), report
