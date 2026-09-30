"""User intent: a hand edit that no stat fingerprint can see — same size with its
timestamps put back, or a write through a memory map — must still be flagged and kept
byte for byte whenever the exporter would overwrite, remove or set aside that file
(N11's flag-and-keep-both). N16-B tried a stat-fingerprint cache and dropped it; these
guard the plain full-read path against any regression of that kind.
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
    return make_twins(tmp_path, monkeypatch, _seed)


class Clock:
    at = 1_000_000.0

    def __call__(self):
        return self.at


def _render(job):
    return None if job.effect == "delete" else \
        f"{job.id} r{job.entity['revision']} {job.entity['fields'].get('title')}\n".encode()


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
    return verdict


def _derived(connection, root, content, seq):
    exporter = _exporter(connection, root)
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


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_overwritten_on_a_task(twins, edit):
    root = twins.native
    path = root / ".taskmaster" / REL
    with native_connection(root) as connection:
        assert _publish(connection, root, "One") == "exported"
        EDITS[edit](path)
        edited = path.read_bytes()
        assert _publish(connection, root, "Two") == "flagged"
    assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_overwritten_on_a_derived_file(twins, edit):
    root = twins.native
    path = root / ".taskmaster" / "backlog.yaml"
    with native_connection(root) as connection:
        _derived(connection, root, b"render one\n", 7)
        _derived(connection, root, b"render one\n", 8)
        EDITS[edit](path)
        edited = path.read_bytes()
        assert _derived(connection, root, b"render two\n", 9) == "flagged"
    assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", EDITS)
def test_an_invisible_edit_is_flagged_not_removed(twins, edit):
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
def test_an_invisible_edit_made_before_the_set_aside_is_flagged_and_put_back(twins, edit):
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
        assert _publish(connection, root, "One") == "exported"
        assert _publish(connection, root, "Two", checkpoint) == "flagged"
    assert path.read_bytes() == edited["bytes"]
