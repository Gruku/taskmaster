"""Contract tests for importing the git-facing projection into the SQLite store."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter


def _write_v4_projection(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n", encoding="utf-8"
    )

    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "store-tests", "schema_version": 4},
                "context": {"focus": "derived-only"},
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
            "## Notes\n\nImported → intact (yes).",
        ),
        encoding="utf-8",
    )
    return backlog_path, task_path


@pytest.fixture()
def store_api():
    # Import in the fixture so the initial TDD failure clearly identifies the
    # missing store module/API rather than preventing collection of this file.
    from taskmaster import store

    store.reset_for_tests()
    yield store
    store.reset_for_tests()


def _query(store_api, backlog_path: Path, sql: str, params: tuple = ()) -> list:
    with sqlite3.connect(store_api.db_path(backlog_path)) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(sql, params))


def test_unknown_file_with_mismatched_frontmatter_id_is_quarantined(
    tmp_path, store_api
):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    mismatch = backlog_path.parent / "tasks" / "core-002.md"
    mismatch.write_text(
        render_frontmatter(
            {
                "id": "core-999",
                "title": "Mismatched",
                "status": "todo",
                "epic": "core",
                "order": 2.0,
            },
            "Mismatch body",
        ),
        encoding="utf-8",
    )

    opened = store_api.open_store(backlog_path=backlog_path, session="id-mismatch")

    assert all(
        task["id"] != "core-999"
        for epic in opened.load_dict()["epics"]
        for task in epic.get("tasks", [])
    )
    row = _query(
        store_api,
        backlog_path,
        "SELECT id,quarantined FROM projection WHERE file='tasks/core-002.md'",
    )[0]
    assert (row["id"], row["quarantined"]) == ("core-002", 1)


def test_v4_bootstrap_imports_backlog_epic_phase_and_task(tmp_path, store_api):
    backlog_path, _ = _write_v4_projection(tmp_path)

    store_api.open_store(backlog_path=backlog_path, session="bootstrap-test")

    rows = _query(
        store_api,
        backlog_path,
        "SELECT kind, id, doc, body, archived, deleted FROM entities",
    )
    by_key = {(row["kind"], row["id"]): row for row in rows}

    assert sum(row["kind"] == "backlog" for row in rows) == 1
    assert ("epic", "core") in by_key
    assert ("phase", "build") in by_key
    assert ("task", "core-001") in by_key
    assert json.loads(by_key[("epic", "core")]["doc"])["name"] == "Store core"
    assert json.loads(by_key[("phase", "build")]["doc"])["name"] == "Build"
    assert json.loads(by_key[("task", "core-001")]["doc"])["title"] == "Alpha"
    assert by_key[("task", "core-001")]["body"] == (
        "## Notes\n\nImported → intact (yes)."
    )
    assert by_key[("task", "core-001")]["archived"] == 0
    assert by_key[("task", "core-001")]["deleted"] == 0

    # Runtime-only context is reconstructed for callers but is never persisted.
    persisted_docs = "\n".join(row["doc"] for row in rows)
    assert '"context"' not in persisted_docs
    loaded = store_api.load_dict(backlog_path=backlog_path)
    assert loaded["epics"][0]["tasks"][0]["id"] == "core-001"


def test_same_stat_external_edit_is_imported_after_git_generation_change(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="hash-test")
    original_stat = task_path.stat()

    frontmatter, body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
    frontmatter["title"] = "Bravo"  # same byte length as "Alpha"
    edited = render_frontmatter(frontmatter, body.removesuffix("\n"))
    # Preserve the file's existing newline convention.  ``Path.write_text``
    # expands LF to CRLF on Windows, while ``str.encode`` alone does not.
    original_bytes = task_path.read_bytes()
    edited_bytes = edited.encode("utf-8")
    if b"\r\n" in original_bytes:
        edited_bytes = edited_bytes.replace(b"\n", b"\r\n")
    assert len(edited_bytes) == original_stat.st_size
    task_path.write_bytes(edited_bytes)
    os.utime(
        task_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    assert (task_path.stat().st_mtime_ns, task_path.stat().st_size) == (
        original_stat.st_mtime_ns,
        original_stat.st_size,
    )

    # A changed git generation requires a full content-hash pass, including a
    # same-size/same-mtime task edit (the NTFS checkout edge case in spec 3.7).
    head = backlog_path.parents[1] / ".git" / "HEAD"
    head_stat = head.stat()
    head.write_text("ref: refs/heads/next\n", encoding="utf-8")
    os.utime(head, ns=(head_stat.st_atime_ns, head_stat.st_mtime_ns + 1_000_000_000))

    with store.transaction(tool="hash-scan"):
        pass

    task_row = _query(
        store_api,
        backlog_path,
        "SELECT doc FROM entities WHERE kind='task' AND id='core-001'",
    )[0]
    assert json.loads(task_row["doc"])["title"] == "Bravo"
    projection = _query(
        store_api,
        backlog_path,
        "SELECT content_hash, dirty, quarantined FROM projection WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert projection["content_hash"] == hashlib.sha1(task_path.read_bytes()).hexdigest()
    assert (projection["dirty"], projection["quarantined"]) == (0, 0)
    imports = _query(
        store_api,
        backlog_path,
        "SELECT session, tool, op, fields FROM changes "
        "WHERE kind='task' AND id='core-001' AND op='import' ORDER BY seq DESC",
    )
    assert imports
    assert imports[0]["session"] == "<external>"
    assert "title" in json.loads(imports[0]["fields"])


@pytest.mark.parametrize(
    "broken_bytes",
    [
        b"---\nid: [unterminated\n---\nbody\n",
        (
            b"---\nid: core-001\ntitle: Alpha\n"
            b"<<<<<<< HEAD\nstatus: todo\n=======\nstatus: done\n>>>>>>> branch\n"
            b"epic: core\norder: 1.0\n---\nbody\n"
        ),
    ],
    ids=["invalid-frontmatter", "git-conflict"],
)
def test_invalid_or_conflicted_file_is_quarantined_without_overwrite(
    tmp_path, store_api, broken_bytes
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    store = store_api.open_store(backlog_path=backlog_path, session="quarantine-test")
    task_path.write_bytes(broken_bytes)

    with store.transaction(tool="quarantine-scan") as tx:
        pass

    row = _query(
        store_api,
        backlog_path,
        "SELECT doc FROM entities WHERE kind='task' AND id='core-001'",
    )[0]
    projection = _query(
        store_api,
        backlog_path,
        "SELECT dirty, quarantined FROM projection WHERE file=?",
        ("tasks/core-001.md",),
    )[0]
    assert json.loads(row["doc"])["title"] == "Alpha"
    assert projection["quarantined"] == 1
    assert projection["dirty"] == 0
    assert task_path.read_bytes() == broken_bytes
    assert "tasks/core-001.md" in store_api.status(
        backlog_path=backlog_path
    ).quarantined_files
    assert any("quarantin" in warning.lower() for warning in tx.warnings)


def test_load_dict_imports_external_edit_after_two_second_stat_cache(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="read-scan")

    clock = iter((100.0, 101.0, 103.0))
    monkeypatch.setattr(store_api.time, "monotonic", lambda: next(clock))
    assert store_api.load_dict(backlog_path)["epics"][0]["tasks"][0]["title"] == "Alpha"

    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("title: Alpha", "title: External"),
        encoding="utf-8",
    )
    assert store_api.load_dict(backlog_path)["epics"][0]["tasks"][0]["title"] == "Alpha"
    assert store_api.load_dict(backlog_path)["epics"][0]["tasks"][0]["title"] == "External"

    change = opened.connection.execute(
        "SELECT op,session FROM changes WHERE kind='task' AND id='core-001' "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    assert tuple(change) == ("import", "<external>")


def test_scan_hashes_only_stat_candidates_unless_git_generation_changes(
    tmp_path, store_api, monkeypatch
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="scan-candidates")
    with opened.transaction(tool="prime-scan-generation"):
        pass
    real_snapshot = store_api._read_file_snapshot
    task_reads = 0

    def counting_snapshot(path):
        nonlocal task_reads
        if path == task_path:
            task_reads += 1
        return real_snapshot(path)

    monkeypatch.setattr(store_api, "_read_file_snapshot", counting_snapshot)
    with opened.transaction(tool="unchanged-scan"):
        pass
    assert task_reads == 0

    git_head = backlog_path.parent.parent / ".git" / "HEAD"
    git_head.write_text("ref: refs/heads/other\n", encoding="utf-8")
    with opened.transaction(tool="generation-scan"):
        pass
    assert task_reads == 1


def test_quarantine_clears_after_repair_and_deleted_file_is_reexported(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine-repair")
    valid = task_path.read_text(encoding="utf-8")
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="quarantine-broken"):
        pass
    assert "tasks/core-001.md" in opened.status().quarantined_files

    repaired = valid.replace("title: Alpha", "title: Repaired")
    task_path.write_text(repaired, encoding="utf-8")
    with opened.transaction(tool="quarantine-repaired"):
        pass
    assert opened.status().quarantined_files == ()
    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Repaired"

    task_path.write_text("---\nid: [broken-again\n---\n", encoding="utf-8")
    with opened.transaction(tool="quarantine-again"):
        pass
    task_path.unlink()
    with opened.transaction(tool="quarantine-removed"):
        pass
    assert opened.status().quarantined_files == ()
    assert "title: Repaired" in task_path.read_text(encoding="utf-8")


def test_malformed_backlog_does_not_bypass_existing_store_quarantine(
    tmp_path, store_api
):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    store_api.open_store(backlog_path=backlog_path, session="valid-bootstrap")
    store_api.reset_for_tests()
    backlog_file = backlog_path
    malformed = "version: [unterminated\n"
    backlog_file.write_text(malformed, encoding="utf-8")

    reopened = store_api.open_store(backlog_path=backlog_path, session="broken-open")
    loaded = reopened.load_dict()

    assert loaded["epics"][0]["tasks"][0]["title"] == "Alpha"
    assert "backlog.yaml" in reopened.status().quarantined_files
    assert backlog_file.read_text(encoding="utf-8") == malformed


@pytest.mark.parametrize(
    "meta_yaml",
    ["[bad]", "{schema_version: 4, projection_schema: abc}", "{schema_version: 4, projection_schema: 5.9}"],
)
def test_semantically_invalid_backlog_header_is_quarantined_and_db_is_preserved(
    tmp_path, store_api, meta_yaml
):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="semantic-valid")
    backlog_path.write_text(
        f"version: 4\nproject: poisoned\nmeta: {meta_yaml}\nepics: []\nphases: []\n",
        encoding="utf-8",
    )

    with opened.transaction(tool="semantic-invalid-scan"):
        pass

    assert opened.load_dict()["meta"]["project"] == "store-tests"
    assert "backlog.yaml" in opened.status().quarantined_files


def test_exact_byte_repair_clears_quarantine_before_later_db_export(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="exact-repair")
    original = task_path.read_bytes()
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    with opened.transaction(tool="break-projection"):
        pass
    task_path.write_bytes(original)

    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Alpha"
    assert opened.status().quarantined_files == ()
    with opened.transaction(tool="after-exact-repair") as tx:
        task = tx.get("task", "core-001")
        task["title"] = "Database after repair"
        tx.put("task", "core-001", task)
    assert parse_frontmatter(task_path.read_text(encoding="utf-8"))[0]["title"] == (
        "Database after repair"
    )


def test_v3_adoption_keeps_slim_task_fields_that_live_only_in_backlog_yaml(tmp_path):
    """A v3 project splits a task across backlog.yaml (slim) and tasks/<id>.md (heavy).

    Bootstrapping such a project must keep both halves: the heavy file is a
    partial document, not the whole task, so importing it may not replace the
    row the merged v3 load already produced.
    """
    from taskmaster import store

    store.reset_for_tests()
    tm_dir = tmp_path / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "meta": {"project": "legacy", "schema_version": 3},
                "epics": [
                    {
                        "id": "core",
                        "name": "Core",
                        "tasks": [
                            {
                                "id": "core-001",
                                "title": "Legacy task",
                                "status": "in-progress",
                                "priority": "high",
                                "branch": "feature/x",
                            }
                        ],
                    }
                ],
                "phases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tm_dir / "tasks" / "core-001.md").write_text(
        render_frontmatter(
            {"id": "core-001", "title": "Legacy task",
             "gates": {"review-gate": {"verdict": "pass"}}},
            "Heavy body.",
        ),
        encoding="utf-8",
    )

    try:
        data = store.load_dict(backlog_path)
        task = data["epics"][0]["tasks"][0]
        assert task["status"] == "in-progress"
        assert task["priority"] == "high"
        assert task["branch"] == "feature/x"
        assert task["gates"] == {"review-gate": {"verdict": "pass"}}
    finally:
        store.reset_for_tests()
