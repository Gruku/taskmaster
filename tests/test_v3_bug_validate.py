"""Frontmatter invariants for Bug artifacts."""
import pytest


def _valid_fm(**overrides):
    base = {
        "id": "B-001",
        "title": "Path mismatch in v3 handover reader",
        "status": "open",
        "severity": None,  # optional
        "components": ["taskmaster"],
        "found_in": None,
        "discovered": "2026-05-20T10:00:00Z",
        "discovered_by": "user",
        "location": [],
        "fix_commit": None,
        "adopted_into": None,
        "promoted_to": None,
        "links": [],
    }
    base.update(overrides)
    return base


def test_validate_bug_accepts_minimal_open():
    from taskmaster.taskmaster_v3 import _validate_bug
    _validate_bug(_valid_fm())


def test_validate_bug_rejects_unknown_status():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="status must be one of"):
        _validate_bug(_valid_fm(status="weird"))


def test_validate_bug_rejects_unknown_severity():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="severity must be one of"):
        _validate_bug(_valid_fm(severity="P9"))


def test_validate_bug_allows_severity_none():
    from taskmaster.taskmaster_v3 import _validate_bug
    _validate_bug(_valid_fm(severity=None))  # severity optional


def test_validate_bug_fixed_requires_fix_commit():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="status=fixed requires fix_commit"):
        _validate_bug(_valid_fm(status="fixed", fix_commit=None))


def test_validate_bug_adopted_requires_adopted_into():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="status=adopted requires adopted_into"):
        _validate_bug(_valid_fm(status="adopted"))


def test_validate_bug_promoted_requires_promoted_to():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="status=promoted requires promoted_to"):
        _validate_bug(_valid_fm(status="promoted"))


def test_validate_bug_rejects_unknown_discovered_by():
    from taskmaster.taskmaster_v3 import _validate_bug
    with pytest.raises(ValueError, match="discovered_by must be"):
        _validate_bug(_valid_fm(discovered_by="alien"))
