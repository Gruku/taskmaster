"""Tests for the Linear config loader (linear-002)."""
import os
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (  # noqa: E402
    get_linear_workspace,
    linear_config_path,
    load_linear_config,
    resolve_linear_token,
)


# ── Fixtures ───────────────────────────────────────────────────


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    return bp


def _minimal_cfg() -> dict:
    return {
        "workspaces": [
            {
                "alias": "cm",
                "team_id": "a1c7e3c3-a532-412d-8c75-5c200607c4ea",
                "token_env": "TASKMASTER_LINEAR_TOKEN_CM",
            }
        ],
        "default_workspace": "cm",
    }


def _write_cfg(backlog_path: Path, cfg: dict) -> Path:
    p = linear_config_path(backlog_path)
    p.write_text(yaml.safe_dump(cfg))
    return p


# ── Load + missing-file behaviour ──────────────────────────────


def test_load_returns_none_when_file_missing(tmp_path):
    """A project without linear.yaml is supported — Linear sync is opt-in."""
    bp = _make_backlog(tmp_path)
    assert load_linear_config(bp) is None


def test_load_parses_minimal_valid_config(tmp_path):
    bp = _make_backlog(tmp_path)
    _write_cfg(bp, _minimal_cfg())
    cfg = load_linear_config(bp)
    assert cfg is not None
    assert cfg["workspaces"][0]["alias"] == "cm"
    assert cfg["default_workspace"] == "cm"


# ── Validator ──────────────────────────────────────────────────


def test_validator_rejects_empty_workspaces(tmp_path):
    bp = _make_backlog(tmp_path)
    _write_cfg(bp, {"workspaces": []})
    with pytest.raises(ValueError, match="at least one workspace"):
        load_linear_config(bp)


def test_validator_rejects_workspaces_not_a_list(tmp_path):
    bp = _make_backlog(tmp_path)
    _write_cfg(bp, {"workspaces": "cm"})
    with pytest.raises(ValueError, match="at least one workspace"):
        load_linear_config(bp)


@pytest.mark.parametrize("missing_field", ["alias", "team_id", "token_env"])
def test_validator_rejects_workspace_missing_required_field(tmp_path, missing_field):
    bp = _make_backlog(tmp_path)
    cfg = _minimal_cfg()
    del cfg["workspaces"][0][missing_field]
    _write_cfg(bp, cfg)
    with pytest.raises(ValueError, match=missing_field):
        load_linear_config(bp)


def test_validator_rejects_duplicate_aliases(tmp_path):
    bp = _make_backlog(tmp_path)
    cfg = _minimal_cfg()
    cfg["workspaces"].append({
        "alias": "cm",  # duplicate
        "team_id": "other",
        "token_env": "TASKMASTER_LINEAR_TOKEN_OTHER",
    })
    _write_cfg(bp, cfg)
    with pytest.raises(ValueError, match="alias.*duplicated"):
        load_linear_config(bp)


def test_validator_rejects_duplicate_token_envs(tmp_path):
    """Two workspaces using the same env var would silently share the same token,
    masking config errors. Refuse at load."""
    bp = _make_backlog(tmp_path)
    cfg = _minimal_cfg()
    cfg["workspaces"].append({
        "alias": "other",
        "team_id": "other",
        "token_env": "TASKMASTER_LINEAR_TOKEN_CM",  # duplicate
    })
    _write_cfg(bp, cfg)
    with pytest.raises(ValueError, match="token_env.*duplicated"):
        load_linear_config(bp)


def test_validator_rejects_unknown_default_workspace(tmp_path):
    bp = _make_backlog(tmp_path)
    cfg = _minimal_cfg()
    cfg["default_workspace"] = "ghost"
    _write_cfg(bp, cfg)
    with pytest.raises(ValueError, match="default_workspace.*ghost"):
        load_linear_config(bp)


def test_validator_accepts_no_default_workspace_when_single_workspace(tmp_path):
    """default_workspace is optional. Code that needs a workspace must either
    pass alias explicitly or read it from a tracker."""
    bp = _make_backlog(tmp_path)
    cfg = _minimal_cfg()
    del cfg["default_workspace"]
    _write_cfg(bp, cfg)
    cfg_loaded = load_linear_config(bp)
    assert cfg_loaded is not None
    assert "default_workspace" not in cfg_loaded


# ── get_linear_workspace ───────────────────────────────────────


def test_get_workspace_by_alias(tmp_path):
    cfg = _minimal_cfg()
    ws = get_linear_workspace(cfg, "cm")
    assert ws["team_id"] == "a1c7e3c3-a532-412d-8c75-5c200607c4ea"


def test_get_workspace_uses_default_when_no_alias_passed(tmp_path):
    cfg = _minimal_cfg()
    ws = get_linear_workspace(cfg)
    assert ws["alias"] == "cm"


def test_get_workspace_raises_on_unknown_alias():
    cfg = _minimal_cfg()
    with pytest.raises(ValueError, match="ghost"):
        get_linear_workspace(cfg, "ghost")


def test_get_workspace_raises_when_no_alias_and_no_default():
    cfg = _minimal_cfg()
    del cfg["default_workspace"]
    with pytest.raises(ValueError, match="no workspace alias"):
        get_linear_workspace(cfg)


# ── resolve_linear_token ───────────────────────────────────────


def test_resolve_token_reads_env_var(monkeypatch):
    monkeypatch.setenv("TASKMASTER_LINEAR_TOKEN_CM", "lin_api_secret_xyz")
    ws = _minimal_cfg()["workspaces"][0]
    assert resolve_linear_token(ws) == "lin_api_secret_xyz"


def test_resolve_token_refuses_when_env_missing(monkeypatch):
    monkeypatch.delenv("TASKMASTER_LINEAR_TOKEN_CM", raising=False)
    ws = _minimal_cfg()["workspaces"][0]
    with pytest.raises(ValueError, match="TASKMASTER_LINEAR_TOKEN_CM"):
        resolve_linear_token(ws)


def test_resolve_token_refuses_when_workspace_missing_token_env():
    ws = {"alias": "cm", "team_id": "x"}  # no token_env
    with pytest.raises(ValueError, match="token_env"):
        resolve_linear_token(ws)


def test_resolve_token_error_message_includes_creation_url(monkeypatch):
    """Operator should be told where to get a token, not just that it's missing."""
    monkeypatch.delenv("TASKMASTER_LINEAR_TOKEN_CM", raising=False)
    ws = _minimal_cfg()["workspaces"][0]
    with pytest.raises(ValueError, match="linear.app/settings/api"):
        resolve_linear_token(ws)
