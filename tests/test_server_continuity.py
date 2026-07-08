import importlib
import json
import pytest


@pytest.fixture
def in_backlog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.write_text(
        "meta:\n  schema_version: 3\nepics: []\n",
        encoding="utf-8",
    )
    import taskmaster.backlog_server as srv
    importlib.reload(srv)
    return srv, bp


def test_backlog_continuity_items_returns_json_with_items_array(in_backlog):
    srv, _ = in_backlog
    srv.backlog_decision_create(title="x", options=["a", "b"])
    out = srv.backlog_continuity_items()
    data = json.loads(out)
    assert "items" in data
    assert any(i["type"] == "decision" for i in data["items"])
    for it in data["items"]:
        assert {"id", "type", "title", "action_class", "timestamp"} <= set(it)


def test_backlog_continuity_items_filters_auto_stage_by_default(in_backlog):
    srv, _ = in_backlog
    srv.backlog_handover_create(
        tldr="auto stub", session_kind="auto-stage", body="", task_ids=[],
    )
    items = json.loads(srv.backlog_continuity_items())["items"]
    assert not any(i["type"] == "handover" and i["title"] == "auto stub" for i in items)
    items_all = json.loads(srv.backlog_continuity_items(include_auto_stage=True))["items"]
    assert any(i["title"] == "auto stub" for i in items_all)
