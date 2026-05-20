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
