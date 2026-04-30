"""HTTP tests for /api/lessons routes. Reuses the running_server fixture from test_server_api."""
import json
import urllib.request
from pathlib import Path

import pytest

from tests.test_server_api import running_server  # noqa: F401  (re-export fixture)


def _write_lesson(root: Path, lesson_id: str, **overrides):
    base = {
        "id": lesson_id,
        "title": "Sample",
        "kind": "gotcha",
        "tier": "active",
        "triggers": {"files": ["**/*.css"], "task_titles_match": [], "task_kinds": []},
        "reinforce_count": 2,
        "last_reinforced": "2026-04-15T00:00:00Z",
        "created": "2026-03-01T00:00:00Z",
        "related_tasks": [],
        "related_issues": [],
        "reinforce_events": [],
    }
    base.update(overrides)
    p = root / ".taskmaster" / "lessons" / f"{lesson_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = []
    import yaml
    fm_lines.append("---")
    fm_lines.append(yaml.safe_dump(base, sort_keys=False).rstrip())
    fm_lines.append("---")
    fm_lines.append("Body.")
    p.write_text("\n".join(fm_lines))


def test_post_reinforce_bumps_counter_and_returns_summary(running_server, tmp_path):
    base, _ = running_server
    _write_lesson(tmp_path, "L-100")

    body = json.dumps({"source": "user", "note": "deliberate apply"}).encode()
    req = urllib.request.Request(
        f"{base}/api/lessons/L-100/reinforce",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    payload = json.loads(resp.read())
    assert payload["id"] == "L-100"
    assert payload["reinforce_count"] == 3


def test_post_reinforce_unknown_id_returns_404(running_server):
    base, _ = running_server
    body = json.dumps({"source": "user"}).encode()
    req = urllib.request.Request(
        f"{base}/api/lessons/L-NOPE/reinforce",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 404


def test_post_reinforce_rejects_bad_source(running_server, tmp_path):
    base, _ = running_server
    _write_lesson(tmp_path, "L-101")
    body = json.dumps({"source": "alien"}).encode()
    req = urllib.request.Request(
        f"{base}/api/lessons/L-101/reinforce",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_get_lessons_returns_list_with_shelf_placement(running_server, tmp_path):
    from datetime import datetime, timedelta, timezone
    base, _ = running_server
    now = datetime.now(timezone.utc)
    fresh_events = [
        {"at": (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
        for d in [1, 2, 3, 4, 5, 6, 7, 8]
    ]
    cold_events = [
        {"at": (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
    ]

    _write_lesson(tmp_path, "L-CORE", reinforce_count=8, reinforce_events=fresh_events)
    _write_lesson(tmp_path, "L-COLD", reinforce_count=1, reinforce_events=cold_events)

    resp = urllib.request.urlopen(f"{base}/api/lessons")
    assert resp.status == 200
    payload = json.loads(resp.read())
    assert "lessons" in payload
    by_id = {l["id"]: l for l in payload["lessons"]}
    assert by_id["L-CORE"]["shelf"] == "core"
    assert by_id["L-COLD"]["shelf"] == "retired"


def test_thresholds_override_changes_shelf_placement(running_server, tmp_path):
    """If user lowers core_count to 2, lessons with 3 events go to 'core'."""
    base, _ = running_server
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    events = [
        {"at": (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "user", "note": ""}
        for d in [1, 5, 10]
    ]
    _write_lesson(tmp_path, "L-T1", reinforce_count=3, reinforce_events=events)

    # Default thresholds → 3 events is below core_count=7 → 'active'
    payload = json.loads(urllib.request.urlopen(f"{base}/api/lessons").read())
    by_id = {l["id"]: l for l in payload["lessons"]}
    assert by_id["L-T1"]["shelf"] == "active"

    # Lower the threshold via PUT /api/viewer/prefs
    body = json.dumps({"lessons": {"thresholds": {"core_count": 2}}}).encode()
    req = urllib.request.Request(
        f"{base}/api/viewer/prefs", data=body, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)

    # Now the same lesson should be 'core'
    payload = json.loads(urllib.request.urlopen(f"{base}/api/lessons").read())
    by_id = {l["id"]: l for l in payload["lessons"]}
    assert by_id["L-T1"]["shelf"] == "core"


def test_reinforce_records_event_with_correct_source_and_note(running_server, tmp_path):
    base, _ = running_server
    _write_lesson(tmp_path, "L-300")
    body = json.dumps({"source": "claude", "note": "applied during refactor"}).encode()
    req = urllib.request.Request(
        f"{base}/api/lessons/L-300/reinforce", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)

    # Read back via API
    payload = json.loads(urllib.request.urlopen(f"{base}/api/lessons").read())
    by_id = {l["id"]: l for l in payload["lessons"]}
    events = by_id["L-300"]["reinforce_events"]
    assert events[-1]["source"] == "claude"
    assert events[-1]["note"] == "applied during refactor"
