"""HTTP tests for /api/issues + compute_issue_aging unit tests."""
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_server_api import running_server  # noqa: F401


def _write_issue(root: Path, issue_id: str, **overrides):
    import yaml
    base = {
        "id": issue_id,
        "title": "Sample defect",
        "status": "open",
        "severity": "P1",
        "components": ["viewer"],
        "impact": "Users cannot save filters.",
        "location": ["viewer/cards.css:206"],
        "discovered": "2026-04-10T00:00:00Z",
        "discovered_by": "user",
        "resolved": None,
        "related_tasks": [],
        "fixed_in_task": None,
        "duplicate_of": None,
        "symptom": "The filter clears on reload.",
        "repro": ["open kanban", "set epic filter", "reload page"],
    }
    base.update(overrides)
    p = root / ".taskmaster" / "issues" / f"{issue_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = "---\n" + yaml.safe_dump(base, sort_keys=False).rstrip() + "\n---\nBody.\n"
    p.write_text(fm)


def test_compute_issue_aging_fresh_band():
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    aging_cfg = {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}
    out = compute_issue_aging(issue, aging_cfg, now=now)
    assert out["tier"] == "Fresh"
    assert 0 <= out["percent"] < 25


def test_compute_issue_aging_aging_band():
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    out = compute_issue_aging(issue, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    assert out["tier"] == "Aging"
    assert 25 <= out["percent"] < 60


def test_compute_issue_aging_stale_band():
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    out = compute_issue_aging(issue, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    assert out["tier"] == "Stale"
    assert out["percent"] >= 60


def test_compute_issue_aging_critical_decays_faster_than_low():
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    discovered = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    crit = compute_issue_aging({"discovered": discovered, "severity": "P0"}, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    low = compute_issue_aging({"discovered": discovered, "severity": "P3"}, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    assert crit["percent"] > low["percent"]


def test_get_issues_returns_list_with_aging_and_label(running_server, tmp_path):
    base, _ = running_server
    _write_issue(tmp_path, "ISS-001", severity="P0", discovered=(datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _write_issue(tmp_path, "ISS-002", severity="P3", status="fixed", resolved=(datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    resp = urllib.request.urlopen(f"{base}/api/issues")
    payload = json.loads(resp.read())
    by_id = {i["id"]: i for i in payload["issues"]}
    assert by_id["ISS-001"]["severity_label"] == "Critical"
    assert by_id["ISS-001"]["aging"]["tier"] in {"Fresh", "Aging", "Stale"}
    assert by_id["ISS-002"]["severity_label"] == "Low"


def test_get_issues_excludes_resolved_when_query_param_set(running_server, tmp_path):
    base, _ = running_server
    _write_issue(tmp_path, "ISS-010", status="open")
    _write_issue(tmp_path, "ISS-011", status="fixed", resolved="2026-04-20T00:00:00Z")

    resp = urllib.request.urlopen(f"{base}/api/issues?include_resolved=false")
    payload = json.loads(resp.read())
    ids = [i["id"] for i in payload["issues"]]
    assert "ISS-010" in ids
    assert "ISS-011" not in ids


def test_compute_issue_aging_accepts_date_only_format():
    """ISS-005: compute_issue_aging must accept YYYY-MM-DD without crashing."""
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    # 5 days ago in date-only format
    issue = {"discovered": "2026-05-10", "severity": "P1"}
    aging_cfg = {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}
    out = compute_issue_aging(issue, aging_cfg, now=now)
    # 5 days / 30 days High base = ~16.7% → Fresh
    assert out["tier"] == "Fresh"
    assert 0 <= out["percent"] < 25


def test_compute_issue_aging_date_only_stale():
    """ISS-005: date-only format should still produce correct tier (Stale)."""
    from taskmaster.taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    # 60 days ago in date-only format → P1/High base=30 → 200% → Stale
    issue = {"discovered": "2026-03-16", "severity": "P1"}
    aging_cfg = {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}
    out = compute_issue_aging(issue, aging_cfg, now=now)
    assert out["tier"] == "Stale"
    assert out["percent"] >= 60


def test_compute_issue_aging_invalid_format_returns_fresh():
    """ISS-005: completely malformed discovered should degrade gracefully to Fresh."""
    from taskmaster.taskmaster_v3 import compute_issue_aging
    issue = {"discovered": "not-a-date", "severity": "P1"}
    aging_cfg = {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}
    out = compute_issue_aging(issue, aging_cfg)
    assert out["tier"] == "Fresh"
    assert out["percent"] == 0.0


def test_api_issues_date_only_discovered_included(running_server, tmp_path):
    """ISS-005: /api/issues must include issues with date-only discovered field, not skip them."""
    base, _ = running_server
    _write_issue(tmp_path, "ISS-D01", discovered="2026-05-10")
    _write_issue(tmp_path, "ISS-D02", discovered="2026-05-01T00:00:00Z")

    payload = json.loads(urllib.request.urlopen(f"{base}/api/issues").read())
    ids = [i["id"] for i in payload["issues"]]
    # Both should appear — date-only must not be silently dropped
    assert "ISS-D01" in ids
    assert "ISS-D02" in ids


def test_api_issues_malformed_discovered_does_not_blank_others(running_server, tmp_path):
    """ISS-005: one issue with completely invalid discovered must not blank the whole response."""
    base, _ = running_server
    _write_issue(tmp_path, "ISS-BAD", discovered="not-a-date")
    _write_issue(tmp_path, "ISS-GOOD", discovered="2026-05-01T00:00:00Z")

    payload = json.loads(urllib.request.urlopen(f"{base}/api/issues").read())
    ids = [i["id"] for i in payload["issues"]]
    # ISS-GOOD must be present regardless of ISS-BAD
    assert "ISS-GOOD" in ids


def test_aging_override_changes_tier(running_server, tmp_path):
    base, _ = running_server
    discovered = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_issue(tmp_path, "ISS-AG1", severity="P1", discovered=discovered)

    # Default High base = 30d → 5d ≈ 17% → Fresh
    payload = json.loads(urllib.request.urlopen(f"{base}/api/issues").read())
    by_id = {i["id"]: i for i in payload["issues"]}
    assert by_id["ISS-AG1"]["aging"]["tier"] == "Fresh"

    # Override High base to 5 days → 5d == 100% → Stale
    body = json.dumps({"issues": {"aging": {"High": 5}}}).encode()
    req = urllib.request.Request(
        f"{base}/api/viewer/prefs", data=body, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)

    payload = json.loads(urllib.request.urlopen(f"{base}/api/issues").read())
    by_id = {i["id"]: i for i in payload["issues"]}
    assert by_id["ISS-AG1"]["aging"]["tier"] == "Stale"
