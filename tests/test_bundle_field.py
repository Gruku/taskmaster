"""Task bundle: slug field plumbing + validation."""
from taskmaster import backlog_server


def test_add_task_accepts_bundle(tm_epic_phase):
    backlog_server.backlog_add_task(
        title="A", epic="test-epic", phase="dev", task_id="b-1", bundle="asset-ux")
    out = backlog_server.backlog_get_task("b-1", verbose=True)
    assert "asset-ux" in out


def test_bundle_present_in_slim_read(tm_epic_phase):
    backlog_server.backlog_add_task(
        title="A", epic="test-epic", phase="dev", task_id="b-1", bundle="asset-ux")
    slim = backlog_server.backlog_get_task("b-1")  # slim default
    assert "asset-ux" in slim


def test_bad_slug_rejected_on_add(tm_epic_phase):
    out = backlog_server.backlog_add_task(
        title="A", epic="test-epic", phase="dev", task_id="b-1", bundle="Bad Slug!")
    assert out.lower().startswith("error")


def test_update_sets_and_clears_bundle(tm_epic_phase):
    backlog_server.backlog_add_task(title="A", epic="test-epic", phase="dev", task_id="b-1")
    assert "error" not in backlog_server.backlog_update_task("b-1", "bundle", "asset-ux").lower()
    # empty value clears (descope) and must be allowed
    assert "error" not in backlog_server.backlog_update_task("b-1", "bundle", "").lower()


def test_update_bad_slug_rejected(tm_epic_phase):
    backlog_server.backlog_add_task(title="A", epic="test-epic", phase="dev", task_id="b-1")
    assert backlog_server.backlog_update_task("b-1", "bundle", "Bad!").lower().startswith("error")
