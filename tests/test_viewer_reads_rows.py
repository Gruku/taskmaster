"""User intent: the viewer must show what the store committed, not what the
markdown export happens to hold. Every GET answers from the committed rows of
one snapshot and carries that snapshot's ETag, so a reader and the edit that
follows it can never straddle two different states.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster import taskmaster_v3 as v3


_HTTP_TIMEOUT = 15


@pytest.fixture()
def seeded(tm_epic_phase):
    """A project holding one entity of every kind the viewer lists."""
    assert "Error" not in bs.backlog_add_task(
        title="Work", epic="test-epic", phase="dev", tldr="w"
    )
    assert "Error" not in bs.backlog_bug_create(
        title="A real bug", found_in="test-epic-001"
    )
    assert "Error" not in bs.backlog_issue_create(
        title="A real issue",
        severity="P1",
        components=["viewer"],
        impact="Users cannot save filters.",
        evidence="Reported three times in a week; every reload clears the filter.",
        tldr="filters do not persist",
    )
    assert "Error" not in bs.backlog_idea_create(title="A real idea")
    assert "Error" not in bs.backlog_note(action="create", text="A real note")
    created = bs.backlog_handover_create(
        tldr="what happened",
        next_action="carry on",
        task_ids=["test-epic-001"],
        session_kind="end-of-day",
        body="## Narrative\n\nthe long version\n",
    )
    assert "Error" not in created, created
    return tm_epic_phase


@pytest.fixture()
def server(seeded):
    """`seeded` served over HTTP; yields (base_url, root)."""
    srv, port = bs._make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    yield base, seeded
    srv.shutdown()
    srv.server_close()


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=_HTTP_TIMEOUT) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers


def _request(method, url, body=None, headers=None):
    head = dict(headers or {})
    data = None
    if body is not None:
        head["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=head)
    try:
        return urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return exc


@pytest.fixture()
def no_markdown_reads(monkeypatch):
    """Make any attempt to parse an entity file a loud failure."""
    def refuse(*args, **kwargs):
        raise AssertionError("a viewer read path parsed markdown instead of rows")

    for name in (
        "read_bug",
        "list_bug_ids",
        "read_handover",
        "list_handover_ids",
        "list_notes",
        "list_note_ids",
        "read_note",
    ):
        monkeypatch.setattr(v3, name, refuse)
    return monkeypatch


# ── every GET answers from rows ───────────────────────────────────────────────


def test_bugs_endpoint_reads_rows(server, no_markdown_reads):
    base, _root = server
    status, body, _headers = _get(base, "/api/bugs")
    assert status == 200
    assert [b["title"] for b in body] == ["A real bug"], body


def test_issues_endpoint_reads_rows(server, no_markdown_reads):
    base, root = server
    # The projection file is gone; only the committed row remains.
    for path in (root / ".taskmaster" / "issues").glob("ISS-*.md"):
        path.unlink()
    status, body, _headers = _get(base, "/api/issues")
    assert status == 200
    titles = [i["title"] for i in body["issues"]]
    assert titles == ["A real issue"], body


def test_ideas_endpoint_reads_rows(server, no_markdown_reads):
    base, root = server
    for path in (root / ".taskmaster" / "ideas").glob("IDEA-*.md"):
        path.unlink()
    status, body, _headers = _get(base, "/api/ideas")
    assert status == 200
    assert [i["title"] for i in body["ideas"]] == ["A real idea"], body


def test_notes_endpoint_reads_rows(server, no_markdown_reads):
    base, _root = server
    status, body, _headers = _get(base, "/api/notes")
    assert status == 200
    assert [n["body"] for n in body["notes"]] == ["A real note"], body


def test_sessions_endpoints_read_rows(server, no_markdown_reads):
    base, root = server
    for path in (root / ".taskmaster" / "handovers").glob("*.md"):
        path.unlink()
    status, body, _headers = _get(base, "/api/sessions")
    assert status == 200
    assert body, "no session lane was synthesised from the handover rows"
    sid = body[0]["id"]
    assert body[0]["tldr"] == "what happened"

    status, detail, _headers = _get(base, f"/api/sessions/{sid}")
    assert status == 200
    assert detail["session"]["id"] == sid
    assert detail["handovers"][0]["resume_prompt"].startswith("## Narrative")


def test_handover_detail_endpoint_reads_rows(server, no_markdown_reads):
    base, root = server
    with store.transaction(
        backlog_path=root / ".taskmaster" / "backlog.yaml", tool="test-read"
    ) as tx:
        hid = [h for h, _doc, _body in tx.list("handover")][0]
    for path in (root / ".taskmaster" / "handovers").glob("*.md"):
        path.unlink()
    status, body, _headers = _get(base, f"/api/handover/{hid}")
    assert status == 200
    assert body["tldr"] == "what happened"


# ── one snapshot, one ETag ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/backlog",
        "/api/bugs",
        "/api/issues",
        "/api/ideas",
        "/api/notes",
        "/api/sessions",
        "/api/epic/test-epic",
        "/api/task/test-epic-001",
    ],
)
def test_every_list_get_carries_the_snapshot_etag(server, path):
    base, _root = server
    _status, _body, headers = _get(base, path)
    etag = headers.get("ETag")
    assert etag, f"{path} served no ETag"
    assert etag.strip('"') == bs._viewer_etag(), path


def test_list_etags_move_together_after_a_write(server):
    base, _root = server
    before = {p: _get(base, p)[2].get("ETag") for p in ("/api/bugs", "/api/notes")}
    bs.backlog_note(action="create", text="second note")
    after = {p: _get(base, p)[2].get("ETag") for p in ("/api/bugs", "/api/notes")}
    assert after["/api/notes"] != before["/api/notes"]
    # One store, one revision: an unrelated list moves too, so a client can hold
    # a single revision for the whole screen.
    assert after["/api/bugs"] == after["/api/notes"]


# ── the projection-only (network filesystem) store ────────────────────────────


def _go_projection_only(root: Path, monkeypatch):
    store.reset_for_tests()
    for name in ("store.db", "store.db-wal", "store.db-shm"):
        (root / ".taskmaster" / "local" / name).unlink(missing_ok=True)
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )


def test_projection_only_etag_is_not_a_constant(seeded, monkeypatch):
    _go_projection_only(seeded, monkeypatch)

    first = bs._viewer_etag()
    assert first not in ("", ":0"), first

    note = seeded / ".taskmaster" / "notes" / "NOTE-002.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\nid: NOTE-002\ncreated: 2026-09-06T00:00:00Z\nauthor: user\n---\nlater\n",
        encoding="utf-8",
    )
    store.reset_for_tests()
    monkeypatch.setattr(
        store, "_network_filesystem_reason", lambda path: "network filesystem (smb)"
    )

    assert bs._viewer_etag() != first, (
        "the projection-only ETag did not move when the projection changed"
    )


# ── a refused legacy layout is a 409, not a 500 ───────────────────────────────


@pytest.fixture()
def legacy_layout_server(server, monkeypatch):
    """The same server, with every store open refusing the layout."""
    base, root = server

    def refuse(*args, **kwargs):
        raise store.LegacyLayoutError(
            "backlog at .claude/backlog.yaml is not at <root>/.taskmaster; run "
            "backlog_canonicalize_layout first to move it."
        )

    monkeypatch.setattr(store, "open_store", refuse)
    return base, root


def test_backlog_get_reports_legacy_layout_as_409(legacy_layout_server):
    base, _root = legacy_layout_server
    resp = _request("GET", f"{base}/api/backlog")
    assert resp.status == 409, resp.status
    body = json.loads(resp.read())
    assert "backlog_canonicalize_layout" in body["error"], body


@pytest.mark.parametrize("path", [
    "/api/bugs",
    "/api/issues",
    "/api/ideas",
    "/api/notes",
    "/api/sessions",
    "/api/sessions/2026-09-01-fixture",
    "/api/decisions/DEC-001",
    "/api/handover/2026-09-01-fixture",
    "/api/epic/test-epic",
])
def test_every_row_backed_get_reports_legacy_layout_as_409(legacy_layout_server, path):
    """`_snapshot()` caught only FileNotFoundError, so `store.LegacyLayoutError`
    escaped every GET 4.2 moved off `_serve_json`. The client got a dropped
    connection and a server-side traceback instead of the 409 carrying the one
    instruction that fixes the layout — precisely the case that message is for.
    """
    base, _root = legacy_layout_server
    resp = _request("GET", f"{base}{path}")
    assert resp.status == 409, (path, resp.status)
    body = json.loads(resp.read())
    assert "backlog_canonicalize_layout" in body["error"], body


def test_pattern_scan_reports_legacy_layout_as_409(legacy_layout_server):
    base, _root = legacy_layout_server
    resp = _request("POST", f"{base}/api/bugs/pattern-scan", {})
    assert resp.status == 409, resp.status
    body = json.loads(resp.read())
    assert "backlog_canonicalize_layout" in body["error"], body


def test_patch_reports_legacy_layout_as_409(legacy_layout_server):
    base, _root = legacy_layout_server
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Renamed"})
    assert resp.status == 409, resp.status
    body = json.loads(resp.read())
    assert "backlog_canonicalize_layout" in body["error"], body
