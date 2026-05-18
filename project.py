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


# ---------------------------------------------------------------------------
# Task 2: Schema dataclasses
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from typing import Any


# Opinionated defaults baked into the schema (spec section "Schema (v1)")
_DEFAULT_PUSH_POLICY = "always-ask"
_DEFAULT_POINTER_POLICY = "separate-chore-commit"
_DEFAULT_TDD = "preferred"
_DEFAULT_COMMIT_STYLE = "freeform"
_DEFAULT_SPEC_RATIO_WARN = 3


@dataclass
class Branches:
    default: str = ""
    protected: list[str] = field(default_factory=list)
    naming: str = ""


@dataclass
class Repo:
    name: str
    path: str
    description: str = ""
    stack: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    branches: Branches = field(default_factory=Branches)
    push_policy: str = _DEFAULT_PUSH_POLICY


@dataclass
class Submodule:
    name: str
    parent_repo: str
    path: str
    description: str = ""
    stack: list[str] = field(default_factory=list)
    pointer_policy: str = _DEFAULT_POINTER_POLICY
    upstream: str = ""


@dataclass
class ErrorTraceEntry:
    layer: str
    kind: str  # devtools-network | console | http-log | trace
    description: str = ""
    path: str = ""
    url: str = ""
    provider: str = ""


@dataclass
class Observability:
    error_trace_ladder: list[ErrorTraceEntry] = field(default_factory=list)


@dataclass
class ExternalIntegration:
    name: str
    kind: str = ""
    docs: str = ""


@dataclass
class Integrations:
    observability: Observability = field(default_factory=Observability)
    external: list[ExternalIntegration] = field(default_factory=list)


@dataclass
class DeployTarget:
    target: str
    repos: list[str] = field(default_factory=list)
    branch: str = ""


@dataclass
class KnowledgeLink:
    title: str = ""
    path: str = ""
    url: str = ""


@dataclass
class Knowledge:
    docs: list[KnowledgeLink] = field(default_factory=list)
    dashboards: list[KnowledgeLink] = field(default_factory=list)
    links: list[KnowledgeLink] = field(default_factory=list)


@dataclass
class Policies:
    tdd: str = _DEFAULT_TDD
    commit_style: str = _DEFAULT_COMMIT_STYLE
    spec_to_task_ratio_warn: int = _DEFAULT_SPEC_RATIO_WARN


@dataclass
class Conventions:
    narrative_ref: str = "./CLAUDE.md"
    policies: Policies = field(default_factory=Policies)


@dataclass
class Meta:
    name: str
    slug: str
    kind: str = "app"  # app | library | research | platform | tool
    updated: str = ""


@dataclass
class ProjectIdentity:
    description: str = ""
    goal: str = ""
    owners: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ProjectManifest:
    schema_version: int
    meta: Meta
    project: ProjectIdentity = field(default_factory=ProjectIdentity)
    repos: list[Repo] = field(default_factory=list)
    submodules: list[Submodule] = field(default_factory=list)
    integrations: Integrations = field(default_factory=Integrations)
    deploy: list[DeployTarget] = field(default_factory=list)
    knowledge: Knowledge = field(default_factory=Knowledge)
    conventions: Conventions = field(default_factory=Conventions)
    extensions: dict[str, Any] = field(default_factory=dict)
