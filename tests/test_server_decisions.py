import importlib
from pathlib import Path
import pytest


@pytest.fixture
def in_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.write_text(
        "meta:\n  schema_version: 3\nepics: []\nhandovers: []\nissues: []\n",
        encoding="utf-8",
    )
    import taskmaster.backlog_server as srv
    importlib.reload(srv)
    return srv, bp


def test_backlog_decision_create_writes_file_and_returns_id(in_backlog):
    srv, bp = in_backlog
    out = srv.backlog_decision_create(
        title="Land 086",
        options=["push MR", "merge into develop", "hold"],
        recommendation=2,
        task_id="t-001",
    )
    assert "DEC-001" in out
    assert (bp.parent / "decisions" / "DEC-001.md").exists()


def test_backlog_decision_list_returns_open_only_by_default(in_backlog):
    srv, _ = in_backlog
    srv.backlog_decision_create(title="a", options=["x", "y"])
    did2 = srv.backlog_decision_create(title="b", options=["x", "y"]).split()[2]
    srv.backlog_decision_resolve(did2, resolved_with=1)
    out = srv.backlog_decision_list()
    assert "DEC-001" in out
    assert "DEC-002" not in out

    out_all = srv.backlog_decision_list(status="all")
    assert "DEC-001" in out_all and "DEC-002" in out_all


def test_backlog_decision_resolve_and_drop_round_trip(in_backlog):
    srv, _ = in_backlog
    srv.backlog_decision_create(title="r", options=["x", "y"])
    srv.backlog_decision_resolve("DEC-001", resolved_with=2, rationale="best")
    got = srv.backlog_decision_get("DEC-001")
    assert "resolved" in got and "resolved_with: 2" in got


def test_backlog_decision_create_returns_error_when_no_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import taskmaster.backlog_server as srv
    importlib.reload(srv)
    out = srv.backlog_decision_create(title="x", options=["a", "b"])
    assert "no backlog" in out.lower()
