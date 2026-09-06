"""User intent: a tool that updates one task changes that task and nothing else.

The real-backlog verification (task 5.3) made one `backlog_update_task` call and
got two `changes` rows: the second rewrote an unrelated task's file and stamped
it with a fabricated `created` date it never had. `_normalize_task` backfills
`created` on every task in the loaded dict, the dict write-back diffs against a
snapshot taken before that pass, so the whole backfill is persisted by whichever
tool happens to run first.
"""
from __future__ import annotations

import yaml

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _seed(tmp_path):
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    bp = tm / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "The one I edit", "status": "todo",
             "priority": "high", "created": "2026-01-01T00:00"},
            # No `created`: the field `_normalize_task` synthesizes.
            {"id": "e-002", "title": "The one I never name", "status": "todo",
             "priority": "high"},
        ]}],
        "phases": [], "context": {},
    }, sort_keys=False), encoding="utf-8")
    store.reset_for_tests()
    return bp


def test_updating_one_task_does_not_rewrite_another(tmp_path, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    opened = store.open_store(backlog_path=bp, session="one-write-one-entity")
    untouched = v3.task_file_path(bp, "e-002")
    before_bytes = untouched.read_bytes()
    before_seq = opened.status().max_seq

    out = bs.backlog_update_task("e-001", "notes", "store verification")
    assert "Error" not in out, out

    assert untouched.read_bytes() == before_bytes, (
        "a write to e-001 rewrote e-002's file"
    )
    rows = list(opened.connection.execute(
        "SELECT id, fields FROM changes WHERE seq > ? AND kind='task'", (before_seq,)
    ))
    assert [row[0] for row in rows] == ["e-001"], rows
    store.reset_for_tests()


def test_the_backfilled_created_sentinel_never_reaches_disk(tmp_path, monkeypatch):
    """`2025-01-01T00:00` is a display fallback, not a fact about the task."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="no-sentinel-on-disk")

    assert "Error" not in bs.backlog_update_task("e-001", "notes", "x")

    frontmatter, _ = v3.read_task_file(v3.task_file_path(bp, "e-002"))
    assert "created" not in frontmatter, frontmatter
    store.reset_for_tests()


def test_the_backfill_is_still_visible_to_readers(tmp_path, monkeypatch):
    """Not writing it must not mean not showing it: the reason the backfill
    exists is that every read path wants a `created` to sort and display by."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="backfill-still-read")

    task, _epic = bs._find_task(bs._load(), "e-002")

    assert task["created"] == "2025-01-01T00:00", task
    store.reset_for_tests()


def test_an_explicit_write_to_created_is_still_persisted(tmp_path, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="explicit-created")

    # The revert must key on the synthesized value, not on the field name: a
    # tool that deliberately sets `created` still writes it.
    with bs._transaction(tool="test:explicit-created") as data:
        task, _epic = bs._find_task(data, "e-002")
        task["created"] = "2026-02-02T00:00"
        bs._mutate_and_save(data)

    frontmatter, _ = v3.read_task_file(v3.task_file_path(bp, "e-002"))
    assert frontmatter["created"] == "2026-02-02T00:00", frontmatter
    store.reset_for_tests()
