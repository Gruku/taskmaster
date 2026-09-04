"""Inventory coverage for projection bootstrap and subsequent import scans."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter


def _make_repo(tmp_path: Path, backlog: dict | None = None) -> Path:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    tm_dir.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n", encoding="utf-8"
    )
    payload = backlog or {
        "version": 4,
        "meta": {"project": "inventory-tests", "schema_version": 4},
        "epics": [],
        "phases": [],
    }
    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return backlog_path


def _write_markdown(
    tm_dir: Path,
    relative_path: str,
    ident: str,
    *,
    marker: str,
    body: str | None = None,
    extra: dict | None = None,
) -> Path:
    path = tm_dir / Path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {"id": ident, "title": marker, "status": "open"}
    frontmatter.update(extra or {})
    path.write_text(
        render_frontmatter(frontmatter, body or f"{marker} body"),
        encoding="utf-8",
    )
    return path


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


def test_v3_inline_tasks_are_immediately_projected_as_equivalent_v4(
    tmp_path, store_api
):
    backlog_path = _make_repo(
        tmp_path,
        {
            "version": 3,
            "project": "legacy-inline",
            "meta": {"schema_version": 3},
            "context": {"focus": "must become derived"},
            "epics": [
                {
                    "id": "core",
                    "name": "Core",
                    "status": "in-progress",
                    "tasks": [
                        {
                            "id": "core-001",
                            "title": "Inline → projected",
                            "status": "todo",
                            "notes": ["preserve this inline heavy field"],
                        }
                    ],
                }
            ],
            "phases": [],
        },
    )
    task_path = backlog_path.parent / "tasks" / "core-001.md"
    assert not task_path.exists()

    store = store_api.open_store(backlog_path=backlog_path, session="v3-migrate")

    persisted = yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    assert persisted["meta"]["schema_version"] == 4
    assert persisted["meta"]["projection_schema"] == store_api.PROJECTION_SCHEMA
    assert "context" not in persisted
    assert all("tasks" not in epic for epic in persisted["epics"])

    task_fm, task_body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    assert task_fm == {
        "id": "core-001",
        "title": "Inline → projected",
        "status": "todo",
        "notes": ["preserve this inline heavy field"],
        "epic": "core",
        "order": 1.0,
    }
    assert task_body == ""

    loaded_task = store.load_dict()["epics"][0]["tasks"][0]
    assert loaded_task == task_fm


def test_bootstrap_inventory_imports_every_file_backed_kind_and_archive_glob(
    tmp_path, store_api
):
    backlog_path = _make_repo(
        tmp_path,
        {
            "version": 4,
            "meta": {"project": "full-inventory", "schema_version": 4},
            "epics": [
                {
                    "id": "core",
                    "name": "Core",
                    "status": "in-progress",
                    "phase": "build",
                }
            ],
            "phases": [
                {"id": "build", "name": "Build", "status": "in-progress"}
            ],
        },
    )
    tm_dir = backlog_path.parent
    _write_markdown(
        tm_dir,
        "tasks/core-001.md",
        "core-001",
        marker="task marker",
        extra={"epic": "core", "order": 1.0},
    )
    _write_markdown(
        tm_dir,
        "epics/core.md",
        "core",
        marker="epic mirror",
        body="epic body",
        extra={"description": "epic marker"},
    )
    _write_markdown(
        tm_dir,
        "phases/build.md",
        "build",
        marker="phase mirror",
        body="phase body",
        extra={"description": "phase marker"},
    )

    standalone = [
        ("bug", "bugs/B-001.md", "B-001", "bug marker", False),
        (
            "issue",
            "issues/archive/ISS-001.md",
            "ISS-001",
            "archived issue marker",
            True,
        ),
        (
            "handover",
            "handovers/_archive/2026/2026-09-01-store.md",
            "2026-09-01-store",
            "nested handover marker",
            True,
        ),
        (
            "decision",
            "decisions/DEC-001.md",
            "DEC-001",
            "decision marker",
            False,
        ),
        ("idea", "ideas/IDEA-001.md", "IDEA-001", "idea marker", False),
        ("note", "notes/NOTE-001.md", "NOTE-001", "note marker", False),
        (
            "note",
            "notes/_archive/NOTE-002.md",
            "NOTE-002",
            "archived note marker",
            True,
        ),
        ("area", "areas/platform.md", "platform", "area marker", False),
        (
            "tracker",
            "trackers/jira-main.md",
            "jira-main",
            "canonical tracker marker",
            False,
        ),
        (
            "tracker",
            "integrations/trackers/jira-legacy.md",
            "jira-legacy",
            "legacy tracker marker",
            False,
        ),
    ]
    for _kind, rel, ident, marker, _archived in standalone:
        _write_markdown(tm_dir, rel, ident, marker=marker)

    store_api.open_store(backlog_path=backlog_path, session="inventory-bootstrap")

    rows = _query(
        store_api,
        backlog_path,
        "SELECT kind,id,doc,body,archived,deleted FROM entities",
    )
    by_key = {(row["kind"], row["id"]): row for row in rows}
    assert {row["kind"] for row in rows} == {
        "backlog",
        "task",
        "epic",
        "phase",
        "bug",
        "issue",
        "handover",
        "decision",
        "idea",
        "note",
        "area",
        "tracker",
    }
    assert json.loads(by_key[("task", "core-001")]["doc"])["title"] == (
        "task marker"
    )
    assert json.loads(by_key[("epic", "core")]["doc"])["description"] == (
        "epic marker"
    )
    assert by_key[("epic", "core")]["body"] == "epic body"
    assert json.loads(by_key[("phase", "build")]["doc"])["description"] == (
        "phase marker"
    )
    assert by_key[("phase", "build")]["body"] == "phase body"
    for kind, _rel, ident, marker, archived in standalone:
        row = by_key[(kind, ident)]
        assert json.loads(row["doc"])["title"] == marker
        assert row["body"] == f"{marker} body"
        assert row["archived"] == int(archived)
        assert row["deleted"] == 0

    projection_files = {
        row["file"]
        for row in _query(store_api, backlog_path, "SELECT file FROM projection")
    }
    assert "trackers/jira-main.md" in projection_files
    assert "integrations/trackers/jira-legacy.md" in projection_files
    assert "handovers/_archive/2026/2026-09-01-store.md" in projection_files
    assert "issues/archive/ISS-001.md" in projection_files
    assert "notes/_archive/NOTE-002.md" in projection_files


def test_later_scan_discovers_an_unknown_valid_file(tmp_path, store_api):
    backlog_path = _make_repo(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="later-discovery")
    _write_markdown(
        backlog_path.parent,
        "areas/on-call.md",
        "on-call",
        marker="created after bootstrap",
    )

    with store.transaction(tool="discover-later"):
        pass

    row = _query(
        store_api,
        backlog_path,
        "SELECT doc,deleted FROM entities WHERE kind='area' AND id='on-call'",
    )[0]
    assert json.loads(row["doc"])["title"] == "created after bootstrap"
    assert row["deleted"] == 0
    imported = _query(
        store_api,
        backlog_path,
        "SELECT session,tool,op FROM changes "
        "WHERE kind='area' AND id='on-call' ORDER BY seq DESC LIMIT 1",
    )[0]
    assert (imported["session"], imported["tool"], imported["op"]) == (
        "<external>",
        "discover-later",
        "import",
    )


def test_missing_later_imported_file_is_reexported_not_deleted(tmp_path, store_api):
    backlog_path = _make_repo(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="missing-file")
    area_path = _write_markdown(
        backlog_path.parent,
        "areas/on-call.md",
        "on-call",
        marker="must survive deletion",
        body="keep this body",
    )
    with store.transaction(tool="import-unknown"):
        pass

    area_path.unlink()
    with store.transaction(tool="restore-missing"):
        pass

    assert area_path.is_file()
    frontmatter, body = parse_frontmatter(area_path.read_text(encoding="utf-8"))
    assert frontmatter["id"] == "on-call"
    assert frontmatter["title"] == "must survive deletion"
    assert body.removesuffix("\n") == "keep this body"
    row = _query(
        store_api,
        backlog_path,
        "SELECT deleted,doc,body FROM entities WHERE kind='area' AND id='on-call'",
    )[0]
    assert row["deleted"] == 0
    assert json.loads(row["doc"])["title"] == "must survive deletion"
    assert row["body"] == "keep this body"
    assert not _query(
        store_api,
        backlog_path,
        "SELECT 1 FROM changes WHERE kind='area' AND id='on-call' AND op='delete'",
    )


def test_project_yaml_is_a_plain_yaml_row_and_is_always_hash_scanned(
    tmp_path, store_api
):
    backlog_path = _make_repo(tmp_path)
    project_path = backlog_path.parent / "project.yaml"
    initial = b"name: Inventory project\nowner: alpha\n"
    project_path.write_bytes(initial)
    store = store_api.open_store(backlog_path=backlog_path, session="project-row")

    initial_row = _query(
        store_api,
        backlog_path,
        "SELECT id,doc FROM entities WHERE kind='project'",
    )
    assert len(initial_row) == 1
    assert json.loads(initial_row[0]["doc"])["owner"] == "alpha"

    original_stat = project_path.stat()
    edited = initial.replace(b"alpha", b"bravo")
    assert len(edited) == len(initial)
    project_path.write_bytes(edited)
    os.utime(
        project_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    assert (project_path.stat().st_mtime_ns, project_path.stat().st_size) == (
        original_stat.st_mtime_ns,
        original_stat.st_size,
    )

    with store.transaction(tool="project-hash-scan"):
        pass

    row = _query(
        store_api,
        backlog_path,
        "SELECT doc FROM entities WHERE kind='project'",
    )[0]
    assert json.loads(row["doc"])["owner"] == "bravo"
    projection = _query(
        store_api,
        backlog_path,
        "SELECT quarantined FROM projection WHERE file='project.yaml'",
    )[0]
    assert projection["quarantined"] == 0


def test_later_inventory_ignores_dot_temp_lock_and_corrupt_files(
    tmp_path, store_api
):
    backlog_path = _make_repo(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="ignored-files")
    ignored = (
        ("bugs/.B-900.md", "B-900"),
        ("bugs/B-901.md.tmp.writer", "B-901"),
        ("bugs/B-902.md.lock", "B-902"),
        ("bugs/B-903.md.corrupt-20260904", "B-903"),
    )
    for rel, ident in ignored:
        _write_markdown(
            backlog_path.parent,
            rel,
            ident,
            marker=f"ignored {ident}",
        )

    with store.transaction(tool="ignored-inventory"):
        pass

    assert not _query(
        store_api,
        backlog_path,
        "SELECT id FROM entities WHERE kind='bug'",
    )
    assert not _query(
        store_api,
        backlog_path,
        "SELECT file FROM projection WHERE file LIKE 'bugs/%'",
    )
