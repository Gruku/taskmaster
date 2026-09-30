"""User intent: the last normal-path tools served by the native core (N08) — the
dashboard, search, read-only SQL, store status, blast radius, the project manifest
tools and the local Linear link/list/show/status/unlink actions — must answer,
refuse, commit and project exactly as the legacy tools do, or differ only where a
native store necessarily reports different facts about itself.
"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest
import yaml

from taskmaster import backlog_server as bs
from native_twins import make_twins, normalize

MANIFEST = {
    "schema_version": 1,
    "meta": {"name": "Demo", "slug": "demo", "kind": "app"},
    "repos": [{"name": "api", "path": "api", "depends_on": ["web"]}, {"name": "web", "path": "web"}],
    "integrations": {"observability": {"error_trace_ladder": [{"layer": "browser", "kind": "console"}]}},
    "conventions": {"policies": {"merge_targets": [{"label": "develop", "branches": ["dev"]}]}},
}


def _seed():
    bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
    bs.backlog_add_task(title="Searchable loader crash", epic="test-epic", phase="dev", priority="high",
                        notes="The loader crashes when the cache is cold")
    bs.backlog_add_task(title="Second task", epic="test-epic", phase="dev", depends_on="test-epic-001")
    bs.backlog_update_task(task_id="test-epic-001", field="anchors", value="src/loader.py")
    bs.backlog_bug_create(title="Loader crash in cache", components=["loader"])
    bs.backlog_issue_create(title="Cold cache systemic crash", severity="P1", evidence="three reports")


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def _check(twins):
    twins.assert_state_matches()
    twins.assert_files_match()


def test_status_and_blast_radius_match(twins):
    twins.same("backlog_pick_task", task_id="test-epic-001")
    for kwargs in ({}, {"verbose": True}):
        twins.same("backlog_status", **kwargs)
    for kwargs in ({"task_id": "test-epic-001"}, {"task_id": "test-epic-002", "structured": True},
                   {"task_id": "ghost-1"}, {"task_id": "test-epic-001", "mode": "sideways"}):
        twins.same("backlog_blast_radius", **kwargs)


def test_search_matches(twins):
    for kwargs in ({"query": "loader"}, {"query": "cache crash", "kinds": ["bug", "issue"]},
                   {"query": "loader", "kinds": "task"}, {"query": "nothing-matches-this"},
                   {"query": "loader", "kinds": ["bogus"]}, {"query": '"quoted" -dash'}):
        twins.same("backlog_search", **kwargs)


def test_read_only_sql_matches(twins):
    for sql in ("SELECT kind,id,status FROM entities WHERE deleted=0 ORDER BY kind,id",
                "SELECT id,json_extract(doc,'$.title') AS title FROM entities WHERE kind='task' ORDER BY id",
                "WITH t AS (SELECT id FROM entities WHERE kind='bug') SELECT COUNT(*) AS n FROM t",
                "SELECT kind,id FROM entity_fts WHERE entity_fts MATCH 'loader' ORDER BY kind,id",
                "SELECT path,kind,id FROM entity_paths ORDER BY path,kind,id",
                "DELETE FROM entities", "SELECT * FROM meta", "SELECT 1; SELECT 2"):
        twins.same("backlog_query", sql=sql, limit=2)
        twins.same("backlog_query", sql=sql)


def test_project_manifest_tools_match(twins):
    for tool in ("backlog_project_get", "backlog_project_ship_order", "backlog_project_error_trace_ladder"):
        twins.same(tool)
    twins.same("backlog_project_get_field", path="meta.name")
    twins.same("backlog_project_init", name="Demo Project")
    for tool in ("backlog_project_get", "backlog_project_ship_order", "backlog_project_error_trace_ladder"):
        twins.same(tool)
    for exc_args in (("Other",), ("",)):
        results = []
        for root in (twins.legacy, twins.native):
            with twins.at(root), pytest.raises(ValueError) as caught:
                bs.backlog_project_init(name=exc_args[0])
            results.append(str(caught.value).replace(str(root), "<root>"))
        assert results[0] == results[1]
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(MANIFEST))
    for tool in ("backlog_project_get", "backlog_project_ship_order", "backlog_project_error_trace_ladder"):
        twins.same(tool)
    for path in ("meta.name", "repos[0].depends_on[0]", "repos[5].name", "", "project.goal"):
        twins.same("backlog_project_get_field", path=path)
    for bad in ("::not yaml", "- a list", yaml.safe_dump({"schema_version": 9})):
        results = []
        for root in (twins.legacy, twins.native):
            with twins.at(root), pytest.raises(ValueError) as caught:
                bs.backlog_project_set(yaml_content=bad)
            results.append(type(caught.value).__name__ + str(caught.value))
        assert results[0] == results[1]
    _check(twins)


def test_merge_ladder_reads_the_native_manifest(twins):
    twins.same("backlog_project_set", yaml_content=yaml.safe_dump(MANIFEST))
    twins.same("backlog_record_merge", task_id="test-epic-001", rung="develop", sha="abcdef123")
    _check(twins)


def test_linear_local_actions_match(twins):
    config = {"version": 1, "default_workspace": "cm",
              "workspaces": [{"alias": "cm", "team_id": "T1", "token_env": "LINEAR_TOKEN_TEST"}]}
    for root in (twins.legacy, twins.native):
        (root / ".taskmaster" / "linear.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for kwargs in ({"action": "link", "task_id": "test-epic-001", "external_key": "ENG-42"},
                   {"action": "link", "task_id": "test-epic-001", "external_key": "ENG-43"},
                   {"action": "link", "task_id": "test-epic-002", "external_key": "ENG-42"},
                   {"action": "link", "task_id": "ghost-1", "external_key": "ENG-1"},
                   {"action": "link", "task_id": "test-epic-002", "external_key": "ENG-7", "workspace_alias": "nope"},
                   {"action": "list"}, {"action": "show", "tracker_id": "linear-cm-eng-42"},
                   {"action": "show", "tracker_id": "linear-cm-eng-404"}, {"action": "status"},
                   {"action": "unlink", "task_id": "test-epic-001"}, {"action": "unlink", "task_id": "test-epic-001"},
                   {"action": "unlink", "task_id": "ghost-1"}, {"action": "explode"}):
        legacy, native = twins.call("backlog_linear", **kwargs)
        assert normalize(json.loads(native)) == normalize(json.loads(legacy)), kwargs
    _check(twins)


def test_store_status_reports_every_section_for_a_native_store(twins):
    """Recorded N08 difference: sizes, schema version and change history are facts
    about each database, so the native report is compared by its lines' labels.
    N11 difference: only a native store has a projection exporter, so only its
    report carries the `Exporter lease` line (§5.4)."""
    legacy, native = twins.call("backlog_store_status")
    labels = lambda text: [line.split(":", 1)[0] for line in text.splitlines() if not line.startswith("  ")]
    assert [label for label in labels(native) if label != "Exporter lease"] == labels(legacy)
    assert labels(native).count("Exporter lease") == 1 and "Exporter lease" not in labels(legacy)
    assert "schema v2" in native and "Warning: none" in native


def test_linear_probe_matches(twins, monkeypatch):
    from taskmaster.integrations.linear import client as linear_client

    class FakeClient:
        def __init__(self, token):
            self.token = token

        def list_teams(self):
            return [{"id": "T1", "name": "Eng", "key": "ENG"}]

        def list_issue_statuses(self, team):
            return [{"id": "S1", "name": "Todo"}]

        def list_users(self, team):
            raise linear_client.LinearAPIError("users hidden")

    monkeypatch.delenv("PROBE_TOKEN", raising=False)
    twins.same("backlog_linear", action="probe", token_env="PROBE_TOKEN")
    monkeypatch.setenv("PROBE_TOKEN", "secret")
    monkeypatch.setattr(linear_client, "LinearClient", FakeClient)
    legacy, native = twins.same("backlog_linear", action="probe", token_env="PROBE_TOKEN")
    assert json.loads(native)["teams"][0]["users_error"] == "users hidden"


def _seed_validation_findings():
    """Rows the tools cannot produce: artifacts without a tldr, a tracker whose
    frontmatter is invalid, and task/issue tracker ids that match no tracker."""
    from taskmaster import store
    _seed()
    bs.backlog_idea_create(title="Idea without tldr")
    bs.backlog_handover_create(tldr="Handover losing its tldr")
    store.reset_for_tests()
    with store.transaction(tool="test:seed", backlog_path=bs.ROOT / ".taskmaster") as tx:
        for kind, ident in (("issue", "ISS-001"), ("idea", "IDEA-001")):
            doc = tx.get(kind, ident)
            doc.pop("tldr", None)
            tx.put(kind, ident, doc)
        task = tx.get("task", "test-epic-002")
        task["tracker_id"] = "linear-ghost"
        tx.put("task", "test-epic-002", task)
        issue = tx.get("issue", "ISS-001")
        issue["tracker_id"] = "linear-missing"
        tx.put("issue", "ISS-001", issue)
        tx.create("tracker", {"id": "linear-bad", "external_system": "linear"}, requested_id="linear-bad")
    store.reset_for_tests()


def test_validate_matches(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed_validation_findings)
    # The legacy issue index refreshes on an issue write; the native one is always derived.
    twins.same("backlog_issue_update", issue_id="ISS-001", field="severity", value="P2")
    legacy, native = twins.same("backlog_validate")
    for finding in ("tracker_id `linear-ghost`", "issue `ISS-001`: tracker_id `linear-missing`", "tracker `",
                    "IDEA-001 missing tldr", "ISS-001 missing tldr"):
        assert finding in native, (finding, native)
    twins.same("backlog_add_task", title="Docs task", epic="test-epic", phase="dev",
               options={"docs": "plan:docs/missing.md;spec:has a space"})
    legacy, native = twins.same("backlog_validate")
    assert "docs.plan path not found" in native


def _flag(root, rel, kind, ident):
    """Hand-edit `rel` and flag it the way the legacy store does (B-089): both changed."""
    path = root / ".taskmaster" / rel
    hand_edit = path.read_bytes() + b"\nHand edit nobody has chosen against.\n"
    path.write_bytes(hand_edit)
    with closing(sqlite3.connect(root / ".taskmaster" / "local" / "store.db")) as connection:
        connection.execute(
            "INSERT INTO projection_conflict(file,kind,id,flagged_at,file_hash,file_content) "
            "VALUES(?,?,?,'2026-09-21T00:00:00Z','x',?)", (rel, kind, ident, hand_edit))
        connection.commit()
    return path, hand_edit


def _task_files(root, ident):
    return sorted(p.relative_to(root / ".taskmaster").as_posix()
                  for p in (root / ".taskmaster").rglob(f"{ident}.md"))


def test_a_file_flagged_before_activation_is_kept_and_reported(twins):
    """B-089 on the native path: a file the legacy store flagged (both it and the
    store changed) must not be overwritten by a native write, and store status must
    name it, until someone chooses a side."""
    rel = "tasks/test-epic-001.md"
    path, hand_edit = _flag(twins.native, rel, "task", "test-epic-001")
    with twins.at(twins.native):
        answer = bs.backlog_update_task(task_id="test-epic-001", field="notes", value="store side")
        report = bs.backlog_store_status()
    assert path.read_bytes() == hand_edit, answer
    assert f"export pending: {rel} is flagged" in answer
    flagged = report.split("Flagged", 1)[1].split("\n", 2)
    assert rel in "".join(flagged[:2]), report


def test_a_flagged_entity_is_not_moved_to_its_archive_path(twins):
    """The hold is per entity, as in the legacy `_export_blocked`: archiving must not
    write the archive copy beside the flagged file, leaving one id in two places."""
    rel = "tasks/test-epic-001.md"
    path, hand_edit = _flag(twins.native, rel, "task", "test-epic-001")
    with twins.at(twins.native):
        answer = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert path.read_bytes() == hand_edit, answer
    assert _task_files(twins.native, "test-epic-001") == [rel], answer
    assert f"export pending: {rel} is flagged" in answer


def test_a_flagged_entity_file_is_not_removed(twins):
    """An epic whose own file would go (its heavy fields cleared) keeps a flagged file."""
    with twins.at(twins.native):
        bs.backlog_update_epic(epic_id="test-epic", field="description", value="Store prose")
    (rel,) = _task_files(twins.native, "test-epic")
    path, hand_edit = _flag(twins.native, rel, "epic", "test-epic")
    with twins.at(twins.native):
        answer = bs.backlog_update_epic(epic_id="test-epic", field="description", value="")
    assert path.read_bytes() == hand_edit, answer
    assert _task_files(twins.native, "test-epic") == [rel], answer
    assert f"export pending: {rel} is flagged" in answer
