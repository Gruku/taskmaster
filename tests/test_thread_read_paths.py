# User intent: listing threads is a read — it must not queue behind the writer lock or
# fail outright on projection-only (network) storage, where the store refuses to write.
"""Thread listing reads without taking a write transaction (fix wave F9)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store
from tests.entity_helpers import (write_handover)


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    for sub in ("tasks", "local", "handovers"):
        (backlog_path / sub).mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "threads",
        "meta": {"project": "threads", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", backlog_path / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    write_handover(
        backlog_path / "backlog.yaml",
        tldr="a line of work",
        session_kind="end-of-day",
        thread="alpha",
    )
    # The first listing performs the one-off index backfill; later listings must
    # be pure reads.
    bs.backlog_thread_list()
    return backlog_path


def test_listing_threads_commits_nothing_after_the_backfill(project):
    before = store.status(project).max_seq

    output = bs.backlog_thread_list()

    assert "alpha" in output, output
    assert store.status(project).max_seq == before, (
        "the thread board took a write transaction to answer a read"
    )


def test_listing_threads_does_not_queue_behind_the_writer_lock(project):
    entered = threading.Event()
    release = threading.Event()
    opened = store.open_store(project)

    def hold_the_writer():
        with opened._writer_mutex():
            entered.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=hold_the_writer)
    holder.start()
    try:
        assert entered.wait(timeout=10)
        done = threading.Event()
        result: dict[str, str] = {}

        def read():
            result["out"] = bs.backlog_thread_list()
            done.set()

        reader = threading.Thread(target=read)
        reader.start()
        assert done.wait(timeout=10), "the read blocked on the writer mutex"
        reader.join(timeout=10)
    finally:
        release.set()
        holder.join(timeout=10)

    assert "alpha" in result["out"]


def test_listing_threads_works_on_projection_only_storage(project, monkeypatch):
    """A network store refuses transactions; the listing must still answer."""
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )

    output = bs.backlog_thread_list()

    assert "alpha" in output, output


def test_thread_index_backfill_is_skipped_on_projection_only_storage(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "readonly"
    backlog_path = _seed(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", backlog_path / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    write_handover(
        backlog_path / "backlog.yaml",
        tldr="unindexed work",
        session_kind="end-of-day",
        thread="beta",
    )
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )

    output = bs.backlog_thread_list()

    assert "beta" in output, output


def test_http_threads_endpoint_answers_on_projection_only_storage(project, monkeypatch):
    from taskmaster.taskmaster_v3 import list_threads

    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )

    rows = list_threads(bs._threads_data(project / "backlog.yaml"))

    assert json.dumps(rows)
    assert any(row["name"] == "alpha" for row in rows), rows
