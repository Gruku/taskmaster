# User intent: a teammate's merge stamp travels to the team server as one command, the "is mine older" question is
# answered by the teammate's own clone, and a stamp the server can't take waits in an owner-only queue in the user's
# cache dir, reusing the solo queue's rules (team-server-008 D7, D8).
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskmaster.native_routing import merge_stamps, registry
from taskmaster.remote import stamps as stamps_module
from taskmaster.remote.client import ReadResult, ReadUnavailable
from taskmaster.remote.stamps import remote_apply

SHA_A, SHA_B = "a" * 40, "b" * 40


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    shas = []
    for i in range(3):
        (path / "f").write_text(str(i), encoding="utf-8")
        _git(path, "add", "f")
        _git(path, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(path, "rev-parse", "HEAD"))
    return SimpleNamespace(path=path, old=shas[0], mid=shas[1], new=shas[2])


class FakeClient:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def write(self, view, body, *, deadline=None):
        self.sent.append((view, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ReadResult(answer, 1, "s", "server", 0.0)


def entry(repo=None, sha=SHA_A, **more):
    return {"branch": "feature/x", "target": "qa-line", "sha": sha, "merged_at": "2026-11-07T10:15",
            "repo": str(repo.path) if repo else "/some/clone", "queued_at": time.time(), **more}


def run(client, e, log=None):
    lines = [] if log is None else log
    return remote_apply(SimpleNamespace(client=client), lines.append, time.monotonic() + 30)(e)


# ── the D7 table ────────────────────────────────────────────────


@pytest.mark.parametrize("answer, expected", [
    ({"outcome": "recorded", "task_id": "t", "rung": "qa"}, "applied"),
    ({"outcome": "already", "task_id": "t", "rung": "qa"}, "dropped"),
    ({"outcome": "no-task"}, "dropped"),
    ({"outcome": "ambiguous", "candidates": ["a-1", "a-2"]}, ("reject", "several tasks use branch feature/x (a-1, a-2)")),
    ({"outcome": "refused", "message": "Error: nope"}, ("reject", "Error: nope")),
])
def test_each_simple_outcome(answer, expected):
    client = FakeClient(answer)
    assert run(client, entry()) == expected
    assert client.sent[0][0] == "merge-stamp" and "checked" not in client.sent[0][1]


def test_the_body_carries_the_stamp_and_the_clone_but_not_the_repo_or_queue_time():
    client = FakeClient({"outcome": "recorded"})
    run(client, entry(clone="c-0123456789abcdef"))
    assert client.sent[0][1] == {"branch": "feature/x", "target": "qa-line", "sha": SHA_A,
                                 "merged_at": "2026-11-07T10:15", "clone": "c-0123456789abcdef"}


def test_check_with_an_ancestor_commit_is_dropped(repo):
    current = {"merge_commit": repo.new, "merged_at": "2026-11-07T09:00"}
    client = FakeClient({"outcome": "check", "current": current})
    log = []
    assert run(client, entry(repo, sha=repo.old), log) == "dropped"
    assert len(client.sent) == 1 and any("superseded" in line for line in log)


def test_check_with_a_descendant_resends_with_checked_against(repo):
    current = {"merge_commit": repo.old, "merged_at": "2026-11-07T09:00"}
    client = FakeClient({"outcome": "check", "current": current}, {"outcome": "recorded"})
    assert run(client, entry(repo, sha=repo.new)) == "applied"
    assert client.sent[1][1]["checked"] == {"against": repo.old}


def test_an_unknown_recorded_commit_falls_back_to_merged_at(repo):
    unknown = "f" * 40
    newer = {"merge_commit": unknown, "merged_at": "2026-11-07T10:15"}      # same minute: the record wins
    assert run(FakeClient({"outcome": "check", "current": newer}), entry(repo, sha=repo.new)) == "dropped"
    older = {"merge_commit": unknown, "merged_at": "2026-11-07T09:00"}
    client = FakeClient({"outcome": "check", "current": older}, {"outcome": "recorded"})
    assert run(client, entry(repo, sha=repo.new)) == "applied"


def test_three_checks_in_a_row_retry(repo):
    answers = [{"outcome": "check", "current": {"merge_commit": repo.old, "merged_at": "2026-11-07T09:00"}}] * 3
    client = FakeClient(*answers)
    assert run(client, entry(repo, sha=repo.new)) == "retry" and len(client.sent) == 3


@pytest.mark.parametrize("kind", ["unreachable", "busy", "server", "bad-response", "refused", "no-key", "config",
                                  "version"])
def test_each_unavailable_kind_raises_unavailable(kind):
    with pytest.raises(merge_stamps.Unavailable):
        run(FakeClient(ReadUnavailable(kind, "down")), entry())


def test_bad_request_rejects():
    assert run(FakeClient(ReadUnavailable("bad-request", "branch is not a ref")), entry()) == \
        ("reject", "branch is not a ref")


def test_an_unknown_outcome_rejects():
    assert run(FakeClient({"outcome": "strange"}), entry())[0] == "reject"


def test_git_is_ancestor_honours_its_timeout(repo, monkeypatch):
    seen = {}

    def fake(argv, timeout=None, **kwargs):
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(merge_stamps, "run_bounded", fake)
    assert merge_stamps.git_is_ancestor(repo.path, timeout=1.25)(repo.old, repo.new) is True
    assert seen["timeout"] == 1.25
    assert stamps_module.ANCESTRY_MIN_SECONDS == 0.5


# ── QueueDir and the new replay options ─────────────────────────


def test_a_queue_dir_works_in_a_plain_directory_and_logs_to_its_own_file(tmp_path):
    queue = merge_stamps.QueueDir(tmp_path / "stamps", tmp_path / "state" / "hook.log")
    (tmp_path / "stamps").mkdir()
    e = entry()
    merge_stamps.enqueue(queue, e)
    assert merge_stamps.has_pending(queue)
    assert (tmp_path / "stamps" / merge_stamps.PENDING).is_file()
    seen = []
    log_lines = []
    counts = merge_stamps.replay_queue(queue, lambda item: seen.append(item) or "applied", log=log_lines.append,
                                       required=("branch", "target", "sha", "repo"))
    assert counts["applied"] == 1 and seen[0]["sha"] == SHA_A
    assert not merge_stamps.has_pending(queue)
    merge_stamps.hook_log(queue, "hello")
    assert "hello" in (tmp_path / "state" / "hook.log").read_text(encoding="utf-8")
    assert not (tmp_path / "stamps" / "local").exists()


def test_required_keys_label_and_max_age(tmp_path):
    queue = merge_stamps.QueueDir(tmp_path, tmp_path / "hook.log")
    merge_stamps.enqueue(queue, {"branch": "b"})                                  # lacks the required keys
    merge_stamps.enqueue(queue, entry(queued_at=time.time() - 40 * 86400))       # too old
    merge_stamps.enqueue(queue, entry(sha=SHA_B))
    lines = []
    applied = []
    counts = merge_stamps.replay_queue(queue, lambda item: applied.append(item["sha"]) or "applied", log=lines.append,
                                       required=("branch", "target", "sha", "repo"),
                                       label=lambda e: f"L:{e['sha'][:3]}", max_age=30 * 86400)
    assert applied == [SHA_B] and counts["rejected"] == 2
    assert any("L:bbb" in line for line in lines)
    assert any("30 days" in line or "queued more than" in line for line in lines)


def test_solo_replay_keeps_everything_with_no_max_age(tmp_path):
    (tmp_path / "local").mkdir()
    merge_stamps.enqueue(tmp_path, {"task_id": "t-1", "rung": "qa", "sha": SHA_A, "queued_at": 1})
    applied = []
    merge_stamps.replay_queue(tmp_path, lambda item: applied.append(item) or "applied")
    assert len(applied) == 1


# ── the host never replays a queue itself ───────────────────────


def test_a_serve_daemon_does_not_replay_a_host_queue(tmp_path, monkeypatch):
    backlog = tmp_path / ".taskmaster"
    (backlog / "local" / "coordinator").mkdir(parents=True)
    (backlog / "local" / "coordinator" / "discovery.json").write_text("{}", encoding="utf-8")
    merge_stamps.enqueue(backlog, {"task_id": "t-1", "rung": "qa", "sha": SHA_A, "merged_at": "x"})
    from taskmaster.coordinator import client as coordinator_client
    started = []
    monkeypatch.setattr(registry.threading, "Thread", lambda *a, **k: started.append(1) or SimpleNamespace(
        start=lambda: None, is_alive=lambda: False))
    monkeypatch.setattr(coordinator_client, "serving", lambda: True)
    registry._replay_merge_stamps(backlog / "local" / "store.db", backlog, "s")
    assert started == []
    monkeypatch.setattr(coordinator_client, "serving", lambda: False)
    registry._replay_merge_stamps(backlog / "local" / "store.db", backlog, "s")
    assert started == [1]
    assert Path(backlog / "local" / merge_stamps.PENDING).is_file()


def test_merged_at_is_a_utc_minute():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mrs", Path(__file__).resolve().parents[1] / "hooks" / "merge_recorder_stamp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stamp = module._utc_minute()
    assert len(stamp) == 16 and stamp[10] == "T" and time.strptime(stamp, "%Y-%m-%dT%H:%M")
    assert json.dumps(stamp)
