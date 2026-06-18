"""Task bundle: member lookup + birth-time sub_repo validation."""
import backlog_server
from backlog_server import _find_tasks_by_bundle, _load as _load_backlog


def _add(task_id, bundle="", sub_repo=""):
    backlog_server.backlog_add_task(
        title=task_id, epic="test-epic", phase="dev",
        task_id=task_id, bundle=bundle, sub_repo=sub_repo)


def test_find_returns_only_members(tm_epic_phase):
    _add("b-1", bundle="ux"); _add("b-2", bundle="ux"); _add("b-3", bundle="other")
    data = _load_backlog()
    ids = {t["id"] for t in _find_tasks_by_bundle(data, "ux")}
    assert ids == {"b-1", "b-2"}


def test_birth_sub_repo_mismatch_rejected_on_add(tm_epic_phase):
    _add("b-1", bundle="ux", sub_repo="api")
    out = backlog_server.backlog_add_task(
        title="b-2", epic="test-epic", phase="dev",
        task_id="b-2", bundle="ux", sub_repo="web")
    assert "sub_repo" in out.lower() and out.lower().startswith("error")


def test_birth_sub_repo_match_allowed(tm_epic_phase):
    _add("b-1", bundle="ux", sub_repo="api")
    out = backlog_server.backlog_add_task(
        title="b-2", epic="test-epic", phase="dev",
        task_id="b-2", bundle="ux", sub_repo="api")
    assert not out.lower().startswith("error")


def test_update_into_bundle_sub_repo_mismatch_rejected(tm_epic_phase):
    _add("b-1", bundle="ux", sub_repo="api")
    _add("b-2", sub_repo="web")
    out = backlog_server.backlog_update_task("b-2", "bundle", "ux")
    assert "sub_repo" in out.lower() and out.lower().startswith("error")


def test_update_into_bundle_sub_repo_match_allowed(tm_epic_phase):
    _add("b-1", bundle="ux", sub_repo="api")
    _add("b-2", sub_repo="api")
    out = backlog_server.backlog_update_task("b-2", "bundle", "ux")
    assert not out.lower().startswith("error")
