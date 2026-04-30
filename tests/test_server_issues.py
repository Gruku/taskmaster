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
