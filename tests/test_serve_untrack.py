# User intent: prove the 014 untrack transform (team-server-014 Step 4) on disposable repositories: a plumbing commit that
# changes exactly three root entries, never touches a working file or runs a hook, moves refs by compare-and-swap, stays
# inside its argv allowlist, and a host HEAD guard; plus executable documentation of what teammates' and the host's
# clones do afterwards (design §10).
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.serve import gitrun, untrack

HOST_ID = "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c"
URL = "https://tm.example.com/mcp"
MARKER = sm.Marker(URL, HOST_ID)
STORE_ID = "store-0001"
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@pytest.fixture(autouse=True)
def _fresh_fence():
    fence.reset_for_tests()
    yield
    fence.reset_for_tests()


def run(cwd, *args, check=True, stdin=None, env=None):
    done = subprocess.run(["git", *args], cwd=cwd, env=env or CLEAN_ENV, input=stdin, capture_output=True,
                          timeout=120, **FLAGS)
    if check and done.returncode:
        raise AssertionError(f"git {args} -> {done.returncode}: {done.stderr.decode(errors='replace')}")
    return done


def g(cwd, *args, check=True) -> str:
    return run(cwd, *args, check=check).stdout.decode("utf-8", "replace").strip()


def make_repo(path: Path, autocrlf: str, *, gitignore: bytes | None = b"node_modules/\n.taskmaster/local/\n") -> Path:
    path.mkdir(parents=True)
    run(path, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Tester"), ("user.email", "tester@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", autocrlf)):
        run(path, "config", key, value)
    (path / ".taskmaster" / "tasks").mkdir(parents=True)
    (path / ".taskmaster" / "local").mkdir()
    (path / ".taskmaster" / "backlog.yaml").write_bytes(b"backlog: 1\n")
    (path / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\n")
    (path / ".taskmaster" / "local" / "store.db").write_bytes(b"live store bytes")
    (path / ".taskmaster" / "local" / "PROGRESS.md").write_bytes(b"progress\r\n")
    (path / "code.txt").write_bytes(b"code\n")
    if gitignore is not None:
        (path / ".gitignore").write_bytes(gitignore)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", "initial")
    return path


def files_of(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def untrack_current(src: Path, *, record=None, sign=False):
    """The pack's own sequence for the checked-out branch: build, freeze file, move, sync."""
    git = untrack.git_for(src, record=record)
    branch = untrack.current_branch(git)
    old = untrack.tip(git, branch)
    new = untrack.build_branch_commit(git, branch, old, MARKER, STORE_ID, sign=sign)
    sm.write_marker(src, URL, HOST_ID)                  # the freeze writes the working copy before any ref moves
    untrack.move_ref(git, branch, new, old)
    untrack.sync_current(git)
    return git, branch, old, new


def root_names(src: Path, rev: str) -> dict[str, str]:
    out = g(src, "ls-tree", rev).splitlines()
    return {line.split("\t", 1)[1]: line.split("\t", 1)[0].split()[2] for line in out}


# -- the commit --------------------------------------------------------------------------------------

@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_the_commit_changes_exactly_three_root_entries(tmp_path, autocrlf):
    src = make_repo(tmp_path / "src", autocrlf)
    git = untrack.git_for(src)
    old = untrack.tip(git, "main")
    new = untrack.build_branch_commit(git, "main", old, MARKER, STORE_ID, sign=False)
    before, after = root_names(src, old), root_names(src, new)
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    assert changed == {".taskmaster", ".gitignore", ".taskmaster-server.json"}
    assert ".taskmaster" not in after and ".taskmaster-server.json" in after
    assert g(src, "rev-list", "--parents", "-n1", new).split() == [new, old]
    message = g(src, "log", "-1", "--format=%B", new)
    assert "Taskmaster-Migration: 1" in message and f"Taskmaster-Store: {STORE_ID}" in message
    assert untrack.tip(git, "main") == old                     # objects only: no ref moved yet


@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_after_the_sync_status_is_clean_and_no_file_changed(tmp_path, autocrlf):
    src = make_repo(tmp_path / "src", autocrlf)
    before = files_of(src)
    untrack_current(src)
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""
    after = files_of(src)
    assert set(after) - set(before) == {".taskmaster-server.json"}
    for rel, data in before.items():
        if rel != ".gitignore":
            assert after[rel] == data, rel
    assert after[".taskmaster-server.json"] == sm.render(MARKER).encode()
    assert b"/.taskmaster/" in after[".gitignore"]
    assert g(src, "ls-files", "--", ".taskmaster") == ""
    snapshot = files_of(src)
    untrack.sync_current(untrack.git_for(src))                # a second sync changes nothing
    assert files_of(src) == snapshot
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""


# -- .gitignore ----------------------------------------------------------------------------------------

def gitignore_after(tmp_path, content):
    src = make_repo(tmp_path / "src", "false", gitignore=content)
    git = untrack.git_for(src)
    old = untrack.tip(git, "main")
    new = untrack.build_branch_commit(git, "main", old, MARKER, STORE_ID, sign=False)
    blob = root_names(src, new)[".gitignore"]
    return run(src, "cat-file", "blob", blob).stdout, root_names(src, old).get(".gitignore"), blob


def test_gitignore_absent_is_created(tmp_path):
    data, before, _ = gitignore_after(tmp_path, None)
    assert before is None and data == b"/.taskmaster/\n"


def test_gitignore_crlf_gets_a_crlf_line(tmp_path):
    data, _, _ = gitignore_after(tmp_path, b"node_modules/\r\nbuild/\r\n")
    assert data == b"node_modules/\r\nbuild/\r\n/.taskmaster/\r\n"


def test_gitignore_without_a_trailing_newline_gets_one_first(tmp_path):
    data, _, _ = gitignore_after(tmp_path, b"node_modules/")
    assert data == b"node_modules/\n/.taskmaster/\n"


@pytest.mark.parametrize("spelling", [b".taskmaster", b".taskmaster/", b"/.taskmaster", b"/.taskmaster/"])
def test_gitignore_already_ignoring_keeps_its_blob(tmp_path, spelling):
    _, before, after = gitignore_after(tmp_path, b"x/\n" + spelling + b"\n")
    assert before == after


# -- other branches ------------------------------------------------------------------------------------

def make_other(src: Path, name="other"):
    run(src, "checkout", "-q", "-b", name)
    (src / "other.txt").write_bytes(b"other\n")
    (src / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\nother: yes\n")
    run(src, "add", "-A")
    run(src, "commit", "-q", "-m", name)
    run(src, "checkout", "-q", "main")
    (src / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\n")       # main's working copy again


def test_a_branch_that_is_not_checked_out_moves_without_touching_the_checkout(tmp_path):
    src = make_repo(tmp_path / "src", "true")
    make_other(src)
    git = untrack.git_for(src)
    old = untrack.tip(git, "other")
    assert untrack.branch_problem(git, src, "other", current="main") is None
    new = untrack.build_branch_commit(git, "other", old, MARKER, STORE_ID, sign=False)
    before = (files_of(src), (src / ".git" / "index").read_bytes(), g(src, "rev-parse", "HEAD"))
    untrack.move_ref(git, "other", new, old)
    assert untrack.tip(git, "other") == new
    assert (files_of(src), (src / ".git" / "index").read_bytes(), g(src, "rev-parse", "HEAD")) == before
    assert ".taskmaster" not in root_names(src, new)


def objects(src):
    return g(src, "count-objects", "-v")


def test_refused_branches_write_nothing(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    make_other(src)
    run(src, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wtb")
    run(src, "symbolic-ref", "refs/heads/alias", "refs/heads/other")
    # an export-shaped branch: a root tree with only backlog.yaml and export trailers
    blob = run(src, "hash-object", "-w", "--stdin", stdin=b"x: 1\n").stdout.decode().strip()
    tree = run(src, "mktree", stdin=f"100644 blob {blob}\tbacklog.yaml\n".encode()).stdout.decode().strip()
    commit = run(src, "commit-tree", tree, "-F", "-",
                 stdin=b"export\n\nTaskmaster-Export: 1\nTaskmaster-Store: s1\n").stdout.decode().strip()
    run(src, "branch", "exportish", commit)
    # a branch whose .taskmaster is a file
    blob2 = run(src, "hash-object", "-w", "--stdin", stdin=b"not a dir\n").stdout.decode().strip()
    tree2 = run(src, "mktree", stdin=f"100644 blob {blob2}\t.taskmaster\n".encode()).stdout.decode().strip()
    commit2 = run(src, "commit-tree", tree2, "-m", "odd").stdout.decode().strip()
    run(src, "branch", "oddball", commit2)
    git = untrack.git_for(src)
    before = objects(src)
    expect = {"main": "checked-out", "wtb": "worktree", "alias": "symbolic", "exportish": "export branch",
              "oddball": "other than a directory", "ghost": "does not exist"}
    for branch, text in expect.items():
        problem = untrack.branch_problem(git, src, branch, current="main")
        assert problem is not None and text in problem, (branch, problem)
    assert objects(src) == before


def test_a_tip_that_is_a_recorded_new_commit_is_accepted_as_it_stands(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    make_other(src)
    git = untrack.git_for(src)
    old = untrack.tip(git, "other")
    new = untrack.build_branch_commit(git, "other", old, MARKER, STORE_ID, sign=False)
    untrack.move_ref(git, "other", new, old)
    assert "already has" in untrack.branch_problem(git, src, "other", current="main")
    assert untrack.branch_problem(git, src, "other", current="main", owned=frozenset({new})) is None


def test_a_lost_compare_and_swap_leaves_the_ref_alone(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    make_other(src)
    git = untrack.git_for(src)
    old = untrack.tip(git, "other")
    new = untrack.build_branch_commit(git, "other", old, MARKER, STORE_ID, sign=False)
    run(src, "checkout", "-q", "other")
    (src / "later.txt").write_bytes(b"later\n")
    run(src, "add", "-A")
    run(src, "commit", "-q", "-m", "someone moved it")
    run(src, "checkout", "-q", "main")
    moved = untrack.tip(git, "other")
    with pytest.raises(untrack.LostRace):
        untrack.move_ref(git, "other", new, old)
    assert untrack.tip(git, "other") == moved


# -- signing (m6) --------------------------------------------------------------------------------------

def fake_gpg(tmp_path, body):
    script = tmp_path / "fake-gpg.sh"
    script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    os.chmod(script, 0o755)
    return script


def test_a_signing_failure_is_a_refusal_naming_the_fix(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    run(src, "config", "commit.gpgSign", "true")
    run(src, "config", "gpg.program", fake_gpg(tmp_path, "exit 1").as_posix())
    git = untrack.git_for(src)
    assert untrack.signing_enabled(git)
    old = untrack.tip(git, "main")
    with pytest.raises(untrack.SigningFailed, match="commit.gpgSign=false"):
        untrack.build_branch_commit(git, "main", old, MARKER, STORE_ID, sign=True)
    assert untrack.tip(git, "main") == old


def test_signing_variables_reach_the_signer_and_git_variables_do_not(tmp_path, monkeypatch):
    src = make_repo(tmp_path / "src", "false")
    record = tmp_path / "env.txt"
    run(src, "config", "commit.gpgSign", "true")
    run(src, "config", "gpg.program", fake_gpg(tmp_path, f'env > "{record.as_posix()}"\nexit 1').as_posix())
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gnupg"))
    monkeypatch.setenv("GIT_TRACE_SENTINEL", "leak")
    git = untrack.git_for(src)
    with pytest.raises(untrack.SigningFailed):
        untrack.build_branch_commit(git, "main", untrack.tip(git, "main"), MARKER, STORE_ID, sign=True)
    text = record.read_text(encoding="utf-8", errors="replace")
    assert "GNUPGHOME=" in text and "GIT_TRACE_SENTINEL" not in text


def test_gpgsign_off_builds_with_no_gpg_sign(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    git = untrack.git_for(src)
    assert not untrack.signing_enabled(git)


# -- no hook runs ----------------------------------------------------------------------------------------

def test_no_hook_runs_during_the_untrack(tmp_path):
    src = make_repo(tmp_path / "src", "true")
    sentinel = tmp_path / "hook-ran"
    for name in ("pre-commit", "post-index-change", "reference-transaction", "post-checkout", "commit-msg", "pre-push"):
        hook = src / ".git" / "hooks" / name
        hook.write_text(f'#!/bin/sh\necho {name} >> "{sentinel.as_posix()}"\n', encoding="utf-8", newline="\n")
        os.chmod(hook, 0o755)
    run(src, "branch", "control")
    run(src, "update-ref", "refs/heads/control", "HEAD")           # the control: a plain Git call does fire hooks
    assert sentinel.exists()
    sentinel.unlink()
    untrack_current(src)
    assert not sentinel.exists()


# -- the argv allowlist ----------------------------------------------------------------------------------

def test_every_call_is_on_the_list_and_nothing_else_can_run(tmp_path, monkeypatch):
    src = make_repo(tmp_path / "src", "false")
    record = []
    untrack_current(src, record=record)
    assert {argv[0] for argv, _ in record} <= {"rev-parse", "symbolic-ref", "ls-tree", "cat-file", "hash-object",
                                              "mktree", "commit-tree", "update-ref", "reset", "checkout-index",
                                              "config"}
    spawned = []
    monkeypatch.setattr(gitrun.managed, "probe", lambda *a, **k: spawned.append(a) or (0, b"", b""))
    git = untrack.git_for(src)
    forbidden = [("push", "origin", "main"), ("fetch",), ("pull",), ("rm", "-r", "--cached", "x"), ("reset", "--hard"),
                 ("reset", "-q", "HEAD", "--", "other"), ("checkout", "main"), ("switch", "main"), ("merge", "x"),
                 ("clean", "-fd"), ("stash",), ("commit", "-m", "x"), ("gc",),
                 ("update-ref", "-m", untrack.MOVE_MESSAGE, "refs/heads/unplanned", "a" * 40, "b" * 40),
                 ("checkout-index", "-a", "-f")]
    for argv in forbidden:
        with pytest.raises(untrack.UntrackRefused, match="argv refused"):
            git.run(*argv)
    assert spawned == []


# -- the host HEAD guard (D13) ---------------------------------------------------------------------------

def test_head_tracks_taskmaster_answers_for_old_and_retracked_commits(tmp_path):
    src = make_repo(tmp_path / "src", "false")
    _, _, old, new = untrack_current(src)
    assert untrack.head_tracks_taskmaster(src) is None
    run(src, "checkout", "-q", old)
    assert untrack.head_tracks_taskmaster(src) == old
    run(src, "checkout", "-q", "main")
    assert untrack.head_tracks_taskmaster(src) is None
    run(src, "checkout", "-q", "-b", "retrack")
    (src / ".taskmaster" / "tasks").mkdir(parents=True, exist_ok=True)
    (src / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\n")
    run(src, "add", "-f", ".taskmaster/tasks/t1.yaml")
    run(src, "commit", "-q", "-m", "re-adds it")
    retracked = g(src, "rev-parse", "HEAD")
    run(src, "checkout", "-q", "main")
    run(src, "merge", "-q", "--ff-only", "retrack")
    assert untrack.head_tracks_taskmaster(src) == retracked


def test_head_tracks_taskmaster_is_none_outside_a_repository(tmp_path):
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    assert untrack.head_tracks_taskmaster(tmp_path) is None


# -- teammates and the host, as executable documentation of §10 ------------------------------------------

@pytest.fixture
def world(tmp_path):
    base = tmp_path / "w"
    base.mkdir()
    src = make_repo(base / "src", "true")
    origin = base / "origin.git"
    run(base, "init", "-q", "--bare", "-b", "main", str(origin))
    run(src, "remote", "add", "origin", str(origin))
    run(src, "push", "-q", "-u", "origin", "main")
    mates = {}
    for name in ("mate", "mate2", "mate3", "host"):
        run(base, "clone", "-q", str(origin), name)
        for key, value in (("user.name", "M"), ("user.email", "m@example.invalid"), ("core.autocrlf", "true")):
            run(base / name, "config", key, value)
        mates[name] = base / name
    return base, src, origin, mates


def pack_and_push(src):
    _, _, old, new = untrack_current(src)
    run(src, "push", "-q", "origin", "main")
    return old, new


def test_a_clean_teammate_pull_removes_the_tracked_files_and_keeps_local(world):
    base, src, _, mates = world
    mate = mates["mate"]
    (mate / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)
    (mate / ".taskmaster" / "local" / "store.db").write_bytes(b"mate db")
    pack_and_push(src)
    run(mate, "pull", "-q")
    left = sorted(p.relative_to(mate / ".taskmaster").as_posix() for p in (mate / ".taskmaster").rglob("*") if p.is_file())
    assert left == ["local/store.db"]


def test_a_teammate_with_a_modified_tracked_file_cannot_pull(world):
    _, src, _, mates = world
    mate = mates["mate2"]
    (mate / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\nedited: yes\n")
    pack_and_push(src)
    assert run(mate, "pull", "-q", check=False).returncode != 0


def test_a_feature_branch_merging_the_new_main_conflicts_modify_delete(world):
    _, src, _, mates = world
    mate = mates["mate3"]
    run(mate, "checkout", "-q", "-b", "feature")
    (mate / ".taskmaster" / "tasks" / "t1.yaml").write_bytes(b"id: t1\nfeature: 1\n")
    run(mate, "commit", "-q", "-am", "feature touches the backlog")
    pack_and_push(src)
    run(mate, "fetch", "-q", "origin")
    merge = run(mate, "merge", "--no-edit", "origin/main", check=False)
    assert merge.returncode != 0 and b"modify/delete" in merge.stdout
    assert "UD .taskmaster/tasks/t1.yaml" in g(mate, "status", "--porcelain=v1").splitlines()


def test_checking_out_an_old_commit_brings_the_files_back_and_the_latch_still_refuses(world):
    _, src, _, mates = world
    mate = mates["mate"]
    old, _ = pack_and_push(src)
    run(mate, "pull", "-q")
    assert "Taskmaster server" in fence.refusal(mate)             # the first refusal at the new commit latches the clone
    run(mate, "checkout", "-q", old)
    assert (mate / ".taskmaster" / "backlog.yaml").exists()
    fence.reset_for_tests()
    assert "has seen Taskmaster server mode" in fence.refusal(mate)


def test_i1_git_overwrites_ignored_live_files_when_a_tracking_commit_is_checked_out_or_pulled(world):
    base, src, origin, mates = world
    host = mates["host"]
    old, _ = pack_and_push(src)
    run(host, "pull", "-q")
    live = host / ".taskmaster" / "tasks" / "t1.yaml"
    (host / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)       # a served host always has local/
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"LIVE\n")
    assert g(host, "status", "--porcelain=v1", "--untracked-files=all") == ""      # ignored, as designed
    assert untrack.head_tracks_taskmaster(host) is None
    # (a) a checkout of the parent overwrites the ignored file, exit code 0
    assert run(host, "checkout", "-q", old).returncode == 0
    assert live.read_bytes() != b"LIVE\n"
    run(host, "checkout", "-q", "main")
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"LIVE\n")
    # (b) a fast-forward pull of an upstream that re-tracks it does the same
    run(src, "checkout", "-q", "main")
    run(src, "add", "-f", ".taskmaster/tasks/t1.yaml")
    run(src, "commit", "-q", "-m", "re-track")
    run(src, "push", "-q", "origin", "main")
    assert run(host, "pull", "--ff-only", "-q").returncode == 0
    assert live.read_bytes() != b"LIVE\n"
    assert untrack.head_tracks_taskmaster(host) is not None
