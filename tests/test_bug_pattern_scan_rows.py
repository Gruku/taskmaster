"""User intent: the bug pattern scanner must answer from the committed rows
like every other bug read. Globbing `bugs/*.md` described the export, not the
store: a bug whose file write failed its retry is invisible to the scanner, and
a stale file makes it cluster on text nobody wrote any more — so the recurrence
signal that promotes bugs to an Issue is computed from the wrong evidence.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from taskmaster import backlog_server as bs
from taskmaster import taskmaster_v3 as v3


_HTTP_TIMEOUT = 15


@pytest.fixture()
def two_matching_bugs(tm_epic_phase):
    assert "Error" not in bs.backlog_add_task(
        title="Work", epic="test-epic", phase="dev", tldr="w"
    )
    for title in ("Login form drops the session cookie",
                  "Login form drops the session token"):
        out = bs.backlog_bug_create(
            title=title, found_in="test-epic-001", components=["auth"]
        )
        assert "Error" not in out, out
    return tm_epic_phase


@pytest.fixture()
def no_markdown_reads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the pattern scanner parsed markdown instead of rows")

    monkeypatch.setattr(v3, "list_bug_ids", refuse)
    monkeypatch.setattr(v3, "read_bug", refuse)
    return monkeypatch


def test_pattern_scan_groups_from_rows(two_matching_bugs, no_markdown_reads):
    out = bs.backlog_bug_pattern_scan()
    assert "No bug patterns found" not in out, out
    assert "B-001" in out and "B-002" in out, out


# There is deliberately no "stale file is ignored" test: the store's scan-on-read
# treats a hand edit to `bugs/<id>.md` as a legitimate external edit and merges
# it into the row. The defect this file covers is the opposite direction — a row
# the export never reached, which the old directory glob could not see at all.


def test_pattern_scan_sees_a_bug_whose_file_is_missing(two_matching_bugs, no_markdown_reads):
    (two_matching_bugs / ".taskmaster" / "bugs" / "B-002.md").unlink()
    out = bs.backlog_bug_pattern_scan()
    assert "B-001" in out and "B-002" in out, out


def test_open_only_mode_filters_on_the_row_status(two_matching_bugs, no_markdown_reads):
    assert "Error" not in bs.backlog_bug_update("B-002", "fix_commit", "abc123")
    assert "Error" not in bs.backlog_bug_update("B-002", "status", "fixed")
    assert "No bug patterns found" in bs.backlog_bug_pattern_scan(mode="open_only")


@pytest.fixture()
def server(two_matching_bugs):
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
    yield base
    srv.shutdown()
    srv.server_close()


def test_viewer_pattern_scan_reads_rows(server, two_matching_bugs, no_markdown_reads):
    for path in (two_matching_bugs / ".taskmaster" / "bugs").glob("B-*.md"):
        path.unlink()
    req = urllib.request.Request(
        f"{server}/api/bugs/pattern-scan",
        data=json.dumps({"mode": "all"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    ids = {bid for group in body["groups"] for bid in group["bug_ids"]}
    assert ids == {"B-001", "B-002"}, body
