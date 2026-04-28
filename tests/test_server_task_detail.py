"""HTTP API tests for Task Detail endpoints (Plan 3)."""
import json
import threading
import time
import urllib.request
import urllib.error
import pytest


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    """Start backlog_server on a free port; yields (base_url, server)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "tasks").mkdir()
    (tmp_path / ".taskmaster" / "lessons").mkdir()
    (tmp_path / ".taskmaster" / "handovers").mkdir()
    (tmp_path / ".taskmaster" / "issues").mkdir()

    (tmp_path / "backlog.yaml").write_text(
        "meta:\n  project: test\n"
        "epics:\n  - {id: viewer, name: Viewer, color: '#6ea8ff'}\n"
        "phases:\n  - {id: 'P-01', name: Foundations}\n"
        "tasks:\n"
        "  - id: T-148\n"
        "    title: Implement task detail\n"
        "    status: in-progress\n"
        "    priority: High\n"
        "    estimate: M\n"
        "    epic: viewer\n"
        "    phase: P-01\n"
        "    branch: feat/task-detail\n"
        "    depends_on: [T-100]\n"
        "    anchors: ['plugins/taskmaster/viewer/**/*.js']\n"
        "    created: '2026-04-20'\n"
        "    started: '2026-04-22'\n"
        "  - id: T-100\n"
        "    title: Foundation\n"
        "    status: done\n"
        "    priority: High\n"
        "    estimate: L\n"
        "    epic: viewer\n"
        "    phase: P-01\n"
        "    created: '2026-04-10'\n"
        "    completed: '2026-04-19'\n"
        "  - id: T-200\n"
        "    title: Kanban screen\n"
        "    status: backlog\n"
        "    priority: High\n"
        "    estimate: L\n"
        "    epic: viewer\n"
        "    phase: P-01\n"
        "    depends_on: [T-148]\n"
        "    created: '2026-04-25'\n"
    )

    (tmp_path / ".taskmaster" / "tasks" / "T-148.md").write_text(
        "---\n"
        "id: T-148\n"
        "docs:\n"
        "  spec: docs/spec.md\n"
        "  plan: docs/plan.md\n"
        "review_instructions: |\n"
        "  Click Toggle button. Verify variant flips.\n"
        "---\n"
        "## Description\n"
        "Build the Task Detail screen in both variants.\n"
        "\n"
        "## Notes\n"
        "Use the `marked` library from CDN.\n"
    )

    from backlog_server import _make_server
    server, port = _make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    yield base, server
    server.shutdown()
    server.server_close()


def test_get_task_returns_full_payload(running_server):
    base, _ = running_server
    resp = urllib.request.urlopen(f"{base}/api/task/T-148")
    assert resp.status == 200
    body = json.loads(resp.read())

    # Index-level fields surfaced from backlog.yaml
    assert body["id"] == "T-148"
    assert body["title"] == "Implement task detail"
    assert body["status"] == "in-progress"
    assert body["priority"] == "High"
    assert body["estimate"] == "M"
    assert body["epic"] == "viewer"
    assert body["phase"] == "P-01"
    assert body["branch"] == "feat/task-detail"
    assert body["depends_on"] == ["T-100"]
    assert body["anchors"] == ["plugins/taskmaster/viewer/**/*.js"]
    assert body["created"] == "2026-04-20"
    assert body["started"] == "2026-04-22"

    # Body-level fields from .taskmaster/tasks/T-148.md frontmatter
    assert body["docs"] == {"spec": "docs/spec.md", "plan": "docs/plan.md"}
    assert "Click Toggle button" in body["review_instructions"]

    # Markdown sections parsed from the body
    assert "## Description" in body["_body"]
    assert "## Notes" in body["_body"]
    assert "Build the Task Detail screen" in body["description"]
    assert "marked" in body["notes"]
