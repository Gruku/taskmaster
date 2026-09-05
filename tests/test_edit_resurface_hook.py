# User intent: prove the ambient edit hook surfaces exactly one line of open backlog
# work for the file just edited — right ids, right order, silent when there is nothing
# open — so the agent never edits a file blind to the bugs/tasks pointing at it. It
# reads the SQLite store the server writes and must never import or rebuild anything.
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

from taskmaster import store  # noqa: E402

HOOK = str((PLUGIN_ROOT / "hooks" / "edit_resurface.py").resolve())
FIXTURE_SRC = PLUGIN_ROOT / "tests" / "fixtures" / "index_backlog" / ".taskmaster"


# ── Harness (subprocess, mirrors test_worktree_submodule_init_hook.py) ──


def run(payload: dict, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
    )


def payload(root, rel, tool="Edit", session="s1"):
    return {"session_id": session, "cwd": str(root), "tool_name": tool,
            "tool_input": {"file_path": str(root / rel)}, "tool_response": {"success": True}}


def _hook_env(root: Path) -> dict:
    """Environment that pins root resolution at `root` without a git repo.

    The hook shares `taskmaster.root.resolve_root`, whose first rule is
    `TASKMASTER_ROOT`; without it a tmp_path under a checkout would resolve to
    the checkout's own git common dir instead of the fixture.
    """
    env = dict(os.environ)
    env["TASKMASTER_ROOT"] = str(root)
    return env


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
def fixture_root_without_store(tmp_path):
    return _fixture_root(tmp_path)


@pytest.fixture()
def stored_root(fixture_root_without_store, monkeypatch):
    """The fixture projection adopted into `local/store.db` by the store itself."""
    monkeypatch.setenv("TASKMASTER_ROOT", str(fixture_root_without_store))
    store.reset_for_tests()
    store.open_store(root=fixture_root_without_store, session="fixture-adopt")
    store.reset_for_tests()
    return fixture_root_without_store


def context(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return out["hookSpecificOutput"]["additionalContext"]


def _run(root: Path, rel: str, **kwargs) -> subprocess.CompletedProcess:
    session = kwargs.pop("session", "s1")
    tool = kwargs.pop("tool", "Edit")
    return run(payload(root, rel, tool=tool, session=session), root, env=_hook_env(root))


# ── Behaviour ───────────────────────────────────────────────────


def test_open_items_one_line(stored_root):
    ctx = context(_run(stored_root, "api/src/svc/model.py"))
    assert ctx.startswith(
        "TM: api/src/svc/model.py → B-001 open, eng-001 in-progress, "
        "HND 2026-09-01-fixture-hando…")
    assert "+1 closed" in ctx and "\n" not in ctx


def test_closed_only_is_silent(tmp_path):
    """Closed history lives behind backlog_query — it never costs a context line."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "fixed", "svc/legacy.py", "exact", "location"),
        ("t-900", "task", "done", "svc/legacy.py", "exact", "anchors"),
    ])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "legacy.py").write_text("x", encoding="utf-8")
    r = _run(root, "svc/legacy.py")
    assert r.stdout == ""
    assert r.returncode == 0


def test_closed_items_counted_beside_open_ones(stored_root):
    # legacy.py: B-002 (fixed) by location, eng-001 (in-progress) by anchor glob.
    ctx = context(_run(stored_root, "api/src/svc/legacy.py"))
    assert ctx.startswith("TM: api/src/svc/legacy.py → eng-001 in-progress")
    assert "+1 closed" in ctx


def test_glob_anchor_matches_nested(stored_root):
    assert "eng-001 in-progress" in context(_run(stored_root, "api/src/svc/deep/new.py"))


def test_prose_match_counted_not_listed(stored_root):
    """other.py matches eng-001 by anchor glob and B-001 only in prose.

    B-001 is open, but a prose mention is not a claim about the file, so it
    is counted and never named.
    """
    ctx = context(_run(stored_root, "api/src/svc/other.py"))
    assert ctx.startswith("TM: api/src/svc/other.py → eng-001 in-progress")
    assert "+1 prose" in ctx
    assert "B-001" not in ctx


def test_prose_only_open_match_is_silent(tmp_path):
    """A file whose only open match is prose-derived produces no line at all."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/only_prose.py", "exact", "prose"),
    ])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "only_prose.py").write_text("x", encoding="utf-8")
    assert _run(root, "svc/only_prose.py").stdout == ""


def test_archived_entity_is_not_listed(tmp_path):
    """Archiving is how an item leaves the backlog; it must leave the line too."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/a.py", "exact", "location"),
    ], archived={"B-900"})
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "a.py").write_text("x", encoding="utf-8")
    assert _run(root, "svc/a.py").stdout == ""


def test_deleted_entity_is_not_listed(tmp_path):
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/a.py", "exact", "location"),
    ], deleted={"B-900"})
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "a.py").write_text("x", encoding="utf-8")
    assert _run(root, "svc/a.py").stdout == ""


def test_worktree_prefix_stripped(stored_root):
    ctx = context(_run(stored_root, ".worktrees/wt-1/api/src/svc/model.py"))
    assert ctx.startswith("TM: api/src/svc/model.py → ")
    assert "B-001 open" in ctx


def test_linked_worktree_reads_the_main_checkout_store(tmp_path):
    """A hook fired inside a linked worktree must read the checkout's own store.

    The worktree has no `.taskmaster/` of its own; `resolve_root` walks to the
    git common dir, which is what makes the backlog visible from there at all.
    """
    root = _synthetic_root(tmp_path, [
        ("B-700", "bug", "open", "svc/shared.py", "exact", "location"),
    ])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "shared.py").write_text("x", encoding="utf-8")
    _git_init(root)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", "feat", str(worktree)],
                   check=True, capture_output=True)
    (worktree / "svc").mkdir(parents=True, exist_ok=True)
    (worktree / "svc" / "shared.py").write_text("y", encoding="utf-8")

    env = dict(os.environ)
    env.pop("TASKMASTER_ROOT", None)
    r = run(payload(worktree, "svc/shared.py"), worktree, env=env)
    assert context(r) == "TM: svc/shared.py → B-700 open"
    # The line was memoised in the main checkout, not beside the worktree.
    assert (root / ".taskmaster" / "local" / "hook-seen" / "s1.json").is_file()
    assert not (worktree / ".taskmaster").exists()


def test_dedupe_per_session(stored_root):
    _run(stored_root, "api/src/svc/model.py")
    assert _run(stored_root, "api/src/svc/model.py").stdout == ""
    assert _run(stored_root, "api/src/svc/model.py", session="s2").stdout != ""


def test_a_new_change_seq_reprints_a_changed_line(tmp_path):
    """Dedupe is scoped to a store revision: `MAX(changes.seq)` versus the seq
    recorded beside the memoised line in `local/hook-seen/`."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/a.py", "exact", "location")])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "a.py").write_text("x", encoding="utf-8")
    assert context(_run(root, "svc/a.py")) == "TM: svc/a.py → B-900 open"
    assert _run(root, "svc/a.py").stdout == ""

    _append_rows(root, [("B-901", "bug", "open", "svc/a.py", "exact", "location")], bump_seq=True)
    assert context(_run(root, "svc/a.py")) == "TM: svc/a.py → B-900 open, B-901 open"


def test_an_unrelated_change_does_not_repeat_the_same_line(tmp_path):
    """A new seq alone is not news — the line is only reprinted when it differs."""
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "open", "svc/a.py", "exact", "location")])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "a.py").write_text("x", encoding="utf-8")
    assert _run(root, "svc/a.py").stdout != ""
    _append_rows(root, [("B-901", "bug", "open", "svc/elsewhere.py", "exact", "location")],
                 bump_seq=True)
    assert _run(root, "svc/a.py").stdout == ""


def test_silent_edit_does_not_burn_the_dedupe_slot(tmp_path):
    """Dedupe suppresses a repeated *line*, not a repeated edit.

    Editing a file before anyone files a bug against it must not mute the line
    for the rest of the session once the bug exists.
    """
    root = _synthetic_root(tmp_path, [
        ("B-900", "bug", "fixed", "svc/quiet.py", "exact", "location")])
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "quiet.py").write_text("x", encoding="utf-8")
    assert _run(root, "svc/quiet.py").stdout == ""

    _append_rows(root, [("B-901", "bug", "open", "svc/quiet.py", "exact", "location")],
                 bump_seq=True)
    assert context(_run(root, "svc/quiet.py")) == "TM: svc/quiet.py → B-901 open (+1 closed)"


def test_dedupe_prunes_seen_files_older_than_seven_days(stored_root):
    seen_dir = stored_root / ".taskmaster" / "local" / "hook-seen"
    seen_dir.mkdir(parents=True, exist_ok=True)
    stale = seen_dir / "old-session.json"
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(stale, (old, old))
    _run(stored_root, "api/src/svc/model.py")
    assert not stale.exists()
    assert (seen_dir / "s1.json").exists()


def test_outside_root_and_taskmaster_dir_skipped(stored_root, tmp_path):
    assert _run(stored_root, ".taskmaster/bugs/B-001.md").stdout == ""
    other = tmp_path / "elsewhere.py"
    other.write_text("x", encoding="utf-8")
    p = payload(stored_root, "x")
    p["tool_input"]["file_path"] = str(other)
    assert run(p, stored_root, env=_hook_env(stored_root)).stdout == ""


def test_missing_store_is_silent_and_logged(fixture_root_without_store):
    """No store yet is not an error and never a reason to build one (R10)."""
    r = _run(fixture_root_without_store, "api/src/svc/model.py")
    assert r.stdout == ""
    assert r.returncode == 0
    assert not (fixture_root_without_store / ".taskmaster" / "local" / "store.db").exists()
    log = (fixture_root_without_store / ".taskmaster" / "local" / "hook.log")
    assert log.is_file()
    assert "no store" in log.read_text(encoding="utf-8")


def test_no_backlog_anywhere_is_silent_and_writes_nothing(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "a.py").write_text("x", encoding="utf-8")
    assert _run(lone, "a.py").stdout == ""
    assert not (lone / ".taskmaster").exists()


def test_hook_never_opens_a_write_transaction(stored_root):
    """The store file must be byte-identical after the hook has read it."""
    local = stored_root / ".taskmaster" / "local"
    db = local / "store.db"
    wal = local / "store.db-wal"

    def snapshot():
        return db.read_bytes(), (wal.read_bytes() if wal.exists() else b"")

    before = snapshot()
    assert context(_run(stored_root, "api/src/svc/model.py"))
    # A committed write lands in the database or, under WAL, in the log beside
    # it. Opening a WAL database read-only materialises an EMPTY log; anything
    # written through it would leave frames behind.
    assert snapshot() == before


def test_non_edit_tool_ignored(stored_root):
    assert _run(stored_root, "api/src/svc/model.py", tool="Read").stdout == ""


def test_failed_tool_call_ignored(stored_root):
    p = payload(stored_root, "api/src/svc/model.py")
    p["tool_response"] = {"success": False}
    assert run(p, stored_root, env=_hook_env(stored_root)).stdout == ""


def test_malformed_stdin_exit_zero(stored_root):
    r = subprocess.run([sys.executable, HOOK], input="not json", text=True,
                       capture_output=True, cwd=str(stored_root), timeout=60)
    assert r.returncode == 0 and r.stdout == ""


def test_missing_session_id_uses_nosession(stored_root):
    p = payload(stored_root, "api/src/svc/model.py")
    del p["session_id"]
    assert run(p, stored_root, env=_hook_env(stored_root)).stdout != ""
    assert (stored_root / ".taskmaster" / "local" / "hook-seen" / "nosession.json").exists()


def test_hook_imports_no_heavy_module():
    """Hooks run under system Python, not the uv venv — a yaml/fastmcp/server
    import would make the hook dead on every machine without the venv. Only
    `taskmaster.root` (standard library only) may be imported."""
    src = Path(HOOK).read_text(encoding="utf-8")
    for banned in ("import yaml", "import fastmcp", "from taskmaster.store",
                   "from taskmaster import store", "backlog_server"):
        assert banned not in src
    assert "from taskmaster.root import" in src


def test_root_module_imports_only_stdlib():
    """`taskmaster.root` is the one module the hooks import; it must stay light."""
    src = (PLUGIN_ROOT / "taskmaster" / "root.py").read_text(encoding="utf-8")
    for banned in ("import yaml", "import fastmcp", "from taskmaster"):
        assert banned not in src


def test_hook_runs_with_yaml_and_fastmcp_unimportable(stored_root, tmp_path):
    """The property behind the import ban, proven rather than grepped."""
    blocker = tmp_path / "blocked"
    blocker.mkdir()
    for name in ("yaml", "fastmcp"):
        (blocker / f"{name}.py").write_text(
            "raise ImportError('blocked for the hook test')\n", encoding="utf-8")
    env = _hook_env(stored_root)
    env["PYTHONPATH"] = str(blocker)
    r = run(payload(stored_root, "api/src/svc/model.py"), stored_root, env=env)
    assert "B-001 open" in context(r)


def test_corrupt_store_is_silent_and_logged(stored_root):
    db = stored_root / ".taskmaster" / "local" / "store.db"
    db.write_bytes(b"not a database at all")
    r = _run(stored_root, "api/src/svc/model.py")
    assert r.stdout == "" and r.returncode == 0
    assert (stored_root / ".taskmaster" / "local" / "hook.log").exists()


# ── In-process unit tests (resolve / format_line) ───────────────


def _load_hook_module():
    from importlib import util
    spec = util.spec_from_file_location("edit_resurface", HOOK)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t.invalid"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(root), "config", key, value],
                       check=True, capture_output=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"],
                   check=True, capture_output=True)


def _connect(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(root / ".taskmaster" / "local" / "store.db")


def _insert(con: sqlite3.Connection, rows, archived=(), deleted=()) -> None:
    seen = set()
    for eid, kind, status, path, mk, source in rows:
        if (kind, eid) not in seen:
            seen.add((kind, eid))
            con.execute(
                "insert into entities(kind,id,epic,status,archived,deleted,doc,body,"
                "rev,updated_seq) values (?,?,NULL,?,?,?,'{}','',1,0)",
                (kind, eid, status, int(eid in archived), int(eid in deleted)))
        con.execute("insert into entity_paths(kind,id,path,match_kind,source) "
                    "values (?,?,?,?,?)", (kind, eid, path, mk, source))


def _synthetic_root(tmp_path: Path, rows, archived=(), deleted=()) -> Path:
    """A project root whose store.db holds exactly `rows`.

    rows: (entity_id, kind, status, path, match_kind, source)
    """
    root = tmp_path / "syn"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    (root / ".taskmaster" / "backlog.yaml").write_text("version: 4\n", encoding="utf-8")
    con = sqlite3.connect(root / ".taskmaster" / "local" / "store.db")
    con.executescript(store.SCHEMA_SQL)
    _insert(con, rows, archived, deleted)
    con.commit()
    con.close()
    return root


def _append_rows(root: Path, rows, *, bump_seq: bool = False) -> None:
    con = _connect(root)
    _insert(con, rows)
    if bump_seq:
        con.execute(
            "insert into changes(ts,session,tool,kind,id,op) "
            "values ('2026-09-05','s','test','bug','B-x','create')")
    con.commit()
    con.close()


def _resolve(mod, root: Path, rel: str):
    return mod.resolve(root / ".taskmaster" / "local" / "store.db", rel)


def test_format_orders_bugs_issues_tasks_handovers(tmp_path):
    mod = _load_hook_module()
    rows = [(eid, kind, status, "a/b.py", "exact", src) for eid, kind, status, src in [
        ("t-1", "task", "todo", "anchors"),
        ("2026-09-02-mapped-unimplemented", "handover", "open", "prose"),
        ("B-1", "bug", "open", "location"),
        ("ISS-1", "issue", "investigating", "location"),
    ]]
    root = _synthetic_root(tmp_path, rows)
    res = _resolve(mod, root, "a/b.py")
    assert mod.format_line("a/b.py", res) == (
        "TM: a/b.py → B-1 open, ISS-1 investigating, t-1 todo, "
        "HND 2026-09-02-mapped-unimpl…")


def test_short_handover_id_is_not_truncated(tmp_path):
    mod = _load_hook_module()
    root = _synthetic_root(tmp_path, [
        ("2026-09-02-short", "handover", "open", "a/b.py", "exact", "prose")])
    assert mod.format_line("a/b.py", _resolve(mod, root, "a/b.py")) == (
        "TM: a/b.py → HND 2026-09-02-short")


def test_format_caps_at_six_ids(tmp_path):
    mod = _load_hook_module()
    rows = [(f"B-{i:02d}", "bug", "open", "a/b.py", "exact", "location") for i in range(9)]
    root = _synthetic_root(tmp_path, rows)
    line = mod.format_line("a/b.py", _resolve(mod, root, "a/b.py"))
    assert line.endswith("+3 more")
    assert line.count(" open") == 6


def test_format_counts_closed_and_prose(tmp_path):
    mod = _load_hook_module()
    rows = [
        ("B-1", "bug", "open", "a/b.py", "exact", "location"),
        ("B-2", "bug", "fixed", "a/b.py", "exact", "location"),
        ("DEC-1", "decision", "open", "a/b.py", "exact", "prose"),
    ]
    root = _synthetic_root(tmp_path, rows)
    assert mod.format_line("a/b.py", _resolve(mod, root, "a/b.py")) == (
        "TM: a/b.py → B-1 open (+1 closed, +1 prose)")


def test_related_open_work_is_counted_not_named(tmp_path):
    """`related` rows carry work that travels with the matched item; the count
    is the invitation to ask, the ids stay behind backlog_query."""
    mod = _load_hook_module()
    root = _synthetic_root(tmp_path, [
        ("B-1", "bug", "open", "a/b.py", "exact", "location"),
    ])
    con = _connect(root)
    _insert(con, [("t-9", "task", "todo", "other/z.py", "exact", "anchors")])
    _insert(con, [("t-8", "task", "done", "other/y.py", "exact", "anchors")])
    con.execute("insert into related(a_kind,a_id,b_kind,b_id,via,weight) "
                "values ('bug','B-1','task','t-9','path',2)")
    con.execute("insert into related(a_kind,a_id,b_kind,b_id,via,weight) "
                "values ('task','t-8','bug','B-1','path',1)")
    con.commit()
    con.close()
    assert mod.format_line("a/b.py", _resolve(mod, root, "a/b.py")) == (
        "TM: a/b.py → B-1 open (+1 related)")


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
    res = _resolve(mod, root, "a/b.py")
    assert [e.id for e in res.listed] == [
        "B-adopted", "I-inv", "t-blocked", "t-review", "t-todo"]
    assert res.closed == 3


def test_connect_falls_back_to_query_only_when_read_only_open_fails(tmp_path, monkeypatch):
    """A WAL store whose `-shm` cannot be created refuses a `mode=ro` open.

    Going quiet there would hide the backlog on exactly the machines that need
    it most, so the hook reads through a `query_only` connection instead — which
    still cannot write a row.
    """
    mod = _load_hook_module()
    root = _synthetic_root(tmp_path, [
        ("B-1", "bug", "open", "a/b.py", "exact", "location")])
    db = root / ".taskmaster" / "local" / "store.db"
    real_connect = mod.sqlite3.connect

    def refuse_read_only(database, *args, **kwargs):
        if isinstance(database, str) and "mode=ro" in database:
            raise mod.sqlite3.OperationalError("unable to open database file")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(mod.sqlite3, "connect", refuse_read_only)
    assert [e.id for e in _resolve(mod, root, "a/b.py").listed] == ["B-1"]

    con = mod._connect_ro(db)
    try:
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(mod.sqlite3.OperationalError):
            con.execute(
                "INSERT INTO entities(kind,id,epic,status,archived,deleted,doc,"
                "body,rev,updated_seq) VALUES('bug','B-2',NULL,'open',0,0,'{}','',1,0)")
    finally:
        con.close()


def test_query_budget_under_50ms(tmp_path):
    mod = _load_hook_module()
    root = tmp_path / "big"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    db = root / ".taskmaster" / "local" / "store.db"
    con = sqlite3.connect(db)
    con.executescript(store.SCHEMA_SQL)
    con.executemany(
        "insert into entities(kind,id,epic,status,archived,deleted,doc,body,rev,"
        "updated_seq) values ('task',?,NULL,?,0,0,'{}','',1,0)",
        [(f"t-{i}", "todo" if i % 2 else "done") for i in range(3000)])
    con.executemany(
        "insert into entity_paths(kind,id,path,match_kind,source) values ('task',?,?,?,?)",
        [(f"t-{i}", f"repo{i % 7}/src/mod{i}/file{i}.py", "exact", "anchors")
         for i in range(3000)]
        + [(f"t-{i}", f"repo{i % 7}/src/area{i % 50}/**", "glob", "anchors")
           for i in range(0, 3000, 10)]
        + [(f"t-{i}", f"repo{i % 7}/src/mod{i}/extra.py", "exact", "prose")
           for i in range(3000)])
    con.commit()
    con.close()
    t = time.perf_counter()
    mod.resolve(db, "repo1/src/area1/x.py")
    assert (time.perf_counter() - t) < 0.05
