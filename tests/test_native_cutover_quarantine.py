# User intent: the cutover must refuse to start while any projection file is quarantined (native
# managed Git refuses until they are repaired), naming each file, and pass once it is repaired.
"""Quarantined-file preflight of `taskmaster.native.cutover` on disposable legacy projects."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import projection_parse, store
from taskmaster.native import cutover
from tests import cutover_stubs
from tests.native_twins import committed, point_server_at
from tests.test_native_cutover import build_project, db, tree_hash

IDENT = "2026-03-04-headerless-notes"
REL = f"handovers/_archive/2026/{IDENT}.md"
BODY = "# Session notes\n\nWhat happened, written before handovers carried frontmatter.\n"
# The minimal frontmatter the runbook prescribes: the keys `build_handover_doc` always writes
# except the timestamps and empty lists it adds on creation, a status so the handover is not
# owed the legacy status backfill, and `archived: true`, which the exporter writes into every
# archived handover (a file repaired in place is imported from its frontmatter, not its path).
REPAIRED = (f"---\nid: {IDENT}\ndate: '2026-03-04'\ntldr: Session notes recovered from a headerless "
            "archived handover\nnext_action: ''\ntask_ids: []\nsession_kind: continuity\nstatus: closed\n"
            "archived: true\n---\n" + BODY)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = build_project(tmp_path / "proj", monkeypatch)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def quiesce(monkeypatch):
    module = cutover_stubs.install(monkeypatch)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    return module


def _adopt(root, monkeypatch):
    """Any tool call scans the projection; `backlog_handover_list` is the runbook's example."""
    point_server_at(monkeypatch, root)
    bs.backlog_handover_list()
    store.reset_for_tests()
    return store.read_only_status(root=root).quarantined_files


def _quarantine_headerless(root, monkeypatch):
    path = root / ".taskmaster" / REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BODY, encoding="utf-8")
    assert _adopt(root, monkeypatch) == (REL,)
    return path


def test_cutover_refuses_a_quarantined_file_and_names_it(project, quiesce, monkeypatch):
    _quarantine_headerless(project, monkeypatch)
    with pytest.raises(cutover.CutoverRefused) as refused:
        cutover.cutover(project)
    message = str(refused.value)
    assert REL in message and "missing or invalid frontmatter" in message
    assert cutover.RUNBOOK in message and "quarantined" in message


def test_dry_run_reports_the_quarantine_refusal(project, quiesce, monkeypatch, capsys):
    _quarantine_headerless(project, monkeypatch)
    report = cutover.dry_run(project)
    assert not report["ok"] and report["planned"] == []
    assert any(REL in r and "missing or invalid frontmatter" in r for r in report["refusals"])
    assert cutover.main(["--root", str(project), "--dry-run", "--json"]) == cutover.EXIT_REFUSED
    assert REL in capsys.readouterr().out


def test_refusal_has_no_side_effects(project, quiesce, monkeypatch, capsys):
    _quarantine_headerless(project, monkeypatch)
    before = tree_hash(project)
    with pytest.raises(cutover.CutoverRefused):
        cutover.cutover(project)
    assert cutover.main(["--root", str(project), "--json"]) == cutover.EXIT_REFUSED
    report = json.loads(capsys.readouterr().out)
    assert any(REL in r for r in report["refusals"])
    assert tree_hash(project) == before
    assert not (db(project).parent / "backups").exists()
    with closing(sqlite3.connect(db(project))) as connection:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        assert meta.get("migration_state", "ready") == "ready" and "migration_token" not in meta
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (cutover.JOURNAL,)).fetchone()


def test_repaired_and_readopted_file_passes_the_preflight(project, quiesce, monkeypatch):
    path = _quarantine_headerless(project, monkeypatch)
    path.write_text(REPAIRED, encoding="utf-8")
    assert _adopt(project, monkeypatch) == ()
    assert cutover.dry_run(project)["ok"]
    assert cutover.cutover(project)["ok"]
    doc, body, archived = committed(project)[("handover", IDENT)]
    assert (archived, doc["status"], body) == (True, "closed", BODY.removesuffix("\n"))


def test_resume_under_the_fence_warns_instead_of_refusing(project, quiesce, monkeypatch):
    """The fence refuses the legacy clients a repair is re-adopted through, so a resume that
    refused would leave only --rollback; it names the files as a warning instead."""
    def crash(name):
        if name == "backup:begin":
            raise RuntimeError(name)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE projection SET quarantined=1 WHERE file LIKE 'bugs/%'")
    lines = []
    assert cutover.cutover(project, resume=True, log=lines.append)["ok"]
    assert any(line.startswith("warning: 1 projection file(s) are quarantined") and "bugs/" in line
               for line in lines)


def test_native_import_parses_headerless_and_repaired_handovers_like_legacy():
    """Native import (`sync_prepare`) and legacy scan share `projection_parse.entity_text`, so the
    file native would quarantine is the one legacy did, and a repaired file stays repaired."""
    with pytest.raises(ValueError, match="missing or invalid frontmatter"):
        projection_parse.authored_rows("handover", IDENT, BODY.encode())
    rows = projection_parse.authored_rows("handover", IDENT, REPAIRED.encode())
    doc, body = rows[("handover", IDENT)]
    assert doc["id"] == IDENT and doc["status"] == "closed" and body == BODY.removesuffix("\n")
