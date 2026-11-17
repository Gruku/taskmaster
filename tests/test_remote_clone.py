# User intent: a clone gets one stable, random, owner-only id that every worktree of it shares, never created where
# it cannot be trusted (a submodule, a link), and printable for the MCP header (team-server-011 D1, Step 1).
from __future__ import annotations

import io
import multiprocessing
import os
import subprocess
import sys

import pytest

from taskmaster.remote import clone as clone_module
from taskmaster.remote.cli import main as remote_main


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    (path / "f").write_text("x", encoding="utf-8")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "init")
    return path


def test_the_first_call_creates_the_id_and_later_calls_return_it(repo):
    first = clone_module.clone_id(repo)
    assert clone_module.CLONE_RE.fullmatch(first)
    assert clone_module.clone_id(repo) == first
    assert (repo / ".git" / "taskmaster-clone.json").is_file()


def test_a_linked_worktree_of_the_same_clone_shares_the_id(repo, tmp_path):
    _git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    from taskmaster.root import resolve_root
    top = resolve_root(tmp_path / "wt").root
    assert clone_module.clone_id(top) == clone_module.clone_id(repo)


def test_create_false_never_writes(repo):
    assert clone_module.clone_id(repo, create=False) is None
    assert not (repo / ".git" / "taskmaster-clone.json").exists()
    made = clone_module.clone_id(repo)
    assert clone_module.clone_id(repo, create=False) == made


def test_a_dot_git_file_gives_none(tmp_path):
    (tmp_path / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8")
    assert clone_module.clone_id(tmp_path) is None


def test_no_dot_git_gives_none(tmp_path):
    assert clone_module.clone_id(tmp_path) is None


@pytest.mark.parametrize("content", ["not json", '{"clone": "nope"}', '{"clone": 5}', "[]", "x" * 2000])
def test_a_malformed_file_gives_none_and_is_not_replaced(repo, content):
    path = repo / ".git" / "taskmaster-clone.json"
    path.write_text(content, encoding="utf-8")
    assert clone_module.clone_id(repo) is None
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
def test_a_linked_file_gives_none(repo, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text('{"clone": "c-0123456789abcdef"}', encoding="utf-8")
    (repo / ".git" / "taskmaster-clone.json").symlink_to(target)
    assert clone_module.clone_id(repo) is None


@pytest.mark.skipif(sys.platform == "win32", reason="mode bits are POSIX")
def test_a_group_readable_file_gives_none(repo):
    clone_module.clone_id(repo)
    os.chmod(repo / ".git" / "taskmaster-clone.json", 0o644)
    assert clone_module.clone_id(repo) is None


def _race(root, queue):
    queue.put(clone_module.clone_id(root))


def test_two_processes_racing_on_first_use_agree(repo):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [context.Process(target=_race, args=(str(repo), queue)) for _ in range(4)]
    for worker in workers:
        worker.start()
    ids = {queue.get(timeout=60) for _ in workers}
    for worker in workers:
        worker.join(30)
    assert len(ids) == 1 and None not in ids


def test_remote_clone_id_prints_it(repo):
    out, err = io.StringIO(), io.StringIO()
    assert remote_main(["clone-id", "--root", str(repo)], stdout=out, stderr=err) == 0
    assert out.getvalue().strip() == clone_module.clone_id(repo)


def test_remote_clone_id_refuses_where_there_is_no_git_dir(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    assert remote_main(["clone-id", "--root", str(tmp_path)], stdout=out, stderr=err) == 2
    assert "refused" in err.getvalue() and out.getvalue() == ""
