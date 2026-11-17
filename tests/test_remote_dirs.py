# User intent: a teammate's key and cache directories are owner-only and never reached through a link, in the
# platform's per-user dirs, and the existing store-local private directories behave exactly as before.
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from taskmaster.coordinator import ownership
from taskmaster.remote import dirs


def _link(target: Path, link: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_private_directory_at_creates_an_owner_only_directory(tmp_path):
    path = ownership.private_directory_at(tmp_path / "keys")
    assert path.is_dir()
    ownership.verify_private(path)
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_private_directory_at_is_idempotent(tmp_path):
    first = ownership.private_directory_at(tmp_path / "keys")
    (first / "x").write_text("x", encoding="utf-8")
    assert ownership.private_directory_at(tmp_path / "keys") == first
    assert (first / "x").read_text(encoding="utf-8") == "x"


def test_private_directory_at_refuses_a_link(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    _link(target, tmp_path / "keys")
    with pytest.raises(PermissionError):
        ownership.private_directory_at(tmp_path / "keys")


def test_private_directory_under_the_store_is_unchanged(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    path = ownership.private_directory(tmp_path, "serve")
    assert path == tmp_path / ".taskmaster" / "local" / "serve"
    ownership.verify_private(path)
    with pytest.raises(ValueError):
        ownership.private_directory(tmp_path, "Bad Name")


def test_private_chain_makes_every_part_private(tmp_path):
    leaf = dirs.private_chain(tmp_path / "base", "remote", "abc", "key")
    assert leaf == tmp_path / "base" / "remote" / "abc" / "key"
    for path in (leaf, leaf.parent, leaf.parent.parent):
        ownership.verify_private(path)


def test_private_chain_refuses_a_link_in_the_middle(tmp_path):
    (tmp_path / "base").mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    _link(target, tmp_path / "base" / "remote")
    with pytest.raises(PermissionError):
        dirs.private_chain(tmp_path / "base", "remote", "abc")


def test_overrides_win(tmp_path):
    env = {"TASKMASTER_CACHE_DIR": str(tmp_path / "c"), "TASKMASTER_CONFIG_DIR": str(tmp_path / "k")}
    assert dirs.cache_base(env) == tmp_path / "c"
    assert dirs.config_base(env) == tmp_path / "k"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows dirs")
def test_windows_dirs(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "L"), "APPDATA": str(tmp_path / "R")}
    assert dirs.cache_base(env) == tmp_path / "L" / "taskmaster"
    assert dirs.config_base(env) == tmp_path / "R" / "taskmaster"


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="XDG dirs")
def test_xdg_dirs(tmp_path):
    env = {"XDG_CACHE_HOME": str(tmp_path / "c"), "XDG_CONFIG_HOME": str(tmp_path / "k"), "HOME": str(tmp_path)}
    assert dirs.cache_base(env) == tmp_path / "c" / "taskmaster"
    assert dirs.config_base(env) == tmp_path / "k" / "taskmaster"
    assert dirs.cache_base({"HOME": str(tmp_path)}) == tmp_path / ".cache" / "taskmaster"
