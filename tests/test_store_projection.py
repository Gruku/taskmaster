"""Contract tests for exporting and recovering the SQLite file projection."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter


def _write_v4_projection(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "store-tests", "schema_version": 4},
                "context": {"focus": "must-not-persist"},
                "epics": [
                    {
                        "id": "core",
                        "name": "Store core",
                        "status": "in-progress",
                        "phase": "build",
                    }
                ],
                "phases": [
                    {"id": "build", "name": "Build", "status": "in-progress"}
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    task_path = tm_dir / "tasks" / "core-001.md"
    task_path.write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Alpha",
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            "## Notes\n\nUnicode → value (safe): yes",
        ),
        encoding="utf-8",
    )
    return backlog_path, task_path


@pytest.fixture()
def store_api():
    from taskmaster import store

    store.reset_for_tests()
    yield store
    store.reset_for_tests()


def _query(store_api, backlog_path: Path, sql: str, params: tuple = ()) -> list:
    with sqlite3.connect(store_api.db_path(backlog_path)) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(sql, params))


def test_bootstrap_projection_marks_schema_and_drops_runtime_context(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)

    store_api.open_store(backlog_path=backlog_path, session="projection-schema-test")

    projected = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    assert projected["meta"]["projection_schema"] == store_api.PROJECTION_SCHEMA == 5
    assert "context" not in projected
    assert all("tasks" not in epic for epic in projected["epics"])

    frontmatter, body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert frontmatter["title"] == "Alpha"
    assert body.removesuffix("\n") == "## Notes\n\nUnicode → value (safe): yes"


def test_missing_projection_file_is_reexported_not_deleted(tmp_path, store_api):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="missing-file-test")
    task_path.unlink()

    with store.transaction(tool="missing-file-scan"):
        pass

    assert task_path.is_file()
    frontmatter, _ = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert frontmatter["title"] == "Alpha"
    row = _query(
        store_api,
        backlog_path,
        "SELECT deleted, archived FROM entities WHERE kind='task' AND id='core-001'",
    )[0]
    projection = _query(
        store_api,
        backlog_path,
        "SELECT dirty, quarantined FROM projection WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert (row["deleted"], row["archived"]) == (0, 0)
    assert (projection["dirty"], projection["quarantined"]) == (0, 0)


def test_export_failure_commits_dirty_and_next_transaction_drains(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="retry-test")
    baseline_bytes = task_path.read_bytes()
    real_replace = os.replace
    attempts = 0

    def sharing_violation(source, destination):
        nonlocal attempts
        if Path(destination) == task_path:
            attempts += 1
            raise PermissionError(13, "simulated sharing violation", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", sharing_violation)
    with store.transaction(tool="update-during-contention") as tx:
        task = tx.get("task", "core-001")
        task["title"] = "Committed despite contention"
        tx.put("task", "core-001", task)

    assert attempts > 1, "sharing failures must be retried before export is deferred"
    assert task_path.read_bytes() == baseline_bytes
    committed = _query(
        store_api,
        backlog_path,
        "SELECT doc FROM entities WHERE kind='task' AND id='core-001'",
    )[0]
    assert json.loads(committed["doc"])["title"] == "Committed despite contention"
    projection = _query(
        store_api,
        backlog_path,
        "SELECT dirty, quarantined FROM projection WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert (projection["dirty"], projection["quarantined"]) == (1, 0)
    base = _query(
        store_api,
        backlog_path,
        "SELECT content FROM projection_base WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert bytes(base["content"]) == baseline_bytes
    failures = _query(
        store_api,
        backlog_path,
        "SELECT op FROM changes WHERE kind='task' AND id='core-001' "
        "AND op='export-fail'",
    )
    assert failures
    assert any("export pending" in warning.lower() for warning in tx.warnings)

    # Any later transaction, even a no-op from another tool, drains dirty files.
    monkeypatch.setattr(os, "replace", real_replace)
    with store.transaction(tool="next-call-drains"):
        pass

    frontmatter, _ = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert frontmatter["title"] == "Committed despite contention"
    projection = _query(
        store_api,
        backlog_path,
        "SELECT dirty, quarantined FROM projection WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert (projection["dirty"], projection["quarantined"]) == (0, 0)
    assert not _query(
        store_api,
        backlog_path,
        "SELECT 1 FROM projection_base WHERE file=?",
        ("tasks/core-001.md",),
    )
    assert not list(task_path.parent.glob("core-001.md.tmp.*"))


def test_process_crash_after_replace_cannot_resurrect_uncommitted_projection(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    parent = store_api.open_store(backlog_path=backlog_path, session="crash-parent")
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from taskmaster import store

        opened = store.open_store(
            backlog_path=Path({str(backlog_path)!r}), session="crash-child"
        )
        opened._regenerate_progress_if_due = lambda tx: os._exit(23)
        with opened.transaction(tool="crash-before-commit") as tx:
            task = tx.get("task", "core-001")
            task["title"] = "UNCOMMITTED-RESURRECTED"
            tx.put("task", "core-001", task)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], check=False)
    assert result.returncode == 23
    assert "UNCOMMITTED-RESURRECTED" in task_path.read_text(encoding="utf-8")

    with parent.transaction(tool="already-open-parent-recovers"):
        pass
    loaded = parent.load_dict()
    assert loaded["epics"][0]["tasks"][0]["title"] == "Alpha"
    frontmatter, _body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert frontmatter["title"] == "Alpha"
    assert not list(parent.db_path.parent.glob("export-intent.*.json"))


def test_crash_recovery_quarantines_mixed_uncommitted_and_user_edits(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    parent = store_api.open_store(backlog_path=backlog_path, session="mixed-parent")
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from taskmaster import store
        opened = store.open_store(backlog_path=Path({str(backlog_path)!r}), session="mixed-child")
        opened._regenerate_progress_if_due = lambda tx: os._exit(23)
        with opened.transaction(tool="mixed-crash") as tx:
            task = tx.get("task", "core-001")
            task["title"] = "UNCOMMITTED"
            tx.put("task", "core-001", task)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], check=False)
    assert result.returncode == 23
    frontmatter, body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    frontmatter["status"] = "done"
    task_path.write_text(render_frontmatter(frontmatter, body), encoding="utf-8")

    with parent.transaction(tool="recover-mixed-intent"):
        pass

    recovered, _ = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert (recovered["title"], recovered["status"]) == ("Alpha", "todo")
    quarantined = list(task_path.parent.glob(f"{task_path.name}.intent-conflict-*"))
    assert len(quarantined) == 1
    mixed, _ = parse_frontmatter(quarantined[0].read_text(encoding="utf-8"))
    assert (mixed["title"], mixed["status"]) == ("UNCOMMITTED", "done")


def test_archive_remove_contention_commits_dirty_then_converges(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="remove-contention")
    real_unlink = Path.unlink
    attempts = 0

    def held_unlink(path, *args, **kwargs):
        nonlocal attempts
        if path == task_path:
            attempts += 1
            raise PermissionError(13, "held open", str(path))
        return real_unlink(path, *args, **kwargs)

    clock = iter((0.0, 3.0))
    monkeypatch.setattr(Path, "unlink", held_unlink)
    monkeypatch.setattr(store_api.time, "monotonic", lambda: next(clock))
    with opened.transaction(tool="archive-held") as tx:
        tx.archive("task", "core-001")

    assert attempts == 1
    assert task_path.exists()
    assert "tasks/core-001.md" in opened.status().dirty_files
    monkeypatch.setattr(Path, "unlink", real_unlink)
    with opened.transaction(tool="archive-drain"):
        pass
    assert not task_path.exists()
    assert (task_path.parent / "archive" / task_path.name).exists()
    assert opened.status().dirty_files == ()


def test_archive_remove_contention_merges_edit_made_before_drain(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="remove-merge")
    real_unlink = Path.unlink

    def held_unlink(path, *args, **kwargs):
        if path == task_path:
            raise PermissionError(13, "held open", str(path))
        return real_unlink(path, *args, **kwargs)

    clock = iter((0.0, 3.0))
    monkeypatch.setattr(Path, "unlink", held_unlink)
    monkeypatch.setattr(store_api.time, "monotonic", lambda: next(clock))
    with opened.transaction(tool="archive-held-edit") as tx:
        tx.archive("task", "core-001")

    monkeypatch.setattr(Path, "unlink", real_unlink)
    frontmatter, body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    frontmatter["title"] = "External title before drain"
    task_path.write_text(render_frontmatter(frontmatter, body), encoding="utf-8")
    with opened.transaction(tool="archive-merge-drain"):
        pass

    archived = task_path.parent / "archive" / task_path.name
    assert not task_path.exists()
    assert parse_frontmatter(archived.read_text(encoding="utf-8"))[0]["title"] == (
        "External title before drain"
    )
    assert _query(
        store_api,
        backlog_path,
        "SELECT 1 FROM changes WHERE tool='archive-merge-drain' AND op='merge'",
    )


def test_postcommit_intent_cleanup_failure_does_not_report_transaction_failure(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="intent-cleanup")
    real_unlink = Path.unlink

    def fail_intent_cleanup(path, *args, **kwargs):
        if path.name.startswith("export-intent."):
            raise PermissionError(13, "cleanup held", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_intent_cleanup)
    with opened.transaction(tool="successful-despite-cleanup") as tx:
        task = tx.get("task", "core-001")
        task["title"] = "Committed once"
        tx.put("task", "core-001", task)

    assert tx.committed[("task", "core-001")]["title"] == "Committed once"
    assert parse_frontmatter(task_path.read_text(encoding="utf-8"))[0]["title"] == "Committed once"


_ARCHIVE_MOVE_CASES = (
    ("bug", "B-001", "bugs/B-001.md", "bugs/archive/B-001.md"),
    ("issue", "ISS-001", "issues/ISS-001.md", "issues/archive/ISS-001.md"),
    ("note", "NOTE-001", "notes/NOTE-001.md", "notes/_archive/NOTE-001.md"),
    (
        "handover",
        "2026-09-05-ship-it",
        "handovers/2026-09-05-ship-it.md",
        "handovers/_archive/2026/2026-09-05-ship-it.md",
    ),
)


@pytest.mark.parametrize(
    ("kind", "ident", "live_rel", "archived_rel"),
    _ARCHIVE_MOVE_CASES,
    ids=[case[0] for case in _ARCHIVE_MOVE_CASES],
)
def test_archive_moves_the_projection_file_and_both_rows(
    tmp_path, store_api, kind, ident, live_rel, archived_rel
):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    instance = store_api.open_store(
        backlog_path=backlog_path, session=f"archive-move-{kind}"
    )
    document = {"id": ident, "title": f"{kind} under test", "status": "open"}
    if kind == "handover":
        document.update({"date": "2026-09-05", "tldr": "Ship it"})
    with instance.transaction(tool=f"create-{kind}") as tx:
        tx.create(kind, document, body="Body text.")

    live = backlog_path.parent / live_rel
    archived = backlog_path.parent / archived_rel
    assert live.exists()

    with instance.transaction(tool=f"archive-{kind}") as tx:
        tx.archive(kind, ident)

    assert not live.exists()
    assert archived.exists()
    frontmatter, body = parse_frontmatter(archived.read_text(encoding="utf-8"))
    assert frontmatter["id"] == ident
    assert frontmatter["archived"] is True
    assert body.strip() == "Body text."

    rows = _query(
        store_api,
        backlog_path,
        "SELECT file FROM projection WHERE kind=? AND id=? ORDER BY file",
        (kind, ident),
    )
    assert [row["file"] for row in rows] == [archived_rel]
    assert not _query(
        store_api, backlog_path, "SELECT file FROM projection WHERE file=?", (live_rel,)
    )
    entity = _query(
        store_api,
        backlog_path,
        "SELECT archived FROM entities WHERE kind=? AND id=?",
        (kind, ident),
    )
    assert [row["archived"] for row in entity] == [1]
    assert not _query(
        store_api,
        backlog_path,
        "SELECT seq FROM changes WHERE op='export-fail'",
    )


def _seed_ideas(instance) -> None:
    with instance.transaction(tool="create-ideas") as tx:
        tx.create(
            "idea",
            {
                "id": "IDEA-001",
                "title": "First",
                "created": "2026-09-01T10:00:00Z",
                "status": "",
                "archived": False,
            },
            body="One.",
        )
        tx.create(
            "idea",
            {
                "id": "IDEA-002",
                "title": "Second",
                "created": "2026-09-02T11:30:00Z",
                "status": "shipped",
                "archived": False,
            },
            body="Two.",
        )


def test_ideas_index_is_rendered_from_idea_rows(tmp_path, store_api):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    instance = store_api.open_store(
        backlog_path=backlog_path, session="ideas-index-export"
    )
    _seed_ideas(instance)

    index = backlog_path.parent / "ideas" / "IDEAS.md"
    assert index.read_text(encoding="utf-8").splitlines() == [
        "# Ideas",
        "",
        "- 2026-09-02 11:30 — [IDEA-002](IDEA-002.md) — Second _(shipped)_",
        "- 2026-09-01 10:00 — [IDEA-001](IDEA-001.md) — First",
    ]

    rows = _query(
        store_api,
        backlog_path,
        "SELECT kind,id FROM projection WHERE file='ideas/IDEAS.md'",
    )
    assert [(row["kind"], row["id"]) for row in rows] == [("ideas-index", None)]
    assert not _query(
        store_api, backlog_path, "SELECT id FROM entities WHERE kind='ideas-index'"
    )

    with instance.transaction(tool="archive-idea") as tx:
        tx.archive("idea", "IDEA-001")

    assert index.read_text(encoding="utf-8").splitlines()[3] == (
        "- 2026-09-01 10:00 — [IDEA-001](IDEA-001.md) — ~~First~~ _(archived)_"
    )


def test_hand_edited_ideas_index_is_never_parsed_back_into_a_row(tmp_path, store_api):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    instance = store_api.open_store(
        backlog_path=backlog_path, session="ideas-index-scan"
    )
    _seed_ideas(instance)

    index = backlog_path.parent / "ideas" / "IDEAS.md"
    hand_edit = "# Ideas\n\nhand written, not frontmatter\n"
    index.write_text(hand_edit, encoding="utf-8")

    with instance.transaction(tool="scan-after-hand-edit"):
        pass

    assert index.read_text(encoding="utf-8") == hand_edit
    assert not _query(
        store_api, backlog_path, "SELECT id FROM entities WHERE kind='ideas-index'"
    )
    rows = _query(
        store_api,
        backlog_path,
        "SELECT content_hash,dirty,quarantined FROM projection "
        "WHERE file='ideas/IDEAS.md'",
    )
    assert len(rows) == 1
    assert rows[0]["dirty"] == 0
    assert rows[0]["quarantined"] == 0
    assert rows[0]["content_hash"] == hashlib.sha1(index.read_bytes()).hexdigest()
    assert not [
        warning
        for warning in _query(
            store_api,
            backlog_path,
            "SELECT id FROM changes WHERE op='export-fail'",
        )
    ]
