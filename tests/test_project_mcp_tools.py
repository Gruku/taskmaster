from __future__ import annotations

import yaml

from backlog_server import (
    backlog_project_get,
    backlog_project_get_field,
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
