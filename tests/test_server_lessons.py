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
