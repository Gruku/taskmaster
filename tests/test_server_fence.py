# User intent: prove the fence's core: it costs two lstat calls in solo, finds the marker at the enclosing repo
# root from any subdirectory, and only the process holding the host lease (this pid, while held) is exempt
# (team-server-009 Step 2).
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from taskmaster import root as root_module
from taskmaster import server_fence as fence
from taskmaster import server_marker as sm

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
URL = "https://tm.example.com/mcp"
_GIT_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    leaked = fence.reset_for_tests()
    assert not leaked, f"leaked host leases: {[lease.root for lease in leaked]}"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60, **_GIT_FLAGS)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "-q", cwd=path)
    (path / "sub" / "deeper").mkdir(parents=True)
    return path


def project(path: Path) -> Path:
    (path / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)
    return path


def mark(path: Path, url=URL) -> None:
    sm.write_marker(path, url, HOST_ID)


def run_python(code: str, *, env=None, timeout=60):
    environment = dict(os.environ, **(env or {}))
    environment.pop("PYTHONPATH", None)
    prelude = f"import sys; sys.path.insert(0, {str(PLUGIN_ROOT)!r}); "
    return subprocess.run([sys.executable, "-c", prelude + code], capture_output=True, text=True,
                          timeout=timeout, env=environment, cwd=str(PLUGIN_ROOT))


def _outside_git(path: Path) -> bool:
    found, answered = root_module.git_common_root_checked(path)
    return answered and found is None


# -- cost and roots ----------------------------------------------------------------------------------

def _count_lexists(monkeypatch):
    calls = []
    real = os.path.lexists

    def counting(path):
        calls.append(str(path))
        return real(path)
    monkeypatch.setattr(fence.os.path, "lexists", counting)
    return calls


def test_solo_costs_two_probes_and_creates_nothing(repo, monkeypatch):
    before = sorted(p.name for p in repo.iterdir()), sorted(p.name for p in (repo / ".git").iterdir())
    assert fence.refusal(repo) is None          # warms the root cache
    calls = _count_lexists(monkeypatch)
    assert fence.refusal(repo) is None
    assert len(calls) == 2
    assert (sorted(p.name for p in repo.iterdir()), sorted(p.name for p in (repo / ".git").iterdir())) == before


def test_subdirectory_costs_four_probes(repo, monkeypatch):
    fence.refusal(repo / "sub")
    calls = _count_lexists(monkeypatch)
    assert fence.refusal(repo / "sub") is None
    assert len(calls) == 4


def test_fence_roots(repo):
    key = lambda p: Path(os.path.normcase(os.path.abspath(p)))   # noqa: E731
    assert fence.fence_roots(repo) == (key(repo),)
    assert fence.fence_roots(repo / "sub" / "deeper") == (key(repo / "sub" / "deeper"), key(repo))
    assert fence.fence_roots(str(repo / "sub") + os.sep) == (key(repo / "sub"), key(repo))


def test_fence_roots_of_a_linked_worktree_include_the_main_checkout(repo, tmp_path):
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c", cwd=repo)
    linked = tmp_path / "linked"
    git("worktree", "add", "-q", str(linked), cwd=repo)
    roots = fence.fence_roots(linked)
    assert roots[-1] == Path(os.path.normcase(os.path.abspath(repo)))


def test_fence_roots_outside_git_are_the_path_alone(tmp_path):
    if not _outside_git(tmp_path):
        pytest.skip("the test temp dir is inside a git repository")
    assert fence.fence_roots(tmp_path) == (Path(os.path.normcase(os.path.abspath(tmp_path))),)


def test_fence_roots_are_cached(repo, monkeypatch):
    fence.fence_roots(repo / "sub")

    def forbidden(start):
        raise AssertionError("asked git again")
    monkeypatch.setattr(fence, "git_common_root_checked", forbidden)
    assert len(fence.fence_roots(repo / "sub")) == 2


def test_failed_git_answer_is_not_cached_and_walks_for_git(repo, monkeypatch):
    asked = []

    def failing(start):
        asked.append(start)
        return None, False
    monkeypatch.setattr(fence, "git_common_root_checked", failing)
    roots = fence.fence_roots(repo / "sub" / "deeper")
    assert roots[-1] == Path(os.path.normcase(os.path.abspath(repo)))
    fence.fence_roots(repo / "sub" / "deeper")
    assert len(asked) == 2


def test_a_path_not_created_yet_is_not_cached(repo):
    future = repo / "sub" / "not-yet"
    assert fence.fence_roots(future)[-1] == Path(os.path.normcase(os.path.abspath(repo)))
    assert fence._key(future) not in fence._ROOTS


# -- refusals ------------------------------------------------------------------------------------------

def test_marker_at_the_repo_root_refuses_a_subdirectory_and_latches(repo):
    mark(repo)
    text = fence.refusal(repo / "sub" / "deeper")
    assert text is not None and URL in text and "Nothing was changed" in text
    assert sm.read_latch(repo) == sm.read_marker(repo)
    with pytest.raises(fence.ServerModeRefused) as caught:
        fence.check(repo / "sub")
    assert caught.value.kind == "marker"


def test_unreadable_marker_refuses(repo):
    (repo / sm.MARKER_NAME).write_text("{not json", encoding="utf-8")
    text = fence.refusal(repo)
    assert text is not None and "can't be used" in text
    with pytest.raises(fence.ServerModeRefused) as caught:
        fence.check(repo)
    assert caught.value.kind == "unreadable"
    assert not sm.latch_path(repo).exists()


def test_latch_alone_refuses_with_its_own_text(repo):
    mark(repo)
    fence.refusal(repo)
    (repo / sm.MARKER_NAME).unlink()
    text = fence.refusal(repo / "sub")
    assert "has seen Taskmaster server mode" in text and URL in text and "clear-latch" in text


def test_unreadable_latch_refuses_with_unknown_url(repo):
    sm.latch_path(repo).write_text("garbage", encoding="utf-8")
    text = fence.refusal(repo)
    assert "has seen Taskmaster server mode (unknown)" in text


def test_server_mode_refused_is_an_unsupported_store_error():
    from taskmaster.admission import UnsupportedStoreError
    assert issubclass(fence.ServerModeRefused, UnsupportedStoreError)


# -- the lease -----------------------------------------------------------------------------------------

def test_lease_exempts_every_spelling_of_its_root_and_nothing_else(repo, tmp_path, monkeypatch):
    project(repo)
    mark(repo)
    other = project(tmp_path / "other")
    git("init", "-q", cwd=other)
    mark(other)
    with fence.claim_host(repo) as lease:
        assert lease.held()
        monkeypatch.chdir(repo.parent)
        spellings = [repo, str(repo), str(repo) + os.sep, Path("repo"), repo / "sub" / "deeper"]
        if os.name == "nt":
            spellings.append(Path(str(repo).upper()))
        for spelling in spellings:
            assert fence.exempt(spelling), spelling
            assert fence.refusal(spelling) is None
        assert not fence.exempt(other)
        assert fence.refusal(other) is not None
    assert not fence.exempt(repo)
    assert fence.refusal(repo) is not None


def test_second_claim_in_this_process_is_refused(repo):
    project(repo)
    with fence.claim_host(repo):
        started = time.monotonic()
        with pytest.raises(fence.HostLeaseHeld):
            fence.claim_host(repo)
        assert time.monotonic() - started >= fence.CLAIM_RETRY_SECONDS * 0.9


CLAIM = ("from taskmaster import server_fence as f; import sys\n"
         "try:\n    f.claim_host(sys.argv[1]); print('claimed')\n"
         "except f.HostLeaseHeld:\n    print('held')\n")


def _claim_in_subprocess(root):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    code = f"import sys; sys.path.insert(0, {str(PLUGIN_ROOT)!r})\n" + CLAIM
    done = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=60,
                          env=environment, cwd=str(PLUGIN_ROOT))
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_lease_is_exclusive_across_processes(repo):
    project(repo)
    lease = fence.claim_host(repo)
    try:
        assert _claim_in_subprocess(repo) == "held"
        assert fence.host_lease_held(repo)
    finally:
        lease.release()
    assert _claim_in_subprocess(repo) == "claimed"
    assert not fence.host_lease_held(repo)


def test_a_probe_racing_the_claim_does_not_make_it_fail(repo):
    project(repo)
    fence.Ownership(repo, fence.SERVE_DIR, fence.HOST_LOCK)     # create the lock file the probe opens
    with fence.claim_host(repo):
        pass
    stop = threading.Event()

    def probe():
        while not stop.is_set():
            fence.host_lease_held(repo)
    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            fence.claim_host(repo).release()
    finally:
        stop.set()
        thread.join(5)


GRANDPARENT = """
import os, subprocess, sys
sys.path.insert(0, {plugin!r})
from taskmaster import server_fence as f
f.claim_host(sys.argv[1])
flags = {{'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}} if os.name == 'nt' else {{'start_new_session': True}}
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
print(child.pid, flush=True)
"""


def _kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30, **_GIT_FLAGS)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_the_lease_dies_with_its_holder_not_with_a_child(repo):
    # Fails if the lock descriptor were inherited: the grandchild would keep the lease alive.
    project(repo)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    done = subprocess.run([sys.executable, "-c", GRANDPARENT.format(plugin=str(PLUGIN_ROOT)), str(repo)],
                          capture_output=True, text=True, timeout=60, env=environment, cwd=str(PLUGIN_ROOT))
    assert done.returncode == 0, done.stderr
    grandchild = int(done.stdout.strip())
    try:
        assert _claim_in_subprocess(repo) == "claimed"
    finally:
        _kill(grandchild)


def test_lease_is_bound_to_its_pid(repo, monkeypatch):
    project(repo)
    mark(repo)
    with fence.claim_host(repo):
        assert fence.exempt(repo)
        real = os.getpid()
        monkeypatch.setattr(fence.os, "getpid", lambda: real + 1)
        assert not fence.exempt(repo)
        assert fence.refusal(repo) is not None
        monkeypatch.setattr(fence.os, "getpid", lambda: real)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork")
def test_a_forked_child_is_not_exempt(repo):
    project(repo)
    mark(repo)
    with fence.claim_host(repo):
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            os.write(write, b"refused" if fence.refusal(repo) is not None else b"exempt")
            os._exit(0)
        os.waitpid(pid, 0)
        assert os.read(read, 16) == b"refused"


def test_environment_never_grants_the_exemption(repo):
    # A regression guard, not the proof: the exemption has no environment input at all.
    project(repo)
    mark(repo)
    done = run_python(f"from taskmaster import server_fence as f; print(f.refusal({str(repo)!r}) is not None)",
                      env={"TASKMASTER_SERVE": "1", "TASKMASTER_ROOT": str(repo)})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True"


def test_claim_needs_a_local_store_directory(repo):
    with pytest.raises(FileNotFoundError):
        fence.claim_host(repo)


# -- host id -------------------------------------------------------------------------------------------

def test_ensure_host_id(repo):
    project(repo)
    first = fence.ensure_host_id(repo)
    assert len(first) == 32 and int(first, 16) >= 0
    assert fence.ensure_host_id(repo) == first
    assert fence.read_host_id(repo) == first
    rotated = fence.ensure_host_id(repo, rotate=True)
    assert rotated != first and fence.read_host_id(repo) == rotated
    names = sorted(p.name for p in (repo / ".taskmaster" / "local" / "serve").iterdir())
    assert names == ["host-id"]
    if os.name != "nt":
        assert fence.host_id_path(repo).stat().st_mode & 0o077 == 0
    else:
        from taskmaster.coordinator.ownership import verify_private
        verify_private(fence.host_id_path(repo).parent)


def test_malformed_host_id_is_replaced(repo):
    project(repo)
    fence.ensure_host_id(repo)
    fence.host_id_path(repo).write_text("nope\n", encoding="ascii")
    assert fence.read_host_id(repo) is None
    assert len(fence.ensure_host_id(repo)) == 32
