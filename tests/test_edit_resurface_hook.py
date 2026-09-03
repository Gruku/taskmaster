# User intent: prove the ambient edit hook surfaces exactly one line of open backlog
# work for the file just edited — right ids, right order, silent when there is nothing
# open — so the agent never edits a file blind to the bugs/tasks pointing at it.
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

from taskmaster.index import SCHEMA_SQL, build_index  # noqa: E402

HOOK = str((PLUGIN_ROOT / "hooks" / "edit_resurface.py").resolve())
FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


# ── Harness (subprocess, mirrors test_worktree_submodule_init_hook.py) ──


def run(payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(cwd),
        timeout=60,
    )


def payload(root, rel, tool="Edit", session="s1"):
    return {"session_id": session, "cwd": str(root), "tool_name": tool,
            "tool_input": {"file_path": str(root / rel)}, "tool_response": {"success": True}}


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE_SRC, root / ".taskmaster")
    for rel in ("api/src/svc/model.py", "api/src/svc/legacy.py",
                "api/src/svc/other.py", "api/src/svc/deep/new.py",
                ".worktrees/wt-1/api/src/svc/model.py"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return root


@pytest.fixture()
def fixture_root_without_index(tmp_path):
    return _fixture_root(tmp_path)


@pytest.fixture()
def indexed_root(fixture_root_without_index):
    build_index(fixture_root_without_index / ".taskmaster" / "backlog.yaml")
    return fixture_root_without_index


def context(result: subprocess.CompletedProcess) -> str:
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return out["hookSpecificOutput"]["additionalContext"]


# ── Behaviour ───────────────────────────────────────────────────


def test_open_items_one_line(indexed_root):
    r = run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    ctx = context(r)
    assert ctx.startswith(
        "TM: api/src/svc/model.py → B-001 open, eng-001 in-progress, 2026-09-01-fixture-hando…")
    assert "(+1 closed" in ctx and "\n" not in ctx


def test_closed_only_is_silent(tmp_path):
    """Closed history lives behind backlog_query — it never costs a context line."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "fixed", "svc/legacy.py", "exact", "location"),
        ("t-900", "task", "done", "svc/legacy.py", "exact", "anchors"),
    ])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "legacy.py").write_text("x", encoding="utf-8")
    r = run(payload(root, "svc/legacy.py"), root)
    assert r.stdout == ""
    assert r.returncode == 0


def test_closed_items_counted_beside_open_ones(indexed_root):
    # legacy.py: B-002 (fixed) by location, eng-001 (in-progress) by anchor glob.
    ctx = context(run(payload(indexed_root, "api/src/svc/legacy.py"), indexed_root))
    assert ctx == "TM: api/src/svc/legacy.py → eng-001 in-progress (+1 closed)"


def test_glob_anchor_matches_nested(indexed_root):
    r = run(payload(indexed_root, "api/src/svc/deep/new.py"), indexed_root)
    assert "eng-001 in-progress" in context(r)


def test_prose_match_counted_not_listed(indexed_root):
    """other.py matches eng-001 by anchor glob and B-001 only in prose.

    B-001 is open, but a prose mention is not a claim about the file, so it
    is counted and never named.
    """
    ctx = context(run(payload(indexed_root, "api/src/svc/other.py"), indexed_root))
    assert ctx == "TM: api/src/svc/other.py → eng-001 in-progress (+1 prose)"


def test_prose_only_open_match_is_silent(tmp_path):
    """A file whose only open match is prose-derived produces no line at all."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/only_prose.py", "exact", "prose"),
    ])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "only_prose.py").write_text("x", encoding="utf-8")
    assert run(payload(root, "svc/only_prose.py"), root).stdout == ""


def test_worktree_prefix_stripped(indexed_root):
    r = run(payload(indexed_root, ".worktrees/wt-1/api/src/svc/model.py"), indexed_root)
    ctx = context(r)
    assert ctx.startswith("TM: api/src/svc/model.py → ")
    assert "B-001 open" in ctx


def test_dedupe_per_session(indexed_root):
    run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    r = run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    assert r.stdout == ""
    r2 = run(payload(indexed_root, "api/src/svc/model.py", session="s2"), indexed_root)
    assert r2.stdout != ""


def test_dedupe_prunes_seen_files_older_than_seven_days(indexed_root):
    seen_dir = indexed_root / ".taskmaster" / "local" / "hook-seen"
    seen_dir.mkdir(parents=True, exist_ok=True)
    stale = seen_dir / "old-session.json"
    stale.write_text("[]", encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(stale, (old, old))
    run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    assert not stale.exists()
    assert (seen_dir / "s1.json").exists()


def test_outside_root_and_taskmaster_dir_skipped(indexed_root, tmp_path):
    assert run(payload(indexed_root, ".taskmaster/bugs/B-001.md"), indexed_root).stdout == ""
    other = tmp_path / "elsewhere.py"
    other.write_text("x", encoding="utf-8")
    p = payload(indexed_root, "x")
    p["tool_input"]["file_path"] = str(other)
    assert run(p, indexed_root).stdout == ""


def test_missing_db_silent(fixture_root_without_index):
    r = run(payload(fixture_root_without_index, "api/src/svc/model.py"),
            fixture_root_without_index)
    assert r.stdout == ""
    assert r.returncode == 0


def test_no_backlog_anywhere_is_silent(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "a.py").write_text("x", encoding="utf-8")
    assert run(payload(lone, "a.py"), lone).stdout == ""


def test_stale_flag(indexed_root):
    bl = indexed_root / ".taskmaster" / "backlog.yaml"
    future = time.time() + 60
    os.utime(bl, (future, future))
    assert "(index stale)" in run(payload(indexed_root, "api/src/svc/model.py"),
                                  indexed_root).stdout


def test_stale_flag_from_last_report(indexed_root):
    """A budget-truncated build still stamps a fresh built_at, so the report flag
    is the only signal that the index is incomplete."""
    db = indexed_root / ".taskmaster" / "local" / "index.db"
    con = sqlite3.connect(db)
    report = json.loads(con.execute(
        "select value from meta where key='last_report'").fetchone()[0])
    report["stale"] = True
    con.execute("update meta set value=? where key='last_report'", (json.dumps(report),))
    con.commit()
    con.close()
    assert "(index stale)" in run(payload(indexed_root, "api/src/svc/model.py"),
                                  indexed_root).stdout


def test_fresh_index_is_not_marked_stale(indexed_root):
    ctx = context(run(payload(indexed_root, "api/src/svc/model.py"), indexed_root))
    assert "(index stale)" not in ctx


def test_non_edit_tool_ignored(indexed_root):
    assert run(payload(indexed_root, "api/src/svc/model.py", tool="Read"),
               indexed_root).stdout == ""


def test_failed_tool_call_ignored(indexed_root):
    p = payload(indexed_root, "api/src/svc/model.py")
    p["tool_response"] = {"success": False}
    assert run(p, indexed_root).stdout == ""


def test_malformed_stdin_exit_zero(indexed_root):
    r = subprocess.run([sys.executable, HOOK], input="not json", text=True,
                       capture_output=True, cwd=str(indexed_root), timeout=60)
    assert r.returncode == 0 and r.stdout == ""


def test_missing_session_id_uses_nosession(indexed_root):
    p = payload(indexed_root, "api/src/svc/model.py")
    del p["session_id"]
    assert run(p, indexed_root).stdout != ""
    assert (indexed_root / ".taskmaster" / "local" / "hook-seen" / "nosession.json").exists()


def test_hook_imports_only_stdlib():
    """Hooks run under system Python, not the uv venv — a yaml/fastmcp/taskmaster
    import would make the hook dead on every machine without the venv."""
    src = Path(HOOK).read_text(encoding="utf-8")
    for banned in ("import yaml", "import fastmcp", "from taskmaster", "import taskmaster"):
        assert banned not in src


def test_corrupt_db_is_silent_and_logged(indexed_root):
    db = indexed_root / ".taskmaster" / "local" / "index.db"
    db.write_bytes(b"not a database at all")
    r = run(payload(indexed_root, "api/src/svc/model.py"), indexed_root)
    assert r.stdout == "" and r.returncode == 0
    assert (indexed_root / ".taskmaster" / "local" / "hook.log").exists()


# ── In-process unit tests (resolve / format_line) ───────────────


def _load_hook_module():
    from importlib import util
    spec = util.spec_from_file_location("edit_resurface", HOOK)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic_root(tmp_path: Path, rows) -> Path:
    """A project root whose index.db holds exactly `rows`.

    rows: (entity_id, kind, status, path, match_kind, source)
    """
    root = tmp_path / "syn"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    (root / ".taskmaster" / "backlog.yaml").write_text("version: 3\n", encoding="utf-8")
    con = sqlite3.connect(root / ".taskmaster" / "local" / "index.db")
    con.executescript(SCHEMA_SQL)
    seen = set()
    for eid, kind, status, path, mk, source in rows:
        if eid not in seen:
            seen.add(eid)
            con.execute("insert into entities(id,kind,status) values (?,?,?)",
                        (eid, kind, status))
        con.execute("insert into entity_paths values (?,?,?,?)", (eid, path, mk, source))
    con.execute("insert into meta values ('built_at_epoch', ?)", (str(time.time() + 3600),))
    con.execute("insert into meta values ('source_mtime_max', ?)",
                (str(time.time() + 3600),))
    con.commit()
    con.close()
    return root


def test_format_orders_bugs_issues_tasks_handovers(tmp_path):
    mod = _load_hook_module()
    rows = [(eid, kind, status, "a/b.py", "exact", src) for eid, kind, status, src in [
        ("t-1", "task", "todo", "anchors"),
        ("HND-x", "handover", "open", "prose"),
        ("B-1", "bug", "open", "location"),
        ("ISS-1", "issue", "investigating", "location"),
    ]]
    root = _synthetic_root(tmp_path, rows)
    res = mod.resolve(root / ".taskmaster" / "local" / "index.db", "a/b.py")
    assert mod.format_line("a/b.py", res, False) == (
        "TM: a/b.py → B-1 open, ISS-1 investigating, t-1 todo, HND-x")


def test_format_caps_at_six_ids(tmp_path):
    mod = _load_hook_module()
    rows = [(f"B-{i:02d}", "bug", "open", "a/b.py", "exact", "location") for i in range(9)]
    root = _synthetic_root(tmp_path, rows)
    res = mod.resolve(root / ".taskmaster" / "local" / "index.db", "a/b.py")
    line = mod.format_line("a/b.py", res, False)
    assert line.endswith("+3 more")
    assert line.count(" open") == 6


def test_format_stale_and_counts(tmp_path):
    mod = _load_hook_module()
    rows = [
        ("B-1", "bug", "open", "a/b.py", "exact", "location"),
        ("B-2", "bug", "fixed", "a/b.py", "exact", "location"),
        ("DEC-1", "decision", "open", "a/b.py", "exact", "prose"),
    ]
    root = _synthetic_root(tmp_path, rows)
    res = mod.resolve(root / ".taskmaster" / "local" / "index.db", "a/b.py")
    assert mod.format_line("a/b.py", res, True) == (
        "TM: a/b.py → B-1 open (+1 closed, +1 prose) (index stale)")


def test_open_status_vocab(tmp_path):
    mod = _load_hook_module()
    rows = [
        ("t-todo", "task", "todo", "a/b.py", "exact", "anchors"),
        ("t-review", "task", "in-review", "a/b.py", "exact", "anchors"),
        ("t-blocked", "task", "blocked", "a/b.py", "exact", "anchors"),
        ("t-done", "task", "done", "a/b.py", "exact", "anchors"),
        ("B-adopted", "bug", "adopted", "a/b.py", "exact", "location"),
        ("B-wontfix", "bug", "wontfix", "a/b.py", "exact", "location"),
        ("I-inv", "issue", "investigating", "a/b.py", "exact", "location"),
        ("I-fixed", "issue", "fixed", "a/b.py", "exact", "location"),
    ]
    root = _synthetic_root(tmp_path, rows)
    res = mod.resolve(root / ".taskmaster" / "local" / "index.db", "a/b.py")
    assert [e.id for e in res.listed] == [
        "B-adopted", "I-inv", "t-blocked", "t-review", "t-todo"]
    assert res.closed == 3


def test_query_budget_under_50ms(tmp_path):
    mod = _load_hook_module()
    db = tmp_path / "index.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA_SQL)
    con.executemany("insert into entities(id,kind,status) values (?,?,?)",
                    [(f"t-{i}", "task", "todo" if i % 2 else "done") for i in range(3000)])
    con.executemany(
        "insert into entity_paths values (?,?,?,?)",
        [(f"t-{i}", f"repo{i % 7}/src/mod{i}/file{i}.py", "exact", "anchors")
         for i in range(3000)]
        + [(f"t-{i}", f"repo{i % 7}/src/area{i % 50}/**", "glob", "anchors")
           for i in range(0, 3000, 10)]
        + [(f"t-{i}", f"repo{i % 7}/src/mod{i}/extra.py", "exact", "prose")
           for i in range(3000)])
    con.execute("insert into meta values ('built_at_epoch', ?)", (str(time.time()),))
    con.commit()
    con.close()
    t = time.perf_counter()
    mod.resolve(db, "repo1/src/area1/x.py")
    assert (time.perf_counter() - t) < 0.05
