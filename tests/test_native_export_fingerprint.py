"""User intent: N16-B — the exporter must stop reading and hashing a file it already
verified when the file visibly has not changed (same volume, file id, size, mtime_ns,
ctime_ns), while never publishing over bytes it has not verified. Any doubt — a racy
timestamp, a replaced file, an expired entry, a forced full verification, a verdict
that needs the bytes — means a full read.
"""
from __future__ import annotations

import os
import time

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection as outbox
from native_twins import commit_only, make_twins, native_connection

REL = "tasks/test-epic-001.md"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    outbox.reset_fingerprints()
    monkeypatch.delenv(outbox.FULL_VERIFY_ENV, raising=False)
    return make_twins(tmp_path, monkeypatch, _seed)


class Clock:
    at = 1_000_000.0

    def __call__(self):
        return self.at


def _render(job):
    return None if job.effect == "delete" else \
        f"{job.id} r{job.entity['revision']} {job.entity['fields'].get('title')}\n".encode()


@pytest.fixture
def reads(monkeypatch):
    """Every full read the exporter makes, by file name."""
    seen = []
    real = outbox._read_file

    def counting(path, **kwargs):
        seen.append(os.path.basename(str(path)))
        return real(path, **kwargs)
    monkeypatch.setattr(outbox, "_read_file", counting)
    return seen


@pytest.fixture
def settled(monkeypatch):
    """No racy window: a file counts as settled as soon as it is read. (Backdating
    cannot stand in for waiting on Windows, where `lstat`'s ctime is the change
    time and `utime` itself moves it.)"""
    monkeypatch.setattr(outbox, "RACY_NS", -1)


def _publish(connection, root, title):
    commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": title}})
    exporter = outbox.Exporter(connection, root / ".taskmaster", owner="A", session="A", clock=Clock())
    (job,) = exporter.claim()
    content = _render(job)
    exporter.intend([(job, content)])
    verdict = exporter.publish(job, content)
    exporter.finish()
    return verdict, content


def _derived(connection, root, content, seq):
    exporter = outbox.Exporter(connection, root / ".taskmaster", owner="A", session="A", clock=Clock())
    exporter.claim()
    exporter.intend([("backlog.yaml", content)])
    verdict = exporter.publish_derived("backlog.yaml", "backlog", content, seq)
    exporter.finish()
    return verdict


def test_an_unchanged_verified_file_is_not_read_again(twins, reads, settled):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        assert _derived(connection, root, b"render one\n", 7) == "exported"
        # First verification reads the file in full and records its fingerprint.
        reads.clear()
        assert _derived(connection, root, b"render one\n", 8) == "exported"
        assert reads == ["backlog.yaml"]
        # Unchanged since: the agreeing verification reads nothing.
        reads.clear()
        assert _derived(connection, root, b"render one\n", 9) == "exported"
        assert reads == []


def test_a_publish_over_a_verified_file_skips_the_reads_its_fingerprint_vouches_for(twins, reads, settled):
    root = twins.native
    path = root / ".taskmaster" / REL
    with native_connection(root) as connection:
        assert _publish(connection, root, "One")[0] == "exported"
        reads.clear()
        verdict, content = _publish(connection, root, "Two")
        assert verdict == "exported" and path.read_bytes() == content
        cold = len(reads)
        outbox._read_file(path)          # a verification (as a sync or agree would make)
        reads.clear()
        verdict, content = _publish(connection, root, "Three")
        assert verdict == "exported" and path.read_bytes() == content
        # Warm: the classify read is skipped; the aside check is skipped where a
        # rename keeps the fingerprint (Windows), and read in full elsewhere.
        assert len(reads) < cold
        assert len(reads) == (0 if os.name == "nt" else 1)


def test_a_same_size_edit_is_caught(twins, reads, settled):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)       # records the fingerprint
        path.write_bytes(b"render ONE\n")                     # same size, new mtime
        assert _derived(connection, root, b"render two\n", 9) == "flagged"
    assert path.read_bytes() == b"render ONE\n"


def test_a_replaced_file_is_read_in_full(twins, reads, settled):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        info = os.stat(path)
        other = path.with_name("elsewhere")
        other.write_bytes(b"render 1ne\n")
        os.utime(other, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(other, path)                               # same size and mtime, new file id
        reads.clear()
        assert _derived(connection, root, b"render two\n", 9) == "flagged"
        assert "backlog.yaml" in reads


def test_a_racy_file_is_never_trusted(twins, reads):
    root = twins.native
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)       # just written: inside the racy window
        reads.clear()
        _derived(connection, root, b"render one\n", 8)
        _derived(connection, root, b"render one\n", 9)
        assert reads == ["backlog.yaml", "backlog.yaml"]


@pytest.mark.parametrize("doubt", ["expired", "forced"])
def test_an_edit_the_fingerprint_cannot_see_is_caught_by_full_verification(twins, reads, settled, monkeypatch, doubt):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        key = outbox._cache_key(path)
        _fingerprint, digests, verified = outbox._FINGERPRINTS[key]
        # An edit no fingerprint shows (in place, same size, every timestamp put
        # back): simulated exactly, as the old digests under the file's current
        # fingerprint.
        path.write_bytes(b"Render one\n")
        outbox._FINGERPRINTS[key] = (outbox._fingerprint(os.lstat(path)), digests, verified)
        if doubt == "expired":
            monkeypatch.setattr(outbox, "FINGERPRINT_TTL", 0.0)
        else:
            monkeypatch.setenv(outbox.FULL_VERIFY_ENV, "1")
        assert _derived(connection, root, b"render two\n", 9) == "flagged"
    assert path.read_bytes() == b"Render one\n"


def test_without_full_verification_the_invisible_edit_would_be_trusted(twins, reads, settled):
    """The control for the test above: the fingerprint really is what vouched."""
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        key = outbox._cache_key(path)
        _fingerprint, digests, verified = outbox._FINGERPRINTS[key]
        path.write_bytes(b"Render one\n")
        outbox._FINGERPRINTS[key] = (outbox._fingerprint(os.lstat(path)), digests, verified)
        assert outbox._known(path) == digests
