# User intent: no write path may close a depends_on cycle (the gates would wait on each other forever); the tools
# checked before sending, but commands=, batch lines and the viewer reached the store unchecked (security review m-8).
from __future__ import annotations

import pytest

from taskmaster import backlog_server as bs
from native_twins import commit_only, make_twins, native_connection


def _seed():
    for title in ("A", "B", "C"):
        bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
    bs.backlog_update_task(task_id="test-epic-001", field="depends_on", value="test-epic-002")
    bs.backlog_update_task(task_id="test-epic-002", field="depends_on", value="test-epic-003")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


@pytest.mark.parametrize("operation, arguments", [
    ("task.update", {"id": "test-epic-003", "field": "depends_on", "value": "test-epic-001"}),
    ("task.batch_line", {"id": "test-epic-003", "op": "update", "field": "depends_on", "value": "test-epic-001"}),
    ("batch", {"commands": [{"operation": "task.update", "arguments": {
        "id": "test-epic-003", "field": "depends_on", "value": "test-epic-001"}}]}),
    ("task.viewer_update", {"id": "test-epic-003", "patch": {"depends_on": ["test-epic-001"]}}),
    ("task.update", {"id": "test-epic-003", "field": "depends_on", "value": "test-epic-003"}),
])
def test_no_write_path_closes_a_cycle(twins, operation, arguments):
    with native_connection(twins.native) as connection:
        high = connection.execute("SELECT max(seq) FROM domain_events").fetchone()[0]
        with pytest.raises(ValueError, match="cycle|cannot depend on itself"):
            commit_only(connection, operation, arguments)
        assert connection.execute("SELECT max(seq) FROM domain_events").fetchone()[0] == high


def test_an_acyclic_dependency_is_still_accepted(twins):
    with native_connection(twins.native) as connection:
        commit_only(connection, "task.update", {"id": "test-epic-001", "field": "depends_on",
                                                "value": "test-epic-002,test-epic-003"})
