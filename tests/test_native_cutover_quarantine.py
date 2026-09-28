# User intent: the cutover must never activate a project with a quarantined or flagged projection
# file (native managed Git holds on them), checked before it starts and again under the fence,
# naming each file; and the runbook's repair must leave the file adopted, unchanged and usable.
"""Held-file preflight and fenced re-check of `taskmaster.native.cutover` on disposable projects."""
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
HEADER = (f"---\nid: {IDENT}\ndate: '2026-03-04'\ntldr: Session notes recovered from a headerless "
          "archived handover\nnext_action: ''\ntask_ids: []\nsession_kind: continuity\nstatus: closed\n")
REPAIRED = HEADER + "archived: true\n---\n" + BODY
REPAIRED_WITHOUT_ARCHIVED = HEADER + "---\n" + BODY


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


def _repair(root, monkeypatch, text=REPAIRED):
    path = root / ".taskmaster" / REL
    path.write_text(text, encoding="utf-8")
    assert _adopt(root, monkeypatch) == ()
    return path


def _meta(root):
    with closing(sqlite3.connect(db(root))) as connection:
        return dict(connection.execute("SELECT key,value FROM meta"))


# ── Preflight ────────────────────────────────────────────────────────────────

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
    meta = _meta(project)
    assert meta.get("migration_state", "ready") == "ready" and "migration_token" not in meta
    with closing(sqlite3.connect(db(project))) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (cutover.JOURNAL,)).fetchone()


def test_repaired_and_readopted_file_passes_the_preflight(project, quiesce, monkeypatch):
    _quarantine_headerless(project, monkeypatch)
    _repair(project, monkeypatch)
    assert cutover.dry_run(project)["ok"]
    assert cutover.cutover(project)["ok"]
    doc, body, archived = committed(project)[("handover", IDENT)]
    assert (archived, doc["status"], body) == (True, "closed", BODY.removesuffix("\n"))


def test_a_repair_without_archived_true_brings_the_handover_back_live(project, quiesce, monkeypatch):
    """What the runbook warns about: a file repaired in place is imported from its frontmatter."""
    _quarantine_headerless(project, monkeypatch)
    _repair(project, monkeypatch, REPAIRED_WITHOUT_ARCHIVED)
    assert committed(project)[("handover", IDENT)][2] is False
    assert cutover.cutover(project)["ok"]
    assert committed(project)[("handover", IDENT)][2] is False


def test_a_project_yaml_that_never_parsed_is_refused_until_repaired(project, quiesce, monkeypatch):
    """It gets no projection row, so only the quarantine log (kept on purpose) remembers it."""
    path = project / ".taskmaster" / "project.yaml"
    path.write_text("conventions: [unclosed\n", encoding="utf-8")
    _adopt(project, monkeypatch)
    with closing(sqlite3.connect(db(project))) as connection:
        assert not connection.execute("SELECT 1 FROM projection WHERE file='project.yaml'").fetchone()
    with pytest.raises(cutover.CutoverRefused, match=r"project\.yaml \(.+\)") as refused:
        cutover.cutover(project)
    assert "reason not on record" not in str(refused.value)
    assert any("project.yaml" in r for r in cutover.dry_run(project)["refusals"])
    path.write_text("conventions: []\n", encoding="utf-8")
    _adopt(project, monkeypatch)
    assert cutover.dry_run(project)["ok"]
    assert cutover.cutover(project)["ok"]


def test_a_dirty_quarantined_file_is_flagged_on_repair_and_refused_until_resolved(project, quiesce, monkeypatch):
    """A store change the broken file could not take (dirty + quarantined): re-adopting the
    repair flags it, the cutover refuses the flag, and backlog_resolve_conflict clears it."""
    point_server_at(monkeypatch, project)
    bs.backlog_handover_create(tldr="live notes", next_action="go on", body="Live notes body.")
    store.reset_for_tests()
    with closing(sqlite3.connect(db(project))) as connection:
        ident, rel = connection.execute("SELECT id,file FROM projection WHERE kind='handover'").fetchone()
    path = project / ".taskmaster" / rel
    original = path.read_bytes()
    path.write_bytes(b"Live notes body.\n")  # the frontmatter is lost
    assert _adopt(project, monkeypatch) == (rel,)
    point_server_at(monkeypatch, project)
    bs.backlog_handover_update_status(handover_id=ident, status="closed", reason="done")
    store.reset_for_tests()
    status = store.read_only_status(root=project)
    assert status.stuck_exports == (rel,)
    path.write_bytes(original)  # the repair
    assert _adopt(project, monkeypatch) == ()
    assert store.read_only_status(root=project).flagged_files == (rel,)
    with pytest.raises(cutover.CutoverRefused, match="flagged") as refused:
        cutover.cutover(project)
    assert rel in str(refused.value) and "backlog_resolve_conflict" in str(refused.value)
    assert any(rel in r for r in cutover.dry_run(project)["refusals"])
    point_server_at(monkeypatch, project)
    bs.backlog_resolve_conflict(file=rel, take="store")
    store.reset_for_tests()
    assert cutover.dry_run(project)["ok"]
    assert cutover.cutover(project)["ok"]
    assert committed(project)[("handover", ident)][0]["status"] == "closed"


# ── Under the fence ──────────────────────────────────────────────────────────

@pytest.mark.allow_projection_bypass  # The hook stands in for a hand edit on the cutover's stack.
def test_a_quarantine_created_by_the_reconcile_flush_aborts_before_activation(project, quiesce, monkeypatch):
    """A file broken after the preflight is quarantined by the reconcile flush's scan; the
    fenced re-check aborts, --rollback clears the fence, and a fresh run after repair passes."""
    with closing(sqlite3.connect(db(project))) as connection:
        bug = connection.execute("SELECT file FROM projection WHERE kind='bug'").fetchone()[0]
    bug_path = project / ".taskmaster" / bug
    good = bug_path.read_bytes()

    def break_under_fence(name):
        if name == "reconcile:begin":
            bug_path.write_bytes(b"no frontmatter any more\n")
            with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
                connection.execute("UPDATE projection SET dirty=1 WHERE file='backlog.yaml'")  # the flush runs
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", break_under_fence)
    with pytest.raises(cutover.CutoverAborted) as aborted:
        cutover.cutover(project)
    message = str(aborted.value)
    assert bug in message and "missing or invalid frontmatter" in message and "--rollback" in message
    with closing(sqlite3.connect(db(project))) as connection:
        assert cutover.classify(connection)["authority"] == "legacy"
        assert "activate" not in cutover.classify(connection)["completed_stages"]
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    assert cutover.rollback(project)["ok"]
    with pytest.raises(cutover.CutoverRefused, match="quarantined"):
        cutover.cutover(project)
    bug_path.write_bytes(good)
    point_server_at(monkeypatch, project)
    bs.backlog_bug_list()
    store.reset_for_tests()
    assert cutover.cutover(project)["ok"]


def test_a_quarantine_appearing_before_compare_aborts_a_resume(project, quiesce, monkeypatch):
    """Resume has no preflight of its own for this; the fenced compare stage re-checks."""
    def crash(name):
        if name == "backup:begin":
            raise RuntimeError(name)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        cutover.cutover(project)
    monkeypatch.setitem(cutover.HOOKS, "checkpoint", None)
    with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
        connection.execute("UPDATE projection SET quarantined=1 WHERE kind='bug'")
    with pytest.raises(cutover.CutoverAborted, match=r"bugs/.*quarantined|quarantined.*bugs/"):
        cutover.cutover(project, resume=True)
    with closing(sqlite3.connect(db(project))) as connection:
        assert cutover.classify(connection)["authority"] == "legacy"


# ── After activation: the repaired file in native use ────────────────────────

def _native_client(root):
    from taskmaster.coordinator.client import Client
    return Client(root, autostart=False, timeout=120)


@pytest.mark.allow_projection_bypass
@pytest.mark.xdist_group("heavy_processes")
def test_the_repaired_file_is_unchanged_by_cutover_and_sync_and_git_commits(project, quiesce, monkeypatch):
    from native_git_helpers import git, init_repo
    from taskmaster.coordinator.service import Coordinator
    _quarantine_headerless(project, monkeypatch)
    path = _repair(project, monkeypatch)
    written = path.read_bytes()
    assert cutover.cutover(project)["ok"]
    assert path.read_bytes() == written
    init_repo(project)
    with Coordinator(project):
        client = _native_client(project)
        assert client.sync()["state"] == "synchronized"
        assert path.read_bytes() == written
        result = client.git_run(kind="commit", message="tm: after the cutover")
        assert result["state"] == "completed", result
    assert git(project, "show", f"HEAD:.taskmaster/{REL}").encode().replace(b"\r\n", b"\n") == \
        written.replace(b"\r\n", b"\n")


@pytest.mark.allow_projection_bypass
@pytest.mark.xdist_group("heavy_processes")
def test_after_activation_editing_a_quarantined_file_clears_the_hold(project, quiesce, monkeypatch):
    """Native sync quarantines a file broken after activation and managed Git refuses; editing it
    back clears both. Without a merge base, a repair that differs from the store is flagged."""
    from native_git_helpers import init_repo
    from taskmaster.coordinator.service import Coordinator
    _quarantine_headerless(project, monkeypatch)
    path = _repair(project, monkeypatch)
    good = path.read_bytes()
    assert cutover.cutover(project)["ok"]
    init_repo(project)

    def held():
        with closing(sqlite3.connect(db(project))) as connection:
            quarantined = connection.execute("SELECT quarantined FROM projection WHERE file=?", (REL,)).fetchone()[0]
            flagged = connection.execute("SELECT COUNT(*) FROM projection_conflict WHERE file=?", (REL,)).fetchone()[0]
        return quarantined, flagged
    with Coordinator(project):
        client = _native_client(project)
        assert client.sync()["state"] == "synchronized"
        path.write_bytes(BODY.encode())
        assert client.sync()["state"] != "synchronized" and held() == (1, 0)
        refused = client.git_run(kind="commit", message="tm: blocked")
        assert refused["state"] == "refused" and "not synchronized" in json.dumps(refused)
        path.write_bytes(good)
        assert client.sync()["state"] == "synchronized" and held() == (0, 0)
        assert client.git_run(kind="commit", message="tm: unblocked")["state"] == "completed"
        # The caveat: no merge base, and repaired bytes that differ from the store's version.
        path.write_bytes(BODY.encode())
        client.sync()
        with closing(sqlite3.connect(db(project), isolation_level=None)) as connection:
            connection.execute("DELETE FROM projection_base WHERE file=?", (REL,))
        path.write_bytes(good.replace(b"status: closed", b"status: open"))
        assert client.sync()["state"] != "synchronized"
        assert held()[1] == 1


def test_native_import_parses_headerless_and_repaired_handovers_like_legacy():
    """Native import (`sync_prepare`) and legacy scan share `projection_parse.entity_text`, so the
    file native would quarantine is the one legacy did, and a repaired file stays repaired."""
    with pytest.raises(ValueError, match="missing or invalid frontmatter"):
        projection_parse.authored_rows("handover", IDENT, BODY.encode())
    rows = projection_parse.authored_rows("handover", IDENT, REPAIRED.encode())
    doc, body = rows[("handover", IDENT)]
    assert doc["id"] == IDENT and doc["status"] == "closed" and body == BODY.removesuffix("\n")
