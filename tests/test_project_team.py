"""User intent: the project.yaml `team:` block (spec §11) has one typed, validated home with safe defaults
(sync off); .taskmaster/local/team.yaml may override only sync.mode; team.protocol changes only through an explicit
upgrade. Every later team-sync task reads its settings through this block (team-sync-001).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from taskmaster.project import (
    TeamConfig,
    ValidationError,
    load_project_manifest,
    load_project_manifest_raw,
    manifest_to_dict,
    validate_manifest_dict,
)

BASE = {"schema_version": 1, "meta": {"name": "Demo", "slug": "demo"}}


def write_project(root: Path, doc: dict | None = None, *, text: str | None = None) -> Path:
    d = root / ".taskmaster"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "project.yaml"
    p.write_text(text if text is not None else yaml.safe_dump(doc), encoding="utf-8")
    return p


def with_team(team) -> dict:
    return {**copy.deepcopy(BASE), "team": team}


FULL_TEAM = {
    "protocol": 1,
    "sync": {
        "mode": "manual",
        "remote": "upstream",
        "state_branch": "team/state",
        "debounce_s": 10,
        "claim_wait_s": 2,
        "linger_s": 60,
    },
    "plan": {"fade_days": 14},
    "linear_clone": "clone-abc",
    "ref_roots": ["docs/a", "docs/b"],
}


# ---------------------------------------------------------------------------
# Task 1: dataclasses and defaults
# ---------------------------------------------------------------------------

def test_team_defaults_match_spec_section_11():
    t = TeamConfig()
    assert t.protocol == 1
    s = t.sync
    assert (s.mode, s.remote, s.state_branch, s.debounce_s, s.claim_wait_s, s.linger_s) == (
        "off", "origin", "taskmaster-state", 30, 5, 120)
    assert t.plan.fade_days == 7
    assert t.linear_clone == ""
    assert t.ref_roots == ["docs/superpowers"]
    other = TeamConfig()
    other.ref_roots.append("x")
    assert t.ref_roots == ["docs/superpowers"]


def test_manifest_without_team_block_loads_with_sync_off(tmp_path):
    write_project(tmp_path, BASE)
    m = load_project_manifest(tmp_path)
    assert m.team == TeamConfig()
    assert m.team.sync.mode == "off"
    assert "team" not in load_project_manifest_raw(tmp_path)


SCAFFOLD = {
    "schema_version": 1,
    "meta": {"name": "n", "slug": "n", "kind": "app"},
    "project": {"description": "", "goal": "", "owners": [], "tags": []},
    "repos": [],
    "submodules": [],
    "integrations": {"observability": {"error_trace_ladder": []}, "external": []},
    "conventions": {"narrative_ref": "./CLAUDE.md", "policies": {}},
    "extensions": {},
}
OVERVIEW_MANIFEST = {
    "schema_version": 1,
    "meta": {"name": "Demo", "slug": "demo", "kind": "app"},
    "repos": [{"name": "api", "path": "api", "depends_on": ["web"]}, {"name": "web", "path": "web"}],
    "integrations": {"observability": {"error_trace_ladder": [{"layer": "browser", "kind": "console"}]}},
    "conventions": {"policies": {"merge_targets": [{"label": "develop", "branches": ["dev"]}]}},
}
FULL_SAMPLE = {
    "schema_version": 1,
    "meta": {"name": "cm", "slug": "cm", "kind": "app"},
    "project": {"goal": "ship it", "owners": ["gruku"]},
    "repos": [
        {"name": "api", "path": "./api", "branches": {"default": "develop"}},
        {"name": "ui", "path": "./ui", "depends_on": ["api"]},
    ],
    "submodules": [{"name": "mcp", "parent_repo": "ui", "path": "mcp"}],
    "extensions": {"foo": "bar"},
}


@pytest.mark.parametrize("doc", [SCAFFOLD, OVERVIEW_MANIFEST, FULL_SAMPLE], ids=["scaffold", "overview", "full"])
def test_existing_manifests_still_validate_and_load(tmp_path, doc):
    doc = copy.deepcopy(doc)
    assert validate_manifest_dict(doc) == (True, [])
    write_project(tmp_path, doc)
    m = load_project_manifest(tmp_path)
    assert m.meta.name == doc["meta"]["name"]
    assert [r.name for r in m.repos] == [r["name"] for r in doc.get("repos", [])]
    assert [s.name for s in m.submodules] == [s["name"] for s in doc.get("submodules", [])]
    want = doc.get("conventions", {}).get("policies", {}).get("merge_targets", [])
    assert [(r.label, r.branches) for r in m.conventions.policies.merge_targets] == [
        (r["label"], r["branches"]) for r in want] or not want
    assert load_project_manifest_raw(tmp_path) == doc
    assert m.team == TeamConfig()


def test_full_team_block_round_trips(tmp_path):
    write_project(tmp_path, with_team(FULL_TEAM))
    m = load_project_manifest(tmp_path)
    t = m.team
    assert t.protocol == 1
    assert (t.sync.mode, t.sync.remote, t.sync.state_branch) == ("manual", "upstream", "team/state")
    assert (t.sync.debounce_s, t.sync.claim_wait_s, t.sync.linger_s) == (10, 2, 60)
    assert t.plan.fade_days == 14
    assert t.linear_clone == "clone-abc"
    assert t.ref_roots == ["docs/a", "docs/b"]
    assert manifest_to_dict(m)["team"] == FULL_TEAM


def test_partial_team_block_fills_defaults(tmp_path):
    write_project(tmp_path, with_team({"sync": {"mode": "manual"}}))
    t = load_project_manifest(tmp_path).team
    expected = TeamConfig()
    expected.sync.mode = "manual"
    assert t == expected


@pytest.mark.parametrize("team", [None, {"sync": None}, {"plan": None}])
def test_null_sections_take_defaults(tmp_path, team):
    doc = with_team(team)
    assert validate_manifest_dict(doc) == (True, [])
    write_project(tmp_path, doc)
    assert load_project_manifest(tmp_path).team == TeamConfig()


def test_unquoted_off_reads_as_off(tmp_path):
    text = "schema_version: 1\nmeta:\n  name: Demo\n  slug: demo\nteam:\n  sync:\n    mode: off\n"
    write_project(tmp_path, text=text)
    m = load_project_manifest(tmp_path)
    assert m is not None
    assert m.team.sync.mode == "off"


def test_unknown_team_keys_are_preserved(tmp_path):
    doc = with_team({"future": 1, "sync": {"mode": "auto", "later": "x"}})
    assert validate_manifest_dict(doc) == (True, [])
    write_project(tmp_path, doc)
    raw = load_project_manifest_raw(tmp_path)
    assert raw["team"]["future"] == 1
    assert raw["team"]["sync"]["later"] == "x"
    assert load_project_manifest(tmp_path).team.sync.mode == "auto"


# ---------------------------------------------------------------------------
# Task 2: validation
# ---------------------------------------------------------------------------

MODE_ENUM = "team.sync.mode: 'sometimes' is not one of"
BRANCH_MSG = "team.sync.state_branch: must be a valid branch name"
REMOTE_MSG = "team.sync.remote: must be a git remote name"
REF_MSG = "team.ref_roots[0]: must be a relative path inside the repository"

INVALID_TEAMS = [
    (42, "team: must be a mapping"),
    ({"protocol": 0}, "team.protocol: must be an integer >= 1"),
    ({"protocol": "1"}, "team.protocol: must be an integer >= 1"),
    ({"protocol": True}, "team.protocol: must be an integer >= 1"),
    ({"protocol": 1.0}, "team.protocol: must be an integer >= 1"),
    ({"sync": []}, "team.sync: must be a mapping"),
    ({"plan": []}, "team.plan: must be a mapping"),
    ({"sync": {"mode": "sometimes"}}, MODE_ENUM),
    ({"sync": {"mode": True}}, 'team.sync.mode: an unquoted on/yes/true reads as a YAML boolean'),
    ({"sync": {"mode": None}}, "team.sync.mode: None is not one of"),
    ({"sync": {"remote": "-oProxyCommand=x"}}, REMOTE_MSG),
    ({"sync": {"remote": "git@host:r.git"}}, REMOTE_MSG),
    ({"sync": {"remote": ""}}, REMOTE_MSG),
    ({"sync": {"state_branch": "a..b"}}, BRANCH_MSG),
    ({"sync": {"state_branch": "x.lock"}}, BRANCH_MSG),
    ({"sync": {"state_branch": "-x"}}, BRANCH_MSG),
    ({"sync": {"state_branch": "a/"}}, BRANCH_MSG),
    ({"sync": {"state_branch": ".hidden/x"}}, BRANCH_MSG),
    ({"sync": {"state_branch": "HEAD"}}, BRANCH_MSG),
    ({"sync": {"state_branch": "a b"}}, BRANCH_MSG),
    ({"sync": {"debounce_s": 0}}, "team.sync.debounce_s: must be an integer from 1 to 3600"),
    ({"sync": {"debounce_s": 2.5}}, "team.sync.debounce_s: must be an integer from 1 to 3600"),
    ({"sync": {"debounce_s": False}}, "team.sync.debounce_s: must be an integer from 1 to 3600"),
    ({"sync": {"claim_wait_s": -1}}, "team.sync.claim_wait_s: must be an integer from 0 to 30"),
    ({"sync": {"claim_wait_s": 31}}, "team.sync.claim_wait_s: must be an integer from 0 to 30"),
    ({"sync": {"linger_s": -1}}, "team.sync.linger_s: must be an integer from 0 to 86400"),
    ({"plan": {"fade_days": 0}}, "team.plan.fade_days: must be an integer >= 1"),
    ({"linear_clone": "a b"}, "team.linear_clone: must be a clone id"),
    ({"linear_clone": 5}, "team.linear_clone: must be a clone id"),
    ({"ref_roots": None}, "team.ref_roots: must be a list of relative paths"),
    ({"ref_roots": "docs"}, "team.ref_roots: must be a list of relative paths"),
    ({"ref_roots": ["/etc"]}, REF_MSG),
    ({"ref_roots": ["C:/x"]}, REF_MSG),
    ({"ref_roots": ["../up"]}, REF_MSG),
    ({"ref_roots": ["docs\\x"]}, REF_MSG),
    ({"ref_roots": [""]}, REF_MSG),
]


@pytest.mark.parametrize("team,prefix", INVALID_TEAMS, ids=[str(i) for i in range(len(INVALID_TEAMS))])
def test_invalid_team_values_are_rejected_with_the_field_path(tmp_path, team, prefix):
    doc = with_team(team)
    ok, errors = validate_manifest_dict(doc)
    assert ok is False
    assert len(errors) == 1 and errors[0].startswith(prefix), errors
    with pytest.raises(ValidationError):
        validate_manifest_dict(doc, raise_on_error=True)
    write_project(tmp_path, doc)
    assert load_project_manifest(tmp_path) is None


def test_valid_edge_values_pass():
    for team in (
        {"sync": {"claim_wait_s": 0}},
        {"sync": {"claim_wait_s": 30}},
        {"sync": {"linger_s": 0}},
        {"ref_roots": []},
        {"sync": {"state_branch": "team/state"}},
        {"sync": {"remote": "upstream"}},
    ):
        assert validate_manifest_dict(with_team(team)) == (True, []), team


def test_protocol_above_supported_still_validates(tmp_path):
    from taskmaster.project import team_protocol_supported

    doc = with_team({"protocol": 2})
    assert validate_manifest_dict(doc) == (True, [])
    write_project(tmp_path, doc)
    assert load_project_manifest(tmp_path).team.protocol == 2
    assert team_protocol_supported(2) is False
    assert team_protocol_supported(1) is True


# ---------------------------------------------------------------------------
# Task 3: effective config, local override, team_mode_enabled
# ---------------------------------------------------------------------------

import logging  # noqa: E402

from taskmaster.project import (  # noqa: E402
    TeamConfigError,
    load_team_config,
    team_mode_enabled,
)


def write_local(root: Path, text: str) -> Path:
    d = root / ".taskmaster" / "local"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "team.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_local_override_changes_only_mode(tmp_path):
    write_project(tmp_path, with_team({"sync": {"mode": "auto", "debounce_s": 9}}))
    write_local(tmp_path, "sync:\n  mode: manual\n")
    cfg = load_team_config(tmp_path)
    assert cfg.sync.mode == "manual"
    assert cfg.sync.debounce_s == 9


@pytest.mark.parametrize("text", [
    "remote: x\n",
    "sync:\n  mode: auto\n  remote: x\n",
    "- 1\n",
    "auto\n",
])
def test_local_override_rejects_other_keys(tmp_path, text):
    write_project(tmp_path, with_team({"sync": {"mode": "auto"}}))
    write_local(tmp_path, text)
    with pytest.raises(TeamConfigError) as exc:
        load_team_config(tmp_path)
    assert str(exc.value).startswith("local/team.yaml:")


def test_local_override_invalid_mode_is_a_clear_error(tmp_path):
    write_project(tmp_path, with_team({"sync": {"mode": "auto"}}))
    write_local(tmp_path, "sync:\n  mode: sometimes\n")
    with pytest.raises(TeamConfigError) as exc:
        load_team_config(tmp_path)
    assert "sync.mode: 'sometimes' is not one of" in str(exc.value)


@pytest.mark.parametrize("text", [None, "", "{}\n", "sync: {}\n", "sync: null\n"])
def test_local_override_absent_or_empty_means_no_override(tmp_path, text):
    write_project(tmp_path, with_team({"sync": {"mode": "auto"}}))
    if text is not None:
        write_local(tmp_path, text)
    assert load_team_config(tmp_path).sync.mode == "auto"


def test_local_override_ignored_without_team_block(tmp_path):
    write_project(tmp_path, BASE)
    write_local(tmp_path, "sync:\n  mode: auto\n")
    assert team_mode_enabled(tmp_path) is False


@pytest.mark.parametrize("shared", [None, "off", "manual", "auto"])
@pytest.mark.parametrize("local", [None, "off", "manual", "auto"])
def test_team_mode_enabled_truth_table(tmp_path, shared, local):
    write_project(tmp_path, BASE if shared is None else with_team({"sync": {"mode": shared}}))
    if local is not None:
        write_local(tmp_path, f"sync:\n  mode: '{local}'\n")
    if shared is None:
        expected = False
    else:
        expected = (local or shared) in ("manual", "auto")
    assert team_mode_enabled(tmp_path) is expected


@pytest.mark.parametrize("case", ["bad_local", "bad_team", "not_yaml", "list_top"])
def test_team_mode_enabled_fails_closed(tmp_path, caplog, case):
    if case == "bad_local":
        write_project(tmp_path, with_team({"sync": {"mode": "auto"}}))
        write_local(tmp_path, "remote: x\n")
    elif case == "bad_team":
        write_project(tmp_path, with_team({"sync": {"mode": "sometimes"}}))
    elif case == "not_yaml":
        write_project(tmp_path, text="not: valid: yaml: [\n")
    else:
        write_project(tmp_path, text="- a\n- b\n")
    with caplog.at_level(logging.WARNING):
        assert team_mode_enabled(tmp_path) is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    if case in ("bad_local", "bad_team"):
        with pytest.raises(TeamConfigError):
            load_team_config(tmp_path)
        assert ("local/team.yaml" if case == "bad_local" else "team.sync.mode") in warnings[0].getMessage()


def test_unrelated_manifest_error_does_not_disable_team_mode(tmp_path):
    doc = {**with_team({"sync": {"mode": "auto"}}), "repos": [{"name": "a"}]}
    write_project(tmp_path, doc)
    assert team_mode_enabled(tmp_path) is True
    assert load_project_manifest(tmp_path) is None


def test_project_doc_wins_over_the_file(tmp_path):
    p = write_project(tmp_path, with_team({"sync": {"mode": "auto"}}))
    off_doc = with_team({"sync": {"mode": "off"}})
    assert team_mode_enabled(tmp_path, project_doc=off_doc) is False
    p.unlink()
    assert team_mode_enabled(tmp_path, project_doc=with_team({"sync": {"mode": "auto"}})) is True


def test_no_project_yaml_means_off(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    assert team_mode_enabled(tmp_path) is False
    assert load_team_config(tmp_path) == TeamConfig()


# ---------------------------------------------------------------------------
# Task 4: protocol guard helpers (pure)
# ---------------------------------------------------------------------------

from taskmaster.project import (  # noqa: E402
    TeamProtocolError,
    check_team_protocol_change,
    effective_team_protocol,
    team_import_refusal,
    with_team_protocol,
)


@pytest.mark.parametrize("doc,want", [
    (None, 1), ({}, 1), (BASE, 1), ({"team": {}}, 1), ({"team": {"protocol": "x"}}, 1),
    ({"team": {"protocol": 3}}, 3),
])
def test_effective_protocol_defaults_to_one(doc, want):
    assert effective_team_protocol(doc) == want


def _p(n):
    return {**BASE, "team": {"protocol": n}} if n else dict(BASE)


@pytest.mark.parametrize("before,after,old,new", [
    (_p(1), _p(2), 1, 2),
    (_p(None), _p(2), 1, 2),
    (_p(2), _p(None), 2, 1),
    (_p(2), {**BASE, "team": {"sync": {"mode": "off"}}}, 2, 1),
])
def test_protocol_change_without_upgrade_is_refused(before, after, old, new):
    with pytest.raises(TeamProtocolError) as exc:
        check_team_protocol_change(before, after)
    assert str(exc.value).startswith(
        f"team.protocol changes only through 'init-team upgrade' (stored {old}, requested {new})")


def test_unchanged_protocol_passes():
    check_team_protocol_change(_p(None), _p(None))
    check_team_protocol_change(_p(None), _p(1))
    check_team_protocol_change(_p(1), {**BASE, "team": {"protocol": 1, "sync": {"mode": "auto"}}})
    check_team_protocol_change(None, dict(BASE))


@pytest.fixture()
def supports_two(monkeypatch):
    monkeypatch.setattr("taskmaster.project.SUPPORTED_TEAM_PROTOCOLS", (1, 2))


def test_upgrade_raises_to_a_supported_protocol(supports_two):
    stored = _p(1)
    check_team_protocol_change(stored, with_team_protocol(stored, 2), upgrade=True)


def test_upgrade_refusals(supports_two):
    with pytest.raises(TeamProtocolError, match="must raise the protocol"):
        check_team_protocol_change(_p(2), _p(1), upgrade=True)
    with pytest.raises(TeamProtocolError, match="is not supported by this taskmaster build"):
        check_team_protocol_change(_p(1), _p(3), upgrade=True)
    with pytest.raises(TeamProtocolError, match="needs an existing project manifest"):
        check_team_protocol_change(None, _p(2), upgrade=True)
    stored = _p(1)
    changed = with_team_protocol(stored, 2)
    changed["meta"]["name"] = "Other"
    with pytest.raises(TeamProtocolError, match="changes only team.protocol"):
        check_team_protocol_change(stored, changed, upgrade=True)
    bare = dict(BASE)
    check_team_protocol_change(bare, with_team_protocol(bare, 2), upgrade=True)


def test_with_team_protocol_does_not_mutate_input():
    doc = copy.deepcopy(BASE)
    out = with_team_protocol(doc, 2)
    assert doc == BASE
    assert out["team"]["protocol"] == 2
    nested = {**BASE, "team": {"sync": {"mode": "auto"}}}
    snap = copy.deepcopy(nested)
    out = with_team_protocol(nested, 2)
    assert nested == snap
    assert out["team"] == {"sync": {"mode": "auto"}, "protocol": 2}


def test_team_import_refusal():
    cur = _p(1)
    assert "init-team upgrade" in team_import_refusal(cur, _p(2))
    assert "team.sync.mode" in team_import_refusal(cur, {**cur, "team": {"sync": {"mode": "sometimes"}}})
    assert team_import_refusal(cur, {**cur, "team": {"sync": {"mode": "manual"}}}) is None


# ---------------------------------------------------------------------------
# Review-gate fixes: validators are a security boundary (values reach git argv)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sync", [
    {"remote": "origin\n"}, {"remote": "ori\x00gin"}, {"remote": "a\tb"}, {"remote": "a\x7f"},
    {"state_branch": "x\n"}, {"state_branch": "a/b\n"}, {"state_branch": "a\x1bb"},
    {"state_branch": "a.lock/b"}, {"state_branch": "a/.b"}, {"state_branch": "a/../b"},
    {"state_branch": "a@{b"}, {"state_branch": "a//b"}, {"state_branch": "a/b."},
    {"state_branch": "a/b.lock"}, {"state_branch": "@"},
    {"debounce_s": 3601}, {"linger_s": 86401},
])
def test_shared_git_values_reject_newlines_and_ref_format_violations(sync):
    ok, errors = validate_manifest_dict(with_team({"sync": sync}))
    assert ok is False and len(errors) == 1, errors


@pytest.mark.parametrize("sync", [{"debounce_s": 3600}, {"linger_s": 86400}, {"state_branch": "team/state-1.x"}])
def test_upper_bounds_and_branch_edges_pass(sync):
    assert validate_manifest_dict(with_team({"sync": sync})) == (True, [])


def test_cap_error_text_states_the_bounds():
    _, e1 = validate_manifest_dict(with_team({"sync": {"debounce_s": 0}}))
    _, e2 = validate_manifest_dict(with_team({"sync": {"linger_s": -1}}))
    assert e1 == ["team.sync.debounce_s: must be an integer from 1 to 3600"]
    assert e2 == ["team.sync.linger_s: must be an integer from 0 to 86400"]


@pytest.mark.parametrize("roots", [
    ["-rf"], ["--output=x"], ["docs/-x"], ["a\nb"], ["a\x00b"], ["."], ["docs/./x"], ["a//b"], ["docs/"], [" "][:0] + ["\t"],
])
def test_ref_roots_reject_option_like_and_control_and_whole_repo(roots):
    ok, errors = validate_manifest_dict(with_team({"ref_roots": roots}))
    assert ok is False and errors[0].startswith("team.ref_roots[0]: must be a relative path"), errors


def test_linear_clone_rejects_control_characters():
    for v in ("a\nb", "a\x00", "a\x7f"):
        assert validate_manifest_dict(with_team({"linear_clone": v}))[0] is False


def test_non_mapping_project_doc_raises_team_config_error(tmp_path):
    for bad in ([], [1], "text", 5):
        with pytest.raises(TeamConfigError):
            load_team_config(tmp_path, project_doc=bad)
        assert team_mode_enabled(tmp_path, project_doc=bad) is False


def test_stored_id_field_is_ignored_by_the_shared_comparison(supports_two):
    stored = {**BASE, "id": "__project__", "team": {"protocol": 1}}
    upgraded = with_team_protocol({**BASE, "team": {"protocol": 1}}, 2)
    check_team_protocol_change(stored, upgraded, upgrade=True)
    assert team_import_refusal(stored, {**BASE, "team": {"protocol": 1, "sync": {"mode": "auto"}}}) is None
    assert "init-team upgrade" in team_import_refusal(stored, upgraded)
