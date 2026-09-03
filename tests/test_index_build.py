# User intent: prove the derived SQLite index really mirrors the backlog files —
# row counts, path/link/related edges, incremental re-ingest, and safe rebuild on
# corruption — so the agent can trust index.db without ever treating it as canonical.
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.index import (  # noqa: E402
    build_index,
    db_path,
    extract_prose_paths,
    infer_repo,
    last_report,
    normalize_location,
    normalize_task_anchor,
    open_ro,
)

FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


@pytest.fixture()
def fixture_tm(tmp_path):
    """A fresh, writable copy of the index fixture's `.taskmaster/` tree."""
    dest = tmp_path / ".taskmaster"
    shutil.copytree(FIXTURE_SRC, dest)
    return dest


def test_full_build_row_counts(fixture_tm):
    rep = build_index(fixture_tm / "backlog.yaml")
    assert rep.full_rebuild and not rep.stale
    assert rep.row_counts["entities"] == 9   # 2 tasks, 1 epic, 2 bugs, 1 issue, 1 handover, 1 decision, 1 idea
    con = open_ro(fixture_tm / "backlog.yaml")
    assert con.execute("select repo from entities where id='eng-001'").fetchone()[0] == "api"
    assert con.execute("select repo from entities where id='B-001'").fetchone()[0] == "api"
    assert con.execute("select repo from entities where id='ISS-001'").fetchone()[0] == "web"


def test_anchor_normalization():
    assert normalize_task_anchor("src/svc/model.py", "api") == ("api/src/svc/model.py", "exact")
    assert normalize_task_anchor("src/svc/", "api") == ("api/src/svc/**", "glob")
    assert normalize_task_anchor("api/src/x.py", "api") == ("api/src/x.py", "exact")
    assert normalize_task_anchor(".\\src\\a.py", None) == ("src/a.py", "exact")


def test_location_strips_line_suffix():
    assert normalize_location("api/src/svc/model.py:42") == "api/src/svc/model.py"


def test_prose_paths_exclude_jsonl_and_urls():
    got = extract_prose_paths("see api/src/svc/other.py and https://x.y/a/b.py and C:\\Users\\x\\abc.jsonl")
    assert got == ["api/src/svc/other.py"]


def test_prose_paths_keep_the_whole_extension():
    got = extract_prose_paths("web/app/page.tsx, sln/x.csproj, src/y.hpp and logs/abc.jsonl")
    assert got == ["web/app/page.tsx", "sln/x.csproj", "src/y.hpp"]


def test_infer_repo_longest_prefix_wins():
    repos = [("api", "api"), ("api-web", "api/web")]
    assert infer_repo(["api/web/page.tsx"], repos) == "api-web"
    assert infer_repo(["api/svc/x.py"], repos) == "api"
    assert infer_repo(["other/x.py"], repos) is None
    assert infer_repo(["api/svc/x.py"], []) is None


def test_entity_paths_sources(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    rows = set(con.execute("select entity_id, path, match_kind, source from entity_paths").fetchall())
    assert ("eng-001", "api/src/svc/model.py", "exact", "anchors") in rows
    assert ("eng-001", "api/src/svc/**", "glob", "anchors") in rows
    assert ("B-001", "api/src/svc/model.py", "exact", "location") in rows
    assert ("B-001", "api/src/svc/other.py", "exact", "prose") in rows
    assert not any(p.endswith(".jsonl") for _, p, _, _ in rows)


def test_related_edges(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    rel = set(con.execute("select a, b, via from related").fetchall())
    assert ("B-001", "eng-001", "path") in rel          # shared exact path
    assert ("eng-001", "eng-002", "handover") in rel    # same handover task_ids
    assert ("B-001", "B-002", "path") in rel


def test_links_include_reverse(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    assert con.execute("select 1 from links where src='eng-002' and type='blocks' and dst='eng-001'").fetchone()


def test_incremental_reingest_on_mtime(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    bug = fixture_tm / "bugs" / "B-001.md"
    bug.write_text(bug.read_text(encoding="utf-8").replace("status: open", "status: fixed"), encoding="utf-8")
    os.utime(bug, (time.time() + 5, time.time() + 5))
    rep = build_index(bp)
    assert not rep.full_rebuild and rep.files_ingested == 1
    con = open_ro(bp)
    assert con.execute("select status from entities where id='B-001'").fetchone()[0] == "fixed"


def test_removed_file_rows_deleted(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from links where src='B-002'").fetchone()[0] > 0
    (fixture_tm / "bugs" / "B-002.md").unlink()
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from entities where id='B-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from entity_paths where entity_id='B-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from related where a='B-002' or b='B-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from links where src='B-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from links where dst='B-002'").fetchone()[0] == 0


def test_link_removal_drops_forward_row_and_mirror(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    text = bp.read_text(encoding="utf-8")
    bp.write_text(
        text.replace("        links:\n          - type: depends_on\n            target: eng-002\n", ""),
        encoding="utf-8",
    )
    os.utime(bp, (time.time() + 5, time.time() + 5))
    build_index(bp)
    con = open_ro(bp)
    assert con.execute(
        "select count(*) from links where src='eng-001' and dst='eng-002'").fetchone()[0] == 0
    assert con.execute(
        "select count(*) from links where src='eng-002' and dst='eng-001'").fetchone()[0] == 0


def test_project_yaml_change_reingests_repo_inference(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select repo from entities where id='B-001'").fetchone()[0] == "api"
    pf = fixture_tm / "project.yaml"
    pf.write_text(pf.read_text(encoding="utf-8").replace("name: api", "name: svc"), encoding="utf-8")
    os.utime(pf, (time.time() + 5, time.time() + 5))
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select repo from entities where id='B-001'").fetchone()[0] == "svc"


def test_task_detail_file_ingested_alongside_project_yaml(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    detail = fixture_tm / "tasks" / "eng-001.md"
    detail.parent.mkdir(exist_ok=True)
    detail.write_text(
        "---\nid: eng-001\ntitle: Rework model usage accounting\n"
        "notes: heavy notes mention Quokkasaurus\n---\n\nBody.\n",
        encoding="utf-8",
    )
    pf = fixture_tm / "project.yaml"
    pf.write_text(pf.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stamp = time.time() + 5
    for f in (detail, pf):
        os.utime(f, (stamp, stamp))
    build_index(bp)
    con = open_ro(bp)
    ids = [r[0] for r in con.execute("select id from entity_fts where entity_fts match 'Quokkasaurus'")]
    assert ids == ["eng-001"]


def test_v3_task_detail_removal_keeps_the_entity(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    detail = fixture_tm / "tasks" / "eng-001.md"
    detail.parent.mkdir(exist_ok=True)
    detail.write_text(
        "---\nid: eng-001\ntitle: Rework model usage accounting\nnotes: heavy notes\n---\n\nBody.\n",
        encoding="utf-8",
    )
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from entities where id='eng-001'").fetchone()[0] == 1
    detail.unlink()
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from entities where id='eng-001'").fetchone()[0] == 1
    assert con.execute("select file from entities where id='eng-001'").fetchone()[0] == "backlog.yaml"


def test_string_valued_list_fields_are_tolerated(fixture_tm):
    bug = fixture_tm / "bugs" / "B-003.md"
    bug.write_text(
        "---\nid: B-003\ntitle: Solo location\nstatus: open\nseverity: low\n"
        "discovered: '2026-08-20T00:00:00Z'\nlocation: api/src/svc/solo.py\n---\n\nBody.\n",
        encoding="utf-8",
    )
    build_index(fixture_tm / "backlog.yaml")
    con = open_ro(fixture_tm / "backlog.yaml")
    rows = con.execute(
        "select path from entity_paths where entity_id='B-003' and source='location'").fetchall()
    assert [r[0] for r in rows] == ["api/src/svc/solo.py"]


def test_read_succeeds_while_a_write_transaction_is_open(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    writer = sqlite3.connect(db_path(bp))
    try:
        writer.execute("begin immediate")
        writer.execute("insert into meta(key, value) values ('probe','1')")
        con = open_ro(bp)
        assert con.execute("select count(*) from entities").fetchone()[0] == 9
    finally:
        writer.rollback()
        writer.close()


def test_task_removed_from_backlog_is_dropped(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    text = bp.read_text(encoding="utf-8")
    head, _, _ = text.partition("      - id: eng-002")
    bp.write_text(head + "phases:\n  - id: dev\n    name: Development\ncontext: {}\n", encoding="utf-8")
    os.utime(bp, (time.time() + 5, time.time() + 5))
    build_index(bp)
    con = open_ro(bp)
    assert con.execute("select count(*) from entities where id='eng-002'").fetchone()[0] == 0
    assert con.execute("select count(*) from entities where id='eng-001'").fetchone()[0] == 1


def test_budget_marks_stale(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    rep = build_index(bp, budget_s=0.0)
    assert rep.stale and rep.pending_files


def _meta(bp, key):
    row = open_ro(bp).execute("select value from meta where key=?", (key,)).fetchone()
    return row[0] if row else None


def test_stale_build_does_not_advance_source_mtime_max(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    before = _meta(bp, "source_mtime_max")
    bug = fixture_tm / "bugs" / "B-001.md"
    os.utime(bug, (time.time() + 500, time.time() + 500))
    rep = build_index(bp, budget_s=0.0)
    assert rep.stale
    assert _meta(bp, "source_mtime_max") == before


def test_first_build_under_budget_records_no_source_mtime_max(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    rep = build_index(bp, budget_s=0.0)
    assert rep.stale
    assert _meta(bp, "source_mtime_max") is None


def test_schema_bump_forces_full_rebuild(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = sqlite3.connect(db_path(bp)); con.execute("update meta set value='0' where key='schema_version'"); con.commit(); con.close()
    assert build_index(bp).full_rebuild


def test_corrupt_db_rebuilds(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    db_path(bp).write_bytes(b"not a database")
    assert build_index(bp).full_rebuild


def test_fts_ranks_handover(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = open_ro(bp)
    ids = [r[0] for r in con.execute("select id from entity_fts where entity_fts match 'ModelUsageService' order by bm25(entity_fts)")]
    assert "eng-001" in ids


def test_handover_rows_and_tasks(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    build_index(bp)
    con = open_ro(bp)
    hid = "2026-09-01-fixture-handover"
    row = con.execute("select thread, tldr, next_action, branch from handovers where id=?", (hid,)).fetchone()
    assert row[0] == "fixture-thread"
    assert row[1] == "Usage accounting rework is half done"
    assert row[3] == "design/fixture"
    tasks = {r[0] for r in con.execute("select task_id from handover_tasks where handover_id=?", (hid,))}
    assert tasks == {"eng-001", "eng-002"}


def test_last_report_round_trips(fixture_tm):
    bp = fixture_tm / "backlog.yaml"
    rep = build_index(bp)
    stored = last_report(bp)
    assert stored is not None
    assert stored.row_counts == rep.row_counts
    assert stored.built_at == rep.built_at


def test_local_gitignore_written(fixture_tm):
    build_index(fixture_tm / "backlog.yaml")
    assert (fixture_tm / "local" / ".gitignore").read_text() == "*\n"


def test_cli_prints_report(fixture_tm):
    out = subprocess.run([sys.executable, "-m", "taskmaster.index", str(fixture_tm.parent)], capture_output=True, text=True, cwd=PLUGIN_ROOT)
    assert out.returncode == 0 and json.loads(out.stdout)["row_counts"]["entities"] == 9
