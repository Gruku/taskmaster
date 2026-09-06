"""User intent: on a Windows checkout the exporter must not turn every write
into a whole-tree rewrite.

The real-backlog verification (task 5.3) adopted a repo with `core.autocrlf=true`
and CRLF working-tree files. The exporter writes LF, so 169 files showed as
modified with zero content hunks and the other ~2,060 carried the line-ending
flip on top of their real change — making the migration commit a whole-tree
rewrite and every later `git diff` on the backlog unreadable.
"""
from __future__ import annotations

import yaml

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _seed(tmp_path):
    """An LF project. The bytes are written explicitly: `Path.write_text` on
    Windows translates `\\n` to CRLF, which would seed a CRLF project instead
    and hide what these tests are about."""
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_bytes(b"## Changelog\n")
    bp = tm / "backlog.yaml"
    bp.write_bytes(yaml.safe_dump({
        "version": 4, "project": "t",
        "meta": {"updated": "", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [{"id": "dev", "name": "Dev", "status": "active", "order": 1}],
    }, sort_keys=False).encode("utf-8"))
    store.reset_for_tests()
    return bp


def _write_crlf(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def test_a_crlf_task_file_stays_crlf_through_a_write(tmp_path, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    task_file = v3.task_file_path(bp, "e-001")
    _write_crlf(task_file, (
        "---\nid: e-001\ntitle: CRLF task\nepic: e\nstatus: todo\n"
        "priority: high\norder: 1.0\ncreated: '2026-01-01T00:00'\n---\n"
        "the body\n"
    ))
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="crlf-export")

    assert "Error" not in bs.backlog_update_task("e-001", "notes", "changed")

    written = task_file.read_bytes()
    assert b"\r\n" in written, written[:120]
    assert b"\n" not in written.replace(b"\r\n", b""), (
        "the file gained bare LF lines: it is now mixed"
    )
    assert b"changed" in written


def test_a_new_file_is_written_with_lf(tmp_path, monkeypatch):
    """Nothing on disk to match means the repository default, LF."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="lf-new-file")

    created = bs.backlog_add_task(title="Fresh", epic="e", phase="dev", tldr="t")
    assert "Error" not in created, created

    files = sorted((bp.parent / "tasks").glob("*.md"))
    assert files, "no task file was exported"
    assert b"\r\n" not in files[0].read_bytes()


def test_an_lf_file_is_not_converted(tmp_path, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    task_file = v3.task_file_path(bp, "e-001")
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_bytes((
        "---\nid: e-001\ntitle: LF task\nepic: e\nstatus: todo\n"
        "priority: high\norder: 1.0\ncreated: '2026-01-01T00:00'\n---\n"
        "the body\n"
    ).encode("utf-8"))
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="lf-export")

    assert "Error" not in bs.backlog_update_task("e-001", "notes", "changed")

    assert b"\r\n" not in task_file.read_bytes()


def test_adopting_a_crlf_projection_passes_the_round_trip_check(tmp_path):
    """Adoption verifies the bytes it is about to write, and on a Windows
    checkout those bytes are CRLF. The two have to agree or every adoption on
    Windows refuses itself."""
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    bp = tm / "backlog.yaml"
    bp.write_bytes(yaml.safe_dump({
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active",
                   "description": "heavy", "tasks": [
                       {"id": "e-001", "title": "CRLF", "status": "todo",
                        "notes": "line one\nline two"},
                   ]}],
        "phases": [], "context": {},
    }, sort_keys=False).replace("\n", "\r\n").encode("utf-8"))
    _write_crlf(v3.task_file_path(bp, "e-001"), (
        "---\nid: e-001\ntitle: CRLF\nepic: e\nstatus: todo\n---\nbody\n"
    ))
    store.reset_for_tests()

    loaded = store.open_store(backlog_path=bp, session="crlf-adoption").load_dict()

    task = loaded["epics"][0]["tasks"][0]
    assert task["notes"] == "line one\nline two", task
    assert b"\r\n" in v3.task_file_path(bp, "e-001").read_bytes()
    store.reset_for_tests()


def test_the_recorded_hash_matches_the_bytes_on_disk(tmp_path, monkeypatch):
    """The content hash has to be taken on what was written, or the next scan
    reads the file as edited out of band and re-imports it forever."""
    import hashlib  # noqa: PLC0415

    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    task_file = v3.task_file_path(bp, "e-001")
    _write_crlf(task_file, (
        "---\nid: e-001\ntitle: CRLF task\nepic: e\nstatus: todo\n"
        "priority: high\norder: 1.0\ncreated: '2026-01-01T00:00'\n---\n"
        "the body\n"
    ))
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    opened = store.open_store(backlog_path=bp, session="crlf-hash")

    assert "Error" not in bs.backlog_update_task("e-001", "notes", "changed")

    row = opened.connection.execute(
        "SELECT content_hash FROM projection WHERE file='tasks/e-001.md'"
    ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha1(task_file.read_bytes()).hexdigest()
    # …and a second write finds nothing stale to re-import.
    assert "Error" not in bs.backlog_update_task("e-001", "notes", "again")
    assert b"\r\n" in task_file.read_bytes()
    store.reset_for_tests()


def _seed_crlf(tmp_path):
    """A project whose git-facing files are all CRLF — a Windows checkout."""
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    bp = tm / "backlog.yaml"
    bp.write_bytes(yaml.safe_dump({
        "version": 4, "project": "t",
        "meta": {"updated": "", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [{"id": "dev", "name": "Dev", "status": "active", "order": 1}],
    }, sort_keys=False).replace("\n", "\r\n").encode("utf-8"))
    _write_crlf(v3.task_file_path(bp, "e-001"), (
        "---\nid: e-001\ntitle: CRLF task\nepic: e\nstatus: todo\n"
        "priority: high\norder: 1.0\ncreated: '2026-01-01T00:00'\n---\n"
        "the body\n"
    ))
    store.reset_for_tests()
    return bp


def _assert_pure_crlf(path) -> None:
    written = path.read_bytes()
    assert b"\r\n" in written, written[:120]
    assert b"\n" not in written.replace(b"\r\n", b""), (
        f"{path.name} has mixed line endings"
    )


def test_a_new_file_on_a_crlf_project_is_written_with_crlf(tmp_path, monkeypatch):
    """Adoption of a CRLF backlog creates ~90 new files. Written LF they sit
    next to 2,000 CRLF files and every later diff on them is a whole-file flip."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed_crlf(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="crlf-new-file")

    created = bs.backlog_add_task(title="Fresh", epic="e", phase="dev", tldr="t")
    assert "Error" not in created, created

    new_files = [
        path for path in sorted((bp.parent / "tasks").glob("*.md"))
        if path.name != "e-001.md"
    ]
    assert new_files, "no new task file was exported"
    _assert_pure_crlf(new_files[0])
    store.reset_for_tests()


def test_archiving_a_crlf_file_keeps_crlf_at_the_new_path(tmp_path, monkeypatch):
    """The archive move deletes the old path before the new one is written, so
    there was nothing left to probe and the moved file landed LF."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed_crlf(tmp_path)
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="crlf-archive")

    assert "Error" not in bs.backlog_archive_task("e-001", reason="deprecated")

    archived = bp.parent / "tasks" / "archive" / "e-001.md"
    assert archived.exists(), sorted((bp.parent / "tasks").rglob("*.md"))
    _assert_pure_crlf(archived)
    store.reset_for_tests()


def test_archiving_an_lf_file_keeps_lf_at_the_new_path(tmp_path, monkeypatch):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path)
    task_file = v3.task_file_path(bp, "e-001")
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_bytes((
        "---\nid: e-001\ntitle: LF task\nepic: e\nstatus: todo\n"
        "priority: high\norder: 1.0\ncreated: '2026-01-01T00:00'\n---\n"
        "the body\n"
    ).encode("utf-8"))
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="lf-archive")

    assert "Error" not in bs.backlog_archive_task("e-001", reason="deprecated")

    archived = bp.parent / "tasks" / "archive" / "e-001.md"
    assert archived.exists()
    assert b"\r\n" not in archived.read_bytes()
    store.reset_for_tests()
