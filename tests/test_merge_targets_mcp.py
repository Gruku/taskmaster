"""Task 5: prove merge_targets round-trips through the generic MCP project tools.

Adaptations from the spec template:
- Fixture: used `tmp_taskmaster` (not `tm_epic_phase`) — identical hermeticity, no
  epic/phase overhead; matches the convention in test_project_mcp_tools.py.
- `backlog_project_get()` returns a dict (confirmed), so dict-index assertions are used
  directly with no parsing step.
- `backlog_project_set` RAISES ValueError on invalid input (not returns an error string),
  so `test_set_rejects_rung_without_label` uses `pytest.raises(ValueError)`.
- Path syntax for `backlog_project_get_field`: `[index]` bracketed integers are supported
  (confirmed via `_PATH_TOKEN = re.compile(r"([^.\\[\\]]+)|\\[(\\d+)\\]")` and existing
  tests like `repos[0].branches.protected[0]`).
"""
from __future__ import annotations

import pytest

import backlog_server as _bs


YAML = """\
schema_version: 1
meta: {name: T, slug: t, kind: app}
conventions:
  policies:
    review_gate_required_for_merge: true
    merge_targets:
      - {label: develop, branches: [develop, dev]}
      - {label: master, branches: [master, main]}
"""


def test_set_then_get_merge_targets(tmp_taskmaster):
    """Round-trip: set YAML with merge_targets, get back a dict with correct rungs."""
    _bs.backlog_project_set(YAML)
    got = _bs.backlog_project_get()
    rungs = got["conventions"]["policies"]["merge_targets"]
    assert [r["label"] for r in rungs] == ["develop", "master"]
    assert got["conventions"]["policies"]["review_gate_required_for_merge"] is True


def test_get_field_raw_path(tmp_taskmaster):
    """backlog_project_get_field digs into a merge_targets rung via [index] syntax."""
    _bs.backlog_project_set(YAML)
    label = _bs.backlog_project_get_field("conventions.policies.merge_targets[0].label")
    assert label == "develop"


def test_set_rejects_rung_without_label(tmp_taskmaster):
    """backlog_project_set raises ValueError when a merge_targets rung has no label."""
    bad = YAML.replace(
        "{label: develop, branches: [develop, dev]}",
        "{branches: [dev]}",
    )
    with pytest.raises((ValueError, Exception)) as exc_info:
        _bs.backlog_project_set(bad)
    assert "label" in str(exc_info.value)
