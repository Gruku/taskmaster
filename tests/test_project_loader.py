from __future__ import annotations

from pathlib import Path

import pytest

from project import (
    PROJECT_YAML_RELATIVE,
    project_yaml_path,
    resolve_project_root,
)


def test_project_yaml_relative_path():
    assert PROJECT_YAML_RELATIVE == Path(".taskmaster") / "project.yaml"


def test_project_yaml_path_joins(tmp_path):
    assert project_yaml_path(tmp_path) == tmp_path / ".taskmaster" / "project.yaml"


def test_resolve_project_root_returns_dir_containing_taskmaster(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert resolve_project_root(nested) == tmp_path


def test_resolve_project_root_returns_none_when_not_found(tmp_path):
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert resolve_project_root(nested) is None


# ---------------------------------------------------------------------------
# Task 4: load_project_manifest, load_project_manifest_or_default, manifest_to_dict
# ---------------------------------------------------------------------------

import yaml

from project import (
    SCHEMA_VERSION,
    ProjectManifest,
    load_project_manifest,
    load_project_manifest_or_default,
    manifest_to_dict,
)


def _write_manifest(tmp_path, data: dict) -> None:
    (tmp_path / ".taskmaster").mkdir(exist_ok=True)
    (tmp_path / ".taskmaster" / "project.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_load_returns_none_when_file_missing(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    assert load_project_manifest(tmp_path) is None


def test_load_or_default_returns_empty_manifest_when_missing(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    m = load_project_manifest_or_default(tmp_path)
    assert isinstance(m, ProjectManifest)
    assert m.schema_version == SCHEMA_VERSION
    assert m.repos == []
    assert m.meta.name == ""  # synthetic placeholder
    assert m.meta.slug == ""


def test_load_minimal_manifest(tmp_path):
    _write_manifest(tmp_path, {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "demo", "slug": "demo"},
    })
    m = load_project_manifest(tmp_path)
    assert m is not None
    assert m.meta.name == "demo"


def test_load_full_manifest_round_trips(tmp_path):
    data = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "cm", "slug": "cm", "kind": "app"},
        "project": {"goal": "ship it", "owners": ["gruku"]},
        "repos": [
            {"name": "api", "path": "./api", "branches": {"default": "develop"}},
            {"name": "ui", "path": "./ui", "depends_on": ["api"]},
        ],
        "submodules": [
            {"name": "mcp", "parent_repo": "ui", "path": "mcp"}
        ],
        "extensions": {"foo": "bar"},
    }
    _write_manifest(tmp_path, data)
    m = load_project_manifest(tmp_path)
    assert m is not None
    assert len(m.repos) == 2
    assert m.repos[1].depends_on == ["api"]
    assert m.submodules[0].parent_repo == "ui"
    assert m.extensions == {"foo": "bar"}
    # Round-trip
    assert manifest_to_dict(m)["extensions"] == {"foo": "bar"}


def test_load_invalid_manifest_returns_none_and_warns(tmp_path, caplog):
    _write_manifest(tmp_path, {"schema_version": 99, "meta": {}})
    m = load_project_manifest(tmp_path)
    assert m is None
    assert any("project.yaml" in rec.message for rec in caplog.records)


def test_load_malformed_yaml_returns_none(tmp_path, caplog):
    (tmp_path / ".taskmaster").mkdir()
    (tmp_path / ".taskmaster" / "project.yaml").write_text(
        "not: valid: yaml: [\n", encoding="utf-8"
    )
    assert load_project_manifest(tmp_path) is None


# ---------------------------------------------------------------------------
# B-017: duplicate repo names pass validation
# ---------------------------------------------------------------------------

from project import validate_manifest_dict


def test_validate_rejects_duplicate_repo_names():
    """B-017: two repos with the same name must fail validation."""
    data = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "proj", "slug": "proj"},
        "repos": [
            {"name": "api", "path": "./api"},
            {"name": "api", "path": "./svc"},
        ],
    }
    ok, errs = validate_manifest_dict(data)
    assert not ok
    assert any("duplicate" in e and "api" in e for e in errs)


def test_validate_unique_repo_names_pass():
    """B-017: distinct repo names still pass."""
    data = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "proj", "slug": "proj"},
        "repos": [
            {"name": "api", "path": "./api"},
            {"name": "ui", "path": "./ui"},
        ],
    }
    ok, errs = validate_manifest_dict(data)
    assert ok, errs


# ---------------------------------------------------------------------------
# B-018: schema_version float / bool passes incorrectly
# ---------------------------------------------------------------------------


def test_validate_rejects_float_schema_version():
    """B-018: schema_version=1.0 (float) must be rejected."""
    data = {
        "schema_version": 1.0,
        "meta": {"name": "proj", "slug": "proj"},
    }
    ok, errs = validate_manifest_dict(data)
    assert not ok
    assert any("schema_version" in e for e in errs)


def test_validate_rejects_bool_schema_version():
    """B-018: schema_version=True (bool) must be rejected."""
    data = {
        "schema_version": True,
        "meta": {"name": "proj", "slug": "proj"},
    }
    ok, errs = validate_manifest_dict(data)
    assert not ok
    assert any("schema_version" in e for e in errs)


def test_validate_accepts_int_schema_version():
    """B-018: schema_version=SCHEMA_VERSION (int) must still pass."""
    data = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "proj", "slug": "proj"},
    }
    ok, errs = validate_manifest_dict(data)
    assert ok, errs
