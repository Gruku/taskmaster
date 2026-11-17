# User intent: keep the split-brain fence from leaking into the suite: a leaked host lease would exempt later tests,
# and a stray marker or latch in the plugin repo would fence every test that resolves to it (team-server-009 Step 11).
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import check_no_leaked_leases
from taskmaster import server_fence as fence
from taskmaster import server_marker as sm

REPO = Path(__file__).resolve().parents[1]


def test_the_lease_guard_fails_on_a_leaked_lease_and_releases_it(tmp_path):
    root = tmp_path / "proj"
    (root / ".taskmaster" / "local").mkdir(parents=True)
    fence.claim_host(root)                       # deliberately not released
    with pytest.raises(AssertionError, match="leaked serve host leases"):
        check_no_leaked_leases()
    assert not fence.host_lease_held(root)       # the guard released it
    check_no_leaked_leases()


def test_the_plugin_repo_has_no_marker_and_no_latch():
    assert not (REPO / sm.MARKER_NAME).exists(), "a marker in the plugin repo would fence the whole suite"
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=REPO,
                            capture_output=True, text=True, timeout=30)
    if common.returncode == 0:
        latch = Path(common.stdout.strip()) / sm.LATCH_NAME
        assert not latch.exists(), (f"{latch} is invisible to git status but fences every test that resolves to "
                                    "this repo; run `taskmaster serve marker clear-latch --root <repo>`")
