# User intent: prove the three maintenance scripts commit through the SQLite
# store — one transaction per run, bodies preserved, a dry run that writes
# nothing, and a second run that is a no-op — so a migration can never race a
# concurrent edit or leave the projection with two writers.
"""Store-boundary tests for `scripts/` (spec §4.6, plan §5.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskmaster import store
from taskmaster.taskmaster_v3 import BODY_KEY, entity_links

from scripts import backfill_tldr as backfill_script
from scripts import migrate_handover_statuses as handover_script
from scripts import migrate_links as links_script

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


# ── helpers ────────────────────────────────────────────────────────────────


def _bp(root: Path) -> Path:
    return root / ".taskmaster"


def _connection(root: Path):
    return store.open_store(_bp(root)).connection


def _changes(root: Path, tool: str) -> list[tuple[str, str, str]]:
    """`(kind, id, op)` for every change row this tool committed."""
    return [
        (row["kind"], row["id"], row["op"])
        for row in _connection(root).execute(
            "SELECT kind,id,op FROM changes WHERE tool=? ORDER BY seq", (tool,)
        )
    ]


def _row(root: Path, kind: str, ident: str) -> tuple[dict, str | None]:
    row = _connection(root).execute(
        "SELECT doc,body FROM entities WHERE kind=? AND id=? AND deleted=0",
        (kind, ident),
    ).fetchone()
    assert row is not None, f"{kind} {ident} missing from committed state"
    return json.loads(row["doc"]), row["body"]


def _backlog_doc(root: Path) -> dict:
    return _row(root, "backlog", "__backlog__")[0]


@pytest.fixture()
def transactions(monkeypatch):
    """Records the `tool` of every store transaction opened during a test."""
    opened: list[str] = []
    real = store.Store.transaction

    def spy(self, *, tool, **kwargs):
        opened.append(tool)
        return real(self, tool=tool, **kwargs)

    monkeypatch.setattr(store.Store, "transaction", spy)
    return opened


def _seed(root: Path, kind: str, doc: dict, body: str | None) -> str:
    """Create one entity row through the store, as a real tool would."""
    with store.transaction(tool="tests/seed", backlog_path=_bp(root)) as tx:
        return tx.create(kind, doc, body=body)


def _strip(root: Path, kind: str, ident: str, *fields: str) -> None:
    """Remove frontmatter fields from a committed row, keeping its body."""
    with store.transaction(tool="tests/seed", backlog_path=_bp(root)) as tx:
        doc = tx.get(kind, ident)
        body = doc.pop(BODY_KEY, None)
        for field in fields:
            doc.pop(field, None)
        tx.put(kind, ident, doc, body=body)


def _seed_task(root: Path, ident: str, **fields) -> None:
    doc = {"id": ident, "status": "todo", "epic": "test-epic", "phase": "dev"}
    doc.update(fields)
    _seed(root, "task", doc, None)


# ── the guard now covers scripts/ ──────────────────────────────────────────


def test_a_raw_projection_write_from_scripts_trips_the_guard(tm_epic_phase):
    """`scripts/` joins `taskmaster/` and `hooks/` as a guarded entry point."""
    target = _bp(tm_epic_phase) / "tasks" / "T-guarded.md"
    code = compile(
        "target.write_text('x', encoding='utf-8')",
        str(SCRIPTS_DIR / "not_a_real_script.py"),
        "exec",
    )
    with pytest.raises(AssertionError, match="projection bypass"):
        exec(code, {"target": target})


def test_scripts_carry_no_projection_writer():
    """No script reaches the projection with a raw writer any more."""
    forbidden = ("write_text", "write_bytes", "save_v3", "save_v4",
                 "atomic_write", "write_entity_anywhere", "write_task_file")
    for name in ("backfill_tldr.py", "migrate_links.py",
                 "migrate_handover_statuses.py"):
        source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} still calls {token}"


# ── backfill_tldr ──────────────────────────────────────────────────────────


@pytest.fixture()
def missing_tldrs(tm_epic_phase):
    root = tm_epic_phase
    _seed_task(root, "T-001", title="Legacy task", tldr="placeholder")
    _strip(root, "task", "T-001", "tldr", "tldr_autogen")
    _seed(root, "issue", {"id": "ISS-001", "title": "Legacy issue",
                          "severity": "P2", "status": "open"},
          "Issue body. Second sentence.")
    _seed(root, "idea", {"id": "IDEA-001", "title": "Legacy idea",
                         "status": "open"},
          "Idea body. Second sentence.")
    return root


def test_backfill_writes_every_missing_tldr_through_the_store(
    missing_tldrs, transactions
):
    root = missing_tldrs
    assert backfill_script.main(["--root", str(root)]) == 0

    assert transactions.count("scripts/backfill_tldr") == 1
    touched = {(kind, ident) for kind, ident, _op in
               _changes(root, "scripts/backfill_tldr")}
    assert touched == {("task", "T-001"), ("issue", "ISS-001"),
                       ("idea", "IDEA-001")}

    for kind, ident in touched:
        doc, _body = _row(root, kind, ident)
        assert doc["tldr"], f"{kind} {ident} still has no tldr"
        assert doc["tldr_autogen"] is True


def test_backfill_preserves_bodies(missing_tldrs):
    root = missing_tldrs
    assert backfill_script.main(["--root", str(root)]) == 0
    _doc, body = _row(root, "issue", "ISS-001")
    assert body == "Issue body. Second sentence."


def test_backfill_is_idempotent(missing_tldrs, capsys):
    root = missing_tldrs
    assert backfill_script.main(["--root", str(root)]) == 0
    first = _changes(root, "scripts/backfill_tldr")
    capsys.readouterr()

    assert backfill_script.main(["--root", str(root)]) == 0
    assert _changes(root, "scripts/backfill_tldr") == first
    assert "nothing to do" in capsys.readouterr().out.lower()


def test_backfill_dry_run_opens_no_write_transaction(missing_tldrs, transactions, capsys):
    root = missing_tldrs
    assert backfill_script.main(["--root", str(root), "--dry-run"]) == 0

    assert "scripts/backfill_tldr" not in transactions
    assert _changes(root, "scripts/backfill_tldr") == []
    assert "tldr" not in _row(root, "issue", "ISS-001")[0]
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert "ISS-001" in out


# ── migrate_links ──────────────────────────────────────────────────────────


@pytest.fixture()
def legacy_links(tm_epic_phase):
    root = tm_epic_phase
    _seed_task(root, "T-001", title="First", tldr="a",
               depends_on=["T-002"], related_issues=["ISS-001"])
    _seed_task(root, "T-002", title="Second", tldr="b")
    _seed(root, "issue", {"id": "ISS-001", "title": "Bug", "tldr": "x",
                          "severity": "P2", "status": "open",
                          "fixed_in_task": "T-001"},
          "Issue body.")
    return root


def test_migrate_links_translates_and_reconciles_in_one_transaction(
    legacy_links, transactions, capsys
):
    root = legacy_links
    assert links_script.main(["--root", str(root)]) == 0
    assert transactions.count("scripts/migrate_links") == 1

    t1 = _row(root, "task", "T-001")[0]
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)
    assert {"type": "relates_to", "target": "ISS-001"} in entity_links(t1)
    assert {"type": "fixes", "target": "ISS-001"} in entity_links(t1)
    assert t1["depends_on"] == ["T-002"]  # live schema, kept
    assert "related_issues" not in t1

    t2 = _row(root, "task", "T-002")[0]
    assert {"type": "blocks", "target": "T-001"} in entity_links(t2)

    issue_doc, issue_body = _row(root, "issue", "ISS-001")
    assert {"type": "fixed_in_task", "target": "T-001"} in entity_links(issue_doc)
    assert "fixed_in_task" not in issue_doc
    assert issue_body == "Issue body."

    summary = json.loads(capsys.readouterr().out)
    assert summary["migrated"]["tasks"] == 1
    assert summary["migrated"]["issues"] == 1
    assert summary["reconcile"]["orphans"] == []


def test_migrate_links_keeps_depends_on_and_the_dependency_gate(
    tm_epic_phase, capsys
):
    """`depends_on` is live schema, not a legacy field to trim.

    Every dependency gate (next_available, pick, validate, blast radius) reads
    the scalar field and never the `links` array, so dropping it on migration
    would silently unblock every task in the backlog.
    """
    from taskmaster import backlog_server as bs

    root = tm_epic_phase
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev",
                        tldr="b", options={"task_id": "T-002"})
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev",
                        tldr="a", depends_on="T-002",
                        options={"task_id": "T-001"})
    assert "`T-002`" in bs.backlog_dependencies("T-001")

    assert links_script.main(["--root", str(root)]) == 0
    capsys.readouterr()

    t1 = _row(root, "task", "T-001")[0]
    assert t1["depends_on"] == ["T-002"]
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)

    # The gate must still see the dependency after the migration.
    after = bs.backlog_dependencies("T-001")
    assert "Depends on (upstream)" in after
    assert "`T-002`" in after
    assert "`T-001`" in bs.backlog_dependencies("T-002")  # downstream, too


def test_migrate_links_keeps_depends_on_on_a_second_run(legacy_links, capsys):
    """A re-run neither drops the field nor rewrites anything."""
    root = legacy_links
    assert links_script.main(["--root", str(root)]) == 0
    first = _changes(root, "scripts/migrate_links")
    capsys.readouterr()

    assert links_script.main(["--root", str(root)]) == 0
    assert _changes(root, "scripts/migrate_links") == first
    assert json.loads(capsys.readouterr().out)["status"] == "no changes"
    assert _row(root, "task", "T-001")[0]["depends_on"] == ["T-002"]


def test_migrate_links_reports_an_orphan_target(tm_epic_phase, capsys):
    root = tm_epic_phase
    _seed_task(root, "T-001", title="First", tldr="a", depends_on=["T-404"])
    assert links_script.main(["--root", str(root)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["reconcile"]["orphans"] == [
        {"source": "T-001", "target": "T-404", "type": "depends_on"}
    ]


def test_migrate_links_is_idempotent(legacy_links, capsys):
    root = legacy_links
    assert links_script.main(["--root", str(root)]) == 0
    first = _changes(root, "scripts/migrate_links")
    capsys.readouterr()

    assert links_script.main(["--root", str(root)]) == 0
    assert _changes(root, "scripts/migrate_links") == first
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "no changes"
    assert entity_links(_row(root, "task", "T-001")[0]).count(
        {"type": "depends_on", "target": "T-002"}
    ) == 1


def test_migrate_links_keep_legacy_leaves_the_old_fields(legacy_links):
    root = legacy_links
    assert links_script.main(["--root", str(root), "--keep-legacy"]) == 0
    t1 = _row(root, "task", "T-001")[0]
    assert t1["depends_on"] == ["T-002"]
    assert {"type": "depends_on", "target": "T-002"} in entity_links(t1)


def test_migrate_links_dry_run_opens_no_write_transaction(
    legacy_links, transactions, capsys
):
    root = legacy_links
    assert links_script.main(["--root", str(root), "--dry-run"]) == 0

    assert "scripts/migrate_links" not in transactions
    assert _changes(root, "scripts/migrate_links") == []
    assert "depends_on" in _row(root, "task", "T-001")[0]
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True
    assert summary["would_write"] == ["ISS-001", "T-001", "T-002"]


# ── migrate_handover_statuses ──────────────────────────────────────────────


@pytest.fixture()
def legacy_handovers(tm_epic_phase):
    root = tm_epic_phase
    _seed_task(root, "T-001", title="Finished", tldr="a", status="todo")
    _seed(root, "handover",
          {"id": "HND-2026-01-01-alpha", "tldr": "Alpha", "status": "todo",
           "next_action": "keep going", "created": "2026-01-01T00:00",
           "session_kind": "implementation", "task_ids": ["T-001"]},
          "Handover body.")
    return root


def test_handover_migration_rewrites_statuses_in_one_transaction(
    legacy_handovers, transactions, capsys
):
    root = legacy_handovers
    assert handover_script.main(["--root", str(root)]) == 0
    assert transactions.count("scripts/migrate_handover_statuses") == 1

    doc, body = _row(root, "handover", "HND-2026-01-01-alpha")
    assert doc["status"] == "open"
    assert doc["status_reason"] == "migrated from legacy enum"
    assert body == "Handover body."
    assert _backlog_doc(root)["handover_status_v2_migrated"] is True

    out = capsys.readouterr().out
    assert "HND-2026-01-01-alpha" in out
    assert "backlog_handover_resync" not in out


def test_handover_migration_hands_the_planner_rows_not_a_path(legacy_handovers):
    """Regression: the CLI passed a Path to a planner that takes rows (TypeError)."""
    assert handover_script.main(["--root", str(legacy_handovers)]) == 0
    assert _row(legacy_handovers, "handover", "HND-2026-01-01-alpha")[0][
        "status"
    ] == "open"


def test_root_defaults_to_the_current_directory(legacy_handovers):
    """The fixture chdirs into the project, so `--root` may be omitted."""
    assert handover_script.main([]) == 0
    assert _backlog_doc(legacy_handovers)["handover_status_v2_migrated"] is True


def test_handover_migration_is_idempotent(legacy_handovers, capsys):
    root = legacy_handovers
    assert handover_script.main(["--root", str(root)]) == 0
    first = _changes(root, "scripts/migrate_handover_statuses")
    capsys.readouterr()

    assert handover_script.main(["--root", str(root)]) == 0
    assert _changes(root, "scripts/migrate_handover_statuses") == first
    assert "nothing to do" in capsys.readouterr().out.lower()


def test_handover_migration_dry_run_opens_no_write_transaction(
    legacy_handovers, transactions, capsys
):
    root = legacy_handovers
    assert handover_script.main(["--root", str(root), "--dry-run"]) == 0

    assert "scripts/migrate_handover_statuses" not in transactions
    assert _changes(root, "scripts/migrate_handover_statuses") == []
    assert _row(root, "handover", "HND-2026-01-01-alpha")[0]["status"] == "todo"
    assert "handover_status_v2_migrated" not in _backlog_doc(root)
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert "HND-2026-01-01-alpha" in out


# ── shared CLI behaviour ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module", [backfill_script, links_script, handover_script],
    ids=["backfill_tldr", "migrate_links", "migrate_handover_statuses"],
)
def test_missing_backlog_is_reported_not_created(module, tmp_path, capsys):
    assert module.main(["--root", str(tmp_path)]) == 2
    assert not (tmp_path / ".taskmaster").exists()
    assert "backlog.yaml" in capsys.readouterr().err
