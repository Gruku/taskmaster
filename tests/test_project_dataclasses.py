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


# ---------------------------------------------------------------------------
# Task 3: Hand-written validator tests
# ---------------------------------------------------------------------------

import pytest

from project import (
    KIND_VALUES,
    PUSH_POLICY_VALUES,
    SCHEMA_VERSION,
    TDD_VALUES,
    TRACE_KIND_VALUES,
    ValidationError,
    validate_manifest_dict,
)


def _minimal() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {"name": "x", "slug": "x"},
    }


def test_validate_minimal_ok():
    ok, errs = validate_manifest_dict(_minimal())
    assert ok is True
    assert errs == []


def test_missing_schema_version():
    data = _minimal()
    del data["schema_version"]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("schema_version" in e for e in errs)


def test_unknown_schema_version_major():
    data = _minimal()
    data["schema_version"] = 99
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("schema_version" in e for e in errs)


def test_missing_meta_name_or_slug():
    for missing in ("name", "slug"):
        data = _minimal()
        del data["meta"][missing]
        ok, errs = validate_manifest_dict(data)
        assert ok is False
        assert any(missing in e for e in errs)


def test_invalid_kind_enum():
    data = _minimal()
    data["meta"]["kind"] = "spaceship"
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("kind" in e for e in errs)
    assert any(v in " ".join(errs) for v in KIND_VALUES)


def test_repo_requires_name_and_path():
    data = _minimal()
    data["repos"] = [{"name": "api"}]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("path" in e for e in errs)


def test_repo_push_policy_enum():
    data = _minimal()
    data["repos"] = [{"name": "api", "path": "./api", "push_policy": "yolo"}]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("push_policy" in e for e in errs)


def test_repo_depends_on_cycle_detected():
    data = _minimal()
    data["repos"] = [
        {"name": "a", "path": "./a", "depends_on": ["b"]},
        {"name": "b", "path": "./b", "depends_on": ["a"]},
    ]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("cycle" in e.lower() for e in errs)


def test_repo_depends_on_unknown_repo():
    data = _minimal()
    data["repos"] = [{"name": "a", "path": "./a", "depends_on": ["ghost"]}]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("ghost" in e for e in errs)


def test_submodule_orphan_parent_repo():
    data = _minimal()
    data["submodules"] = [
        {"name": "x", "parent_repo": "ghost", "path": "x"}
    ]
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("parent_repo" in e and "ghost" in e for e in errs)


def test_error_trace_kind_enum():
    data = _minimal()
    data["integrations"] = {
        "observability": {
            "error_trace_ladder": [{"layer": "ui", "kind": "smoke-signals"}]
        }
    }
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("kind" in e for e in errs)


def test_policies_tdd_enum():
    data = _minimal()
    data["conventions"] = {"policies": {"tdd": "maybe"}}
    ok, errs = validate_manifest_dict(data)
    assert ok is False
    assert any("tdd" in e for e in errs)


def test_extensions_passthrough_ok():
    data = _minimal()
    data["extensions"] = {"anything": {"goes": True}}
    ok, errs = validate_manifest_dict(data)
    assert ok is True
    assert errs == []


def test_validation_error_aggregates_messages():
    data = _minimal()
    del data["meta"]["slug"]
    data["repos"] = [{"name": "a"}]
    with pytest.raises(ValidationError) as exc:
        validate_manifest_dict(data, raise_on_error=True)
    msg = str(exc.value)
    assert "slug" in msg
    assert "path" in msg
