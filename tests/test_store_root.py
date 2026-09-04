"""Root resolution and filesystem-policy contracts for the SQLite store."""
from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest
import yaml

from taskmaster import store


@pytest.fixture(autouse=True)
def _isolated_store_state(monkeypatch):
    """Keep process-level root/warning/connection state out of adjacent tests."""
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _write_projection(root: Path, *, project: str = "test-project") -> Path:
    backlog_path = root / ".taskmaster"
    backlog_path.mkdir(parents=True, exist_ok=True)
    document = {
        "version": 4,
        "project": project,
        "meta": {
            "schema_version": 4,
            "projection_schema": store.PROJECTION_SCHEMA,
        },
        "epics": [],
        "phases": [],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repository(path: Path) -> None:
    _git("init", "-b", "main", str(path))
    _git("config", "user.email", "store-tests@example.invalid", cwd=path)
    _git("config", "user.name", "Store Tests", cwd=path)
    (path / "README.md").write_text("store root fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "fixture", cwd=path)


def _assert_resolution(resolution, expected_root: Path, source: str) -> None:
    expected = expected_root.resolve()
    assert resolution.root == expected
    assert resolution.backlog_path == expected / ".taskmaster"
    assert resolution.source == source
    assert resolution.root.is_absolute()
    assert resolution.backlog_path.is_absolute()


def test_explicit_root_wins_over_environment_and_git(tmp_path, monkeypatch):
    git_root = tmp_path / "git-root"
    _init_repository(git_root)
    start = git_root / "nested"
    start.mkdir()

    env_root = tmp_path / "environment-root"
    env_root.mkdir()
    explicit_root = tmp_path / "explicit-root"
    explicit_root.mkdir()
    monkeypatch.setenv("TASKMASTER_ROOT", str(env_root))

    resolution = store.resolve_root(start, explicit_root=explicit_root)

    _assert_resolution(resolution, explicit_root, "explicit")


def test_environment_root_wins_over_git_discovery(tmp_path, monkeypatch):
    git_root = tmp_path / "git-root"
    _init_repository(git_root)
    start = git_root / "nested"
    start.mkdir()

    env_root = tmp_path / "environment-root"
    env_root.mkdir()
    monkeypatch.setenv("TASKMASTER_ROOT", str(env_root / "."))

    resolution = store.resolve_root(start)

    _assert_resolution(resolution, env_root, "env")


def test_main_checkout_uses_parent_of_git_common_dir(tmp_path):
    repo = tmp_path / "main"
    _init_repository(repo)
    start = repo / "a" / "b"
    start.mkdir(parents=True)

    resolution = store.resolve_root(start)

    _assert_resolution(resolution, repo, "git-common-dir")


def test_linked_worktree_uses_main_checkout_store(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repository(main)
    _git("worktree", "add", "-b", "feature/store-test", str(linked), cwd=main)
    start = linked / "nested"
    start.mkdir()

    resolution = store.resolve_root(start)

    _assert_resolution(resolution, main, "git-common-dir")
    assert resolution.backlog_path != linked / ".taskmaster"


def test_default_open_from_linked_worktree_uses_main_checkout_projection(
    tmp_path, monkeypatch
):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repository(main)
    main_backlog = _write_projection(main, project="main-authority")
    _git("worktree", "add", "-b", "feature/store-open", str(linked), cwd=main)
    monkeypatch.chdir(linked)

    opened = store.open_store(session="linked-default-open")

    assert opened.backlog_path == main_backlog.resolve()
    assert opened.load_dict()["project"] == "main-authority"
    assert not (linked / ".taskmaster" / "local" / "store.db").exists()


def test_explicit_linked_worktree_backlog_uses_main_checkout_store(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _init_repository(main)
    main_backlog = _write_projection(main, project="main-explicit-authority")
    _git("worktree", "add", "-b", "feature/store-explicit", str(linked), cwd=main)

    opened = store.open_store(
        backlog_path=linked / ".taskmaster" / "backlog.yaml",
        session="linked-explicit-open",
    )

    assert opened.backlog_path == main_backlog.resolve()
    assert opened.load_dict()["project"] == "main-explicit-authority"


def test_explicit_nested_backlog_inside_checkout_is_not_redirected(tmp_path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    nested_backlog = _write_projection(repo / ".tmp" / "fixture", project="isolated")

    opened = store.open_store(backlog_path=nested_backlog, session="nested-explicit")

    assert opened.backlog_path == nested_backlog.resolve()
    assert opened.load_dict()["project"] == "isolated"


def test_repeated_explicit_open_reuses_cached_git_resolution(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    backlog_path = _write_projection(root)
    calls = 0
    real_checkout_root = store._git_checkout_root

    def counting_checkout_root(start):
        nonlocal calls
        calls += 1
        return real_checkout_root(start)

    monkeypatch.setattr(store, "_git_checkout_root", counting_checkout_root)
    store.open_store(backlog_path=backlog_path, session="resolution-one")
    store.open_store(backlog_path=backlog_path, session="resolution-two")

    assert calls == 1


def test_non_git_start_directory_is_the_absolute_normalized_fallback(
    tmp_path, monkeypatch
):
    start = tmp_path / "parent" / "child"
    start.mkdir(parents=True)
    non_normalized = start / ".." / "child" / "."

    def no_repository(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    # The suite's basetemp may itself sit inside this repository. Inject the
    # non-git discovery result so this remains a root-resolution unit test.
    monkeypatch.setattr(store.subprocess, "run", no_repository)
    resolution = store.resolve_root(non_normalized)

    _assert_resolution(resolution, start, "cwd")


def test_db_path_is_under_the_backlog_local_directory(tmp_path):
    backlog_path = (tmp_path / "repo" / ".taskmaster").resolve()

    assert store.db_path(backlog_path) == backlog_path / "local" / "store.db"
    assert store.DB_RELPATH == Path("local") / "store.db"


def test_network_filesystem_allows_reads_but_refuses_write_transactions(
    tmp_path, monkeypatch
):
    root = tmp_path / "network-project"
    backlog_path = _write_projection(root, project="network-read")

    # Bootstrap the fixture while it is still classified as host-local. The
    # injected probe then gives deterministic network behavior on every OS.
    store.open_store(root=root, session="local-bootstrap")
    assert store.load_dict(backlog_path)["project"] == "network-read"
    store.reset_for_tests()
    local_ignore = backlog_path / "local" / ".gitignore"
    local_ignore.unlink()
    monkeypatch.setattr(
        store,
        "_network_filesystem_reason",
        lambda _path: "network filesystem test double (WAL is unsafe)",
    )

    assert store.load_dict(backlog_path)["project"] == "network-read"
    assert not local_ignore.exists()
    with pytest.raises(RuntimeError, match=r"(?i)(network.*WAL|WAL.*network)"):
        with store.transaction(tool="network-write", backlog_path=backlog_path):
            pass


def test_fresh_network_filesystem_reads_projection_without_creating_local_files(
    tmp_path, monkeypatch
):
    root = tmp_path / "fresh-network-project"
    backlog_path = _write_projection(root, project="fresh-network-read")
    monkeypatch.setattr(
        store,
        "_network_filesystem_reason",
        lambda _path: "network filesystem test double (WAL is unsafe)",
    )

    opened = store.open_store(backlog_path=backlog_path, session="fresh-network")

    assert opened.load_dict()["project"] == "fresh-network-read"
    assert opened.status().warning.startswith("network filesystem")
    assert not (backlog_path / "local").exists()


def test_checkpoint_all_skips_network_stores(tmp_path, monkeypatch):
    root = tmp_path / "network-checkpoint"
    backlog_path = _write_projection(root)
    monkeypatch.setattr(
        store,
        "_network_filesystem_reason",
        lambda _path: "network filesystem test double (WAL is unsafe)",
    )
    store.open_store(backlog_path=backlog_path, session="network-checkpoint")

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("checkpoint attempted a SQLite connection on network storage")

    monkeypatch.setattr(store.sqlite3, "connect", forbidden_connect)
    store.checkpoint_all()


def test_cloud_synced_path_warns_once_per_process_and_remains_usable(tmp_path):
    root = tmp_path / "OneDrive" / "cloud-project"
    backlog_path = _write_projection(root, project="cloud-project")

    resolution = store.resolve_root(root, explicit_root=root)
    assert resolution.filesystem_warning is not None
    assert "cloud" in resolution.filesystem_warning.lower()
    store.reset_for_tests()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        store.open_store(root=root, session="cloud-one")
        store.open_store(root=root, session="cloud-two")

    cloud_warnings = [
        item for item in caught if "cloud" in str(item.message).lower()
    ]
    assert len(cloud_warnings) == 1
    assert store.load_dict(backlog_path)["project"] == "cloud-project"
