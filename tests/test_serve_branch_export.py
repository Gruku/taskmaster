# User intent: a restore must bring back which branch gates which task for each member, or the merge gate would find
# nothing and answer ALLOW. Branch rows ride the state export and come back through the operator-only clone.restore
# (team-server-011 D10, Step 9).
from __future__ import annotations

import json

import pytest

from taskmaster.coordinator import allowlist
from taskmaster.coordinator.actor_resolver import LOCAL
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import clone_fields
from taskmaster.serve import state_export as se
from native_git_helpers import git
from native_twins import native_connection
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_export_cli import BRANCH, TASK, repo, run_cli, submit  # noqa: F401  (fixtures)

pytestmark = pytest.mark.xdist_group("heavy_processes")

CLONE = "c-" + "a" * 16
ROW = {"handle": "ann", "clone": CLONE, "kind": "task", "id": TASK, "field": "branch", "value": "fix/login"}


def tree_file(repo, path):
    return git(repo, "show", f"{BRANCH}:{path}")


def test_the_export_holds_the_branch_rows_and_a_restore_brings_them_back(repo, capsys):
    with Coordinator(repo) as owner:
        submit(owner, "r1", "clone.restore", ROW)
    assert run_cli("export", "run", "--root", str(repo)) == 0
    rows = json.loads(tree_file(repo, "clone-fields/branches.json"))
    assert rows == [{"handle": "ann", "clone": CLONE, "kind": "task", "id": TASK, "branch": "fix/login"}]
    with native_connection(repo) as connection:                      # the rows are lost, as in a rebuilt store
        connection.execute("DELETE FROM clone_fields")
    capsys.readouterr()
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", BRANCH) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["branches_restored"] == 1 and report["branches_refused"] == [] and report["branches_failed"] == []
    with native_connection(repo) as connection:
        assert clone_fields.branch_matches(connection, "fix/login") == [("ann", CLONE, TASK)]
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", BRANCH) == 0   # idempotent
    assert json.loads(capsys.readouterr().out)["branches_restored"] == 1


def test_no_rows_means_no_file(repo):
    assert run_cli("export", "run", "--root", str(repo)) == 0
    with pytest.raises(Exception):
        tree_file(repo, "clone-fields/branches.json")


def test_a_row_whose_task_is_gone_is_refused_and_reported(repo, capsys):
    with Coordinator(repo) as owner:
        submit(owner, "r1", "clone.restore", ROW)
    assert run_cli("export", "run", "--root", str(repo)) == 0
    with native_connection(repo) as connection:
        connection.execute("DELETE FROM clone_fields")
        connection.execute("UPDATE entity_core SET deleted=1 WHERE public_id=?", (TASK,))
    capsys.readouterr()
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", BRANCH) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["branches_restored"] == 0 and len(report["branches_refused"]) == 1


@pytest.mark.parametrize("change", [{"handle": "Ann Smith"}, {"clone": "c-xyz"}, {"kind": "epic"}, {"field": "worktree"},
                                    {"value": "a..b"}, {"value": ""}, {"id": "../x"}, {"extra": 1}])
def test_the_command_refuses_a_malformed_row(change):
    with pytest.raises(ValueError):
        clone_fields.validate("clone.restore", {**ROW, **change})


def test_only_the_host_operator_can_send_it():
    plain = {"protocol": 2, "store_id": "s", "caller_scope": "x", "request_id": "r", "operation": "clone.restore",
             "arguments": ROW, "expected_revisions": []}
    batch = {**plain, "operation": "batch", "arguments": {"commands": [{"operation": "clone.restore", "arguments": ROW}]}}
    for body in (plain, batch):
        assert "clone.restore" in (allowlist.refusal("execute", {"envelope": body}, "0123456789abcdef") or "")
        assert allowlist.refusal("execute", {"envelope": body}, allowlist.OPERATOR) is None
    from taskmaster.native import batch_commands
    assert "clone.restore" in batch_commands.INTERNAL_OPERATIONS          # nor through the tool's `commands=` form


def test_a_branch_row_written_with_no_event_still_reaches_the_next_export(repo):
    """Review minor 2: a fully diverted branch write moves no event, so the skip test must watch the rows too."""
    assert run_cli("export", "run", "--root", str(repo)) == 0
    assert run_cli("export", "run", "--root", str(repo)) == 0          # settles into the skip path
    with native_connection(repo) as connection:
        clone_fields.put(connection, "ann", CLONE, "task", TASK, "branch", "fix/quiet", 1)
    assert run_cli("export", "run", "--root", str(repo)) == 0
    rows = json.loads(tree_file(repo, "clone-fields/branches.json"))
    assert [r["branch"] for r in rows] == ["fix/quiet"]


def test_a_branch_row_whose_value_alone_changes_reaches_the_next_export(repo):
    """Re-round minor 2: same row count, same seq, new value."""
    with native_connection(repo) as connection:
        clone_fields.put(connection, "ann", CLONE, "task", TASK, "branch", "fix/one", 5)
    assert run_cli("export", "run", "--root", str(repo)) == 0
    assert run_cli("export", "run", "--root", str(repo)) == 0
    with native_connection(repo) as connection:
        clone_fields.put(connection, "ann", CLONE, "task", TASK, "branch", "fix/two", 5)
    assert run_cli("export", "run", "--root", str(repo)) == 0
    assert [r["branch"] for r in json.loads(tree_file(repo, "clone-fields/branches.json"))] == ["fix/two"]
