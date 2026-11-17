"""N13 candidate preparation uses real observed bases, not rendered guesses."""
import hashlib

import pytest
import yaml

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


def setup_base(connection, root, rel=REL):
    content = (root / ".taskmaster" / rel).read_bytes()
    connection.execute("INSERT INTO projection_base(file,content) VALUES(?,?) ON CONFLICT(file) DO UPDATE SET content=excluded.content",
                       (rel, content))
    connection.execute("UPDATE projection SET content_hash=? WHERE file=?", (hashlib.sha1(content).hexdigest(), rel))
    connection.commit()
    return content


def edited_file(root, original, old="Original", new="External"):
    content = original.decode("utf-8").replace(old, new)
    (root / ".taskmaster" / REL).write_bytes(content.encode("utf-8"))


def candidate(connection, root, rel=REL, **kwargs):
    with Repository(connection).snapshot() as snapshot:
        return prepare(snapshot, root / ".taskmaster", rel, **kwargs)


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


# -- project.yaml hand edits: the team block (team-sync-001) ------------------

PROJECT_REL = "project.yaml"
TEAM_MANIFEST = {"schema_version": 1, "meta": {"name": "Demo", "slug": "demo", "kind": "app"},
                 "team": {"protocol": 1, "sync": {"mode": "off"}}}


@pytest.fixture
def project_twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_project_set(yaml.safe_dump(TEAM_MANIFEST)),
                      engine_oracle=True)


def hand_edit_project(root, **team):
    path = root / ".taskmaster" / PROJECT_REL
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["team"] = {**doc["team"], **team}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_hand_edited_team_protocol_is_quarantined(project_twins):
    root = project_twins.native
    with native_connection(root) as connection:
        setup_base(connection, root, PROJECT_REL)
        hand_edit_project(root, protocol=2)
        plan = candidate(connection, root, PROJECT_REL)
        assert plan.state == "quarantine" and "init-team upgrade" in plan.reason
        with pytest.raises(ValueError, match=r"^project\.yaml cannot be taken:"):
            candidate(connection, root, PROJECT_REL, take_file=True)
        commit_only(connection, "sync.apply", plan.arguments)
        with Repository(connection).snapshot() as snapshot:
            assert snapshot.get("project", "__project__")["fields"]["team"]["protocol"] == 1


def test_hand_edited_invalid_team_value_is_quarantined(project_twins):
    root = project_twins.native
    with native_connection(root) as connection:
        setup_base(connection, root, PROJECT_REL)
        hand_edit_project(root, sync={"mode": "sometimes"})
        plan = candidate(connection, root, PROJECT_REL)
        assert plan.state == "quarantine" and "team.sync.mode" in plan.reason


def test_hand_edited_team_mode_imports(project_twins):
    root = project_twins.native
    with native_connection(root) as connection:
        setup_base(connection, root, PROJECT_REL)
        hand_edit_project(root, sync={"mode": "manual"}, future=1)
        plan = candidate(connection, root, PROJECT_REL)
        assert plan.state == "apply", plan.reason
        team = plan.arguments["rows"][0]["fields"]["team"]
        assert team["sync"]["mode"] == "manual" and team["future"] == 1


def test_unquoted_off_hand_edit_imports(project_twins):
    from taskmaster.project import team_config_from_document

    root = project_twins.native
    path = root / ".taskmaster" / PROJECT_REL
    with native_connection(root) as connection:
        setup_base(connection, root, PROJECT_REL)
        text = path.read_text(encoding="utf-8")
        assert "mode: off" in text or "mode: 'off'" in text
        path.write_text(text.replace("mode: 'off'", "mode: off"), encoding="utf-8")
        assert "mode: 'off'" not in path.read_text(encoding="utf-8")
        plan = candidate(connection, root, PROJECT_REL)
        assert plan.state == "apply", plan.reason
        fields = plan.arguments["rows"][0]["fields"]
        assert fields["team"]["sync"]["mode"] is False
        assert team_config_from_document(fields).sync.mode == "off"


def test_stored_row_id_does_not_disturb_the_import_guard(project_twins):
    root = project_twins.native
    with native_connection(root) as connection:
        setup_base(connection, root, PROJECT_REL)
        with Repository(connection).snapshot() as snapshot:
            assert snapshot.get("project", "__project__", include_body=True)["fields"]["id"] == "__project__"
        hand_edit_project(root, sync={"mode": "auto"})
        assert candidate(connection, root, PROJECT_REL).state == "apply"
        hand_edit_project(root, protocol=2)
        assert candidate(connection, root, PROJECT_REL).state == "quarantine"
