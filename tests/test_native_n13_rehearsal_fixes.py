# User intent: regressions for the N13 CodeMaestro copy-only rehearsal import defects
# (D2 conflict-marker false positive, D3 accepted-but-dropped document fields, D4
# trailing-newline base asymmetry) -- sync must never claim an edit it did not apply.
from contextlib import closing
import base64
import hashlib
import json
import sqlite3

import pytest
import yaml

from taskmaster import backlog_server as bs, projection_parse as parse, store
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.protocol import connect
from taskmaster.coordinator.service import Coordinator
from taskmaster.coordinator.sync_prepare import prepare
from taskmaster.native.queries import Repository
from native_twins import commit_only, make_twins, native_connection

# Anonymised shape of CodeMaestro B-339: a bug body quoting pytest's summary rule.
B339 = """---
id: B-339
title: 'host: `pytest tests/` is un-runnable'
status: open
severity: P2
components:
- host
- tests
discovered: '2026-07-29T17:54:51Z'
discovered_by: claude
fix_commit: null
links: []
---
## Symptom

`pytest tests/` exits 2 with **zero tests executed**.

```
from host.patches import PATCHES_57, apply_57_patches
E   ImportError: cannot import name 'PATCHES_57' from 'host.patches'
=========================== short test summary info ===========================
ERROR tests/test_patches.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.29s
```

## Notes

- A setext heading underline is also not a conflict marker
=======
- nor is a quoted `<<<<<<< HEAD` mid-line.
"""


# --- D2: conflict markers ---------------------------------------------------

def test_d2_body_quoting_pytest_rule_parses():
    doc, body = parse.entity_text("bug", B339)
    assert doc["id"] == "B-339" and "short test summary info" in body


@pytest.mark.parametrize("block", [
    "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature/x\n",
    "<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> 1a2b3c\r\n",
    "<<<<<<< ours\nmine\n||||||| base\norig\n=======\nyours\n>>>>>>> theirs\n",
    "<<<<<<<\nours\n=======\ntheirs\n>>>>>>>\n",
])
def test_d2_real_git_conflict_blocks_are_still_rejected(block):
    with pytest.raises(ValueError, match="conflict markers"):
        parse.entity_text("bug", "---\nid: B-1\ntitle: t\n---\nintro\n" + block)


def test_d2_conflict_in_frontmatter_is_rejected():
    raw = "---\nid: B-1\n<<<<<<< HEAD\ntitle: a\n=======\ntitle: b\n>>>>>>> other\n---\nbody\n"
    with pytest.raises(ValueError):
        parse.entity_text("bug", raw)


BUG_REL = "bugs/B-339.md"


@pytest.fixture
def bug_root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, lambda: bs.backlog_bug_create(title="Seed bug"),
                       visibility=None)
    with twins.at(twins.native):
        yield twins.native


def _bug_rel(root):
    [path] = (root / ".taskmaster" / "bugs").glob("B-*.md")
    return path.relative_to(root / ".taskmaster").as_posix()


def test_d2_previously_quarantined_bytes_become_eligible_once_they_parse(bug_root):
    """The live store quarantined B-339 under the substring rule. The unchanged bytes
    must be re-parsed on the next sync, not skipped as 'unchanged quarantined bytes'."""
    rel = _bug_rel(bug_root)
    ident = rel.split("/")[-1][:-3]
    path = bug_root / ".taskmaster" / rel
    content = B339.replace("B-339", ident).encode("utf-8")
    path.write_bytes(content)
    with native_connection(bug_root) as connection:
        with Repository(connection).snapshot() as snapshot:
            token = prepare(snapshot, bug_root / ".taskmaster", rel).arguments["expected_manifest"]
        # Record the quarantine exactly as the old substring rule did.
        commit_only(connection, "sync.apply", {
            "file": rel, "mode": "quarantine", "rows": [], "reason": "invalid authored projection: git conflict markers",
            "observed_base64": base64.b64encode(content).decode("ascii"),
            "observed_hash": hashlib.sha1(content).hexdigest(), "expected_manifest": token})
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()[0] == 1
    with Coordinator(bug_root):
        client = Client(bug_root, autostart=False)
        result = client.sync(files=[rel])
        assert result["imports"] and result["imports"][0]["state"] in {"accepted", "conflict"}, result
    with native_connection(bug_root) as connection:
        assert connection.execute("SELECT quarantined FROM projection WHERE file=?", (rel,)).fetchone()[0] == 0


# --- D3: slim fields edited in an epic/phase document -------------------------

EPIC_REL = "epics/test-epic.md"


@pytest.fixture
def epic_root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch,
                       lambda: bs.backlog_update_epic("test-epic", "description", "Heavy prose"),
                       visibility=None)
    with twins.at(twins.native):
        yield twins


def _epic(root):
    with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
        return snapshot.get("epic", "test-epic", include_body=True)


def test_d3_legacy_baseline_ignores_the_epic_document_title_mirror(epic_root):
    """Compatibility baseline: the legacy Store treats the document `title:` as a
    readability mirror of backlog.yaml `name` and never applies it."""
    with epic_root.at(epic_root.legacy):
        path = epic_root.legacy / ".taskmaster" / EPIC_REL
        path.write_text(path.read_text(encoding="utf-8").replace("title: Test Epic", "title: Renamed"),
                        encoding="utf-8")
        store.reset_for_tests()
        bs.backlog_epic_status("test-epic")
        with closing(sqlite3.connect(epic_root.legacy / ".taskmaster" / "local" / "store.db")) as connection:
            doc = json.loads(connection.execute(
                "SELECT doc FROM entities WHERE kind='epic' AND id='test-epic'").fetchone()[0])
        assert doc.get("name") == "Test Epic" and "title" not in doc


def test_d3_epic_title_edit_is_not_reported_accepted_and_the_file_is_kept(epic_root):
    root = epic_root.native
    path = root / ".taskmaster" / EPIC_REL
    with Coordinator(root):
        client = Client(root, autostart=False)
        assert client.sync(files=[EPIC_REL])["state"] == "synchronized"
        text = path.read_text(encoding="utf-8")
        assert "title: Test Epic" in text
        edited = text.replace("title: Test Epic", "title: Renamed Epic")
        path.write_text(edited, encoding="utf-8")
        result = client.sync(files=[EPIC_REL])
        [outcome] = result["imports"]
        assert outcome["state"] != "accepted", result
        assert outcome["state"] == "conflict"
        assert "title" in outcome["reason"] and "backlog.yaml" in outcome["reason"]
        assert _epic(root)["fields"]["name"] == "Test Epic"
        # Flag-and-keep: the author's bytes are not overwritten by the old title.
        assert path.read_text(encoding="utf-8") == edited


def test_d3_heavy_edit_alongside_title_edit_applies_heavy_and_flags_title(epic_root):
    root = epic_root.native
    path = root / ".taskmaster" / EPIC_REL
    with Coordinator(root):
        client = Client(root, autostart=False)
        client.sync(files=[EPIC_REL])
        edited = path.read_text(encoding="utf-8").replace("title: Test Epic", "title: Renamed Epic") \
            .replace("Heavy prose", "New heavy prose")
        path.write_text(edited, encoding="utf-8")
        [outcome] = client.sync(files=[EPIC_REL])["imports"]
        assert outcome["state"] == "conflict" and "title" in outcome["reason"]
        epic = _epic(root)["fields"]
        assert epic["description"] == "New heavy prose" and epic["name"] == "Test Epic"
        assert path.read_text(encoding="utf-8") == edited


def test_d3_resolving_through_backlog_yaml_then_clears_the_flag(epic_root):
    root = epic_root.native
    path = root / ".taskmaster" / EPIC_REL
    with Coordinator(root):
        client = Client(root, autostart=False)
        client.sync(files=[EPIC_REL, "backlog.yaml"])
        path.write_text(path.read_text(encoding="utf-8").replace("title: Test Epic", "title: Renamed Epic"),
                        encoding="utf-8")
        assert client.sync(files=[EPIC_REL])["imports"][0]["state"] == "conflict"
        backlog = root / ".taskmaster" / "backlog.yaml"
        backlog.write_text(backlog.read_text(encoding="utf-8").replace("name: Test Epic", "name: Renamed Epic"),
                           encoding="utf-8")
        client.sync(files=["backlog.yaml"])
        assert _epic(root)["fields"]["name"] == "Renamed Epic"
        result = client.sync(files=[EPIC_REL])
        assert result["state"] == "synchronized", result


def test_d3_take_file_refuses_a_title_it_cannot_apply(epic_root):
    root = epic_root.native
    path = root / ".taskmaster" / EPIC_REL
    with Coordinator(root):
        client = Client(root, autostart=False)
        client.sync(files=[EPIC_REL])
    path.write_text(path.read_text(encoding="utf-8").replace("title: Test Epic", "title: Renamed Epic"),
                    encoding="utf-8")
    with native_connection(root) as connection, Repository(connection).snapshot() as snapshot:
        with pytest.raises(ValueError, match="title"):
            prepare(snapshot, root / ".taskmaster", EPIC_REL, take_file=True)


def test_d3_unchanged_title_mirror_does_not_flag_a_heavy_only_edit(epic_root):
    root = epic_root.native
    path = root / ".taskmaster" / EPIC_REL
    with Coordinator(root):
        client = Client(root, autostart=False)
        client.sync(files=[EPIC_REL])
        path.write_text(path.read_text(encoding="utf-8").replace("Heavy prose", "Edited prose"),
                        encoding="utf-8")
        result = client.sync(files=[EPIC_REL])
        assert result["imports"][0]["state"] == "accepted" and result["state"] == "synchronized", result
        assert _epic(root)["fields"]["description"] == "Edited prose"


# --- D3 sweep: other document-owned-elsewhere fields --------------------------

TASK_REL = "tasks/test-epic-001.md"


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch,
                       lambda: bs.backlog_add_task(title="Service task", epic="test-epic", phase="dev"),
                       visibility=None)
    with twins.at(twins.native):
        yield twins.native


def test_d3_sweep_claim_written_into_a_task_file_is_not_reported_accepted(task_root):
    path = task_root / ".taskmaster" / TASK_REL
    with Coordinator(task_root):
        client = Client(task_root, autostart=False)
        client.sync(files=[TASK_REL])
        text = path.read_text(encoding="utf-8")
        assert "locked_by" not in text
        path.write_text(text.replace("---\n", "---\nlocked_by: someone-else\n", 1), encoding="utf-8")
        [outcome] = client.sync(files=[TASK_REL])["imports"]
        assert outcome["state"] == "conflict" and "locked_by" in outcome["reason"], outcome


def test_d3_sweep_derived_backlog_index_edit_is_not_reported_accepted(task_root):
    path = task_root / ".taskmaster" / "backlog.yaml"
    with Coordinator(task_root):
        client = Client(task_root, autostart=False)
        client.sync(files=["backlog.yaml"])
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["handovers"] = [{"id": "invented", "status": "open"}]
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        [outcome] = client.sync(files=["backlog.yaml"])["imports"]
        assert outcome["state"] == "conflict" and "handovers" in outcome["reason"], outcome


def test_d3_sweep_heavy_field_typed_into_backlog_yaml_is_not_reported_accepted(task_root):
    path = task_root / ".taskmaster" / "backlog.yaml"
    with Coordinator(task_root):
        client = Client(task_root, autostart=False)
        client.sync(files=["backlog.yaml"])
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("name: Test Epic", "name: Test Epic\n  description: typed here"),
                        encoding="utf-8")
        [outcome] = client.sync(files=["backlog.yaml"])["imports"]
        assert outcome["state"] == "conflict" and "description" in outcome["reason"], outcome


def test_d3_sweep_epic_removed_from_backlog_yaml_is_not_reported_accepted(task_root):
    """Absence is never a deletion, so the re-export restores the entry: say so."""
    path = task_root / ".taskmaster" / "backlog.yaml"
    with Coordinator(task_root):
        client = Client(task_root, autostart=False)
        client.sync(files=["backlog.yaml"])
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["phases"] = [phase for phase in data["phases"] if phase["id"] != "dev"]
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        [outcome] = client.sync(files=["backlog.yaml"])["imports"]
        assert outcome["state"] == "conflict" and "phase:dev (removed" in outcome["reason"], outcome


# --- D4: stored bodies ending in a newline ------------------------------------

@pytest.fixture
def handover_root(tmp_path, monkeypatch):
    def seed():
        bs.backlog_handover_create(tldr="Newline body", next_action="Continue",
                                   body="## Where execution stands\n\nFirst line.")
        [path] = (bs.ROOT / ".taskmaster" / "handovers").glob("*.md")
        store.reset_for_tests()
        # 6.x stored some bodies with a trailing newline; reproduce that row shape.
        with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db",
                                     isolation_level=None)) as connection:
            connection.execute("UPDATE entities SET body=body || char(10) WHERE kind='handover' AND id=?",
                               (path.stem,))
        store.reset_for_tests()
    twins = make_twins(tmp_path, monkeypatch, seed, visibility=None)
    with twins.at(twins.native):
        yield twins.native


def test_d4_append_to_newline_terminated_body_merges_cleanly(handover_root):
    root = handover_root
    [path] = (root / ".taskmaster" / "handovers").glob("*.md")
    rel = path.relative_to(root / ".taskmaster").as_posix()
    with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
        assert snapshot.get("handover", path.stem, include_body=True)["body"].endswith("\n")
    with Coordinator(root):
        client = Client(root, autostart=False)
        assert client.sync(files=[rel])["state"] == "synchronized"
        # The first sync recorded the base; the stored body still ends in a newline.
        with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
            assert snapshot.get("handover", path.stem, include_body=True)["body"].endswith("\n")
        path.write_bytes(path.read_bytes() + b"\nAppended line.\n")
        result = client.sync(files=[rel])
        [outcome] = result["imports"]
        assert outcome["state"] == "accepted", result
        with closing(connect(root, readonly=True)) as connection, Repository(connection).snapshot() as snapshot:
            assert snapshot.get("handover", path.stem, include_body=True)["body"].rstrip("\n").endswith("Appended line.")


def test_d4_sweep_legacy_dirty_merge_keeps_an_append_to_a_newline_terminated_body(tmp_path, monkeypatch):
    """Control for the legacy merge of a hand edit over a failed export: it compares
    the stored body with a parsed base too, but every legacy write that can leave a
    row dirty goes through `split_body`, which drops the trailing newline first."""
    import os
    from pathlib import Path
    from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter
    store.reset_for_tests()
    tm = tmp_path / "repo" / ".taskmaster"
    (tm / "tasks").mkdir(parents=True)
    backlog = tm / "backlog.yaml"
    backlog.write_text(yaml.safe_dump({"version": 4, "meta": {"schema_version": 4},
                                       "epics": [{"id": "core", "name": "Core", "status": "in-progress"}],
                                       "phases": []}, sort_keys=False), encoding="utf-8")
    task_path = tm / "tasks" / "core-001.md"
    task_path.write_text(render_frontmatter({"id": "core-001", "title": "Base title", "status": "todo",
                                             "epic": "core", "order": 1.0}, "Base body"), encoding="utf-8")
    opened = store.open_store(backlog_path=backlog, session="d4-legacy")
    try:
        with closing(sqlite3.connect(opened.db_path, isolation_level=None)) as connection:
            connection.execute("UPDATE entities SET body=body || char(10) WHERE kind='task' AND id='core-001'")
        real_replace = os.replace

        def fail_task_export(source, destination):
            if Path(destination) == task_path:
                raise PermissionError(13, "held open", str(destination))
            return real_replace(source, destination)

        monkeypatch.setattr(os, "replace", fail_task_export)
        with opened.transaction(tool="db-title") as tx:
            task = tx.get("task", "core-001")
            task["title"] = "DB title"
            tx.put("task", "core-001", task)
        monkeypatch.setattr(os, "replace", real_replace)
        disk_fm, disk_body = parse_frontmatter(task_path.read_text(encoding="utf-8"))
        task_path.write_text(render_frontmatter(disk_fm, disk_body.rstrip("\n") + "\n\nAppended line."),
                             encoding="utf-8")
        with opened.transaction(tool="merge-dirty"):
            pass
        with closing(sqlite3.connect(opened.db_path)) as connection:
            doc, body = connection.execute(
                "SELECT doc,body FROM entities WHERE kind='task' AND id='core-001'").fetchone()
        assert json.loads(doc)["title"] == "DB title"
        assert "Appended line." in body
    finally:
        store.reset_for_tests()
