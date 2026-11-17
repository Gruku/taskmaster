# User intent: the operator's `serve marker` commands write the canonical marker with the host id and the latch,
# show the state, and clear the latch only once the marker is gone, without importing the server (team-server-009
# Step 10).
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.serve import cli

from test_server_fence import PLUGIN_ROOT, URL, project, repo  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    assert not fence.reset_for_tests()


def test_write_creates_host_id_marker_latch_and_prints_the_git_hint(repo, capsys):
    project(repo)
    assert cli.main(["marker", "write", "--root", str(repo), "--url", URL]) == 0
    out = capsys.readouterr().out
    assert "git add .taskmaster-server.json" in out
    marker = sm.read_marker(repo)
    assert marker.url == URL and marker.host_id == fence.read_host_id(repo)
    assert sm.read_latch(repo) == marker
    first = sm.marker_path(repo).read_bytes()
    assert cli.main(["marker", "write", "--root", str(repo), "--url", URL]) == 0
    assert sm.marker_path(repo).read_bytes() == first


def test_rotate_changes_marker_and_latch(repo):
    project(repo)
    cli.main(["marker", "write", "--root", str(repo), "--url", URL])
    before = sm.read_marker(repo).host_id
    cli.main(["marker", "write", "--root", str(repo), "--url", URL, "--rotate-host-id"])
    after = sm.read_marker(repo)
    assert after.host_id != before and sm.read_latch(repo).host_id == after.host_id == fence.read_host_id(repo)


def test_a_bad_url_exits_2_and_writes_nothing(repo):
    project(repo)
    assert cli.main(["marker", "write", "--root", str(repo), "--url", "http://example.com/mcp"]) == 2
    assert not sm.marker_path(repo).exists() and fence.read_host_id(repo) is None


def test_write_needs_a_host_clone(repo):
    assert cli.main(["marker", "write", "--root", str(repo), "--url", URL]) == 2
    assert not sm.marker_path(repo).exists()


def test_show_json_reports_the_fields(repo, capsys):
    project(repo)
    cli.main(["marker", "write", "--root", str(repo), "--url", URL])
    capsys.readouterr()
    assert cli.main(["marker", "show", "--root", str(repo), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["marker"]["url"] == URL and report["latch"]["url"] == URL
    assert report["host_id_matches"] is True and report["host_lease_held"] is False
    assert report["marker_tracked"] is False and report["marker_error"] is None


def test_clear_latch_refuses_while_the_marker_is_present(repo):
    project(repo)
    cli.main(["marker", "write", "--root", str(repo), "--url", URL])
    assert cli.main(["marker", "clear-latch", "--root", str(repo)]) == 2
    assert sm.latch_path(repo).exists()
    sm.marker_path(repo).unlink()
    assert cli.main(["marker", "clear-latch", "--root", str(repo)]) == 0
    assert not sm.latch_path(repo).exists()
    assert cli.main(["marker", "clear-latch", "--root", str(repo)]) == 0       # nothing left: still exit 0


def test_marker_help_never_imports_the_server():
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from taskmaster.serve import cli\n"
            "try:\n    cli.main(['marker', 'show', '--help'])\nexcept SystemExit:\n    pass\n"
            "print(sorted(m for m in ('uvicorn', 'taskmaster.backlog_server') if m in sys.modules))\n"
            ) % str(PLUGIN_ROOT)
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip().endswith("[]"), result.stdout + result.stderr


def test_clear_latch_from_a_linked_worktree_clears_the_main_checkouts_latch(repo, tmp_path):
    from test_server_fence import git
    project(repo)
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "--allow-empty", "-m", "x", cwd=repo)
    linked = tmp_path / "linked"
    git("worktree", "add", str(linked), cwd=repo)
    cli.main(["marker", "write", "--root", str(repo), "--url", URL])
    sm.marker_path(repo).unlink()
    assert cli.main(["marker", "clear-latch", "--root", str(linked)]) == 0
    assert not sm.latch_path(repo).exists()
