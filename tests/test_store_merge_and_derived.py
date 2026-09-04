"""Merge, archive-move, derived-row, and export-rollback contracts."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster import store
from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter


@pytest.fixture()
def project(tmp_path):
    store.reset_for_tests()
    tm = tmp_path / "repo" / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    (tm / "epics").mkdir()
    backlog = tm / "backlog.yaml"
    backlog.write_text(
        yaml.safe_dump(
            {
                "version": 4,
                "meta": {"schema_version": 4},
                "epics": [{"id": "core", "name": "Core", "status": "in-progress"}],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task_path = tm / "tasks" / "core-001.md"
    task_path.write_text(
        render_frontmatter(
            {
                "id": "core-001",
                "title": "Base title",
                "status": "todo",
                "epic": "core",
                "order": 1.0,
            },
            "Base body",
        ),
        encoding="utf-8",
    )
    epic_path = tm / "epics" / "core.md"
    epic_path.write_text(
        render_frontmatter(
            {"id": "core", "title": "Core", "description": "Base description"},
            "Base epic body",
        ),
        encoding="utf-8",
    )
    opened = store.open_store(backlog_path=backlog, session="merge-tests")
    yield opened, backlog, task_path, epic_path
    store.reset_for_tests()


def _row(opened, kind, ident):
    with sqlite3.connect(opened.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM entities WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()


def test_dirty_external_edit_three_way_merges_db_fields_and_foreign_body(
    project, monkeypatch
):
    opened, _backlog, task_path, _epic_path = project
    real_replace = os.replace

    def fail_task_export(source, destination):
        if Path(destination) == task_path:
            raise PermissionError(13, "held open", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_task_export)
    with opened.transaction(tool="db-title") as tx:
        task = tx.get("task", "core-001")
        task["title"] = "DB title"
        tx.put("task", "core-001", task)
    monkeypatch.setattr(os, "replace", real_replace)

    disk_fm, _ = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    task_path.write_text(
        render_frontmatter(disk_fm, "External body"), encoding="utf-8"
    )
    with opened.transaction(tool="merge-dirty"):
        pass

    merged_fm, merged_body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert merged_fm["title"] == "DB title"
    assert merged_body.removesuffix("\n") == "External body"
    row = _row(opened, "task", "core-001")
    assert json.loads(row["doc"])["title"] == "DB title"
    assert row["body"] == "External body"
    with sqlite3.connect(opened.db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM changes WHERE kind='task' AND id='core-001' AND op='merge'"
        ).fetchone()


def test_dirty_merge_history_preserves_foreign_conflicts_and_independent_changes(
    project, monkeypatch
):
    opened, _backlog, task_path, _epic_path = project
    real_replace = os.replace

    def fail_task_export(source, destination):
        if Path(destination) == task_path:
            raise PermissionError(13, "held open", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_task_export)
    with opened.transaction(tool="db-conflicts") as tx:
        task = tx.get("task", "core-001")
        task["title"] = "DB title"
        task["_body"] = "DB body"
        tx.put("task", "core-001", task)
    monkeypatch.setattr(os, "replace", real_replace)

    disk_fm, _ = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    disk_fm["title"] = "External title"
    disk_fm["status"] = "done"
    task_path.write_text(render_frontmatter(disk_fm, "External body"), encoding="utf-8")
    with opened.transaction(tool="mixed-merge"):
        pass

    with sqlite3.connect(opened.db_path) as connection:
        fields, before = connection.execute(
            "SELECT fields,before FROM changes WHERE tool='mixed-merge' AND op='merge'"
        ).fetchone()
    assert "status" in json.loads(fields)
    conflicts = json.loads(before)["_conflicts"]
    assert conflicts["title"]["theirs"]["value"] == "External title"
    assert conflicts["_body"]["theirs"]["value"] == "External body"


def test_dirty_backlog_conflict_is_recorded_even_when_database_wins(
    project, monkeypatch
):
    opened, backlog, _task_path, _epic_path = project
    real_replace = os.replace

    def fail_backlog_export(source, destination):
        if Path(destination) == backlog:
            raise PermissionError(13, "held open", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_backlog_export)
    with opened.transaction(tool="db-backlog-conflict") as tx:
        doc = tx.get("backlog", "__backlog__")
        doc["project"] = "Database"
        tx.put("backlog", "__backlog__", doc)
    monkeypatch.setattr(os, "replace", real_replace)
    raw = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    raw["project"] = "External"
    backlog.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with opened.transaction(tool="backlog-conflict-drain"):
        pass

    with sqlite3.connect(opened.db_path) as connection:
        before = connection.execute(
            "SELECT before FROM changes WHERE tool='backlog-conflict-drain' "
            "AND kind='backlog' AND op='merge'"
        ).fetchone()
    assert before is not None
    assert json.loads(before[0])["_conflicts"]["project"]["theirs"]["value"] == "External"


def test_external_epic_sidecar_edit_preserves_slim_backlog_fields(project):
    opened, _backlog, _task_path, epic_path = project
    epic_path.write_text(
        render_frontmatter(
            {"id": "core", "title": "Core", "description": "Edited description"},
            "Edited epic body",
        ),
        encoding="utf-8",
    )

    with opened.transaction(tool="epic-sidecar-edit"):
        pass

    epic = opened.transaction(tool="read-epic")
    with epic as tx:
        current = tx.get("epic", "core")
    assert current["name"] == "Core"
    assert current["status"] == "in-progress"
    assert current["description"] == "Edited description"
    assert current["_body"] == "Edited epic body"


def test_external_slim_backlog_edit_preserves_epic_sidecar_fields(project):
    opened, backlog, _task_path, _epic_path = project
    raw = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    raw["epics"][0]["name"] = "Renamed Core"
    backlog.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with opened.transaction(tool="slim-backlog-edit") as tx:
        pass

    epic = tx.get("epic", "core")
    assert epic["name"] == "Renamed Core"
    assert epic["description"] == "Base description"
    assert epic["_body"] == "Base epic body"


def test_dirty_backlog_three_way_merge_keeps_db_and_external_fields(
    project, monkeypatch
):
    opened, backlog, _task_path, _epic_path = project
    real_replace = os.replace

    def fail_backlog_export(source, destination):
        if Path(destination) == backlog:
            raise PermissionError(13, "held open", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_backlog_export)
    with opened.transaction(tool="db-backlog-field") as tx:
        doc = tx.get("backlog", "__backlog__")
        doc["version"] = 5
        tx.put("backlog", "__backlog__", doc)
    monkeypatch.setattr(os, "replace", real_replace)

    raw = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    raw["project"] = "External project"
    backlog.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with opened.transaction(tool="merge-dirty-backlog"):
        pass

    merged = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    assert merged["version"] == 5
    assert merged["project"] == "External project"
    backlog_row = json.loads(_row(opened, "backlog", "__backlog__")["doc"])
    assert backlog_row["version"] == 5
    assert backlog_row["project"] == "External project"


def test_external_archive_move_is_one_row_and_one_projection(project):
    opened, _backlog, task_path, _epic_path = project
    archive = task_path.parent / "archive" / task_path.name
    archive.parent.mkdir()
    task_path.replace(archive)

    with opened.transaction(tool="external-archive-move"):
        pass

    assert not task_path.exists()
    assert archive.exists()
    row = _row(opened, "task", "core-001")
    assert row["archived"] == 1
    with sqlite3.connect(opened.db_path) as connection:
        projections = connection.execute(
            "SELECT file FROM projection WHERE kind='task' AND id='core-001'"
        ).fetchall()
    assert projections == [("tasks/archive/core-001.md",)]


def test_handover_archive_moves_to_year_directory_and_stays_there_on_update(project):
    opened, _backlog, _task_path, _epic_path = project
    ident = "2026-09-04-store-handoff"
    with opened.transaction(tool="create-handover") as tx:
        tx.create(
            "handover",
            {"id": ident, "title": "Store handoff", "task_ids": ["core-001"]},
            requested_id=ident,
        )
    live = opened.backlog_path / "handovers" / f"{ident}.md"
    archived = opened.backlog_path / "handovers" / "_archive" / "2026" / f"{ident}.md"

    with opened.transaction(tool="archive-handover") as tx:
        tx.archive("handover", ident)
    assert not live.exists()
    assert archived.exists()

    with opened.transaction(tool="update-archived-handover") as tx:
        handover = tx.get("handover", ident)
        handover["title"] = "Updated archived handoff"
        tx.put("handover", ident, handover)
    assert not live.exists()
    assert parse_frontmatter(archived.read_text(encoding="utf-8"))[0]["title"] == "Updated archived handoff"


def test_updating_cross_kind_same_id_task_keeps_handover_task_edge(project):
    opened, _backlog, _task_path, _epic_path = project
    ident = "shared-001"
    with opened.transaction(tool="cross-kind-create") as tx:
        tx.create(
            "task",
            {"id": ident, "title": "Shared task", "epic": "core", "status": "todo"},
            requested_id=ident,
        )
        tx.create(
            "handover",
            {"id": ident, "title": "Shared handover", "task_ids": [ident]},
            requested_id=ident,
        )
    with opened.transaction(tool="cross-kind-task-update") as tx:
        task = tx.get("task", ident)
        task["title"] = "Updated task"
        tx.put("task", ident, task)

    with sqlite3.connect(opened.db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM handover_tasks WHERE handover_id=? AND task_id=?",
            (ident, ident),
        ).fetchone()


def test_derived_rows_refresh_in_same_transaction(project):
    opened, _backlog, _task_path, _epic_path = project
    with opened.transaction(tool="derived-update") as tx:
        task = tx.get("task", "core-001")
        task["description"] = "Searchable quasar phrase"
        task["anchors"] = ["src/store.py"]
        task["links"] = [{"type": "depends_on", "target": "core-002"}]
        tx.put("task", "core-001", task)

    with sqlite3.connect(opened.db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM entity_fts WHERE entity_fts MATCH 'quasar' AND id='core-001'"
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM entity_paths WHERE kind='task' AND id='core-001' "
            "AND path='src/store.py' AND source='anchors'"
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM links WHERE src_kind='task' AND src_id='core-001' "
            "AND type='depends_on' AND dst_id='core-002'"
        ).fetchone()


def test_rebuild_derived_never_scans_or_mutates_authoritative_tables(project):
    opened, _backlog, task_path, _epic_path = project
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("Base title", "External title"),
        encoding="utf-8",
    )
    with sqlite3.connect(opened.db_path) as connection:
        before = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("entities", "changes", "projection")
        }

    opened.rebuild_derived()

    with sqlite3.connect(opened.db_path) as connection:
        after = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("entities", "changes", "projection")
        }
    assert after == before


def test_late_export_exception_restores_already_replaced_files(project, monkeypatch):
    opened, _backlog, task_path, epic_path = project
    task_before = task_path.read_bytes()
    epic_before = epic_path.read_bytes()
    real_export = opened._replace_projection
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fault after first replacement")
        return real_export(*args, **kwargs)

    monkeypatch.setattr(opened, "_replace_projection", fail_second)
    with pytest.raises(RuntimeError, match="fault after first replacement"):
        with opened.transaction(tool="late-export-fault") as tx:
            epic = tx.get("epic", "core")
            epic["description"] = "DB epic change"
            tx.put("epic", "core", epic)
            task = tx.get("task", "core-001")
            task["title"] = "DB task change"
            tx.put("task", "core-001", task)

    assert task_path.read_bytes() == task_before
    assert epic_path.read_bytes() == epic_before
    assert json.loads(_row(opened, "task", "core-001")["doc"])["title"] == "Base title"
    assert json.loads(_row(opened, "epic", "core")["doc"])["description"] == "Base description"


def test_archive_move_is_restored_when_later_export_step_rolls_back(
    project, monkeypatch
):
    opened, _backlog, task_path, _epic_path = project
    original = task_path.read_bytes()
    archive_path = task_path.parent / "archive" / task_path.name
    real_replace = opened._replace_projection

    def fail_archive(tx, rel, kind, ident, content, exported_seq):
        if kind == "task" and rel.startswith("tasks/archive/"):
            raise RuntimeError("fault after old projection removal")
        return real_replace(tx, rel, kind, ident, content, exported_seq)

    monkeypatch.setattr(opened, "_replace_projection", fail_archive)
    with pytest.raises(RuntimeError, match="fault after old projection removal"):
        with opened.transaction(tool="archive-fault") as tx:
            tx.archive("task", "core-001")

    assert task_path.read_bytes() == original
    assert not archive_path.exists()
    row = opened.connection.execute(
        "SELECT archived FROM entities WHERE kind='task' AND id='core-001'"
    ).fetchone()
    assert row["archived"] == 0


def test_context_is_derived_in_memory_and_progress_is_throttled(project, monkeypatch):
    opened, _backlog, _task_path, _epic_path = project
    rendered = []
    clock = iter((100.0, 101.0, 106.0, 107.0))
    monkeypatch.setattr(store.time, "monotonic", lambda: next(clock))

    def context_builder(data):
        data["context"] = {"task_count": sum(len(e["tasks"]) for e in data["epics"])}

    def progress_renderer(data, existing):
        rendered.append((data["context"]["task_count"], existing))
        return f"tasks={data['context']['task_count']}\n"

    store.configure_derivers(
        context_builder=context_builder, progress_renderer=progress_renderer
    )
    for index in range(3):
        with opened.transaction(tool=f"progress-{index}") as tx:
            task = tx.get("task", "core-001")
            task["status"] = f"state-{index}"
            tx.put("task", "core-001", task)

    assert store.load_dict(opened.backlog_path)["context"] == {"task_count": 1}
    assert rendered == [(1, ""), (1, "tasks=1\n")]
    assert (opened.db_path.parent / "PROGRESS.md").read_text(encoding="utf-8") == "tasks=1\n"
    persisted = "\n".join(
        row[0]
        for row in sqlite3.connect(opened.db_path).execute("SELECT doc FROM entities")
    )
    assert '"context"' not in persisted
