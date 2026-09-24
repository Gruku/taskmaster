# User intent: prove a cutover interrupted at any stage boundary — by an exception or by the
# process dying (os._exit) — can be resumed to a native store or rolled back to the exact
# legacy store, and that a crash after activation commits can only roll forward.
"""Crash matrix for `taskmaster.native.cutover`: 16 points x {exception, exit} x {resume, rollback}."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from taskmaster.admission import assert_compatible
from taskmaster.native import cutover
from taskmaster.native.db import assert_native
from tests import cutover_stubs
from tests.native_twins import committed
from tests.test_native_cutover import build_project, legacy_state

POINTS = [f"{stage}:{when}" for stage in cutover.STAGES for when in ("before-commit", "after-commit")] + [
    "backfill.entities", "backfill.search"]  # Inside backfill's own transaction.
ACTIVATED = {"activate:after-commit", "release:before-commit", "release:after-commit"}
SCRIPT = """
import os, sys
from pathlib import Path
from tests import cutover_stubs
from taskmaster.native import cutover
cutover_stubs.install()
def hook(name):
    if name == sys.argv[2]:
        os._exit(37)
cutover.HOOKS["checkpoint"] = hook
cutover.cutover(Path(sys.argv[1]))
os._exit(0)
"""


class Injected(RuntimeError):
    pass


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj", monkeypatch)
    monkeypatch.chdir(tmp_path)
    cutover_stubs.install(monkeypatch)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    return root


def crash(root: Path, point: str, mode: str, monkeypatch) -> None:
    if mode == "exception":
        def hook(name):
            if name == point:
                raise Injected(point)
        monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)
        with pytest.raises(Injected):
            cutover.cutover(root)
        monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    else:
        repo = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-c", SCRIPT, str(root), point], cwd=repo, timeout=120,
                                env=dict(os.environ, PYTHONPATH=repo), capture_output=True, text=True)
        assert result.returncode == 37, result.stdout + result.stderr


def authority(root: Path) -> str | None:
    with closing(sqlite3.connect(cutover.database_path(root))) as connection:
        row = connection.execute("SELECT value FROM native_manifest WHERE key='authority'").fetchone() \
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='native_manifest'").fetchone() else None
        return row[0] if row else None


@pytest.mark.parametrize("mode", ["exception", "exit"])
@pytest.mark.parametrize("point", POINTS)
def test_crash_then_resume(project, point, mode, monkeypatch):
    before = committed(project)
    crash(project, point, mode, monkeypatch)
    assert (authority(project) == "native") == (point in ACTIVATED)
    if point == "fence:before-commit":
        # The fence never committed: nothing to resume, and a fresh run succeeds.
        with pytest.raises(cutover.CutoverRefused, match="no cutover journal"):
            cutover.cutover(project, resume=True)
        report = cutover.cutover(project)
    else:
        report = cutover.cutover(project, resume=True)
    assert report["ok"] and report["completed_stages"][-1] == "release"
    with closing(sqlite3.connect(cutover.database_path(project))) as connection:
        assert_native(connection)
    assert committed(project) == before


@pytest.mark.parametrize("mode", ["exception", "exit"])
@pytest.mark.parametrize("point", POINTS)
def test_crash_then_rollback(project, point, mode, monkeypatch):
    before, digest = legacy_state(project), None
    with closing(sqlite3.connect(cutover.database_path(project))) as connection:
        digest = cutover.domain_digest(connection)
    crash(project, point, mode, monkeypatch)
    if point in ACTIVATED:
        with pytest.raises(cutover.CutoverRefused, match="(?i)escape hatch"):
            cutover.rollback(project)
        assert authority(project) == "native"
        return
    if point == "fence:before-commit":
        with pytest.raises(cutover.CutoverRefused, match="no cutover fence"):
            cutover.rollback(project)
    else:
        report = cutover.rollback(project)
        assert report["ok"] and report["restored_from"] is None and report["domain_digest"] == digest
    assert legacy_state(project) == before
    assert authority(project) in (None, "legacy")
    with closing(sqlite3.connect(cutover.database_path(project))) as connection:
        assert_compatible(connection)
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (cutover.JOURNAL,)).fetchone()
    # A rolled-back store can be cut over again from scratch.
    assert cutover.cutover(project)["ok"]


@pytest.mark.parametrize("point", ["rollback:saved", "rollback:restored", "rollback:before-commit"])
def test_interrupted_rollback_can_be_repeated(project, point, monkeypatch):
    before = legacy_state(project)
    crash(project, "compare:before-commit", "exception", monkeypatch)
    with closing(sqlite3.connect(cutover.database_path(project), isolation_level=None)) as connection:
        connection.execute("DELETE FROM entities WHERE kind='bug'")  # Force the restore path.
    def hook(name):
        if name == point:
            raise Injected(point)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", hook)
    with pytest.raises(Injected):
        cutover.rollback(project, discard_writes_since_backup=True)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    report = cutover.rollback(project, discard_writes_since_backup=True)
    assert report["ok"]
    assert legacy_state(project) == before
