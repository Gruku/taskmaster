# User intent: N17 review - the merge recorder must stamp the merge that actually happened
# (target and SHA seen when the hook fired, never HEAD re-read later by a detached stamp),
# only run in-process on an interpreter that can really run the stamp, never start a
# native coordinator from a hook, and never lose a stamp or its failure without a log line.
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_merge_recorder_hook import _init_git_repo, _merge_payload, _read_heavy_merge_status, _seed

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"


def _module(name):
    spec = importlib.util.spec_from_file_location(f"contract_{name}", HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _log(repo):
    path = repo / ".taskmaster" / "local" / "hook.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _merge_again(repo, name):
    _git(repo, "checkout", "-q", "feature/x")
    (repo / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(repo, "add", f"{name}.txt")
    _git(repo, "commit", "-q", "-m", name)
    _git(repo, "checkout", "-q", "master")
    _git(repo, "merge", "-q", "--no-ff", "feature/x", "-m", f"merge {name}")
    return _git(repo, "rev-parse", "HEAD")


def _fire(recorder, repo, monkeypatch):
    """Run the hook's main() in-process and return the stamp argv it launched."""
    launched = []
    monkeypatch.setattr(recorder, "run_stamp", lambda argv, **kw: launched.append(argv))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(dict(_merge_payload("git merge feature/x"),
                                                                  cwd=str(repo)))))
    monkeypatch.chdir(repo)
    assert recorder.main() == 0
    assert len(launched) == 1, launched
    return launched[0]


# ── 1. the hook resolves target and SHA; the stamp never re-reads HEAD ──────


def test_two_quick_merges_each_record_their_own_sha(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tid = _seed(repo)
    monkeypatch.setenv("TASKMASTER_ROOT", str(repo))
    recorder = _module("merge_recorder")

    first_sha = _git(repo, "rev-parse", "HEAD")
    first = _fire(recorder, repo, monkeypatch)
    second_sha = _merge_again(repo, "second")
    second = _fire(recorder, repo, monkeypatch)
    # The agent moves on before either detached stamp runs.
    _git(repo, "checkout", "-q", "-b", "hotfix")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "unrelated")

    assert first[-4:] == ["feature/x", "master", first_sha, str(repo)]
    assert second[-4:] == ["feature/x", "master", second_sha, str(repo)]
    for argv, expected in ((first, first_sha), (second, second_sha)):
        stamp_args = argv[argv.index(str(HOOKS / "merge_recorder_stamp.py")) + 1:]
        subprocess.run([sys.executable, str(HOOKS / "merge_recorder_stamp.py"), *stamp_args], cwd=str(repo),
                       capture_output=True, timeout=120, check=True)
        status = _read_heavy_merge_status(repo, tid)
        assert status["master"]["merge_commit"] == expected, (status, _log(repo))
    assert "branch:hotfix" not in _read_heavy_merge_status(repo, tid)


def test_the_stamp_never_reads_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tid = _seed(repo)
    stamp = (HOOKS / "merge_recorder_stamp.py").as_posix()
    code = ("import runpy, subprocess, sys\n"
            "real = subprocess.run\n"
            "def guarded(argv, *a, **k):\n"
            "    words = [str(x) for x in argv]\n"
            "    if 'rev-parse' in words and 'HEAD' in words:\n"
            "        open('head-read', 'w').write(' '.join(words))\n"
            "    return real(argv, *a, **k)\n"
            "subprocess.run = guarded\n"
            f"sys.argv = [{stamp!r}, 'feature/x', 'master', 'abc1234def', {str(repo)!r}]\n"
            f"runpy.run_path({stamp!r}, run_name='__main__')\n")
    subprocess.run([sys.executable, "-c", code], cwd=str(repo), capture_output=True, timeout=120, check=True)
    assert not (repo / "head-read").exists(), (repo / "head-read").read_text()
    assert _read_heavy_merge_status(repo, tid)["master"]["merge_commit"] == "abc1234def", _log(repo)


def test_a_detached_head_is_logged_not_stamped(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _seed(repo)
    monkeypatch.setenv("TASKMASTER_ROOT", str(repo))
    recorder = _module("merge_recorder")
    _git(repo, "checkout", "-q", "--detach")
    launched = []
    monkeypatch.setattr(recorder, "run_stamp", lambda argv, **kw: launched.append(argv))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_merge_payload("git merge feature/x"))))
    monkeypatch.chdir(repo)
    recorder.main()
    assert launched == []
    assert "detached HEAD" in _log(repo)


# ── 2. interpreter selection ──────────────────────────────────────────────


@pytest.mark.parametrize("python, versions, expected", [
    ((3, 12), {"fastmcp": "3.4.3", "pydantic": "2.13.4", "pyyaml": "6.0.3", "httpx": "0.28.1"}, True),
    ((3, 11), {"fastmcp": "3.9.0", "pydantic": "2.0", "pyyaml": "6.0", "httpx": "0.27"}, True),
    ((3, 12), {"fastmcp": "2.14.0", "pydantic": "2.13.4", "pyyaml": "6.0.3", "httpx": "0.28.1"}, False),
    ((3, 12), {"fastmcp": "3.1.0", "pydantic": "2.13.4", "pyyaml": "6.0.3", "httpx": "0.28.1"}, False),
    ((3, 12), {"fastmcp": "4.0.10", "pydantic": "2.13.4", "pyyaml": "6.0.3", "httpx": "0.28.1"}, False),
    ((3, 10), {"fastmcp": "3.4.3", "pydantic": "2.13.4", "pyyaml": "6.0.3", "httpx": "0.28.1"}, False),
    ((3, 12), {"fastmcp": "3.4.3", "pydantic": "1.10.2", "pyyaml": "6.0.3", "httpx": "0.28.1"}, False),
    ((3, 12), {"fastmcp": "3.4.3", "pydantic": "2.13.4", "httpx": "0.28.1"}, False),
    ((3, 12), {}, False),
])
def test_in_process_needs_the_declared_runtime(python, versions, expected):
    recorder = _module("merge_recorder")
    assert recorder.can_run_in_process(python, versions.get) is expected


def test_the_in_process_bounds_match_the_stamp_header():
    import tomllib
    import re

    recorder = _module("merge_recorder")
    text = (HOOKS / "merge_recorder_stamp.py").read_text(encoding="utf-8")
    block = re.search(r"(?m)^# /// script\s*$\n((?:^#.*$\n)+?)^# ///\s*$", text).group(1)
    header = tomllib.loads("".join(line[2:] for line in block.splitlines(True)))
    assert header["requires-python"] == ">=" + ".".join(map(str, recorder.MIN_PYTHON))
    assert f"fastmcp>={recorder.FASTMCP_MIN[0]}.{recorder.FASTMCP_MIN[1]},<{recorder.FASTMCP_BELOW}" \
        in header["dependencies"]
    assert f"pydantic>={recorder.PYDANTIC_MIN}" in header["dependencies"]


# ── 4. failures reach hook.log ────────────────────────────────────────────


def test_no_way_to_run_the_stamp_is_logged(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _seed(repo)
    monkeypatch.setenv("TASKMASTER_ROOT", str(repo))
    recorder = _module("merge_recorder")
    monkeypatch.setattr(recorder, "stamp_command", lambda *a, **k: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_merge_payload("git merge feature/x"))))
    monkeypatch.chdir(repo)
    recorder.main()
    assert "cannot run the merge stamp" in _log(repo)


def test_a_detached_stamp_writes_its_output_to_hook_log(tmp_path):
    recorder = _module("merge_recorder")
    log = tmp_path / "hook.log"
    code = "import sys, time; print('uv could not resolve', file=sys.stderr)"
    recorder.run_stamp([sys.executable, "-c", code], detach=True, log=log)
    import time
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and "uv could not resolve" not in (log.read_text() if log.exists() else ""):
        time.sleep(0.1)
    assert "uv could not resolve" in log.read_text()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_a_detached_stamp_breaks_away_from_the_host_job_when_allowed(tmp_path, monkeypatch):
    recorder = _module("merge_recorder")
    flags = []

    class Fake:
        def __init__(self, argv, **kw):
            flags.append(kw.get("creationflags", 0))
            if kw.get("creationflags", 0) & recorder.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(recorder.subprocess, "Popen", Fake)
    recorder.run_stamp(["x"], detach=True, log=tmp_path / "hook.log")
    assert flags[0] & recorder.CREATE_BREAKAWAY_FROM_JOB
    assert not flags[1] & recorder.CREATE_BREAKAWAY_FROM_JOB and len(flags) == 2


def test_an_error_answer_from_the_recorder_is_logged(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _seed(repo)
    monkeypatch.setenv("TASKMASTER_ROOT", str(repo))
    stamp = _module("merge_recorder_stamp")
    from taskmaster import backlog_server as bs

    monkeypatch.setattr(bs, "backlog_record_merge", lambda *a, **k: "Error: boom")
    stamp.stamp("feature/x", repo, "master", "abc1234")
    assert "Error: boom" in _log(repo)


def test_an_unexpected_stamp_failure_is_logged(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _seed(repo)
    monkeypatch.setenv("TASKMASTER_ROOT", str(repo))
    stamp = _module("merge_recorder_stamp")

    def explode(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(stamp, "stamp", explode)
    monkeypatch.setattr(sys, "argv", ["merge_recorder_stamp.py", "feature/x", "master", "abc", str(repo)])
    stamp.main()
    assert "kaboom" in _log(repo)


# ── 3. native: never start a coordinator; queue durably, replay from the server ──


def test_superseded_pending_stamps_are_skipped():
    from taskmaster.native_routing import merge_stamps

    entry = {"rung": "master", "sha": "old", "merged_at": "2026-09-29T10:00"}
    assert merge_stamps.superseded(entry, None, lambda a, b: False) is False
    assert merge_stamps.superseded(entry, {"merge_commit": "old", "merged_at": "x"}, lambda a, b: False)
    assert merge_stamps.superseded(entry, {"merge_commit": "new", "merged_at": "2026-09-29T09:00"},
                                   lambda a, b: True)
    assert not merge_stamps.superseded(entry, {"merge_commit": "new", "merged_at": "2026-09-29T11:00"},
                                       lambda a, b: False)
    # Ancestry unknown: the later timestamp wins.
    assert merge_stamps.superseded(entry, {"merge_commit": "new", "merged_at": "2026-09-29T11:00"},
                                   lambda a, b: None)
    assert not merge_stamps.superseded(entry, {"merge_commit": "new", "merged_at": "2026-09-29T09:00"},
                                       lambda a, b: None)
