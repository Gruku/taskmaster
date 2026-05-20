"""Tests for backlog_project_structure tool and its private helpers.

Helpers under test live in backlog_server.py (the same module that owns all
@mcp.tool() decorations and the ViewerHandler HTTP router).

Most tests need a real on-disk git repo. We build one with `git init` inside
tmp_path because the helpers shell out to `git` — there is no mocking-friendly
seam in the helper layer by design (the spec calls for real `git` output).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# Skip the whole module if `git` is not on PATH — CI sandboxes occasionally lack it.
if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
    pytest.skip("git not available", allow_module_level=True)


def _git(*args: str, cwd: Path) -> None:
    """Run git for fixture setup. Raises on non-zero so test setup fails loudly."""
    subprocess.run(
        ["git", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def _run_git_check(args: list[str], *, cwd: Path) -> str:
    """Run git in fixture setup with check=True (raises on failure).

    Distinct from backlog_server._run_git which swallows non-zero — fixtures
    want loud failures so a broken test setup doesn't masquerade as a
    behaviour regression.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(path: Path, *, initial_branch: str = "master") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", initial_branch, cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=path)
    _git("commit", "-q", "-m", "seed", cwd=path)
    return path


def test_run_git_returns_stdout_text(tmp_path):
    from backlog_server import _run_git
    repo = _init_repo(tmp_path / "r")
    out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    assert out.strip() == "master"


def test_run_git_returns_empty_string_on_nonzero(tmp_path):
    """Non-zero exit (e.g. unknown ref) must NOT raise — we treat it as 'feature unavailable'."""
    from backlog_server import _run_git
    repo = _init_repo(tmp_path / "r")
    out = _run_git(["merge-base", "--is-ancestor", "no-such-branch", "master"], cwd=repo)
    assert out == ""  # convention: empty string on failure


def test_run_git_handles_missing_git_dir(tmp_path):
    from backlog_server import _run_git
    out = _run_git(["status"], cwd=tmp_path)  # tmp_path has no .git
    assert out == ""


def test_rank_integration_branch_orders_master_highest():
    from backlog_server import _rank_integration_branch
    # Rank order (lowest → highest): work < dev < stage < <version> < master|main
    assert _rank_integration_branch("work")    < _rank_integration_branch("dev")
    assert _rank_integration_branch("dev")     < _rank_integration_branch("stage")
    assert _rank_integration_branch("stage")   < _rank_integration_branch("1.3.1")
    assert _rank_integration_branch("1.3.1")   < _rank_integration_branch("master")
    assert _rank_integration_branch("master") == _rank_integration_branch("main")


def test_rank_integration_branch_orders_versions_numerically():
    from backlog_server import _rank_integration_branch
    assert _rank_integration_branch("1.2.0") < _rank_integration_branch("1.3.1")
    assert _rank_integration_branch("1.3.1") < _rank_integration_branch("2.0.0")
    assert _rank_integration_branch("1.3.1") < _rank_integration_branch("1.10.0")  # not lexicographic!


def test_discover_sub_repos_finds_embedded(tmp_path):
    """Embedded sub-repo = a nested .git directory (not a submodule)."""
    from backlog_server import _discover_sub_repos
    monorepo = _init_repo(tmp_path / "mono")
    _init_repo(monorepo / "sub-a")          # depth 1
    _init_repo(monorepo / "nested" / "sub-b")  # depth 2 — also discovered

    subs = _discover_sub_repos(monorepo)
    by_path = {s["path"]: s for s in subs}
    assert "sub-a" in by_path
    assert "nested/sub-b" in by_path or "nested\\sub-b" in by_path
    assert by_path["sub-a"]["kind"] == "embedded"
    assert by_path["sub-a"]["submodule_info"] is None


def test_discover_sub_repos_ignores_self_dot_git(tmp_path):
    """The project's own .git must not be reported as a sub-repo."""
    from backlog_server import _discover_sub_repos
    monorepo = _init_repo(tmp_path / "mono")
    subs = _discover_sub_repos(monorepo)
    assert all(s["path"] != "." and s["path"] != "" for s in subs)


def test_discover_sub_repos_skips_depth_three(tmp_path):
    """Nested .git at depth 3 is out of scope (avoid scanning node_modules etc.)."""
    from backlog_server import _discover_sub_repos
    monorepo = _init_repo(tmp_path / "mono")
    _init_repo(monorepo / "a" / "b" / "deep")  # depth 3
    subs = _discover_sub_repos(monorepo)
    assert not any("deep" in s["path"] for s in subs)


def test_discover_sub_repos_parses_gitmodules(tmp_path):
    """An entry in .gitmodules is reported with kind='submodule' even if the
    working tree isn't checked out yet."""
    from backlog_server import _discover_sub_repos
    monorepo = _init_repo(tmp_path / "mono")
    (monorepo / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n'
        '\tpath = vendor/lib\n'
        '\turl = https://example.com/lib.git\n',
        encoding="utf-8",
    )
    subs = _discover_sub_repos(monorepo)
    by_path = {s["path"]: s for s in subs}
    assert "vendor/lib" in by_path
    assert by_path["vendor/lib"]["kind"] == "submodule"


def test_discover_sub_repos_submodule_with_checkout(tmp_path):
    """A submodule whose working tree IS checked out (i.e. has .git as file or dir)
    is still reported as kind='submodule', not 'embedded'."""
    from backlog_server import _discover_sub_repos
    monorepo = _init_repo(tmp_path / "mono")
    (monorepo / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = x\n',
        encoding="utf-8",
    )
    # Simulate a checked-out submodule by creating its directory with a .git marker.
    (monorepo / "vendor" / "lib").mkdir(parents=True)
    (monorepo / "vendor" / "lib" / ".git").write_text(
        "gitdir: ../../.git/modules/vendor/lib\n", encoding="utf-8",
    )
    subs = _discover_sub_repos(monorepo)
    by_path = {s["path"]: s for s in subs}
    assert by_path["vendor/lib"]["kind"] == "submodule"  # NOT 'embedded'


def test_discover_integration_branches_filters_by_pattern(tmp_path):
    from backlog_server import _discover_integration_branches
    repo = _init_repo(tmp_path / "r")
    # Create some branches: 2 integration-like, 2 feature-like.
    for b in ("stage", "1.3.1", "feature/ui-001", "fix/bug-99"):
        _git("branch", b, cwd=repo)

    found = _discover_integration_branches(repo)
    # master (the initial branch) is included; feature/* and fix/* are NOT.
    assert set(found) >= {"master", "stage", "1.3.1"}
    assert "feature/ui-001" not in found
    assert "fix/bug-99" not in found


def test_discover_integration_branches_dedupes_origin_prefix(tmp_path):
    """`git branch -a` lists both 'master' and 'origin/master' when a remote
    exists. The result must dedupe the origin/ prefix."""
    from backlog_server import _discover_integration_branches
    repo = _init_repo(tmp_path / "r")
    # Fake a remote-tracking branch by creating a packed-refs entry.
    refs_dir = repo / ".git" / "refs" / "remotes" / "origin"
    refs_dir.mkdir(parents=True, exist_ok=True)
    head_sha = _run_git_check(["rev-parse", "HEAD"], cwd=repo).strip()
    (refs_dir / "master").write_text(head_sha + "\n", encoding="utf-8")
    (refs_dir / "stage").write_text(head_sha + "\n", encoding="utf-8")

    found = _discover_integration_branches(repo)
    assert found.count("master") == 1
    assert found.count("stage") == 1


def test_discover_integration_branches_orders_by_rank(tmp_path):
    from backlog_server import _discover_integration_branches
    repo = _init_repo(tmp_path / "r")
    for b in ("stage", "dev", "work", "1.3.1"):
        _git("branch", b, cwd=repo)
    found = _discover_integration_branches(repo)
    # Order: work < dev < stage < 1.3.1 < master
    assert found == ["work", "dev", "stage", "1.3.1", "master"]


def test_discover_integration_branches_returns_empty_for_non_repo(tmp_path):
    from backlog_server import _discover_integration_branches
    assert _discover_integration_branches(tmp_path) == []


def test_list_worktrees_returns_main_repo_only_when_no_extras(tmp_path):
    """A fresh repo has exactly one worktree: itself."""
    from backlog_server import _list_worktrees
    repo = _init_repo(tmp_path / "r")
    wts = _list_worktrees(repo)
    assert len(wts) == 1
    assert wts[0]["branch"] == "master"
    assert Path(wts[0]["path"]).resolve() == repo.resolve()


def test_list_worktrees_includes_added_worktree(tmp_path):
    from backlog_server import _list_worktrees
    repo = _init_repo(tmp_path / "r")
    _git("branch", "feature/foo", cwd=repo)
    wt_path = tmp_path / "wt-foo"
    _git("worktree", "add", str(wt_path), "feature/foo", cwd=repo)

    wts = _list_worktrees(repo)
    by_branch = {w["branch"]: w for w in wts}
    assert "feature/foo" in by_branch
    assert Path(by_branch["feature/foo"]["path"]).resolve() == wt_path.resolve()


def test_list_worktrees_returns_empty_for_non_repo(tmp_path):
    from backlog_server import _list_worktrees
    assert _list_worktrees(tmp_path) == []


def test_list_worktrees_detached_head_has_none_branch(tmp_path):
    """A worktree at a detached HEAD has no branch — must not crash and must
    return branch=None so the renderer can label it 'detached'."""
    from backlog_server import _list_worktrees
    repo = _init_repo(tmp_path / "r")
    head_sha = _run_git_check(["rev-parse", "HEAD"], cwd=repo).strip()
    wt_path = tmp_path / "wt-detached"
    _git("worktree", "add", "--detach", str(wt_path), head_sha, cwd=repo)

    wts = _list_worktrees(repo)
    detached = [w for w in wts if Path(w["path"]).resolve() == wt_path.resolve()]
    assert len(detached) == 1
    assert detached[0]["branch"] is None


def test_compute_worktree_git_state_merge_ladder(tmp_path):
    """A worktree branch that has been merged into 'work' but not 'stage'
    reports work=True, stage=False, master=False."""
    from backlog_server import _compute_worktree_git_state
    repo = _init_repo(tmp_path / "r")
    # Create integration branches at the seed commit (so they all contain it).
    for b in ("work", "stage"):
        _git("branch", b, cwd=repo)

    # feature/foo: branch off master, add one commit, merge into work, NOT stage.
    _git("checkout", "-q", "-b", "feature/foo", cwd=repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "f", cwd=repo)
    _git("checkout", "-q", "work", cwd=repo)
    _git("merge", "-q", "--no-ff", "feature/foo", "-m", "merge", cwd=repo)
    _git("checkout", "-q", "master", cwd=repo)

    state = _compute_worktree_git_state(
        repo, branch="feature/foo",
        integration_branches=["work", "stage", "master"],
        worktree_path=repo,
    )
    assert state["merge_ladder"] == {"work": True, "stage": False, "master": False}


def test_compute_worktree_git_state_ahead_behind(tmp_path):
    from backlog_server import _compute_worktree_git_state
    repo = _init_repo(tmp_path / "r")
    _git("checkout", "-q", "-b", "feature/x", cwd=repo)
    for i in range(3):
        (repo / f"a{i}.txt").write_text("x", encoding="utf-8")
        _git("add", f"a{i}.txt", cwd=repo)
        _git("commit", "-q", "-m", f"a{i}", cwd=repo)

    state = _compute_worktree_git_state(
        repo, branch="feature/x",
        integration_branches=["master"],
        worktree_path=repo,
        base="master",
    )
    assert state["ahead"] == 3
    assert state["behind"] == 0


def test_compute_worktree_git_state_dirty_file_count(tmp_path):
    from backlog_server import _compute_worktree_git_state
    repo = _init_repo(tmp_path / "r")
    # Two uncommitted modifications.
    (repo / "dirty1.txt").write_text("a", encoding="utf-8")
    (repo / "dirty2.txt").write_text("b", encoding="utf-8")

    state = _compute_worktree_git_state(
        repo, branch="master",
        integration_branches=["master"],
        worktree_path=repo,
    )
    assert state["dirty_files"] == 2


def test_compute_worktree_git_state_handles_detached(tmp_path):
    """branch=None (detached HEAD) returns a state with empty ladder, no crash."""
    from backlog_server import _compute_worktree_git_state
    repo = _init_repo(tmp_path / "r")
    state = _compute_worktree_git_state(
        repo, branch=None,
        integration_branches=["master"],
        worktree_path=repo,
    )
    assert state["merge_ladder"] == {}
    assert state["ahead"] == 0
    assert state["behind"] == 0


def test_link_worktrees_to_tasks_matches_by_worktree_field():
    from backlog_server import _link_worktrees_to_tasks
    worktrees = [
        {"path": "/abs/wt-a", "branch": "feature/a", "head": "x"},
        {"path": "/abs/wt-b", "branch": "feature/b", "head": "y"},
    ]
    tasks = [
        {"id": "T-1", "title": "alpha", "status": "in-progress",
         "worktree": "/abs/wt-a"},
        {"id": "T-2", "title": "beta",  "status": "todo",
         "worktree": "/abs/wt-a"},
        {"id": "T-3", "title": "gamma", "status": "done",
         "worktree": "/abs/wt-b"},
        {"id": "T-4", "title": "delta", "status": "todo"},  # no worktree
    ]
    out = _link_worktrees_to_tasks(worktrees, tasks)
    assert sorted(t["id"] for t in out["/abs/wt-a"]) == ["T-1", "T-2"]
    assert sorted(t["id"] for t in out["/abs/wt-b"]) == ["T-3"]


def test_link_worktrees_to_tasks_normalises_path_separators():
    """Windows tasks may persist worktree paths with backslashes; the matcher
    must compare canonically (POSIX form, no trailing slash)."""
    from backlog_server import _link_worktrees_to_tasks
    worktrees = [{"path": "C:/proj/wt-a", "branch": "x", "head": "y"}]
    tasks = [{"id": "T-1", "title": "x", "status": "todo",
              "worktree": r"C:\proj\wt-a"}]
    out = _link_worktrees_to_tasks(worktrees, tasks)
    assert len(out["C:/proj/wt-a"]) == 1


def test_link_handovers_to_worktrees_transitive_via_task_ids():
    """A handover linked to T-1 lands on the worktree that owns T-1."""
    from backlog_server import _link_handovers_to_worktrees
    worktree_task_map = {
        "/abs/wt-a": [{"id": "T-1", "title": "x", "status": "todo"}],
        "/abs/wt-b": [{"id": "T-9", "title": "y", "status": "todo"}],
    }
    handovers = [
        {"id": "2026-05-19-foo", "created": "2026-05-19T10:00:00Z",
         "task_ids": ["T-1"], "status": "open"},
        {"id": "2026-05-19-bar", "created": "2026-05-19T11:00:00Z",
         "task_ids": ["T-9"], "status": "closed"},
        {"id": "2026-05-19-baz", "created": "2026-05-19T12:00:00Z",
         "task_ids": [], "status": "open"},  # unlinked
    ]
    out = _link_handovers_to_worktrees(worktree_task_map, handovers)
    assert [h["id"] for h in out["/abs/wt-a"]] == ["2026-05-19-foo"]
    assert [h["id"] for h in out["/abs/wt-b"]] == ["2026-05-19-bar"]


def test_backlog_project_structure_returns_shape(tmp_taskmaster):
    """Smoke test against the tmp_taskmaster fixture (no real sub-repos).
    The result must always have the documented top-level shape even with
    zero sub-repos."""
    import json as _json
    from backlog_server import backlog_project_structure
    raw = backlog_project_structure()
    data = _json.loads(raw) if isinstance(raw, str) else raw
    assert set(data.keys()) >= {
        "project", "sub_repos", "generated_at", "git_state_included",
    }
    assert data["project"]["root"]
    assert data["git_state_included"] is False  # default refresh_git=False
    assert isinstance(data["sub_repos"], list)


def test_backlog_project_structure_discovers_embedded_sub_repo(tmp_path, monkeypatch):
    """End-to-end: a tmp project with one embedded sub-repo and one task
    pointing at a worktree shows up in the JSON shape from the spec."""
    import json as _json
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  schema_version: 3\nepics: []\nphases: []\n",
        encoding="utf-8",
    )
    import importlib
    import backlog_server
    importlib.reload(backlog_server)

    # An embedded sub-repo with one extra worktree.
    sub = _init_repo(tmp_path / "sub-a")
    _git("branch", "feature/x", cwd=sub)
    wt = tmp_path / ".worktrees" / "wt-x"
    _git("worktree", "add", str(wt), "feature/x", cwd=sub)

    raw = backlog_server.backlog_project_structure(refresh_git=False)
    data = _json.loads(raw) if isinstance(raw, str) else raw
    sub_paths = [s["path"] for s in data["sub_repos"]]
    assert "sub-a" in sub_paths
    target = next(s for s in data["sub_repos"] if s["path"] == "sub-a")
    assert target["kind"] == "embedded"
    assert any(w["branch"] == "feature/x" for w in target["worktrees"])
    # git_state must be None when refresh_git=False.
    for w in target["worktrees"]:
        assert w["git_state"] is None


def test_backlog_project_structure_refresh_git_populates_state(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  schema_version: 3\nepics: []\nphases: []\n",
        encoding="utf-8",
    )
    import importlib
    import backlog_server
    importlib.reload(backlog_server)

    sub = _init_repo(tmp_path / "sub-a")
    raw = backlog_server.backlog_project_structure(refresh_git=True)
    data = _json.loads(raw) if isinstance(raw, str) else raw
    assert data["git_state_included"] is True
    target = next(s for s in data["sub_repos"] if s["path"] == "sub-a")
    # Main worktree of the sub-repo is always present.
    assert len(target["worktrees"]) >= 1
    assert target["worktrees"][0]["git_state"] is not None
    assert "merge_ladder" in target["worktrees"][0]["git_state"]


import threading
import time
import urllib.request


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Stand up a real BaseHTTPServer thread bound to a tmp project root.
    Mirrors the pattern from tests/test_server_sessions_recap.py."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  project: test\n  schema_version: 3\nepics: []\nphases: []\n",
        encoding="utf-8",
    )
    import importlib, backlog_server
    importlib.reload(backlog_server)
    server, port = backlog_server._make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    yield base, server
    server.shutdown()
    server.server_close()


def test_http_get_project_structure_default(running_server):
    import json as _json
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/api/project-structure", timeout=5).read()
    data = _json.loads(resp.decode("utf-8"))
    assert data["git_state_included"] is False
    assert "sub_repos" in data
    assert "project" in data


def test_http_get_project_structure_refresh_git(running_server, tmp_path):
    """refresh_git=1 query param flips git_state_included."""
    import json as _json
    base, _ = running_server
    # Add an embedded sub-repo so there's at least one card to populate.
    _init_repo(tmp_path / "sub-a")
    resp = urllib.request.urlopen(
        f"{base}/api/project-structure?refresh_git=1", timeout=15,
    ).read()
    data = _json.loads(resp.decode("utf-8"))
    assert data["git_state_included"] is True
    target = next((s for s in data["sub_repos"] if s["path"] == "sub-a"), None)
    assert target is not None
    assert target["worktrees"][0]["git_state"] is not None
