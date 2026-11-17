# User intent: the team server's backup (team-server-013) commits the store's export to its own branch without
# ever touching code branches, the working tree or secrets, and pushes it only fast-forward to the operator's remote.
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
from pathlib import Path

import pytest

from taskmaster.coordinator import git as managed
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve import state_export as se
from native_git_helpers import git, init_repo, install_hook
from native_twins import native_connection
from test_native_service import root  # noqa: F401  (fixture)

pytestmark = pytest.mark.xdist_group("heavy_processes")
TASK = "test-epic-001"
BRANCH = "taskmaster-state"


# ── Step 1: git.probe takes a bounded extra environment ─────────────────────


def test_probe_env_reaches_git_and_inherited_git_variables_stay_scrubbed(tmp_path, monkeypatch):
    init_repo(tmp_path)
    index = tmp_path / ".taskmaster-probe-index"
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))          # scrubbed, as before
    code, out = managed.probe(tmp_path, "rev-parse", "--git-path", "index", env={"GIT_INDEX_FILE": str(index)})
    assert code == 0 and os.path.normcase(out.decode().strip()) == os.path.normcase(str(index))
    _, top = managed.probe(tmp_path, "rev-parse", "--show-toplevel")
    assert os.path.normcase(os.path.realpath(top.decode().strip())) == os.path.normcase(os.path.realpath(tmp_path))


@pytest.mark.parametrize("key", ["GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "PATH"])
def test_probe_env_refuses_anything_outside_its_list(tmp_path, key):
    with pytest.raises(ValueError, match=key):
        managed.probe(tmp_path, "version", env={key: "x"})


def test_probe_with_stderr_returns_three_values(tmp_path):
    init_repo(tmp_path)
    code, out, err = managed.probe(tmp_path, "rev-parse", "--verify", "-q", "nope", ok=(0, 1), with_stderr=True)
    assert code == 1 and out == b"" and isinstance(err, bytes)
    assert managed.probe(tmp_path, "rev-parse", "HEAD")[0] == 0               # unchanged default shape
    assert git(tmp_path, "status", "--porcelain") == ""


# ── Step 2: tree policy, source filter, redaction (pure) ────────────────────


@pytest.mark.parametrize("rel, rule", [
    ("local/serve/operator.token", "local state"), ("tasks/.gitmodules", "dot component"),
    (".taskmaster/tasks/x.md", "dot component"), ("tasks/principals.json", "secret name"),
    ("tasks/x.key", "secret name"), ("tasks/store.db-wal", "secret name"), ("GIT~1/config", "git alias"),
    ("tasks/a\nb.md", "unsafe characters"), ('"tasks/x.md', "unsafe characters"), ("tasks/../x.md", "unsafe path"),
    ("tasks\\x.md", "unsafe characters"),
])
def test_tree_policy_refuses_secrets_and_unsafe_paths(rel, rule):
    assert se.tree_path_refusal(rel) == rule


@pytest.mark.parametrize("rel", ["backlog.yaml", "tasks/test-epic-001.md", "users/ann/profile.yaml",
                                 "external-documents/task/web-1/spec.md", "handovers/_archive/2026/x.md"])
def test_tree_policy_keeps_store_paths(rel):
    assert se.tree_path_refusal(rel) is None


SECRET_SOURCES = [".env", ".env.local", "config/.env", ".git/config", ".taskmaster/local/serve/operator.token",
                  "principals.json", "store.db", "deploy/server.pem", "keys/id_ed25519", "secrets.yaml",
                  "..\\outside.md", "C:/x.md", "./.ENV", "/etc/passwd", "docs/local/x.md", "prod.env", "a/credentials"]


@pytest.mark.parametrize("path", SECRET_SOURCES)
def test_source_filter_refuses_secret_paths(path):
    assert se.source_path_refusal(path) is not None


@pytest.mark.parametrize("path", ["docs/specs/x.md", "docs/plans/y.md", "./docs/z.md", "README.md"])
def test_source_filter_keeps_documents(path):
    assert se.source_path_refusal(path) is None


def test_resolved_path_is_filtered_too(tmp_path):
    (tmp_path / "secret.env").write_text("TOKEN=abc", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    link = tmp_path / "docs" / "spec.md"
    try:
        os.symlink(tmp_path / "secret.env", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this host")
    assert se.source_path_refusal("docs/spec.md") is None
    assert se.resolved_refusal(tmp_path, "docs/spec.md") == "resolves to a secret name"


def test_body_armour_and_redaction():
    assert se.body_refusal("x\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc") == "private key in the body"
    assert se.body_refusal("a public note") is None
    text = ("fatal: unable to access 'https://u:secret@127.0.0.1:9/x.git/': Failed\n"
            "ssh: git@host.invalid:org/x.git denied; To ssh://git@host.invalid/x.git")
    clean = se.redact(text, "backup")
    for leak in ("secret", "127.0.0.1:9", "host.invalid", "u:"):
        assert leak not in clean
    assert "<remote:backup>" in clean and len(se.redact("x" * 2000, "b")) == 500


# ── Status exit codes (§7.4, I5) ────────────────────────────────────────────


@pytest.mark.parametrize("values, now, code", [
    ({}, 1000, 0),
    ({"failures": 1, "last_error": "x"}, 1000, 1),
    ({"push_failures": 1, "push_state": "push_failed"}, 1000, 1),
    ({"push_state": "push_blocked"}, 1000, 1),
    ({"deferred_since": 900, "state": "deferred", "deferred_reason": "managed Git pin"}, 1000, 0),
    ({"deferred_since": 0, "state": "deferred", "deferred_reason": "managed Git pin", "last_ok": 3700}, 3700, 1),
    ({"deferred_since": 0, "state": "behind", "behind_reason": "PROGRESS debt", "last_ok": 3700}, 3700, 1),
    ({}, 1000 + 3601, 1),
    ({"held": {"tasks/x.md": 0}}, 1000, 0),
    ({"held": {"tasks/x.md": 0}, "last_ok": 90000}, 90000, 1),
    ({"dropped_documents_count": 3}, 1000, 0),
])
def test_status_exit_codes(tmp_path, values, now, code):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    from taskmaster.serve.auth import serve_directory
    serve_directory(tmp_path)
    se.save_state(tmp_path, {"branch": BRANCH, "interval": 900, "started": 0, "last_ok": 1000, **values})
    report, found = se.status(tmp_path, now=now)
    assert found == code, report
    if code == 1 and values.get("behind_reason"):
        assert any("PROGRESS debt" in problem for problem in report["problems"])


def test_status_without_configuration_is_2(tmp_path):
    assert se.status(tmp_path)[1] == 2
