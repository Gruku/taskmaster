# plugins/taskmaster/tests/test_v3_task_validate.py
import pytest
import yaml
from pathlib import Path


@pytest.fixture
def populated_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "meta": {"project": "test"},
        "epics": [
            {"id": "e1", "name": "E1", "status": "active",
             "tasks": [
                 {"id": "e1-001", "title": "A", "status": "todo", "priority": "medium"},
                 {"id": "e1-002", "title": "B", "status": "todo", "priority": "medium",
                  "depends_on": ["e1-001"]},
             ]},
        ],
        "phases": [{"id": "p1", "name": "P1", "status": "active"}],
    }))
    return bp


def test_validate_passes_for_clean_patch(populated_backlog):
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"title": "Renamed"}, backlog_path=populated_backlog)
    assert errors == {}


def test_validate_rejects_unknown_epic(populated_backlog):
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"epic": "missing"}, backlog_path=populated_backlog)
    assert "epic" in errors


def test_validate_rejects_unknown_phase(populated_backlog):
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"phase": "p-missing"}, backlog_path=populated_backlog)
    assert "phase" in errors


def test_validate_rejects_unknown_dep(populated_backlog):
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"depends_on": ["e1-999"]}, backlog_path=populated_backlog)
    assert "depends_on" in errors


def test_validate_rejects_self_dep(populated_backlog):
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"depends_on": ["e1-001"]}, backlog_path=populated_backlog)
    assert "depends_on" in errors


def test_validate_rejects_dep_cycle(populated_backlog):
    """e1-002 depends on e1-001. Adding e1-002 to e1-001's deps creates a cycle."""
    from taskmaster_v3 import validate_task_write
    errors = validate_task_write("e1-001", {"depends_on": ["e1-002"]}, backlog_path=populated_backlog)
    assert "depends_on" in errors
    assert "cycle" in errors["depends_on"].lower()
