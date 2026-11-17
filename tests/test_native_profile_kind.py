# User intent: the shared per-person profile is a real native kind: created only by the profile.create
# command, stored in the database, exported to users/<handle>/profile.yaml by the drain, re-imported from
# hand edits by sync, never a link target, and never placed in a store an older build could still open.
from __future__ import annotations

import re

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import projection_parse, store
from taskmaster.admission import UnsupportedStoreError
from taskmaster.coordinator import sync_files
from taskmaster.native import batch_commands, commands, contracts, migrate, relations, schema, upgrade
from taskmaster.native.queries import Repository
from native_twins import make_twins, native_connection

DOC = {"id": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"], "created": "2026-10-31T12:00:00Z"}
PROFILE_BYTES = b"id: jdoe\nname: John Doe\nemails:\n- jd@corp.io\ncreated: '2026-10-31T12:00:00Z'\n"


def _seed():
    bs.backlog_add_task(title="First", epic="test-epic", phase="dev")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed)
    with twins.at(twins.native):
        first = bs.backlog_sync()
    assert first.startswith("Sync complete"), first
    return twins


@pytest.fixture
def upgraded(twins):
    """Twins are born at schema 3; profile rows need 4."""
    with native_connection(twins.native) as connection:
        upgrade.upgrade(connection)
    return twins


def _store_id(connection):
    return connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]


def _envelope(store_id, request_id="p1", **overrides):
    arguments = {"handle": "jdoe", "name": "John Doe", "emails": ["jd@corp.io"], "created": "2026-10-31T12:00:00Z"}
    arguments.update(overrides)
    return {"protocol": 2, "store_id": store_id, "caller_scope": "profile-tests", "request_id": request_id,
            "operation": "profile.create", "arguments": arguments, "expected_revisions": []}


def _counted(answer):
    from tests.test_native_sync_tool import _counted as counted
    return counted(answer)


def test_profile_kind_is_registered_without_ddl():
    assert schema.KINDS["profile"] == {}
    for field in ("name", "emails", "created"):
        assert schema.owner("profile", field) == "entity_extensions"
    assert schema.KIND_SINCE["profile"] == 4 and schema.VERSION == 5
    assert "profile" in commands.NAMED_KINDS


def test_projection_path_for_a_profile():
    assert commands.projection_path("profile", "jdoe", False) == "users/jdoe/profile.yaml"
    assert commands.projection_path("profile", "jdoe", True) == "users/jdoe/profile.yaml"


def test_classify_profile_paths():
    assert projection_parse.classify("users/jdoe/profile.yaml") == ("profile", "jdoe")
    for rel in ("users/JDoe/profile.yaml", "users/profile.yaml", "users/a/b/profile.yaml", "users/jdoe/plan.yaml",
                "users/-x/profile.yaml"):
        with pytest.raises(ValueError):
            projection_parse.classify(rel)


def test_profile_bytes_parse_as_a_yaml_mapping():
    assert projection_parse.projected_file("profile", "jdoe", PROFILE_BYTES) == {("profile", "jdoe"): (DOC, None)}
    for content in (PROFILE_BYTES.replace(b"id: jdoe", b"id: other"), b"- a\n- b\n",
                    b"<<<<<<< HEAD\n" + PROFILE_BYTES + b">>>>>>> theirs\n"):
        with pytest.raises(ValueError):
            projection_parse.projected_file("profile", "jdoe", content)


def test_render_profile_round_trips_through_the_parser():
    content, oracle = store.render_entity_file("profile", DOC, None)
    assert oracle is None
    assert yaml.safe_load(content) == DOC
    assert projection_parse.projected_file("profile", "jdoe", content) == {("profile", "jdoe"): (DOC, None)}


def test_legacy_store_never_scans_profile_files():
    assert "profile" not in {kind for kind, _ in store.Store._ENTITY_FILE_SPECS}


def test_sync_discovery_keys_a_profile_by_its_handle(tmp_path):
    backlog_dir = tmp_path / ".taskmaster"
    for handle in ("jdoe", "JD"):
        (backlog_dir / "users" / handle).mkdir(parents=True)
        (backlog_dir / "users" / handle / "profile.yaml").write_bytes(PROFILE_BYTES)
    files = sync_files.discover(backlog_dir).files
    assert files["users/jdoe/profile.yaml"] == ("profile", "jdoe")
    assert not [rel for rel in files if "JD/" in rel]


@pytest.mark.parametrize("overrides", [
    {"handle": "claude"}, {"handle": "JDoe"}, {"name": "Your Name"}, {"emails": "jd@corp.io"},
    {"extra": 1}, {"created": "yesterday"}, {"handle": "admin"}, {"handle": "test"}, {"name": "N" * 201},
])
def test_profile_create_validation(overrides):
    with pytest.raises(ValueError):
        contracts.validate(_envelope("store", **overrides))


def test_profile_create_is_refused_through_batch_commands():
    assert "profile.create" in batch_commands.INTERNAL_OPERATIONS


def test_link_targets_never_resolve_to_a_profile(upgraded):
    with native_connection(upgraded.native) as connection:
        commands.execute(connection, _envelope(_store_id(connection)))
        assert relations._kind_for_id(connection, "jdoe") == "task"
        assert migrate._reference(connection, "links", {"target": "jdoe"})[0] == "task"


def test_profile_files_are_guarded_from_direct_writes(tmp_path):
    from tests.conftest import _guard_path
    backlog_dir = tmp_path / ".taskmaster"
    (backlog_dir / "users" / "jdoe").mkdir(parents=True)
    assert _guard_path(backlog_dir / "users/jdoe/profile.yaml") == backlog_dir


def _profile(root, handle):
    with native_connection(root) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get("profile", handle)


def test_profile_round_trips_command_db_file_import(upgraded):
    from tests.native_coordinator_helpers import compatibility_client
    root = upgraded.native
    with upgraded.at(root):
        client = compatibility_client(root)
        envelope = _envelope(client.identity["store_id"])
        # 1. Command.
        first = client.execute(envelope)
        receipt = first["receipt"]
        assert [(item["kind"], item["id"]) for item in receipt["affected"]] == [("profile", "jdoe")]
        # 2. Database.
        assert _profile(root, "jdoe")["fields"] == DOC
        with native_connection(root) as connection:
            events = connection.execute("SELECT kind,op,tool FROM domain_events WHERE kind='profile'").fetchall()
        assert events == [("profile", "create", "profile.create")]
        # 3. Exported file.
        client.flush(receipt["commit_seq"])
        path = root / ".taskmaster" / "users" / "jdoe" / "profile.yaml"
        assert yaml.safe_load(path.read_bytes()) == DOC
        # 4. Import with nothing edited.
        answer = bs.backlog_sync()
        assert answer.startswith("Sync complete"), answer
        assert _counted(answer)["imported"] == 0
        # 5. Import of a hand edit.
        path.write_bytes(path.read_bytes().replace(b"name: John Doe", b"name: John Q. Doe"))
        answer = bs.backlog_sync()
        assert _counted(answer)["imported"] == 1, answer
        assert _profile(root, "jdoe")["fields"]["name"] == "John Q. Doe"
        assert _counted(bs.backlog_sync())["imported"] == 0
        # 6. Import of a new file.
        new = root / ".taskmaster" / "users" / "asmith" / "profile.yaml"
        new.parent.mkdir(parents=True)
        new.write_bytes(b"id: asmith\nname: Ann Smith\nemails:\n- as@corp.io\ncreated: '2026-10-31T12:00:00Z'\n")
        answer = bs.backlog_sync()
        assert answer.startswith("Sync complete"), answer
        assert _profile(root, "asmith")["fields"]["name"] == "Ann Smith"
        # 7. Replay.
        assert client.execute(envelope)["receipt"] == receipt
        with native_connection(root) as connection:
            assert connection.execute("SELECT COUNT(*) FROM entity_core WHERE kind='profile' AND public_id='jdoe'"
                                      ).fetchone()[0] == 1


def test_profile_rows_need_schema_4(twins):
    import base64
    import hashlib
    from taskmaster.native import sync
    from native_twins import commit_only
    root = twins.native
    raw = b"id: x\nname: Ex Ample\nemails: []\ncreated: '2026-10-31T12:00:00Z'\n"
    with native_connection(root) as connection:
        high = connection.execute("SELECT value FROM native_manifest WHERE key='event_high_water'").fetchone()[0]
        with pytest.raises(UnsupportedStoreError, match="need native schema 4"):
            commands.execute(connection, _envelope(_store_id(connection)))
        # A file import is refused by the same row-level check.
        rel = "users/x/profile.yaml"
        row = {"kind": "profile", "id": "x", "revision": 0, "body": None,
               "fields": {"id": "x", "name": "Ex Ample", "emails": [], "created": "2026-10-31T12:00:00Z"}}
        with pytest.raises(UnsupportedStoreError, match="need native schema 4"):
            commit_only(connection, "sync.apply", {
                "file": rel, "mode": "apply", "rows": [row], "reason": "test observation",
                "observed_base64": base64.b64encode(raw).decode("ascii"),
                "observed_hash": hashlib.sha1(raw).hexdigest(), "expected_manifest": sync.manifest_token(connection, rel)})
        assert connection.execute("SELECT value FROM native_manifest WHERE key='event_high_water'"
                                  ).fetchone()[0] == high
        assert connection.execute("SELECT COUNT(*) FROM entity_core WHERE kind='profile'").fetchone()[0] == 0
        assert connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0] == "3"


def test_solo_sync_skips_profile_files_and_completes(twins):
    """Review-gate item 2: below schema 4 a teammate's profile file is a definite skip, never pending."""
    root = twins.native
    path = root / ".taskmaster" / "users" / "x" / "profile.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"id: x\nname: Ex Ample\nemails: []\ncreated: '2026-10-31T12:00:00Z'\n")
    with twins.at(root):
        for _ in range(2):
            answer = bs.backlog_sync()
            assert answer.startswith("Sync complete"), answer
            assert "users/x/profile.yaml: profiles are imported only once team mode is on; skipped" in answer
    with native_connection(root) as connection:
        assert connection.execute("SELECT COUNT(*) FROM entity_core WHERE kind='profile'").fetchone()[0] == 0
        assert connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()[0] == "3"
        assert connection.execute("SELECT COUNT(*) FROM projection WHERE file='users/x/profile.yaml'").fetchone()[0] == 0


def test_profile_rows_appear_in_the_sql_compatibility_view(upgraded):
    with native_connection(upgraded.native) as connection:
        commands.execute(connection, _envelope(_store_id(connection)))
        with Repository(connection).snapshot() as snapshot:
            rows = snapshot.sql("SELECT kind,id FROM entities WHERE kind='profile'")["rows"]
    assert [list(row) for row in rows] == [["profile", "jdoe"]]
