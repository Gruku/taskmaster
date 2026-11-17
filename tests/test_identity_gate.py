# User intent: the coordinator, never a prompt, derives this clone's team identity on the first team-mode
# write and refuses attributed writes it cannot attribute; git is read only through bounded, non-interactive
# probes, and the gate is pinned against a real native store without the service.
from __future__ import annotations

import pytest

from taskmaster.bounded_run import run_bounded
from taskmaster.coordinator import git as coordinator_git
from taskmaster.coordinator import identity_gate
from taskmaster.coordinator.git import GitRefused
from taskmaster.team_identity import GitIdentity


# ── Step 4: reading git identity ────────────────────────────────────────────

@pytest.mark.slow
def test_read_git_identity_uses_the_repository_config(tmp_path):
    for args in (["init", "-q"], ["config", "user.name", "John Doe"], ["config", "user.email", "jd@corp.io"]):
        completed = run_bounded(["git", *args], cwd=tmp_path, timeout=60)
        assert completed.returncode == 0, completed.stderr
    assert identity_gate.read_git_identity(tmp_path) == GitIdentity("John Doe", "jd@corp.io")


def _record_probes(monkeypatch, answers):
    calls = []

    def probe(root, *args, **kwargs):
        calls.append((args, kwargs))
        answer = answers[args[-1]]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(coordinator_git, "probe", probe)
    return calls


def test_read_git_identity_runs_only_bounded_config_probes(monkeypatch, tmp_path):
    calls = _record_probes(monkeypatch, {"user.name": (0, b"John Doe\n"), "user.email": (0, b"jd@corp.io\n")})
    assert identity_gate.read_git_identity(tmp_path) == GitIdentity("John Doe", "jd@corp.io")
    assert [args for args, _ in calls] == [("config", "--get", "user.name"), ("config", "--get", "user.email")]
    assert all(kwargs == {"ok": (0, 1), "timeout": identity_gate.GIT_TIMEOUT} for _, kwargs in calls)


def test_unset_git_values_read_as_empty(monkeypatch, tmp_path):
    _record_probes(monkeypatch, {"user.name": (1, b""), "user.email": (1, b"")})
    assert identity_gate.read_git_identity(tmp_path) == GitIdentity("", "")


def test_git_probe_failure_propagates(monkeypatch, tmp_path):
    _record_probes(monkeypatch, {"user.name": GitRefused("git config timed out"), "user.email": (0, b"x@y.io")})
    with pytest.raises(GitRefused, match="git config timed out"):
        identity_gate.read_git_identity(tmp_path)


# ── Step 5: the gate against a real native store, without the service ──────

import re
import sys
from dataclasses import replace

import yaml

from taskmaster import backlog_server as bs
from taskmaster.coordinator import protocol
from taskmaster.coordinator.identity_gate import CALLER_SCOPE, IdentityGate
from taskmaster.native import commands, upgrade
from taskmaster.native.queries import Repository
from taskmaster.team_identity import IDENTITY_REL, IdentityRefused, read_identity, write_identity
from native_twins import commit_only, make_twins


class GitStub:
    def __init__(self, name="John Doe", email="jd@corp.io"):
        self.name, self.email, self.error, self.calls = name, email, None, 0

    def __call__(self, root):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return GitIdentity(self.name, self.email)


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch,
                       lambda: bs.backlog_add_task(title="Gate task", epic="test-epic", phase="dev"), visibility=None)
    with twins.at(twins.native):
        yield twins.native


@pytest.fixture
def team_on(monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)


@pytest.fixture
def git_stub(monkeypatch):
    stub = GitStub()
    monkeypatch.setattr(identity_gate, "read_git_identity", stub)
    return stub


@pytest.fixture
def connection(root):
    connection = protocol.connect(root)
    try:
        yield connection
    finally:
        connection.close()


def _store_id(connection):
    return connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]


def request(connection, key="one", title="Changed"):
    return {"protocol": 2, "store_id": _store_id(connection), "caller_scope": "gate-tests", "request_id": key,
            "operation": "task.patch", "arguments": {"id": "test-epic-001", "set": {"title": title}},
            "expected_revisions": []}


def gate_for(connection, root, **kwargs):
    return IdentityGate(root, store_id=_store_id(connection), **kwargs)


def schema_version(connection):
    return connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0]


def max_seq(connection):
    return connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]


def profiles(connection):
    return [row[0] for row in connection.execute(
        "SELECT public_id FROM entity_core WHERE kind='profile' AND deleted=0 ORDER BY public_id")]


def profile(connection, handle):
    with Repository(connection).snapshot() as snapshot:
        return snapshot.get("profile", handle)


def create_profile(connection, handle, emails):
    upgrade.upgrade(connection)
    commit_only(connection, "profile.create", {"handle": handle, "name": "Someone Else", "emails": emails,
                                               "created": "2026-10-30T12:00:00Z"})


def identity_file(root):
    return root / ".taskmaster" / IDENTITY_REL


def test_first_team_mode_write_bootstraps_identity_and_profile(root, connection, team_on, git_stub):
    assert gate_for(connection, root).admit(connection, request(connection)) is True
    identity = read_identity(root / ".taskmaster")
    assert identity.handle == "jdoe" and identity.confirmed is False
    assert re.fullmatch(r"[0-9a-f]{16}", identity.clone_id)
    assert yaml.safe_load(identity_file(root).read_text(encoding="utf-8"))["source"]["git_name"] == "John Doe"
    assert profile(connection, "jdoe")["fields"]["emails"] == ["jd@corp.io"]
    assert connection.execute("SELECT session FROM domain_events WHERE kind='profile'").fetchall() == [
        (CALLER_SCOPE,)]
    assert not connection.in_transaction


def test_first_team_mode_write_upgrades_the_store_to_schema_5(root, connection, team_on, git_stub):
    stages = []

    def checkpoint(stage):
        # What another connection sees at each stage: the upgrade commits first (the joining
        # clone then reads teammates' profiles), then identity.yaml, then the profile.
        peer = protocol.connect(root, readonly=True)
        try:
            stages.append((stage, schema_version(peer), profiles(peer)))
        finally:
            peer.close()
    assert schema_version(connection) == "3"
    assert gate_for(connection, root, checkpoint=checkpoint).admit(connection, request(connection)) is True
    assert stages == [("schema_upgraded", "5", []), ("identity_written", "5", [])]
    assert schema_version(connection) == "5" and profiles(connection) == ["jdoe"]


def test_refused_bootstrap_leaves_schema_3(root, connection, team_on, git_stub):
    git_stub.name = "Your Name"
    with pytest.raises(IdentityRefused):
        gate_for(connection, root).admit(connection, request(connection))
    assert schema_version(connection) == "3"


def test_crash_after_upgrade_before_profile_recovers(root, connection, team_on, git_stub):
    crash = {"on": True}

    def checkpoint(stage):
        if stage == "schema_upgraded" and crash["on"]:
            raise RuntimeError("crashed after the upgrade")
    gate = gate_for(connection, root, checkpoint=checkpoint)
    with pytest.raises(RuntimeError, match="crashed"):
        gate.admit(connection, request(connection))
    assert schema_version(connection) == "5" and profiles(connection) == []
    assert not identity_file(root).exists()
    assert not connection.in_transaction
    crash["on"] = False
    assert gate.admit(connection, request(connection)) is True
    assert profiles(connection) == ["jdoe"]


def test_second_write_reuses_the_cached_identity(root, connection, team_on, git_stub):
    gate = gate_for(connection, root)
    assert gate.admit(connection, request(connection)) is True
    assert gate.admit(connection, request(connection, "two")) is False
    assert git_stub.calls == 1 and profiles(connection) == ["jdoe"]


def test_identity_is_sticky_across_a_git_name_change(root, connection, team_on, git_stub, monkeypatch):
    gate_for(connection, root).admit(connection, request(connection))
    before = identity_file(root).read_bytes()
    renamed = GitStub("Johnny D", "jd@corp.io")
    monkeypatch.setattr(identity_gate, "read_git_identity", renamed)
    restarted = gate_for(connection, root)
    assert restarted.admit(connection, request(connection, "two")) is False
    assert renamed.calls == 0
    assert identity_file(root).read_bytes() == before
    assert profiles(connection) == ["jdoe"] and restarted.current().handle == "jdoe"


def test_known_profile_with_another_email_suffixes_the_handle(root, connection, team_on, git_stub):
    create_profile(connection, "jdoe", ["other@x.io"])
    assert gate_for(connection, root).admit(connection, request(connection)) is True
    assert read_identity(root / ".taskmaster").handle == "jdoe2"
    assert profiles(connection) == ["jdoe", "jdoe2"]


def test_known_profile_with_the_same_email_is_reused_without_a_create(root, connection, team_on, git_stub):
    create_profile(connection, "jdoe", ["jd@corp.io"])
    high = max_seq(connection)
    assert gate_for(connection, root).admit(connection, request(connection)) is False
    assert read_identity(root / ".taskmaster").handle == "jdoe"
    assert max_seq(connection) == high and profiles(connection) == ["jdoe"]


@pytest.mark.parametrize("name", ["", "   ", "Your Name"])
def test_degenerate_git_name_refuses_attribution_bearing_writes(root, connection, team_on, git_stub, name):
    git_stub.name = name
    high = max_seq(connection)
    with pytest.raises(IdentityRefused, match="taskmaster:init-team"):
        gate_for(connection, root).admit(connection, request(connection))
    assert not identity_file(root).exists()
    assert profiles(connection) == [] and max_seq(connection) == high


def test_degenerate_name_does_not_block_machine_local_operations(root, connection, team_on, git_stub):
    git_stub.name = "Your Name"
    local = {"protocol": 2, "store_id": _store_id(connection), "caller_scope": "linear", "request_id": "settle-1",
             "operation": "linear.settle", "arguments": {}, "expected_revisions": []}
    assert gate_for(connection, root).admit(connection, local) is False
    assert git_stub.calls == 0


def test_fixing_the_git_name_lets_the_retry_through(root, connection, team_on, git_stub):
    gate = gate_for(connection, root)
    git_stub.name = "Your Name"
    with pytest.raises(IdentityRefused):
        gate.admit(connection, request(connection))
    git_stub.name = "John Doe"
    assert gate.admit(connection, request(connection)) is True
    assert profiles(connection) == ["jdoe"]


def test_unreadable_git_refuses_and_backs_off_after_a_timeout(root, connection, team_on, git_stub):
    now = [1000.0]
    git_stub.error = GitRefused("git config timed out")
    gate = gate_for(connection, root, clock=lambda: now[0])
    with pytest.raises(IdentityRefused, match=re.escape("could not be read (git config timed out)")):
        gate.admit(connection, request(connection))
    assert git_stub.calls == 1
    now[0] += 30
    with pytest.raises(IdentityRefused, match="could not be read"):
        gate.admit(connection, request(connection))
    assert git_stub.calls == 1
    now[0] += 31
    with pytest.raises(IdentityRefused, match="could not be read"):
        gate.admit(connection, request(connection))
    assert git_stub.calls == 2


def test_other_git_failures_are_not_backed_off(root, connection, team_on, git_stub):
    git_stub.error = GitRefused("git executable not found on PATH")
    gate = gate_for(connection, root, clock=lambda: 1000.0)
    for _ in range(2):
        with pytest.raises(IdentityRefused, match="not found on PATH"):
            gate.admit(connection, request(connection))
    assert git_stub.calls == 2


def test_team_mode_off_is_a_no_op(root, connection, git_stub, monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: False)
    git_stub.name = "Your Name"
    high = max_seq(connection)
    assert gate_for(connection, root).admit(connection, request(connection)) is False
    assert not identity_file(root).exists() and git_stub.calls == 0
    assert max_seq(connection) == high and schema_version(connection) == "3"


def _manifest(mode):
    return {"schema_version": 1, "meta": {"name": "Gate", "slug": "gate"}, "team": {"sync": {"mode": mode}}}


def test_gate_passes_the_db_project_row(root, connection, git_stub, monkeypatch):
    seen = []
    monkeypatch.setattr(identity_gate, "team_mode_enabled",
                        lambda root, *, project_doc=None: seen.append(project_doc) or False)
    gate = gate_for(connection, root)
    gate.admit(connection, request(connection))
    assert seen == [{}]
    commit_only(connection, "project.set", {"document": _manifest("manual")})
    gate.admit(connection, request(connection, "two"))
    with Repository(connection).snapshot() as snapshot:
        assert seen[-1] == snapshot.get("project", "__project__")["fields"]
    assert seen[-1]["team"] == {"sync": {"mode": "manual"}}


def test_team_mode_follows_the_db_row_without_restart(root, connection, git_stub):
    commit_only(connection, "project.set", {"document": _manifest("off")})
    gate = gate_for(connection, root)
    assert gate.admit(connection, request(connection)) is False
    assert not identity_file(root).exists() and git_stub.calls == 0
    commit_only(connection, "project.set", {"document": _manifest("manual")})
    assert gate.admit(connection, request(connection, "two")) is True
    assert profiles(connection) == ["jdoe"]


def test_exported_project_yaml_is_not_read(root, connection, git_stub, monkeypatch):
    commit_only(connection, "project.set", {"document": _manifest("off")})
    (root / ".taskmaster" / "project.yaml").write_text(yaml.safe_dump(_manifest("manual")), encoding="utf-8")
    real, seen = identity_gate.team_mode_enabled, []

    def spy(path, *, project_doc=None):
        seen.append(project_doc)
        return real(path, project_doc=project_doc)
    monkeypatch.setattr(identity_gate, "team_mode_enabled", spy)
    assert gate_for(connection, root).admit(connection, request(connection)) is False
    assert not identity_file(root).exists() and git_stub.calls == 0
    assert seen and all(doc["team"]["sync"]["mode"] == "off" for doc in seen)


def test_team_mode_cache_follows_the_project_row(root, connection, git_stub, monkeypatch):
    real, reads = identity_gate._project_doc, []
    monkeypatch.setattr(identity_gate, "_project_doc", lambda conn: reads.append(1) or real(conn))
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: False)
    gate = gate_for(connection, root)
    gate.admit(connection, request(connection))
    gate.admit(connection, request(connection, "two"))
    assert len(reads) == 1
    commit_only(connection, "project.set", {"document": _manifest("off")})
    gate.admit(connection, request(connection, "three"))
    assert len(reads) == 2


def test_crash_between_identity_file_and_profile_recovers(root, connection, team_on, git_stub, monkeypatch):
    def crash(stage):
        if stage == "identity_written":
            raise RuntimeError("crashed after the identity file")
    with pytest.raises(RuntimeError, match="crashed"):
        gate_for(connection, root, checkpoint=crash).admit(connection, request(connection))
    identity = read_identity(root / ".taskmaster")
    assert identity is not None and profiles(connection) == []
    no_git = GitStub()
    monkeypatch.setattr(identity_gate, "read_git_identity", no_git)
    assert gate_for(connection, root).admit(connection, request(connection)) is True
    assert no_git.calls == 0
    assert profile(connection, "jdoe")["fields"]["created"] == identity.created
    assert connection.execute("SELECT request_id FROM command_receipts WHERE caller_scope=?", (CALLER_SCOPE,)
                              ).fetchall() == [(f"profile-{identity.clone_id}-jdoe",)]


def test_invalid_identity_file_is_refused_and_kept(root, connection, team_on, git_stub):
    path = identity_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("version: 1\nhandle: JDoe\nclone_id: 3f9a2c4e5b6d7e8f\nclone_label: HOST\nconfirmed: false\n"
                    "created: '2026-10-31T12:00:00Z'\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(IdentityRefused, match="identity.yaml is unreadable"):
        gate_for(connection, root).admit(connection, request(connection))
    assert path.read_bytes() == before and git_stub.calls == 0 and profiles(connection) == []


def test_identity_file_edit_is_picked_up_without_restart(root, connection, team_on, git_stub):
    gate = gate_for(connection, root)
    assert gate.admit(connection, request(connection)) is True
    identity = read_identity(root / ".taskmaster")
    write_identity(root / ".taskmaster", replace(identity, handle="johnd"))
    assert gate.admit(connection, request(connection, "two")) is True
    assert profiles(connection) == ["jdoe", "johnd"] and gate.current().handle == "johnd"


def test_replay_of_a_committed_request_is_never_refused(root, connection, team_on, git_stub):
    committed = request(connection)
    commands.execute(connection, committed)
    assert not identity_file(root).exists()
    git_stub.name = "Your Name"
    assert gate_for(connection, root).admit(connection, committed) is False
    assert git_stub.calls == 0


def test_gate_never_prompts(root, connection, team_on, monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    calls = _record_probes(monkeypatch, {"user.name": (0, b"John Doe\n"), "user.email": (0, b"jd@corp.io\n")})
    assert gate_for(connection, root).admit(connection, request(connection)) is True
    assert calls and all("stdin" not in kwargs and "input" not in kwargs for _, kwargs in calls)


# ── Review-gate fixes ───────────────────────────────────────────────────────

from taskmaster.team_identity import TeamIdentity


def write_teammate_profile(root, handle, email, text=None):
    """A teammate's profile as it arrives through git: on disk, never imported here yet."""
    path = root / ".taskmaster" / "users" / handle / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else
                    f"id: {handle}\nname: Teammate\nemails:\n- {email}\ncreated: '2026-10-30T12:00:00Z'\n",
                    encoding="utf-8")


def remove_row(connection, kind, ident):
    """Physically remove one entity (a rebuilt or restored store); its receipts stay."""
    key = connection.execute("SELECT entity_key FROM entity_core WHERE kind=? AND public_id=?",
                             (kind, ident)).fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    for (table,) in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall():
        if table != "entity_core" and "entity_key" in {row[1] for row in connection.execute(
                f'PRAGMA table_info("{table}")')}:
            connection.execute(f'DELETE FROM "{table}" WHERE entity_key=?', (key,))
    connection.execute("DELETE FROM document_search WHERE kind=? AND id=?", (kind, ident))
    connection.execute("DELETE FROM id_reservations WHERE kind=? AND public_id=?", (kind, ident))
    connection.execute("DELETE FROM entity_core WHERE entity_key=?", (key,))
    connection.commit()


# Item 1: a joining clone sees teammates' profiles on disk before choosing a handle.

def test_joining_clone_suffixes_against_a_teammate_profile_on_disk(root, connection, team_on, git_stub):
    write_teammate_profile(root, "jdoe", "other@x.io")
    assert schema_version(connection) == "3" and profiles(connection) == []
    assert gate_for(connection, root).admit(connection, request(connection)) is True
    assert read_identity(root / ".taskmaster").handle == "jdoe2"
    assert profiles(connection) == ["jdoe2"] and schema_version(connection) == "5"


def test_joining_clone_reuses_its_own_profile_on_disk_without_creating_a_row(root, connection, team_on, git_stub):
    write_teammate_profile(root, "jdoe", "JD@corp.io")
    high = max_seq(connection)
    gate = gate_for(connection, root)
    assert gate.admit(connection, request(connection)) is False
    assert read_identity(root / ".taskmaster").handle == "jdoe"
    assert profiles(connection) == [] and max_seq(connection) == high
    assert schema_version(connection) == "5"   # ready for the sync that imports the file
    assert gate.admit(connection, request(connection, "two")) is False
    assert profiles(connection) == []


def test_unparseable_profile_on_disk_is_ignored_for_the_handle_choice(root, connection, team_on, git_stub, caplog):
    write_teammate_profile(root, "jdoe", "", text="- not\n- a mapping\n")
    with caplog.at_level("WARNING"):
        assert gate_for(connection, root).admit(connection, request(connection)) is True
    assert read_identity(root / ".taskmaster").handle == "jdoe"
    assert "users/jdoe/profile.yaml" in caplog.text


# Item 3: profile arguments are validated before identity.yaml or the upgrade.

@pytest.mark.parametrize("name,email", [
    ("John " + "D" * 250, "jd@corp.io"),     # display name over the profile limit
    ("Володимир", "admin@corp.io"),          # the email fallback gives a placeholder word
    ("T Est", "t@corp.io"),                  # first initial + last word spells one
])
def test_invalid_profile_arguments_refuse_before_anything_is_written(root, connection, team_on, git_stub,
                                                                     name, email):
    git_stub.name, git_stub.email = name, email
    high = max_seq(connection)
    with pytest.raises(IdentityRefused, match="taskmaster:init-team"):
        gate_for(connection, root).admit(connection, request(connection))
    assert not identity_file(root).exists()
    assert schema_version(connection) == "3" and profiles(connection) == [] and max_seq(connection) == high


@pytest.mark.parametrize("handle", ["claude", "test", "admin"])
def test_unusable_handle_in_the_identity_file_refuses_and_keeps_schema_3(root, connection, team_on, git_stub,
                                                                         handle):
    write_identity(root / ".taskmaster", TeamIdentity(handle=handle, clone_id="3f9a2c4e5b6d7e8f",
                                                      clone_label="HOST", confirmed=False,
                                                      created="2026-10-31T12:00:00Z"))
    before = identity_file(root).read_bytes()
    with pytest.raises(IdentityRefused, match="taskmaster:init-team"):
        gate_for(connection, root).admit(connection, request(connection))
    assert identity_file(root).read_bytes() == before
    assert schema_version(connection) == "3" and profiles(connection) == []


# Item 4: a committed request's replay is admitted before the identity file is read (D15).

def test_replay_is_never_refused_even_with_an_invalid_identity_file(root, connection, team_on, git_stub):
    committed = request(connection)
    commands.execute(connection, committed)
    path = identity_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("handle: JDoe\n", encoding="utf-8")
    assert gate_for(connection, root).admit(connection, committed) is False
    with pytest.raises(IdentityRefused, match="identity.yaml is unreadable"):
        gate_for(connection, root).admit(connection, request(connection, "new"))


# Item 5: the clone's own profile row is recreated when it is gone.

def test_missing_own_profile_row_is_recreated(root, connection, team_on, git_stub):
    gate = gate_for(connection, root)
    assert gate.admit(connection, request(connection)) is True
    remove_row(connection, "profile", "jdoe")
    assert profiles(connection) == []
    assert gate.admit(connection, request(connection, "two")) is True        # not the cached handle
    assert profiles(connection) == ["jdoe"]
    remove_row(connection, "profile", "jdoe")
    assert gate_for(connection, root).admit(connection, request(connection, "three")) is True   # not the receipt
    assert profiles(connection) == ["jdoe"]
    ids = [row[0] for row in connection.execute(
        "SELECT request_id FROM command_receipts WHERE caller_scope=? ORDER BY commit_seq", (CALLER_SCOPE,))]
    assert len(ids) == len(set(ids)) == 3
    assert gate.admit(connection, request(connection, "four")) is False
