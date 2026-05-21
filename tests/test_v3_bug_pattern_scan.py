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
