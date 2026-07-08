from taskmaster import project


def _manifest(policies):
    return {
        "schema_version": 1,
        "meta": {"name": "X", "slug": "x", "kind": "app"},
        "conventions": {"policies": policies},
    }


def test_merge_targets_parses_as_ordered_list():
    data = _manifest({
        "merge_targets": [
            {"label": "develop", "branches": ["develop", "dev"]},
            {"label": "master", "branches": ["master", "main"]},
        ]
    })
    m = project._dict_to_dataclass(project.ProjectManifest, data)
    rungs = m.conventions.policies.merge_targets
    assert [r.label for r in rungs] == ["develop", "master"]   # ORDER preserved
    assert rungs[0].branches == ["develop", "dev"]


def test_merge_targets_roundtrips_through_asdict():
    data = _manifest({"merge_targets": [{"label": "master", "branches": ["main"]}]})
    m = project._dict_to_dataclass(project.ProjectManifest, data)
    out = project.manifest_to_dict(m)
    assert out["conventions"]["policies"]["merge_targets"][0]["label"] == "master"


def test_review_gate_flag_defaults_false():
    m = project._dict_to_dataclass(project.ProjectManifest, _manifest({}))
    assert m.conventions.policies.review_gate_required_for_merge is False
    assert m.conventions.policies.merge_targets == []


def test_resolved_applies_defaults_when_absent():
    m = project._dict_to_dataclass(project.ProjectManifest, _manifest({}))
    resolved = m.merge_targets_resolved()
    labels = [r["label"] for r in resolved]
    assert labels == ["develop", "stage", "master"]


def test_resolved_uses_explicit_when_present():
    m = project._dict_to_dataclass(project.ProjectManifest,
                                   _manifest({"merge_targets": [{"label": "master", "branches": ["main"]}]}))
    assert [r["label"] for r in m.merge_targets_resolved()] == ["master"]


def test_validation_rejects_rung_without_label():
    ok, errors = project.validate_manifest_dict(
        _manifest({"merge_targets": [{"branches": ["main"]}]}))
    assert not ok
    assert any("label" in e for e in errors)
