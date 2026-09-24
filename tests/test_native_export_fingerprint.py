"""User intent: N16-B — the exporter may skip re-reading a file it already verified
only for the verdict that writes nothing (`agrees`). An overwrite, a set-aside or a
removal always reads the bytes it displaces in full, so a hand edit no fingerprint can
see (same size with mtime restored, or a memory-mapped write) is flagged and kept byte
for byte (N11's flag-and-keep-both), never lost.
"""
from __future__ import annotations

import mmap
import os

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
    monkeypatch.setenv(outbox.ENABLE_ENV, "1")    # the cache is opt-in; exercise it
    return make_twins(tmp_path, monkeypatch, _seed)


def test_the_cache_is_off_by_default(twins, reads, settled, monkeypatch):
    monkeypatch.delenv(outbox.ENABLE_ENV)
    root = twins.native
    with native_connection(root) as connection:
        for seq in (7, 8, 9):
            _derived(connection, root, b"render one\n", seq)
    assert outbox._FINGERPRINTS == {}


@pytest.fixture
def settled(monkeypatch):
    """No racy window: a file is recorded as soon as it is read. This makes the cache
    as eager as it can ever be — the worst case for every safety test here."""
    monkeypatch.setattr(outbox, "RACY_NS", -1)


class Clock:
    at = 1_000_000.0

    def __call__(self):
        return self.at


def _render(job):
    return None if job.effect == "delete" else \
        f"{job.id} r{job.entity['revision']} {job.entity['fields'].get('title')}\n".encode()


@pytest.fixture
def reads(monkeypatch):
    """Every full read the exporter's classification makes, by file name."""
    seen = []
    real = outbox._read_file

    def counting(path, **kwargs):
        seen.append(os.path.basename(str(path)))
        return real(path, **kwargs)
    monkeypatch.setattr(outbox, "_read_file", counting)
    return seen


def _exporter(connection, root, checkpoint=None):
    return outbox.Exporter(connection, root / ".taskmaster", owner="A", session="A", clock=Clock(),
                           checkpoint=checkpoint)


def _publish(connection, root, title, checkpoint=None):
    commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": title}})
    exporter = _exporter(connection, root, checkpoint)
    (job,) = exporter.claim()
    content = _render(job)
    exporter.intend([(job, content)])
    verdict = exporter.publish(job, content)
    exporter.finish()
    return verdict, content


def _derived(connection, root, content, seq, checkpoint=None):
    exporter = _exporter(connection, root, checkpoint)
    exporter.claim()
    exporter.intend([("backlog.yaml", content)])
    verdict = exporter.publish_derived("backlog.yaml", "backlog", content, seq)
    exporter.finish()
    return verdict


def _utime_edit(path):
    """A same-size edit whose editor puts the old timestamps back."""
    info = os.lstat(path)
    with open(path, "r+b") as handle:
        first = handle.read(1)
        handle.seek(0)
        handle.write(b"#" if first != b"#" else b"%")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))


def _mmap_edit(path):
    """A write through a memory map: neither mtime nor the change time moves."""
    with open(path, "r+b") as handle:
        mapped = mmap.mmap(handle.fileno(), 0)
        mapped[0:1] = b"#" if mapped[0:1] != b"#" else b"%"
        mapped.flush()
        mapped.close()


EDITS = {"utime-restored": _utime_edit, "mmap": _mmap_edit}


# ── The one verdict the cache may give: agrees (nothing is written) ─────────


def test_an_unchanged_verified_file_is_not_read_again(twins, reads, settled):
    root = twins.native
    with native_connection(root) as connection:
        assert _derived(connection, root, b"render one\n", 7) == "exported"
        reads.clear()
        assert _derived(connection, root, b"render one\n", 8) == "exported"
        assert reads == ["backlog.yaml"]       # first verification: full read, recorded
        reads.clear()
        assert _derived(connection, root, b"render one\n", 9) == "exported"
        assert reads == []                     # unchanged since: agrees without a read


def test_a_publish_that_changes_a_file_always_reads_it(twins, reads, settled):
    root = twins.native
    path = root / ".taskmaster" / REL
    with native_connection(root) as connection:
        assert _publish(connection, root, "One")[0] == "exported"
        outbox._read_file(path, trusted=True)  # warm: the fingerprint is recorded
        reads.clear()
        verdict, content = _publish(connection, root, "Two")
        assert verdict == "exported" and path.read_bytes() == content
        assert reads == ["test-epic-001.md"]   # classification reads in full; the aside is read too


def test_a_racy_file_is_never_trusted(twins, reads):
    root = twins.native
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)   # just written: inside the racy window
        reads.clear()
        _derived(connection, root, b"render one\n", 8)
        _derived(connection, root, b"render one\n", 9)
        assert reads == ["backlog.yaml", "backlog.yaml"]


# ── Invisible edits: real ones, every destructive path ──────────────────────


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_overwritten_on_a_task(twins, settled, edit):
    root = twins.native
    path = root / ".taskmaster" / REL
    with native_connection(root) as connection:
        assert _publish(connection, root, "One")[0] == "exported"
        outbox._read_file(path, trusted=True)
        EDITS[edit](path)
        edited = path.read_bytes()
        assert _publish(connection, root, "Two")[0] == "flagged"
    assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_overwritten_on_a_derived_file(twins, settled, edit):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)   # verified and recorded
        EDITS[edit](path)
        edited = path.read_bytes()
        assert _derived(connection, root, b"render two\n", 9) == "flagged"
    assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_removed(twins, settled, edit):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        EDITS[edit](path)
        edited = path.read_bytes()
        assert _derived(connection, root, None, 9) == "flagged"
    assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_made_before_the_set_aside_is_flagged_and_put_back(twins, settled, edit):
    """The edit lands after classification, before the rename aside: only the full
    read of the aside bytes can catch it."""
    root = twins.native
    path = root / ".taskmaster" / REL
    edited = {}

    def checkpoint(stage, rel):
        if stage == "temp_written" and rel == REL:
            EDITS[edit](path)
            edited["bytes"] = path.read_bytes()
    with native_connection(root) as connection:
        assert _publish(connection, root, "One")[0] == "exported"
        outbox._read_file(path, trusted=True)
        assert _publish(connection, root, "Two", checkpoint)[0] == "flagged"
    assert path.read_bytes() == edited["bytes"]


# ── Full verification and the fingerprint's inputs ──────────────────────────


@pytest.mark.parametrize("doubt", ["expired", "forced"])
def test_full_verification_bypasses_the_agrees_shortcut(twins, reads, settled, monkeypatch, doubt):
    root = twins.native
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        if doubt == "expired":
            monkeypatch.setattr(outbox, "FINGERPRINT_TTL", 0.0)
        else:
            monkeypatch.setenv(outbox.FULL_VERIFY_ENV, "1")
        reads.clear()
        assert _derived(connection, root, b"render one\n", 9) == "exported"
        assert reads == ["backlog.yaml"]


@pytest.mark.skipif(not outbox.CHANGE_TIME, reason="no change time from fstat on this interpreter")
def test_the_fingerprint_sees_a_utime_restored_edit(tmp_path, settled, monkeypatch):
    monkeypatch.setenv(outbox.ENABLE_ENV, "1")
    path = tmp_path / "f.md"
    path.write_bytes(b"hello\n")
    outbox._read_file(path, trusted=True)
    assert outbox._known_digest(path, trusted=True) is not None
    _utime_edit(path)
    assert outbox._known_digest(path, trusted=True) is None


def test_an_unproven_volume_never_caches(tmp_path, settled, monkeypatch):
    monkeypatch.setenv(outbox.ENABLE_ENV, "1")
    path = tmp_path / "f.md"
    path.write_bytes(b"hello\n")
    outbox._read_file(path, trusted=False)
    assert outbox._known_digest(path, trusted=True) is None
    assert outbox._known_digest(path, trusted=False) is None
    assert outbox.local_volume(r"\\server\share\project") is False
    assert outbox.local_volume("//server/share/project") is False


def test_the_volume_is_asked_per_exporter_not_per_process(twins, settled, monkeypatch):
    asked = []
    monkeypatch.setattr(outbox, "local_volume", lambda directory: asked.append(directory) or True)
    root = twins.native
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
    assert len(asked) == 2
