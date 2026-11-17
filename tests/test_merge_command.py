# User intent: the one command-line reader the merge gate and the merge recorder share must gate every branch a
# command merges (compound commands, `-m msg`, octopus, `-C`), ignore everything that is not a merge, and say
# "unsure" for what it cannot follow, so a fail-closed gate is only as good as this table (team-server-008 D11).
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
spec = importlib.util.spec_from_file_location("merge_command_under_test", HOOKS / "merge_command.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


def merge(*branches, kind="merge", directory=None):
    return (kind, list(branches), directory)


def shape(command):
    parsed = mc.parse(command)
    return [(m.kind, m.branches, m.directory) for m in parsed.merges], parsed.unsure


NONE = ([], None)

ROWS = [
    ('git merge --no-ff feature/x -m "Merge feature/x: add thing"', [merge("feature/x")]),
    ("git merge --no-ff feature/x -m msg", [merge("feature/x")]),
    ("git merge feature/x && git push", [merge("feature/x")]),
    ("git merge feature/x; echo done", [merge("feature/x")]),
    ("git merge feature/x | tail -5", [merge("feature/x")]),
    ("git -C ../other merge feature/y", [merge("feature/y", directory="../other")]),
    ("git -c core.editor=true merge --no-edit feature/x", [merge("feature/x")]),
    ("git merge -mmsg feature/x", [merge("feature/x")]),
    ("git merge --message=msg feature/x", [merge("feature/x")]),
    ("git merge -s ours -X theirs feature/x", [merge("feature/x")]),
    ("git merge a b c", [merge("a", "b", "c")]),
    ("git merge -- feature/x", [merge("feature/x")]),
    ("git pull origin feature/x", [merge("feature/x", kind="pull")]),
    ("git pull --rebase origin +feature/x:feature/x", [merge("feature/x", kind="pull")]),
    ("git pull origin +a:a b:b", [merge("a", "b", kind="pull")]),
    ("git merge fix@v2", [merge("fix@v2")]),
    ("git merge feat+x", [merge("feat+x")]),
    ("git merge release#1", [merge("release#1")]),
    ("git merge feature/x # merge it", [merge("feature/x")]),
    ("git merge feature/x 2>&1 | tail -5", [merge("feature/x")]),
    ("git merge feature/x 2>/dev/null", [merge("feature/x")]),
    ("git merge feature/x >out.log", [merge("feature/x")]),
    ("git merge feature/x>out.log", [merge("feature/x")]),
    ("git merge feature/x >>log", [merge("feature/x")]),
    ("git merge feature/x &>log", [merge("feature/x")]),
    ("git merge feature/x <in", [merge("feature/x")]),
    ("git merge feature/x&&git push", [merge("feature/x")]),
    ("git merge feature/x;echo y", [merge("feature/x")]),
    ("git merge a b c 2>&1", [merge("a", "b", "c")]),
    ("cd sub && git merge feature/x", [merge("feature/x")]),
    ("GIT_EDITOR=true git merge feature/x", [merge("feature/x")]),
    ("git merge feature/x\ngit push", [merge("feature/x")]),
    ("env GIT_EDITOR=true git merge feature/x", [merge("feature/x")]),
    ("git.exe merge feature/x", [merge("feature/x")]),
    ("git -C a -C b merge feature/x", [merge("feature/x", directory="a/b")]),
    ("git merge feature/x\\\n  --no-edit", [merge("feature/x")]),
    ("git fetch && git merge origin/feature/x", [merge("origin/feature/x")]),
    ("git merge a && git merge b", [merge("a"), merge("b")]),
    ("echo $(date) && git merge feature/x", [merge("feature/x")]),
    ("git diff $(git merge-base main HEAD)", []),
    ("timeout 60 git merge feature/x", [merge("feature/x")]),
    ("timeout -s KILL 60 git merge feature/x", [merge("feature/x")]),
    ("nohup git merge feature/x", [merge("feature/x")]),
    ("stdbuf -oL git merge feature/x", [merge("feature/x")]),
    ("sudo git merge feature/x", [merge("feature/x")]),
    ("sudo -u bob git merge feature/x", [merge("feature/x")]),
    ("if git merge feature/x; then echo ok; fi", [merge("feature/x")]),
    ("{ git merge feature/x; }", [merge("feature/x")]),
    ("! git merge feature/x", [merge("feature/x")]),
    ("exec -a foo git merge feature/x", [merge("feature/x")]),
    ("while ! git merge feature/x; do sleep 1; done", [merge("feature/x")]),
]


@pytest.mark.parametrize("command,merges", ROWS)
def test_the_reader_finds_exactly_the_merged_branches(command, merges):
    got, unsure = shape(command)
    assert unsure is None, (command, unsure)
    assert got == merges, command


NOT_A_MERGE = [
    "git merge-base main feature/x", "git merge-tree a b", "git mergetool", 'git commit -m "merge fix"',
    "git merge --abort", "git merge --continue", "git merge --quit", "git merge HEAD~1", "git merge 1a2b3c4d",
    "git merge FETCH_HEAD", "git merge", "git pull", "git pull origin", 'echo "git merge is fine"',
    "git status", "ls", "npm run merge-check", "git pull --rebase", "git merge -s ours",
    "git log --merges --oneline", "echo merge && git status",
]


@pytest.mark.parametrize("command", NOT_A_MERGE)
def test_everything_else_is_no_merge_and_not_unsure(command):
    assert shape(command) == NONE, command


UNSURE = [
    "git merge -",
    'git merge "feature/x',
    "git merge 'feature/x",
    "$(echo git) merge feature/x",
    "`git merge feature/x`",
    'sh -c "git merge feature/x"',
    'bash -c "git merge feature/x"',
    'eval "git merge feature/x"',
    "echo feature/x | xargs git merge",
    "$GIT merge feature/x",
    "git --unknown-global merge feature/x",
    "git merge $(git rev-parse --abbrev-ref @{u})",
    "git merge $BRANCH",
    'echo "$(git merge feature/x)"',
    "git $CMD feature/x merge",
    'cmd /c "git merge feature/x"',
    'powershell -Command "git pull origin feature/x"',
    "git -C",
    "unknownwrap --flag git merge feature/x",
    "stdbuf -oL xargs git pull",
]


@pytest.mark.parametrize("command", UNSURE)
def test_what_it_cannot_follow_is_unsure(command):
    merges, unsure = shape(command)
    assert unsure and merges == [], (command, merges, unsure)


def test_an_unknown_global_option_without_a_merge_after_it_is_not_unsure():
    assert shape("git --version") == NONE
    assert shape("git --unknown status") == NONE


def test_a_comment_hides_the_rest_of_its_line_only():
    assert shape("git status # git merge hidden\ngit merge feature/x") == ([merge("feature/x")], None)
    assert shape("echo '# not a comment' && git merge feature/x") == ([merge("feature/x")], None)


def test_a_quoted_hash_word_is_not_a_comment():
    assert shape('git merge "a #b"') == ([merge("a #b")], None)


# ── prefilter ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["ls", "git status", "npm run merge-check", "echo merge", "git add ."])
def test_prefilter_is_false_for_commands_that_cannot_merge(command):
    assert mc.prefilter(command) is False


@pytest.mark.parametrize("command", [row[0] for row in ROWS] + NOT_A_MERGE[:6])
def test_prefilter_is_true_for_every_row_naming_git_and_merge_or_pull(command):
    if "git" in command and ("merge" in command or "pull" in command):
        assert mc.prefilter(command) is True


# ── names ───────────────────────────────────────────────────────────────────────


def _repo(tmp_path, remotes):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    for name in remotes:
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", name, "https://example.invalid/r.git"],
                       check=True, timeout=60)
    return tmp_path


def test_refs_heads_names_the_short_branch_too(tmp_path):
    assert mc.branch_names("refs/heads/x", tmp_path, remotes=lambda d: []) == ["refs/heads/x", "x"]


def test_a_remote_prefix_adds_the_short_name_only_for_a_real_remote(tmp_path):
    repo = _repo(tmp_path, ["origin"])
    assert mc.branch_names("origin/feature/x", repo) == ["origin/feature/x", "feature/x"]
    assert mc.branch_names("refs/remotes/origin/feature/x", repo) == ["refs/remotes/origin/feature/x", "feature/x"]
    assert mc.branch_names("upstream/feature/x", repo) == ["upstream/feature/x"]
    assert mc.branch_names("feature/x", repo) == ["feature/x"]


def test_a_name_without_a_slash_never_asks_git(tmp_path):
    def forbidden(directory):
        raise AssertionError("git remote must not run")
    assert mc.branch_names("plain", tmp_path, remotes=forbidden) == ["plain"]


def test_remotes_of_a_directory_that_is_not_a_repo_is_empty(tmp_path):
    assert mc.remotes_of(tmp_path / "missing") == []
