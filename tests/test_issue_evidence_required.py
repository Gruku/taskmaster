"""Issue creation requires the new `evidence` field."""
import pytest


def test_validate_issue_rejects_empty_evidence():
    from taskmaster_v3 import _validate_issue
    fm = {
        "id": "ISS-001",
        "title": "test",
        "status": "open",
        "severity": "P1",
        "evidence": "",  # empty — should fail
    }
    with pytest.raises(ValueError, match="evidence is required"):
        _validate_issue(fm)


def test_validate_issue_accepts_non_empty_evidence():
    from taskmaster_v3 import _validate_issue
    fm = {
        "id": "ISS-001",
        "title": "test",
        "status": "open",
        "severity": "P1",
        "evidence": "Systemic: affects v3 reader and writer.",
    }
    _validate_issue(fm)


def test_write_issue_persists_evidence_field(tmp_path):
    from taskmaster_v3 import write_issue, read_issue
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text("schema_version: 3\n")
    iid, _ = write_issue(
        bp,
        title="t",
        severity="P1",
        impact="ev",
        evidence="Recurring: B-001 + B-002.",
    )
    fm, _ = read_issue(bp, iid)
    assert fm["evidence"] == "Recurring: B-001 + B-002."
