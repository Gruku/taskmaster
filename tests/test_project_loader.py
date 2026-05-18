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
