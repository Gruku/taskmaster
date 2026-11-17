# User intent: prove `serve init --from` (team-server-014 Steps 7-8): a packed bundle installs on the host clone byte for
# byte with the right identity and a first export, every pre-flight refusal writes nothing, and a rerun after a crash at
# any stage ends where an uninterrupted init would.
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from taskmaster import server_fence as fence
from taskmaster import server_marker as sm
from taskmaster.serve import bundle as bundles
from taskmaster.serve import migration
from taskmaster.serve import state_export as se
from taskmaster.serve.config import ServeRefused, ServeUnavailable

import migration_helpers as mh
from migration_helpers import URL, fingerprint, g, run
from test_native_service import root  # noqa: F401  (fixture)

pytestmark = pytest.mark.xdist_group("heavy_processes")
TEMPLATE: dict = {}


@pytest.fixture(autouse=True)
def _fresh_fence(monkeypatch):
    fence.reset_for_tests()
    monkeypatch.setattr(migration, "CHECKPOINT", lambda stage: None)
    yield
    leaked = fence.reset_for_tests()
    assert not leaked, f"leaked host leases: {[lease.root for lease in leaked]}"


def build_template(root, base: Path) -> dict:
    """A source packed once per worker, and a host clone that has pulled the pack commit (copied for each test)."""
    src = mh.make_source(root)
    mh.run(src, "config", "core.autocrlf", "false")
    base.mkdir()
    host = base / "host"
    run(base, "clone", "-q", str(src), str(host))
    for key, value in (("user.name", "Host"), ("user.email", "host@example.invalid"), ("commit.gpgsign", "false")):
        run(host, "config", key, value)
    bundle = base / "proj.tmbundle"
    migration.pack(src, url=URL, out=bundle)
    fence.reset_for_tests()
    run(host, "pull", "-q", "--ff-only", "origin", "main")
    return {"src": src, "host": host, "bundle": bundle}


@pytest.fixture
def parts(root, tmp_path_factory):  # noqa: F811
    if "template" not in TEMPLATE:
        TEMPLATE["template"] = build_template(root, tmp_path_factory.mktemp("template") / "t")
    return TEMPLATE["template"]


def point_server_at(monkeypatch, target):
    """`load_server_module` binds one root per process; tests re-point the module the way conftest's twins do."""
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "ROOT", Path(target).resolve())


@pytest.fixture
def host(parts, tmp_path, monkeypatch):
    target = tmp_path / "host"
    shutil.copytree(parts["host"], target)
    point_server_at(monkeypatch, target)
    return target


@pytest.fixture
def bundle(parts):
    return parts["bundle"]


def refused(host, bundle, code=2, match=None, **kwargs):
    before = fingerprint(host)
    with pytest.raises(ServeRefused, match=match) as caught:
        migration.init(host, bundle=bundle, **kwargs)
    assert caught.value.exit_code == code, caught.value
    assert fingerprint(host) == before
    return caught.value


def tree_files(host):
    return {p.relative_to(host / ".taskmaster").as_posix(): p.read_bytes()
            for p in sorted((host / ".taskmaster").rglob("*")) if p.is_file()
            and not p.relative_to(host / ".taskmaster").as_posix().startswith("local/")
            and not p.name.startswith("store.db")}


# -- the happy path ------------------------------------------------------------------------------------

def test_init_installs_the_bundle_byte_exact_with_the_identity_and_a_first_export(host, bundle):
    manifest = mh.manifest_of(bundle)
    report = migration.init(host, bundle=bundle)
    assert report["state"] == "installed" and report["store_id"] == manifest["store_id"]
    with bundles.open_bundle(bundle) as opened:
        for entry in opened.tree():
            data = (host / ".taskmaster" / Path(*entry["path"].split("/"))).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
    store = host / ".taskmaster" / "local" / "store.db"
    with contextlib.closing(sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True)) as connection:
        assert dict(connection.execute("SELECT key,value FROM native_manifest"))["store_id"] == manifest["store_id"]
        assert connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0] == manifest["high_seq"]
        documents = connection.execute("SELECT COUNT(*) FROM external_documents").fetchone()[0]
    assert documents == manifest["counts"]["external_document"] == 1
    assert fence.read_host_id(host) == manifest["marker"]["host_id"] == sm.server_mode(host).host_id
    assert sm.read_latch(host) is not None
    message = g(host, "log", "-1", "--format=%B", "taskmaster-state")
    assert f"Taskmaster-Store: {manifest['store_id']}" in message
    files = set(g(host, "ls-tree", "-r", "--name-only", "taskmaster-state").splitlines())
    assert {e["path"] for e in manifest["tree"] if e["role"] == "projection"} <= files
    assert any(name.startswith("external-documents/") for name in files)
    assert migration.load_journal(host, migration.INIT_JOURNAL)["stage"] == "done"
    assert report["host_pull_rule"] == migration.HOST_PULL_RULE and report["single_use"] == migration.SINGLE_USE
    assert report["rollout_rule"] == migration.rollout_rule()
    assert migration.init(host, bundle=bundle) == report                      # done: the same report again


def test_init_records_the_pre_team_cutoff(host, bundle):
    """027 D6: events at or below the bundle's high-water seq are pre-team history."""
    manifest = mh.manifest_of(bundle)
    migration.init(host, bundle=bundle)
    assert migration.load_journal(host, migration.INIT_JOURNAL)["pre_team_through_seq"] == manifest["high_seq"]


def test_init_pushes_the_export_to_a_named_remote(host, bundle, tmp_path):
    backup = tmp_path / "backup.git"
    run(tmp_path, "init", "-q", "--bare", str(backup))
    run(host, "remote", "add", "backup", str(backup))
    report = migration.init(host, bundle=bundle, export_push="backup")
    assert report["export"]["push"] == "backup"
    assert g(backup, "rev-parse", "--verify", "refs/heads/taskmaster-state")


def test_no_export_skips_the_branch(host, bundle):
    report = migration.init(host, bundle=bundle, no_export=True)
    assert report["export"] is None
    assert g(host, "for-each-ref", "refs/heads/taskmaster-state") == ""


def test_the_marker_and_the_host_id_agree_so_the_daemon_binding_check_passes(host, bundle):
    migration.init(host, bundle=bundle)
    from taskmaster.serve import daemon
    assert sm.server_mode(host).host_id == fence.read_host_id(host)
    checker = getattr(daemon.ServeDaemon, "_check_marker", None)
    assert checker is not None


# -- pre-flight refusals write nothing -----------------------------------------------------------------

def test_h1_not_the_top_level(host, bundle):
    sub = host / "pkg"
    sub.mkdir()
    assert "top level" in str(refused(sub, bundle))


def test_h2_a_bundle_that_does_not_validate(host, tmp_path):
    junk = tmp_path / "junk.tmbundle"
    junk.write_bytes(b"not a zip")
    refused(host, junk, match="zip|bundle|manifest")


def test_h3_head_without_the_marker_or_with_another_one(host, bundle):
    run(host, "reset", "-q", "--hard", "HEAD~1")
    refused(host, bundle, match="matching")


def test_h3_the_working_marker_must_equal_heads(host, bundle):
    (host / sm.MARKER_NAME).write_text(sm.render(sm.Marker(URL, "cd" * 16)), encoding="utf-8")
    refused(host, bundle, match="differs from HEAD")


def test_h3_passes_with_a_crlf_working_marker(host, bundle):
    path = host / sm.MARKER_NAME
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert migration.init(host, bundle=bundle)["state"] == "installed"


def test_h4_the_pack_commit_must_be_an_ancestor(host, bundle):
    marker = (host / sm.MARKER_NAME).read_bytes()
    run(host, "checkout", "-q", "--orphan", "other")
    (host / sm.MARKER_NAME).write_bytes(marker)
    run(host, "add", "-f", sm.MARKER_NAME)
    run(host, "commit", "-q", "-m", "marker only")
    refused(host, bundle, match="pack commit")


def test_h5_a_head_that_tracks_taskmaster(host, bundle):
    (host / ".taskmaster").mkdir(exist_ok=True)
    (host / ".taskmaster" / "stray.md").write_text("x\n", encoding="utf-8")
    run(host, "add", "-f", ".taskmaster/stray.md")
    run(host, "commit", "-q", "-m", "retrack")
    refused(host, bundle, match="tracks .taskmaster")


def test_h6_an_existing_store_or_backlog(host, bundle):
    (host / ".taskmaster" / "local").mkdir(parents=True)
    (host / ".taskmaster" / "local" / "store.db").write_bytes(b"x")
    refused(host, bundle, match="already initialized")


def test_h6_a_token_made_earlier_is_allowed(host, bundle):
    serve = host / ".taskmaster" / "local" / "serve"
    serve.mkdir(parents=True)
    (serve / "operator.token").write_text("secret\n", encoding="utf-8")
    assert migration.init(host, bundle=bundle)["state"] == "installed"
    assert (serve / "operator.token").read_text(encoding="utf-8") == "secret\n"


def test_h7_a_host_id_for_another_server(host, bundle):
    serve = host / ".taskmaster" / "local" / "serve"
    serve.mkdir(parents=True)
    (serve / "host-id").write_text("ef" * 16 + "\n", encoding="ascii")
    refused(host, bundle, match="host-id")


def test_h8_export_settings_and_an_existing_branch(host, bundle):
    refused(host, bundle, match="remote", export_push="nope")
    run(host, "branch", "taskmaster-state")
    refused(host, bundle, match="already exists")


def test_h9_a_journal_for_another_bundle(host, bundle):
    (host / ".taskmaster" / "local").mkdir(parents=True)
    migration.save_journal(host, migration.INIT_JOURNAL, {"stage": "staged", "bundle_sha256": "0" * 64})
    refused(host, bundle, match="different bundle")


def test_a_lease_held_by_another_process_is_exit_69(host, bundle):
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from taskmaster import server_fence as f\n"
            "lease = f.host_lease(%r)\nprint('HELD', flush=True)\nsys.stdin.readline()\n" % (str(mh.PLUGIN), str(host)))
    (host / ".taskmaster" / "local").mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "HELD"
        error = refused(host, bundle, code=69)
        assert isinstance(error, ServeUnavailable)
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=30)


# -- Step 8: resume ------------------------------------------------------------------------------------

class Crash(Exception):
    pass


def crash_at(monkeypatch, stage):
    def checkpoint(name):
        if name == stage:
            raise Crash(name)
    monkeypatch.setattr(migration, "CHECKPOINT", checkpoint)


def no_crash(monkeypatch):
    monkeypatch.setattr(migration, "CHECKPOINT", lambda name: None)


@pytest.fixture
def reference(parts, tmp_path_factory, monkeypatch):
    if "reference" not in TEMPLATE:
        target = tmp_path_factory.mktemp("reference") / "host"
        shutil.copytree(parts["host"], target)
        point_server_at(monkeypatch, target)
        migration.init(target, bundle=parts["bundle"])
        fence.reset_for_tests()
        TEMPLATE["reference"] = {"files": tree_files(target), "state": g(target, "ls-tree", "-r", "taskmaster-state")}
    return TEMPLATE["reference"]


@pytest.mark.parametrize("stage", ["init_journal", "init_staged", "init_published", "init_verified", "init_exported"])
def test_a_rerun_after_a_crash_equals_an_uninterrupted_init(host, bundle, monkeypatch, stage, reference):
    point_server_at(monkeypatch, host)             # `reference` re-pointed the module while it built its own host
    crash_at(monkeypatch, stage)
    with pytest.raises(Crash):
        migration.init(host, bundle=bundle)
    fence.reset_for_tests()
    no_crash(monkeypatch)
    report = migration.init(host, bundle=bundle)
    assert report["state"] == "installed"
    assert tree_files(host) == reference["files"]
    assert migration.load_journal(host, migration.INIT_JOURNAL)["stage"] == "done"
    assert g(host, "ls-tree", "-r", "taskmaster-state")
    assert sm.read_latch(host) is not None and fence.read_host_id(host) == sm.server_mode(host).host_id


def test_events_appended_to_a_published_store_stop_the_resume_with_exit_1(host, bundle, monkeypatch):
    crash_at(monkeypatch, "init_published")
    with pytest.raises(Crash):
        migration.init(host, bundle=bundle)
    fence.reset_for_tests()
    with contextlib.closing(sqlite3.connect(host / ".taskmaster" / "local" / "store.db", timeout=30)) as connection:
        connection.execute("INSERT INTO domain_events(ts,session,tool,kind,id,op) "
                           "VALUES('2026-11-07T00:00:00Z','x','test','task','x','noop')")
        connection.commit()
    no_crash(monkeypatch)
    before = fingerprint(host)
    with pytest.raises(ServeRefused, match="written after a partial init") as caught:
        migration.init(host, bundle=bundle)
    assert caught.value.exit_code == 1
    assert fingerprint(host) == before


# -- Codex round: #4 owner-only files and dirs (POSIX), #5 destination links ---------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_installed_store_and_dirs_are_owner_only_even_under_a_022_umask(host, bundle):
    import os
    import stat
    old = os.umask(0o022)
    try:
        migration.init(host, bundle=bundle)
    finally:
        os.umask(old)
    local = host / ".taskmaster" / "local"
    assert stat.S_IMODE((local / "store.db").stat().st_mode) == 0o600
    assert stat.S_IMODE(local.stat().st_mode) == 0o700
    assert stat.S_IMODE((local / "serve").stat().st_mode) == 0o700


def test_a_linked_destination_directory_is_refused_before_anything_is_published(host, bundle, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (host / ".taskmaster").mkdir(exist_ok=True)
    try:
        (host / ".taskmaster" / "tasks").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    before = sorted(p.name for p in outside.iterdir())
    with pytest.raises(ServeRefused, match="link"):
        migration.init(host, bundle=bundle)
    assert sorted(p.name for p in outside.iterdir()) == before == []
    assert not (host / ".taskmaster" / "local" / "store.db").exists()
