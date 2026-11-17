# User intent: a teammate's tools cannot adopt host files, probe or bootstrap Linear, or name an environment variable; the operator can, with the fixed token name; solo is unchanged.
from __future__ import annotations

import json

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster.coordinator import allowlist
from taskmaster.coordinator import client as client_module
from taskmaster.integrations.linear import client as linear_client
from native_twins import make_twins
from test_native_linear_bootstrap import _bootstrap, _config_path, _leftovers, twins  # noqa: F401
from test_native_resolve_take_file import REL, _flag, _flagged, _hand_edit, _path, _seed

MEMBER = "a" * 16


def provide(monkeypatch, value):
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", value)


@pytest.fixture
def no_linear(monkeypatch):
    built = []

    class Forbidden:
        def __init__(self, *args, **kwargs):
            built.append(kwargs)
            raise AssertionError("no LinearClient may be built for this caller")
    monkeypatch.setattr(linear_client, "LinearClient", Forbidden)
    return built


def _probe(twins_, token_env):  # noqa: F811
    with twins_.at(twins_.native):
        return json.loads(bs.backlog_linear(action="probe", token_env=token_env))


def test_a_member_cannot_probe_linear(twins, monkeypatch, no_linear):  # noqa: F811
    monkeypatch.setenv("SECRET_X", "s3cr3t")
    provide(monkeypatch, lambda: MEMBER)
    answer = _probe(twins, "SECRET_X")
    assert "backlog_linear probe is a host operator action" in answer["error"]
    assert "s3cr3t" not in json.dumps(answer) and no_linear == []


def test_a_member_cannot_bootstrap_linear(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: MEMBER)
    answer = _bootstrap(twins)
    assert "backlog_linear bootstrap_apply is a host operator action" in answer["error"]
    assert not _config_path(twins).exists() and _leftovers(twins) == []


def test_an_unknown_caller_cannot_bootstrap_linear(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: None)
    assert "host operator action" in _bootstrap(twins)["error"]
    assert not _config_path(twins).exists()


def test_the_operator_bootstraps_with_the_fixed_name(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: "operator")
    answer = _bootstrap(twins, token_env="")
    assert answer["ok"] is True, answer
    workspaces = yaml.safe_load(_config_path(twins).read_text(encoding="utf-8"))["workspaces"]
    assert [(ws["alias"], ws["token_env"]) for ws in workspaces] == [("ops", "TASKMASTER_LINEAR_TOKEN")]


def test_the_operator_cannot_name_another_variable(twins, monkeypatch, no_linear):  # noqa: F811
    provide(monkeypatch, lambda: "operator")
    assert _bootstrap(twins, token_env="OPS_LINEAR_TOKEN") == {"error": allowlist.LINEAR_TOKEN_ENV_REFUSED}
    assert not _config_path(twins).exists()
    assert _probe(twins, "AWS_SECRET_ACCESS_KEY") == {"error": allowlist.LINEAR_TOKEN_ENV_REFUSED}
    assert no_linear == []


def test_the_operator_probe_reads_the_fixed_name(twins, monkeypatch):  # noqa: F811
    built = []

    class Fake:
        def __init__(self, token=None, **kwargs):
            built.append(token)

        def list_teams(self):
            return []
    monkeypatch.setattr(linear_client, "LinearClient", Fake)
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN", "lin_test")
    monkeypatch.setenv("OTHER_TOKEN", "never-read")
    provide(monkeypatch, lambda: "operator")
    assert _probe(twins, "") == {"teams": []}
    assert built == ["lin_test"]


def test_solo_linear_is_unchanged(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, None)
    assert _bootstrap(twins)["ok"] is True
    workspaces = yaml.safe_load(_config_path(twins).read_text(encoding="utf-8"))["workspaces"]
    assert [ws["token_env"] for ws in workspaces] == ["OPS_LINEAR_TOKEN"]


@pytest.fixture
def flagged(tmp_path, monkeypatch):
    provide(monkeypatch, None)
    world = make_twins(tmp_path, monkeypatch, _seed)
    edited = _flag(world, _hand_edit)
    return world, edited


def _resolve(world, take):
    with world.at(world.native):
        return bs.backlog_resolve_conflict(file=REL, take=take)


def test_a_member_cannot_take_a_flagged_file(flagged, monkeypatch):
    world, edited = flagged
    provide(monkeypatch, lambda: MEMBER)
    answer = _resolve(world, "file")
    assert answer.startswith("Error:"), answer
    assert "adopting a flagged file from the host's disk" in answer
    assert 'take="store") is available to everyone' in answer
    assert _flagged(world) == [REL] and _path(world).read_bytes() == edited


def test_a_member_can_take_the_store_version(flagged, monkeypatch):
    world, _ = flagged
    provide(monkeypatch, lambda: MEMBER)
    assert "kept the store version" in _resolve(world, "store")
    assert _flagged(world) == []


def test_the_operator_takes_a_flagged_file(flagged, monkeypatch):
    world, _ = flagged
    provide(monkeypatch, lambda: "operator")
    assert _resolve(world, "file").startswith(f"Resolved {REL}: kept the file version.")


# ── gate findings C3 and M1 ───────────────────────────────────────────────────


def _blast(twins_, mode):  # noqa: F811
    with twins_.at(twins_.native):
        return bs.backlog_blast_radius(task_id="test-epic-001", mode=mode)


def test_a_member_cannot_run_evidence_blast_radius(twins, monkeypatch):  # noqa: F811
    ran = []
    from taskmaster import blast_radius as br
    monkeypatch.setattr(br, "analyze_evidence", lambda *a, **k: ran.append(1) or {})
    provide(monkeypatch, lambda: MEMBER)
    answer = _blast(twins, "evidence")
    assert answer.startswith("Error:") and 'backlog_blast_radius(mode="evidence") is a host operator action' in answer
    assert ran == []
    assert not _blast(twins, "predictive").startswith("Error:")      # predictive reads only the store


def test_an_unknown_caller_cannot_run_evidence_blast_radius(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: None)
    assert "host operator action" in _blast(twins, "evidence")


def test_the_operator_runs_evidence_blast_radius(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: "operator")
    assert "host operator action" not in _blast(twins, "evidence")


def test_solo_blast_radius_is_unchanged(twins, monkeypatch):  # noqa: F811
    provide(monkeypatch, None)
    solo = _blast(twins, "evidence")
    provide(monkeypatch, lambda: "operator")
    assert _blast(twins, "evidence") == solo


def test_the_legacy_linear_body_is_gated_too(monkeypatch, no_linear):
    # A routing fallback to the legacy body (registry returns NotImplemented) must not run probe ungated (M1).
    monkeypatch.setenv("SECRET_X", "s3cr3t")
    provide(monkeypatch, lambda: MEMBER)
    import inspect
    legacy = inspect.unwrap(bs.backlog_linear)          # the body _route_native falls back to
    assert legacy is not bs.backlog_linear
    answer = json.loads(legacy(action="probe", token_env="SECRET_X"))
    assert "backlog_linear probe is a host operator action" in answer["error"] and no_linear == []
    assert "host operator action" in json.loads(legacy(action="bootstrap_apply", workspace_alias="ops",
                                                       team_id="T2", token_env=""))["error"]
    provide(monkeypatch, lambda: "operator")
    assert json.loads(legacy(action="probe", token_env="SECRET_X")) == {
        "error": allowlist.LINEAR_TOKEN_ENV_REFUSED}
    assert no_linear == []
