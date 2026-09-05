"""User intent: `.taskmaster/linear.yaml` is shared state under a backlog root,
so adding a workspace must not be a read-modify-write two agents can both win.
Both additions have to survive, and the guard has to notice if anything writes
that file outside the store again.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import taskmaster_v3 as v3
from tests import conftest as tests_conftest


def _config(root: Path) -> dict:
    return yaml.safe_load((root / ".taskmaster" / "linear.yaml").read_text(encoding="utf-8"))


def test_two_concurrent_workspace_additions_both_survive(tm_epic_phase, monkeypatch):
    """Reading the file, appending and truncating it outside any lock let two
    agents both report success while only one addition landed."""
    root = tm_epic_phase
    real_validate = v3._validate_linear_config
    first = threading.Event()

    def slow_validate(cfg):
        # Widen the read-modify-write window for the first writer only.
        if not first.is_set():
            first.set()
            time.sleep(0.4)
        return real_validate(cfg)

    monkeypatch.setattr(v3, "_validate_linear_config", slow_validate)

    results: dict[str, str] = {}

    def add(alias: str) -> None:
        results[alias] = bs.backlog_linear_bootstrap_apply(
            workspace_alias=alias,
            team_id=f"team-{alias}",
            token_env=f"TOKEN_{alias.upper()}",
            default_workspace=False,
        )

    threads = [threading.Thread(target=add, args=(alias,)) for alias in ("aa", "bb")]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    for thread in threads:
        thread.join(timeout=15)

    for alias, result in results.items():
        assert '"ok": true' in result.lower(), f"{alias}: {result}"
    aliases = {ws["alias"] for ws in _config(root)["workspaces"]}
    assert aliases == {"aa", "bb"}, aliases


def test_the_bypass_guard_covers_the_linear_config(tm_epic_phase):
    """A root configuration file under a backlog directory is guarded state,
    like the projection files beside it."""
    target = tm_epic_phase / ".taskmaster" / "linear.yaml"
    assert tests_conftest._guard_path(target) is not None


def test_the_bypass_guard_intercepts_raw_write_opens(tm_epic_phase):
    """`Path.open("w")` truncates just as thoroughly as `write_text`, so the
    guard has to see it too — otherwise a bypass just changes primitive."""
    assert Path.open is not tests_conftest._REAL_PATH_OPEN
    guarded = tm_epic_phase / ".taskmaster" / "bugs" / "B-999.md"
    assert tests_conftest._guard_path(guarded) is not None
