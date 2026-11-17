# User intent: the host's backup is safe to hand to a remote and sufficient to rebuild from: the exported `taskmaster-state` tree carries no secrets or machine state, records who uploaded each document, reaches the backup remote fast-forward only, and a fresh host restored by following the deploy runbook's restore block literally gets the documents back with their original uploader and no keys (team-server-015 S7; the runbook is tested too).
from __future__ import annotations

import contextlib
import io
import shutil
import json
import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from twohost.host import Host
from twohost.laptop import clone_host, migrated_world
from twohost.member import Member, machine_cli, run_git
from twohost.world import Machine

pytestmark = [pytest.mark.twohost, pytest.mark.slow, pytest.mark.real_service_process,
              pytest.mark.xdist_group("heavy_processes"), pytest.mark.timeout(900)]

REPO = Path(__file__).resolve().parents[1]
TASK = "test-epic-001"
UPLOAD = "Ann's uploaded spec, S7.\n"
BRANCH = "taskmaster-state"


def export_run(machine, *extra):
    return machine_cli(machine, "serve", "export", "run", "--root", str(machine.root), "--export-push", "backup", *extra,
                       timeout=300)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from taskmaster.serve.auth import ensure_operator_token
    from taskmaster.serve.principals import add_user
    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("s7") / "run"
    base.mkdir()
    try:
        moved = migrated_world(base, patch)
        assert moved["init"].returncode == 0, (moved["init"].stdout, moved["init"].stderr)
        for path in (moved["bundle"], moved["host_bundle"]):
            path.unlink()
        host = moved["host"]
        backup = base / "backup.git"
        backup.mkdir()
        run_git(host, backup, "init", "-q", "--bare")
        run_git(host, host.root, "remote", "add", "backup", str(backup))
        key = add_user(host.root, "ann", name="Ann", emails=["ann@twohost.invalid"])[0]
        operator_token = ensure_operator_token(host.root)[0]
        bob_key = add_user(host.root, "bob", name="Bob", emails=["bob@twohost.invalid"])[0]
        ann_machine = Machine.create(base, "ann")
        daemon = Host(host, base, name="s7host")
        try:
            daemon.start()
            ann = Member(ann_machine, daemon, key)
            answer = ann.call("backlog_document_import", {"kind": "task", "entity_id": TASK, "content": {"spec": UPLOAD}})
        finally:
            daemon.stop()                                       # the export runs with the daemon stopped
        first = export_run(host)
        yield {**moved, "backup": backup, "ann_key": key, "upload_answer": answer, "first": first, "ann_machine": ann_machine,
               "key": key, "operator_token": operator_token, "bob_key": bob_key}
    finally:
        patch.undo()


def git_in(world, repo, *args):
    return run_git(world["host"], Path(repo), *args)


def test_the_export_ran_and_pushed_to_the_backup(world):
    assert world["first"].returncode == 0, (world["first"].stdout, world["first"].stderr)
    report = json.loads(world["first"].stdout)
    assert report["state"] in ("committed", "unchanged") and report["pushed"], report
    assert git_in(world, world["backup"], "rev-parse", "--verify", f"refs/heads/{BRANCH}")


def test_the_exported_tree_has_no_machine_state_no_secrets_and_no_denylisted_names(world):
    from taskmaster.serve.state_export import _TREE_DENY
    names = git_in(world, world["backup"], "ls-tree", "-r", "--name-only", BRANCH).splitlines()
    assert names, "the backup tree is empty"
    assert not [n for n in names if n.startswith("local/") or "/local/" in n], names
    assert not [n for n in names if _TREE_DENY.fullmatch(n.rsplit("/", 1)[-1])], names
    from taskmaster.serve.principals import key_digest
    # the secret values themselves (and the stored digests), not just the file names that usually hold them
    needles = (world["ann_key"], world["bob_key"], world["operator_token"], key_digest(world["ann_key"]),
               key_digest(world["bob_key"]), key_digest(world["operator_token"]), "operator.token", "principals")
    for needle in needles:
        assert grep_branch(world, world["backup"], needle).returncode == 1, needle


def grep_branch(world, repo, needle, ref=BRANCH):
    return subprocess.run(["git", "-C", str(repo), "grep", "-l", "-F", needle, ref],
                          env=world["host"].env, capture_output=True, text=True, timeout=60)


def test_the_secret_grep_would_catch_a_token_under_any_file_name(world, tmp_path):
    """Control for the check above: the same grep finds the operator token in a file named innocently."""
    scratch = tmp_path / "scratch"
    git_in(world, tmp_path, "clone", "-q", str(world["backup"]), str(scratch))
    git_in(world, scratch, "checkout", "-q", BRANCH)
    (scratch / "notes.txt").write_text(f"x {world['operator_token']} y", encoding="utf-8")
    git_in(world, scratch, "add", "-A")
    git_in(world, scratch, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "plant")
    hit = grep_branch(world, scratch, world["operator_token"], "HEAD")
    assert hit.returncode == 0 and "notes.txt" in hit.stdout, hit


def test_the_manifest_records_anns_upload_with_imported_by_ann(world):
    manifest = json.loads(git_in(world, world["backup"], "show", f"{BRANCH}:external-documents/index.json"))
    [entry] = [e for e in manifest if e["id"] == TASK and e["section"] == "spec"]
    assert entry["imported_by"] == "ann" and entry["source"] == "upload", (entry, world["upload_answer"])
    assert git_in(world, world["backup"], "show", f"{BRANCH}:external-documents/task/{TASK}/spec.md") == UPLOAD.strip()


def test_a_later_export_reaches_the_backup_fast_forward_only(world):
    host = world["host"]
    before = git_in(world, world["backup"], "rev-parse", f"refs/heads/{BRANCH}")
    daemon = Host(host, world["base"], name="s7host2")
    try:
        daemon.start()
        ann = Member(world["ann_machine"], daemon, world["ann_key"])
        ann.call("backlog_document_import", {"kind": "task", "entity_id": TASK, "content": {"spec": UPLOAD + "second\n"}})
    finally:
        daemon.stop()
    second = export_run(host)
    assert second.returncode == 0, (second.stdout, second.stderr)
    after = git_in(world, world["backup"], "rev-parse", f"refs/heads/{BRANCH}")
    assert after != before
    done = subprocess.run(["git", "-C", str(world["backup"]), "merge-base", "--is-ancestor", before, after],
                          env=host.env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, "the second push was not a fast-forward of the first"


def archive_into(machine, commit, target: Path):
    """`git archive <commit> -- . ':(exclude)external-documents' | tar -x -C .taskmaster`, without a shell."""
    done = subprocess.run(["git", "-C", str(machine.root), "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive",
                           "--format=tar", commit, "--", ".", ":(exclude)external-documents"], env=machine.env,
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(target, filter="data")


def test_a_fresh_host_restores_from_the_backup_following_the_runbook_and_gets_no_keys(world):
    from taskmaster.native import documents as native_documents
    from native_twins import native_connection
    from taskmaster.native.queries import Repository
    h2 = clone_host(world["base"], world["origin"], "hostclone2")
    run_git(h2, h2.root, "remote", "add", "backup", str(world["backup"]))
    run_git(h2, h2.root, "fetch", "-q", "backup", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")
    log = run_git(h2, h2.root, "log", "--format=%H %ci", BRANCH)
    commit = log.splitlines()[0].split()[0]                                    # "pick a commit": the newest
    (h2.root / ".taskmaster").mkdir(exist_ok=True)
    archive_into(h2, commit, h2.root / ".taskmaster")
    # The runbook's restore block (deploy runbook section 10): the clone `REPO_URL` gives carries the committed server
    # marker, which the cutover refuses; the operator moves it aside, clears the latch, opens the store (no CLI does; the
    # runbook runs the library call with the release's own python), cuts over, and puts the marker back afterwards.
    marker = h2.root / ".taskmaster-server.json"
    aside = h2.root.parent / "marker.aside.json"
    shutil.move(str(marker), str(aside))
    cleared = machine_cli(h2, "serve", "marker", "clear-latch", "--root", str(h2.root))
    assert cleared.returncode == 0, (cleared.stdout, cleared.stderr)
    opened = subprocess.run([sys.executable, "-I", "-c", "from pathlib import Path; import sys; from taskmaster import store; "
                             "store.open_store(root=Path(sys.argv[1]))", str(h2.root)],
                            env=h2.env, capture_output=True, text=True, timeout=120)
    assert opened.returncode == 0, opened.stderr
    cut = machine_cli(h2, "cutover", "--root", str(h2.root), "--confirm-stopped", timeout=300)   # nothing of ours runs on H2
    assert cut.returncode == 0, ("cutover", cut.stdout[-1500:], cut.stderr[-1500:])
    restored = machine_cli(h2, "serve", "export", "restore-documents", "--root", str(h2.root), "--from", commit, timeout=300)
    assert restored.returncode == 0, (restored.stdout, restored.stderr)
    adopted = export_run(h2, "--adopt")
    assert adopted.returncode == 0, (adopted.stdout, adopted.stderr)
    shutil.move(str(aside), str(marker))
    assert run_git(h2, h2.root, "status", "--porcelain", "--", ".taskmaster-server.json") == ""   # the tracked marker is back as committed
    with native_connection(h2.root) as connection, Repository(connection).snapshot() as snapshot:
        doc = native_documents.retrieve(snapshot, "task", TASK, sections=["spec"])
    provenance = doc["provenance"]["spec"]
    assert provenance["origin"] == "restore" and provenance["imported_by"] == "ann", provenance
    principals = list((h2.root / ".taskmaster" / "local" / "serve").glob("principals*")) if (
        h2.root / ".taskmaster" / "local" / "serve").exists() else []
    assert principals == [] and not (h2.root / ".taskmaster" / "local" / "serve" / "operator.token").exists()
