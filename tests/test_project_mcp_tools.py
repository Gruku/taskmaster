from __future__ import annotations

import pytest
import yaml

from taskmaster.backlog_server import (
    backlog_project_error_trace_ladder,
    backlog_project_get,
    backlog_project_get_field,
    backlog_project_init,
    backlog_project_set,
    backlog_project_ship_order,
)
from taskmaster.project import SCHEMA_VERSION


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


# ---------------------------------------------------------------------------
# B-019: _dig returns whole manifest on empty/whitespace path
# ---------------------------------------------------------------------------


def test_get_field_empty_string_returns_none(tmp_taskmaster):
    """B-019: backlog_project_get_field('') must return None, not the whole manifest."""
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
    })
    assert backlog_project_get_field("") is None


def test_get_field_whitespace_path_returns_none(tmp_taskmaster):
    """B-019: backlog_project_get_field('   ') must return None."""
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
    })
    assert backlog_project_get_field("   ") is None


def test_get_field_valid_path_still_works_after_fix(tmp_taskmaster):
    """B-019: ensure a valid path still resolves after the empty-path guard."""
    _write(tmp_taskmaster, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "hello", "slug": "hello"},
    })
    assert backlog_project_get_field("meta.name") == "hello"


# ---------------------------------------------------------------------------
# B-016: backlog_project_init(name='') writes invalid manifest
# ---------------------------------------------------------------------------


def test_init_empty_name_raises_and_writes_no_file(tmp_taskmaster):
    """B-016: init with empty name must raise ValueError and write nothing."""
    yaml_path = tmp_taskmaster / ".taskmaster" / "project.yaml"
    assert not yaml_path.exists()
    with pytest.raises(ValueError, match="name"):
        backlog_project_init(name="")
    assert not yaml_path.exists(), "project.yaml must NOT be written on empty name"


def test_init_whitespace_name_raises(tmp_taskmaster):
    """B-016: init with whitespace-only name must also raise ValueError."""
    with pytest.raises(ValueError, match="name"):
        backlog_project_init(name="   ")


def test_init_valid_name_still_creates_manifest(tmp_taskmaster):
    """B-016: a valid name still produces a readable manifest."""
    result = backlog_project_init(name="myproj", slug="myproj")
    assert "myproj" in result or "created" in result.lower()
    data = backlog_project_get()
    assert data is not None
    assert data["meta"]["name"] == "myproj"
