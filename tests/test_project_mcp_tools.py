from __future__ import annotations

import pytest
import yaml

from backlog_server import (
    backlog_project_error_trace_ladder,
    backlog_project_get,
    backlog_project_get_field,
    backlog_project_init,
    backlog_project_set,
    backlog_project_ship_order,
)
from project import SCHEMA_VERSION


def _write(tmp_taskmaster, data: dict) -> None:
    (tmp_taskmaster / ".taskmaster" / "project.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_get_returns_none_when_missing(tmp_taskmaster):
    assert backlog_project_get() is None


def test_get_returns_dict_when_present(tmp_taskmaster):
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
    })
    result = backlog_project_get()
    assert result["meta"]["name"] == "x"


def test_get_field_dotted_path(tmp_taskmaster):
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
        "repos": [
            {"name": "api", "path": "./api",
             "branches": {"default": "develop", "protected": ["master"]}}
        ],
    })
    assert backlog_project_get_field("meta.name") == "x"
    assert backlog_project_get_field("repos[0].name") == "api"
    assert backlog_project_get_field("repos[0].branches.protected[0]") == "master"


def test_get_field_returns_none_on_missing_path(tmp_taskmaster):
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION, "meta": {"name": "x", "slug": "x"}
    })
    assert backlog_project_get_field("project.goal") is None
    assert backlog_project_get_field("repos[3].name") is None


def test_ship_order_returns_topo_sorted(tmp_taskmaster):
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
        "repos": [
            {"name": "ui", "path": "./ui", "depends_on": ["api"]},
            {"name": "api", "path": "./api"},
        ],
    })
    assert backlog_project_ship_order() == ["api", "ui"]


def test_ship_order_returns_empty_when_no_manifest(tmp_taskmaster):
    assert backlog_project_ship_order() == []


def test_set_writes_and_round_trips(tmp_taskmaster):
    yaml_text = """\
schema_version: 1
meta:
  name: demo
  slug: demo
"""
    backlog_project_set(yaml_text)
    written = (tmp_taskmaster / ".taskmaster" / "project.yaml").read_text(encoding="utf-8")
    assert "name: demo" in written
    assert backlog_project_get()["meta"]["name"] == "demo"


def test_set_rejects_invalid_yaml(tmp_taskmaster):
    with pytest.raises(ValueError, match="YAML"):
        backlog_project_set("not: valid: [\n")


def test_set_rejects_invalid_schema(tmp_taskmaster):
    bad = "schema_version: 99\nmeta:\n  name: x\n  slug: x\n"
    with pytest.raises(ValueError, match="schema_version"):
        backlog_project_set(bad)


def test_init_creates_scaffold_when_missing(tmp_taskmaster):
    result = backlog_project_init(name="demo", slug="demo")
    assert "created" in result.lower() or "wrote" in result.lower()
    data = backlog_project_get()
    assert data["meta"]["name"] == "demo"
    assert data["meta"]["slug"] == "demo"
    assert data["schema_version"] == SCHEMA_VERSION


def test_init_refuses_to_overwrite(tmp_taskmaster):
    backlog_project_init(name="demo", slug="demo")
    with pytest.raises(ValueError, match="exists"):
        backlog_project_init(name="other", slug="other")


def test_error_trace_ladder_returns_ladder(tmp_taskmaster):
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
        "integrations": {"observability": {"error_trace_ladder": [
            {"layer": "ui", "kind": "devtools-network"},
            {"layer": "api", "kind": "http-log", "path": "/var/log/api"},
        ]}},
    })
    ladder = backlog_project_error_trace_ladder()
    assert [e["layer"] for e in ladder] == ["ui", "api"]
    assert ladder[1]["path"] == "/var/log/api"


def test_error_trace_ladder_empty_when_no_manifest(tmp_taskmaster):
    assert backlog_project_error_trace_ladder() == []
