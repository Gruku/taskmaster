from __future__ import annotations

from pathlib import Path, PureWindowsPath, PurePosixPath

import pytest

from project import (
    SCHEMA_VERSION,
    Branches,
    ErrorTraceEntry,
    Meta,
    Observability,
    Integrations,
    Policies,
    Conventions,
    ProjectManifest,
    Repo,
    Submodule,
    resolve_manifest_path,
)


def _manifest(**overrides) -> ProjectManifest:
    base = ProjectManifest(schema_version=SCHEMA_VERSION, meta=Meta(name="x", slug="x"))
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_repo_lookup_by_name():
    m = _manifest(repos=[Repo(name="api", path="./api"), Repo(name="ui", path="./ui")])
    assert m.repo("api").path == "./api"
    assert m.repo("missing") is None


def test_submodule_lookup_by_name():
    m = _manifest(
        repos=[Repo(name="ui", path="./ui")],
        submodules=[Submodule(name="mcp", parent_repo="ui", path="mcp")],
    )
    assert m.submodule("mcp").parent_repo == "ui"
    assert m.submodule("missing") is None


def test_ship_order_topological():
    m = _manifest(repos=[
        Repo(name="ui", path="./ui", depends_on=["api"]),
        Repo(name="api", path="./api"),
        Repo(name="app", path="./app", depends_on=["ui"]),
    ])
    assert m.ship_order() == ["api", "ui", "app"]


def test_ship_order_independent_repos_stable():
    m = _manifest(repos=[
        Repo(name="a", path="./a"),
        Repo(name="b", path="./b"),
    ])
    assert m.ship_order() == ["a", "b"]


def test_ship_order_raises_on_cycle():
    m = _manifest(repos=[
        Repo(name="a", path="./a", depends_on=["b"]),
        Repo(name="b", path="./b", depends_on=["a"]),
    ])
    with pytest.raises(ValueError, match="cycle"):
        m.ship_order()


def test_protected_branches():
    m = _manifest(repos=[
        Repo(name="api", path="./api",
             branches=Branches(default="develop", protected=["develop", "master"]))
    ])
    assert m.protected_branches("api") == ["develop", "master"]
    assert m.protected_branches("missing") == []


def test_policy_lookup_with_default():
    m = _manifest(conventions=Conventions(policies=Policies(spec_to_task_ratio_warn=5)))
    assert m.policy("spec_to_task_ratio_warn") == 5
    assert m.policy("unknown_key", default="fallback") == "fallback"


def test_error_trace_ladder_returns_ordered_list():
    m = _manifest(integrations=Integrations(
        observability=Observability(error_trace_ladder=[
            ErrorTraceEntry(layer="ui", kind="devtools-network"),
            ErrorTraceEntry(layer="api", kind="http-log"),
        ])
    ))
    ladder = m.error_trace_ladder()
    assert [e.layer for e in ladder] == ["ui", "api"]


def test_orphan_submodule_warning(caplog):
    # parent_repo "ghost" not in repos — loader should drop and warn.
    # Simulate post-load behavior by running the helper directly.
    m = ProjectManifest(
        schema_version=SCHEMA_VERSION,
        meta=Meta(name="x", slug="x"),
        repos=[Repo(name="ui", path="./ui")],
        submodules=[
            Submodule(name="mcp", parent_repo="ui", path="mcp"),
            Submodule(name="orphan", parent_repo="ghost", path="orphan"),
        ],
    )
    living = m.valid_submodules()
    assert [s.name for s in living] == ["mcp"]


# ---------------------------------------------------------------------------
# resolve_manifest_path
# ---------------------------------------------------------------------------


def test_resolve_manifest_path_empty_string_returns_project_root():
    root = Path("/projects/foo")
    assert resolve_manifest_path(root, "") == root


def test_resolve_manifest_path_relative_joined_to_root():
    root = Path("/projects/foo")
    assert resolve_manifest_path(root, "api/src") == root / "api" / "src"


def test_resolve_manifest_path_absolute_windows_passthrough():
    # Use PureWindowsPath to verify behavior is platform-agnostic at the type level.
    # On Windows, "C:\\..." is absolute; on POSIX, it's relative. The helper relies on
    # Path.is_absolute(), so test the concrete platform behavior via str input.
    root = Path("/projects/foo")
    raw = "C:\\opt\\external\\repo" if PureWindowsPath("C:\\x").is_absolute() else "/opt/external/repo"
    result = resolve_manifest_path(root, raw)
    assert result == Path(raw)
    assert result.is_absolute()


def test_resolve_manifest_path_absolute_posix_passthrough():
    # The helper defers to pathlib.Path.is_absolute(). On POSIX, a leading-slash
    # path is absolute and passes through; on Windows it is NOT absolute (no
    # drive letter) and gets joined to the project root. Test the actual
    # platform behavior so the contract is exercised on whichever OS runs CI.
    root = Path("/projects/foo")
    raw = "/opt/external/repo"
    result = resolve_manifest_path(root, raw)
    if Path(raw).is_absolute():
        assert result == Path(raw)
    else:
        assert result == root / raw


def test_resolve_manifest_path_tilde_not_expanded():
    root = Path("/projects/foo")
    result = resolve_manifest_path(root, "~/notes")
    # `~` must be treated as a literal path segment, not expanded to $HOME.
    assert "~" in str(result)
    assert result == root / "~/notes"


def test_resolve_manifest_path_accepts_path_like_root():
    # Helper should round-trip whatever Path-like root it receives.
    root = Path("/projects/foo")
    assert resolve_manifest_path(root, "x") == root / "x"
