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
