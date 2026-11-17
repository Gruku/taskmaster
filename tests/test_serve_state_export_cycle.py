# User intent: team-server-013's export cycle against a real coordinator and real Git: one verified commit per change on
# its own branch, guards that refuse anything not provably this store's export history, no hook or secret ever, and
# fast-forward-only pushes that never leak the remote URL. All repositories are disposable temp roots.
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
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
GIT_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


def envelope(owner, key, operation="task.patch", arguments=None, scope="export-tests"):
    return {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": scope, "request_id": key,
            "operation": operation, "arguments": arguments or {"id": TASK, "set": {"title": key}},
            "expected_revisions": []}


def write(owner, key, **kwargs):
    receipt = owner.submit(envelope(owner, key, **kwargs)).result(60)
    owner.flush(receipt["commit_seq"], timeout=60)
    return receipt


@pytest.fixture
def repo(root):  # noqa: F811
    init_repo(root)
    git(root, "config", "core.autocrlf", "true")
    return Path(root).resolve()


@pytest.fixture
def owner(repo):
    with Coordinator(repo) as coordinator:
        yield coordinator


@pytest.fixture
def remote(repo, tmp_path_factory):
    path = tmp_path_factory.mktemp("remote") / "backup.git"
    git(repo, "init", "-q", "--bare", str(path))
    git(repo, "remote", "add", "backup", str(path))
    return path


def exporter(owner, push=None, branch=BRANCH, **kwargs):
    settings = se.Settings(Path(owner.root).resolve(), branch=branch, interval=900, push=push)
    return se.Exporter(owner, settings, **kwargs)


def fingerprint(repo, *, skip=()):
    refs = [line for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
            if line.split()[0] not in skip]
    logs = {}
    base = repo / ".git" / "logs" / "refs"
    for path in (base.rglob("*") if base.exists() else ()):
        rel = path.relative_to(repo / ".git").as_posix()
        if path.is_file() and rel not in {f"logs/{ref}" for ref in skip}:
            logs[rel] = path.read_bytes()
    worktrees = repo / ".git" / "worktrees"
    return (git(repo, "rev-parse", "HEAD").strip(), git(repo, "symbolic-ref", "HEAD").strip(),
            hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest(),
            git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"), refs, logs,
            sorted(p.name for p in worktrees.glob("*")) if worktrees.exists() else [])


def tree_files(repo, rev=BRANCH):
    return set(git(repo, "ls-tree", "-r", "--name-only", rev).splitlines())


def show_bytes(repo, spec):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "cat-file", "blob", spec], cwd=repo, env=GIT_ENV,
                          capture_output=True, check=True).stdout


def trailers(repo, rev=BRANCH):
    text = git(repo, "log", "-1", "--format=%(trailers:only,unfold)", rev)
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


def plumb_commit(repo, parent, message):
    tree = git(repo, "rev-parse", f"{parent}^{{tree}}").strip()
    return subprocess.run(["git", "commit-tree", tree, "-p", parent, "-m", message], cwd=repo, env=GIT_ENV,
                          capture_output=True, text=True, check=True).stdout.strip()


# ── Step 3: plumbing builder (G1, G6) ───────────────────────────────────────


def test_first_export_commits_the_generation_byte_exact_and_leaves_the_checkout_alone(repo, owner):
    (repo / "staged.txt").write_text("user staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / ".gitignore").write_text(".taskmaster/\n", encoding="utf-8")       # an unstaged edit that ignores the export
    skip = (f"refs/heads/{BRANCH}",)
    before = fingerprint(repo, skip=skip)
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "committed", outcome
    assert fingerprint(repo, skip=skip) == before
    files = tree_files(repo)
    assert "backlog.yaml" in files and f"tasks/{TASK}.md" in files
    assert not any(name.startswith((".taskmaster", "local/")) for name in files)
    for rel in files:
        assert show_bytes(repo, f"{BRANCH}:{rel}") == (repo / ".taskmaster" / rel).read_bytes(), rel
    found = trailers(repo)
    assert found[se.TRAILER_EXPORT] == "1" and found[se.TRAILER_STORE] == owner.identity["store_id"]
    assert int(found[se.TRAILER_SEQ]) == outcome["through"]
    assert git(repo, "log", "-1", "--format=%an <%ae>", BRANCH).strip() == \
        "Taskmaster export <taskmaster-export@localhost.invalid>"


def test_a_crlf_projection_file_is_committed_byte_exact(repo, owner):
    exporter(owner).cycle()
    path = repo / ".taskmaster" / "tasks" / f"{TASK}.md"
    original = path.read_bytes()
    lf = original.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n") if original == lf else lf     # flip the line endings either way
    with native_connection(repo) as connection:                  # the store publishes these bytes (as on Windows)
        connection.execute("UPDATE projection SET content_hash=? WHERE file=?",
                           (hashlib.sha1(crlf).hexdigest(), f"tasks/{TASK}.md"))
    path.write_bytes(crlf)
    state = se.load_state(repo)
    state["through"] = -1                                        # force a full cycle
    se.save_state(repo, state)
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "committed", (outcome["held"], outcome["dropped"])
    assert show_bytes(repo, f"{BRANCH}:tasks/{TASK}.md") == crlf


def test_no_repository_hook_runs(repo, owner, remote, tmp_path_factory):
    sentinels = tmp_path_factory.mktemp("sentinels")
    hooks = tmp_path_factory.mktemp("repo-hooks")
    for name in ("post-index-change", "reference-transaction", "pre-push", "post-commit"):
        body = f'touch "{(sentinels / name).as_posix()}"\nexit 0'
        (hooks / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        (hooks / name).chmod(0o755)
        install_hook(repo, name, body)
    git(repo, "config", "core.hooksPath", str(hooks))
    git(repo, "update-ref", "refs/heads/control", "HEAD")                    # control: the hooks are live
    assert (sentinels / "reference-transaction").exists()
    (sentinels / "reference-transaction").unlink()
    item = exporter(owner, push="backup")
    outcome = item.cycle()
    assert outcome["state"] == "committed" and outcome["record"]["pushed"] == outcome["record"]["commit"]
    item.maintain({"last_maintenance": 0}, 10 ** 10)
    assert list(sentinels.iterdir()) == []


# ── Step 4: guards and CAS (G2, G10) ────────────────────────────────────────


def test_a_branch_checked_out_in_a_worktree_is_refused(repo, owner, tmp_path_factory):
    assert exporter(owner).cycle()["state"] == "committed"
    git(repo, "worktree", "add", str(tmp_path_factory.mktemp("wt") / "state"), BRANCH)
    write(owner, "after-checkout")
    tip = git(repo, "rev-parse", BRANCH).strip()
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "checked out" in outcome["reason"]
    assert git(repo, "rev-parse", BRANCH).strip() == tip


def test_a_managed_checkout_during_the_locked_phase_never_gets_publication(repo, owner, monkeypatch):
    assert exporter(owner).cycle()["state"] == "committed"
    write(owner, "racing")
    head = git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(managed, "PUBLICATION_TIMEOUT", 0.2)
    results, real = {}, managed.generation

    def racing_generation(*args, **kwargs):
        worker = threading.Thread(target=lambda: results.setdefault("git", managed.run(
            owner, kind="checkout", ref=BRANCH, caller_scope="race", request_id="race-1")))
        worker.start()
        worker.join(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(managed, "generation", racing_generation)
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "committed", outcome
    assert results["git"]["state"] == "pending"
    assert (git(repo, "rev-parse", "HEAD").strip(), git(repo, "symbolic-ref", "HEAD").strip()) == (head, "refs/heads/main")


def test_forged_trailers_and_default_branches_are_refused(repo, owner):
    for name in ("main", "master", "HEAD", "@{-1}", "a..b"):
        with pytest.raises(se.ExportRefused):
            se.check_settings(se.Settings(repo, branch=name))
    git(repo, "checkout", "-q", "-b", "work")
    (repo / "code.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-q", "-m", "normal work\n\nTaskmaster-Export: 1")
    git(repo, "checkout", "-q", "main")
    tip = git(repo, "rev-parse", "work").strip()
    outcome = exporter(owner, branch="work").cycle()
    assert outcome["state"] == "commit_failed" and "not an export branch" in outcome["reason"]
    assert git(repo, "rev-parse", "work").strip() == tip
    assert se.is_export_branch(repo, "refs/heads/work") is False


@pytest.mark.parametrize("name", ["Master", "TASKMASTER-STATE-OLD"])
def test_a_case_variant_of_an_existing_branch_is_refused_and_never_moved(repo, owner, name):
    """Gate I1: on case-insensitive refs, `refs/heads/Master` would land on a packed `master`."""
    git(repo, "branch", "master")
    git(repo, "branch", "taskmaster-state-old")
    git(repo, "checkout", "-q", "-b", "work")                                  # neither is checked out
    git(repo, "pack-refs", "--all")
    before = {ref: git(repo, "rev-parse", ref).strip() for ref in ("master", "taskmaster-state-old", "main")}
    with pytest.raises(se.ExportRefused, match="default branch|differs only in case"):
        se.check_settings(se.Settings(repo, branch=name))
    outcome = exporter(owner, branch=name).cycle(adopt=True)
    assert outcome["state"] == "commit_failed", outcome
    assert "default branch" in outcome["reason"] or "differs only in case" in outcome["reason"], outcome
    assert {ref: git(repo, "rev-parse", ref).strip() for ref in before} == before


def test_a_symbolic_export_ref_is_refused_and_its_target_untouched(repo, owner):
    git(repo, "branch", "victim")
    victim = git(repo, "rev-parse", "victim").strip()
    git(repo, "symbolic-ref", f"refs/heads/{BRANCH}", "refs/heads/victim")
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "symbolic" in outcome["reason"]
    assert git(repo, "rev-parse", "victim").strip() == victim


def test_lineage_accepts_export_descendants_and_refuses_anything_else(repo, owner):
    assert exporter(owner).cycle()["state"] == "committed"
    assert se.is_export_branch(repo, f"refs/heads/{BRANCH}") is True
    store = owner.identity["store_id"]
    good = plumb_commit(repo, BRANCH, f"x\n\nTaskmaster-Export: 1\nTaskmaster-Store: {store}")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", good)
    write(owner, "lineage-1")
    assert exporter(owner).cycle()["state"] == "committed"
    bad = plumb_commit(repo, BRANCH, "someone else's commit")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", bad)
    write(owner, "lineage-2")
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "not exports" in outcome["reason"]
    assert git(repo, "rev-parse", BRANCH).strip() == bad


def test_one_store_per_branch_and_adoption_is_explicit_and_recorded(repo, owner):
    assert exporter(owner).cycle()["state"] == "committed"
    foreign = "other\n\nTaskmaster-Export: 1\nTaskmaster-Store: other-store"
    git(repo, "update-ref", f"refs/heads/{BRANCH}", plumb_commit(repo, BRANCH, foreign))
    write(owner, "foreign-1")
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "another store (other-store)" in outcome["reason"]
    se.state_path(repo).unlink()                                                # a restore: no record here
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "--adopt" in outcome["reason"]
    outcome = exporter(owner).cycle(adopt=True)
    assert outcome["state"] == "committed", outcome
    assert trailers(repo)[se.TRAILER_ADOPTED] == "other-store"
    assert se.load_state(repo)["adopted"]["from_store"] == "other-store"
    git(repo, "update-ref", f"refs/heads/{BRANCH}", plumb_commit(repo, BRANCH, foreign))
    write(owner, "foreign-2")
    assert exporter(owner).cycle()["state"] == "commit_failed"                 # adoption happens once


def test_a_stale_ref_lock_is_reported_and_left_alone(repo, owner):
    assert exporter(owner).cycle()["state"] == "committed"
    lock = repo / ".git" / "refs" / "heads" / f"{BRANCH}.lock"
    lock.write_text("", encoding="utf-8")
    write(owner, "locked")
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "lock" in outcome["reason"]
    assert lock.exists()


def test_sweep_removes_only_its_own_temp_files(tmp_path):
    for name in ("index-1-1.tmp", "doc-2-3.tmp", "other.tmp"):
        (tmp_path / name).write_text("x")
    (tmp_path / "index-9-9.tmp").mkdir()
    se.sweep(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index-9-9.tmp", "other.tmp"]


def test_sha256_repositories_are_refused(tmp_path):
    git(tmp_path, "init", "-q", "--object-format=sha256")
    (tmp_path / ".taskmaster" / "local").mkdir(parents=True)
    with pytest.raises(se.ExportRefused, match="sha256"):
        se.check_settings(se.Settings(tmp_path.resolve()))


# ── Step 5: the cycle against a real coordinator ────────────────────────────


class CountingLock:
    def __init__(self, inner):
        self.inner, self.acquired = inner, 0

    def acquire(self, *args, **kwargs):
        self.acquired += 1
        return self.inner.acquire(*args, **kwargs)

    def release(self):
        return self.inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def test_an_unchanged_cycle_takes_no_lock_and_each_change_makes_one_commit(repo, owner, monkeypatch):
    assert exporter(owner).cycle()["state"] == "committed"
    first = git(repo, "rev-parse", BRANCH).strip()
    counting = CountingLock(owner.publication)
    monkeypatch.setattr(owner, "publication", counting)
    outcome = exporter(owner, wall=lambda: 5.0e9).cycle()
    assert outcome["state"] == "unchanged" and counting.acquired == 0
    assert outcome["record"]["last_ok"] == 5.0e9 and outcome["record"]["caught_up"] is True
    monkeypatch.setattr(owner, "publication", counting.inner)
    receipt = write(owner, "second")
    assert exporter(owner).cycle()["state"] == "committed"
    assert git(repo, "rev-parse", f"{BRANCH}~1").strip() == first
    assert int(trailers(repo)[se.TRAILER_SEQ]) >= receipt["commit_seq"]


def test_a_hand_edited_file_is_held_forward(repo, owner):
    assert exporter(owner).cycle()["state"] == "committed"
    rel = f"tasks/{TASK}.md"
    committed = show_bytes(repo, f"{BRANCH}:{rel}")
    (repo / ".taskmaster" / rel).write_text("hand edit\n", encoding="utf-8")
    state = se.load_state(repo)
    state["through"] = -1
    se.save_state(repo, state)
    outcome = exporter(owner).cycle()
    record = outcome["record"]
    assert rel in record["held"] and record["held_count"] == 1, outcome
    assert show_bytes(repo, f"{BRANCH}:{rel}") == committed


def test_an_archive_move_removes_the_old_path_and_adds_the_new_one(repo, owner):
    assert exporter(owner).cycle()["state"] == "committed"
    write(owner, "archive", operation="task.archive", arguments={"id": TASK, "reason": "superseded"})
    assert exporter(owner).cycle()["state"] == "committed"
    files = tree_files(repo)
    assert f"tasks/{TASK}.md" not in files and f"tasks/archive/{TASK}.md" in files
    assert git(repo, "log", "-1", "--format=%B", BRANCH).startswith("taskmaster export:")


def test_a_pin_busy_publisher_or_active_git_defers(repo, owner):
    owner.git_pin = {"state": "recovery_required", "reason": "test"}
    try:
        outcome = exporter(owner).cycle()
    finally:
        owner.git_pin = None
    assert outcome["state"] == "deferred" and outcome["reason"] == "managed Git pin"
    assert outcome["record"]["deferred_since"] and "last_ok" not in outcome["record"]
    owner.git_active = {"op_id": "x"}
    try:
        assert exporter(owner).cycle()["reason"] == "managed Git operation"
    finally:
        owner.git_active = None
    release, held = threading.Event(), threading.Event()

    def hold():
        with owner.publication:
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        assert exporter(owner).cycle(acquire_timeout=0.2)["reason"] == "publisher busy"
    finally:
        release.set()
        holder.join(30)


def test_stopping_after_the_acquire_abandons_the_cycle(repo, owner):
    stop = threading.Event()
    item = exporter(owner, halted=stop.is_set)
    real = item.guards

    def guards(*args, **kwargs):
        stop.set()
        return real(*args, **kwargs)

    item.guards = guards
    assert item.cycle()["state"] == "stopped"
    assert git(repo, "rev-parse", "--verify", "-q", BRANCH, check=False).strip() == ""


def test_a_file_changed_after_the_verified_read_aborts_the_cycle(repo, owner, monkeypatch):
    real = managed.generation

    def changing(*args, **kwargs):
        result = real(*args, **kwargs)
        with open(repo / ".taskmaster" / "tasks" / f"{TASK}.md", "wb") as stream:   # a writer racing the read
            stream.write(b"changed underneath\n")
        return result

    monkeypatch.setattr(managed, "generation", changing)
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "commit_failed" and "changed during the export" in outcome["reason"]
    assert git(repo, "rev-parse", "--verify", "-q", BRANCH, check=False).strip() == ""


def test_a_pinned_store_never_reads_healthy_and_status_ages_to_exit_1(repo, owner):
    now = [1.0e9]
    item = exporter(owner, wall=lambda: now[0])
    assert item.cycle()["state"] == "committed"
    assert se.status(repo, now=now[0])[1] == 0
    owner.git_pin = {"state": "recovery_required", "reason": "test"}
    try:
        receipt = owner.submit(envelope(owner, "pinned")).result(60)          # committed, never exported
        for _ in range(3):
            now[0] += 900
            assert item.cycle()["state"] == "deferred"
        assert se.load_state(repo)["last_ok"] == 1.0e9
        now[0] += 3600
        report, code = se.status(repo, now=now[0])
        assert code == 1 and any("managed Git pin" in p for p in report["problems"]), report
    finally:
        owner.git_pin = None
    owner.export_needed.set()
    owner.flush(receipt["commit_seq"], timeout=60)
    now[0] += 60
    outcome = item.cycle()
    assert outcome["state"] == "committed" and outcome["record"]["caught_up"] is True, outcome
    assert se.status(repo, now=now[0])[1] == 0


@pytest.mark.parametrize("term", ["export watermark", "PROGRESS debt"])
def test_a_stalled_export_is_behind_and_names_the_failing_check(repo, owner, monkeypatch, term):
    from taskmaster.native import projection as outbox
    from taskmaster.native_routing import progress
    now = [1.0e9]
    item = exporter(owner, wall=lambda: now[0])
    assert item.cycle()["state"] == "committed"
    if term == "export watermark":
        monkeypatch.setattr(outbox, "watermark", lambda connection: -1)
    else:
        monkeypatch.setattr(progress, "owes_through", lambda connection, through: True)
    write(owner, "stalled")
    now[0] += 900
    record = item.cycle()["record"]
    assert record["state"] == "behind" and record["behind_reason"] == term and record["last_ok"] == 1.0e9
    now[0] += 3601
    report, code = se.status(repo, now=now[0])
    assert code == 1 and any(term in p for p in report["problems"]), report
    monkeypatch.undo()
    now[0] += 60
    assert item.cycle()["record"]["caught_up"] is True


def test_a_busy_server_that_keeps_up_clears_behind_against_the_recorded_target(repo, owner, monkeypatch):
    item = exporter(owner)
    assert item.cycle()["state"] == "committed"
    state, marks, calls = se.load_state(repo), item.marks(), []

    def owed(target):
        calls.append(target)
        return len(calls) == 1, "export watermark"

    monkeypatch.setattr(item, "owed", owed)

    def read(extra):
        return se.Marks(high=marks.high + extra, through=0, watermark=0, owes=False, docs_seq=0)

    outcome = {"state": "unchanged", "commit": state["commit"], "through": 0, "docs_seq": 0, "held": {},
               "dropped": [], "dropped_documents": [], "adopted": None}
    item._settle(state, dict(outcome, sync=item.in_sync(state, read(0))), 1.0)
    assert state["state"] == "behind" and state["behind_owed"] == marks.high
    item._settle(state, dict(outcome, sync=item.in_sync(state, read(5))), 2.0)   # newer writes; old target exported
    assert calls[1] == marks.high and state["caught_up"] is True and state["behind_owed"] is None


def test_in_sync_is_judged_at_the_read_not_after_it(repo, owner, monkeypatch):
    """Gate M2: an export that catches up while the cycle builds does not vouch for the commit."""
    from taskmaster.native import projection as outbox
    item = exporter(owner)
    assert item.cycle()["state"] == "committed"
    write(owner, "late")
    caught_up_later = threading.Event()
    real_watermark, real_generation = outbox.watermark, managed.generation
    monkeypatch.setattr(outbox, "watermark", lambda c: real_watermark(c) if caught_up_later.is_set() else -1)

    def generation(*args, **kwargs):
        caught_up_later.set()                                  # the projection catches up after the read
        return real_generation(*args, **kwargs)

    monkeypatch.setattr(managed, "generation", generation)
    state = se.load_state(repo)
    state["through"] = -1
    se.save_state(repo, state)
    record = item.cycle()["record"]
    assert record["state"] == "behind" and record["behind_reason"] == "export watermark", record


def _declare(repo, docs):
    from taskmaster.native.commands import _write_field   # test-only: the task declares its documents
    with native_connection(repo) as connection:
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?",
                                 (TASK,)).fetchone()[0]
        _write_field(connection, key, "task", "docs", docs)


def _import(owner, key, section, path, body):
    return write(owner, key, operation="document.import",
                 arguments={"kind": "task", "id": TASK, "section": section, "path": path, "body": body})


def test_documents_are_exported_and_a_document_from_dot_env_never_is(repo, owner):
    _declare(repo, {"spec": "docs/specs/x.md", "plan": ".env",
                     "design": ".taskmaster/local/serve/operator.token"})
    _import(owner, "doc-spec", "spec", "docs/specs/x.md", "The spec.\n")
    _import(owner, "doc-env", "plan", ".env", "TOKEN=abc\n")
    with native_connection(repo) as connection:                               # planted: no command would allow it
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?",
                                 (TASK,)).fetchone()[0]
        body = "TOKEN=abc operator\n"
        connection.execute("INSERT INTO external_documents VALUES(?,?,?,?,?,?)",
                           (key, "design", ".taskmaster/local/serve/operator.token", body,
                            hashlib.sha256(body.encode()).hexdigest(), 1))
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "committed", outcome
    files = tree_files(repo)
    assert f"external-documents/task/{TASK}/spec.md" in files
    assert not {f"external-documents/task/{TASK}/plan.md", f"external-documents/task/{TASK}/design.md"} & files
    manifest = json.loads(show_bytes(repo, f"{BRANCH}:external-documents/index.json"))
    assert [(m["section"], m["path"], m["source"], m["imported_by"]) for m in manifest] == \
        [("spec", "docs/specs/x.md", "file", None)]
    assert manifest[0]["sha256"] == hashlib.sha256(b"The spec.\n").hexdigest()
    assert show_bytes(repo, f"{BRANCH}:external-documents/task/{TASK}/spec.md") == b"The spec.\n"
    assert git(repo, "grep", "-e", "TOKEN=abc", BRANCH, check=False) == ""
    record = outcome["record"]
    assert record["dropped_documents_count"] == 2 and {d["rule"] for d in record["dropped_documents"]} == {"dot path"}
    message = git(repo, "log", "-1", "--format=%B", BRANCH)
    assert f"dropped document: task/{TASK}/plan (dot path)" in message and ".env" not in message


def test_a_planted_secret_projection_row_and_team_sync_settings_change_nothing(repo, owner):
    (repo / ".taskmaster" / "tasks" / "x.key").write_text("secret\n", encoding="utf-8")
    with native_connection(repo) as connection:
        connection.execute("INSERT INTO projection(file,kind,id,content_hash) VALUES(?,?,?,?)",
                           ("tasks/x.key", "task", "x-key", hashlib.sha1(b"secret\n").hexdigest()))
    project = repo / ".taskmaster" / "project.yaml"
    with open(project, "a", encoding="utf-8") as stream:                # G5: the deferred Git sync's settings
        stream.write("\nteam:\n  sync:\n    remote: evil\n    state_branch: master\n    debounce_s: 1\n")
    outcome = exporter(owner).cycle()
    assert outcome["state"] == "committed", outcome
    assert "tasks/x.key" not in tree_files(repo)
    assert "dropped: tasks/x.key (secret name)" in git(repo, "log", "-1", "--format=%B", BRANCH)
    record = outcome["record"]
    assert (record["branch"], record["remote"], record["interval"]) == (BRANCH, None, 900)
    assert git(repo, "rev-parse", "master", check=False).strip() in ("", "master")   # never created or moved


# ── Step 6: push (G3, G7) ───────────────────────────────────────────────────


def test_push_is_fast_forward_only_and_a_rejection_is_never_forced(repo, owner, remote):
    record = exporter(owner, push="backup").cycle()["record"]
    assert record["pushed"] == record["commit"] == git(remote, "rev-parse", BRANCH).strip()
    write(owner, "push-2")
    record = exporter(owner, push="backup").cycle()["record"]
    pushed = git(remote, "rev-parse", BRANCH).strip()
    assert record["pushed"] == pushed
    git(repo, "update-ref", f"refs/heads/{BRANCH}", f"{BRANCH}~1")              # rewind and lose the record
    se.state_path(repo).unlink()
    write(owner, "push-3")
    outcome = exporter(owner, push="backup").cycle()
    record = outcome["record"]
    assert outcome["state"] == "committed" and record["push_state"] == "push_blocked", record
    assert "never force-pushes" in record["push_error"]
    assert git(remote, "rev-parse", BRANCH).strip() == pushed
    assert se.status(repo)[1] == 1


def test_push_configuration_is_checked(repo, owner, remote):
    with pytest.raises(se.ExportRefused, match="no configured remote"):
        se.check_settings(se.Settings(repo, push="nowhere"))
    git(repo, "config", "remote.backup.mirror", "true")
    with pytest.raises(se.ExportRefused, match="mirror"):
        se.check_settings(se.Settings(repo, push="backup"))
    git(repo, "config", "--unset", "remote.backup.mirror")
    git(repo, "symbolic-ref", "refs/remotes/backup/HEAD", "refs/remotes/backup/prod")
    with pytest.raises(se.ExportRefused, match="default branch"):
        se.check_settings(se.Settings(repo, branch="prod", push="backup"))
    se.check_settings(se.Settings(repo, push="backup"))


def test_no_url_or_credential_leaks_anywhere(repo, owner, caplog):
    git(repo, "remote", "add", "leaky", "https://u:secret@127.0.0.1:9/x.git")
    caplog.set_level(logging.DEBUG, logger="taskmaster.serve.export")
    se.check_settings(se.Settings(repo, push="leaky"))
    item = se.StateExporter(owner, se.Settings(repo, push="leaky"))
    item.step()
    record = se.load_state(repo)
    assert record["push_state"] == "push_failed", record
    report, code = se.status(repo)
    blob = json.dumps(record) + json.dumps(report) + caplog.text
    for leak in ("secret", "127.0.0.1:9", "u:secret"):
        assert leak not in blob
    assert code == 1


def test_git_ssh_command_from_the_daemon_environment_reaches_the_push(repo, owner, tmp_path_factory, monkeypatch):
    sentinel = tmp_path_factory.mktemp("ssh") / "ran"
    git(repo, "remote", "add", "sshy", "ssh://git@host.invalid/x.git")
    python = Path(sys.executable).as_posix()
    monkeypatch.setenv("GIT_SSH_COMMAND", f'"{python}" -c "open(r\'{sentinel.as_posix()}\', \'w\').close()"')
    record = exporter(owner, push="sshy").cycle()["record"]
    assert record["push_state"] == "push_failed" and sentinel.exists()
    assert "host.invalid" not in json.dumps(record)


def test_every_git_call_is_on_the_allowlist(repo, owner, remote):
    seen = []
    item = exporter(owner, push="backup", record=seen)
    assert item.cycle()["state"] == "committed"
    item.maintain({"last_maintenance": 0}, 10 ** 10)
    commands = {argv[0] for argv, _ in seen}
    assert commands <= {"rev-parse", "symbolic-ref", "worktree", "rev-list", "merge-base", "cat-file", "ls-tree",
                        "check-ref-format", "for-each-ref", "remote", "config", "hash-object", "update-index", "write-tree",
                        "commit-tree", "update-ref", "push", "maintenance"}
    assert {"update-ref", "push", "maintenance", "commit-tree", "update-index"} <= commands
    for argv, env in [(["push", "--force", "backup", f"refs/heads/{BRANCH}"], {}),
                      (["push", "--no-verify", "--porcelain", "backup", f"+refs/heads/{BRANCH}:refs/heads/{BRANCH}"], {}),
                      (["update-ref", "-m", "x", f"refs/heads/{BRANCH}", "0" * 40, "0" * 40], {}),
                      (["update-ref", "--no-deref", "-m", "x", "HEAD", "0" * 40, "0" * 40], {}),
                      (["update-index", "-z", "--add", "--index-info"], {}),
                      (["write-tree"], {"GIT_INDEX_FILE": str(repo / ".git" / "index")}),
                      (["gc", "--auto"], {}), (["config", "--get", "user.name"], {}), (["checkout", BRANCH], {}),
                      (["rev-parse", "HEAD"], {}), (["status"], {"GIT_DIR": "x"})]:
        with pytest.raises(se.ExportRefused, match="argv refused"):
            item.git.run(*argv, env=env)


# ── Step 7: scheduler, backoff, maintenance ─────────────────────────────────


class FakeRandom:
    def uniform(self, low, high):
        return 1.0


def test_next_delay_follows_the_backoff_table():
    rng = FakeRandom()
    assert se.next_delay({"state": "committed"}, {}, 900, 0, rng) == 900
    assert se.next_delay({"state": "deferred"}, {}, 900, 0, rng) == 60
    assert [se.next_delay({"state": "commit_failed"}, {"failures": n}, 900, 0, rng) for n in (1, 2, 3, 5, 9)] == \
        [60, 120, 240, 900, 900]
    owed = {"remote": "b", "commit": "c", "pushed": "p", "push_not_before": 130}
    assert se.next_delay({"state": "committed"}, owed, 900, 10, rng) == 120


def test_the_scheduler_backs_off_logs_and_stops_promptly(repo, owner, monkeypatch, caplog):
    outcomes = iter([{"state": "deferred", "reason": "publisher busy", "record": {}}] * 3 +
                    [{"state": "commit_failed", "reason": "x",
                      "record": {"state": "commit_failed", "failures": 1, "last_error": "boom"}}])
    timeouts = []
    item = se.StateExporter(owner, se.Settings(repo, interval=900), rng=FakeRandom(), first_delay=0.05)

    def fake_cycle(*, acquire_timeout, **kwargs):
        timeouts.append(acquire_timeout)
        return next(outcomes)

    monkeypatch.setattr(item.exporter, "cycle", fake_cycle)
    caplog.set_level(logging.WARNING, logger="taskmaster.serve.export")
    assert [item.step() for _ in range(4)] == [60, 60, 60, 60]
    assert timeouts == [managed.PUBLICATION_TIMEOUT] * 3 + [se.BUSY_ACQUIRE]
    assert "state export failed: boom" in caplog.text
    blocker = threading.Event()
    item.step = lambda: (blocker.wait(30), 900.0)[1]
    item.start()
    time.sleep(0.3)
    started = time.monotonic()
    blocker.set()
    item.stop(timeout=10)
    assert not item.thread.is_alive() and time.monotonic() - started < 10


def test_maintenance_packs_loose_objects_and_keeps_refs_reflogs_and_worktrees(repo, owner, tmp_path_factory):
    assert exporter(owner).cycle()["state"] == "committed"
    git(repo, "worktree", "add", str(tmp_path_factory.mktemp("wt") / "w"), "-b", "side")
    before = fingerprint(repo)
    item, state = exporter(owner), {"last_maintenance": 0}
    item.maintain(state, 10 ** 10)
    assert state["last_maintenance"] == 10 ** 10 and fingerprint(repo) == before
    item.maintain(state, 10 ** 10 + 10)                                         # at most once a day
    assert state["last_maintenance"] == 10 ** 10


# ── Codex review round: fetch mappings, document snapshot, userinfo redaction, config allowlist ──


def test_a_fetch_refspec_onto_a_local_branch_refuses_the_push_and_leaves_main_alone(repo, owner, remote):
    git(repo, "config", "remote.backup.fetch", f"+refs/heads/{BRANCH}:refs/heads/main")
    main_before = git(repo, "rev-parse", "main").strip()
    outcome = exporter(owner, push="backup").cycle()
    record = outcome["record"]
    assert outcome["state"] == "committed" and record["push_state"] == "push_blocked", record
    assert "outside refs/remotes/backup/" in record["push_error"] and str(remote) not in record["push_error"]
    assert git(repo, "rev-parse", "main").strip() == main_before
    assert git(remote, "rev-parse", "--verify", "-q", BRANCH, check=False).strip() == ""     # nothing was pushed


def test_a_symbolic_tracking_ref_refuses_the_push(repo, owner, remote):
    git(repo, "symbolic-ref", f"refs/remotes/backup/{BRANCH}", "refs/heads/main")
    main_before = git(repo, "rev-parse", "main").strip()
    record = exporter(owner, push="backup").cycle()["record"]
    assert record["push_state"] == "push_blocked" and "symbolic" in record["push_error"], record
    assert git(repo, "rev-parse", "main").strip() == main_before


def test_a_document_changed_while_the_exporter_waited_is_never_vouched_for(repo, owner):
    _declare(repo, {"spec": "docs/specs/a.md"})
    _import(owner, "doc-a", "spec", "docs/specs/a.md", "old\n")
    item = exporter(owner)
    original, fired = item.documents, []

    def stale_snapshot(parent):
        snapshot = original(parent)
        if not fired:                                    # a request lands between the snapshot and the lock
            fired.append(1)
            _declare(repo, {"spec": "docs/specs/b.md"})
            _import(owner, "doc-b", "spec", "docs/specs/b.md", "new\n")
        return snapshot

    item.documents = stale_snapshot
    outcome = item.cycle()
    assert outcome["state"] == "committed", outcome
    manifest = json.loads(show_bytes(repo, f"{BRANCH}:external-documents/index.json"))
    assert [(m["path"], m["sha256"]) for m in manifest] == [("docs/specs/b.md", hashlib.sha256(b"new\n").hexdigest())]
    assert show_bytes(repo, f"{BRANCH}:external-documents/task/{TASK}/spec.md") == b"new\n"


def test_a_snapshot_that_keeps_moving_defers_instead_of_catching_up(repo, owner):
    _declare(repo, {"spec": "docs/specs/a.md"})
    _import(owner, "doc-a", "spec", "docs/specs/a.md", "v0\n")
    item = exporter(owner)
    marks = item.marks
    item.marks = lambda: dataclasses.replace(marks(), docs_seq=10 ** 6)       # the writer never stops
    outcome = item.cycle()
    assert outcome["state"] == "deferred" and "documents changed" in outcome["reason"]
    assert item.git.text("rev-parse", "--verify", "-q", item.git.ref)[1] == ""


def test_ssh_userinfo_is_redacted_from_bare_stderr():
    se.learn_remote_secrets("origin", "ssh://SECRETUSER@host.example/repo.git")
    text = se.redact("SECRETUSER@host.example: Permission denied (publickey).", "origin")
    assert "SECRETUSER" not in text
    se.learn_remote_secrets("web", "https://bob:hunter22@host.example/x.git")
    assert "hunter22" not in se.redact("fatal: auth failed for hunter22", "web")
    se.learn_remote_secrets("scp", "deploy-key@host.example:org/repo.git")
    assert "deploy-key" not in se.redact("deploy-key@host.example: denied", "scp")


def test_git_runner_config_must_be_a_permitted_pair(repo, owner):
    item = exporter(owner)
    with pytest.raises(se.ExportRefused, match="argv refused"):
        item.git.run("rev-parse", "--show-toplevel", config=("clean", "-fdx", "--"))
    with pytest.raises(se.ExportRefused, match="argv refused"):
        item.git.run("rev-parse", "--show-toplevel", config=("-c", "core.fsmonitor=evil"))
    assert item.git.run("rev-parse", "--show-toplevel", config=("-c", "push.followTags=false"))[0] == 0


# ── team-server-014 D13: a server-mode host whose HEAD tracks .taskmaster/ ──────────────────────────────────


def test_a_marker_clone_whose_head_tracks_taskmaster_still_exports_but_alarms_until_untracked(repo, owner, caplog, request):
    from taskmaster import server_fence, server_marker as sm
    sm.write_marker(repo, "https://tm.example.com/mcp", "ab" * 16)
    lease = server_fence.claim_host(repo)                                           # the exporter runs inside the daemon
    request.addfinalizer(lease.release)
    assert git(repo, "ls-files", "--", ".taskmaster").strip()                       # init_repo committed the projection
    head = git(repo, "rev-parse", "HEAD").strip()
    now = [1000.0]
    settings = se.Settings(Path(owner.root).resolve(), branch=BRANCH, interval=900, push=None)
    runner = se.StateExporter(owner, settings, wall=lambda: now[0])
    with caplog.at_level(logging.ERROR):
        runner.step()
        runner.step()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "tracks .taskmaster" in r.getMessage()]
    assert len(errors) == 1                                                          # at most once an hour
    record = se.load_state(repo)
    assert record["host_tracks_taskmaster"] == head
    assert git(repo, "rev-parse", "--verify", f"refs/heads/{BRANCH}").strip()        # the backup was still written
    report, code = se.status(repo, now=now[0])
    assert code == 1 and report["host_tracks_taskmaster"] == head
    assert any("tracks .taskmaster" in problem for problem in report["problems"])
    now[0] += 3601
    with caplog.at_level(logging.ERROR):
        runner.step()
    assert len([r for r in caplog.records if r.levelno == logging.ERROR and "tracks .taskmaster" in r.getMessage()]) == 2
    git(repo, "rm", "-r", "-q", "--cached", ".taskmaster")
    git(repo, "commit", "-q", "-m", "untrack")
    runner.step()
    assert "host_tracks_taskmaster" not in se.load_state(repo)
    assert se.status(repo, now=now[0])[0]["host_tracks_taskmaster"] is None


def test_without_a_marker_a_tracked_taskmaster_head_is_not_flagged(repo, owner):
    exporter(owner).cycle()
    assert "host_tracks_taskmaster" not in se.load_state(repo)
