# User intent: prove the derived tables the agent queries — FTS, entity_paths, links,
# related, handover_tasks — are built and maintained inside the SQLite store itself, with
# the path rules that used to live in index.py, so there is one database and no second
# builder to fall out of date.
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.paths import (  # noqa: E402
    REVERSE_TYPE,
    extract_prose_paths,
    infer_repo,
    normalize_location,
    normalize_task_anchor,
)

FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


@pytest.fixture()
def stored_server(tmp_taskmaster):
    """`tmp_taskmaster` with the fixture projection adopted into `local/store.db`."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    shutil.copytree(FIXTURE_SRC, tmp_taskmaster / ".taskmaster", dirs_exist_ok=True)
    bs._load()
    return bs


def con(bs):
    return bs._store().connection


# ── Path helpers (moved from index.py to taskmaster/paths.py) ──


def test_anchor_normalization():
    assert normalize_task_anchor("src/svc/model.py", "api") == ("api/src/svc/model.py", "exact")
    assert normalize_task_anchor("src/svc/", "api") == ("api/src/svc/**", "glob")
    assert normalize_task_anchor("api/src/x.py", "api") == ("api/src/x.py", "exact")
    assert normalize_task_anchor(".\\src\\a.py", None) == ("src/a.py", "exact")


def test_location_strips_line_suffix():
    assert normalize_location("api/src/svc/model.py:42") == "api/src/svc/model.py"


def test_prose_paths_exclude_jsonl_and_urls():
    got = extract_prose_paths(
        "see api/src/svc/other.py and https://x.y/a/b.py and C:\\Users\\x\\abc.jsonl")
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


def test_reverse_type_is_reachable_from_paths():
    assert REVERSE_TYPE["depends_on"] == "blocks"


def test_the_legacy_index_module_is_gone():
    """R9: one database. Nothing may import a second builder back into the tree."""
    assert not (PLUGIN_ROOT / "taskmaster" / "index.py").exists()
    with pytest.raises(ImportError):
        __import__("taskmaster.index")


# ── Derived tables, built by the store ──


def test_entity_paths_carry_anchors_locations_and_prose(stored_server):
    rows = {tuple(r) for r in con(stored_server).execute(
        "SELECT kind,id,path,match_kind,source FROM entity_paths")}
    assert ("task", "eng-001", "api/src/svc/model.py", "exact", "anchors") in rows
    assert ("task", "eng-001", "api/src/svc/**", "glob", "anchors") in rows
    assert ("bug", "B-001", "api/src/svc/model.py", "exact", "location") in rows
    assert ("bug", "B-001", "api/src/svc/other.py", "exact", "prose") in rows
    assert not any(path.endswith(".jsonl") for _, _, path, _, _ in rows)


def test_related_edges(stored_server):
    rel = {tuple(r) for r in con(stored_server).execute("SELECT a_id,b_id,via FROM related")}
    assert ("B-001", "eng-001", "path") in rel        # shared exact path
    assert ("eng-001", "eng-002", "handover") in rel  # same handover task_ids
    assert ("B-001", "B-002", "path") in rel


def test_links_include_the_reverse_edge(stored_server):
    """`eng-001 depends_on eng-002` must also be readable as `eng-002 blocks eng-001`."""
    assert con(stored_server).execute(
        "SELECT 1 FROM links WHERE src_id='eng-002' AND type='blocks' AND dst_id='eng-001' "
        "AND derived=1").fetchone()


def test_link_removal_drops_the_forward_row_and_its_mirror(stored_server):
    """The mirror is owned by no document, so only a re-derivation can retire it."""
    from taskmaster import store  # noqa: PLC0415

    bs = stored_server
    with store.transaction(tool="test-unlink") as tx:
        task = tx.get("task", "eng-001")
        task["depends_on"] = []
        task["links"] = []
        tx.put("task", "eng-001", task)
    assert not con(bs).execute(
        "SELECT 1 FROM links WHERE src_id='eng-001' AND dst_id='eng-002'").fetchone()
    assert not con(bs).execute(
        "SELECT 1 FROM links WHERE src_id='eng-002' AND dst_id='eng-001'").fetchone()


def test_deleting_an_entity_clears_its_derived_rows(stored_server):
    from taskmaster import store  # noqa: PLC0415

    bs = stored_server
    with store.transaction(tool="test-delete") as tx:
        tx.delete("bug", "B-002")
    c = con(bs)
    assert not c.execute("SELECT 1 FROM entity_paths WHERE id='B-002'").fetchone()
    assert not c.execute("SELECT 1 FROM entity_fts WHERE id='B-002'").fetchone()
    assert not c.execute("SELECT 1 FROM related WHERE a_id='B-002' OR b_id='B-002'").fetchone()
    assert not c.execute("SELECT 1 FROM links WHERE src_id='B-002' OR dst_id='B-002'").fetchone()


def test_archiving_keeps_the_derived_rows(stored_server):
    """Archived work still answers search and the edit hook, which both read these tables."""
    bs = stored_server
    bs.backlog_bug_archive("B-002")
    assert con(bs).execute("SELECT 1 FROM entity_paths WHERE id='B-002'").fetchone()
    assert con(bs).execute("SELECT 1 FROM entity_fts WHERE id='B-002'").fetchone()


def test_handover_tasks_rows(stored_server):
    tasks = {r[0] for r in con(stored_server).execute(
        "SELECT task_id FROM handover_tasks WHERE handover_id='2026-09-01-fixture-handover'")}
    assert tasks == {"eng-001", "eng-002"}


def test_fts_covers_branch_docs_and_anchors(stored_server):
    """Field parity with the old substring search: branch, doc paths and anchors are indexed."""
    def ids(match: str) -> list[str]:
        return [r[0] for r in con(stored_server).execute(
            "SELECT id FROM entity_fts WHERE entity_fts MATCH ?", (match,))]

    assert ids('"quokka"') == ["eng-001"]           # from branch design/quokka-rework
    assert "eng-001" in ids('"usage-rework-spec"')  # from docs.spec path
    assert "eng-001" in ids('"svc"')                # from the anchors src/svc/model.py, src/svc/
    assert "eng-001" in ids('"ModelUsageService"')  # from notes


def test_ideas_index_is_not_an_entity(stored_server):
    """`ideas/IDEAS.md` is derived output; indexing it put a phantom `IDEAS` idea in search."""
    assert (stored_server.ROOT / ".taskmaster" / "ideas" / "IDEAS.md").exists()
    assert not con(stored_server).execute(
        "SELECT 1 FROM entities WHERE kind='idea' AND id='IDEAS'").fetchone()
    assert not con(stored_server).execute(
        "SELECT 1 FROM entity_fts WHERE kind='idea' AND id='IDEAS'").fetchone()


def test_rebuild_derived_reproduces_the_same_rows(stored_server):
    from taskmaster import store  # noqa: PLC0415

    tables = ("entity_paths", "links", "related", "handover_tasks")
    before = {t: sorted(map(tuple, con(stored_server).execute(f"SELECT * FROM {t}"))) for t in tables}
    st = stored_server._store()
    st.rebuild_derived()
    after = {t: sorted(map(tuple, con(stored_server).execute(f"SELECT * FROM {t}"))) for t in tables}
    assert after == before
    assert store.DERIVED_TABLES  # the report and the rebuild share one list


def test_derived_status_reports_counts_and_last_rebuild(stored_server):
    st = stored_server._store()
    status = st.derived_status()
    assert status["rebuilt_at"] is None
    assert status["row_counts"]["entity_paths"] == 9
    assert status["row_counts"]["entity_fts"] > 0
    st.rebuild_derived()
    assert st.derived_status()["rebuilt_at"]


# ── backlog_index_status ──


def test_status_reports_rows_and_the_store_path(stored_server):
    out = stored_server.backlog_index_status()
    assert "Store: " in out and "store.db" in out
    assert "entities=12" in out and "entity_fts=12" in out
    assert "Rebuilt: never" in out


def test_status_rebuild_recomputes_and_stamps(stored_server):
    out = stored_server.backlog_index_status(rebuild=True)
    assert "Rebuilt: 20" in out  # an ISO timestamp, not "never"
    assert "entity_paths=9" in out


def test_status_rebuild_never_touches_the_authoritative_tables(stored_server):
    c = con(stored_server)
    before = (
        sorted(map(tuple, c.execute("SELECT kind,id,rev FROM entities"))),
        c.execute("SELECT COUNT(*) FROM changes").fetchone()[0],
        sorted(map(tuple, c.execute("SELECT file,content_hash FROM projection"))),
    )
    stored_server.backlog_index_status(rebuild=True)
    after = (
        sorted(map(tuple, c.execute("SELECT kind,id,rev FROM entities"))),
        c.execute("SELECT COUNT(*) FROM changes").fetchone()[0],
        sorted(map(tuple, c.execute("SELECT file,content_hash FROM projection"))),
    )
    assert after == before


def test_status_rebuild_sweeps_a_legacy_index_db(stored_server, tmp_taskmaster):
    """R9: `local/index.db` and its log are unlinked on rebuild and ignored otherwise."""
    local = tmp_taskmaster / ".taskmaster" / "local"
    stale_db = local / "index.db"
    stale_log = local / "index.log"
    stale_db.write_bytes(b"not a database")
    stale_log.write_text("old\n", encoding="utf-8")

    assert "Store: " in stored_server.backlog_index_status()  # ignored, not read
    assert stale_db.exists()

    stored_server.backlog_index_status(rebuild=True)
    assert not stale_db.exists() and not stale_log.exists()


def test_no_build_index_cli_remains(stored_server):
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    assert not hasattr(bs, "_build_index_cli")
    hook = (PLUGIN_ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    assert "--build-index" not in hook
