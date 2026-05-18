"""`.taskmaster/project.yaml` — Project manifest loader.

Spec: docs/superpowers/specs/2026-05-17-taskmaster-project-yaml-design.md
"""
from __future__ import annotations

from pathlib import Path

PROJECT_YAML_RELATIVE = Path(".taskmaster") / "project.yaml"


def project_yaml_path(project_root: Path) -> Path:
    """Return the absolute path to .taskmaster/project.yaml for a project root."""
    return project_root / PROJECT_YAML_RELATIVE


def resolve_project_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a directory containing `.taskmaster/`.

    Returns the directory containing `.taskmaster/`, or None if not found.
    """
    current = start.resolve()
    while True:
        if (current / ".taskmaster").is_dir():
            return current
        if current.parent == current:
            return None
        current = current.parent
