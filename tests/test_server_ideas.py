"""HTTP + MCP wrapper tests for ideas."""
from pathlib import Path

from tests.test_server_api import running_server  # noqa: F401


def test_backlog_idea_create_writes_file_and_returns_id(tmp_path, monkeypatch):
    from taskmaster import backlog_server
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
    from taskmaster import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"  # does not exist
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = backlog_server.backlog_idea_create(title="anything")
    assert out.startswith("Error:")


def test_backlog_idea_list_filters(tmp_path, monkeypatch):
    from taskmaster import backlog_server
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
    from taskmaster import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    backlog_server.backlog_idea_create(title="solo", body="full body here")
    out = backlog_server.backlog_idea_list(idea_id="IDEA-001")
    assert "IDEA-001" in out
    assert "full body here" in out


def test_backlog_idea_update_archive(tmp_path, monkeypatch):
    from taskmaster import backlog_server
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
    from taskmaster import backlog_server
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\nphases: []\n")
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = backlog_server.backlog_idea_update(idea_id="IDEA-999", status="x")
    assert out.startswith("Idea not found")


# ── HTTP tests for GET /api/ideas ────────────────────────────────────────────

def _write_idea_file(root: Path, idea_id: str, title: str, body: str = "", **overrides):
    """Write an idea .md file directly into tmp_path (mirrors _write_issue)."""
    import yaml
    from datetime import datetime, timezone
    base = {
        "id": idea_id,
        "title": title,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": "Claude",
        "status": "",
        "tags": [],
        "related_tasks": [],
        "related_issues": [],
        "related_lessons": [],
        "promoted_to": None,
        "archived": False,
    }
    base.update(overrides)
    ideas_dir = root / ".taskmaster" / "ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    p = ideas_dir / f"{idea_id}.md"
    fm = "---\n" + yaml.safe_dump(base, sort_keys=False).rstrip() + "\n---\n" + (body + "\n" if body else "")
    p.write_text(fm)


def test_get_ideas_returns_list(running_server, tmp_path):
    """Verify /api/ideas serves the JSON the viewer expects."""
    import json
    import time
    import urllib.request
    base, _ = running_server
    _write_idea_file(tmp_path, "IDEA-001", "A", status="exploring", tags=["perf"])
    time.sleep(0.01)  # ensure distinct created timestamps for ordering
    _write_idea_file(tmp_path, "IDEA-002", "B")
    resp = urllib.request.urlopen(f"{base}/api/ideas")
    payload = json.loads(resp.read())
    assert "ideas" in payload
    titles = [i["title"] for i in payload["ideas"]]
    assert "A" in titles
    assert "B" in titles
    # Newest first (by id number since timestamps may be equal in fast runs)
    ids = [i["id"] for i in payload["ideas"]]
    assert ids.index("IDEA-002") < ids.index("IDEA-001")


def test_get_ideas_filter_by_status(running_server, tmp_path):
    import json
    import urllib.request
    base, _ = running_server
    _write_idea_file(tmp_path, "IDEA-010", "A", status="exploring")
    _write_idea_file(tmp_path, "IDEA-011", "B", status="parking-lot")
    resp = urllib.request.urlopen(f"{base}/api/ideas?status=exploring")
    payload = json.loads(resp.read())
    titles = [i["title"] for i in payload["ideas"]]
    assert "A" in titles
    assert "B" not in titles


def test_get_ideas_excludes_archived_by_default(running_server, tmp_path):
    import json
    import urllib.request
    base, _ = running_server
    _write_idea_file(tmp_path, "IDEA-020", "active")
    _write_idea_file(tmp_path, "IDEA-021", "archived-one", archived=True)
    resp = urllib.request.urlopen(f"{base}/api/ideas")
    titles = [i["title"] for i in json.loads(resp.read())["ideas"]]
    assert "active" in titles
    assert "archived-one" not in titles
    # Explicit opt-in returns archived
    resp2 = urllib.request.urlopen(f"{base}/api/ideas?archived=true")
    titles2 = [i["title"] for i in json.loads(resp2.read())["ideas"]]
    assert "archived-one" in titles2


def test_get_ideas_summary_false_includes_body(running_server, tmp_path):
    """The viewer fetches with summary=false so the detail pane has body."""
    import json
    import urllib.request
    base, _ = running_server
    _write_idea_file(tmp_path, "IDEA-030", "with-body", body="this is the body")
    # Default GET (summary defaults false on HTTP) should include body
    resp = urllib.request.urlopen(f"{base}/api/ideas?summary=false")
    payload = json.loads(resp.read())
    record = next(i for i in payload["ideas"] if i["id"] == "IDEA-030")
    assert record["body"].strip() == "this is the body"
    # summary=true keeps body out
    resp2 = urllib.request.urlopen(f"{base}/api/ideas?summary=true")
    payload2 = json.loads(resp2.read())
    record2 = next(i for i in payload2["ideas"] if i["id"] == "IDEA-030")
    assert "body" not in record2


def test_post_ideas_creates_idea(running_server, tmp_path):
    """POST /api/ideas creates an idea and the GET endpoint returns it."""
    import json
    import urllib.request
    base, _ = running_server
    payload = json.dumps({
        "title": "Created from viewer",
        "body": "body content",
        "status": "exploring",
        "tags": ["perf"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/ideas",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 201
    out = json.loads(resp.read())
    assert out["ok"] is True
    assert out["id"].startswith("IDEA-")
    # Confirm round-trip via GET
    resp2 = urllib.request.urlopen(f"{base}/api/ideas")
    titles = [i["title"] for i in json.loads(resp2.read())["ideas"]]
    assert "Created from viewer" in titles


def test_post_ideas_rejects_missing_title(running_server, tmp_path):
    """POST without title returns 400."""
    import json
    import urllib.request
    base, _ = running_server
    req = urllib.request.Request(
        f"{base}/api/ideas",
        data=json.dumps({"body": "no title"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        assert False, "expected HTTPError 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        body = json.loads(e.read())
        assert body["ok"] is False
        assert "title" in body["error"]
