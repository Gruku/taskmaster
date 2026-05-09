"""HTTP + MCP wrapper tests for ideas."""
from pathlib import Path


def test_backlog_idea_create_writes_file_and_returns_id(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)

    out = backlog_server.backlog_idea_create(
        title="Per-task spike budgets",
        body="why and how",
        tags=["perf"],
        status="exploring",
    )
    assert "IDEA-001" in out
    assert (tmp_path / ".taskmaster" / "ideas" / "IDEA-001.md").exists()
    assert (tmp_path / ".taskmaster" / "ideas" / "IDEAS.md").exists()


def test_backlog_idea_create_no_backlog_returns_error(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"  # does not exist
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = backlog_server.backlog_idea_create(title="anything")
    assert out.startswith("Error:")


def test_backlog_idea_list_filters(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    backlog_server.backlog_idea_create(title="A", status="exploring")
    backlog_server.backlog_idea_create(title="B", status="parking-lot")
    out = backlog_server.backlog_idea_list(status="exploring")
    assert "IDEA-001" in out
    assert "IDEA-002" not in out


def test_backlog_idea_list_idea_id_returns_full(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    backlog_server.backlog_idea_create(title="solo", body="full body here")
    out = backlog_server.backlog_idea_list(idea_id="IDEA-001")
    assert "IDEA-001" in out
    assert "full body here" in out


def test_backlog_idea_update_archive(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    backlog_server.backlog_idea_create(title="archive me")
    out = backlog_server.backlog_idea_update(idea_id="IDEA-001", archived=True)
    assert "IDEA-001" in out
    idx = (tmp_path / ".taskmaster" / "ideas" / "IDEAS.md").read_text()
    assert "~~archive me~~" in idx


def test_backlog_idea_update_unknown_id_returns_error(tmp_path, monkeypatch):
    import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = backlog_server.backlog_idea_update(idea_id="IDEA-999", status="x")
    assert out.startswith("Idea not found")
