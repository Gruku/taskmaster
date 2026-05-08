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

    # Canonical v3 layout: backlog.yaml lives inside .taskmaster/ next to its
    # artifact subdirs (tasks/, lessons/, issues/, handovers/).  The old layout
    # placed backlog.yaml at the project root, which caused a path mismatch when
    # the server resolved sidecar files relative to backlog_path.parent.
    (tmp_path / ".taskmaster" / "backlog.yaml").write_text(
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

    import backlog_server
    # Redirect the server's path resolution to the test tree so every call to
    # _resolve_paths() (which uses the module-level ROOT, CONFIG_PATH, and
    # LEGACY_CONFIG_PATH) reads fixture data instead of the real project.
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH",
                        tmp_path / ".taskmaster" / "missing.json")
    monkeypatch.setattr(backlog_server, "LEGACY_CONFIG_PATH",
                        tmp_path / ".claude" / "missing.json")
    server, port = backlog_server._make_server(host="127.0.0.1", port=0)
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


def test_get_task_404_for_unknown_id(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/task/T-9999")
    assert exc.value.code == 404
    body = json.loads(exc.value.read())
    assert body["ok"] is False
    assert "T-9999" in body["error"]


def test_get_task_404_for_empty_id(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/task/")
    assert exc.value.code == 404


def test_get_task_related_returns_lessons_handovers_issues_and_deps(running_server, tmp_path):
    base, _ = running_server

    (tmp_path / ".taskmaster" / "lessons" / "LSN-01.md").write_text(
        "---\n"
        "id: LSN-01\n"
        "kind: pattern\n"
        "anchors: ['plugins/taskmaster/viewer/**/*.js']\n"
        "title: Use ES modules without bundler\n"
        "---\n"
        "Vanilla ES modules load without a build step.\n"
    )
    (tmp_path / ".taskmaster" / "lessons" / "LSN-02.md").write_text(
        "---\n"
        "id: LSN-02\n"
        "kind: gotcha\n"
        "anchors: ['scripts/**/*.sh']\n"
        "title: Unrelated\n"
        "---\nNot in scope.\n"
    )
    (tmp_path / ".taskmaster" / "handovers" / "2026-04-25-detail.md").write_text(
        "---\n"
        "id: HOV-0001a\n"
        "task_ids: [T-148]\n"
        "kind: mid-task\n"
        "session: SES-0010\n"
        "created: '2026-04-25T16:48:00Z'\n"
        "---\n"
        "Paused at variant B graph layout.\n"
    )
    (tmp_path / ".taskmaster" / "issues" / "ISS-01.md").write_text(
        "---\n"
        "id: ISS-01\n"
        "severity: Medium\n"
        "status: open\n"
        "task_ids: [T-148]\n"
        "title: Bezier control points off on row offset\n"
        "---\nSymptom: edges look kinked.\n"
    )

    resp = urllib.request.urlopen(f"{base}/api/task/T-148/related")
    assert resp.status == 200
    body = json.loads(resp.read())

    lesson_ids = [l["id"] for l in body["lessons"]]
    assert "LSN-01" in lesson_ids
    assert "LSN-02" not in lesson_ids

    handover_ids = [h["id"] for h in body["handovers"]]
    assert "HOV-0001a" in handover_ids

    issue_ids = [i["id"] for i in body["issues"]]
    assert "ISS-01" in issue_ids

    assert any(t["id"] == "T-100" for t in body["dependencies"])
    assert any(t["id"] == "T-200" for t in body["unblocks"])


def test_get_task_surfaces_forward_compat_fields(running_server, tmp_path):
    base, _ = running_server
    (tmp_path / ".taskmaster" / "tasks" / "T-148.md").write_text(
        "---\n"
        "id: T-148\n"
        "worktree: ../wt-task-detail\n"
        "spec_review: {verdict: pass, codex_note: 'Looks clean.'}\n"
        "patchnote: 'Adds Variant A and B detail views.'\n"
        "release: v2.1.0\n"
        "locked_by: 'session SES-0010'\n"
        "docs:\n"
        "  spec: docs/spec.md\n"
        "---\n"
        "## Description\nbody.\n"
    )
    body = json.loads(urllib.request.urlopen(f"{base}/api/task/T-148").read())
    assert body["worktree"] == "../wt-task-detail"
    assert body["spec_review"]["verdict"] == "pass"
    assert "Looks clean" in body["spec_review"]["codex_note"]
    assert body["patchnote"].startswith("Adds Variant A")
    assert body["release"] == "v2.1.0"
    assert body["locked_by"] == "session SES-0010"
