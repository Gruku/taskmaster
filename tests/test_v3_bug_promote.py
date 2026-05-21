"""Atomic promotion of N matched Bugs into one Issue."""
import pytest


def _setup(tmp_path):
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    return bp


def test_promote_bugs_creates_issue_and_marks_bugs(tmp_path):
    from taskmaster_v3 import write_bug, promote_bugs_to_issue, read_bug, read_issue
    bp = _setup(tmp_path)
    b1, _ = write_bug(bp, title="Path mismatch in v3 reader", components=["taskmaster"], discovered_by="user")
    b2, _ = write_bug(bp, title="Path mismatch in v3 reader handover variant", components=["taskmaster"], discovered_by="user")
    iss_id = promote_bugs_to_issue(
        bp,
        bug_ids=[b1, b2],
        title="Path mismatch across v3 readers",
        severity="P1",
        evidence_text="Recurring: B-001 and B-002 both show the same defect.",
    )
    assert iss_id.startswith("ISS-")
    fm_iss, _ = read_issue(bp, iss_id)
    assert fm_iss["evidence"]  # non-empty
    assert b1 in (fm_iss.get("promoted_from") or [])
    assert b2 in (fm_iss.get("promoted_from") or [])

    fm_b1, _ = read_bug(bp, b1)
    assert fm_b1["status"] == "promoted"
    assert fm_b1["promoted_to"] == iss_id


def test_promote_bugs_refuses_single_bug_without_evidence(tmp_path):
    from taskmaster_v3 import write_bug, promote_bugs_to_issue
    bp = _setup(tmp_path)
    b1, _ = write_bug(bp, title="Single defect", discovered_by="user")
    with pytest.raises(ValueError, match="evidence_text is required"):
        promote_bugs_to_issue(bp, bug_ids=[b1], title="Issue", severity="P1", evidence_text="")
