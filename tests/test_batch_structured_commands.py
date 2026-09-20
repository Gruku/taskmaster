"""User intent: `backlog_batch_update` must offer several mutations in ONE atomic
transaction with revision preconditions (N09 S14, D5) without disturbing the
`operations` line form, which is deliberately partial. The two contracts are
opposite — partial-apply versus all-or-nothing — so the tool must refuse to mix
them rather than silently pick one, and a failed precondition must leave nothing
applied and never render a partial summary.

The form contract binds both stores; the atomic behaviour is native-only, because
revisions and a single all-or-nothing transaction exist only in the native core.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from native_twins import activate_native, committed, is_native


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def legacy_project(tm_epic_phase):
    assert not is_native(tm_epic_phase)
    return tm_epic_phase


@pytest.fixture
def native_project(tm_epic_phase):
    activate_native(tm_epic_phase)
    assert is_native(tm_epic_phase)
    return tm_epic_phase


@pytest.fixture(params=["legacy", "native"])
def either_project(request, tm_epic_phase):
    """The argument-form contract is the tool's own and holds on either store."""
    if request.param == "native":
        activate_native(tm_epic_phase)
        assert is_native(tm_epic_phase)
    return tm_epic_phase


def _seed_task() -> None:
    added = bs.backlog_add_task(title="Child", epic="test-epic", phase="dev", tldr="c")
    assert "Error" not in added, added


def _title(root: Path, task_id: str) -> str:
    store.reset_for_tests()
    if is_native(root):
        return committed(root)[("task", task_id)][0]["title"]
    with store.transaction(backlog_path=root / ".taskmaster" / "backlog.yaml", tool="test-read") as tx:
        return dict(tx.get("task", task_id))["title"]


def _revision(root: Path, kind: str, ident: str) -> int:
    store.reset_for_tests()
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        return connection.execute("SELECT revision FROM entity_core WHERE kind=? AND public_id=?",
                                  (kind, ident)).fetchone()[0]


def _patch(task_id: str, title: str) -> dict:
    return {"operation": "task.patch", "arguments": {"id": task_id, "set": {"title": title}}}


def _answer(result: str) -> dict:
    """Every answer to a `commands=` call is JSON, so a caller can act on it."""
    assert isinstance(result, str), result
    return json.loads(result)


# ── The two forms must never mix ────────────────────────────────────────────

def test_lines_and_commands_together_are_refused_rather_than_one_being_picked(either_project):
    _seed_task()

    result = _answer(bs.backlog_batch_update(
        operations="update test-epic-001 title FromLines",
        commands=[_patch("test-epic-001", "FromCommands")]))

    assert result["ok"] is False, result
    assert result["error"] == "both_forms", result
    assert result["applied"] is False, result
    assert _title(either_project, "test-epic-001") == "Child", "a refused call applied one of the forms"


def test_atomic_is_refused_on_the_line_form_whose_partiality_is_its_contract(either_project):
    _seed_task()

    for atomic in (True, False):
        result = bs.backlog_batch_update(operations="update test-epic-001 title Renamed", atomic=atomic)
        assert result.startswith("Error:"), result
        assert "atomic" in result, result
        assert _title(either_project, "test-epic-001") == "Child", result


def test_expected_revisions_are_refused_on_the_line_form(either_project):
    _seed_task()

    result = bs.backlog_batch_update(operations="update test-epic-001 title Renamed",
                                     expected_revisions=[{"kind": "task", "id": "test-epic-001", "revision": 1}])

    assert result.startswith("Error:"), result
    assert "expected_revisions" in result, result
    assert _title(either_project, "test-epic-001") == "Child", result


def test_the_commands_form_cannot_be_asked_to_apply_partially(either_project):
    _seed_task()

    result = _answer(bs.backlog_batch_update(commands=[_patch("test-epic-001", "Renamed")], atomic=False))

    assert result["ok"] is False, result
    assert result["error"] == "not_atomic", result
    assert _title(either_project, "test-epic-001") == "Child", result


def test_an_empty_commands_list_is_refused(either_project):
    result = _answer(bs.backlog_batch_update(commands=[]))
    assert result["ok"] is False and result["error"] == "empty_commands", result


def test_more_commands_than_one_transaction_holds_are_refused_whole(either_project):
    _seed_task()

    result = _answer(bs.backlog_batch_update(
        commands=[_patch("test-epic-001", f"Title {n}") for n in range(101)]))

    assert result["ok"] is False and result["error"] == "too_many_commands", result
    assert "100" in result["detail"], result
    assert _title(either_project, "test-epic-001") == "Child", result


def test_the_line_and_viewer_operations_are_refused_by_name(either_project):
    """`task.batch_line` and the viewer writes exist to serve one caller each with
    its own rules. Accepting them here would make `commands=` a second front door
    into the partial line contract and into the viewer's If-Match writes."""
    _seed_task()

    for operation in ("task.batch_line", "epic.batch_line", "task.viewer_update"):
        result = _answer(bs.backlog_batch_update(
            commands=[{"operation": operation, "arguments": {"id": "test-epic-001"}}]))
        assert result["ok"] is False, (operation, result)
        assert result["error"] == "internal_operation", (operation, result)
        assert operation in result["detail"], (operation, result)


def test_an_unknown_operation_is_refused_by_name_with_no_passthrough(either_project):
    result = _answer(bs.backlog_batch_update(
        commands=[{"operation": "task.obliterate", "arguments": {"id": "test-epic-001"}}]))

    assert result["ok"] is False and result["error"] == "invalid_command", result
    assert "task.obliterate" in result["detail"], result


# ── The commands form on a legacy store ─────────────────────────────────────

def test_the_commands_form_refuses_on_a_legacy_store_and_names_the_line_form(legacy_project):
    _seed_task()

    result = _answer(bs.backlog_batch_update(commands=[_patch("test-epic-001", "Renamed")]))

    assert result["ok"] is False and result["error"] == "legacy_store", result
    assert "operations" in result["detail"], result
    assert _title(legacy_project, "test-epic-001") == "Child", result


# ── The commands form on a native store ─────────────────────────────────────

def test_several_commands_commit_in_one_transaction_with_a_receipt(native_project):
    _seed_task()
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev", tldr="s")

    result = _answer(bs.backlog_batch_update(commands=[
        _patch("test-epic-001", "First renamed"),
        _patch("test-epic-002", "Second renamed")]))

    assert result["ok"] is True, result
    assert result["applied"] is True and result["commands"] == 2, result
    receipt = result["receipt"]
    assert receipt["request_id"], receipt
    affected = {(entry["kind"], entry["id"]): entry["revision"] for entry in receipt["affected"]}
    assert set(affected) == {("task", "test-epic-001"), ("task", "test-epic-002")}, receipt
    assert set(affected.values()) == {2}, "both revisions must advance in the one commit"
    # One transaction, one receipt: each entity's own event sequence sits inside
    # the commit, whose sequence is the last one it allocated.
    assert receipt["commit_seq"] == max(entry["last_seq"] for entry in receipt["affected"]), receipt
    assert _title(native_project, "test-epic-001") == "First renamed"
    assert _title(native_project, "test-epic-002") == "Second renamed"


def test_a_matching_revision_precondition_applies(native_project):
    _seed_task()
    revision = _revision(native_project, "task", "test-epic-001")

    result = _answer(bs.backlog_batch_update(
        commands=[_patch("test-epic-001", "Renamed")],
        expected_revisions=[{"kind": "task", "id": "test-epic-001", "revision": revision}]))

    assert result["ok"] is True, result
    assert _title(native_project, "test-epic-001") == "Renamed"


def test_a_failed_precondition_applies_nothing_and_never_summarizes_a_partial(native_project):
    _seed_task()
    bs.backlog_add_task(title="Second", epic="test-epic", phase="dev", tldr="s")
    stale = _revision(native_project, "task", "test-epic-002") + 7

    result = bs.backlog_batch_update(
        commands=[_patch("test-epic-001", "First renamed"), _patch("test-epic-002", "Second renamed")],
        expected_revisions=[{"kind": "task", "id": "test-epic-002", "revision": stale}])

    assert "**Batch update:**" not in result, "a failed precondition rendered the line form's partial summary"
    answer = _answer(result)
    assert answer["ok"] is False and answer["error"] == "precondition_failed", answer
    assert "test-epic-002" in answer["detail"], answer
    assert _title(native_project, "test-epic-001") == "Child", "a failed precondition applied an earlier command"
    assert _title(native_project, "test-epic-002") == "Second"


def test_a_command_the_core_refuses_leaves_every_other_command_unapplied(native_project):
    _seed_task()

    result = _answer(bs.backlog_batch_update(commands=[
        _patch("test-epic-001", "Renamed"),
        _patch("ghost-001", "Nope")]))

    assert result["ok"] is False, result
    assert result["applied"] is False, result
    assert _title(native_project, "test-epic-001") == "Child", "a refused batch committed its good command"
