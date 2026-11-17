# User intent: prove `serve init pack` (team-server-014 Step 5): the happy path leaves the laptop fenced and the bundle
# whole, every pre-flight refusal writes nothing, a late write is refused rather than drained (C1), and a commit that
# slips past the write gate is caught by the read-only re-check before any ref moves.
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.coordinator.protocol import ServiceUnavailable
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve import bundle as bundles
from taskmaster.serve import migration, untrack
from taskmaster.serve import state_export as se
from taskmaster.serve.config import ServeRefused, ServeUnavailable

import migration_helpers as mh
from migration_helpers import URL, envelope, fingerprint, g, run
from test_native_service import root  # noqa: F401  (fixture)

pytestmark = pytest.mark.xdist_group("heavy_processes")


@pytest.fixture(autouse=True)
def _fresh_fence(monkeypatch):
    fence.reset_for_tests()
    monkeypatch.setattr(migration, "CHECKPOINT", lambda stage: None)
    yield
    leaked = fence.reset_for_tests()
    assert not leaked, f"leaked host leases: {[lease.root for lease in leaked]}"


@pytest.fixture
def src(root):  # noqa: F811
    return mh.make_source(root)


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out" / "proj.tmbundle"


@pytest.fixture(autouse=True)
def _out_parent(tmp_path):
    (tmp_path / "out").mkdir()


def do_pack(src, out, **kwargs):
    return migration.pack(src, url=URL, out=out, **kwargs)


def refused(src, out, code=2, match=None, clean=True, **kwargs):
    before = fingerprint(src)
    with pytest.raises(ServeRefused, match=match) as caught:
        do_pack(src, out, **kwargs)
    assert caught.value.exit_code == code, caught.value
    assert fingerprint(src) == before
    assert not Path(out).exists()
    if clean:
        assert not sm.marker_path(src).exists() and not sm.latch_path(src).exists()
    return caught.value


# -- the happy path ------------------------------------------------------------------------------------

def test_pack_writes_the_bundle_the_marker_the_latch_and_the_commit(src, out):
    old = g(src, "rev-parse", "HEAD")
    store_id = None
    report = do_pack(src, out)
    manifest = mh.manifest_of(out)
    with bundles.open_bundle(out) as opened:
        store_id = opened.manifest["store_id"]
    assert report["state"] == "packed" and report["store_id"] == store_id
    assert manifest["high_seq"] == report["high_seq"] == mh.store_events(src)
    assert manifest["marker"]["url"] == URL and manifest["current_branch"] == "main"
    assert manifest["schema_version"] in (3, 4, 5) and manifest["counts"].get("task", 0) >= 1
    assert manifest["counts"].get("external_document") == 1
    assert sm.read_marker(src).host_id == manifest["marker"]["host_id"] and sm.read_latch(src) is not None
    assert fence.read_host_id(src) is None                          # the laptop can never run `taskmaster serve`
    new = g(src, "rev-parse", "HEAD")
    assert new != old and g(src, "rev-list", "--parents", "-n1", new).split() == [new, old]
    assert manifest["commits"] == {"main": new}
    assert g(src, "ls-files", "--", ".taskmaster") == ""
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""
    journal = migration.load_journal(src, migration.PACK_JOURNAL)
    assert journal["stage"] == "done" and journal["high"] == manifest["high_seq"]
    assert journal["gitignore_before_sha256"] is not None
    assert migration.sha256_path(out) == report["bundle_sha256"]
    assert "rollout_rule" in report and "push_rejected_rule" in report and "single_use" in report
    # the second call prints the same report (stage done)
    assert do_pack(src, out) == report


def test_not_migrated_lists_name_tracked_and_local_extras(src, out):
    report = do_pack(src, out)
    assert "harvests/x.md" in report["not_migrated"]["tracked"]
    assert "viewer.json" in report["not_migrated"]["local"]
    assert mh.manifest_of(out)["not_migrated"] == report["not_migrated"]


def test_after_pack_the_laptop_is_fenced_in_a_fresh_process(src, out):
    do_pack(src, out)
    code = ("import sys, pathlib; sys.path.insert(0, %r)\n"
            "from taskmaster.coordinator import protocol\n"
            "from taskmaster import server_fence as f\n"
            "try:\n protocol.connect(pathlib.Path(%r), readonly=True)\nexcept f.ServerModeRefused: print('REFUSED')\n"
            % (str(mh.PLUGIN), str(src)))
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert "REFUSED" in done.stdout, done.stdout + done.stderr
    served = mh.cli("serve", "--root", str(src), "--port", "0", "--no-viewer", timeout=120)
    assert served.returncode == 2, served.stdout + served.stderr


# -- refusals write nothing ----------------------------------------------------------------------------

def test_p1_not_a_git_top_level_a_linked_worktree_or_a_separate_git_dir(src, out, tmp_path):
    sub = src / "pkg"
    (sub / ".taskmaster" / "local").mkdir(parents=True)
    assert "top level" in str(refused(sub, out))
    linked = tmp_path / "linked"
    run(src, "worktree", "add", "-q", str(linked), "-b", "side")
    (linked / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)
    assert "linked worktree" in str(refused(linked, out))
    separate = tmp_path / "separate"
    separate.mkdir()
    run(separate, "init", "-q", "--separate-git-dir", str(tmp_path / "sep.git"))
    (separate / ".taskmaster" / "local").mkdir(parents=True)
    assert ".git that isn't a directory" in str(refused(separate, out))


def test_p2_a_marker_or_a_latch_means_already_in_server_mode(src, out):
    sm.write_marker(src, URL, "ab" * 16)
    refused(src, out, match="already in server mode", clean=False)
    sm.marker_path(src).unlink()
    sm.record_latch(src, sm.Marker(URL, "ab" * 16))
    refused(src, out, match="already in server mode", clean=False)


def test_p3_detached_head_and_operations_in_progress(src, out):
    (src / ".git" / "MERGE_HEAD").write_text(g(src, "rev-parse", "HEAD") + "\n")
    refused(src, out, match="in progress")
    (src / ".git" / "MERGE_HEAD").unlink()
    run(src, "checkout", "-q", "--detach")
    refused(src, out, match="detached")


def test_p4_gitignore_changes_and_a_stray_marker(src, out):
    (src / ".gitignore").write_text("edited\n", encoding="utf-8")
    refused(src, out, match=r"\.gitignore has")
    run(src, "checkout", "--", ".gitignore")
    (src / sm.MARKER_NAME).write_text("{}", encoding="utf-8")
    refused(src, out, match="server mode", clean=False)


def test_p5_no_store_and_a_store_that_is_not_native(src, out, tmp_path):
    bare = tmp_path / "nostore"
    bare.mkdir()
    run(bare, "init", "-q", "-b", "main")
    (bare / ".taskmaster" / "local").mkdir(parents=True)
    assert "no store" in str(refused(bare, out))
    with contextlib.closing(sqlite3.connect(bare / ".taskmaster" / "local" / "store.db")) as connection:
        connection.execute("CREATE TABLE meta(key TEXT, value TEXT)")
        connection.execute("INSERT INTO meta VALUES('schema_version','1')")
        connection.commit()
    assert "native" in str(refused(bare, out))


def test_pack_refuses_a_ladder_label_that_is_not_branch_like(src, out, monkeypatch):
    """027 D8: a label with whitespace or tags would reach hook text, so it is renamed before migrating."""
    monkeypatch.setattr(migration, "_ladder_label_problems", lambda store: ["release candidate"])
    error = refused(src, out, match="branch-like")
    assert "release candidate" in str(error)


def test_ladder_label_problems_reads_the_stored_manifest(tmp_path):
    store = tmp_path / "s.db"
    with contextlib.closing(sqlite3.connect(store)) as connection:
        connection.execute("CREATE TABLE entity_core(entity_key INTEGER PRIMARY KEY, kind TEXT, public_id TEXT)")
        connection.execute("CREATE TABLE configuration(entity_key INTEGER, field TEXT, value_json TEXT)")
        connection.execute("INSERT INTO entity_core VALUES(1,'project','__project__')")
        labels = [{"label": "main"}, {"label": "release candidate"}, {"label": 'a"b'}]
        connection.execute("INSERT INTO configuration VALUES(1,'conventions',?)",
                           (json.dumps({"policies": {"merge_targets": labels}}),))
        connection.commit()
    assert migration._ladder_label_problems(store) == ["release candidate", 'a"b']


def test_p6_bad_url(src, out):
    with pytest.raises(ServeRefused, match="--url"):
        migration.pack(src, url="http://example.com/mcp", out=out)
    assert not sm.marker_path(src).exists()


def test_p7_out_exists_has_no_parent_or_is_inside_the_repo(src, out, tmp_path):
    out.write_bytes(b"x")
    refused_text = str(pytest.raises(ServeRefused, migration.pack, src, url=URL, out=out).value)
    assert "already exists" in refused_text and out.read_bytes() == b"x"
    out.unlink()
    with pytest.raises(ServeRefused, match="doesn't exist"):
        migration.pack(src, url=URL, out=tmp_path / "nowhere" / "x.tmbundle")
    with pytest.raises(ServeRefused, match="outside the repository"):
        migration.pack(src, url=URL, out=src / "x.tmbundle")
    assert not sm.marker_path(src).exists()


def test_p8_no_git_identity(src, out):
    run(src, "config", "user.name", "")
    run(src, "config", "user.email", "")
    refused(src, out, match="identity")


def test_p9_unsupported_repository_formats(src, out, monkeypatch):
    real = untrack.UntrackGit.text

    def text(self, *args, **kwargs):
        if args[:1] == ("rev-parse",) and args[1:] == ("--show-object-format",):
            return 0, "sha256"
        return real(self, *args, **kwargs)
    monkeypatch.setattr(untrack.UntrackGit, "text", text)
    refused(src, out, match="sha256")


def test_p10_branch_problems_are_named(src, out):
    refused(src, out, match="does not exist", branches=("ghost",))
    refused(src, out, match="checked-out branch", branches=("main",))


def test_p11_a_branch_whose_taskmaster_is_a_file(src, out):
    entries = g(src, "ls-tree", "HEAD").splitlines()
    kept = [line for line in entries if not line.endswith("\t.taskmaster")]
    blob = run(src, "hash-object", "-w", "--stdin", stdin=b"file\n").stdout.decode().strip()
    kept.append(f"100644 blob {blob}\t.taskmaster")
    tree = run(src, "mktree", stdin=("\n".join(kept) + "\n").encode()).stdout.decode().strip()
    commit = run(src, "commit-tree", tree, "-p", "HEAD", "-m", "odd").stdout.decode().strip()
    run(src, "update-ref", "refs/heads/main", commit)
    refused(src, out, match="other than a directory")


def origin_of(src, tmp_path):
    origin = tmp_path / "origin.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    run(src, "remote", "add", "origin", str(origin))
    run(src, "push", "-q", "-u", "origin", "main")
    return origin


def test_p12_upstream_ahead_refuses_an_ancestor_passes_and_none_warns(src, out, tmp_path):
    origin = origin_of(src, tmp_path)
    mate = tmp_path / "mate"
    run(tmp_path, "clone", "-q", str(origin), str(mate))
    (mate / "teammate.txt").write_text("hi\n", encoding="utf-8")
    run(mate, "add", "-A")
    run(mate, "-c", "user.name=M", "-c", "user.email=m@example.invalid", "commit", "-q", "-m", "teammate")
    run(mate, "push", "-q", "origin", "main")
    run(src, "fetch", "-q", "origin")
    refused(src, out, match="behind its upstream")
    run(src, "merge", "-q", "--ff-only", "origin/main")
    report = do_pack(src, out)
    assert report["state"] == "packed" and not report["warnings"]


def test_p12_without_an_upstream_the_report_warns(src, out):
    report = do_pack(src, out)
    assert any("no upstream" in warning for warning in report["warnings"])


def test_a_hand_edited_projection_file_is_refused_at_settle_before_the_freeze(src, out):
    task = src / ".taskmaster" / "tasks" / f"{mh.TASK}.md"
    task.write_text(task.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")
    refused(src, out, match="differs from the store")


def test_the_host_lease_held_by_another_process_is_exit_69(src, out):
    code = ("import sys, time; sys.path.insert(0, %r)\n"
            "from taskmaster import server_fence as f\n"
            "lease = f.host_lease(%r)\nprint('HELD', flush=True)\nsys.stdin.readline()\n" % (str(mh.PLUGIN), str(src)))
    holder = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "HELD"
        error = refused(src, out, code=69, match="another pack is running")
        assert isinstance(error, ServeUnavailable)
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=30)


def test_a_session_coordinator_owning_the_store_is_exit_69(src, out):
    with Coordinator(src):
        error = refused(src, out, code=69, match="coordinator")
        assert isinstance(error, ServeUnavailable)


# -- C1 and the step-9 re-check ------------------------------------------------------------------------

def spy_owner(monkeypatch):
    captured = {}
    real = se.offline_owner

    @contextlib.contextmanager
    def spying(root, **kwargs):
        with real(root, **kwargs) as owner:
            captured.setdefault("owners", []).append(owner)
            yield owner
    monkeypatch.setattr(se, "offline_owner", spying)
    return captured


def test_c1_a_write_paused_at_the_snapshot_is_refused_and_refs_move_after_stop(src, out, monkeypatch):
    captured = spy_owner(monkeypatch)
    futures = []

    def checkpoint(stage):
        if stage == "snapshot_taken":
            futures.append(captured["owners"][-1].submit(envelope(captured["owners"][-1], "late-write", arguments={
                "id": mh.TASK, "set": {"title": "Late"}})))
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)
    moves = []
    real_move = untrack.move_ref

    def move(git, branch, new, old):
        owner = captured["owners"][-1]
        moves.append((owner.stopping.is_set(), any(t.name == "taskmaster-writer" and t.is_alive() for t in threading.enumerate())))
        return real_move(git, branch, new, old)
    monkeypatch.setattr(untrack, "move_ref", move)
    before = mh.store_events(src)
    report = do_pack(src, out)
    assert len(futures) == 1
    with pytest.raises(ServiceUnavailable, match="moved to the team server"):
        futures[0].result(timeout=30)
    assert mh.store_events(src) == before == report["high_seq"] == mh.manifest_of(out)["high_seq"]
    assert moves and all(stopped and not alive for stopped, alive in moves)


def test_a_commit_that_slips_past_the_gate_makes_the_pack_snapshot_again(src, out, monkeypatch):
    injected = []

    def checkpoint(stage):
        if stage == "snapshot_taken" and not injected:
            with contextlib.closing(sqlite3.connect(src / ".taskmaster" / "local" / "store.db", timeout=30)) as connection:
                connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op) "
                                   "VALUES('2026-11-07T00:00:00Z','raw','test','task','x','noop')")
                connection.commit()
            injected.append(mh.store_events(src))
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)
    report = do_pack(src, out)
    assert injected and report["high_seq"] == injected[0] == mh.manifest_of(out)["high_seq"]


# -- Step 6: resume and --undo --------------------------------------------------------------------------

class Crash(Exception):
    pass


def crash_at(monkeypatch, stage):
    def checkpoint(name):
        if name == stage:
            raise Crash(name)
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)


def no_crash(monkeypatch):
    monkeypatch.setattr(migration, "CHECKPOINT", lambda name: None)


def make_other(src, name="other"):
    run(src, "checkout", "-q", "-b", name)
    (src / "other.txt").write_bytes(b"other\n")
    run(src, "add", "-A")
    run(src, "commit", "-q", "-m", name)
    run(src, "checkout", "-q", "main")


def count_owners(monkeypatch):
    calls = []
    real = se.offline_owner

    @contextlib.contextmanager
    def counting(root, **kwargs):
        calls.append(1)
        with real(root, **kwargs) as owner:
            yield owner
    monkeypatch.setattr(se, "offline_owner", counting)
    return calls


STAGES = ["frozen", "snapshot", "commits_built", "bundled", "ref_moved:other", "ref_moved:main", "index_synced",
          "gitignore_synced"]
NO_COORDINATOR = {"bundled", "ref_moved:other", "ref_moved:main", "index_synced", "gitignore_synced"}


def assert_finished(src, out, old_main, old_other):
    manifest = mh.manifest_of(out)
    bundles.open_bundle(out).close()
    for name, old in (("main", old_main), ("other", old_other)):
        new = g(src, "rev-parse", f"refs/heads/{name}")
        assert manifest["commits"][name] == new and new != old
        assert g(src, "rev-list", "--parents", "-n1", new).split() == [new, old]
        assert ".taskmaster" not in g(src, "ls-tree", "--name-only", new).splitlines()
    assert g(src, "ls-files", "--", ".taskmaster") == ""
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert migration.load_journal(src, migration.PACK_JOURNAL)["stage"] == "done"


@pytest.mark.parametrize("stage", STAGES)
def test_a_rerun_after_a_crash_finishes_like_an_uninterrupted_pack(src, out, monkeypatch, stage):
    make_other(src)
    old_main, old_other = g(src, "rev-parse", "main"), g(src, "rev-parse", "other")
    crash_at(monkeypatch, stage)
    with pytest.raises(Crash):
        do_pack(src, out, branches=("other",))
    no_crash(monkeypatch)
    owners = count_owners(monkeypatch)
    report = do_pack(src, out, branches=("other",))
    assert report["state"] == "packed"
    assert_finished(src, out, old_main, old_other)
    if stage in NO_COORDINATOR:
        assert owners == []                       # R1: no coordinator, no settle once the bundle exists
    else:
        assert owners                             # before the bundle the store is settled again


def test_r2_a_crash_between_update_ref_and_reset_keeps_the_prepack_gitignore_and_is_accepted(src, out, monkeypatch):
    before = (src / ".gitignore").read_bytes()
    crash_at(monkeypatch, "ref_moved:main")
    with pytest.raises(Crash):
        do_pack(src, out)
    journal = migration.load_journal(src, migration.PACK_JOURNAL)
    assert (src / ".gitignore").read_bytes() == before
    assert migration.sha256_path(src / ".gitignore") == journal["gitignore_before_sha256"]
    no_crash(monkeypatch)
    do_pack(src, out)
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""


@pytest.mark.parametrize("stage", ["ref_moved:main", "gitignore_synced"])
def test_a_hand_edited_gitignore_is_refused_on_resume(src, out, monkeypatch, stage):
    crash_at(monkeypatch, stage)
    with pytest.raises(Crash):
        do_pack(src, out)
    with open(src / ".gitignore", "ab") as stream:
        stream.write(b"hand-edit/\n")
    no_crash(monkeypatch)
    with pytest.raises(ServeRefused, match=r"\.gitignore has"):
        do_pack(src, out)


def test_a_store_written_after_the_bundle_stops_the_refs_resume_with_exit_1(src, out, monkeypatch):
    crash_at(monkeypatch, "refs_started")
    with pytest.raises(Crash):
        do_pack(src, out)
    with contextlib.closing(sqlite3.connect(src / ".taskmaster" / "local" / "store.db", timeout=30)) as connection:
        connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op) "
                           "VALUES('2026-11-07T00:00:00Z','old-build','test','task','x','noop')")
        connection.commit()
    no_crash(monkeypatch)
    before = fingerprint(src)
    with pytest.raises(ServeRefused, match="written after the bundle") as caught:
        do_pack(src, out)
    assert caught.value.exit_code == 1
    assert fingerprint(src) == before


def test_a_resume_whose_settle_fails_before_any_ref_moved_undoes_the_freeze(src, out, monkeypatch):
    crash_at(monkeypatch, "frozen")
    with pytest.raises(Crash):
        do_pack(src, out)
    task = src / ".taskmaster" / "tasks" / f"{mh.TASK}.md"
    task.write_text(task.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")
    no_crash(monkeypatch)
    with pytest.raises(ServeRefused, match="differs from the store") as caught:
        do_pack(src, out)
    assert caught.value.exit_code == 2
    assert not sm.marker_path(src).exists() and not sm.latch_path(src).exists()
    assert migration.load_journal(src, migration.PACK_JOURNAL)["stage"] == "undone"


def test_a_rerun_with_a_different_url_names_undo(src, out, monkeypatch):
    crash_at(monkeypatch, "bundled")
    with pytest.raises(Crash):
        do_pack(src, out)
    no_crash(monkeypatch)
    with pytest.raises(ServeRefused, match="--undo"):
        migration.pack(src, url="https://other.example.com/mcp", out=out)


def snapshot_of(src):
    skip = ("serve", "coordinator")
    return {"refs": g(src, "for-each-ref", "--format=%(refname) %(objectname)"), "staged": g(src, "ls-files", "-s"),
            "gitignore": (src / ".gitignore").read_bytes(), "head": g(src, "rev-parse", "HEAD"),
            "taskmaster": {p.relative_to(src).as_posix(): p.read_bytes() for p in sorted((src / ".taskmaster").rglob("*"))
                           if p.is_file() and not set(skip) & set(p.relative_to(src / ".taskmaster").parts)
                           and not p.name.startswith("store.db")}}


@pytest.mark.parametrize("stage", ["frozen", "snapshot", "commits_built", "bundled", "ref_moved:main", "gitignore_synced",
                                   "done"])
def test_undo_not_pushed_restores_refs_index_and_gitignore(src, out, monkeypatch, stage):
    before = snapshot_of(src)
    if stage == "done":
        do_pack(src, out)
    else:
        crash_at(monkeypatch, stage)
        with pytest.raises(Crash):
            do_pack(src, out)
        no_crash(monkeypatch)
    result = migration.undo(src, not_pushed=True)
    assert result["state"] == "undone"
    assert snapshot_of(src) == before
    assert not sm.marker_path(src).exists() and not sm.latch_path(src).exists()
    assert migration.load_journal(src, migration.PACK_JOURNAL)["stage"] == "undone"
    if stage in ("bundled", "ref_moved:main", "gitignore_synced", "done"):
        assert out.exists()                        # the bundle is left where it is
    other_out = out.with_name("again.tmbundle")
    assert do_pack(src, other_out)["state"] == "packed"


def test_undo_is_refused_without_not_pushed_when_pushed_on_top_or_elsewhere(src, out):
    do_pack(src, out)
    with pytest.raises(ServeRefused, match="--not-pushed"):
        migration.undo(src, not_pushed=False)
    new = g(src, "rev-parse", "HEAD")
    run(src, "update-ref", "refs/remotes/origin/main", new)
    with pytest.raises(ServeRefused, match="already pushed"):
        migration.undo(src, not_pushed=True)
    run(src, "update-ref", "-d", "refs/remotes/origin/main")
    run(src, "checkout", "-q", "-b", "elsewhere")
    with pytest.raises(ServeRefused, match="check out"):
        migration.undo(src, not_pushed=True)
    run(src, "checkout", "-q", "main")
    (src / "later.txt").write_text("later\n", encoding="utf-8")
    run(src, "add", "later.txt")
    run(src, "commit", "-q", "-m", "on top")
    with pytest.raises(ServeRefused, match="on top"):
        migration.undo(src, not_pushed=True)


# -- gate round 1: I-1 (no root .gitignore) and I-2 (upstream moves during the pack) ---------------------

@pytest.fixture
def bare_src(src):
    run(src, "rm", "-q", ".gitignore")
    (src / ".taskmaster" / "local" / ".gitignore").write_text("*\n", encoding="utf-8")
    run(src, "commit", "-q", "-m", "no root ignore")
    return src


@pytest.mark.parametrize("stage", ["done", "ref_moved:main"])
def test_undo_in_a_repo_without_a_root_gitignore_removes_the_created_one_and_can_be_rerun(bare_src, out, monkeypatch, stage):
    src = bare_src
    before_head = g(src, "rev-parse", "HEAD")
    if stage == "done":
        do_pack(src, out)
    else:
        crash_at(monkeypatch, stage)
        with pytest.raises(Crash):
            do_pack(src, out)
        no_crash(monkeypatch)
    assert (src / ".gitignore").exists() == (stage == "done")
    assert migration.undo(src, not_pushed=True)["state"] == "undone"
    assert not (src / ".gitignore").exists()
    assert g(src, "rev-parse", "HEAD") == before_head
    assert not sm.marker_path(src).exists() and not sm.latch_path(src).exists()
    assert g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert do_pack(src, out.with_name("again.tmbundle"))["state"] == "packed"


def test_a_partial_undo_is_finished_by_a_rerun(src, out, monkeypatch):
    do_pack(src, out)
    real = untrack.sync_index
    state = {"n": 0}

    def flaky(git):
        state["n"] += 1
        if state["n"] == 1:
            raise untrack.UntrackError("boom")
        return real(git)
    monkeypatch.setattr(untrack, "sync_index", flaky)
    with pytest.raises(untrack.UntrackError):
        migration.undo(src, not_pushed=True)
    assert migration.undo(src, not_pushed=True)["state"] == "undone"
    assert not sm.marker_path(src).exists() and g(src, "status", "--porcelain=v1", "--untracked-files=all") == ""


def advance_upstream(src, tmp_path):
    origin = origin_of(src, tmp_path)
    mate = tmp_path / "mate"
    run(tmp_path, "clone", "-q", str(origin), str(mate))
    (mate / "t.txt").write_text("hi\n", encoding="utf-8")
    run(mate, "add", "-A")
    run(mate, "-c", "user.name=M", "-c", "user.email=m@example.invalid", "commit", "-q", "-m", "teammate")
    run(mate, "push", "-q", "origin", "main")

    def moved(name):
        if name == "gitignore_synced":
            run(src, "fetch", "-q", "origin")
    return moved


def test_an_upstream_that_advances_during_the_pack_is_a_report_line_not_a_refusal(src, out, tmp_path, monkeypatch):
    moved = advance_upstream(src, tmp_path)
    monkeypatch.setattr(migration, "CHECKPOINT", moved)
    report = do_pack(src, out)
    assert report["state"] == "packed" and any("moved during pack" in w for w in report["warnings"])
    assert sm.read_marker(src) is not None and migration.load_journal(src, migration.PACK_JOURNAL)["stage"] == "done"


def test_a_refs_resume_never_unfences_when_the_upstream_has_moved(src, out, tmp_path, monkeypatch):
    moved = advance_upstream(src, tmp_path)

    def checkpoint(name):
        moved(name)
        if name == "gitignore_synced":
            raise Crash(name)
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)
    with pytest.raises(Crash):
        do_pack(src, out)
    no_crash(monkeypatch)
    report = do_pack(src, out)
    assert report["state"] == "packed" and any("moved during pack" in w for w in report["warnings"])
    assert sm.read_marker(src) is not None and sm.read_latch(src) is not None


# -- Codex round: #2 untracked .gitignore, #3 unowned .partial, #6 machine-local checkout records, #1 execution lock ---

def test_an_existing_working_gitignore_that_head_lacks_is_refused_and_left_alone(bare_src, out):
    (bare_src / ".gitignore").write_text("mine/\n", encoding="utf-8")
    refused(bare_src, out, match="gitignore")
    assert (bare_src / ".gitignore").read_text(encoding="utf-8") == "mine/\n"


def test_an_unowned_partial_next_to_out_is_never_deleted_and_a_late_out_is_not_replaced(src, out, monkeypatch):
    stray = out.with_name(out.name + ".partial")
    stray.write_bytes(b"someone else's data")
    real = bundles.open_bundle

    def late(path):
        out.write_bytes(b"appeared after pre-flight")
        return real(path)
    monkeypatch.setattr(bundles, "open_bundle", late)
    with pytest.raises(ServeRefused, match="appeared"):
        do_pack(src, out)
    assert stray.read_bytes() == b"someone else's data" and out.read_bytes() == b"appeared after pre-flight"
    assert not sm.marker_path(src).exists() or migration.load_journal(src, migration.PACK_JOURNAL)["stage"] in ("undone", "bundled")


def test_the_bundle_store_carries_no_machine_local_checkout_records(src, out):
    with contextlib.closing(sqlite3.connect(src / ".taskmaster" / "local" / "store.db")) as connection:
        connection.execute("INSERT INTO sync_state(key,value_json) VALUES('checkout.abc', ?)",
                           (json.dumps({"path": str(src), "git_dir": str(src / ".git")}),))
        connection.commit()
    do_pack(src, out)
    import zipfile
    with zipfile.ZipFile(out) as archive:
        data = archive.read("store.db")
    copy = out.with_name("check.db")
    copy.write_bytes(data)
    with contextlib.closing(sqlite3.connect(copy)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sync_state WHERE key LIKE 'checkout.%'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM entity_core").fetchone()[0] > 0
    assert str(src).encode() not in data


def test_the_bundle_store_carries_no_session_rows(src, out):
    # team-server-026: `sessions` holds the laptop's host name, cwd and pid.
    with contextlib.closing(sqlite3.connect(src / ".taskmaster" / "local" / "store.db")) as connection:
        connection.execute("INSERT OR REPLACE INTO sessions(session,pid,host,started,last_seen,cwd) VALUES"
                           "('s1',1,'LAPTOP-SECRET','2026-01-01','2026-01-01','C:/Users/someone/proj')")
        connection.commit()
    do_pack(src, out)
    import zipfile
    with zipfile.ZipFile(out) as archive:
        data = archive.read("store.db")
    copy = out.with_name("check-sessions.db")
    copy.write_bytes(data)
    with contextlib.closing(sqlite3.connect(copy)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert b"LAPTOP-SECRET" not in data and b"C:/Users/someone" not in data


def test_dropping_machine_state_leaves_no_deleted_bytes_even_without_secure_delete_default(tmp_path):
    # A SQLite build with secure_delete off by default (Python 3.11 on Ubuntu, 3.12 on Windows) otherwise keeps the
    # deleted checkout row's bytes in a freed page, and the laptop path still travels in the bundle.
    path, mark = tmp_path / "copy.db", "C:/Users/someone/laptop/checkout/path"
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA secure_delete=OFF")
        connection.execute("CREATE TABLE sync_state(key TEXT PRIMARY KEY, value_json TEXT)")
        connection.execute("CREATE TABLE filler(v TEXT)")
        connection.execute("CREATE TABLE sessions(session TEXT PRIMARY KEY, host TEXT, cwd TEXT)")
        connection.executemany("INSERT INTO filler VALUES(?)", [("x" * 500,)] * 50)
        connection.execute("INSERT INTO sync_state VALUES('checkout.abc', ?)", (mark * 3,))
        connection.commit()
        migration._drop_machine_state(connection)
        connection.commit()
    assert mark.encode() not in path.read_bytes()


def test_the_snapshot_holds_the_writer_execution_lock_until_writes_are_refused(src, out, monkeypatch):
    captured = spy_owner(monkeypatch)
    seen = []

    def checkpoint(stage):
        if stage == "snapshot_taken":
            owner = captured["owners"][-1]
            seen.append(owner.execution._is_owned())
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)
    do_pack(src, out)
    assert seen == [True]
