# User intent: the operator's `serve init pack|pack --undo|--from` commands map onto the migration functions with the
# documented exit codes and output, and --help never imports the server (team-server-014 Step 10).
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.serve import cli, migration
from taskmaster.serve.config import ServeRefused, ServeUnavailable

import migration_helpers as mh
from migration_helpers import URL, cli as run_cli, g, run
from test_native_service import root  # noqa: F401  (fixture)

pytestmark = pytest.mark.xdist_group("heavy_processes")


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    fence.reset_for_tests()


def test_exit_codes_map_from_the_migration_errors(tmp_path, monkeypatch, capsys):
    cases = [(migration.MigrationFailed("half"), 1), (migration.MigrationRefused("no"), 2), (ServeUnavailable("busy"), 69)]
    for error, code in cases:
        def boom(*args, **kwargs):
            raise error
        monkeypatch.setattr(migration, "pack", boom)
        monkeypatch.setattr(migration, "init", boom)
        assert cli.main(["init", "pack", "--root", str(tmp_path), "--url", URL, "--out", str(tmp_path / "x")]) == code
        assert cli.main(["init", "--root", str(tmp_path), "--from", str(tmp_path / "x")]) == code
        assert str(error) in capsys.readouterr().err


def test_pack_requires_url_and_out_unless_undo(tmp_path, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["init", "pack", "--root", str(tmp_path)])
    assert caught.value.code == 2


def test_undo_without_not_pushed_is_refused(tmp_path, monkeypatch):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    assert cli.main(["init", "pack", "--undo", "--root", str(tmp_path)]) == 2


def test_the_init_commands_work_end_to_end_in_separate_processes(root, tmp_path):  # noqa: F811
    src = mh.make_source(root)
    host = tmp_path / "host"
    run(tmp_path, "clone", "-q", str(src), str(host))
    for key, value in (("user.name", "Host"), ("user.email", "host@example.invalid")):
        run(host, "config", key, value)
    (tmp_path / "out").mkdir()
    bundle = tmp_path / "out" / "p.tmbundle"
    bad = run_cli("serve", "init", "pack", "--root", str(src), "--url", "http://example.com/mcp", "--out", str(bundle))
    assert bad.returncode == 2 and "--url" in bad.stderr and not bundle.exists()
    packed = run_cli("serve", "init", "pack", "--root", str(src), "--url", URL, "--out", str(bundle))
    assert packed.returncode == 0, packed.stdout + packed.stderr
    assert migration.rollout_rule() in packed.stdout and migration.PUSH_REJECTED_RULE in packed.stdout
    assert bundle.exists() and sm.read_marker(src) is not None
    undone = run_cli("serve", "init", "pack", "--undo", "--not-pushed", "--root", str(src), "--json")
    assert undone.returncode == 0 and json.loads(undone.stdout)["state"] == "undone"
    assert sm.read_marker(src) is None
    again = tmp_path / "out" / "q.tmbundle"
    repacked = run_cli("serve", "init", "pack", "--root", str(src), "--url", URL, "--out", str(again), "--json")
    assert repacked.returncode == 0, repacked.stdout + repacked.stderr
    assert json.loads(repacked.stdout)["state"] == "packed"
    run(host, "pull", "-q", "--ff-only", "origin", "main")
    installed = run_cli("serve", "init", "--root", str(host), "--from", str(again))
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert migration.rollout_rule() in installed.stdout and migration.HOST_PULL_RULE in installed.stdout
    assert g(host, "rev-parse", "--verify", "refs/heads/taskmaster-state")
    second = run_cli("serve", "init", "--root", str(host), "--from", str(again), "--json")
    assert second.returncode == 0 and json.loads(second.stdout)["state"] == "installed"


def test_init_help_never_imports_the_server():
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from taskmaster.serve import cli\n"
            "for argv in (['init', '--help'], ['init', 'pack', '--help']):\n"
            "    try:\n        cli.main(argv)\n    except SystemExit:\n        pass\n"
            "print(sorted(m for m in ('uvicorn', 'taskmaster.backlog_server') if m in sys.modules))\n"
            ) % str(mh.PLUGIN)
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip().endswith("[]"), result.stdout + result.stderr


def test_the_module_docstring_lists_the_new_commands():
    for line in ("serve init pack --root", "serve init pack --undo --not-pushed", "serve init --root"):
        assert line in cli.__doc__


def test_git_and_export_errors_exit_1_with_the_message(tmp_path, monkeypatch, capsys):
    for error in (migration.untrack.UntrackError("git broke"), migration.se.ExportError("export broke")):
        def boom(*args, **kwargs):
            raise error
        monkeypatch.setattr(migration, "pack", boom)
        assert cli.main(["init", "pack", "--root", str(tmp_path), "--url", URL, "--out", str(tmp_path / "x")]) == 1
        assert str(error) in capsys.readouterr().err


def test_the_head_guard_fails_closed_for_the_daemon_when_git_cannot_answer(tmp_path, monkeypatch):
    from taskmaster.serve import untrack
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)

    def broken(*args, **kwargs):
        raise OSError("no git")
    monkeypatch.setattr(untrack, "git_for", broken)
    assert untrack.head_tracks_taskmaster(tmp_path) is None
    assert "unknown" in untrack.head_tracks_taskmaster(tmp_path, closed=True)
