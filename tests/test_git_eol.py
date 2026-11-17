# User intent (tm-audit-039): Taskmaster files under .taskmaster/ must be written in the line endings
# Git would check out, so `git status` stays clean after exports; machine-local tracked files are reported.
"""Real-git tests for `taskmaster.git_eol` and its use by the exporters (disposable temp repos only)."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest
import yaml

from taskmaster import git_eol, store
from taskmaster import taskmaster_v3 as v3

pytestmark = pytest.mark.slow


def git(root, *args, check=True):
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if check and done.returncode:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


@pytest.fixture(autouse=True)
def _fresh():
    git_eol.reset_for_tests()
    yield
    git_eol.reset_for_tests()


def make_repo(root, config=(), attributes=None):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "T"), ("user.email", "t@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false"), *config):
        git(root, "config", key, value)
    if attributes is not None:
        (root / ".gitattributes").write_bytes(attributes.encode())
    (root / ".taskmaster").mkdir(exist_ok=True)
    return root


def answer(root, name="tasks/x-001.md"):
    git_eol.reset_for_tests()
    return git_eol.line_ending(root / ".taskmaster", root / ".taskmaster" / name)


native = "crlf" if sys.platform == "win32" else "lf"


@pytest.mark.parametrize("config,attributes,expected", [
    ((), None, None),
    ((("core.autocrlf", "true"),), None, "crlf"),
    ((("core.autocrlf", "input"),), None, "lf"),
    ((("core.autocrlf", "false"),), "* text=auto\n", native),
    ((("core.autocrlf", "false"), ("core.eol", "lf")), "* text=auto\n", "lf"),
    ((("core.autocrlf", "false"), ("core.eol", "crlf")), "* text\n", "crlf"),
    ((("core.autocrlf", "true"),), "* text=auto\n", "crlf"),
    ((("core.autocrlf", "input"),), "* text=auto\n", "lf"),
    ((), "* text=auto eol=lf\n", "lf"),
    ((("core.autocrlf", "true"),), "* text eol=lf\n", "lf"),
    ((), "* text eol=crlf\n", "crlf"),
    ((("core.autocrlf", "true"),), "* -text\n", None),
    ((("core.autocrlf", "true"),), "* binary\n", None),
    ((("core.autocrlf", "true"),), "* -text eol=lf\n", None),
    ((), "*.md text\n", native),
])
def test_matrix_matches_git(tmp_path, config, attributes, expected):
    root = make_repo(tmp_path / "r", config, attributes)
    assert answer(root) == expected
    # and agrees with what Git really does: check out a committed LF file and read the bytes
    if expected in ("lf", "crlf"):
        path = root / ".taskmaster" / "tasks" / "x-001.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"a\nb\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "c")
        path.unlink()
        git(root, "checkout", "--", ".")
        want = b"a\r\nb\r\n" if expected == "crlf" else b"a\nb\n"
        assert path.read_bytes() == want


def test_index_blob_with_crlf_is_respected(tmp_path):
    root = make_repo(tmp_path / "r")
    path = root / ".taskmaster" / "tasks" / "x-001.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a\r\nb\r\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    (root / ".gitattributes").write_bytes(b"* text=auto\n")
    assert "i/crlf" in git(root, "ls-files", "--eol", ".taskmaster/tasks/x-001.md")
    git(root, "config", "core.eol", "lf")
    assert answer(root) == "crlf"


def test_not_a_git_repo_has_no_opinion(tmp_path):
    (tmp_path / ".taskmaster").mkdir()
    assert git_eol.line_ending(tmp_path / ".taskmaster", tmp_path / ".taskmaster" / "a.md") is None
    assert git_eol.tracked_local_files(tmp_path / ".taskmaster") == []


def test_git_failure_and_timeout_fall_back_to_none(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", (("core.autocrlf", "true"),))
    assert answer(root) == "crlf"
    git_eol.reset_for_tests()

    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired("git", 1)
    monkeypatch.setattr(git_eol, "run_bounded", timeout)
    assert git_eol.line_ending(root / ".taskmaster", root / ".taskmaster" / "a.md") is None
    # a failure is remembered briefly: no respawn storm
    calls = []
    monkeypatch.setattr(git_eol, "run_bounded", lambda *a, **k: calls.append(1))
    assert git_eol.line_ending(root / ".taskmaster", root / ".taskmaster" / "a.md") is None
    assert not calls


def test_cache_invalidates_when_gitattributes_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(git_eol, "_RECHECK_AFTER", 0.0)
    root = make_repo(tmp_path / "r", (), "* text eol=lf\n")
    target = root / ".taskmaster" / "a.md"
    assert git_eol.line_ending(root / ".taskmaster", target) == "lf"
    time.sleep(0.05)
    (root / ".gitattributes").write_bytes(b"* text eol=crlf\n")
    assert git_eol.line_ending(root / ".taskmaster", target) == "crlf"


def test_warm_answers_do_not_spawn_git(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", (), "* text eol=lf\n")
    backlog = root / ".taskmaster"
    assert git_eol.line_ending(backlog, backlog / "a.md") == "lf"
    calls = []
    real = git_eol.run_bounded
    monkeypatch.setattr(git_eol, "run_bounded", lambda *a, **k: calls.append(1) or real(*a, **k))
    start = time.perf_counter()
    for _ in range(200):
        assert git_eol.line_ending(backlog, backlog / "a.md") == "lf"
    per_call_ms = (time.perf_counter() - start) / 200 * 1000
    assert not calls
    print(f"warm line_ending: {per_call_ms:.3f} ms/call")
    assert per_call_ms < 5


# ---- exporter behaviour -------------------------------------------------------------------

TASK = ("---\nid: e-001\ntitle: T\nepic: e\nstatus: todo\npriority: high\norder: 1.0\n"
        "created: '2026-01-01T00:00'\n---\nbody\n")


def seed_project(root):
    tm = root / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_bytes(b"## Changelog\n")
    bp = tm / "backlog.yaml"
    bp.write_bytes(yaml.safe_dump({
        "version": 4, "project": "t", "meta": {"updated": "", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [{"id": "dev", "name": "Dev", "status": "active", "order": 1}],
    }, sort_keys=False).encode("utf-8"))
    store.reset_for_tests()
    return bp


def run_update(tmp_path, monkeypatch, root, bp, note):
    from taskmaster import backlog_server as bs
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))
    monkeypatch.setattr(bs, "ROOT", root)
    store.open_store(backlog_path=bp, session="eol")
    assert "Error" not in bs.backlog_update_task("e-001", "notes", note)


def commit_all(root):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "snapshot", "--allow-empty")


def test_attrs_lf_over_existing_crlf_file_writes_lf_and_status_clean(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", (), "* text=auto eol=lf\n")
    bp = seed_project(root)
    task = v3.task_file_path(bp, "e-001")
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_bytes(TASK.replace("\n", "\r\n").encode())
    (root / ".gitignore").write_bytes(b".taskmaster/local/\n")
    run_update(tmp_path, monkeypatch, root, bp, "first")
    assert b"\r\n" not in task.read_bytes()
    commit_all(root)
    run_update(tmp_path, monkeypatch, root, bp, "second")
    assert b"second" in task.read_bytes()
    assert b"\r\n" not in task.read_bytes()
    git(root, "add", "-N", ".")
    changed = git(root, "status", "--porcelain").splitlines()
    assert [line for line in changed if "tasks/" not in line] == []


def test_autocrlf_true_with_lf_file_writes_crlf_and_diff_has_only_real_changes(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", (("core.autocrlf", "true"),))
    (root / ".gitignore").write_bytes(b".taskmaster/local/\n")
    bp = seed_project(root)
    task = v3.task_file_path(bp, "e-001")
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_bytes(TASK.encode())          # an LF-written file in an autocrlf=true repo
    commit_all(root)
    run_update(tmp_path, monkeypatch, root, bp, "changed")
    written = task.read_bytes()
    assert written.count(b"\r\n") == written.count(b"\n") > 0
    # the only difference Git sees is the real content change
    diff = git(root, "diff", "--numstat", "--", ".taskmaster/tasks/e-001.md")
    assert diff.split()[:2] == ["1", "0"] or "changed" in git(root, "diff", ".taskmaster/tasks/e-001.md")
    assert "\r" not in git(root, "diff", "--", ".taskmaster/tasks/e-001.md").replace("\r\n", "\n")


def test_non_git_project_keeps_existing_style(tmp_path, monkeypatch):
    bp = seed_project(tmp_path)
    task = v3.task_file_path(bp, "e-001")
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_bytes(TASK.replace("\n", "\r\n").encode())
    run_update(tmp_path, monkeypatch, tmp_path, bp, "changed")
    written = task.read_bytes()
    assert b"changed" in written and written.count(b"\r\n") == written.count(b"\n") > 0


def test_meta_json_and_progress_follow_git(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", (), "* text eol=crlf\n")
    bp = seed_project(root)
    run_update_ok = None  # noqa: F841
    s = store.open_store(backlog_path=bp, session="meta")
    s.write_local_cache("meta.json", b'{\n  "a": 1\n}\n')
    cached = (bp.parent / "local" / "cache" / "meta.json").read_bytes()
    assert cached == b'{\r\n  "a": 1\r\n}\r\n'
    assert store.apply_git_line_endings(b"x\n", bp.parent / "local" / "PROGRESS.md", bp.parent) == b"x\r\n"


def test_native_projection_write_follows_git(tmp_path, monkeypatch):
    from native_git_helpers import init_repo
    from native_twins import make_twins
    from taskmaster import backlog_server as bs

    def seed():
        bs.backlog_add_task(title="Native task", epic="test-epic", phase="dev")
    twins = make_twins(tmp_path, monkeypatch, seed)
    with twins.at(twins.native):
        root = twins.native
        (root / ".gitattributes").write_bytes(b"* text=auto eol=crlf\n")
        init_repo(root)
        task = root / ".taskmaster" / "tasks" / "test-epic-001.md"
        assert "Error" not in bs.backlog_update_task(task_id="test-epic-001", field="tldr", value="native change")
        data = task.read_bytes()
        assert b"native change" in data
        assert data.count(b"\r\n") == data.count(b"\n") > 0
        numstat = git(root, "diff", "--numstat", "--", ".taskmaster/tasks/test-epic-001.md").split()
        assert numstat and int(numstat[0]) + int(numstat[1]) <= 4, numstat  # real edits only, no whole-file EOL rewrite
        others = git(root, "status", "--porcelain", "--", ".taskmaster/tasks/test-epic-002.md")
        assert others.strip() == ""


# ---- B-099 --------------------------------------------------------------------------------

def test_tracked_local_files_are_reported_once_and_never_untracked(tmp_path):
    root = make_repo(tmp_path / "r")
    local = root / ".taskmaster" / "local"
    local.mkdir()
    for name in ("store.db", "a.json", "b.json", "c.json"):
        (local / name).write_bytes(b"x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "oops")
    line = git_eol.tracked_local_files(root / ".taskmaster")
    assert len(line) == 4

    class Status:
        db_path = local / "store.db"

    text = git_eol_report(Status, root)
    assert "Tracked local files: 4" in text
    assert "git rm -r --cached .taskmaster/local" in text
    assert "+1 more" in text
    assert len(git(root, "ls-files", ".taskmaster/local").split()) == 4  # untouched


def git_eol_report(status, root):
    from taskmaster.backlog_server import _tracked_local_line
    return _tracked_local_line(root / ".taskmaster")


def test_no_tracked_local_line_when_ignored(tmp_path):
    from taskmaster.backlog_server import _tracked_local_line
    root = make_repo(tmp_path / "r")
    (root / ".taskmaster" / "local").mkdir()
    (root / ".taskmaster" / "local" / ".gitignore").write_bytes(b"*\n")
    (root / ".taskmaster" / "local" / "store.db").write_bytes(b"x")
    (root / "README.md").write_bytes(b"x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ok")
    assert _tracked_local_line(root / ".taskmaster") == ""


# ---- review-gate fixes --------------------------------------------------------------------

def _commit_file(root, name, data, attributes=None):
    path = root / ".taskmaster" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    return path


@pytest.mark.parametrize("data", [b"a\nb\0\n", b"a\r\nb\nc\r\n"])
def test_binary_or_mixed_blob_is_not_converted_under_autocrlf(tmp_path, data):
    root = make_repo(tmp_path / "r")
    _commit_file(root, "tasks/x-001.md", data)
    git(root, "config", "core.autocrlf", "true")
    assert answer(root) is None
    (root / ".gitattributes").write_bytes(b"* text=auto\n")
    assert answer(root) is None


def test_text_set_still_converts_a_binary_looking_blob(tmp_path):
    root = make_repo(tmp_path / "r")
    _commit_file(root, "tasks/x-001.md", b"a\nb\0\n")
    (root / ".gitattributes").write_bytes(b"* text\n")
    git(root, "config", "core.autocrlf", "true")
    assert answer(root) == "crlf"


def test_crlf_blob_without_conversion_checks_out_crlf(tmp_path):
    root = make_repo(tmp_path / "r")
    _commit_file(root, "tasks/x-001.md", b"a\r\nb\r\n")
    assert "i/crlf" in git(root, "ls-files", "--eol")
    assert answer(root) == "crlf"


def test_bare_autocrlf_key_means_true(tmp_path):
    root = make_repo(tmp_path / "r")
    with open(root / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        handle.write("[core]\n\tautocrlf\n")
    assert git(root, "config", "--type=bool", "core.autocrlf").strip() == "true"
    assert answer(root) == "crlf"


def test_included_config_changes_invalidate(tmp_path, monkeypatch):
    monkeypatch.setattr(git_eol, "_RECHECK_AFTER", 0.0)
    root = make_repo(tmp_path / "r")
    extra = tmp_path / "extra.gitconfig"
    extra.write_bytes(b"[core]\n\tautocrlf = true\n")
    git(root, "config", "include.path", extra.as_posix())
    backlog = root / ".taskmaster"
    assert git_eol.line_ending(backlog, backlog / "a.md") == "crlf"
    time.sleep(0.05)
    extra.write_bytes(b"[core]\n\tautocrlf = input\n")
    assert git_eol.line_ending(backlog, backlog / "a.md") == "lf"


def test_nested_gitattributes_changes_invalidate(tmp_path, monkeypatch):
    monkeypatch.setattr(git_eol, "_RECHECK_AFTER", 0.0)
    root = make_repo(tmp_path / "r")
    nested = root / ".taskmaster" / "tasks"
    nested.mkdir(parents=True)
    (nested / ".gitattributes").write_bytes(b"* text eol=lf\n")
    target = nested / "a.md"
    assert git_eol.line_ending(root / ".taskmaster", target) == "lf"
    time.sleep(0.05)
    (nested / ".gitattributes").write_bytes(b"* text eol=crlf\n")
    assert git_eol.line_ending(root / ".taskmaster", target) == "crlf"


def test_update_root_config_follows_git(tmp_path):
    root = make_repo(tmp_path / "r", (), "* text eol=crlf\n")
    bp = seed_project(root)
    s = store.open_store(backlog_path=bp, session="cfg")
    s.update_root_config("linear.yaml", lambda cur: {**cur, "a": 1, "b": 2})
    data = (bp.parent / "linear.yaml").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") == 2


def test_init_text_writer_follows_git_and_falls_back(tmp_path):
    from taskmaster.backlog_server import _write_text_git
    root = make_repo(tmp_path / "r", (), "* text eol=crlf\n")
    target = root / ".taskmaster" / "taskmaster.json"
    _write_text_git(target, "{\n}\n", root / ".taskmaster")
    assert target.read_bytes() == b"{\r\n}\r\n"
    plain = tmp_path / "plain"
    plain.mkdir()
    other = plain / "x.json"
    _write_text_git(other, "{\n}\n", plain)
    assert other.read_bytes() == "{\n}\n".replace("\n", "\r\n" if sys.platform == "win32" else "\n").encode()


def test_slow_git_in_one_repo_does_not_block_another(tmp_path, monkeypatch):
    import threading
    slow = make_repo(tmp_path / "slow", (("core.autocrlf", "true"),))
    fast = make_repo(tmp_path / "fast", (("core.autocrlf", "true"),))
    real = git_eol.run_bounded
    entered, release = threading.Event(), threading.Event()

    def gated(args, **kwargs):
        if str(kwargs.get("cwd", "")).startswith(str(slow.resolve())):
            entered.set()
            release.wait(10)
        return real(args, **kwargs)

    monkeypatch.setattr(git_eol, "run_bounded", gated)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault(
        "slow", git_eol.line_ending(slow / ".taskmaster", slow / ".taskmaster" / "a.md")))
    worker.start()
    assert entered.wait(10)
    started = time.perf_counter()
    assert git_eol.line_ending(fast / ".taskmaster", fast / ".taskmaster" / "a.md") == "crlf"
    assert time.perf_counter() - started < 5
    release.set()
    worker.join(10)
    assert result["slow"] == "crlf"
