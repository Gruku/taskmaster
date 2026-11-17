"""`.taskmaster/project.yaml` — Project manifest loader.

Spec: docs/superpowers/specs/2026-05-17-taskmaster-project-yaml-design.md
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path

PROJECT_YAML_RELATIVE = Path(".taskmaster") / "project.yaml"
TEAM_LOCAL_RELATIVE = Path(".taskmaster") / "local" / "team.yaml"


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
class MergeRung:
    label: str
    branches: list[str] = field(default_factory=list)


@dataclass
class Policies:
    tdd: str = _DEFAULT_TDD
    commit_style: str = _DEFAULT_COMMIT_STYLE
    spec_to_task_ratio_warn: int = _DEFAULT_SPEC_RATIO_WARN
    review_gate_required_for_merge: bool = False
    merge_targets: list[MergeRung] = field(default_factory=list)


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


TEAM_SYNC_MODES = ("off", "manual", "auto")
DEFAULT_TEAM_PROTOCOL = 1
SUPPORTED_TEAM_PROTOCOLS: tuple[int, ...] = (1,)   # protocols this build can sync
DEFAULT_TEAM_REF_ROOTS = ("docs/superpowers",)


@dataclass
class TeamSync:
    mode: str = "off"
    remote: str = "origin"
    state_branch: str = "taskmaster-state"
    debounce_s: int = 30
    claim_wait_s: int = 5
    linger_s: int = 120

    def __post_init__(self) -> None:
        # YAML 1.1 reads an unquoted `mode: off` (the spec's own example) as False.
        if self.mode is False:
            self.mode = "off"


@dataclass
class TeamPlan:
    fade_days: int = 7


@dataclass
class TeamConfig:
    protocol: int = DEFAULT_TEAM_PROTOCOL
    sync: TeamSync = field(default_factory=TeamSync)
    plan: TeamPlan = field(default_factory=TeamPlan)
    linear_clone: str = ""
    ref_roots: list[str] = field(default_factory=lambda: list(DEFAULT_TEAM_REF_ROOTS))


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
    team: TeamConfig = field(default_factory=TeamConfig)
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

    def merge_targets_resolved(self) -> list[dict]:
        """Ordered merge ladder as plain dicts. Explicit config wins; else built-in defaults."""
        rungs = self.conventions.policies.merge_targets
        if rungs:
            return [{"label": r.label, "branches": list(r.branches)} for r in rungs]
        return [dict(d) for d in DEFAULT_MERGE_TARGETS]

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

DEFAULT_MERGE_TARGETS = (
    {"label": "develop", "branches": ["develop", "dev"]},
    {"label": "stage",   "branches": ["stage", "staging"]},
    {"label": "master",  "branches": ["master", "main"]},
)
MERGE_GATE_FRESHNESS_VALUES = ("strict", "any")


class ValidationError(ValueError):
    """Raised when manifest validation fails in strict mode."""


def _check_enum(value: Any, allowed: tuple[str, ...], field_name: str, errors: list[str]) -> None:
    if value not in allowed:
        errors.append(f"{field_name}: {value!r} is not one of {list(allowed)}")


_TEAM_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")  # always used with fullmatch
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _is_int(value: Any) -> bool:
    return type(value) is int


def _team_mode_error(value: Any, path: str) -> str | None:
    """Error for a sync.mode value (False is an unquoted YAML `off`), else None."""
    if value is False or (isinstance(value, str) and value in TEAM_SYNC_MODES):
        return None
    if value is True:
        return f'{path}: an unquoted on/yes/true reads as a YAML boolean; write "manual" or "auto"'
    return f"{path}: {value!r} is not one of {list(TEAM_SYNC_MODES)}"


def _has_control(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def _valid_remote(value: Any) -> bool:
    return isinstance(value, str) and not _has_control(value) and _TEAM_NAME_RE.fullmatch(value) is not None


def _valid_state_branch(value: Any) -> bool:
    """A pure-Python subset of `git check-ref-format`, applied per path component."""
    if not _valid_remote(value) or value == "HEAD" or ".." in value or "@{" in value:
        return False
    for part in value.split("/"):
        if not part or part.startswith(".") or part.endswith((".", ".lock")) or part == "@":
            return False
    return True


#: Public names for the remote and branch checks (team-server-013 validates its operator flags with them).
valid_remote_name = _valid_remote
valid_branch_name = _valid_state_branch


def _valid_ref_root(value: Any) -> bool:
    if not isinstance(value, str) or not value or _has_control(value):
        return False
    if value.startswith("/") or "\\" in value or _DRIVE_RE.match(value):
        return False
    return all(part not in ("", ".", "..") and not part.startswith("-") for part in value.split("/"))


def team_block_errors(document: Mapping) -> list[str]:
    """Validate document['team'] (spec §11); absent keys are fine, unknown keys are kept."""
    team = document.get("team")
    if team is None:
        return []
    if not isinstance(team, Mapping):
        return ["team: must be a mapping"]
    errors: list[str] = []
    if "protocol" in team and not (_is_int(team["protocol"]) and team["protocol"] >= 1):
        errors.append("team.protocol: must be an integer >= 1")
    sync = team.get("sync")
    if sync is not None and not isinstance(sync, Mapping):
        errors.append("team.sync: must be a mapping")
    elif sync:
        if "mode" in sync:
            err = _team_mode_error(sync["mode"], "team.sync.mode")
            if err:
                errors.append(err)
        if "remote" in sync and not _valid_remote(sync["remote"]):
            errors.append(
                "team.sync.remote: must be a git remote name (letters, digits, . _ / -; not starting with -)")
        if "state_branch" in sync and not _valid_state_branch(sync["state_branch"]):
            errors.append("team.sync.state_branch: must be a valid branch name")
        for key, low, high, text in (
            ("debounce_s", 1, 3600, "an integer from 1 to 3600"),
            ("claim_wait_s", 0, 30, "an integer from 0 to 30"),
            ("linger_s", 0, 86400, "an integer from 0 to 86400"),
        ):
            if key in sync:
                v = sync[key]
                if not (_is_int(v) and v >= low and (high is None or v <= high)):
                    errors.append(f"team.sync.{key}: must be {text}")
    plan = team.get("plan")
    if plan is not None and not isinstance(plan, Mapping):
        errors.append("team.plan: must be a mapping")
    elif plan and "fade_days" in plan:
        v = plan["fade_days"]
        if not (_is_int(v) and v >= 1):
            errors.append("team.plan.fade_days: must be an integer >= 1")
    if "linear_clone" in team:
        v = team["linear_clone"]
        if not (isinstance(v, str) and len(v) <= 128
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in v)):
            errors.append("team.linear_clone: must be a clone id (no spaces), or empty")
    if "ref_roots" in team:
        roots = team["ref_roots"]
        if not isinstance(roots, list):
            errors.append("team.ref_roots: must be a list of relative paths")
        else:
            for i, item in enumerate(roots):
                if not _valid_ref_root(item):
                    errors.append(f"team.ref_roots[{i}]: must be a relative path inside the repository")
    return errors


def team_protocol_supported(protocol: int) -> bool:
    return protocol in SUPPORTED_TEAM_PROTOCOLS


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


MERGE_LABEL_RE = re.compile(r"[A-Za-z0-9._/][A-Za-z0-9._/-]{0,63}")


def invalid_merge_labels(data) -> list[str]:
    """Merge-ladder labels that are not branch-like (027 D8). A write-path rule, never a read one: a read-side
    failure would hide a whole existing manifest. Labels become `merge_status` keys and hook/view text, so they
    must hold no whitespace, quotes or tags."""
    policies = ((data or {}).get("conventions") or {}).get("policies") or {}
    bad = []
    for rung in policies.get("merge_targets") or []:
        label = rung.get("label") if isinstance(rung, dict) else None
        if isinstance(label, str) and label and not MERGE_LABEL_RE.fullmatch(label):
            bad.append(label)
    return bad


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
    elif isinstance(sv, bool) or not isinstance(sv, int) or sv != SCHEMA_VERSION:
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
            if name in repo_names:
                errors.append(f"repos[{i}].name: duplicate repo name {name!r}")
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
    for i, rung in enumerate(policies.get("merge_targets") or []):
        if not isinstance(rung, dict) or not rung.get("label"):
            errors.append(f"conventions.policies.merge_targets[{i}].label: required")
        elif not isinstance(rung.get("branches", []), list):
            errors.append(f"conventions.policies.merge_targets[{i}].branches: must be a list")
    if "review_gate_required_for_merge" in policies and not isinstance(
        policies["review_gate_required_for_merge"], bool
    ):
        errors.append("conventions.policies.review_gate_required_for_merge: must be a boolean")

    errors.extend(team_block_errors(data))

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

from taskmaster import yaml_io

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
        data = yaml_io.safe_load(raw_text) or {}
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


# ---------------------------------------------------------------------------
# team-sync-001: effective team config (shared block + per-clone sync.mode override)
# ---------------------------------------------------------------------------

class TeamConfigError(ValidationError):
    """Invalid team block or local/team.yaml."""


def team_config_from_document(document: Mapping | None) -> TeamConfig:
    """Validate only the team block and coerce it; None/{}/no team -> defaults."""
    if document is not None and not isinstance(document, Mapping):
        raise TeamConfigError("project document: must be a mapping")
    document = document or {}
    errors = team_block_errors(document)
    if errors:
        raise TeamConfigError("\n".join(errors))
    return _dict_to_dataclass(TeamConfig, document.get("team") or {})


def local_team_mode(root: Path) -> str | None:
    """The .taskmaster/local/team.yaml sync.mode override, or None. Raises TeamConfigError."""
    path = Path(root) / TEAM_LOCAL_RELATIVE
    if not path.is_file():
        return None
    try:
        data = yaml_io.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TeamConfigError(f"local/team.yaml: cannot be read: {exc}") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TeamConfigError("local/team.yaml: must be a mapping; only sync.mode can be set per clone")
    found = [str(k) for k in data if k != "sync"]
    sync = data.get("sync")
    if sync is not None and not isinstance(sync, dict):
        raise TeamConfigError("local/team.yaml: sync must be a mapping; only sync.mode can be set per clone")
    found += [f"sync.{k}" for k in (sync or {}) if k != "mode"]
    if found:
        raise TeamConfigError(
            f"local/team.yaml: only sync.mode can be set per clone (found: {', '.join(found)})")
    if not sync or "mode" not in sync:
        return None
    err = _team_mode_error(sync["mode"], "sync.mode")
    if err:
        raise TeamConfigError(f"local/team.yaml: {err}")
    return "off" if sync["mode"] is False else sync["mode"]


def load_team_config(root: Path, *, project_doc: Mapping | None = None) -> TeamConfig:
    """Effective team config: project_doc (else project.yaml) plus the local mode override.

    The override applies only when the shared document has a `team:` mapping. Raises TeamConfigError.
    """
    if project_doc is None:
        path = project_yaml_path(Path(root))
        if path.is_file():
            try:
                project_doc = yaml_io.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise TeamConfigError(f"{PROJECT_YAML_RELATIVE.as_posix()}: cannot be read: {exc}") from exc
            if project_doc is not None and not isinstance(project_doc, dict):
                raise TeamConfigError(
                    f"{PROJECT_YAML_RELATIVE.as_posix()}: top-level value is not a mapping")
    cfg = team_config_from_document(project_doc)
    if project_doc and isinstance(project_doc.get("team"), Mapping):
        override = local_team_mode(Path(root))
        if override is not None:
            cfg.sync.mode = override
    return cfg


def team_mode_enabled(root: Path, *, project_doc: Mapping | None = None) -> bool:
    """True when the effective sync mode is manual/auto. Never raises: any error logs a WARNING -> False."""
    try:
        return load_team_config(root, project_doc=project_doc).sync.mode in ("manual", "auto")
    except Exception as exc:  # fail closed: "off" is the safe failure
        _log.warning("team sync disabled: %s", exc)
        return False


# ---------------------------------------------------------------------------
# team-sync-001: team.protocol guard (a change needs an explicit upgrade)
# ---------------------------------------------------------------------------

class TeamProtocolError(ValueError):
    """A write would change team.protocol outside an explicit upgrade."""


def effective_team_protocol(document: Mapping | None) -> int:
    """document['team']['protocol'] when a valid int, else the default (1)."""
    team = (document or {}).get("team")
    if isinstance(team, Mapping):
        value = team.get("protocol")
        if _is_int(value) and value >= 1:
            return value
    return DEFAULT_TEAM_PROTOCOL


def _without_team_protocol(document: Mapping) -> str:
    doc = copy.deepcopy(dict(document))
    doc.pop("id", None)  # a stored row carries its own id, which is not part of the manifest
    team = doc.get("team")
    if isinstance(team, dict):
        team.pop("protocol", None)
        if not team:
            del doc["team"]
    return json.dumps(doc, sort_keys=True, default=str)


def check_team_protocol_change(before: Mapping | None, after: Mapping, *, upgrade: bool = False) -> None:
    """Refuse any change of the effective protocol unless it is an explicit, supported upgrade."""
    old, new = effective_team_protocol(before), effective_team_protocol(after)
    if not upgrade:
        if old != new:
            raise TeamProtocolError(
                f"team.protocol changes only through 'init-team upgrade' (stored {old}, requested {new}); "
                f"keep protocol: {old}")
        return
    if before is None:
        raise TeamProtocolError("a team.protocol upgrade needs an existing project manifest")
    if new <= old:
        raise TeamProtocolError(
            f"team.protocol upgrade must raise the protocol (stored {old}, requested {new})")
    if new not in SUPPORTED_TEAM_PROTOCOLS:
        raise TeamProtocolError(
            f"team.protocol {new} is not supported by this taskmaster build "
            f"(supports {list(SUPPORTED_TEAM_PROTOCOLS)}); install a newer taskmaster before upgrading")
    if _without_team_protocol(before) != _without_team_protocol(after):
        raise TeamProtocolError(
            "a team.protocol upgrade changes only team.protocol; "
            "apply other edits with a normal project.set first")


def with_team_protocol(document: Mapping, protocol: int) -> dict:
    """Deep copy of document with team.protocol set (creates team: if absent)."""
    doc = copy.deepcopy(dict(document))
    if not isinstance(doc.get("team"), dict):
        doc["team"] = {}
    doc["team"]["protocol"] = protocol
    return doc


def team_import_refusal(current: Mapping | None, authored: Mapping) -> str | None:
    """Reason to quarantine a hand-edited project.yaml (invalid team block or protocol change), else None."""
    errors = team_block_errors(authored)
    if errors:
        return "; ".join(errors)
    try:
        check_team_protocol_change(current, authored)
    except TeamProtocolError as exc:
        return str(exc)
    return None
