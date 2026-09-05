# User intent: prove `backlog_store_status` reports the store honestly — every
# field spec §3.8 names, read straight from the authority, and never a write.
"""Tests for the `backlog_store_status` MCP tool.

The tool is the operator's only window into the store when something looks
wrong, so these assert what it *shows*: the resolved root, the schema and size
of the database, the change log, dirty and quarantined projection files, live
sessions, recovered corrupt databases, and the pending Linear push count. A
report that silently omits a field is the failure this file exists to catch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402
from taskmaster import store as _store  # noqa: E402


@pytest.fixture
def bare_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real v4 project whose store has deliberately never been opened."""
    backlog_path = tmp_path / ".taskmaster" / "backlog.yaml"
    backlog_path.parent.mkdir(parents=True)
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "status-tool", "schema_version": 4},
                "epics": [
                    {
                        "id": "ts",
                        "name": "Test",
                        "phase": "build",
                    }
                ],
                "phases": [{"id": "build", "name": "Build", "status": "active"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: backlog_path)
    return backlog_path


@pytest.fixture
def project(bare_project: Path) -> Path:
    """`bare_project` with the store adopted, as any tool call would leave it."""
    _store.open_store(bare_project).load_dict()
    return bare_project


@pytest.fixture
def network_root(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make every root look like a network share, as the store's own probe does.

    The store degrades to projection-only there: it opens the database `mode=ro`
    and, when there is no database at all, does not open one. Everything that
    reads a table has to survive that.
    """
    reason = "store.db lives on a network filesystem"
    monkeypatch.setattr(_store, "_network_filesystem_reason", lambda root: reason)
    return reason


def _report(project: Path) -> str:
    return backlog_server.backlog_store_status()


def _line(report: str, prefix: str) -> str:
    for line in report.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no {prefix!r} line in report:\n{report}")


def test_report_carries_every_field_the_spec_names(project: Path) -> None:
    report = _report(project)
    for prefix in (
        "Store:",
        "Root:",
        "Size:",
        "Dirty:",
        "Quarantined:",
        "Corrupt:",
        "Merge conflicts (24 h):",
        "Linear queue:",
        "Warning:",
        "Sessions:",
        "Changes (last ",
    ):
        _line(report, prefix)


def test_report_names_the_database_the_root_and_how_it_was_resolved(
    project: Path,
) -> None:
    opened = _store.open_store(project)
    report = _report(project)

    assert _line(report, "Store:") == f"Store: {opened.db_path}"
    root_line = _line(report, "Root:")
    assert str(opened.root) in root_line
    assert opened.resolution.source in root_line
    assert f"schema v{_store.SCHEMA_VERSION}" in root_line


def test_report_sizes_the_database_and_its_wal(project: Path) -> None:
    opened = _store.open_store(project)
    status = opened.status()
    size_line = _line(_report(project), "Size:")

    assert f"db={status.db_size} B" in size_line
    assert f"wal={status.wal_size} B" in size_line
    assert f"max seq={status.max_seq}" in size_line
    assert status.db_size > 0, "the database exists, so its size must not read 0"


def test_report_lists_the_most_recent_changes_newest_first(project: Path) -> None:
    backlog_server.backlog_add_task(
        "A task", "ts", phase="build", tldr="short", options={"task_id": "ts-001"},
    )
    for priority in ("high", "low", "critical"):
        backlog_server.backlog_update_task("ts-001", "priority", priority)

    report = _report(project)
    change_lines = [
        line for line in report.splitlines() if line.startswith("  [")
    ]
    assert change_lines, f"no change rows rendered:\n{report}"

    seqs = [int(line.split("[", 1)[1].split("]", 1)[0]) for line in change_lines]
    assert seqs == sorted(seqs, reverse=True), "changes must read newest first"
    assert len(seqs) <= 20, "spec 3.8 caps the change list at 20 rows"
    assert "task/ts-001" in change_lines[0]


def test_report_counts_the_session_it_is_running_in(project: Path) -> None:
    opened = _store.open_store(project)
    report = _report(project)

    assert _line(report, "Sessions:").startswith("Sessions: ")
    assert opened.session in report


def test_report_shows_a_dirty_projection_file(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An export that could not reach disk leaves the file dirty; an operator
    reading the report is the only way that ever gets noticed."""
    opened = _store.open_store(project)
    with opened.transaction(tool="dirty-marker") as tx:
        tx.connection.execute(
            "UPDATE projection SET dirty=1 WHERE file='backlog.yaml'"
        )

    assert _line(_report(project), "Dirty:").startswith("Dirty: 1")
    assert "backlog.yaml" in _line(_report(project), "Dirty:")


def test_report_shows_a_quarantined_projection_file(project: Path) -> None:
    opened = _store.open_store(project)
    with opened.transaction(tool="quarantine-marker") as tx:
        tx.connection.execute(
            "UPDATE projection SET quarantined=1 WHERE file='backlog.yaml'"
        )

    quarantined = _line(_report(project), "Quarantined:")
    assert quarantined.startswith("Quarantined: 1")
    assert "backlog.yaml" in quarantined


def test_report_names_recovered_corrupt_databases(project: Path) -> None:
    opened = _store.open_store(project)
    corrupt = opened.db_path.with_name("store.db.corrupt-20260101T000000Z")
    corrupt.write_bytes(b"not a database")

    corrupt_line = _line(_report(project), "Corrupt:")
    assert corrupt_line.startswith("Corrupt: 1")
    assert corrupt.name in corrupt_line


def test_report_counts_pending_linear_pushes_only(project: Path) -> None:
    opened = _store.open_store(project)
    assert _line(_report(project), "Linear queue:") == "Linear queue: 0 pending"

    with opened.transaction(tool="queue-a-push") as tx:
        first = tx.linear_enqueue("task_upsert", "ts-001", None, None)
        tx.linear_enqueue("task_upsert", "ts-002", None, None)

    assert _line(_report(project), "Linear queue:") == "Linear queue: 2 pending"

    # A settled push is history, not backlog — it must drop out of the count.
    opened.linear_mark(first, state="done")
    assert _line(_report(project), "Linear queue:") == "Linear queue: 1 pending"


def test_report_says_none_rather_than_omitting_an_empty_warning(
    project: Path,
) -> None:
    assert _line(_report(project), "Warning:") == "Warning: none"


def test_reporting_never_writes_to_the_store(project: Path) -> None:
    """The tool is a read: it must not advance the change log or dirty a file."""
    opened = _store.open_store(project)
    before = opened.status()

    _report(project)
    _report(project)

    after = opened.status()
    assert after.max_seq == before.max_seq
    assert after.dirty_files == before.dirty_files


def test_report_explains_itself_when_there_is_no_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / ".taskmaster" / "backlog.yaml"
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: missing)
    assert backlog_server.backlog_store_status() == f"no backlog found at {missing}"


def test_report_answers_on_a_network_share_with_no_store(
    bare_project: Path, network_root: str
) -> None:
    """The one situation the `Warning:` line exists for must not be the one the
    tool raises in. The store degrades to projection-only on a share; every line
    of the report still has to come back."""
    report = backlog_server.backlog_store_status()

    assert network_root in _line(report, "Warning:")
    assert _line(report, "Linear queue:") == "Linear queue: 0 pending"
    assert _line(report, "Changes (last 0):") == "Changes (last 0):"
    assert _line(report, "Sessions:") == "Sessions: 0 live"


def test_report_answers_on_a_network_share_that_has_a_store(
    project: Path, network_root: str
) -> None:
    report = backlog_server.backlog_store_status()
    assert network_root in _line(report, "Warning:")
    assert _line(report, "Linear queue:").endswith("pending")


def test_reporting_does_not_create_a_store(bare_project: Path) -> None:
    """A diagnostic that adopts a whole project on first touch is not a
    diagnostic. Nothing under `local/` may appear."""
    local = bare_project.parent / "local"
    assert not (local / "store.db").exists()

    report = backlog_server.backlog_store_status()

    assert not (local / "store.db").exists()
    assert "no store yet" in _line(report, "Warning:")
    assert _line(report, "Size:").startswith("Size: db=0 B")


def test_reporting_a_damaged_store_leaves_it_exactly_as_it_is(
    bare_project: Path,
) -> None:
    """An operator runs this *because* they suspect the store is broken. Moving
    the evidence aside before they can look at it is the failure."""
    db = _store.db_path(bare_project)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is not a database")

    report = backlog_server.backlog_store_status()

    assert db.read_bytes() == b"this is not a database"
    assert sorted(db.parent.glob("store.db.corrupt-*")) == []
    assert _line(report, "Corrupt:") == "Corrupt: 0"
    warning = _line(report, "Warning:")
    assert "not a SQLite database" in warning
    assert "NOT" not in warning or "left exactly as it is" in warning


def test_reporting_a_truncated_store_reports_rather_than_recovers(
    bare_project: Path,
) -> None:
    """A file with the right header but garbage after it is the corruption the
    store would normally recover from; the report must only describe it."""
    db = _store.db_path(bare_project)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(_store.SQLITE_HEADER + b"\x00" * 400)

    report = backlog_server.backlog_store_status()

    assert sorted(db.parent.glob("store.db.corrupt-*")) == []
    assert db.stat().st_size == len(_store.SQLITE_HEADER) + 400
    assert _line(report, "Warning:") != "Warning: none"


def test_read_only_status_reads_a_wal_store_without_its_shm(
    bare_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state an operator actually finds after a crash: a WAL database whose
    shared-memory file is gone. The status tool must read it as it stands --
    reporting it, not repairing it, and never leaving a write behind."""
    _store.reset_for_tests()
    with _store.transaction(backlog_path=bare_project, tool="seed") as tx:
        tx.linear_enqueue("push", "ts-001", None, None)
    db = _store.db_path(bare_project)
    _store.reset_for_tests()  # closes the connections holding the shm open
    shm = db.with_name(db.name + "-shm")
    shm.unlink(missing_ok=True)
    before = {path.name for path in db.parent.iterdir()}

    status = _store.read_only_status(bare_project)

    assert status.schema_version == _store.SCHEMA_VERSION, status
    assert status.max_seq > 0, status
    assert status.linear_pending == 1, status
    assert "corrupt" not in (status.warning or "").lower(), status.warning
    after = {path.name for path in db.parent.iterdir()}
    assert not [name for name in after - before if "corrupt" in name], after - before
