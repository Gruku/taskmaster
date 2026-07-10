"""Task bundle: pick binds the whole bundle into one worktree."""
from taskmaster import backlog_server


def _add(task_id, bundle="ux", sub_repo="", lane=""):
    backlog_server.backlog_add_task(title=task_id, epic="test-epic", phase="dev", bundle=bundle, options={"task_id": task_id, "sub_repo": sub_repo})
    if lane:
        backlog_server.backlog_update_task(task_id, "lane", lane)


def _satisfy_gates(task_id):
    """Record blocking gates as passed so backlog_complete_task can proceed."""
    data = backlog_server._load()
    t, _ = backlog_server._find_task(data, task_id)
    lane = t.get("lane", "")
    from taskmaster import taskmaster_v3 as tv
    for g in tv.blocking_gates(lane):
        backlog_server.backlog_record_gate(task_id, g, verdict="pass")


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
    backlog_server.backlog_add_task(title="solo", epic="test-epic", phase="dev", options={"task_id": "s-1"})
    out = backlog_server.backlog_pick_task("s-1")
    assert "feature/s-1" in out                               # per-task path preserved


def test_complete_last_member_clears_session_bundle(tm_epic_phase):
    """Completing the last bundle member must clear _session_bundle."""
    _add("b-1"); _add("b-2")
    backlog_server.backlog_pick_task("b-1")          # binds both; session bundle set
    assert backlog_server._get_session_bundle() is not None

    _satisfy_gates("b-1")
    out1 = backlog_server.backlog_complete_task("b-1")
    assert "Error" not in out1 and "Cannot" not in out1
    # b-2 still in-progress → bundle must remain set
    assert backlog_server._get_session_bundle() is not None

    _satisfy_gates("b-2")
    out2 = backlog_server.backlog_complete_task("b-2")
    assert "Error" not in out2 and "Cannot" not in out2
    # all members done → session bundle must now be cleared
    assert backlog_server._get_session_bundle() is None


def test_complete_one_member_keeps_session_bundle(tm_epic_phase):
    """Completing only one of two bundle members must NOT clear _session_bundle."""
    _add("b-1"); _add("b-2")
    backlog_server.backlog_pick_task("b-1")          # binds both
    assert backlog_server._get_session_bundle() is not None

    _satisfy_gates("b-1")
    out = backlog_server.backlog_complete_task("b-1")
    assert "Error" not in out and "Cannot" not in out
    # b-2 still in-progress → bundle must remain set
    sb = backlog_server._get_session_bundle()
    assert sb is not None and sb["slug"] == "ux"
