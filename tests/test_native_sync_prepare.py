"""N13 candidate preparation uses real observed bases, not rendered guesses."""
import hashlib

import pytest

from taskmaster import backlog_server as bs, store
from taskmaster.coordinator.sync_prepare import prepare
from taskmaster.native.queries import Repository
from native_twins import commit_only, make_twins, native_connection


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_add_task(title="Original", epic="test-epic", phase="dev"),
                      engine_oracle=True)


REL = "tasks/test-epic-001.md"


def setup_base(connection, root):
    content = (root / ".taskmaster" / REL).read_bytes()
    connection.execute("INSERT INTO projection_base(file,content) VALUES(?,?) ON CONFLICT(file) DO UPDATE SET content=excluded.content",
                       (REL, content))
    connection.execute("UPDATE projection SET content_hash=? WHERE file=?", (hashlib.sha1(content).hexdigest(), REL))
    connection.commit()
    return content


def edited_file(root, original, old="Original", new="External"):
    content = original.decode("utf-8").replace(old, new)
    (root / ".taskmaster" / REL).write_bytes(content.encode("utf-8"))


def candidate(connection, root, **kwargs):
    with Repository(connection).snapshot() as snapshot:
        return prepare(snapshot, root / ".taskmaster", REL, **kwargs)


def test_verified_base_merges_disjoint_database_and_file_edits(twins, monkeypatch):
    with native_connection(twins.native) as connection:
        base = setup_base(connection, twins.native)
        commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"notes": "Database notes"}})
        edited_file(twins.native, base)
        monkeypatch.setattr(store, "render_entity_file", lambda *a, **k: pytest.fail("merge base must not be rendered"))
        plan = candidate(connection, twins.native)
        assert plan.state == "apply"
        assert plan.arguments["rows"][0]["fields"]["title"] == "External"
        assert plan.arguments["rows"][0]["fields"]["notes"] == "Database notes"


def test_overlap_keeps_database_value_and_observed_external_bytes(twins):
    with native_connection(twins.native) as connection:
        base = setup_base(connection, twins.native)
        commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": "Database"}})
        edited_file(twins.native, base)
        plan = candidate(connection, twins.native)
        assert plan.state == "conflict" and ".title" in plan.reason
        assert plan.arguments["rows"][0]["fields"]["title"] == "Database"
        assert b"External" in plan.observation.content


@pytest.mark.parametrize("base", [None, b"forged base"])
def test_missing_or_forged_base_cannot_silently_import_divergent_file(twins, base):
    with native_connection(twins.native) as connection:
        original = setup_base(connection, twins.native)
        connection.execute("DELETE FROM projection_base WHERE file=?", (REL,))
        if base is not None:
            connection.execute("INSERT INTO projection_base VALUES(?,?)", (REL, base))
        connection.commit()
        edited_file(twins.native, original)
        plan = candidate(connection, twins.native)
        assert plan.state == "conflict" and "no verified prior base" in plan.reason
        assert not plan.arguments["rows"]
        assert candidate(connection, twins.native, take_file=True).state == "apply"


def test_bad_file_quarantined_and_repaired_bytes_are_eligible(twins):
    with native_connection(twins.native) as connection:
        original = setup_base(connection, twins.native)
        path = twins.native / ".taskmaster" / REL
        path.write_bytes(b"---\nid: wrong-id\n---\n")
        plan = candidate(connection, twins.native)
        assert plan.state == "quarantine"
        commit_only(connection, "sync.apply", plan.arguments)
        assert candidate(connection, twins.native).arguments is None
        path.write_bytes(original)
        assert candidate(connection, twins.native).state == "apply"


def test_missing_file_is_repair_not_a_delete(twins):
    with native_connection(twins.native) as connection:
        (twins.native / ".taskmaster" / REL).unlink()
        plan = candidate(connection, twins.native)
        assert plan.state == "repair" and plan.observation is None
        with pytest.raises(ValueError, match="missing file"):
            candidate(connection, twins.native, take_file=True)
