# User intent: in a team-server clone the merge gate must never let a gated merge through because the server did not
# give a usable answer: each failure kind blocks with its own text, a malformed or cached answer blocks, and only a
# fresh whole `merge-gate` view can allow (team-server-008 D1, D3.6, D3.7, D4).
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from taskmaster.native_routing import hook_reads
from taskmaster.remote import hookside
from taskmaster.remote.client import ReadResult, ReadUnavailable
from taskmaster.remote.locate import server_ref

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SHA = "a" * 40


def _module():
    spec = importlib.util.spec_from_file_location("gate_server_mode", HOOKS / "merge_gate_decide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate():
    return _module()


def _view(**task):
    base = {"id": "t-1", "mine": True, "skip_merge_gate": False, "merge_gate_freshness": "strict",
            "review_gate": {"verdict": "pass", "commit_sha": SHA}}
    return {"policy_required": True, "ambiguous": False, "candidates": ["t-1"], "task": {**base, **task}}


class FakeClient:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def read(self, view, params, **kwargs):
        self.calls.append((view, params, kwargs))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fake server: `hookside.connect` hands out `world.client`; the hook log lives in tmp_path."""
    class World:
        pass
    w = World()
    w.server = server_ref("https://team.example")
    w.state = tmp_path / "state"
    w.state.mkdir()
    w.client = FakeClient(ReadResult(_view(), 1, "s", "server", 0.0))
    monkeypatch.setattr(hookside, "server_of", lambda root: w.server)
    monkeypatch.setattr(hookside, "state_dir", lambda server: w.state)
    monkeypatch.setattr(hookside, "connect",
                        lambda root, log=None: hookside.Session(w.server, w.client, w.state))
    w.log = lambda: (w.state / "hook.log").read_text(encoding="utf-8") if (w.state / "hook.log").exists() else ""
    return w


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=60,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "f").write_text("x", encoding="utf-8")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "init")
    _git(path, "branch", "feature/x")
    return path


def decide(gate, world, repo, src="feature/x", **kwargs):
    return gate.decide_remote(repo, src, repo, **kwargs)


@pytest.mark.parametrize("kind,message,retry,expected", [
    ("unreachable", "connection refused", None,
     "BLOCK:-: Taskmaster server unreachable; retry or bypass explicitly (connection refused)."),
    ("busy", "rate limited", 3.0,
     "BLOCK:-: the Taskmaster server is rate limiting this key; retry in 3 s or bypass explicitly."),
    ("busy", "rate limited", None,
     "BLOCK:-: the Taskmaster server is rate limiting this key; retry in a few s or bypass explicitly."),
    ("server", "HTTP 503", None,
     "BLOCK:-: the Taskmaster server could not read its store; retry or bypass explicitly."),
    ("bad-response", "the reply is not JSON", None,
     "BLOCK:-: the Taskmaster server's answer was unreadable (the reply is not JSON); retry or bypass explicitly."),
    ("version", "x", None,
     "BLOCK:-: the Taskmaster server's read API is not one this plugin speaks; upgrade the taskmaster plugin, "
     "or bypass explicitly."),
    ("bad-request", "HTTP 400", None,
     "BLOCK:-: the Taskmaster server refused the merge gate's request (HTTP 400); bypass explicitly and report it."),
    ("config", "bad marker", None,
     "BLOCK:-: this clone's .taskmaster-server.json (or its latch in .git/) can't be read (bad marker); fix it, "
     "or bypass explicitly."),
])
def test_each_failure_kind_blocks_with_its_text(gate, world, repo, kind, message, retry, expected):
    world.client.answer = ReadUnavailable(kind, message, retry)
    assert decide(gate, world, repo) == expected
    assert kind in world.log() and "blocking the merge" in world.log()


def test_refused_and_no_key_blocks_never_say_re_enter_the_key(gate, world, repo):
    world.client.answer = ReadUnavailable("refused", "HTTP 401")
    refused = decide(gate, world, repo)
    assert refused.startswith("BLOCK:-: the Taskmaster server did not accept this key: it may have been revoked")
    assert "taskmaster remote set-key" in refused and "re-enter" not in refused
    world.client.answer = ReadUnavailable("no-key", "none")
    missing = decide(gate, world, repo)
    assert missing == ("BLOCK:-: no key for the Taskmaster server at https://team.example: run taskmaster remote "
                       "set-key --url <the marker's url>, or bypass explicitly.")


def test_an_ambiguous_branch_names_the_candidates(gate, world, repo):
    world.client.answer = ReadResult({**_view(), "ambiguous": True, "candidates": ["t-1", "t-2"], "task": None},
                                     1, "s", "server", 0.0)
    got = decide(gate, world, repo)
    assert got.startswith("BLOCK:-: several tasks use branch feature/x (t-1, t-2) and none of them is yours")
    assert "backlog_update_task" in got


def test_a_name_ref_problem_refuses_blocks_and_makes_no_request(gate, world, repo):
    got = decide(gate, world, repo, src="release#1")
    assert got.startswith("BLOCK:-: the branch name 'release#1' can't be sent to the Taskmaster server (")
    assert world.client.calls == []


def test_a_fresh_pass_at_the_tip_allows_and_asks_for_the_branch(gate, world, repo):
    tip = _git(repo, "rev-parse", "feature/x")
    world.client.answer = ReadResult(_view(review_gate={"verdict": "pass", "commit_sha": tip}), 1, "s", "server", 0.0)
    assert decide(gate, world, repo) == "ALLOW"
    view, params, _ = world.client.calls[0]
    assert (view, params) == ("merge-gate", {"branch": "feature/x"})


def test_the_clone_id_rides_along_when_given(gate, world, repo):
    tip = _git(repo, "rev-parse", "feature/x")
    world.client.answer = ReadResult(_view(review_gate={"verdict": "pass", "commit_sha": tip}), 1, "s", "server", 0.0)
    clone = "c-0123456789abcdef"
    assert decide(gate, world, repo, clone=clone) == "ALLOW"
    assert world.client.calls[0][1] == {"branch": "feature/x", "clone": clone}


def test_an_unreadable_tip_blocks_in_server_mode(gate, world, repo):
    world.client.answer = ReadResult(_view(), 1, "s", "server", 0.0)       # pass at "aaaa", branch unresolvable
    got = decide(gate, world, repo, src="feature/missing")
    assert got == "BLOCK:-: the tip of feature/missing can't be read in this clone; retry, or bypass explicitly."


def test_a_stale_gate_on_a_teammates_task_carries_the_teammate_sentence(gate, world, repo):
    world.client.answer = ReadResult(_view(mine=False), 1, "s", "server", 0.0)
    got = decide(gate, world, repo)
    assert got.startswith("BLOCK:t-1: the review-gate passed at aaaaaaaaaaaa, but the branch tip is now")
    assert got.endswith(" This is a teammate's task; its review-gate still applies.")
    world.client.answer = ReadResult(_view(mine=True), 1, "s", "server", 0.0)
    assert not decide(gate, world, repo).endswith("still applies.")


def test_a_no_verdict_block_does_not_get_the_teammate_sentence(gate, world, repo):
    world.client.answer = ReadResult(_view(mine=False), 1, "s", "server", 0.0)
    assert not decide(gate, world, repo, src="feature/missing").endswith("still applies.")


@pytest.mark.parametrize("make", [
    lambda: ReadResult({**_view(), "policy_required": "yes"}, 1, "s", "server", 0.0),
    lambda: ReadResult({k: v for k, v in _view().items() if k != "ambiguous"}, 1, "s", "server", 0.0),
    lambda: ReadResult({**_view(), "candidates": "t-1"}, 1, "s", "server", 0.0),
    lambda: ReadResult({**_view(), "task": []}, 1, "s", "server", 0.0),
    lambda: ReadResult(_view(id=7), 1, "s", "server", 0.0),
    lambda: ReadResult(_view(merge_gate_freshness="loose"), 1, "s", "server", 0.0),
    lambda: ReadResult(_view(review_gate="pass"), 1, "s", "server", 0.0),
    lambda: ReadResult(_view(skip_merge_gate="no"), 1, "s", "server", 0.0),
])
def test_a_malformed_view_blocks_naming_the_field(gate, world, repo, make):
    world.client.answer = make()
    got = decide(gate, world, repo)
    assert got.startswith("BLOCK:-: the Taskmaster server's answer was unreadable (") and "retry or bypass" in got


@pytest.mark.parametrize("source", ["cache", "stale"])
def test_a_cached_or_stale_answer_never_allows(gate, world, repo, source):
    tip = _git(repo, "rev-parse", "feature/x")
    world.client.answer = ReadResult(_view(review_gate={"verdict": "pass", "commit_sha": tip}), 1, "s", source, 5.0)
    assert decide(gate, world, repo) == ("BLOCK:-: the Taskmaster server's answer was unreadable (not a fresh "
                                         "server answer); retry or bypass explicitly.")


@pytest.mark.parametrize("mutate", [lambda v: v.update(policy_required=False), lambda v: v.update(task=None),
                                    lambda v: v["task"].update(skip_merge_gate=True),
                                    lambda v: v["task"].update(merge_gate_freshness="any")])
def test_policy_off_no_task_skip_and_any_freshness_still_allow(gate, world, repo, mutate):
    view = _view()
    mutate(view)
    world.client.answer = ReadResult(view, 1, "s", "server", 0.0)
    assert decide(gate, world, repo) == "ALLOW"


# ── decide(): the exits that used to be fail-open ──────────────────────────────


def test_main_in_server_mode_still_blocks_when_block_itself_raises(monkeypatch, tmp_path):
    """Review minor 3: a failure while writing the block must not fall through to `return 0` in a server clone."""
    import io
    monkeypatch.syspath_prepend(str(HOOKS))
    import merge_gate
    monkeypatch.setattr(merge_gate.merge_command, "marker_file_above", lambda cwd: True)
    monkeypatch.setattr(merge_gate, "_gate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(merge_gate, "block", lambda *a, **k: (_ for _ in ()).throw(OSError("stderr gone")))
    payload = {"tool_input": {"command": "git merge feature/x"}, "cwd": str(tmp_path), "session_id": "s"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert merge_gate.main() == 2


def test_an_exception_in_the_server_branch_blocks_as_internal(gate, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(gate, "decide_remote", boom)
    monkeypatch.setattr(gate, "server_root", lambda cwd: tmp_path)
    got = gate.decide("feature/x", tmp_path, server=True)
    assert got == "BLOCK:-: the merge gate failed (RuntimeError); retry, or bypass explicitly and report it."


@pytest.mark.parametrize("fake", ["none", "raises"])
def test_a_root_that_cannot_be_found_blocks_as_config(gate, monkeypatch, tmp_path, fake):
    def nothing(cwd):
        if fake == "raises":
            raise OSError("gone")
        return None
    monkeypatch.setattr(gate, "server_root", nothing)
    got = gate.decide("feature/x", tmp_path, server=True)
    assert got.startswith("BLOCK:-: this clone's .taskmaster-server.json") and "root can't be found" in got


def test_server_true_with_a_solo_looking_root_still_takes_the_server_branch(gate, world, monkeypatch, tmp_path):
    (tmp_path / ".taskmaster").mkdir()             # a solo-looking root
    monkeypatch.setattr(gate, "server_root", lambda cwd: tmp_path)
    world.client.answer = ReadUnavailable("unreachable", "down")
    assert gate.decide("feature/x", tmp_path, server=True).startswith("BLOCK:-: Taskmaster server unreachable")
    assert world.client.calls


def test_a_malformed_ref_through_decide_blocks(gate, world, monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "server_root", lambda cwd: tmp_path)
    assert gate.decide("a b", tmp_path, server=True).startswith("BLOCK:-: the branch name 'a b'")


# ── server_clone ───────────────────────────────────────────────────────────────

MARKER = {"format": 1, "host_id": "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c", "mode": "server", "url": "https://team.example/mcp"}


def test_server_clone_with_a_marker(gate, tmp_path):
    (tmp_path / ".taskmaster-server.json").write_text(json.dumps(MARKER), encoding="utf-8")
    assert gate.server_clone(tmp_path) is True


def test_server_clone_with_an_invalid_marker_is_true(gate, tmp_path):
    (tmp_path / ".taskmaster-server.json").write_text("{not json", encoding="utf-8")
    assert gate.server_clone(tmp_path) is True


def test_server_clone_with_no_marker_is_false(gate, tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    assert gate.server_clone(tmp_path) is False


def test_server_clone_when_resolution_fails_looks_for_a_marker_file_above(gate, monkeypatch, tmp_path):
    import taskmaster.root as root_module

    def broken(cwd):
        raise OSError("no git")
    monkeypatch.setattr(root_module, "resolve_root", broken)
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert gate.server_clone(sub) is False
    (tmp_path / ".taskmaster-server.json").write_text("{}", encoding="utf-8")
    assert gate.server_clone(sub) is True


def test_server_clone_in_a_latch_only_clone_is_true(gate, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    from taskmaster import server_marker
    latch = server_marker.latch_path(tmp_path)
    latch.write_text(json.dumps(MARKER), encoding="utf-8")
    assert gate.server_clone(tmp_path) is True
