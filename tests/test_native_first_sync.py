# User intent: N16 track A - the first explicit sync after a legacy->native cutover must be fast
# (activation seeds merge bases and fingerprints from bytes it hashed itself; observes need no Git
# HEAD probe) and honest (a write that committed is never reported as "outcome uncertain").
"""Activation seeding, the observe HEAD-probe rule and the honest sync outcome."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from taskmaster.coordinator import checkouts, sync_files, sync_worker
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import carryover, cutover
from tests.native_twins import activate_native, committed
from tests.test_native_cutover import project, quiesce  # noqa: F401
from native_git_helpers import git, init_repo
from test_native_service import root, request  # noqa: F401
from test_native_service_sync import REL, edit, title

TAMPERED = "tasks/cut-epic-002.md"
QUARANTINED = "tasks/cut-epic-001.md"


def _db(root):
    return closing(sqlite3.connect(cutover.database_path(root), isolation_level=None, timeout=30))


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _age_projection(root: Path) -> None:
    """Fingerprints are recorded only for files older than the racy window (mtime and ChangeTime)."""
    from test_native_sync_perf import age_projection
    age_projection(root)


def _adopted_without_bases(root: Path) -> None:
    """A legacy store adopted from its files: no merge bases at all (CodeMaestro's shape),
    and one quarantined row."""
    with _db(root) as connection:
        connection.execute("DELETE FROM projection_base")
        connection.execute("UPDATE projection SET quarantined=1,quarantine_hash=content_hash WHERE file=?",
                           (QUARANTINED,))


def _tamper_when_activation_begins(root: Path, monkeypatch) -> bytes:
    """Bytes that differ from the recorded digest, written after the cutover's drift check."""
    path = root / ".taskmaster" / TAMPERED
    changed = path.read_bytes() + b"\nEdited by hand during the cutover.\n"

    def hook(name):
        if name == "activate:begin":
            path.write_bytes(changed)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)
    return changed


def _bases(root: Path) -> dict[str, bytes]:
    with _db(root) as connection:
        return {rel: bytes(content) for rel, content in connection.execute("SELECT file,content FROM projection_base")}


def _published(root: Path) -> dict[str, str]:
    with _db(root) as connection:
        return dict(connection.execute("SELECT file,content_hash FROM projection WHERE quarantined=0 "
                                       "AND file NOT LIKE 'local/%'"))


# ── 1. Activation seeds merge bases only from bytes it hashed ────────────────

# The hook stands in for a hand edit, but it runs on the cutover's stack.
@pytest.mark.allow_projection_bypass
def test_activation_seeds_bases_only_for_files_whose_bytes_it_hashed(project, quiesce, monkeypatch):
    _adopted_without_bases(project)
    changed = _tamper_when_activation_begins(project, monkeypatch)
    report = cutover.cutover(project)
    assert report["ok"], report
    bases, published = _bases(project), _published(project)
    backlog = project / ".taskmaster"
    assert TAMPERED not in bases and QUARANTINED not in bases
    assert (backlog / TAMPERED).read_bytes() == changed
    expected = sorted(rel for rel, value in published.items()
                      if rel != TAMPERED and (backlog / rel).is_file()
                      and _sha1((backlog / rel).read_bytes()) == value)
    assert expected and sorted(bases) == expected
    for rel, content in bases.items():
        assert content == (backlog / rel).read_bytes() and _sha1(content) == published[rel]
    counts = report["stages"]["activate"]["bases"]
    assert counts["seeded"] == len(expected) and counts["differs"] == 1, counts
    with _db(project) as connection:
        detail = cutover._detail(cutover.journal(connection), "activate")
    assert sorted(detail["seeded_bases"]) == expected


def test_activation_never_replaces_an_existing_base(project, quiesce):
    rel = "tasks/cut-epic-002.md"
    with _db(project) as connection:
        connection.execute("UPDATE projection_base SET content=? WHERE file=?", (b"an older generation", rel))
        kept = {r: bytes(c) for r, c in connection.execute("SELECT file,content FROM projection_base")}
    assert cutover.cutover(project)["ok"]
    bases = _bases(project)
    assert {r: bases.get(r) for r in kept} == kept  # an existing base, trusted or not, is kept as it was


def test_the_post_activation_oracle_allows_exactly_the_seeded_bases(project, quiesce):
    _adopted_without_bases(project)
    assert cutover.cutover(project)["ok"]
    with _db(project) as connection:
        entries = cutover.journal(connection)
        before = cutover._detail(entries, "backup")["carryover"]
        seeded = cutover._detail(entries, "activate")["seeded_bases"]
        assert seeded
        connection.execute("BEGIN")
        try:
            assert carryover.verify_carryover(connection, before, seeded_bases=seeded) == []
            # Without the journaled list the added rows are a change, as before N16.
            assert any("projection_base" in p for p in carryover.verify_carryover(connection, before))
            # A seeded base that is not the recorded bytes is refused, as is an unnamed extra row.
            connection.execute("UPDATE projection_base SET content=? WHERE file=?", (b"forged", seeded[0]))
            problems = carryover.verify_carryover(connection, before, seeded_bases=seeded)
            assert any(seeded[0] in p for p in problems), problems
        finally:
            connection.rollback()
        connection.execute("BEGIN")
        try:
            connection.execute("INSERT INTO projection_base(file,content) VALUES('tasks/unnamed.md',x'00')")
            assert carryover.verify_carryover(connection, before, seeded_bases=seeded)
        finally:
            connection.rollback()


def test_a_crash_before_the_activation_commit_seeds_nothing_and_resume_seeds(project, quiesce, monkeypatch):
    _adopted_without_bases(project)

    def hook(name):
        if name == "activate:before-commit":
            raise RuntimeError("crash at activate:before-commit")
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)
    with pytest.raises(RuntimeError):
        cutover.cutover(project)
    assert _bases(project) == {}
    assert not sync_files.cache_path(project).exists()
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    assert cutover.cutover(project, resume=True)["ok"]
    assert _bases(project)


# ── 2. Activation seeds the sync fingerprint cache from the same reads ───────

# The hook stands in for a hand edit, but it runs on the cutover's stack.
@pytest.mark.allow_projection_bypass
def test_activation_seeds_the_fingerprint_cache_from_the_bytes_it_read(project, quiesce, monkeypatch):
    _adopted_without_bases(project)
    _age_projection(project)
    changed = _tamper_when_activation_begins(project, monkeypatch)  # fresh mtime: racy, never cached
    assert cutover.cutover(project)["ok"]
    cache = json.loads(sync_files.cache_path(project).read_text(encoding="utf-8"))
    backlog = project / ".taskmaster"
    entries = cache["checkouts"][sync_files._cache_key(backlog)]["entries"]
    seeded = _bases(project)
    assert entries and set(entries) <= set(seeded) | {TAMPERED}
    assert TAMPERED not in entries and _sha1(changed) not in json.dumps(entries)
    for rel, (fingerprint, digests) in entries.items():
        assert digests[0] == _sha1((backlog / rel).read_bytes())
    scan = sync_files.open_scan(project, backlog)
    assert all(scan.digests(rel) is not None for rel in entries)


def test_twins_activation_seeds_bases_and_commits_the_same_domain(tmp_path, monkeypatch):
    from tests.test_native_cutover import build_project
    root = build_project(tmp_path / "twin", monkeypatch)
    before = committed(root)
    _adopted_without_bases(root)
    activate_native(root)
    assert committed(root) == before
    assert set(_bases(root)) == set(_published(root))


# ── 3. Observe plans need no Git HEAD probe; imports keep theirs ─────────────

def _drop_native_bases(root: Path, keep=()) -> int:
    """Bases lost (as on an adopted store); `keep` retains some, so an edit there is a merge."""
    with _db(root) as connection:
        connection.execute(f"DELETE FROM projection_base WHERE file NOT IN ({','.join('?' for _ in keep)})",
                           tuple(keep))
        return connection.execute("SELECT COUNT(*) FROM projection WHERE quarantined=0 "
                                  "AND file NOT LIKE 'local/%'").fetchone()[0] - len(keep)


def test_observes_skip_the_head_probe_and_an_import_keeps_it(root, monkeypatch):
    init_repo(root)
    probes = []
    original = checkouts.observe
    monkeypatch.setattr(checkouts, "observe", lambda checkout: probes.append(1) or original(checkout))
    with Coordinator(root):
        client = Client(root, autostart=False, timeout=120)
        assert client.sync()["state"] == "synchronized"
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", "published generation")
        assert client.sync()["state"] == "synchronized"
    files = _drop_native_bases(root, keep=[REL])
    edit(root, "Authored after the bases were lost")
    probes.clear()
    with Coordinator(root):
        client = Client(root, autostart=False, timeout=120)
        result = client.sync()
    assert result["state"] == "synchronized", result
    assert result["observed"] == files >= 1, (result, files)
    assert [item["file"] for item in result["imports"]] == [REL], result
    # detect, the one import's check and the completion check: never one per observed file.
    assert len(probes) == 3, probes
    assert title(root) == "Authored after the bases were lost"


def test_head_moving_during_observes_records_them_but_not_the_observation(root, monkeypatch):
    init_repo(root)
    with Coordinator(root):
        client = Client(root, autostart=False, timeout=120)
        assert client.sync()["state"] == "synchronized"
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", "published generation")
        assert client.sync()["state"] == "synchronized"
    _drop_native_bases(root)
    remembered = []
    monkeypatch.setattr(checkouts, "remember", lambda *a, **k: remembered.append(a))
    with Coordinator(root) as owner:
        moved = []

        def checkpoint(stage):
            if stage == "sync_prepared" and not moved:
                moved.append(1)
                git(root, "commit", "-q", "--allow-empty", "-m", "concurrent commit")
        owner.checkpoint = checkpoint
        result = owner.sync(caller_scope="head", request_id="moves")
    assert moved and result["observed"] > 1, result
    assert not any("HEAD moved" in notice for notice in result["notices"]), result
    assert remembered == []  # HEAD moved: the next sync re-detects instead of skipping


# ── 4. Honest outcome: time reserve, grace, receipt ─────────────────────────

def _block_writer_at_admission(owner, seconds=None, release=None):
    """The next command the writer admits waits (for `seconds`, or until `release`)."""
    def writer_checkpoint(stage):
        if stage == "admitted":
            owner.checkpoint = lambda inner: None
            if release is not None:
                release.wait(30)
            else:
                time.sleep(seconds)
    return writer_checkpoint


def test_an_import_that_commits_within_the_grace_is_reported_committed(root, monkeypatch):
    monkeypatch.setattr(sync_worker, "IMPORT_GRACE", 5)
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Slow but committed")

        def checkpoint(stage):
            if stage == "sync_prepared":
                owner.checkpoint = _block_writer_at_admission(owner, seconds=2.0)
        owner.checkpoint = checkpoint
        result = owner.sync(caller_scope="grace", request_id="one", files=[REL], timeout=1)
    assert not any("uncertain" in n for n in result["notices"]), result
    assert [item["state"] for item in result["imports"]] == ["accepted"], result
    assert title(root) == "Slow but committed"


def test_an_import_still_queued_after_the_grace_is_cancelled_and_not_committed(root, monkeypatch):
    monkeypatch.setattr(sync_worker, "IMPORT_GRACE", 0.5)
    release = threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Never started")
        blockers = []

        def checkpoint(stage):
            if stage == "sync_prepared" and not blockers:
                owner.checkpoint = _block_writer_at_admission(owner, release=release)
                other_field = dict(request(client, "blocker"), arguments={"id": "test-epic-001",
                                                                          "set": {"priority": "low"}})
                blockers.append(owner.submit(other_field))
                time.sleep(0.3)  # the blocker is admitted before the import is queued behind it
        owner.checkpoint = checkpoint
        try:
            result = owner.sync(caller_scope="queued", request_id="one", files=[REL], timeout=1)
        finally:
            release.set()
        blockers[0].result(timeout=30)
        (item,) = result["imports"]
        assert item["state"] == "not_committed" and item["may_have_committed"] is False, result
        assert any("not committed" in n for n in result["notices"]), result
        assert not any("uncertain" in n for n in result["notices"]), result
        assert client.receipt(item["caller_scope"], item["request_id"]) == {"state": "unknown"}
        assert title(root) == "Service task"
        retried = owner.sync(caller_scope="queued", request_id="one", files=[REL], timeout=60)
        assert retried["state"] == "synchronized", retried
        assert title(root) == "Never started"


def test_an_import_the_writer_is_still_running_is_the_only_uncertain_outcome(root, monkeypatch):
    monkeypatch.setattr(sync_worker, "IMPORT_GRACE", 0.5)
    release = threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Still running")

        def checkpoint(stage):
            if stage == "sync_prepared":
                owner.checkpoint = _block_writer_at_admission(owner, release=release)
        owner.checkpoint = checkpoint
        try:
            result = owner.sync(caller_scope="running", request_id="one", files=[REL], timeout=1)
        finally:
            release.set()
    (item,) = result["imports"]
    assert item["state"] == "uncertain" and item["may_have_committed"], result
    assert any("still running" in n for n in result["notices"]), result


def test_an_observe_outcome_is_reported_apart_from_imports(root, monkeypatch):
    monkeypatch.setattr(sync_worker, "IMPORT_GRACE", 0.5)
    _drop_native_bases(root)
    release = threading.Event()
    with Coordinator(root) as owner:
        def checkpoint(stage):
            if stage == "sync_prepared":
                owner.checkpoint = _block_writer_at_admission(owner, release=release)
        owner.checkpoint = checkpoint
        try:
            result = owner.sync(caller_scope="observe", request_id="one", files=[REL], timeout=1)
        finally:
            release.set()
    assert result["imports"] == [] and result["observed"] == 0, result
    (item,) = result["observes"]
    assert item["state"] == "uncertain" and item["file"] == REL, result
    assert not any("import outcome" in n for n in result["notices"]), result
    assert any(n.startswith(f"sync pending: {REL}: ") and "base record" in n for n in result["notices"]), result


def test_no_file_is_started_inside_the_time_reserve(root, monkeypatch):
    monkeypatch.setattr(sync_worker, "FILE_RESERVE", 10)
    monkeypatch.setattr(sync_worker, "RESERVE_SHARE", 1.0)
    submitted = []
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Not started")
        original = owner.submit
        owner.submit = lambda envelope: submitted.append(envelope["operation"]) or original(envelope)
        result = owner.sync(caller_scope="reserve", request_id="one", files=[REL], timeout=5)
    assert submitted == ["sync.begin"], submitted
    assert REL in result["unresolved"] and any("time budget" in n for n in result["notices"]), result
    assert result["imports"] == []


# ── 5. Review follow-ups: cancel/retry, error futures, reply bound ───────────

def _wait(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_retry_after_a_cancel_the_queue_could_not_undo_runs_afresh(root):
    """The writer already dequeued the command (it waits at the admission gate), so the
    cancel cannot remove it; the same key submitted again must run, not inherit the
    cancelled future."""
    from taskmaster.native.contracts import CancelledBeforeExecution
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        owner.pause_writer()
        try:
            first = owner.submit(request(client, "retry-key", "Cancelled first"))
            assert _wait(lambda: owner.queue.empty())  # dequeued, held at the gate
            assert owner.cancel("service-tests", "retry-key")["state"] == "cancelled_before_execution"
            second = owner.submit(request(client, "retry-key", "Cancelled first"))
            assert second is not first
        finally:
            owner.resume_writer()
        with pytest.raises(CancelledBeforeExecution):
            first.result(timeout=30)
        assert second.result(timeout=30)["commit_seq"] > 0
        assert title(root) == "Cancelled first"
        assert _wait(lambda: not owner.pending)


def test_a_cancelled_future_in_the_file_loop_is_a_pending_notice(root, monkeypatch):
    from concurrent.futures import Future
    from taskmaster.native.contracts import CancelledBeforeExecution
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Cancelled elsewhere")
        original = owner.submit

        def submit(envelope):
            if envelope["operation"] != "sync.apply":
                return original(envelope)
            future = Future()
            future.set_exception(CancelledBeforeExecution("cancelled before execution"))
            return future
        owner.submit = submit
        result = owner.sync(caller_scope="cancelled", request_id="one", files=[REL], timeout=30)
    (item,) = result["imports"]
    assert item["state"] == "not_committed" and item["may_have_committed"] is False, result
    assert REL in result["unresolved"] and title(root) == "Service task"


def test_settle_maps_writer_failures_by_what_is_known(root):
    from concurrent.futures import Future
    from taskmaster.coordinator.protocol import ServiceUnavailable
    from taskmaster.native.contracts import CancelledBeforeExecution, Conflict
    with Coordinator(root) as owner:
        def failed(error):
            future = Future()
            future.set_exception(error)
            return future
        interrupted = failed(ServiceUnavailable("writer interrupted; retry the same request_id"))
        assert sync_worker._settle(owner, interrupted, "scope", "never-committed", 0.1)[:2] == (None, "uncertain")
        cancelled = failed(CancelledBeforeExecution("cancelled while queued"))
        assert sync_worker._settle(owner, cancelled, "scope", "key", 0.1)[:2] == (None, "not_committed")
        with pytest.raises(Conflict):  # a refusal: rolled back, reported as the file's reason
            sync_worker._settle(owner, failed(Conflict("revision changed")), "scope", "key", 0.1)
        # A receipt proves the commit whatever the transport said.
        client = Client(root, autostart=False)
        receipt = client.execute(request(client, "committed-key", "Committed"))["receipt"]
        lost = failed(ServiceUnavailable("writer interrupted; retry the same request_id"))
        assert sync_worker._settle(owner, lost, "service-tests", "committed-key", 0.1)[:2] == (receipt, "committed")


def test_the_reply_stays_within_the_budget_plus_the_finish_minimum(root, monkeypatch):
    """Grace (import) and sync.finish share one FINISH_TIMEOUT allowance past the budget."""
    monkeypatch.setattr(sync_worker, "IMPORT_GRACE", 5)
    release = threading.Event()
    with Coordinator(root) as owner:
        client = Client(root, autostart=False)
        client.sync(files=[REL])
        edit(root, "Committed in the grace")
        operations = []
        original = owner.submit

        def submit(envelope):
            operations.append(envelope["operation"])
            if envelope["operation"] == "sync.finish":
                owner.checkpoint = _block_writer_at_admission(owner, release=release)
            return original(envelope)
        owner.submit = submit

        def checkpoint(stage):
            if stage == "sync_prepared":
                owner.checkpoint = _block_writer_at_admission(owner, seconds=3.0)
        owner.checkpoint = checkpoint
        started = time.monotonic()
        try:
            result = owner.sync(caller_scope="bound", request_id="one", files=[REL], timeout=1)
        finally:
            elapsed = time.monotonic() - started
            release.set()
    assert [item["state"] for item in result["imports"]] == ["accepted"], result
    assert "sync.finish" in operations, (operations, result)
    assert any("completion receipt uncertain" in n for n in result["notices"]), result
    assert elapsed < 1 + sync_worker.FINISH_TIMEOUT + 1.0, elapsed  # was budget + grace + 5 s


# ── 6. Backup archive: one read per file; archive, manifest and disk agree ───

def test_the_backup_reads_each_projection_file_once_and_its_records_agree(project, quiesce, monkeypatch):
    import builtins
    import io
    import zipfile
    backlog = (project / ".taskmaster").resolve()
    opened, active = {}, []
    real_open = builtins.open

    def counting_open(file, *args, **kwargs):
        try:
            path = Path(os.fspath(file)).resolve()
        except TypeError:
            path = None
        if active and path is not None and backlog in path.parents and "local" not in path.relative_to(backlog).parts:
            opened[path] = opened.get(path, 0) + 1
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", counting_open)
    monkeypatch.setattr(io, "open", counting_open)
    real_backup = cutover.write_backup

    def counted_backup(*args, **kwargs):
        active.append(1)
        try:
            return real_backup(*args, **kwargs)
        finally:
            active.clear()
    monkeypatch.setattr(cutover, "write_backup", counted_backup)
    report = cutover.cutover(project)
    assert report["ok"], report
    assert opened and max(opened.values()) == 1, opened
    backup = report["stages"]["backup"]
    manifest = json.loads(Path(backup["manifest"]).read_text(encoding="utf-8"))
    listed = {entry["path"]: entry for entry in manifest["projection_files"]}
    with zipfile.ZipFile(manifest["projection_archive"]["path"]) as archive:
        assert archive.testzip() is None
        assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())
        zipped = {name: archive.read(name) for name in archive.namelist()}
    assert set(zipped) == set(listed) and len(listed) == manifest["projection_archive"]["files"]
    for rel, data in zipped.items():
        assert listed[rel]["sha256"] == hashlib.sha256(data).hexdigest() and listed[rel]["size"] == len(data)
    with _db(project) as connection:
        assert cutover.projection_files(project, connection) == manifest["projection_files"]


# ── 7. Combined-review follow-ups: finish floor, store errors while settling ─

def test_the_finish_wait_keeps_a_floor_however_long_settling_took():
    assert sync_worker._finish_timeout(0, 0) == sync_worker.FINISH_TIMEOUT
    assert sync_worker._finish_timeout(0, 2.0) == sync_worker.FINISH_TIMEOUT - 2.0
    assert sync_worker._finish_timeout(0, 7.5) == sync_worker.FINISH_FLOOR > 0
    assert sync_worker._finish_timeout(30, 7.5) == 30


@pytest.mark.parametrize("failing", ["cancel", "receipt"])
def test_a_store_error_while_settling_is_uncertain_not_a_failed_sync(root, monkeypatch, failing):
    from concurrent.futures import Future
    from taskmaster.coordinator.protocol import ServiceUnavailable

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    with Coordinator(root) as owner:
        if failing == "cancel":
            monkeypatch.setattr(owner, "cancel", locked)
            future = Future()  # still running after the grace
        else:
            monkeypatch.setattr(sync_worker, "_receipt", locked)
            future = Future()
            future.set_exception(ServiceUnavailable("writer interrupted; retry the same request_id"))
        assert sync_worker._settle(owner, future, "scope", "key", 0.05)[:2] == (None, "uncertain")
