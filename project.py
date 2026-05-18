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


# ---------------------------------------------------------------------------
# Task 3: Hand-written validator
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

KIND_VALUES = ("app", "library", "research", "platform", "tool")
PUSH_POLICY_VALUES = ("always-ask", "gated", "open")
POINTER_POLICY_VALUES = ("separate-chore-commit", "allow-mixed")
TRACE_KIND_VALUES = ("devtools-network", "console", "http-log", "trace")
TDD_VALUES = ("required", "preferred", "optional")
COMMIT_STYLE_VALUES = ("conventional", "freeform")


class ValidationError(ValueError):
    """Raised when manifest validation fails in strict mode."""


def _check_enum(value: Any, allowed: tuple[str, ...], field: str, errors: list[str]) -> None:
    if value not in allowed:
        errors.append(f"{field}: {value!r} is not one of {list(allowed)}")


def _has_cycle(repos: list[dict]) -> bool:
    """DFS cycle detection over depends_on edges."""
    graph: dict[str, list[str]] = {r.get("name", ""): list(r.get("depends_on") or []) for r in repos}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in graph}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue  # unknown repo — caught by separate validation
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and dfs(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color.get(n, WHITE) == WHITE and dfs(n) for n in graph)


def validate_manifest_dict(
    data: dict, *, raise_on_error: bool = False
) -> tuple[bool, list[str]]:
    """Validate a manifest dict against the v1 schema.

    Returns (ok, errors). If raise_on_error and not ok, raises ValidationError.
    """
    errors: list[str] = []

    sv = data.get("schema_version")
    if sv is None:
        errors.append("schema_version: required")
    elif sv != SCHEMA_VERSION:
        errors.append(f"schema_version: {sv!r} is unknown (expected {SCHEMA_VERSION})")

    meta = data.get("meta") or {}
    for required in ("name", "slug"):
        if not meta.get(required):
            errors.append(f"meta.{required}: required")
    if "kind" in meta:
        _check_enum(meta["kind"], KIND_VALUES, "meta.kind", errors)

    repos = data.get("repos") or []
    repo_names: set[str] = set()
    for i, r in enumerate(repos):
        name = r.get("name")
        if not name:
            errors.append(f"repos[{i}].name: required")
        else:
            repo_names.add(name)
        if not r.get("path"):
            errors.append(f"repos[{i}].path: required")
        if "push_policy" in r:
            _check_enum(r["push_policy"], PUSH_POLICY_VALUES, f"repos[{i}].push_policy", errors)

    for i, r in enumerate(repos):
        for dep in r.get("depends_on") or []:
            if dep not in repo_names:
                errors.append(f"repos[{i}].depends_on: unknown repo {dep!r}")
    if _has_cycle(repos):
        errors.append("repos: depends_on cycle detected")

    for i, s in enumerate(data.get("submodules") or []):
        for required in ("name", "parent_repo", "path"):
            if not s.get(required):
                errors.append(f"submodules[{i}].{required}: required")
        if s.get("parent_repo") and s["parent_repo"] not in repo_names:
            errors.append(
                f"submodules[{i}].parent_repo: unknown repo {s['parent_repo']!r}"
            )
        if "pointer_policy" in s:
            _check_enum(
                s["pointer_policy"], POINTER_POLICY_VALUES,
                f"submodules[{i}].pointer_policy", errors,
            )

    obs = (data.get("integrations") or {}).get("observability") or {}
    for i, e in enumerate(obs.get("error_trace_ladder") or []):
        for required in ("layer", "kind"):
            if not e.get(required):
                errors.append(f"integrations.observability.error_trace_ladder[{i}].{required}: required")
        if "kind" in e and e.get("kind"):
            _check_enum(
                e["kind"], TRACE_KIND_VALUES,
                f"integrations.observability.error_trace_ladder[{i}].kind", errors,
            )

    policies = (data.get("conventions") or {}).get("policies") or {}
    if "tdd" in policies:
        _check_enum(policies["tdd"], TDD_VALUES, "conventions.policies.tdd", errors)
    if "commit_style" in policies:
        _check_enum(
            policies["commit_style"], COMMIT_STYLE_VALUES,
            "conventions.policies.commit_style", errors,
        )

    ok = not errors
    if raise_on_error and not ok:
        raise ValidationError("\n".join(errors))
    return ok, errors
