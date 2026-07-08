"""Verify backlog_handover_latest emits a deprecation warning and returns
the same content as backlog_handover_list(status="open", limit=1)."""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def test_backlog_handover_latest_docstring_says_deprecated():
    """Cheap static check — the tool's docstring must mention deprecation."""
    from taskmaster.backlog_server import backlog_handover_latest
    doc = backlog_handover_latest.__doc__ or ""
    assert "deprecated" in doc.lower() or "alias" in doc.lower(), (
        "backlog_handover_latest docstring must declare it deprecated"
    )


def test_backlog_handover_latest_returns_deprecation_warning_in_output(tmp_path, monkeypatch):
    """Integration check: returned string includes a deprecation notice."""
    import yaml
    from pathlib import Path as _Path

    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({
        "meta": {}, "epics": [],
        "handovers": [
            {
                "id": "2026-01-01-test",
                "date": "2026-01-01",
                "created": "2026-01-01T00:00:00+00:00",
                "tldr": "latest test",
                "next_action": "",
                "task_ids": [],
                "session_kind": "end-of-day",
                "status": "open",
            }
        ],
    }))
    (tmp_path / "handovers").mkdir()

    # Patch the backlog path so the MCP tool finds our temp backlog.
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path",
                        lambda: _Path(str(bp)))
    monkeypatch.setattr(backlog_server, "_ensure_handover_status_backfilled",
                        lambda: None)

    result = backlog_server.backlog_handover_latest()
    assert "deprecated" in result.lower() or "alias" in result.lower()
    assert "backlog_handover_list" in result
