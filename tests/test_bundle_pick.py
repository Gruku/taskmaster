"""Task bundle: pick binds the whole bundle into one worktree."""
import backlog_server


def _add(task_id, bundle="ux", sub_repo="", lane=""):
    backlog_server.backlog_add_task(
        title=task_id, epic="test-epic", phase="dev",
        task_id=task_id, bundle=bundle, sub_repo=sub_repo)
    if lane:
        backlog_server.backlog_update_task(task_id, "lane", lane)


def test_pick_member_binds_all_members(tm_epic_phase):
    _add("b-1"); _add("b-2")
    out = backlog_server.backlog_pick_task("b-1")
    assert "b-1" in out and "b-2" in out                      # announces membership
    t1 = backlog_server.backlog_get_task("b-1")
    t2 = backlog_server.backlog_get_task("b-2")
    assert "in-progress" in t1 and "in-progress" in t2        # both bound
    assert "feature/ux" in out and ".worktrees/ux" in out     # shared, slug-based


def test_pick_records_session_bundle(tm_epic_phase):
    _add("b-1"); _add("b-2")
    backlog_server.backlog_pick_task("b-1")
    sb = backlog_server._get_session_bundle()
    assert sb and sb["slug"] == "ux" and set(sb["members"]) == {"b-1", "b-2"}


def test_pick_uses_strictest_lane(tm_epic_phase):
    _add("b-1", lane="express"); _add("b-2", lane="full")
    out = backlog_server.backlog_pick_task("b-1")
    assert "full" in out.lower()                              # bundle execution lane


def test_repick_bound_bundle_idempotent(tm_epic_phase):
    _add("b-1"); _add("b-2")
    backlog_server.backlog_pick_task("b-1")
    out = backlog_server.backlog_pick_task("b-2")             # re-pick another member
    assert "feature/ux" in out and "error" not in out.lower()


def test_non_bundle_pick_unchanged(tm_epic_phase):
    backlog_server.backlog_add_task(title="solo", epic="test-epic", phase="dev", task_id="s-1")
    out = backlog_server.backlog_pick_task("s-1")
    assert "feature/s-1" in out                               # per-task path preserved
