"""N13 native merge preserves absence, scalar types and operational authority."""
import pytest

from taskmaster.native.sync_merge import merge, protect_local


def test_disjoint_field_body_and_unknown_field_edits():
    base = ({"title": "old", "unknown": {"custom": 1}}, "old prose")
    ours = ({"title": "database", "unknown": {"custom": 1}}, "old prose")
    theirs = ({"title": "old", "unknown": {"custom": 2}}, "file prose")
    assert merge(base, ours, theirs) == (({"title": "database", "unknown": {"custom": 2}}, "file prose"), [])


def test_overlap_retains_database_and_names_external_conflict():
    assert merge(({"title": "base"}, None), ({"title": "database"}, None),
                 ({"title": "file"}, None)) == (({"title": "database"}, None), ["title"])


def test_absence_null_boolean_and_number_are_distinct_values():
    assert merge(({"x": None, "y": 0}, None), ({"y": False}, None),
                 ({"x": "file", "y": 1}, None)) == (({"y": False}, None), ["x", "y"])


def test_file_cannot_claim_a_new_task_or_replace_a_current_claim():
    source = {"id": "a", "locked_by": "stale", "claim_expires": "forged", "status": "in-progress"}
    assert protect_local("task", source, None) == {"id": "a", "status": "in-progress"}
    current = {"locked_by": "live", "claim_expires": "real", "claim_expires_for": "live"}
    assert protect_local("task", source, current) == dict(current, id="a", status="in-progress")


def test_terminal_transition_releases_claim_and_reopen_never_resurrects_it():
    current = {"status": "in-progress", "locked_by": "live", "claim_expires": "real"}
    assert protect_local("task", {"status": "done"}, current) == {"status": "done"}
    current["status"] = "done"
    assert protect_local("task", {"status": "todo"}, current) == {"status": "todo"}


def test_archive_path_import_cannot_keep_or_revive_a_claim():
    current = {"status": "in-progress", "locked_by": "live"}
    assert protect_local("task", {"status": "in-progress", "archived": True}, current) == {
        "status": "in-progress", "archived": True}
    current["archived"] = True
    assert protect_local("task", {"status": "in-progress"}, current) == {"status": "in-progress"}


def test_indexes_and_push_receipts_remain_local_but_authored_unknowns_survive():
    assert protect_local("backlog", {"bugs": "stale", "custom": 1}, {"bugs": ["current"]}) == {
        "bugs": ["current"], "custom": 1}
    assert protect_local("tracker", {"last_pushed": "stale", "external_key": "new", "custom": 2},
                         {"last_pushed": "current"}) == {
        "last_pushed": "current", "external_key": "new", "custom": 2}


@pytest.mark.parametrize("source", [{}, {"linear_issue_id": "stale-uuid"}])
def test_tracker_import_cannot_erase_or_replace_durable_remote_identity(source):
    assert protect_local("tracker", source, {"linear_issue_id": "current-uuid"}) == {
        "linear_issue_id": "current-uuid"}
    assert "linear_issue_id" not in protect_local("tracker", source, None)
