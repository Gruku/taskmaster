"""Pattern-match signatures and cross-bug grouping."""
import pytest


def test_bug_signature_normalizes_title_and_components():
    from taskmaster_v3 import _bug_signature
    fm = {
        "title": "Path mismatch in v3 reader 47",
        "components": ["viewer", "taskmaster"],
    }
    sig = _bug_signature(fm)
    # components sorted, lowercased; title tokens dropped <3 chars and digits
    assert sig == (("taskmaster", "viewer"), ("mismatch", "path", "reader"))


def test_bug_signature_requires_min_three_tokens():
    from taskmaster_v3 import _bug_signature
    fm = {"title": "the bug", "components": ["x"]}
    assert _bug_signature(fm) is None  # not enough signal


def test_scan_bug_patterns_groups_matches(tmp_path):
    from taskmaster_v3 import write_bug, scan_bug_patterns
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    b1, _ = write_bug(bp, title="Path mismatch in v3 reader", components=["taskmaster"], discovered_by="user")
    b2, _ = write_bug(bp, title="Path mismatch in v3 reader (handover)", components=["taskmaster"], discovered_by="user")
    b3, _ = write_bug(bp, title="Unrelated bug about something else entirely", components=["viewer"], discovered_by="user")
    groups = scan_bug_patterns(bp)
    assert len(groups) == 1
    assert sorted(groups[0]["bug_ids"]) == sorted([b1, b2])
    assert b3 not in groups[0]["bug_ids"]


def test_scan_bug_patterns_threshold_at_least_two(tmp_path):
    from taskmaster_v3 import write_bug, scan_bug_patterns
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    write_bug(bp, title="Unique single occurrence here", components=["x"], discovered_by="user")
    assert scan_bug_patterns(bp) == []


# ---------------------------------------------------------------------------
# B-024: mode="open_only" silently behaves as "all"
# ---------------------------------------------------------------------------


def _make_backlog(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text("schema_version: 3\n")
    return bp


def test_scan_open_only_excludes_archived_bug(tmp_path):
    """B-024: scan_bug_patterns(open_only=True) must exclude archived/non-open bugs."""
    from taskmaster_v3 import write_bug, scan_bug_patterns, archive_bug
    bp = _make_backlog(tmp_path)
    # Two open bugs that cluster together
    b1, _ = write_bug(bp, title="Connection timeout reader client socket", components=["net"], discovered_by="user")
    b2, _ = write_bug(bp, title="Connection timeout reader client socket retry", components=["net"], discovered_by="user")
    # A third bug with same cluster tokens but written as shelved (non-open) then promoted
    # Write it open first, then use update_bug to set promoted status for archiving
    from taskmaster_v3 import update_bug
    b3, _ = write_bug(bp, title="Connection timeout reader client socket fallback", components=["net"], discovered_by="user")
    update_bug(bp, b3, status="promoted", promoted_to="ISS-001")
    archive_bug(bp, b3)

    # include_archive=True (default) should include all three
    groups_all = scan_bug_patterns(bp, include_archive=True)
    all_ids = [bid for g in groups_all for bid in g["bug_ids"]]
    assert b3 in all_ids, "include_archive=True must include archived bug"

    # open_only=True must exclude the archived bug
    groups_open = scan_bug_patterns(bp, open_only=True)
    open_ids = [bid for g in groups_open for bid in g["bug_ids"]]
    assert b3 not in open_ids, "open_only=True must exclude archived bug"
    # b1 and b2 are still open and should cluster
    assert b1 in open_ids
    assert b2 in open_ids


def test_scan_open_only_false_includes_non_open(tmp_path):
    """B-024: open_only=False (default) includes all bugs regardless of status."""
    from taskmaster_v3 import write_bug, scan_bug_patterns, archive_bug
    bp = _make_backlog(tmp_path)
    b1, _ = write_bug(bp, title="Database connection timeout client socket reader", components=["db"], discovered_by="user")
    from taskmaster_v3 import update_bug
    b2, _ = write_bug(bp, title="Database connection timeout client socket writer", components=["db"], discovered_by="user")
    update_bug(bp, b2, status="promoted", promoted_to="ISS-001")
    archive_bug(bp, b2)
    groups = scan_bug_patterns(bp, include_archive=True, open_only=False)
    all_ids = [bid for g in groups for bid in g["bug_ids"]]
    assert b2 in all_ids


def test_backlog_bug_pattern_scan_invalid_mode(tmp_taskmaster):
    """B-024: the MCP wrapper must reject unknown mode strings."""
    from backlog_server import backlog_bug_pattern_scan
    result = backlog_bug_pattern_scan(mode="invalid_mode")
    assert result.startswith("Error:")
    assert "invalid_mode" in result


def test_backlog_bug_pattern_scan_open_only_mode(tmp_taskmaster):
    """B-024: mode='open_only' via MCP wrapper must use open_only filter."""
    from backlog_server import backlog_bug_pattern_scan
    # No backlog bugs present; just confirm it runs without error and does not
    # return the whole manifest or crash. An empty result is valid here.
    result = backlog_bug_pattern_scan(mode="open_only")
    # Must not return an error for a valid mode
    assert not result.startswith("Error:")
