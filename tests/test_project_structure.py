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
