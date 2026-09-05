# User intent: the merge gate must decide from the SQLite store the server writes,
# so a gate recorded seconds ago is visible to the hook without re-parsing YAML —
# and must never import, rebuild, or write anything on the way to that answer.
"""Store-backed decisions for hooks/merge_gate_decide.py.

`test_merge_gate_hook.py` covers the shell-level decision table against a
file-only project (the fallback path). This file covers the store path: the
answers come out of `local/store.db`, the projection is not read, and every
failure mode still fails open.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import store  # noqa: E402

DECIDE = (PLUGIN_ROOT / "hooks" / "merge_gate_decide.py").resolve()


@pytest.fixture()
def decide_module():
    spec = importlib.util.spec_from_file_location("merge_gate_decide", DECIDE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Seeding ─────────────────────────────────────────────────────


def _write_projection(root: Path, *, policy: bool, branch: str = "feature/x") -> None:
    tm = root / ".taskmaster"
    (tm / "tasks").mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (tm / "project.yaml").write_text(
        textwrap.dedent(f"""\
            schema_version: 1
            meta: {{name: T, slug: t, kind: app}}
            conventions:
              policies:
                review_gate_required_for_merge: {str(policy).lower()}
        """),
        encoding="utf-8",
    )
    backlog = {
        "version": 3,
        "project": "t",
        "meta": {"project": "t", "schema_version": 3, "updated": "2026-01-01"},
        "context": {},
        "epics": [{
            "id": "core",
            "name": "Core",
            "tasks": [{
                "id": "T-001",
                "title": "Test task",
                "status": "in-progress",
                "priority": "high",
                "created": "2026-01-01T00:00",
                "branch": branch,
            }],
        }],
        "phases": [],
    }
    (tm / "backlog.yaml").write_text(yaml.dump(backlog, allow_unicode=True), encoding="utf-8")


def _adopt(root: Path) -> None:
    """Let the store import the projection exactly as the server would."""
    store.reset_for_tests()
    store.open_store(root=root, session="merge-gate-seed")
    store.reset_for_tests()


def _patch_task(root: Path, **fields) -> None:
    store.reset_for_tests()
    with store.transaction(tool="test:seed", backlog_path=root / ".taskmaster") as tx:
        doc = tx.get("task", "T-001")
        doc.update(fields)
        tx.put("task", "T-001", doc)
    store.reset_for_tests()


@pytest.fixture()
def stored_project(tmp_path, monkeypatch):
    """A project whose store is the only readable copy of the backlog."""
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=True)
    _adopt(root)
    return root


def _blind_the_projection(root: Path) -> None:
    """Make backlog.yaml and the task file unreadable as YAML.

    Any answer produced afterwards can only have come from the store.
    """
    tm = root / ".taskmaster"
    (tm / "backlog.yaml").write_text("{[ not yaml", encoding="utf-8")
    for task_file in (tm / "tasks").glob("*.md"):
        task_file.write_text("---\n{not: yaml\n---\nbody", encoding="utf-8")


def _git_init(root: Path, branch: str = "feature/x") -> str:
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t.invalid"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(root), "config", key, value],
                       check=True, capture_output=True)
    (root / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "checkout", "-b", branch],
                   check=True, capture_output=True)
    (root / "f.txt").write_text("feature", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "f.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "feature"],
                   check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _gate(verdict: str, sha: str) -> dict:
    return {"review-gate": {"verdict": verdict, "commit_sha": sha}}


# ── Store path ──────────────────────────────────────────────────


def test_store_no_gate_blocks_without_reading_the_projection(stored_project, decide_module):
    _blind_the_projection(stored_project)
    verdict = decide_module.decide("feature/x", stored_project)
    assert verdict.startswith("BLOCK:T-001:")
    assert "review-gate" in verdict


def test_store_failed_gate_blocks(stored_project, decide_module):
    _patch_task(stored_project, gates=_gate("fail", "abc"))
    _blind_the_projection(stored_project)
    assert decide_module.decide("feature/x", stored_project).startswith("BLOCK:T-001:")


def test_store_fresh_pass_allows(tmp_path, monkeypatch, decide_module):
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    root.mkdir()
    sha = _git_init(root)
    _write_projection(root, policy=True)
    _adopt(root)
    _patch_task(root, gates=_gate("pass", sha))
    _blind_the_projection(root)
    assert decide_module.decide("feature/x", root) == "ALLOW"


def test_store_stale_pass_blocks_under_strict(tmp_path, monkeypatch, decide_module):
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    root.mkdir()
    _git_init(root)
    _write_projection(root, policy=True)
    _adopt(root)
    _patch_task(root, gates=_gate("pass", "OLDSHA1234567890"))
    _blind_the_projection(root)
    assert decide_module.decide("feature/x", root).startswith("BLOCK:T-001:")


def test_store_stale_pass_allows_under_freshness_any(tmp_path, monkeypatch, decide_module):
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    root.mkdir()
    _git_init(root)
    _write_projection(root, policy=True)
    _adopt(root)
    _patch_task(root, gates=_gate("pass", "OLDSHA1234567890"),
                merge_gate_freshness="any")
    _blind_the_projection(root)
    assert decide_module.decide("feature/x", root) == "ALLOW"


def test_store_gate_recorded_by_the_server_is_the_shape_the_hook_reads(
    tmp_path, monkeypatch, decide_module
):
    """The production writer, not a hand-built doc, feeds the production reader."""
    from taskmaster import backlog_server as bs

    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    root.mkdir()
    sha = _git_init(root)
    _write_projection(root, policy=True)
    _adopt(root)

    monkeypatch.setattr(bs, "ROOT", root)
    store.reset_for_tests()
    result = bs.backlog_record_gate("T-001", "review-gate", verdict="pass",
                                    commit_sha="deadbeef1234")
    assert not result.startswith("Error"), result
    store.reset_for_tests()
    _blind_the_projection(root)

    # A pass recorded on a sha that is not the branch tip is stale, not absent.
    verdict = decide_module.decide("feature/x", root)
    assert verdict.startswith("BLOCK:T-001:")
    assert "deadbeef1234" in verdict and sha[:12] in verdict


def test_store_skip_merge_gate_allows(stored_project, decide_module):
    _patch_task(stored_project, skip_merge_gate=True)
    _blind_the_projection(stored_project)
    assert decide_module.decide("feature/x", stored_project) == "ALLOW"


def test_store_untracked_branch_allows(stored_project, decide_module):
    _blind_the_projection(stored_project)
    assert decide_module.decide("feature/UNTRACKED", stored_project) == "ALLOW"


def test_store_policy_off_allows_even_without_project_yaml_on_disk(
    tmp_path, monkeypatch, decide_module
):
    """Policy lives in the store's project row once the manifest is adopted."""
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=False)
    _adopt(root)
    (root / ".taskmaster" / "project.yaml").unlink()
    _blind_the_projection(root)
    assert decide_module.decide("feature/x", root) == "ALLOW"


def test_store_policy_on_survives_a_deleted_project_yaml(
    tmp_path, monkeypatch, decide_module
):
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=True)
    _adopt(root)
    (root / ".taskmaster" / "project.yaml").unlink()
    _blind_the_projection(root)
    assert decide_module.decide("feature/x", root).startswith("BLOCK:T-001:")


def test_decide_writes_nothing(stored_project, decide_module):
    local = stored_project / ".taskmaster" / "local"
    db = local / "store.db"
    wal = local / "store.db-wal"

    def snapshot():
        return db.read_bytes(), (wal.read_bytes() if wal.exists() else b"")

    before = snapshot()
    assert decide_module.decide("feature/x", stored_project).startswith("BLOCK:")
    assert snapshot() == before


def test_linked_worktree_decides_from_the_main_checkout_store(
    tmp_path, monkeypatch, decide_module
):
    """The gate fires in the worktree the merge runs in; the store is upstream."""
    root = tmp_path / "proj"
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root.mkdir()
    _git_init(root, branch="feature/x")
    subprocess.run(["git", "-C", str(root), "checkout", "master"],
                   capture_output=True)
    _write_projection(root, policy=True)
    _adopt(root)
    _blind_the_projection(root)

    worktree = tmp_path / "wt"
    subprocess.run(["git", "-C", str(root), "worktree", "add", str(worktree), "feature/x"],
                   check=True, capture_output=True)
    assert not (worktree / ".taskmaster").exists()
    assert decide_module.decide("feature/x", worktree).startswith("BLOCK:T-001:")


# ── Fallback and failure modes ──────────────────────────────────


def test_missing_store_falls_back_to_files_and_logs(tmp_path, monkeypatch, decide_module):
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=True)
    # v3 keeps `gates` in the per-task file; without one the reader fails open
    # for a different reason than the one under test.
    (root / ".taskmaster" / "tasks" / "T-001.md").write_text(
        "---\nid: T-001\ntitle: Test task\n---\n", encoding="utf-8")
    assert not (root / ".taskmaster" / "local" / "store.db").exists()

    assert decide_module.decide("feature/x", root).startswith("BLOCK:T-001:")
    # Still no store: a hook must never build one (R10).
    assert not (root / ".taskmaster" / "local" / "store.db").exists()
    log = (root / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "no store" in log


def test_corrupt_store_falls_back_to_the_projection(tmp_path, monkeypatch, decide_module):
    """A broken store must not be a way to walk a merge past the gate."""
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=True)
    (root / ".taskmaster" / "tasks" / "T-001.md").write_text(
        "---\nid: T-001\ntitle: Test task\n---\n", encoding="utf-8")
    _adopt(root)
    (root / ".taskmaster" / "local" / "store.db").write_bytes(b"not a database")

    assert decide_module.decide("feature/x", root).startswith("BLOCK:T-001:")
    log = (root / ".taskmaster" / "local" / "hook.log").read_text(encoding="utf-8")
    assert "store unreadable" in log


def test_unreadable_wal_store_falls_back_to_the_projection(
    tmp_path, monkeypatch, decide_module
):
    """The real failure mode: a WAL store whose `-shm` cannot be created.

    SQLite defers that to the first statement, so a guard wrapped around the
    open alone would let the error escape and fail the gate open.
    """
    root = tmp_path / "proj"
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    _write_projection(root, policy=True)
    (root / ".taskmaster" / "tasks" / "T-001.md").write_text(
        "---\nid: T-001\ntitle: Test task\n---\n", encoding="utf-8")
    _adopt(root)
    local = root / ".taskmaster" / "local"
    assert (local / "store.db").is_file()
    for leftover in ("store.db-shm", "store.db-wal"):
        if (local / leftover).exists():
            (local / leftover).unlink()
    (local / "store.db-shm").mkdir()

    assert decide_module.decide("feature/x", root).startswith("BLOCK:T-001:")
    log = (local / "hook.log").read_text(encoding="utf-8")
    assert "store unreadable" in log


def test_the_read_connection_cannot_write_or_create_a_store(stored_project, decide_module):
    """`query_only` on a normal connection, not a `mode=ro` URI (spec 3.1)."""
    db = stored_project / ".taskmaster" / "local" / "store.db"
    con = decide_module._connect_ro(db)
    try:
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM entities")
    finally:
        con.close()

    missing = stored_project / ".taskmaster" / "local" / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        decide_module._connect_ro(missing)
    assert not missing.exists()


def test_no_project_at_all_allows(tmp_path, monkeypatch, decide_module):
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path / "nothing"))
    assert decide_module.decide("feature/x", tmp_path) == "ALLOW"


def test_decide_never_raises_on_a_broken_root(tmp_path, monkeypatch, decide_module):
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path / "missing"))
    assert decide_module.decide("", tmp_path) == "ALLOW"


def test_hook_does_not_import_the_server(decide_module):
    """A PreToolUse hook runs on the merge path; importing the MCP server there
    costs seconds and can build a store as a side effect."""
    src = DECIDE.read_text(encoding="utf-8")
    for banned in ("backlog_server", "from taskmaster.store", "from taskmaster import store"):
        assert banned not in src


def test_merge_gate_blocks_end_to_end_from_the_store(stored_project, monkeypatch):
    """The shell-facing hook, not just `decide`, reads the store.

    `merge_gate.py` runs the decision module with no cwd argument, so this also
    pins that the module resolves the project from the process cwd.
    """
    _blind_the_projection(stored_project)
    env = dict(os.environ)
    env.pop("TASKMASTER_ROOT", None)
    hook = str((PLUGIN_ROOT / "hooks" / "merge_gate.py").resolve())
    result = subprocess.run(
        [sys.executable, hook],
        input=json.dumps({"tool_input": {"command": "git merge feature/x"},
                          "session_id": "s"}),
        text=True, capture_output=True, cwd=str(stored_project), env=env, timeout=60,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "T-001" in result.stderr


def test_store_query_is_the_documented_one(stored_project):
    """`entities WHERE kind='task' AND json_extract(doc,'$.branch')=?` — the
    contract this hook depends on, asserted against a real store."""
    db = stored_project / ".taskmaster" / "local" / "store.db"
    con = sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, doc FROM entities WHERE kind='task' AND deleted=0"
            " AND json_extract(doc,'$.branch')=?", ("feature/x",)).fetchall()
    finally:
        con.close()
    assert [row[0] for row in rows] == ["T-001"]
    assert json.loads(rows[0][1])["branch"] == "feature/x"
