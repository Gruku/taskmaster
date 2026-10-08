"""MCP/HTTP surface for backlog_bug_* tools."""
import json
import urllib.request
from pathlib import Path

import pytest

from tests.test_server_api import running_server  # noqa: F401


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def test_bug_create_returns_id_and_writes_file(running_server, tmp_path):
    base, _ = running_server
    out = _post(f"{base}/api/bugs", {
        "title": "test bug",
        "found_in": "T-001",
        "discovered_by": "user",
    })
    assert out["id"].startswith("B-")
    p = tmp_path / ".taskmaster" / "bugs" / f"{out['id']}.md"
    assert p.exists()


def test_bug_list_excludes_archive_by_default(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    b = _post(f"{base}/api/bugs", {"title": "beta archived", "discovered_by": "user"})
    _post(f"{base}/api/bugs/{b['id']}", {"status": "fixed", "fix_commit": "abc"})
    _post(f"{base}/api/bugs/{b['id']}/archive", {})
    listing = _get(f"{base}/api/bugs")
    ids = [e["id"] for e in listing]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_bug_pattern_scan_returns_groups(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "Path mismatch in v3 reader", "components": ["taskmaster"], "discovered_by": "user"})
    _post(f"{base}/api/bugs", {"title": "Path mismatch in v3 reader handover", "components": ["taskmaster"], "discovered_by": "user"})
    out = _post(f"{base}/api/bugs/pattern-scan", {})
    assert len(out["groups"]) == 1


def test_bug_promote_creates_issue(running_server, tmp_path):
    base, _ = running_server
    b1 = _post(f"{base}/api/bugs", {"title": "p mismatch v3 reader", "components": ["taskmaster"], "discovered_by": "user"})
    b2 = _post(f"{base}/api/bugs", {"title": "p mismatch v3 reader handover", "components": ["taskmaster"], "discovered_by": "user"})
    out = _post(f"{base}/api/bugs/promote", {
        "bug_ids": [b1["id"], b2["id"]],
        "title": "Path mismatch is systemic",
        "severity": "P1",
        "evidence_text": "Recurring: 2 bugs same component same symptom.",
    })
    assert out["issue_id"].startswith("ISS-")


import urllib.error


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_bug_get_single_returns_one_object(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    _post(f"{base}/api/bugs", {"title": "beta", "discovered_by": "user"})
    one = _get(f"{base}/api/bugs/{a['id']}")
    assert isinstance(one, dict)
    assert one["id"] == a["id"]
    assert one["title"] == "alpha"
    assert "summary" in one


def test_bug_get_single_unknown_is_404(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert _status(f"{base}/api/bugs/B-999") == 404


def test_bug_get_single_finds_archived(running_server, tmp_path):
    base, _ = running_server
    b = _post(f"{base}/api/bugs", {"title": "old", "discovered_by": "user"})
    _post(f"{base}/api/bugs/{b['id']}", {"status": "fixed", "fix_commit": "abc"})
    _post(f"{base}/api/bugs/{b['id']}/archive", {})
    assert _get(f"{base}/api/bugs/{b['id']}")["id"] == b["id"]


@pytest.mark.parametrize("tail", ["B-1%2F..%2Fx", "..", "B-1/extra"])
def test_bug_get_single_rejects_odd_ids(running_server, tmp_path, tail):
    base, _ = running_server
    assert _status(f"{base}/api/bugs/{tail}") == 404


def test_bug_list_still_a_list(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert isinstance(_get(f"{base}/api/bugs"), list)
    assert isinstance(_get(f"{base}/api/bugs?status=open"), list)


def _raw(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_bug_get_single_carries_body_and_location(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {
        "title": "edge", "discovered_by": "user", "found_in": "T-102",
        "location": ["viewer/css/screens/kanban.css:87"], "body": "The border is **too faint**.",
    })
    one = _get(f"{base}/api/bugs/{a['id']}")
    assert one["summary"] == "The border is **too faint**."
    assert one["location"] == ["viewer/css/screens/kanban.css:87"]
    assert one["found_in"] == "T-102"
    assert "_body" not in one


def test_bug_get_single_ignores_query(running_server, tmp_path):
    base, _ = running_server
    a = _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    assert _get(f"{base}/api/bugs/{a['id']}?include_archive=1")["id"] == a["id"]


def test_bug_get_empty_id_is_404_not_the_list(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    status, body = _raw(f"{base}/api/bugs/")
    assert status == 404
    assert not body.lstrip().startswith("[")


def test_bug_list_route_matches_only_its_own_path(running_server, tmp_path):
    base, _ = running_server
    _post(f"{base}/api/bugs", {"title": "alpha", "discovered_by": "user"})
    status, body = _raw(f"{base}/api/bugsx")
    assert status == 404
    assert isinstance(_get(f"{base}/api/bugs?found_in=T-102&include_archive=true"), list)


def test_bug_get_404_body_is_a_reason(running_server, tmp_path):
    base, _ = running_server
    status, body = _raw(f"{base}/api/bugs/B-999")
    assert status == 404
    assert json.loads(body) == {"ok": False, "error": "unknown bug B-999"}
