from __future__ import annotations

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
