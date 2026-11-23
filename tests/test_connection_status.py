# User intent: every Claude Code session's mods name the taskmaster they talk to (local-server-012), from a `server` object the
# server adds to backlog_handover_list(format="json"); the key is additive and the agent's default text answer never changes.
import json
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server as bs  # noqa: E402
from taskmaster import localserve as ls  # noqa: E402

from tests.entity_helpers import write_handover  # noqa: E402

SERVER_KEYS = {"mode", "project", "version", "reason", "opted_out"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "CodeMaestro"
    bp = root / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True)
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    (bp.parent / "handovers").mkdir()
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "_backlog_path", lambda: bp)
    monkeypatch.setattr(bs, "SERVE_PROFILE", None)
    monkeypatch.setattr(bs, "_PER_SESSION_REASON", None)
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    write_handover(bp, tldr="one", when="2026-04-01")
    return bp


def server_of() -> dict:
    return json.loads(bs.backlog_handover_list(format="json"))["server"]


def test_a_stdio_server_is_per_session_and_names_the_root_when_the_backlog_has_no_project_name(project):
    out = server_of()
    assert set(out) == SERVER_KEYS
    assert (out["mode"], out["project"], out["version"]) == ("per-session", "CodeMaestro", bs.VERSION)
    assert out["opted_out"] is False
    assert out["reason"] == "the project store is not a ready native store"     # a YAML store: the static reason applies


def test_the_backlog_project_name_wins_over_the_directory(project):
    data = yaml.safe_load(project.read_text())
    data["meta"]["project"] = "HAY"
    project.write_text(yaml.safe_dump(data))
    assert server_of()["project"] == "HAY"


@pytest.mark.parametrize("how, reason", [("env", ls.OPT_OUT_ENV), ("setting", ls.OPT_OUT_SETTING)])
def test_an_opt_out_is_named_and_marked(project, monkeypatch, how, reason):
    if how == "env":
        monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "0")
    else:
        (project.parent / "taskmaster.json").write_text('{"local_server": "off"}', encoding="utf-8")
    out = server_of()
    assert (out["mode"], out["reason"], out["opted_out"]) == ("per-session", reason, True)


def test_the_reason_is_read_once_per_process(project, monkeypatch):
    calls = []
    monkeypatch.setattr(ls, "per_session_reason", lambda root: calls.append(root) or ("x", False))
    server_of()
    server_of()
    assert len(calls) == 1


def test_a_reason_that_cannot_be_read_never_fails_the_listing(project, monkeypatch):
    monkeypatch.setattr(ls, "per_session_reason", lambda root: (_ for _ in ()).throw(OSError("boom")))
    assert (server_of()["reason"], server_of()["opted_out"]) == ("", False)


@pytest.mark.parametrize("profile", ["local", "team"])
def test_a_served_profile_names_itself_with_no_reason(project, monkeypatch, profile):
    monkeypatch.setattr(bs, "SERVE_PROFILE", profile)
    out = server_of()
    assert (out["mode"], out["reason"], out["opted_out"], out["version"]) == (profile, "", False, bs.VERSION)


def test_the_text_answer_is_unchanged_by_the_connection(project, monkeypatch):
    texts = set()
    for profile in (None, "local", "team"):
        monkeypatch.setattr(bs, "SERVE_PROFILE", profile)
        texts.add(bs.backlog_handover_list())
        texts.add(bs.backlog_handover_list(verbose=True))
    assert len(texts) == 2                                     # slim and verbose, each the same whatever the mode
    for text in texts:
        assert "per-session" not in text and "server" not in text and bs.VERSION not in text


def test_an_error_envelope_carries_no_server(project):
    assert json.loads(bs.backlog_handover_list(format="json", since="nope")) == {
        "error": "`since` must be a date in YYYY-MM-DD format, got 'nope'."}


def test_the_bridge_and_the_server_share_one_fallback_rule(project, monkeypatch):
    import tm_bridge
    monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "off")
    assert tm_bridge.fallback_reason(project.parents[1]) == ls.fallback_reason(project.parents[1]) == ls.OPT_OUT_ENV
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER")
    assert ls.per_session_reason(project.parents[1]) == ("the project store is not a ready native store", False)


def test_a_locked_store_is_no_answer_and_is_asked_again(tmp_path, monkeypatch):
    # Review M2: a store locked past the short wait must not be kept as "not native" for the whole session.
    import sqlite3
    from taskmaster.root import db_path
    root = tmp_path / "Locked"
    path = db_path(root / ".taskmaster")
    path.parent.mkdir(parents=True)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    holder.execute("BEGIN EXCLUSIVE")
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    try:
        assert ls.per_session_reason(root) is None
        assert ls.is_native_store(root) is False                       # the bridge's own rule is unchanged
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert ls.per_session_reason(root) == ("the project store is not a ready native store", False)


def test_the_server_keeps_no_answer_from_a_locked_store(project, monkeypatch):
    answers = [None, ("the project store is not a ready native store", False)]
    calls = []
    monkeypatch.setattr(ls, "per_session_reason", lambda root: calls.append(root) or answers[min(len(calls), 2) - 1])
    assert server_of()["reason"] == ""
    assert server_of()["reason"] == "the project store is not a ready native store"
    server_of()
    assert len(calls) == 2


def test_the_native_listing_names_the_project_from_the_native_backlog(tmp_path, monkeypatch):
    # Review M5: the native path reads meta.project from the native backlog entity, like the legacy one from YAML.
    from native_twins import make_twins
    monkeypatch.setattr(bs, "SERVE_PROFILE", None)
    monkeypatch.setattr(bs, "_PER_SESSION_REASON", None)
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    twins = make_twins(tmp_path, monkeypatch)
    with twins.at(twins.native):
        assert json.loads(bs.backlog_handover_list(format="json"))["server"]["project"] == "native"    # no name: the directory
    # The name as backlog_init records it (meta.project), brought into the native store the documented way: a hand edit of
    # backlog.yaml, then backlog_sync.
    with twins.at(twins.native):
        path = twins.native / ".taskmaster" / "backlog.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.setdefault("meta", {})["project"] = "Named Project"
        path.write_text(yaml.dump(data), encoding="utf-8")
        synced = bs.backlog_sync()
        if "conflict" in synced:                                    # the twin's store rewrote backlog.yaml since its projection
            synced = bs.backlog_resolve_conflict(file="backlog.yaml", take="file")
        text = bs.backlog_handover_list(format="json")
    assert text.startswith("{"), (synced, text)
    native = json.loads(text)["server"]
    assert (native["mode"], native["project"], native["reason"]) == ("per-session", "Named Project", ""), synced
