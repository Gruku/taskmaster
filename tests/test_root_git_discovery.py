# User intent: root resolution reads the repository layout off the filesystem instead of
# spawning git on every entry point, and must give exactly the answers git gives.
"""Filesystem git discovery in `taskmaster.root` against real repositories and real git."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from taskmaster import root


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repository(path: Path) -> Path:
    _git("init", "-b", "main", str(path))
    _git("config", "user.email", "root-tests@example.invalid", cwd=path)
    _git("config", "user.name", "Root Tests", cwd=path)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "fixture", cwd=path)
    return path


def _git_answers(start: Path) -> tuple[Path | None, Path | None]:
    """(checkout root, parent of the common dir) straight from `git rev-parse`."""
    def ask(flag: str) -> str | None:
        proc = subprocess.run(["git", "-C", str(start), "rev-parse", flag],
                              capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else None
    top, common = ask("--show-toplevel"), ask("--git-common-dir")
    if common is not None and not Path(common).is_absolute():
        common = str(start / common)
    return (None if top is None else Path(top).resolve(),
            None if common is None else Path(common).resolve().parent)


@pytest.fixture
def no_git(monkeypatch):
    """Fail any git spawn from root resolution: the filesystem must answer alone."""
    for name in root._GIT_DISCOVERY_ENV:
        monkeypatch.delenv(name, raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("root resolution spawned git")
    monkeypatch.setattr(root, "run_bounded", forbidden)


def _assert_matches_git(start: Path) -> None:
    expected = _git_answers(start)
    assert expected[0] is not None, f"fixture is not a git work tree: {start}"
    assert (root._git_checkout_root(start), root._git_common_root(start)) == expected


@pytest.fixture
def layout(tmp_path):
    """A main checkout, a linked worktree, a submodule and a submodule's linked worktree."""
    library = _repository(tmp_path / "library")
    main = _repository(tmp_path / "main")
    _git("submodule", "add", str(library), "vendor/library", cwd=main)
    _git("commit", "-m", "add submodule", cwd=main)
    linked = tmp_path / "linked"
    _git("worktree", "add", "-b", "feature", str(linked), cwd=main)
    submodule = main / "vendor" / "library"
    sub_linked = tmp_path / "sub-linked"
    _git("worktree", "add", "-b", "sub-feature", str(sub_linked), cwd=submodule)
    for path in (main / "a" / "b", linked / "nested", submodule / "deep" / "er",
                 sub_linked / "x"):
        path.mkdir(parents=True)
    return {"main": main, "linked": linked, "submodule": submodule, "sub_linked": sub_linked}


def test_filesystem_answers_equal_git_across_layouts(layout, no_git):
    main, linked = layout["main"], layout["linked"]
    submodule, sub_linked = layout["submodule"], layout["sub_linked"]
    starts = [
        main, main / "a" / "b", main / "vendor",
        linked, linked / "nested",
        submodule, submodule / "deep" / "er",
        sub_linked, sub_linked / "x",
    ]
    for start in starts:
        _assert_matches_git(start)
    # Spelled out once, so equality with git cannot hide a shared wrong answer.
    assert root._git_common_root(linked / "nested") == main.resolve()
    assert root._git_checkout_root(linked / "nested") == linked.resolve()
    assert root._git_checkout_root(submodule / "deep") == submodule.resolve()
    assert root._git_common_root(submodule) == (main / ".git" / "modules" / "vendor").resolve()


def test_outside_a_repository_is_none_without_git(tmp_path, no_git):
    plain = tmp_path / "plain" / "dir"
    plain.mkdir(parents=True)
    assert _git_answers(plain) == (None, None)
    assert root._git_checkout_root(plain) is None
    assert root._git_common_root(plain) is None


def test_missing_or_file_start_is_none_like_git(tmp_path, no_git):
    repo = _repository(tmp_path / "repo")
    assert root._git_checkout_root(repo / "does-not-exist") is None
    assert root._git_common_root(repo / "README.md") is None
    assert _git_answers(repo / "does-not-exist") == (None, None)


def test_resolve_root_uses_the_filesystem_answer(layout, no_git, monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    resolution = root.resolve_root(layout["linked"] / "nested")
    assert resolution.root == layout["main"].resolve()
    assert resolution.source == "git-common-dir"


def _spy_git(monkeypatch):
    calls = []
    real = root.run_bounded

    def spy(args, **kwargs):
        calls.append(args)
        return real(args, **kwargs)
    monkeypatch.setattr(root, "run_bounded", spy)
    return calls


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                                      "GIT_CEILING_DIRECTORIES"])
def test_discovery_environment_defers_to_git(tmp_path, monkeypatch, variable):
    repo = _repository(tmp_path / "repo")
    value = str(repo / ".git") if variable != "GIT_WORK_TREE" else str(repo)
    monkeypatch.setenv(variable, value)
    calls = _spy_git(monkeypatch)
    assert root._git_checkout_root(repo) == _git_answers(repo)[0]
    assert calls


def test_core_worktree_elsewhere_defers_to_git(tmp_path, monkeypatch):
    repo = _repository(tmp_path / "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git("config", "core.worktree", str(elsewhere).replace("\\", "/"), cwd=repo)
    calls = _spy_git(monkeypatch)
    answer = root._git_checkout_root(repo)
    assert calls
    assert answer == _git_answers(repo)[0]


def test_bare_repository_and_git_dir_starts_defer_to_git(tmp_path, monkeypatch):
    source = _repository(tmp_path / "source")
    bare = tmp_path / "bare.git"
    _git("clone", "--bare", str(source), str(bare))
    for start in (bare, source / ".git", source / ".git" / "refs"):
        calls = _spy_git(monkeypatch)
        assert (root._git_checkout_root(start), root._git_common_root(start)) == _git_answers(start)
        assert calls, start


@pytest.mark.parametrize("content", ["not a gitdir line\n", "gitdir: missing/place\n"])
def test_unusable_git_file_defers_to_git(tmp_path, monkeypatch, content):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text(content, encoding="utf-8")
    calls = _spy_git(monkeypatch)
    assert root._git_checkout_root(work) is None
    assert _git_answers(work) == (None, None)
    assert calls


def test_config_the_parser_cannot_read_plainly_defers_to_git(tmp_path, monkeypatch):
    repo = _repository(tmp_path / "repo")
    with (repo / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write('[include]\n\tpath = extra.config\n')
    calls = _spy_git(monkeypatch)
    assert root._git_checkout_root(repo) == repo.resolve()
    assert calls


def test_core_settings_of_a_missing_config_is_empty(tmp_path):
    assert root._core_settings(tmp_path / "missing") == {}


@pytest.mark.parametrize("text,expected", [
    ("[core]\n\tbare = false\n\tworktree = ../x\n", {"bare": "false", "worktree": "../x"}),
    ("[Core]\nBARE\n", {"bare": "true"}),
    ("[core] bare = true ; comment\n", {"bare": "true"}),
    ("[remote \"origin\"]\n\tworktree = nope\n[core]\n", {}),
    ("[core]\n\tworktree = \"C:/quoted\"\n", None),
    ("[includeIf \"gitdir:x\"]\n\tpath = y\n", None),
])
def test_core_settings_reads_only_plain_core_keys(tmp_path, text, expected):
    config = tmp_path / "config"
    config.write_text(text, encoding="utf-8")
    assert root._core_settings(config) == expected
