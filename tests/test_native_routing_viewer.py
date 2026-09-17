"""User intent: the viewer's HTTP routes on a native store (N08) must answer with the
same status codes and JSON bodies as on a legacy store — board, task/epic detail,
related data, continuity lists, the edit-in-UI writes with their If-Match
preconditions — and commit and project the same state.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins, normalize


def _seed():
    bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
    bs.backlog_add_epic(epic_id="other", name="Other", done_when="x")
    bs.backlog_area_create(area_id="viewer", name="Viewer")
    bs.backlog_add_task(title="Board task", epic="test-epic", phase="dev", notes="## Description\nfrom body")
    bs.backlog_add_task(title="Gated task", epic="test-epic", phase="dev", priority="high",
                        depends_on="test-epic-001")
    bs.backlog_update_task(task_id="test-epic-002", field="status", value="in-progress")
    bs.backlog_update_task(task_id="test-epic-001", field="blockers", value="waiting")
    bs.backlog_bug_create(title="A bug", found_in="test-epic-001", body="Bug body")
    bs.backlog_issue_create(title="An issue", severity="P1", evidence="e", body="Issue body")
    bs.backlog_idea_create(title="An idea", body="Idea body", tags=["t"])
    bs.backlog_note(action="create", text="A note")
    bs.backlog_decision_create(title="A decision", options=["a", "b"])
    bs.backlog_handover_create(tldr="Handover for board task", task_ids=["test-epic-001"], thread="line")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _serve(twins, root, method, path, payload=None, headers=None):
    with twins.at(root):
        server, port = bs._make_server(host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                             headers={"Content-Type": "application/json", **(headers or {})})
            try:
                response = urllib.request.urlopen(request, timeout=30)
            except urllib.error.HTTPError as exc:
                response = exc
            raw = response.read().decode("utf-8")
            etag = response.headers.get("ETag")
            return response.status, raw, etag
        finally:
            server.shutdown()
            server.server_close()


_ROOTED = re.compile(r"[A-Za-z]:\\\\[^\"]*?\\\\(legacy|native)(?=\\\\)")


def _body(raw, root):
    text = raw.replace(str(root).replace("\\", "\\\\"), "<root>").replace(str(root), "<root>")
    try:
        return normalize(json.loads(text))
    except ValueError:
        return normalize(text)


def same(twins, method, path, payload=None, headers=None):
    legacy = _serve(twins, twins.legacy, method, path, payload, headers)
    native = _serve(twins, twins.native, method, path, payload, headers)
    assert native[0] == legacy[0], (method, path, legacy, native)
    assert _body(native[1], twins.native) == _body(legacy[1], twins.legacy), (method, path, legacy[1], native[1])
    assert (native[2] is None) == (legacy[2] is None), (method, path)
    return legacy, native


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


def test_board_and_detail_reads_match(twins):
    for path in ("/api/backlog", "/api/task/test-epic-001", "/api/task/test-epic-002", "/api/task/ghost-1",
                 "/api/task/test-epic-001/related", "/api/task/ghost-1/related", "/api/epic/test-epic",
                 "/api/epic/ghost", "/api/threads", "/api/sessions", "/api/sessions/line", "/api/sessions/ghost",
                 "/api/bugs", "/api/bugs?include_archive=true&status=open", "/api/issues",
                 "/api/issues?include_resolved=false", "/api/ideas", "/api/ideas?summary=true&limit=1",
                 "/api/continuity", "/api/decisions/DEC-001", "/api/decisions/DEC-404",
                 "/api/handover/2026-09-17-handover-for-board-task", "/api/handover/nope", "/api/notes",
                 "/api/notes?include_archived=1", "/api/viewer/prefs", "/api/identity", "/api/session",
                 "/api/dashboard/recent-events?since=2020-01-01T00:00:00Z", "/api/dashboard/recent-events",
                 "/api/dashboard/recent-events?since=bogus", "/api/nothing"):
        same(twins, "GET", path)


def test_continuity_writes_match(twins):
    for method, path, payload in (
            ("POST", "/api/ideas", {"title": "HTTP idea", "body": "mentions ISS-001", "tags": ["x"]}),
            ("POST", "/api/ideas", {"title": " "}), ("POST", "/api/notes", {"text": "HTTP note", "pinned": True}),
            ("POST", "/api/notes", {"text": ""}), ("POST", "/api/notes/NOTE-001/update", {"text": "edited", "pinned": False}),
            ("POST", "/api/notes/NOTE-404/update", {"text": "x"}), ("POST", "/api/notes/NOTE-002/archive", {}),
            ("POST", "/api/handover/2026-09-17-handover-for-board-task/status", {"status": "closed", "reason": "r"}),
            ("POST", "/api/handover/2026-09-17-handover-for-board-task/status", {"status": "sleepy"}),
            ("POST", "/api/handover/nope/status", {"status": "closed"}),
            ("POST", "/api/decisions/DEC-001/resolve", {"resolved_with": 2, "rationale": "because"}),
            ("POST", "/api/decisions/DEC-001/resolve", {}), ("POST", "/api/decisions/DEC-404/drop", {"reason": "x"}),
            ("POST", "/api/bugs", {"title": "HTTP bug", "components": ["ui"], "body": "b"}),
            ("POST", "/api/bugs", {"title": ""}), ("POST", "/api/bugs/B-002", {"status": "shelved", "title": "Renamed"}),
            ("POST", "/api/bugs/B-002", {"status": "weird"}), ("POST", "/api/bugs/B-404", {"title": "x"}),
            ("POST", "/api/bugs/pattern-scan", {"mode": "all"}),
            ("POST", "/api/bugs/promote", {"bug_ids": ["B-001", "B-002"], "title": "Promoted", "severity": "P2",
                                           "evidence_text": "recurs"}),
            ("POST", "/api/bugs/promote", {"bug_ids": []}), ("POST", "/api/bugs/B-001/archive", {}),
            ("POST", "/api/nowhere", {})):
        same(twins, method, path, payload)
    _check(twins)


def test_task_edits_match_including_preconditions(twins):
    legacy_etag = _serve(twins, twins.legacy, "GET", "/api/task/test-epic-001")[2]
    native_etag = _serve(twins, twins.native, "GET", "/api/task/test-epic-001")[2]
    for method, path, payload in (
            ("POST", "/api/tasks/validate", {"task_id": "test-epic-001", "patch": {"status": "done", "area": "nope"}}),
            ("POST", "/api/tasks/validate", {"task_id": "test-epic-002", "patch": {"status": "done"}}),
            ("POST", "/api/tasks/validate", {"patch": {"epic": "ghost", "depends_on": ["ghost-9"]}}),
            ("POST", "/api/tasks", {"epic": "test-epic", "title": "Viewer task", "phase": "dev", "custom": "kept"}),
            ("POST", "/api/tasks", {"epic": "ghost", "title": "x"}), ("POST", "/api/tasks", {"title": "no epic"}),
            ("PATCH", "/api/tasks/test-epic-001", {"title": "Patched", "area": "viewer", "status": "in-progress"}),
            ("PATCH", "/api/tasks/test-epic-001", {"epic": "other"}),
            ("PATCH", "/api/tasks/test-epic-002", {"status": "done"}),
            ("PATCH", "/api/tasks/test-epic-002", {"status": None}),
            ("PATCH", "/api/tasks/test-epic-002", {"depends_on": ["test-epic-002"]}),
            ("PATCH", "/api/tasks/ghost-1", {"title": "x"}),
            ("POST", "/api/tasks/test-epic-003/archive", {})):
        same(twins, method, path, payload)
    for etag, root in ((legacy_etag, twins.legacy), (native_etag, twins.native)):
        status, raw, _ = _serve(twins, root, "PATCH", "/api/tasks/other-001", {"title": "stale"},
                                headers={"If-Match": etag})
        assert status == 409 and json.loads(raw)["error"] == "stale", (root, status, raw)
    _check(twins)
    full = json.loads(_serve(twins, twins.legacy, "GET", "/api/task/test-epic-002")[1])
    full["title"] = "Put title"
    same(twins, "PUT", "/api/tasks/test-epic-002", full)
    same(twins, "PUT", "/api/viewer/prefs", {"theme": "light"})
    same(twins, "OPTIONS", "/api/backlog")
    _check(twins)
