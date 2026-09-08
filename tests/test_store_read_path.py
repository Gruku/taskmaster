"""User intent: a warm read on a big backlog must stay cheap and must never
block behind a writer. B-082 hung every read tool on a 2,050-file project —
a permanently quarantined file forced a full write-transaction scan on every
read, the git index was hashed twice per read, and a cold process took the
30 s writer mutex just to open an existing database.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from taskmaster.taskmaster_v3 import render_frontmatter


def _write_v4_projection(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    tm_dir = root / ".taskmaster"
    (tm_dir / "tasks").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    backlog_path = tm_dir / "backlog.yaml"
    backlog_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"project": "read-path-tests", "schema_version": 4},
                "epics": [
                    {
                        "id": "core",
                        "name": "Store core",
                        "status": "in-progress",
                        "phase": "build",
                    }
                ],
                "phases": [{"id": "build", "name": "Build", "status": "in-progress"}],
            },
            sort_keys=False,
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
            "## Notes\n\nIntact.",
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


def _settle(opened) -> None:
    with opened.transaction(tool="settle"):
        pass
    opened._last_read_scan_clock = None


# -- Defect 1: a permanent quarantine must not force a scan on every read ----


def test_a_permanently_quarantined_file_leaves_the_projection_unchanged(
    tmp_path, store_api
):
    """An unrepairable file is re-quarantined by every scan, so a change check
    that returns True whenever any row is quarantined never settles: every read
    past the throttle opened a write transaction and re-parsed the whole tree."""
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine")
    task_path.write_text(
        "<<<<<<< HEAD\n---\nid: core-001\n---\n=======\nx\n>>>>>>> theirs\n",
        encoding="utf-8",
    )
    _settle(opened)
    assert "tasks/core-001.md" in opened.status().quarantined_files

    assert opened._projection_changed_on_disk() is False


def test_a_repaired_quarantined_file_is_still_adopted(tmp_path, store_api):
    """The cheap path must not blind the store to a real edit of the same file."""
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine")
    intact = task_path.read_text(encoding="utf-8")
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    _settle(opened)
    assert opened._projection_changed_on_disk() is False

    task_path.write_text(intact.replace("Alpha", "Repaired"), encoding="utf-8")
    assert opened._projection_changed_on_disk() is True
    with opened.transaction(tool="adopt"):
        pass
    assert opened.status().quarantined_files == ()
    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Repaired"


def test_a_quarantined_file_is_not_reparsed_while_it_is_unchanged(
    tmp_path, store_api
):
    """A quarantined row opted out of the stat shortcut, so every scan re-read
    and re-parsed each broken file twice. The stamp that produced the
    quarantine is the cheap proof that nothing has changed."""
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="quarantine")
    task_path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    _settle(opened)

    reads: list[str] = []
    original = store_api._read_file_snapshot

    def counting(path):
        reads.append(str(path))
        return original(path)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_api, "_read_file_snapshot", counting)
        with opened.transaction(tool="warm-scan"):
            pass
    assert not [name for name in reads if name.endswith("core-001.md")], reads


def test_an_unparseable_project_yaml_leaves_the_projection_unchanged(
    tmp_path, store_api
):
    """`project.yaml` never gets a projection row when it fails to parse, so
    the known/actual file sets differed forever — the same permanent-rescan
    loop by another route."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="project-yaml")
    (backlog_path.parent / "project.yaml").write_text(
        "- not: a\n- mapping\n", encoding="utf-8"
    )
    _settle(opened)

    assert opened._projection_changed_on_disk() is False


# -- Defect 2: the git generation must not hash the index -------------------


def test_the_git_generation_ignores_the_index_content(tmp_path, store_api):
    """Hashing `.git/index` made every git command in a monorepo force a full
    re-hash of every projection file, twice per read."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="git-gen")
    index = backlog_path.parent.parent / ".git" / "index"
    index.write_bytes(b"A" * 64)
    stat = index.stat()
    before = opened._git_generation()

    index.write_bytes(b"B" * 64)
    import os as _os

    _os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert opened._git_generation() == before


def test_the_git_generation_moves_on_a_branch_switch(tmp_path, store_api):
    """A checkout rewrites files without changing their size, so HEAD moving
    still has to force the hash comparison."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="git-gen")
    head = backlog_path.parent.parent / ".git" / "HEAD"
    before = opened._git_generation()
    head.write_text("ref: refs/heads/other\n", encoding="utf-8")
    assert opened._git_generation() != before


def test_the_git_generation_ignores_the_index_entirely(tmp_path, store_api):
    """`git status` rewrites the index to refresh its stat cache, so with ten
    sessions on one repo the token moved every few seconds and every process
    re-hashed all 2,050 files on its next read. The index is no longer part of
    the token at all; what that costs is a same-size restore inside one mtime
    tick on a coarse-timestamp filesystem, which a non-git restore (tar,
    `rsync -t`) would miss anyway."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="git-gen")
    index = backlog_path.parent.parent / ".git" / "index"
    index.write_bytes(b"A" * 64)
    before = opened._git_generation()
    index.write_bytes(b"A" * 128)
    assert opened._git_generation() == before


def test_a_read_computes_the_git_generation_once(tmp_path, store_api):
    """It was computed in the change check and again in the scan it triggers."""
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="git-gen")
    _settle(opened)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("Alpha", "Beta"),
        encoding="utf-8",
    )

    calls = []
    real = type(opened)._git_generation

    def counting(self):
        calls.append(1)
        return real(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(opened), "_git_generation", counting)
        opened._maybe_scan_on_read()
    assert len(calls) == 1, calls


# -- Defect 3: a cold open must not take the writer mutex -------------------


def test_a_cold_open_of_a_current_database_takes_no_writer_mutex(
    tmp_path, store_api
):
    """A fresh process doing a plain read waited up to 30 s behind any writer
    and then failed with nothing to show for it."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="warm")
    _settle(opened)
    store_api.reset_for_tests()

    taken: list[dict] = []
    real = store_api.Store._writer_mutex

    @contextmanager
    def spy(self, **kwargs):
        taken.append(kwargs)
        with real(self, **kwargs):
            yield

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_api.Store, "_writer_mutex", spy)
        cold = store_api.open_store(backlog_path=backlog_path, session="cold")
        data = cold.load_dict()
    assert taken == [], taken
    assert data["epics"][0]["id"] == "core"


def test_a_cold_read_answers_while_the_writer_mutex_is_held(tmp_path, store_api):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="warm")
    _settle(opened)
    store_api.reset_for_tests()

    @contextmanager
    def blocked(self, **kwargs):
        time.sleep(30)
        yield

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_api.Store, "_writer_mutex", blocked)
        started = time.monotonic()
        cold = store_api.open_store(backlog_path=backlog_path, session="cold")
        data = cold.load_dict()
        elapsed = time.monotonic() - started
    assert elapsed < 5.0, elapsed
    assert data["epics"][0]["id"] == "core"


# -- Defect 4: a no-op read scan must not run the write pipeline ------------


def test_a_read_scan_that_changes_nothing_does_not_run_the_write_pipeline(
    tmp_path, store_api
):
    """`_maybe_scan_on_read` opened a full transaction with an empty body, so
    ten server processes committed on an idle backlog several times a second."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="noop")
    _settle(opened)

    ran: list[str] = []

    def record(name):
        def hook(self, tx):
            ran.append(name)

        return hook

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            type(opened),
            "_projection_changed_on_disk",
            lambda self, **kwargs: True,
        )
        patch.setattr(type(opened), "_refresh_derived", record("derived"))
        patch.setattr(type(opened), "_export_touched", record("export"))
        patch.setattr(
            type(opened), "_regenerate_progress_if_due", record("progress")
        )
        opened._maybe_scan_on_read()
    assert ran == [], ran


def test_a_read_scan_that_finds_a_change_still_commits_it(tmp_path, store_api):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="adopt")
    _settle(opened)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("Alpha", "Gamma"),
        encoding="utf-8",
    )

    assert opened.load_dict()["epics"][0]["tasks"][0]["title"] == "Gamma"


def test_a_new_file_a_deleted_file_and_a_rewrite_are_all_still_adopted(
    tmp_path, store_api
):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="adopt")
    _settle(opened)

    second = task_path.parent / "core-002.md"
    second.write_text(
        render_frontmatter(
            {
                "id": "core-002",
                "title": "Beta",
                "status": "todo",
                "epic": "core",
                "order": 2.0,
            },
            "## Notes\n\nNew.",
        ),
        encoding="utf-8",
    )
    opened._last_read_scan_clock = None
    titles = {
        task["title"] for task in opened.load_dict()["epics"][0]["tasks"]
    }
    assert titles == {"Alpha", "Beta"}

    second.unlink()
    opened._last_read_scan_clock = None
    with opened.transaction(tool="delete-scan"):
        pass
    assert (backlog_path.parent / "tasks" / "core-002.md").exists(), (
        "a deleted projection file is re-exported, never treated as a deletion"
    )


# -- Defect 5: one directory walk, not eighteen globs -----------------------


def test_the_entity_file_walk_matches_the_glob_it_replaces(tmp_path, store_api):
    backlog_path, task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="walk")
    tm_dir = backlog_path.parent
    for rel in (
        "tasks/archive/core-009.md",
        "epics/core.md",
        "phases/build.md",
        "bugs/B-1.md",
        "bugs/archive/B-2.md",
        "issues/ISS-1.md",
        "handovers/H-1.md",
        "handovers/_archive/2026/H-0.md",
        "decisions/DEC-1.md",
        "ideas/IDEA-1.md",
        "notes/NOTE-1.md",
        "notes/_archive/NOTE-0.md",
        "areas/area-1.md",
        "trackers/t1.md",
        "integrations/trackers/t2.md",
    ):
        path = tm_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: x\n---\n", encoding="utf-8")
    # Names the walk must skip, and a legacy duplicate the canonical path wins.
    (tm_dir / "tasks" / ".hidden.md").write_text("x", encoding="utf-8")
    (tm_dir / "tasks" / "core-003.tmp.md").write_text("x", encoding="utf-8")
    (tm_dir / "tasks" / "core-004.corrupt-1.md").write_text("x", encoding="utf-8")
    (tm_dir / "tasks" / "archive" / "core-001.md").write_text("x", encoding="utf-8")

    specs = (
        ("task", ("tasks/*.md", "tasks/archive/*.md")),
        ("epic", ("epics/*.md",)),
        ("phase", ("phases/*.md",)),
        ("bug", ("bugs/*.md", "bugs/archive/*.md")),
        ("issue", ("issues/*.md", "issues/archive/*.md")),
        (
            "handover",
            ("handovers/*.md", "handovers/_archive/*/*.md", "handovers/archive/*.md"),
        ),
        ("decision", ("decisions/*.md",)),
        ("idea", ("ideas/IDEA-*.md",)),
        ("note", ("notes/NOTE-*.md", "notes/_archive/NOTE-*.md")),
        ("area", ("areas/*.md",)),
        ("tracker", ("trackers/*.md", "integrations/trackers/*.md")),
    )
    expected: dict = {}
    for kind, patterns in specs:
        for pattern in patterns:
            for path in sorted(tm_dir.glob(pattern)):
                if (
                    path.name.startswith(".")
                    or ".tmp." in path.name
                    or ".corrupt-" in path.name
                ):
                    continue
                expected.setdefault((kind, path.stem), path)
    assert [(k, i, p) for (k, i), p in expected.items()] == opened._known_entity_files()
    assert task_path.exists()


# -- Defect 6: the busy diagnostic must not add two seconds -----------------


def test_the_busy_diagnostic_uses_a_short_timeout(tmp_path, store_api):
    """It opens a second connection to a store that is by definition contended,
    so its own timeout was pure added latency on every busy error."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="busy")
    import sqlite3

    seen: list[float] = []
    real = sqlite3.connect

    def recording(*args, **kwargs):
        if "timeout" in kwargs:
            seen.append(kwargs["timeout"])
        return real(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sqlite3, "connect", recording)
        message = opened._busy_diagnostic()
    assert seen and max(seen) <= 0.3, seen
    assert message.startswith("store busy for ")


def test_the_busy_diagnostic_falls_back_when_the_second_connection_fails(
    tmp_path, store_api
):
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="busy")
    import sqlite3

    def refusing(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sqlite3, "connect", refusing)
        message = opened._busy_diagnostic()
    assert message.startswith("store busy for ")


# -- Defect 7: a rolled-back probe must not discard what it recorded --------


def _log_lines(opened, needle: str) -> int:
    path = opened.db_path.parent / "store.log"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if needle in line)


def test_a_broken_project_yaml_settles_after_one_read(tmp_path, store_api):
    """The reason for an unparseable `project.yaml` lives in the transaction
    until it commits, and no projection row is written for it -- so the probe
    rolled back, dropped the record, and every later read re-ran the scan and
    appended the same line again."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="project-yaml")
    _settle(opened)
    (backlog_path.parent / "project.yaml").write_text(
        "- not: a\n- mapping\n", encoding="utf-8"
    )
    before = _log_lines(opened, "quarantined project.yaml")

    opened._last_read_scan_clock = None
    opened._maybe_scan_on_read()

    assert opened._projection_changed_on_disk() is False
    for _ in range(3):
        opened._last_read_scan_clock = None
        opened._maybe_scan_on_read()
    assert _log_lines(opened, "quarantined project.yaml") - before == 1


def test_a_probe_that_only_logs_still_records_its_line(tmp_path, store_api):
    """An unreadable legacy linear queue is renamed aside on disk and the
    reason is queued as a log entry. The rename is not undone by a rollback, so
    a probe that discarded the entry lost the only record of it."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="queue")
    _settle(opened)
    queue = backlog_path.parent / "integrations" / "linear-queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("{not json", encoding="utf-8")

    with opened.transaction(tool="probe", _probe_only=True):
        pass

    assert not queue.exists()
    assert _log_lines(opened, "quarantined unreadable") == 1


# -- Defect 8: the new columns must not need a schema version bump ----------


def test_the_schema_version_is_unchanged(store_api):
    """A 6.0.1 process that meets a higher version drops every table and
    rebuilds it without the new columns, and the two versions then ping-pong."""
    assert store_api.SCHEMA_VERSION == 1


def test_a_database_without_the_quarantine_columns_is_upgraded_on_open(
    tmp_path, store_api
):
    """The columns arrive additively, so their absence -- not a version number
    -- is what has to send a cold open down the mutex path."""
    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="warm")
    _settle(opened)
    connection = opened.connection
    for column in ("quarantine_mtime", "quarantine_size"):
        connection.execute(f"ALTER TABLE projection DROP COLUMN {column}")
    connection.commit()
    store_api.reset_for_tests()

    taken: list[dict] = []
    real = store_api.Store._writer_mutex

    @contextmanager
    def spy(self, **kwargs):
        taken.append(kwargs)
        with real(self, **kwargs):
            yield

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_api.Store, "_writer_mutex", spy)
        cold = store_api.open_store(backlog_path=backlog_path, session="cold")
        data = cold.load_dict()
    assert taken, "an old database has to take the mutex to gain the columns"
    columns = {
        row[1] for row in cold.connection.execute("PRAGMA table_info(projection)")
    }
    assert {"quarantine_mtime", "quarantine_size"} <= columns
    assert data["epics"][0]["tasks"][0]["title"] == "Alpha"


# -- Defect 9: the directory memo must not leak across threads --------------


def test_two_interleaved_reads_do_not_leak_the_directory_memo(tmp_path, store_api):
    """Connections are per-thread and reads run concurrently, so a memo saved
    and restored on a shared attribute could be restored after its owner left,
    freezing the listing for the life of the process."""
    import threading

    backlog_path, _task_path = _write_v4_projection(tmp_path)
    opened = store_api.open_store(backlog_path=backlog_path, session="threads")
    first_in = threading.Event()
    second_in = threading.Event()
    first_out = threading.Event()
    seen: dict[str, object] = {}

    def first() -> None:
        with opened._memoized_entity_files():
            first_in.set()
            second_in.wait(5)
        first_out.set()

    def second() -> None:
        first_in.wait(5)
        with opened._memoized_entity_files():
            second_in.set()
            first_out.wait(5)
        seen["after"] = opened._directory_listings

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert seen["after"] is None
