# User intent: through the real coordinator and the MCP tools, the first team-mode write gives this clone an
# identity and a profile, a clone with a placeholder git name is refused with guidance (never told to retry),
# machine-local sync keeps working, and solo projects see no change at all.
from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import identity_gate
from taskmaster.coordinator.service import Coordinator
from taskmaster.native.queries import Repository
from taskmaster.team_identity import IDENTITY_REL, GitIdentity, IdentityRefused, read_identity
from native_twins import make_twins, native_connection, native_database


class GitStub:
    def __init__(self, name="John Doe", email="jd@corp.io"):
        self.name, self.email, self.calls = name, email, 0

    def __call__(self, root):
        self.calls += 1
        return GitIdentity(self.name, self.email)


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_add_task(title="Seed task", epic="test-epic", phase="dev"))


@pytest.fixture
def git_stub(monkeypatch):
    stub = GitStub()
    monkeypatch.setattr(identity_gate, "read_git_identity", stub)
    return stub


@pytest.fixture
def team_on(monkeypatch):
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root, *, project_doc=None: True)


def _client(root):
    from tests.native_coordinator_helpers import compatibility_client
    return compatibility_client(root)


def _value(root, sql, args=()):
    with closing(sqlite3.connect(native_database(root))) as connection:
        return connection.execute(sql, args).fetchall()


def _schema(root):
    return _value(root, "SELECT value FROM native_manifest WHERE key='schema_version'")[0][0]


def _profiles(root):
    return [row[0] for row in _value(root, "SELECT public_id FROM entity_core WHERE kind='profile' ORDER BY public_id")]


def _titles(root):
    with native_connection(root) as connection, Repository(connection).snapshot() as snapshot:
        return [item["fields"]["title"] for item in snapshot.list("task", fields=["title"], limit=500)["items"]]


def _identity_file(root):
    return root / ".taskmaster" / IDENTITY_REL


TEAM_MANIFEST = "schema_version: 1\nmeta:\n  name: Team\n  slug: team\nteam:\n  sync:\n    mode: manual\n"


def test_first_team_mode_tool_write_creates_identity_profile_and_task(twins, git_stub):
    root = twins.native
    with twins.at(root):
        assert not bs.backlog_project_set(yaml_content=TEAM_MANIFEST).startswith("Error"), "project.set refused"
        assert not _identity_file(root).exists()   # team mode was off when that write was admitted
        answer = bs.backlog_add_task(title="Team task", epic="test-epic", phase="dev")
        assert not answer.startswith("Error"), answer
        identity = read_identity(root / ".taskmaster")
        assert identity.handle == "jdoe" and identity.confirmed is False
        assert "confirmed: false" in _identity_file(root).read_text(encoding="utf-8")
        assert _schema(root) == "5"
        assert _profiles(root) == ["jdoe"]
        seq = _value(root, "SELECT MAX(seq) FROM domain_events")[0][0]
        _client(root).flush(seq)
        assert (root / ".taskmaster" / "users" / "jdoe" / "profile.yaml").is_file()
        assert "Team task" in _titles(root)
        changes = bs.backlog_changes_since()
        assert not changes.startswith("Error"), changes


def test_degenerate_name_refusal_reaches_the_tool_caller(twins, git_stub, team_on):
    root = twins.native
    git_stub.name = "Your Name"
    receipts = _value(root, "SELECT COUNT(*) FROM command_receipts")[0][0]
    with twins.at(root):
        answer = bs.backlog_add_task(title="Refused task", epic="test-epic", phase="dev")
    assert answer.startswith("Error: Refused:"), answer
    assert "taskmaster:init-team" in answer and "same request_id" not in answer
    assert "Refused task" not in _titles(root)
    assert _value(root, "SELECT COUNT(*) FROM command_receipts")[0][0] == receipts
    assert not _identity_file(root).exists()


def test_refusal_crosses_ipc_as_identity_refused(twins, git_stub, team_on):
    root = twins.native
    git_stub.name = ""
    with twins.at(root):
        client = _client(root)
        envelope = {"protocol": 2, "store_id": client.identity["store_id"], "caller_scope": "identity-tests",
                    "request_id": "refused-1", "operation": "task.patch",
                    "arguments": {"id": "test-epic-001", "set": {"title": "Nope"}}, "expected_revisions": []}
        with pytest.raises(IdentityRefused, match="taskmaster:init-team") as refused:
            client.execute(envelope)
    assert refused.value.may_have_committed is False


def test_backlog_sync_still_runs_with_a_degenerate_name(twins, git_stub, team_on):
    git_stub.name = "Your Name"
    with twins.at(twins.native):
        answer = bs.backlog_sync()
    assert answer.startswith("Sync complete"), answer
    assert git_stub.calls == 0


def test_solo_mode_is_unchanged(twins, git_stub):
    root = twins.native
    git_stub.name = "Your Name"
    with twins.at(root):
        answer = bs.backlog_add_task(title="Solo task", epic="test-epic", phase="dev")
        assert not answer.startswith("Error"), answer
    assert "Solo task" in _titles(root)
    assert not _identity_file(root).exists() and _profiles(root) == [] and git_stub.calls == 0
    from tests.native_coordinator_helpers import close_owned
    close_owned()
    assert _schema(root) == "3"
    with Coordinator(root):
        pass
    assert _schema(root) == "3"


# Review-gate item 1: the real first-bootstrap order of a joining clone. The store is at
# schema 3 and a teammate's profile arrived on disk through git, never imported.

def _teammate(root, handle, email):
    path = root / ".taskmaster" / "users" / handle / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"id: {handle}\nname: Teammate\nemails:\n- {email}\ncreated: '2026-10-30T12:00:00Z'\n",
                    encoding="utf-8")


def test_joining_clone_with_another_email_takes_a_suffix_and_syncs_clean(twins, git_stub, team_on):
    root = twins.native
    _teammate(root, "jdoe", "other@x.io")
    assert _schema(root) == "3"
    with twins.at(root):
        answer = bs.backlog_add_task(title="Joiner task", epic="test-epic", phase="dev")
        assert not answer.startswith("Error"), answer
        assert read_identity(root / ".taskmaster").handle == "jdoe2"
        synced = bs.backlog_sync()
        assert synced.startswith("Sync complete"), synced
    assert _profiles(root) == ["jdoe", "jdoe2"]


def test_joining_clone_with_the_same_email_reuses_the_handle_without_a_duplicate(twins, git_stub, team_on):
    root = twins.native
    _teammate(root, "jdoe", "jd@corp.io")
    with twins.at(root):
        answer = bs.backlog_add_task(title="Second machine task", epic="test-epic", phase="dev")
        assert not answer.startswith("Error"), answer
        assert read_identity(root / ".taskmaster").handle == "jdoe"
        assert _profiles(root) == []          # the sync imports the teammate's file; no competing row
        synced = bs.backlog_sync()
        assert synced.startswith("Sync complete"), synced
        assert _profiles(root) == ["jdoe"]
        assert bs.backlog_sync().startswith("Sync complete")
        again = bs.backlog_add_task(title="After sync", epic="test-epic", phase="dev")
        assert not again.startswith("Error"), again
    assert _profiles(root) == ["jdoe"]
