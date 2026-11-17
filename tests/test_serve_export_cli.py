# User intent: the operator drives team-server-013's backup from the host: `serve export status` shows a stalled or
# failing backup, `run` exports once with the daemon stopped, `restore-documents` replays stored documents keeping
# their uploader, the daemon runs the exporter on its own schedule, and a lost host can be rebuilt from the branch.
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

from taskmaster import store
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.actor_resolver import LOCAL
from taskmaster.coordinator.service import Coordinator
from taskmaster.serve import cli, state_export as se
from taskmaster.serve.auth import ensure_operator_token
from taskmaster.serve.config import ServeConfig, ServeRefused, validate
from taskmaster.serve.daemon import ServeCoordinator, ServeDaemon, export_settings
from native_git_helpers import git, init_repo
from native_twins import activate_native, committed, native_connection, point_server_at
from test_native_service_build import root  # noqa: F401  (fixture)

pytestmark = pytest.mark.xdist_group("heavy_processes")
TASK = "test-epic-001"
BRANCH = "taskmaster-state"
GIT_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}


@pytest.fixture
def repo(root):  # noqa: F811
    init_repo(root)
    return Path(root).resolve()


@pytest.fixture
def remote(repo, tmp_path_factory):
    path = tmp_path_factory.mktemp("remote") / "backup.git"
    git(repo, "init", "-q", "--bare", str(path))
    git(repo, "remote", "add", "backup", str(path))
    return path


def envelope(owner, key, operation, arguments):
    return {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": "export-cli-tests",
            "request_id": key, "operation": operation, "arguments": arguments, "expected_revisions": []}


def submit(owner, key, operation, arguments):
    receipt = owner.submit(envelope(owner, key, operation, arguments), origin=LOCAL).result(60)
    owner.flush(receipt["commit_seq"], timeout=60)
    return receipt


def documents(owner):
    """A task with an uploaded document (restored, so it carries ann as uploader) and a file import."""
    submit(owner, "d1", "task.update", {"id": TASK, "field": "docs", "value": "spec:docs/specs/x.md"})
    submit(owner, "d2", "task.update", {"id": TASK, "field": "docs", "value": "plan:docs/plans/y.md"})
    submit(owner, "u1", "document.import", {"kind": "task", "id": TASK, "section": "spec", "path": "docs/specs/x.md",
                                            "body": "Uploaded spec.\n", "restore": True, "source": "upload",
                                            "imported_by": "ann"})
    submit(owner, "f1", "document.import", {"kind": "task", "id": TASK, "section": "plan", "path": "docs/plans/y.md",
                                            "body": "Plan from a file.\n"})


def run_cli(*argv):
    return cli.main(list(argv))


# ── status ──────────────────────────────────────────────────────────────────


def test_status_cli_prints_json_without_urls_and_exits_by_health(repo, capsys):
    from taskmaster.serve.auth import serve_directory
    serve_directory(repo)
    assert run_cli("export", "status", "--root", str(repo)) == 2
    capsys.readouterr()
    se.save_state(repo, {"branch": BRANCH, "remote": "backup", "interval": 900, "started": 0, "last_ok": 9e12,
                         "push_state": "push_failed", "push_failures": 1, "push_error": "x <remote:backup> y"})
    assert run_cli("export", "status", "--root", str(repo)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["remote"] == "backup" and report["problems"]
    assert run_cli("export") == 2


# ── run ─────────────────────────────────────────────────────────────────────


def test_run_refuses_while_the_store_is_owned_and_exports_once_when_it_is_not(repo, remote, capsys):
    with Coordinator(repo):
        assert run_cli("export", "run", "--root", str(repo), "--export-push", "backup") == 69
    capsys.readouterr()
    assert run_cli("export", "run", "--root", str(repo), "--export-push", "backup") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "committed" and out["pushed"] == out["commit"]
    assert git(remote, "rev-parse", BRANCH).strip() == out["commit"]
    assert run_cli("export", "run", "--root", str(repo), "--export-branch", "main") == 2


def test_run_adopt_records_the_adoption(repo, capsys):
    assert run_cli("export", "run", "--root", str(repo)) == 0
    tip = git(repo, "rev-parse", BRANCH).strip()
    tree = git(repo, "rev-parse", f"{tip}^{{tree}}").strip()
    foreign = subprocess.run(["git", "commit-tree", tree, "-p", tip, "-m",
                              "x\n\nTaskmaster-Export: 1\nTaskmaster-Store: older-store"], cwd=repo, env=GIT_ENV,
                             capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "update-ref", f"refs/heads/{BRANCH}", foreign)
    se.state_path(repo).unlink()
    capsys.readouterr()
    assert run_cli("export", "run", "--root", str(repo)) == 1                     # needs an explicit adoption
    assert "--adopt" in json.loads(capsys.readouterr().out)["reason"]
    assert run_cli("export", "run", "--root", str(repo), "--adopt") == 0
    assert json.loads(capsys.readouterr().out)["adopted"]["from_store"] == "older-store"


# ── restore-documents ───────────────────────────────────────────────────────


def _hand_backup(repo, entries, bodies):
    """A backup commit written by hand (an older build, or a tampered repo) holding the given manifest."""
    def blob(data):
        return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, env=GIT_ENV, input=data,
                              capture_output=True, check=True).stdout.decode().strip()
    lines = [f"100644 blob {blob((json.dumps(entries) + chr(10)).encode())}\texternal-documents/index.json"]
    lines += [f"100644 blob {blob(data)}\t{path}" for path, data in bodies.items()]
    index = repo / ".git" / "hand-index"
    subprocess.run(["git", "update-index", "--add", "--index-info"], cwd=repo, input="\n".join(lines).encode(),
                   env={**GIT_ENV, "GIT_INDEX_FILE": str(index)}, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=repo, env={**GIT_ENV, "GIT_INDEX_FILE": str(index)},
                          capture_output=True, text=True, check=True).stdout.strip()
    index.unlink()
    return subprocess.run(["git", "commit-tree", tree, "-m", "hand"], cwd=repo, env=GIT_ENV, capture_output=True,
                          text=True, check=True).stdout.strip()


def test_restore_documents_keeps_the_uploader_and_refuses_tampered_or_secret_entries(repo, capsys):
    with Coordinator(repo) as owner:
        submit(owner, "d1", "task.update", {"id": TASK, "field": "docs", "value": "spec:docs/specs/x.md"})
        submit(owner, "d2", "task.update", {"id": TASK, "field": "docs", "value": "plan:docs/plans/y.md"})
        submit(owner, "d3", "task.update", {"id": TASK, "field": "docs", "value": "design:.env"})
    body = b"Restored spec.\n"
    entry = {"kind": "task", "id": TASK, "path": "docs/specs/x.md", "imported_seq": 7}
    entries = [dict(entry, section="spec", sha256=hashlib.sha256(body).hexdigest(), source="upload",
                    imported_by="ann"),
               dict(entry, section="plan", path="docs/plans/y.md", sha256="0" * 64, source="file", imported_by=None),
               dict(entry, section="design", path=".env", sha256=hashlib.sha256(b"T=1\n").hexdigest(),
                    source="weird", imported_by="eve")]
    bodies = {f"external-documents/task/{TASK}/spec.md": body,
              f"external-documents/task/{TASK}/plan.md": b"tampered\n",
              f"external-documents/task/{TASK}/design.md": b"T=1\n"}
    commit = _hand_backup(repo, entries, bodies)
    capsys.readouterr()
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", commit) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["restored"] == 1, report
    assert {(r["document"], r["rule"]) for r in report["refused"]} == {
        (f"task/{TASK}/plan", "blob does not match its sha256"), (f"task/{TASK}/design", "dot path")}
    from taskmaster.native import documents as native_documents
    with native_connection(repo) as connection:
        rows = connection.execute("SELECT section,body,imported_seq FROM external_documents").fetchall()
        assert [(r[0], r[1]) for r in rows] == [("spec", "Restored spec.\n")]
        facts = native_documents.import_origin(connection, rows[0][2])
    assert (facts["origin"], facts["source"], facts["imported_by"]) == ("restore", "upload", "ann")
    capsys.readouterr()
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", commit) == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] == 1                   # re-running is a no-op
    assert run_cli("export", "restore-documents", "--root", str(repo), "--from", "main") == 2
    for bad in ("a..b", "HEAD", "main", "Taskmaster-State"):                      # gate M3 and I1
        if bad == "Taskmaster-State":
            git(repo, "branch", "taskmaster-state")
        assert run_cli("export", "restore-documents", "--root", str(repo), "--from", commit,
                       "--export-branch", bad) == 2, bad


# ── daemon integration ──────────────────────────────────────────────────────


def test_config_validation_for_export_flags(tmp_path):
    with pytest.raises(ServeRefused, match="--export-push needs"):
        validate(ServeConfig(root=tmp_path, export_push="backup"))
    with pytest.raises(ServeRefused, match="--export-interval"):
        validate(ServeConfig(root=tmp_path, export_interval=5))
    assert validate(ServeConfig(root=tmp_path, export_interval=None)).export_interval is None


def test_export_settings_default_on_only_in_a_main_checkout(repo, tmp_path_factory, caplog):
    assert export_settings(repo, ServeConfig(root=repo)) is None                    # ServeConfig default: off
    on = export_settings(repo, ServeConfig(root=repo, export_interval=None))
    assert (on.branch, on.interval, on.push) == (BRANCH, 900, None)
    linked = tmp_path_factory.mktemp("linked") / "wt"
    git(repo, "worktree", "add", "-q", str(linked), "-b", "side")
    (linked / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)
    assert export_settings(linked.resolve(), ServeConfig(root=linked, export_interval=None)) is None
    assert export_settings(linked.resolve(), ServeConfig(root=linked, export_interval=60)).interval == 60
    with pytest.raises(ServeRefused, match="linked worktree needs an explicit"):           # gate M1
        export_settings(linked.resolve(), ServeConfig(root=linked, export_interval=None, export_push="backup"))
    bare = tmp_path_factory.mktemp("nogit")
    (bare / ".taskmaster" / "local").mkdir(parents=True)
    assert export_settings(bare, ServeConfig(root=bare, export_interval=None)) is None
    with pytest.raises(ServeRefused, match="not the top level"):
        export_settings(bare, ServeConfig(root=bare, export_interval=60))
    with pytest.raises(ServeRefused, match="no configured remote"):
        export_settings(repo, ServeConfig(root=repo, export_interval=60, export_push="nowhere"))


@pytest.mark.parametrize("error", [se.ExportError("git rev-parse timed out"), PermissionError(13, "Access denied")])
def test_start_time_export_errors_are_clean_refusals(repo, monkeypatch, error):
    """Gate M4: a Git timeout or an OS error while checking the settings is exit 2, never a traceback."""
    def fail(settings, **kwargs):
        raise error

    monkeypatch.setattr(se, "check_settings", fail)
    with pytest.raises(ServeRefused, match="state export"):
        export_settings(repo, ServeConfig(root=repo, export_interval=60))
    assert run_cli("export", "run", "--root", str(repo)) in (1, 2)


def test_the_daemon_runs_the_exporter_and_stops_it_first(repo, remote, monkeypatch):
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.setattr(se, "FIRST_DELAY", 0.2)
    ensure_operator_token(repo)
    out = io.StringIO()
    daemon = ServeDaemon(ServeConfig(root=repo, port=0, export_interval=60, export_push="backup"), stdout=out).start()
    codes = []
    thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
    thread.start()
    try:
        assert daemon.ready.wait(60)
        ready = json.loads(out.getvalue().splitlines()[0])
        assert ready["export"] == {"branch": BRANCH, "interval": 60, "push": "backup"}
        exporter = daemon.exporter
        deadline = __import__("time").monotonic() + 60
        while __import__("time").monotonic() < deadline and not se.load_state(repo).get("pushed"):
            __import__("time").sleep(0.2)
        record = se.load_state(repo)
        assert record["pushed"] == record["commit"] == git(remote, "rev-parse", BRANCH).strip()
    finally:
        daemon.request_stop("test")
        thread.join(60)
    assert not thread.is_alive() and codes == [0]
    assert not exporter.thread.is_alive() and daemon.exporter is None


# ── step 10: restore round trip ─────────────────────────────────────────────


def _extract(repo, commit, dest):
    data = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", commit,
                           "--", ".", ":(exclude)external-documents"], cwd=repo, env=GIT_ENV,
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            assert not member.name.startswith(("/", "..")) and ".." not in Path(member.name).parts
        archive.extractall(dest, filter="data")


def test_a_lost_host_is_rebuilt_from_the_backup_branch(repo, remote, tmp_path_factory, monkeypatch, capsys):
    git(repo, "config", "core.autocrlf", "true")
    with Coordinator(repo) as owner:
        documents(owner)
        submit(owner, "t1", "task.update", {"id": TASK, "field": "title", "value": "Before the disaster"})
    assert run_cli("export", "run", "--root", str(repo), "--export-push", "backup") == 0
    source_commit = json.loads(capsys.readouterr().out)["commit"]
    source_entities = {key: (doc.get("title"), doc.get("status")) for key, (doc, _, _) in committed(repo).items()}
    exported = {rel: (repo / ".taskmaster" / rel).read_bytes()
                for rel in git(repo, "ls-tree", "-r", "--name-only", source_commit).splitlines()
                if not rel.startswith("external-documents/")}
    for each in git(repo, "rev-list", BRANCH).split():
        names = git(repo, "ls-tree", "-r", "--name-only", each).splitlines()
        assert not any(n.startswith("local/") or se.tree_path_refusal(n) for n in names)

    host = tmp_path_factory.mktemp("newhost") / "project"
    host.mkdir()
    git(host, "init", "-q", "-b", "main")
    git(host, "remote", "add", "backup", str(remote))
    git(host, "fetch", "-q", "backup", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")
    (host / ".taskmaster").mkdir()
    _extract(host, BRANCH, host / ".taskmaster")
    (host / ".taskmaster" / "local").mkdir()
    for rel, data in exported.items():
        assert (host / ".taskmaster" / rel).read_bytes() == data, rel           # byte-exact (M4)
    point_server_at(monkeypatch, host)
    store.open_store(root=host)                                                  # adopts the files
    store.reset_for_tests()
    activate_native(host)
    restored = {key: (doc.get("title"), doc.get("status")) for key, (doc, _, _) in committed(host).items()}
    assert restored == source_entities
    capsys.readouterr()
    assert run_cli("export", "restore-documents", "--root", str(host), "--from", BRANCH) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["restored"] == 2 and not report["refused"] and not report["failed"]
    from taskmaster.native import documents as native_documents
    with native_connection(host) as connection:
        rows = {r[0]: (r[1], r[2], r[3]) for r in connection.execute(
            "SELECT section,body,content_hash,imported_seq FROM external_documents")}
        facts = {section: native_documents.import_origin(connection, seq) for section, (_, _, seq) in rows.items()}
    assert rows["spec"][:2] == ("Uploaded spec.\n", hashlib.sha256(b"Uploaded spec.\n").hexdigest())
    assert (facts["spec"]["source"], facts["spec"]["imported_by"]) == ("upload", "ann")      # survives the restore
    assert facts["plan"]["source"] == "file" and facts["spec"]["origin"] == "restore"
    capsys.readouterr()
    assert run_cli("export", "run", "--root", str(host), "--export-push", "backup") == 1   # needs --adopt
    assert run_cli("export", "run", "--root", str(host), "--export-push", "backup", "--adopt") == 0
    record = se.load_state(host)
    assert record["pushed"] == record["commit"] == git(remote, "rev-parse", BRANCH).strip()
    git(remote, "merge-base", "--is-ancestor", source_commit, BRANCH)            # a fast-forward of the old history
    manifest = json.loads(subprocess.run(["git", "cat-file", "blob", f"{BRANCH}:external-documents/index.json"],
                                         cwd=host, env=GIT_ENV, capture_output=True, check=True).stdout)
    first = json.loads(subprocess.run(["git", "cat-file", "blob", f"{source_commit}:external-documents/index.json"],
                                      cwd=host, env=GIT_ENV, capture_output=True, check=True).stdout)
    assert {(m["section"], m["source"], m["imported_by"]) for m in manifest} == {
        ("spec", "upload", "ann"), ("plan", "file", None)}                        # a second backup keeps them
    assert {m["section"]: m["imported_seq"] for m in manifest} == \
        {m["section"]: m["imported_seq"] for m in first}                           # and the original seq (gate M5)
