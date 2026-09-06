# User intent: prove the `--restore-scalars` recovery mode puts back the
# scalar fields an older migration deleted, so the dependency gates see their
# edges again — through the store on a project that has one, and by splicing
# the files on a pre-6.0.0 projection that does not, which keeps SQLite
# adoption from being a precondition for the repair.
"""Recovery-mode tests for `scripts/migrate_links --restore-scalars`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import migrate_links as links_script
from taskmaster.taskmaster_v3 import parse_frontmatter

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _run(root: Path, *args: str) -> tuple[int, dict]:
    """Run the script in a subprocess, as a user repairing a real project does."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.migrate_links", "--root", str(root),
         "--restore-scalars", *args],
        capture_output=True, text=True, check=False, cwd=str(PLUGIN_ROOT),
    )
    assert result.returncode == 0, result.stderr
    return result.returncode, json.loads(result.stdout)


BACKLOG = """meta:
  project: Demo
  schema_version: 3
epics:
- id: e1
  name: E
  status: active
  tasks:
  - id: T-001
    title: First
    status: todo
    phase: p1
    links:
    - type: depends_on
      target: T-002
    - type: relates_to
      target: ISS-001
  - id: T-002
    title: Second
    status: todo
    phase: p1
    links:
    - type: blocks
      target: T-001
  - id: T-003
    title: Third
    status: todo
    phase: p1
    depends_on:
    - T-002
    links:
    - type: depends_on
      target: T-999
phases:
- id: p1
  name: P1
  status: active
"""

ISSUE = """---
id: ISS-001
title: Broken
tldr: x
severity: P2
status: fixed
evidence: recurs
resolved: '2026-05-21'
links:
- type: fixed_in_task
  target: T-001
---
Body line one.

Body line two.
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A pre-6.0.0 file projection whose scalars an old migration deleted."""
    d = tmp_path / ".taskmaster"
    (d / "issues").mkdir(parents=True)
    (d / "tasks").mkdir()
    # write_bytes, not write_text: text mode would translate to CRLF on
    # Windows and this fixture is the LF half of the line-ending coverage.
    (d / "backlog.yaml").write_bytes(BACKLOG.encode("utf-8"))
    (d / "issues" / "ISS-001.md").write_bytes(ISSUE.encode("utf-8"))
    return tmp_path


# ── the restore itself ─────────────────────────────────────────────────────


def test_restore_puts_the_task_scalar_back(project):
    _, summary = _run(project)
    data = yaml.safe_load((project / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in data["epics"][0]["tasks"]}
    assert tasks["T-001"]["depends_on"] == ["T-002"]
    assert summary["restored"]["tasks"] == 1


def test_the_dependency_gate_sees_the_restored_edge(project, monkeypatch):
    _run(project)
    from taskmaster import backlog_server as bs

    backlog_path = project / ".taskmaster" / "backlog.yaml"
    monkeypatch.setattr(bs, "_backlog_path", lambda: backlog_path)
    out = bs.backlog_dependencies("T-001")
    assert "Depends on (upstream)" in out
    assert "`T-002`" in out


def test_restore_puts_the_issue_scalars_back(project):
    _, summary = _run(project)
    text = (project / ".taskmaster" / "issues" / "ISS-001.md").read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    assert fm["fixed_in_task"] == "T-001"
    assert body == "Body line one.\n\nBody line two.\n"
    assert summary["restored"]["issues"] == 1


def test_restore_never_overwrites_an_existing_scalar(project):
    """T-003's scalar disagrees with its link — the file wins, not the link."""
    _run(project)
    data = yaml.safe_load((project / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in data["epics"][0]["tasks"]}
    assert tasks["T-003"]["depends_on"] == ["T-002"]


def test_restore_leaves_untouched_entities_alone(project):
    _run(project)
    data = yaml.safe_load((project / ".taskmaster" / "backlog.yaml").read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in data["epics"][0]["tasks"]}
    assert "depends_on" not in tasks["T-002"]  # only a `blocks` link


# ── byte layout ────────────────────────────────────────────────────────────


def test_restore_changes_only_the_inserted_lines(project):
    """Every original line survives byte-for-byte; the diff is pure insertion."""
    import difflib

    path = project / ".taskmaster" / "backlog.yaml"
    before = path.read_text(encoding="utf-8", newline="").splitlines(keepends=True)
    _run(project)
    after = path.read_text(encoding="utf-8", newline="").splitlines(keepends=True)

    inserted: list[str] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False).get_opcodes():
        assert tag in ("equal", "insert"), f"restore did a {tag}"
        if tag == "insert":
            inserted.extend(after[j1:j2])
    assert inserted == ["    depends_on:\n", "    - T-002\n"]


def test_restore_preserves_crlf_line_endings(tmp_path):
    d = tmp_path / ".taskmaster"
    (d / "issues").mkdir(parents=True)
    (d / "backlog.yaml").write_bytes(BACKLOG.replace("\n", "\r\n").encode("utf-8"))
    (d / "issues" / "ISS-001.md").write_bytes(ISSUE.replace("\n", "\r\n").encode("utf-8"))
    _run(tmp_path)
    for name in ("backlog.yaml", "issues/ISS-001.md"):
        raw = (d / name).read_bytes()
        assert b"\r\n" in raw
        assert raw.replace(b"\r\n", b"") .count(b"\n") == 0, f"{name} gained a bare LF"


# ── idempotence, dry run, and the store refusal ────────────────────────────


def test_restore_is_idempotent(project):
    _, first = _run(project)
    assert first["status"] != "no changes"
    after_first = (project / ".taskmaster" / "backlog.yaml").read_bytes()

    _, second = _run(project)
    assert second["status"] == "no changes"
    assert second["restored"] == {"tasks": 0, "issues": 0}
    assert (project / ".taskmaster" / "backlog.yaml").read_bytes() == after_first


def test_dry_run_writes_nothing(project):
    before = (project / ".taskmaster" / "backlog.yaml").read_bytes()
    _, summary = _run(project, "--dry-run")
    assert summary["dry_run"] is True
    assert summary["restored"]["tasks"] == 1
    assert (project / ".taskmaster" / "backlog.yaml").read_bytes() == before


def test_restore_survives_a_backlog_with_no_issues_directory(tmp_path):
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_bytes(BACKLOG.encode("utf-8"))
    _, summary = _run(tmp_path)
    assert summary["restored"] == {"tasks": 1, "issues": 0}


def test_a_storeless_projection_takes_the_file_path(project):
    _, summary = _run(project)
    assert summary["via"] == "files"


def test_the_presence_of_a_store_switches_the_path(project):
    """The store owns the projection once it exists — never two writers."""
    from taskmaster import store as store_mod

    store_mod.open_store(project / ".taskmaster").load_dict()
    _, summary = _run(project)
    assert summary["via"] == "store"


def test_the_file_path_will_not_run_behind_a_store(project):
    """Called directly, the raw-writing path still refuses point blank."""
    (project / ".taskmaster" / "local").mkdir(exist_ok=True)
    (project / ".taskmaster" / "local" / "store.db").write_bytes(b"")
    with pytest.raises(RuntimeError, match="restore through the store"):
        links_script.restore_scalars_in_files(project / ".taskmaster",
                                              dry_run=True)


# ── v4: tasks live in `tasks/<id>.md`, not in the epic tree ────────────────
#
# This is the layout the rescue actually has to repair. The real backlog this
# was written for declares schema_version 4, so its `backlog.yaml` epics carry
# no `tasks:` key at all and every affected `depends_on` sits in a shard.

V4_BACKLOG = """meta:
  project: Demo
  schema_version: 4
version: 4
epics:
- id: e1
  name: E
  status: active
phases:
- id: p1
  name: P1
  status: active
"""

# Key order as pyyaml's sorted dump leaves it — that is what the real shards
# look like, and it puts `links:` last, after the multi-line `notes:` scalar
# whose continuation lines the splicer must not mistake for a top-level key.
V4_TASK = """---
epic: e1
id: {id}
notes: 'A wrapped note whose continuation lines are indented, and which mentions

  depends_on: and links: in prose to bait a naive line scanner.'
phase: p1
status: todo
title: {title}
{scalar}links:
- target: {target}
  type: depends_on
---
Body of {id}.
"""


@pytest.fixture()
def v4_project(tmp_path: Path) -> Path:
    """A v4 projection whose task shards lost `depends_on` to the old run."""
    d = tmp_path / ".taskmaster"
    (d / "tasks").mkdir(parents=True)
    (d / "issues").mkdir()
    (d / "backlog.yaml").write_bytes(V4_BACKLOG.encode("utf-8"))
    for ident, target, scalar in (("T-001", "T-002", ""),
                                  ("T-003", "T-999", "depends_on:\n- T-002\n")):
        text = V4_TASK.format(id=ident, title=ident, target=target,
                              scalar=scalar)
        (d / "tasks" / f"{ident}.md").write_bytes(text.encode("utf-8"))
    return tmp_path


def test_restore_puts_the_scalar_back_in_a_v4_task_shard(v4_project):
    _, summary = _run(v4_project)
    text = (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    assert fm["depends_on"] == ["T-002"]
    assert body == "Body of T-001.\n"
    assert summary["restored"]["tasks"] == 1
    assert summary["written"] == ["T-001"]


def test_the_dependency_gate_sees_a_restored_v4_edge(v4_project, monkeypatch):
    """The whole point, in the shape the real backlog is actually in.

    Asking the gate anything opens the store, so by the time the repair runs
    this is a store-bearing project — which is the real one's situation too,
    and is why the rescue has to work through the store rather than refuse.
    """
    from taskmaster import backlog_server as bs

    backlog_path = v4_project / ".taskmaster" / "backlog.yaml"
    monkeypatch.setattr(bs, "_backlog_path", lambda: backlog_path)
    before = bs.backlog_dependencies("T-001")
    assert "T-002" not in before
    assert (v4_project / ".taskmaster" / "local" / "store.db").exists()

    _, summary = _run(v4_project)
    assert summary["via"] == "store"
    assert summary["restored"]["tasks"] == 1

    after = bs.backlog_dependencies("T-001")
    assert "Depends on (upstream)" in after
    assert "`T-002`" in after


def test_the_store_restore_reaches_the_task_shard_on_disk(v4_project):
    """The store re-exports what it wrote, so the file gets the scalar too."""
    from taskmaster import store as store_mod

    store_mod.open_store(v4_project / ".taskmaster").load_dict()
    _, summary = _run(v4_project)
    assert summary["via"] == "store"
    fm, _ = parse_frontmatter(
        (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_text(encoding="utf-8"))
    assert fm["depends_on"] == ["T-002"]


def test_the_store_restore_is_idempotent(v4_project):
    from taskmaster import store as store_mod

    store_mod.open_store(v4_project / ".taskmaster").load_dict()
    _, first = _run(v4_project)
    assert first["restored"]["tasks"] == 1
    _, second = _run(v4_project)
    assert second["via"] == "store"
    assert second["status"] == "no changes"
    assert second["restored"] == {"tasks": 0, "issues": 0}


def test_the_store_dry_run_writes_nothing(v4_project):
    from taskmaster import store as store_mod

    store_mod.open_store(v4_project / ".taskmaster").load_dict()
    before = (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes()
    _, summary = _run(v4_project, "--dry-run")
    assert summary["via"] == "store"
    assert summary["dry_run"] is True
    assert summary["would_write"] == ["T-001"]
    assert (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes() == before


def test_v4_restore_never_overwrites_an_existing_scalar(v4_project):
    """T-003's shard already has a scalar that disagrees — the file wins."""
    _run(v4_project)
    fm, _ = parse_frontmatter(
        (v4_project / ".taskmaster" / "tasks" / "T-003.md").read_text(encoding="utf-8"))
    assert fm["depends_on"] == ["T-002"]


def test_v4_restore_changes_only_the_inserted_lines(v4_project):
    """Pure insertion, and the wrapped `notes:` scalar survives untouched."""
    import difflib

    path = v4_project / ".taskmaster" / "tasks" / "T-001.md"
    before = path.read_text(encoding="utf-8", newline="").splitlines(keepends=True)
    _run(v4_project)
    after = path.read_text(encoding="utf-8", newline="").splitlines(keepends=True)

    inserted: list[str] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False).get_opcodes():
        assert tag in ("equal", "insert"), f"restore did a {tag}"
        if tag == "insert":
            inserted.extend(after[j1:j2])
    assert inserted == ["depends_on:\n", "- T-002\n"]


def test_v4_restore_is_idempotent(v4_project):
    _, first = _run(v4_project)
    assert first["status"] != "no changes"
    after_first = (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes()

    _, second = _run(v4_project)
    assert second["status"] == "no changes"
    assert second["restored"] == {"tasks": 0, "issues": 0}
    assert (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes() == after_first


def test_v4_restore_preserves_crlf_in_a_task_shard(tmp_path):
    d = tmp_path / ".taskmaster"
    (d / "tasks").mkdir(parents=True)
    (d / "backlog.yaml").write_bytes(V4_BACKLOG.replace("\n", "\r\n").encode("utf-8"))
    text = V4_TASK.format(id="T-001", title="T-001", target="T-002", scalar="")
    (d / "tasks" / "T-001.md").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    _run(tmp_path)
    raw = (d / "tasks" / "T-001.md").read_bytes()
    assert b"depends_on:\r\n- T-002\r\n" in raw
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


# ── archived shards ────────────────────────────────────────────────────────
#
# The old migration stripped archived tasks too, and on the real backlog they
# are 108 of the 273 affected files. An archived task that comes back with no
# `depends_on` is the same silent unblocking, just deferred.


@pytest.fixture()
def v4_with_archive(v4_project: Path) -> Path:
    archive = v4_project / ".taskmaster" / "tasks" / "archive"
    archive.mkdir()
    text = V4_TASK.format(id="T-900", title="T-900", target="T-002", scalar="")
    (archive / "T-900.md").write_bytes(text.replace(
        "status: todo", "archived: true\nstatus: done").encode("utf-8"))
    return v4_project


def test_the_file_path_restores_an_archived_shard(v4_with_archive):
    _, summary = _run(v4_with_archive)
    assert summary["via"] == "files"
    fm, _ = parse_frontmatter(
        (v4_with_archive / ".taskmaster" / "tasks" / "archive" / "T-900.md")
        .read_text(encoding="utf-8"))
    assert fm["depends_on"] == ["T-002"]
    assert summary["restored"]["tasks"] == 2


def test_the_store_path_restores_an_archived_row(v4_with_archive):
    """`tx.list` hides archived rows by default — the restore must ask for them."""
    from taskmaster import store as store_mod

    store_mod.open_store(v4_with_archive / ".taskmaster").load_dict()
    _, summary = _run(v4_with_archive)
    assert summary["via"] == "store"
    assert "T-900" in summary["written"]
    fm, _ = parse_frontmatter(
        (v4_with_archive / ".taskmaster" / "tasks" / "archive" / "T-900.md")
        .read_text(encoding="utf-8"))
    assert fm["depends_on"] == ["T-002"]


def test_v4_dry_run_writes_nothing(v4_project):
    before = (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes()
    _, summary = _run(v4_project, "--dry-run")
    assert summary["dry_run"] is True
    assert summary["restored"]["tasks"] == 1
    assert summary["would_write"] == ["T-001"]
    assert (v4_project / ".taskmaster" / "tasks" / "T-001.md").read_bytes() == before


# ── the pure planner ───────────────────────────────────────────────────────


def test_planner_reads_the_link_array():
    doc = {"id": "T-1", "links": [{"type": "depends_on", "target": "T-2"},
                                  {"type": "depends_on", "target": "T-3"}]}
    assert links_script.scalar_restore_plan(doc, "task") == {"depends_on": ["T-2", "T-3"]}


def test_planner_skips_a_present_scalar():
    doc = {"id": "T-1", "depends_on": ["T-9"],
           "links": [{"type": "depends_on", "target": "T-2"}]}
    assert links_script.scalar_restore_plan(doc, "task") == {}


def test_planner_treats_an_empty_scalar_as_absent():
    doc = {"id": "T-1", "depends_on": [],
           "links": [{"type": "depends_on", "target": "T-2"}]}
    assert links_script.scalar_restore_plan(doc, "task") == {"depends_on": ["T-2"]}


def test_planner_keeps_issue_fields_singular():
    doc = {"id": "ISS-1", "links": [{"type": "fixed_in_task", "target": "T-2"},
                                    {"type": "duplicate_of", "target": "ISS-9"}]}
    assert links_script.scalar_restore_plan(doc, "issue") == {
        "fixed_in_task": "T-2", "duplicate_of": "ISS-9"}
