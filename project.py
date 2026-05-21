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


def resolve_manifest_path(project_root: Path, raw: str) -> Path:
    """Resolve a manifest-declared path against the project root.

    Rules (per spec section "Path resolution"):
      - Empty string -> returns project_root itself.
      - Absolute path -> passed through unchanged (after Path() round-trip).
      - Relative path -> joined to project_root.
      - `~` is NOT expanded -- manifest paths are deterministic per project,
        not per user. Pass through any literal `~` as a regular path segment.
    """
    project_root = Path(project_root)
    if raw == "":
        return project_root
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return project_root / candidate


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

    # ------------------------------------------------------------------
    # Task 5: Helper API methods
    # ------------------------------------------------------------------

    def repo(self, name: str) -> Repo | None:
        return next((r for r in self.repos if r.name == name), None)

    def submodule(self, name: str) -> Submodule | None:
        return next((s for s in self.submodules if s.name == name), None)

    def protected_branches(self, repo_name: str) -> list[str]:
        r = self.repo(repo_name)
        return list(r.branches.protected) if r else []

    def policy(self, key: str, default: Any = None) -> Any:
        return getattr(self.conventions.policies, key, default)

    def error_trace_ladder(self) -> list[ErrorTraceEntry]:
        return list(self.integrations.observability.error_trace_ladder)

    def ship_order(self) -> list[str]:
        """Topological sort of repos by depends_on. Raises ValueError on cycle.

        Stable for independent repos: returns them in declaration order.
        """
        order: list[str] = []
        visited: dict[str, int] = {r.name: 0 for r in self.repos}  # 0=white,1=gray,2=black
        repo_by_name = {r.name: r for r in self.repos}

        def visit(name: str) -> None:
            state = visited.get(name, 0)
            if state == 2:
                return
            if state == 1:
                raise ValueError(f"repos: depends_on cycle involving {name!r}")
            visited[name] = 1
            for dep in repo_by_name[name].depends_on:
                if dep in repo_by_name:
                    visit(dep)
            visited[name] = 2
            order.append(name)

        for r in self.repos:
            visit(r.name)
        return order

    def valid_submodules(self) -> list[Submodule]:
        """Drop submodules whose parent_repo is not in repos. Warns on drop."""
        repo_names = {r.name for r in self.repos}
        living: list[Submodule] = []
        for s in self.submodules:
            if s.parent_repo in repo_names:
                living.append(s)
            else:
                _log.warning(
                    "submodule %r has unknown parent_repo %r — dropping",
                    s.name, s.parent_repo,
                )
        return living


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


def _check_enum(value: Any, allowed: tuple[str, ...], field_name: str, errors: list[str]) -> None:
    if value not in allowed:
        errors.append(f"{field_name}: {value!r} is not one of {list(allowed)}")


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


# ---------------------------------------------------------------------------
# Task 4: load_project_manifest, load_project_manifest_or_default, manifest_to_dict
# ---------------------------------------------------------------------------

import logging
import typing
from dataclasses import asdict, fields, is_dataclass

import yaml

_log = logging.getLogger(__name__)


def _dict_to_dataclass(cls, data: Any):
    """Recursively coerce a dict into a dataclass instance, ignoring unknown keys."""
    if data is None:
        return cls() if _has_all_defaults(cls) else None
    if not is_dataclass(cls):
        return data
    kwargs: dict[str, Any] = {}
    type_hints = typing.get_type_hints(cls)
    for f in fields(cls):
        if f.name not in data:
            continue
        raw = data[f.name]
        hint = type_hints.get(f.name, f.type)
        kwargs[f.name] = _coerce_field(hint, raw)
    return cls(**kwargs)


def _coerce_field(type_hint: Any, raw: Any) -> Any:
    """Handle list[X], dict[...], dataclass, and primitive types."""
    origin = getattr(type_hint, "__origin__", None)
    if origin is list:
        (inner,) = type_hint.__args__
        if is_dataclass(inner):
            return [_dict_to_dataclass(inner, item) for item in (raw or [])]
        return list(raw or [])
    if origin is dict:
        return dict(raw or {})
    # Forward references resolve via typing.get_type_hints in practice; for our
    # closed schema, direct dataclass references work because we don't use strings.
    if is_dataclass(type_hint):
        return _dict_to_dataclass(type_hint, raw or {})
    return raw


def _has_all_defaults(cls) -> bool:
    """True if every field has a default or default_factory."""
    from dataclasses import MISSING
    return all(
        f.default is not MISSING or f.default_factory is not MISSING
        for f in fields(cls)
    )


def load_project_manifest_raw(project_root: Path) -> dict | None:
    """Soft load: returns the validated raw dict, or None if missing/malformed/invalid.

    Unlike load_project_manifest, does NOT coerce into dataclasses — so absent
    fields stay absent rather than being filled with schema defaults. Used by
    callers that need to distinguish "user did not set X" from "X is at its
    default value".
    """
    path = project_yaml_path(project_root)
    if not path.is_file():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
    except (OSError, yaml.YAMLError) as exc:
        _log.warning("Failed to read %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        _log.warning("%s: top-level value is not a mapping", path)
        return None
    ok, errors = validate_manifest_dict(data)
    if not ok:
        _log.warning("%s validation failed: %s", path, "; ".join(errors))
        return None
    return data


def load_project_manifest(project_root: Path) -> ProjectManifest | None:
    """Soft load: returns None if file missing, malformed, or invalid.

    Never raises. Validation failures and YAML errors are logged at WARNING.
    """
    data = load_project_manifest_raw(project_root)
    if data is None:
        return None
    return _dict_to_dataclass(ProjectManifest, data)


def load_project_manifest_or_default(project_root: Path) -> ProjectManifest:
    """Always returns a manifest. Missing/invalid files yield an empty one."""
    m = load_project_manifest(project_root)
    if m is not None:
        return m
    return ProjectManifest(schema_version=SCHEMA_VERSION, meta=Meta(name="", slug=""))


def manifest_to_dict(manifest: ProjectManifest) -> dict:
    """Convert a ProjectManifest back to a plain dict, suitable for YAML dump."""
    return asdict(manifest)
