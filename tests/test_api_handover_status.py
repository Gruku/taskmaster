"""HTTP-layer tests for POST /api/handover/<id>/status.

Uses a local running_server fixture (real in-process server on an ephemeral
port) that also patches backlog_server.ROOT so that _backlog_path() resolves
to the tmp_path directory created by pytest — necessary because the POST handler
calls _backlog_path() which derives from ROOT, a module-level constant set at
import time.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from taskmaster import backlog_server

from taskmaster.taskmaster_v3 import write_handover, read_handover


# ── local fixture ─────────────────────────────────────────────────────────────


@pytest.fixture
def server_with_root(tmp_path, monkeypatch):
    """Start backlog_server on a free port with ROOT patched to tmp_path.

    Yields (base_url, server, backlog_path).
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".taskmaster").mkdir()
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.write_text("meta:\n  project: test\nepics: []\nphases: []\n")
    (tmp_path / ".taskmaster" / "handovers").mkdir()

    # Patch ROOT so _backlog_path() and handover_path() resolve to tmp_path.
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)

    from taskmaster.backlog_server import _make_server, _init_storage
    server, port = _make_server(host="127.0.0.1", port=0)
    _init_storage()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)

    yield base, server, bp

    server.shutdown()
    server.server_close()


# ── helpers ───────────────────────────────────────────────────────────────────


def _post_status(base: str, handover_id: str, payload: dict):
    """POST /api/handover/<id>/status, returning (status_code, body_dict)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/handover/{handover_id}/status",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body_dict = json.loads(raw) if raw.strip() else {"ok": False, "error": "(empty body)"}
        return exc.code, body_dict


# ── tests ─────────────────────────────────────────────────────────────────────


def test_post_handover_status_updates_frontmatter(server_with_root):
    base, _, bp = server_with_root

    hid, _ = write_handover(bp, tldr="needs override", session_kind="end-of-day")

    code, body = _post_status(base, hid, {"status": "closed", "reason": "manual smoke"})

    assert code == 200, f"expected 200 got {code}: {body}"
    assert body.get("ok") is True
    assert body.get("status") == "closed"
    assert body.get("id") == hid

    fm, _ = read_handover(bp, hid)
    assert fm["status"] == "closed"
    assert fm["status_user_set"] is True
    assert fm["status_reason"] == "manual smoke"


def test_post_handover_status_invalid_enum(server_with_root):
    base, _, bp = server_with_root

    hid, _ = write_handover(bp, tldr="test invalid enum", session_kind="end-of-day")

    code, body = _post_status(base, hid, {"status": "garbage"})

    assert code == 400, f"expected 400 got {code}: {body}"
    assert body.get("ok") is False


def test_post_handover_status_unknown_id(server_with_root):
    base, _, _ = server_with_root

    code, body = _post_status(base, "2099-01-01-nope", {"status": "closed"})

    assert code == 404, f"expected 404 got {code}: {body}"
    assert body.get("ok") is False
