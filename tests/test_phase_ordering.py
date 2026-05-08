"""Regression tests for phase ordering and completion-rollup bugs.

Repro: user has phases with ids "1", "1.5", "2". Phase "1.5" was added after
"2", so the YAML stores them in the order [1, 2, 1.5].  Before the fix:

- /api/backlog served phases in YAML insertion order → viewer placed phase "2"
  BEFORE "1.5" in the stepper → phase "2" appeared as past/done.
- groupTasks(by='phase') used phasesArr in raw YAML order → swimlane columns
  appeared in wrong sequence.
- phaseNum("1.5") returned "1" (only first digit captured) → indistinguishable
  from phase "1" in the past-chip carousel.

Tests are split into two layers:
1. HTTP-server layer: /api/backlog returns phases sorted by order.
2. MCP-tool layer (via backlog_server module): _phase_stats, backlog_phase_status,
   backlog_advance_phase all use order-based sort — verify with a fixture where
   YAML insertion order differs from logical order.
"""
import json
import textwrap
import threading
import time
import urllib.request

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: project with phases (1, 1.5, 2) in WRONG insertion order
# (1, 2, 1.5) to match the real-world repro.
# ---------------------------------------------------------------------------

BACKLOG_YAML = textwrap.dedent("""\
    schema_version: 3
    meta:
      project: phase-order-test
    epics:
      - id: ep1
        name: Epic One
        tasks:
          - {id: t-001, title: Task A, status: done,   phase: phase-1}
          - {id: t-002, title: Task B, status: done,   phase: phase-1}
          - {id: t-003, title: Task C, status: todo,   phase: phase-1-5}
          - {id: t-004, title: Task D, status: todo,   phase: phase-2}
    phases:
      - id: phase-1
        name: Foundation
        order: 1
        status: done
        created: "2026-01-01T00:00:00Z"
      - id: phase-2
        name: Launch
        order: 3
        status: planned
        created: "2026-01-01T00:00:01Z"
      - id: phase-1-5
        name: Beta
        order: 2
        status: active
        created: "2026-01-01T00:00:02Z"
""")


@pytest.fixture
def out_of_order_project(tmp_path, monkeypatch):
    """Project where YAML insertion order differs from logical phase order."""
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    (tm / "backlog.yaml").write_text(BACKLOG_YAML, encoding="utf-8")
    # PROGRESS.md is required by _mutate_and_save → regenerate_progress_dashboard.
    (tm / "PROGRESS.md").write_text("", encoding="utf-8")

    import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "CONFIG_PATH", tm / "missing.json")
    monkeypatch.setattr(backlog_server, "LEGACY_CONFIG_PATH", tmp_path / ".claude" / "missing.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def running_server_oo(out_of_order_project, monkeypatch):
    """HTTP server backed by the out-of-order project."""
    import backlog_server
    server, port = backlog_server._make_server(host="127.0.0.1", port=0)
    backlog_server._init_storage()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    yield base, server
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# HTTP layer: /api/backlog must return phases in order-field order
# ---------------------------------------------------------------------------

class TestApiBacklogPhaseOrder:
    def test_phases_sorted_by_order_field(self, running_server_oo):
        base, _ = running_server_oo
        resp = urllib.request.urlopen(f"{base}/api/backlog")
        body = json.loads(resp.read())
        phases = body["phases"]
        assert [p["id"] for p in phases] == ["phase-1", "phase-1-5", "phase-2"], (
            "/api/backlog must serve phases sorted by order, not YAML insertion order"
        )

    def test_phases_order_values_ascending(self, running_server_oo):
        base, _ = running_server_oo
        body = json.loads(urllib.request.urlopen(f"{base}/api/backlog").read())
        orders = [p["order"] for p in body["phases"]]
        assert orders == sorted(orders), "Phase order values must be non-decreasing in response"

    def test_phase_2_not_before_phase_1_5(self, running_server_oo):
        """Core regression: phase-2 must not appear before phase-1-5 in the array."""
        base, _ = running_server_oo
        body = json.loads(urllib.request.urlopen(f"{base}/api/backlog").read())
        ids = [p["id"] for p in body["phases"]]
        idx_1_5 = ids.index("phase-1-5")
        idx_2   = ids.index("phase-2")
        assert idx_1_5 < idx_2, (
            f"phase-1-5 (order=2) must come before phase-2 (order=3), "
            f"but got phase-1-5 at index {idx_1_5} and phase-2 at index {idx_2}"
        )


# ---------------------------------------------------------------------------
# MCP-tool layer: _phase_stats uses exact match — no prefix collision
# ---------------------------------------------------------------------------

class TestPhaseStatsExactMatch:
    def test_phase_1_done_count_not_contaminated_by_phase_1_5(self, out_of_order_project):
        """_phase_stats uses exact id match so 'phase-1' never picks up 'phase-1-5' tasks."""
        from backlog_server import _phase_stats, _load
        # out_of_order_project fixture sets monkeypatch.chdir so _backlog_path() resolves.
        data = _load()
        stats_1 = _phase_stats(data, "phase-1")
        stats_1_5 = _phase_stats(data, "phase-1-5")
        # phase-1 has 2 done tasks (t-001, t-002)
        assert stats_1["done"] == 2, f"phase-1 done count wrong: {stats_1}"
        # phase-1-5 has 1 task (t-003), not done
        assert stats_1_5["total"] == 1, f"phase-1-5 total count wrong: {stats_1_5}"
        assert stats_1_5["done"] == 0, f"phase-1-5 done count wrong: {stats_1_5}"

    def test_phase_2_stats_not_contaminated(self, out_of_order_project):
        """_phase_stats for phase-2 must not capture tasks from phase-1 or phase-1-5."""
        from backlog_server import _phase_stats, _load
        data = _load()
        stats_2 = _phase_stats(data, "phase-2")
        # phase-2 has 1 task (t-004)
        assert stats_2["total"] == 1, f"phase-2 total count wrong: {stats_2}"
        assert stats_2["done"] == 0, f"phase-2 done count wrong: {stats_2}"


# ---------------------------------------------------------------------------
# MCP-tool layer: backlog_phase_status output (sorted by order)
# ---------------------------------------------------------------------------

class TestPhaseStatusMcpTool:
    def test_phase_status_active_phase_correct(self, out_of_order_project):
        import backlog_server
        result = backlog_server.backlog_phase_status()
        # Active phase is phase-1-5 (Beta)
        assert "Beta" in result, f"Expected active phase 'Beta' in output:\n{result}"
        assert "phase-1-5" in result or "Beta" in result

    def test_phase_status_next_phase_is_phase_2(self, out_of_order_project):
        import backlog_server
        result = backlog_server.backlog_phase_status()
        # Next phase after phase-1-5 (order=2) must be phase-2 (order=3), not phase-1
        assert "Launch" in result, f"Expected next phase 'Launch' in output:\n{result}"

    def test_phases_listed_in_order(self, out_of_order_project):
        import backlog_server
        result = backlog_server.backlog_phase_status("phase-1")
        # When viewing phase-1, next should be phase-1-5 (Beta), not phase-2 (Launch)
        assert "Beta" in result, (
            f"Phase after Foundation should be Beta (phase-1-5), "
            f"not Launch (phase-2):\n{result}"
        )


# ---------------------------------------------------------------------------
# MCP-tool layer: backlog_advance_phase activates phase-1-5, not phase-2
# ---------------------------------------------------------------------------

class TestAdvancePhaseOrder:
    def test_advance_activates_correct_next_phase(self, out_of_order_project):
        """advance_phase must use order-field to pick next phase, not YAML position."""
        import yaml
        import backlog_server
        from pathlib import Path

        backlog_path = out_of_order_project / ".taskmaster" / "backlog.yaml"

        # Mark all phase-1-5 tasks as done so advance_phase doesn't block.
        data = yaml.safe_load(backlog_path.read_text())
        for epic in data.get("epics", []):
            for t in epic.get("tasks", []):
                if t.get("phase") == "phase-1-5":
                    t["status"] = "done"
        backlog_path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")

        result = backlog_server.backlog_advance_phase(force=True)
        # After advancing, phase-2 (Launch) should be activated — not phase-1
        assert "Launch" in result, (
            f"advance_phase should activate phase-2 (Launch, order=3), got:\n{result}"
        )
        # Verify the state was actually written
        data2 = yaml.safe_load(backlog_path.read_text())
        phases_by_id = {p["id"]: p for p in data2.get("phases", [])}
        assert phases_by_id["phase-2"]["status"] == "active", (
            "phase-2 should be active after advancing from phase-1-5"
        )
        assert phases_by_id["phase-1-5"]["status"] == "done", (
            "phase-1-5 should be done after advancing"
        )
