"""HTTP-layer tests for /api/notes endpoints."""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from taskmaster import backlog_server


# ── fixture (verbatim from test_api_handover_status.py) ───────────────────────


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


# A viewer request now waits behind a real store transaction, so a two-second
# ceiling was a machine-load flake rather than a real failure signal.
_HTTP_TIMEOUT = 15


def _post(base, path, payload):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=_HTTP_TIMEOUT) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# ── tests ─────────────────────────────────────────────────────────────────────


def test_post_creates_user_note(server_with_root):
    base, _server, bp = server_with_root
    status, out = _post(base, "/api/notes", {"text": "hello desk"})
    assert status == 201
    assert out["ok"] is True
    assert out["id"] == "NOTE-001"
    status, listing = _get(base, "/api/notes")
    assert status == 200
    assert len(listing["notes"]) == 1
    note = listing["notes"][0]
    assert note["author"] == "user"          # HTTP channel stamps user
    assert note["body"] == "hello desk"


def test_post_empty_text_400(server_with_root):
    base, _s, _bp = server_with_root
    try:
        _post(base, "/api/notes", {"text": "  "})
        assert False, "expected 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_update_and_archive_roundtrip(server_with_root):
    base, _s, _bp = server_with_root
    _post(base, "/api/notes", {"text": "v1"})
    status, out = _post(base, "/api/notes/NOTE-001/update", {"text": "v2", "pinned": True})
    assert status == 200 and out["ok"] is True
    _, listing = _get(base, "/api/notes")
    assert listing["notes"][0]["body"] == "v2"
    assert listing["notes"][0]["pinned"] is True
    status, out = _post(base, "/api/notes/NOTE-001/archive", {})
    assert status == 200
    _, listing = _get(base, "/api/notes")
    assert listing["notes"] == []
    _, listing = _get(base, "/api/notes?include_archived=1")
    assert len(listing["notes"]) == 1


def test_archive_missing_404(server_with_root):
    base, _s, _bp = server_with_root
    try:
        _post(base, "/api/notes/NOTE-999/archive", {})
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


# ── `seq` on a mutating viewer response (task 3.4) ────────────────────────


def _max_seq(bp: Path) -> int:
    """`MAX(changes.seq)` read straight from the file.

    Deliberately not through `taskmaster.store`: resetting or opening the
    store from the test thread would disturb the connection the running
    server thread is using.
    """
    import sqlite3

    connection = sqlite3.connect(bp.parent / "local" / "store.db")
    try:
        return int(
            connection.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
        )
    finally:
        connection.close()


def test_a_mutating_post_reports_the_sequence_it_committed(server_with_root):
    """The viewer's JSON counterpart of the `[seq N]` suffix on a tool result."""
    base, _s, bp = server_with_root
    status, out = _post(base, "/api/notes", {"text": "seq please"})
    assert status == 201 and out["ok"] is True
    assert out["seq"] == _max_seq(bp), out


def test_a_second_mutation_reports_a_later_sequence(server_with_root):
    base, _s, bp = server_with_root
    _, first = _post(base, "/api/notes", {"text": "one"})
    _, second = _post(base, "/api/notes/NOTE-001/update", {"text": "two"})
    assert second["seq"] > first["seq"], (first, second)
    assert second["seq"] == _max_seq(bp), second


def test_a_read_carries_no_sequence(server_with_root):
    """`seq` attests to a commit; a GET made none."""
    base, _s, _bp = server_with_root
    _post(base, "/api/notes", {"text": "one"})
    _status, listing = _get(base, "/api/notes")
    assert "seq" not in listing, listing


def test_a_rejected_mutation_carries_no_sequence(server_with_root):
    """A request that commits nothing must not inherit the previous one's."""
    base, _s, _bp = server_with_root
    _post(base, "/api/notes", {"text": "one"})
    try:
        _post(base, "/api/notes", {"text": "  "})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
    assert "seq" not in body, body
