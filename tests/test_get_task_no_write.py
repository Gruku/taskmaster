# plugins/taskmaster/tests/test_get_task_no_write.py
"""tm-audit-003: backlog_get_task must be a pure read — no write-on-read.

Covers §6 of docs/superpowers/specs/2026-07-04-tm-audit-003-write-on-read.md:
1. get_task never touches backlog.yaml (byte-identical content, _save spied to raise).
2. last_referenced is unchanged by a read but advances on a genuine mutation.
3. Stale-task detection survives a read — a stale todo task stays stale after
   get_task is called on it.
"""
from __future__ import annotations

import backlog_server
from backlog_server import (
    backlog_add_task,
    backlog_get_task,
    backlog_status,
    backlog_update_task,
)


def test_get_task_never_writes_backlog_file(tm_epic_phase, monkeypatch):
    backlog_add_task(epic="test-epic", task_id="T-RO", title="X", tldr="Short tldr.",
                      notes="Some notes.", phase="dev")

    bp = tm_epic_phase / ".taskmaster" / "backlog.yaml"
    before = bp.read_bytes()

    def _raise(*args, **kwargs):
        raise AssertionError("get_task must not call _save")

    monkeypatch.setattr(backlog_server, "_save", _raise)

    backlog_get_task("T-RO")
    backlog_get_task("T-RO", verbose=True)
    backlog_get_task("T-RO", sections=["notes"])
    backlog_get_task("T-RO", expand_links=True)

    after = bp.read_bytes()
    assert after == before


def test_last_referenced_unchanged_by_read_advanced_by_mutation(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-LR", title="X", tldr="Tldr.",
                      phase="dev")
    # Backdate last_referenced so a same-minute mutation timestamp can't
    # collide with it — isolates "did a read touch this" from clock precision.
    data = backlog_server._load()
    task, _ = backlog_server._find_task(data, "T-LR")
    task["last_referenced"] = "2020-01-01T00:00"
    backlog_server._save(data)

    backlog_get_task("T-LR")

    data = backlog_server._load()
    task, _ = backlog_server._find_task(data, "T-LR")
    assert task.get("last_referenced") == "2020-01-01T00:00"

    backlog_update_task("T-LR", tldr="Updated tldr.")

    data = backlog_server._load()
    task, _ = backlog_server._find_task(data, "T-LR")
    assert task.get("last_referenced") != "2020-01-01T00:00"


def test_stale_task_stays_stale_after_read(tm_epic_phase):
    backlog_add_task(epic="test-epic", task_id="T-STALE", title="Old one", tldr="T.",
                      phase="dev")
    data = backlog_server._load()
    task, _ = backlog_server._find_task(data, "T-STALE")
    task["last_referenced"] = "2020-01-01"
    backlog_server._save(data)

    # A read must not clear staleness.
    backlog_get_task("T-STALE")

    out = backlog_status()
    assert "T-STALE" in out
    assert "stale" in out.lower()
