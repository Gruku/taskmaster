from __future__ import annotations

from project import (
    Branches,
    DeployTarget,
    ErrorTraceEntry,
    ExternalIntegration,
    Knowledge,
    KnowledgeLink,
    Meta,
    Policies,
    ProjectIdentity,
    ProjectManifest,
    Repo,
    Submodule,
)


def test_repo_defaults():
    r = Repo(name="api", path="./api")
    assert r.name == "api"
    assert r.path == "./api"
    assert r.description == ""
    assert r.stack == []
    assert r.depends_on == []
    assert r.branches == Branches()
    assert r.push_policy == "always-ask"


def test_branches_defaults():
    b = Branches()
    assert b.default == ""
    assert b.protected == []
    assert b.naming == ""


def test_submodule_defaults():
    s = Submodule(name="mcp-host", parent_repo="app-desktop", path="mcp-host")
    assert s.pointer_policy == "separate-chore-commit"
    assert s.upstream == ""


def test_error_trace_entry_minimal():
    e = ErrorTraceEntry(layer="ui", kind="devtools-network")
    assert e.path == ""
    assert e.url == ""
    assert e.provider == ""


def test_policies_defaults():
    p = Policies()
    assert p.tdd == "preferred"
    assert p.commit_style == "freeform"
    assert p.spec_to_task_ratio_warn == 3


def test_project_manifest_empty():
    m = ProjectManifest(schema_version=1, meta=Meta(name="x", slug="x"))
    assert m.repos == []
    assert m.submodules == []
    assert m.extensions == {}
    assert m.conventions.policies.spec_to_task_ratio_warn == 3
