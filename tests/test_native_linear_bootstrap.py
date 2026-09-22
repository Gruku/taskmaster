"""User intent: `backlog_linear(action="bootstrap_apply")` on a native store adds a Linear
workspace to `.taskmaster/linear.yaml` atomically under the coordinator's publication
boundary (N13 step 7): other workspaces and unknown settings survive, only the name of
the token's environment variable is ever written, and a failure leaves the file as it was.
"""
from __future__ import annotations

import json
import threading

import pytest
import yaml

from taskmaster import backlog_server as bs
from native_twins import make_twins

EXISTING = ("version: 1\nx_team_note: keep me\ndefault_workspace: cm\n"
            "workspaces:\n- {alias: cm, team_id: T1, token_env: CM_LINEAR_TOKEN, x_extra: 7}\n")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_add_task(title="Seeded", epic="test-epic", phase="dev"))
    with twins.at(twins.native):
        bs.backlog_status()  # a read: the coordinator starts only for the first write
    return twins


def _config_path(twins):
    return twins.native / ".taskmaster" / "linear.yaml"


def _owner(twins):
    from tests.native_coordinator_helpers import _owners, compatibility_client
    compatibility_client(twins.native)
    return _owners[twins.native.resolve()]


def _bootstrap(twins, **overrides):
    arguments = dict(action="bootstrap_apply", workspace_alias="ops", team_id="T2", token_env="OPS_LINEAR_TOKEN",
                     status_mapping="todo:state-1,done:state-2", priority_mapping="")
    arguments.update(overrides)
    with twins.at(twins.native):
        return json.loads(bs.backlog_linear(**arguments))


def _leftovers(twins):
    return sorted(path.name for path in (twins.native / ".taskmaster").iterdir() if ".tmp." in path.name)


def test_bootstrap_adds_a_workspace_and_preserves_the_others(twins, monkeypatch):
    _config_path(twins).write_text(EXISTING, encoding="utf-8")
    monkeypatch.setenv("OPS_LINEAR_TOKEN", "lin_api_this-secret-must-never-be-written")
    answer = _bootstrap(twins)
    assert answer == {"ok": True, "path": str(_config_path(twins)), "workspace": "ops", "default": True}, answer
    raw = _config_path(twins).read_text(encoding="utf-8")
    assert "this-secret-must-never-be-written" not in raw
    config = yaml.safe_load(raw)
    assert config["x_team_note"] == "keep me" and config["version"] == 1
    assert config["default_workspace"] == "ops"
    assert config["workspaces"] == [
        {"alias": "cm", "team_id": "T1", "token_env": "CM_LINEAR_TOKEN", "x_extra": 7},
        {"alias": "ops", "team_id": "T2", "token_env": "OPS_LINEAR_TOKEN",
         "status_mapping": {"todo": "state-1", "done": "state-2"}}]
    assert _leftovers(twins) == []


def test_bootstrap_creates_the_file_when_absent(twins):
    answer = _bootstrap(twins, default_workspace=False)
    assert answer["ok"] and answer["default"] is False
    config = yaml.safe_load(_config_path(twins).read_text(encoding="utf-8"))
    assert [ws["alias"] for ws in config["workspaces"]] == ["ops"] and "default_workspace" not in config


@pytest.mark.parametrize("token_env", ["lin_api_0123456789abcdef", "not a name", "OPS-TOKEN", "LIN_OAUTH_abc"])
def test_bootstrap_refuses_anything_but_an_environment_variable_name(twins, token_env):
    _config_path(twins).write_text(EXISTING, encoding="utf-8")
    answer = _bootstrap(twins, token_env=token_env)
    assert "environment variable" in answer["error"], answer
    assert _config_path(twins).read_text(encoding="utf-8") == EXISTING


@pytest.mark.parametrize("overrides, message", [
    ({"workspace_alias": "cm", "token_env": "OTHER_TOKEN"}, "workspace alias 'cm' already exists in linear.yaml"),
    ({"token_env": "CM_LINEAR_TOKEN"}, "config validation failed"),
    ({"workspace_alias": "has-hyphen"}, "config validation failed"),
    ({"status_mapping": "todo"}, "invalid mapping pair"),
    ({"team_id": ""}, "team_id is required"),
])
def test_a_refused_bootstrap_leaves_the_file_untouched(twins, overrides, message):
    _config_path(twins).write_text(EXISTING, encoding="utf-8")
    answer = _bootstrap(twins, **overrides)
    assert message in answer["error"], answer
    assert _config_path(twins).read_text(encoding="utf-8") == EXISTING
    assert _leftovers(twins) == []


def test_a_failed_replace_leaves_the_file_untouched_and_no_temp(twins, monkeypatch):
    from taskmaster.native import projection
    _config_path(twins).write_text(EXISTING, encoding="utf-8")

    real_replace = projection.os.replace

    def refuse(source, target, *args, **kwargs):
        if str(target).endswith("linear.yaml"):
            raise PermissionError("replace refused")
        return real_replace(source, target, *args, **kwargs)
    monkeypatch.setattr(projection.os, "replace", refuse)
    answer = _bootstrap(twins)
    assert "replace refused" in answer["error"], answer
    assert _config_path(twins).read_text(encoding="utf-8") == EXISTING
    assert _leftovers(twins) == []


def test_bootstrap_waits_for_the_publication_boundary(twins, monkeypatch):
    from taskmaster.coordinator import linear_config
    monkeypatch.setattr(linear_config, "PUBLICATION_TIMEOUT", 0.2)
    _config_path(twins).write_text(EXISTING, encoding="utf-8")
    owner = _owner(twins)
    held, release = threading.Event(), threading.Event()

    def hold():
        with owner.publication:
            held.set()
            release.wait(10)
    holder = threading.Thread(target=hold)
    holder.start()
    try:
        held.wait(5)
        answer = _bootstrap(twins)
    finally:
        release.set()
        holder.join(10)
    assert "publisher busy" in answer["error"], answer
    assert _config_path(twins).read_text(encoding="utf-8") == EXISTING
    assert _bootstrap(twins)["ok"]


def test_an_identical_retry_after_a_lost_response_is_not_an_error(twins):
    first = _bootstrap(twins)
    written = _config_path(twins).read_bytes()
    again = _bootstrap(twins)
    assert first["ok"] and again == dict(first, unchanged=True), again
    assert _config_path(twins).read_bytes() == written


@pytest.mark.parametrize("overrides", [
    {"team_id": "lin_api_0123456789abcdef"},
    {"workspace_alias": "lin_oauth_abc"},
    {"status_mapping": "todo:lin_api_0123456789abcdef"},
    {"status_mapping": "LIN_API_key:state-1"},
    {"priority_mapping": "high:lin_oauth_abc"},
])
def test_bootstrap_refuses_a_credential_in_any_field(twins, overrides):
    _config_path(twins).write_text(EXISTING, encoding="utf-8")
    answer = _bootstrap(twins, **overrides)
    assert "credential" in answer.get("error", ""), answer
    assert _config_path(twins).read_text(encoding="utf-8") == EXISTING
