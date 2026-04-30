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
    from taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    aging_cfg = {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}
    out = compute_issue_aging(issue, aging_cfg, now=now)
    assert out["tier"] == "Fresh"
    assert 0 <= out["percent"] < 25


def test_compute_issue_aging_aging_band():
    from taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    out = compute_issue_aging(issue, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    assert out["tier"] == "Aging"
    assert 25 <= out["percent"] < 60


def test_compute_issue_aging_stale_band():
    from taskmaster_v3 import compute_issue_aging
    now = datetime(2026, 4, 26, tzinfo=timezone.utc)
    issue = {"discovered": (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": "P1"}
    out = compute_issue_aging(issue, {"Critical": 14, "High": 30, "Medium": 60, "Low": 120}, now=now)
    assert out["tier"] == "Stale"
    assert out["percent"] >= 60


def test_compute_issue_aging_critical_decays_faster_than_low():
    from taskmaster_v3 import compute_issue_aging
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
