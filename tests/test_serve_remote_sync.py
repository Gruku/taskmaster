# User intent: on a team server a teammate can never make the host import its own .taskmaster/ files into the shared store (backlog_sync, the resync tools, a sync-id poll); only the host operator can, and solo projects sync exactly as before (team-server-012 D1, closing 005's review finding 2).
from __future__ import annotations

import time
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import allowlist, protocol, sync_files
from taskmaster.coordinator import client as client_module
from test_native_service_build import root  # noqa: F401  (fixture)

MEMBER = "a" * 16
MARKER = "NOTE-MARKER-9"
REL = "notes/NOTE-900.md"


def _has_note(root_, ident):
    with closing(protocol.connect(root_, readonly=True)) as connection:
        return connection.execute("SELECT 1 FROM entity_core WHERE kind='note' AND public_id=?",
                                  (ident,)).fetchone() is not None


def _wait_exported(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} was never exported"
        time.sleep(0.05)
    return path.read_text(encoding="utf-8")


@pytest.fixture
def host(root, monkeypatch):  # noqa: F811
    """A native store with an in-process owner, and an untracked, valid host note the store has never seen."""
    from tests.native_coordinator_helpers import compatibility_client
    compatibility_client(root)                       # an in-process owner; tools never launch one
    created = bs.backlog_note(action="create", text="seed text")
    assert "NOTE-001" in created, created
    template = _wait_exported(root / ".taskmaster" / "notes" / "NOTE-001.md")
    (root / ".taskmaster" / REL).write_text(template.replace("NOTE-001", "NOTE-900").replace("seed text", MARKER),
                                            encoding="utf-8")
    assert not _has_note(root, "NOTE-900")
    return root


def _no_disk(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a refused sync must not list or read host files")
    monkeypatch.setattr(sync_files, "discover", refuse)
    monkeypatch.setattr(sync_files, "observe", refuse)


CALLS = {
    "backlog_sync()": lambda: bs.backlog_sync(),
    "backlog_sync(files)": lambda: bs.backlog_sync(files=[REL]),
    "backlog_sync(sync_id)": lambda: bs.backlog_sync(sync_id="0123456789abcdef0123456789abcdef"),
    "backlog_handover_resync()": lambda: bs.backlog_handover_resync(),
    "backlog_issue_resync()": lambda: bs.backlog_issue_resync(),
}


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize("principal", [MEMBER, None])
def test_a_member_cannot_sync_host_files(host, monkeypatch, name, principal):
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: principal)
    _no_disk(monkeypatch)
    answer = CALLS[name]()
    assert answer == f"Error: {allowlist.HOST_SYNC_REFUSED}", answer
    assert MARKER not in answer
    assert not _has_note(host, "NOTE-900")


def test_finding_2_trigger_leaves_the_store_untouched(host, monkeypatch):
    """005 review finding 2: a member's backlog_sync(files=[an untracked host note]), then a read."""
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: MEMBER)
    assert bs.backlog_sync(files=[REL]).startswith("Error:")
    assert MARKER not in bs.backlog_note(action="list")
    assert MARKER not in bs.backlog_note(action="get", note_id="NOTE-900")
    assert not _has_note(host, "NOTE-900")


def test_the_operator_still_syncs_host_files(host, monkeypatch):
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda: "operator")
    answer = bs.backlog_sync(files=[REL])
    assert not answer.startswith("Error:"), answer
    assert _has_note(host, "NOTE-900")


def test_solo_sync_is_unchanged(host, monkeypatch):
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", None)
    answer = bs.backlog_sync(files=[REL])
    assert "Sync complete" in answer and "1 imported" in answer, answer
    assert _has_note(host, "NOTE-900")
