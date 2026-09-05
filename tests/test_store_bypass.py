# User intent: prove no production code writes the backlog projection behind
# the SQLite store's back — every entity directory, not just tasks — so the
# viewer, the link tools and the schema marker all commit through a store
# transaction and the guard says so loudly when something does not.
"""Bypass-guard tests for the store projection boundary (spec §2.3, §3.1)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


def _committed(root: Path) -> dict:
    store.reset_for_tests()
    return store.load_dict(_bp(root))


def _tasks(root: Path) -> dict:
    out: dict[str, dict] = {}
    for epic in _committed(root).get("epics", []):
        for task in epic.get("tasks", []):
            out[task["id"]] = task
    return out


def _ops(root: Path, ident: str) -> list[str]:
    store.reset_for_tests()
    connection = store.open_store(_bp(root)).connection
    return [
        row[0]
        for row in connection.execute(
            "SELECT op FROM changes WHERE id=? ORDER BY seq", (ident,)
        )
    ]


def _row(root: Path, kind: str, ident: str) -> dict:
    store.reset_for_tests()
    connection = store.open_store(_bp(root)).connection
    row = connection.execute(
        "SELECT archived,deleted FROM entities WHERE kind=? AND id=?", (kind, ident)
    ).fetchone()
    assert row is not None, f"{kind} {ident} missing from committed state"
    return dict(row)


# ── the guard itself ───────────────────────────────────────────────────────


def _write_as_production(target: Path) -> None:
    """Perform a raw projection write whose stack frame is production code.

    The guard classifies a write by the outermost `taskmaster/` frame, so a
    code object compiled under backlog_server's filename is exactly what a real
    bypass writer looks like from the guard's point of view.
    """
    source = "from taskmaster.taskmaster_v3 import write_task_file\n" \
             "write_task_file(target, {'id': 'x'}, 'body')\n"
    code = compile(source, str(Path(bs.__file__)), "exec")
    exec(code, {"target": target})  # noqa: S102 - deliberate frame forgery


def test_guard_trips_on_a_production_task_file_write(tmp_taskmaster):
    target = tmp_taskmaster / ".taskmaster" / "tasks" / "e1-001.md"
    with pytest.raises(AssertionError, match="projection bypass"):
        _write_as_production(target)
    assert not target.exists()


def test_guard_trips_on_a_production_backlog_yaml_write(tmp_taskmaster):
    target = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    with pytest.raises(AssertionError, match="projection bypass"):
        _write_as_production(target)


def test_guard_lets_the_store_write_the_projection(tm_epic_phase):
    created = bs.backlog_add_task(title="A", epic="test-epic", phase="dev")
    assert "Error" not in created, created
    assert (tm_epic_phase / ".taskmaster" / "tasks" / "test-epic-001.md").exists()
    assert (tm_epic_phase / ".taskmaster" / "backlog.yaml").exists()


def test_guard_trips_on_a_production_bug_file_write(tmp_taskmaster):
    """Bugs left the narrow step-3 allowlist: the store owns them too."""
    target = tmp_taskmaster / ".taskmaster" / "bugs" / "B-001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AssertionError, match="projection bypass"):
        _write_as_production(target)
    assert not target.exists()


# Every entity directory the store owns, including the nested shapes: the two
# `archive/` conventions, the `handovers/_archive/<year>/` year bucket, the
# derived `ideas/IDEAS.md` index, the Linear queue file and the
# `integrations/trackers/` import fallback.
_GUARDED_TARGETS = (
    "bugs/B-001.md",
    "bugs/archive/B-001.md",
    "issues/ISS-001.md",
    "handovers/2026-09-05-session.md",
    "handovers/_archive/2026/2026-09-05-session.md",
    "decisions/DEC-001.md",
    "ideas/I-001.md",
    "ideas/IDEAS.md",
    "notes/N-001.md",
    "notes/_archive/N-001.md",
    "areas/backend.md",
    "trackers/linear-acme-eng-1.md",
    "integrations/linear-queue.json",
    "integrations/trackers/linear-acme-eng-1.md",
)


@pytest.mark.parametrize("relative", _GUARDED_TARGETS)
def test_guard_trips_on_a_production_write_to_every_entity_path(
    tmp_taskmaster, relative
):
    target = tmp_taskmaster / ".taskmaster" / Path(relative)
    # Create the directory so an unguarded write really lands on disk — without
    # it a miss would raise FileNotFoundError and read as a pass.
    target.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AssertionError, match="projection bypass"):
        _write_as_production(target)
    assert not target.exists()


# ── the schema marker no longer writes backlog.yaml ────────────────────────


def test_v3_marker_writer_is_gone():
    assert not hasattr(bs, "_ensure_v3_marker"), (
        "_ensure_v3_marker rewrote backlog.yaml outside the store"
    )


def test_creating_an_issue_keeps_the_schema_marker_without_a_raw_write(tm_epic_phase):
    out = bs.backlog_issue_create(
        title="Recurring flake",
        severity="P1",
        evidence="seen twice on CI",
        impact="blocks release",
    )
    assert "Error" not in out, out
    raw = yaml.safe_load(_bp(tm_epic_phase).read_text(encoding="utf-8"))
    assert int(raw["meta"]["schema_version"]) >= 3


def test_creating_a_decision_keeps_the_schema_marker(tm_epic_phase):
    out = bs.backlog_decision_create(
        title="Which store?", options=["a", "b"], recommendation=1
    )
    assert "Error" not in out, out
    raw = yaml.safe_load(_bp(tm_epic_phase).read_text(encoding="utf-8"))
    assert int(raw["meta"]["schema_version"]) >= 3


# ── the legacy viewer write primitives are gone ────────────────────────────


@pytest.mark.parametrize(
    "name", ["create_task", "update_task", "archive_task", "with_file_lock",
             "compute_etag"]
)
def test_taskmaster_v3_no_longer_owns_task_writes(name):
    from taskmaster import taskmaster_v3 as v3

    assert not hasattr(v3, name), f"taskmaster_v3.{name} is a projection bypass"


# ── the viewer writes through the store ────────────────────────────────────


@pytest.fixture()
def viewer(tm_epic_phase):
    """The real HTTP viewer, in-process, on a store-adopted project."""
    created = bs.backlog_add_task(title="X", epic="test-epic", phase="dev")
    assert "Error" not in created, created
    server, port = bs._make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/api/identity", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.05)
    try:
        yield base, tm_epic_phase
    finally:
        server.shutdown()


def _request(method, url, body=None, headers=None):
    head = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        head["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=head)
    try:
        return urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        return exc


def _etag(base: str, task_id: str = "test-epic-001") -> str:
    resp = urllib.request.urlopen(f"{base}/api/task/{task_id}", timeout=10)
    return resp.headers.get("ETag").strip('"')


def test_viewer_patch_commits_through_the_store(viewer):
    base, root = viewer
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Renamed"})
    assert resp.status == 200
    assert json.loads(resp.read())["task"]["title"] == "Renamed"
    assert _tasks(root)["test-epic-001"]["title"] == "Renamed"


def test_viewer_put_commits_through_the_store(viewer):
    base, root = viewer
    resp = _request("PUT", f"{base}/api/tasks/test-epic-001", {"title": "Replaced"})
    assert resp.status == 200
    assert _tasks(root)["test-epic-001"]["title"] == "Replaced"


def test_viewer_patch_status_stamps_and_persists(viewer):
    base, root = viewer
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001",
                    {"status": "in-progress"})
    assert resp.status == 200
    task = _tasks(root)["test-epic-001"]
    assert task["status"] == "in-progress"
    assert task.get("started")


def test_viewer_post_creates_a_task_in_the_store(viewer):
    base, root = viewer
    resp = _request("POST", f"{base}/api/tasks",
                    {"epic": "test-epic", "title": "New", "priority": "high"})
    assert resp.status == 201, resp.read()
    new_id = json.loads(resp.read())["task"]["id"]
    assert new_id in _tasks(root)
    assert (root / ".taskmaster" / "tasks" / f"{new_id}.md").exists()


def test_viewer_archive_records_an_explicit_store_archive(viewer):
    base, root = viewer
    resp = _request("POST", f"{base}/api/tasks/test-epic-001/archive", {})
    assert resp.status == 200
    assert "archive" in _ops(root, "test-epic-001")
    assert _row(root, "task", "test-epic-001")["archived"] == 1
    assert (root / ".taskmaster" / "tasks" / "archive" / "test-epic-001.md").exists()


def test_viewer_etag_is_the_store_creation_token_and_sequence(viewer):
    base, root = viewer
    etag = _etag(base)
    status = store.status(_bp(root))
    assert etag == f"{status.creation_token}:{status.max_seq}"


def test_viewer_etag_advances_after_a_write(viewer):
    base, _root = viewer
    before = _etag(base)
    _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Moved on"})
    assert _etag(base) != before


def test_viewer_stale_if_match_is_rejected(viewer):
    base, _root = viewer
    stale = _etag(base)
    _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Other writer"})
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Mine"},
                    headers={"If-Match": stale})
    assert resp.status == 409
    body = json.loads(resp.read())
    assert body["error"] == "stale"
    assert body["current"]["title"] == "Other writer"


def test_viewer_matching_if_match_succeeds(viewer):
    base, root = viewer
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Fresh"},
                    headers={"If-Match": _etag(base)})
    assert resp.status == 200
    assert _tasks(root)["test-epic-001"]["title"] == "Fresh"


def test_viewer_backlog_endpoint_reads_committed_store_state(viewer):
    base, _root = viewer
    _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "Committed"})
    payload = json.loads(urllib.request.urlopen(f"{base}/api/backlog", timeout=10).read())
    assert "Committed" in [t["title"] for t in payload["tasks"]]


# ── the link tools write tasks through the store ───────────────────────────


@pytest.fixture()
def two_tasks(tmp_taskmaster):
    """Two linkable tasks. The link tools only recognise `T-NNN` task ids.

    Seeded as files (a test-owned write, before the store adopts the project)
    because `backlog_add_task` derives task ids from the epic id.
    """
    backlog = _bp(tmp_taskmaster)
    backlog.write_text(
        yaml.safe_dump({
            "meta": {"schema_version": 3},
            "project": "test-project",
            "epics": [{"id": "e1", "name": "E", "tasks": [
                {"id": "T-001", "title": "A", "status": "todo"},
                {"id": "T-002", "title": "B", "status": "todo"},
            ]}],
            "phases": [],
        }),
        encoding="utf-8",
    )
    store.reset_for_tests()
    return tmp_taskmaster


def test_link_create_between_two_tasks_commits_through_the_store(two_tasks):
    out = bs.backlog_link(action="create", source="T-001",
                          target="T-002", type="blocks")
    assert out.startswith("ok:"), out
    tasks = _tasks(two_tasks)
    src = [(l["type"], l["target"]) for l in tasks["T-001"].get("links", [])]
    dst = [(l["type"], l["target"]) for l in tasks["T-002"].get("links", [])]
    assert ("blocks", "T-002") in src
    assert ("depends_on", "T-001") in dst


def test_link_remove_between_two_tasks_commits_through_the_store(two_tasks):
    bs.backlog_link(action="create", source="T-001",
                    target="T-002", type="blocks")
    out = bs.backlog_link(action="remove", source="T-001",
                          target="T-002")
    assert out.startswith("ok:"), out
    tasks = _tasks(two_tasks)
    assert not tasks["T-001"].get("links")
    assert not tasks["T-002"].get("links")


# ── the derived-index rebuild never touches store.db ───────────────────────


def test_index_rebuild_goes_through_the_store_and_keeps_store_db(tm_epic_phase):
    bs.backlog_add_task(title="A", epic="test-epic", phase="dev")
    db = store.db_path(_bp(tm_epic_phase))
    assert db.exists()
    before = store.status(_bp(tm_epic_phase))
    calls: list[str] = []
    real = store.Store.rebuild_derived

    def spy(self):
        calls.append("rebuild")
        return real(self)

    store.Store.rebuild_derived = spy
    try:
        out = bs.backlog_index_status(rebuild=True)
    finally:
        store.Store.rebuild_derived = real
    assert calls, "backlog_index_status(rebuild=True) skipped Store.rebuild_derived"
    assert "Index:" in out
    assert db.exists(), "the derived rebuild deleted store.db"
    after = store.status(_bp(tm_epic_phase))
    assert after.creation_token == before.creation_token


# ── carried-over Task 2 review finding: batch ops vs archived tasks ────────


@pytest.fixture()
def archived_task(tm_epic_phase):
    bs.backlog_add_task(title="A", epic="test-epic", phase="dev")
    out = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert "Error" not in out, out
    return tm_epic_phase


def test_batch_pick_refuses_an_archived_task(archived_task):
    out = bs.backlog_batch_update(operations="pick test-epic-001")
    assert "archived" in out.lower(), out
    assert _row(archived_task, "task", "test-epic-001")["archived"] == 1


def test_batch_status_refuses_an_illegal_transition_out_of_archived(archived_task):
    out = bs.backlog_batch_update(operations="status test-epic-001 done")
    assert "archived" in out.lower() or "transition" in out.lower(), out
    assert _row(archived_task, "task", "test-epic-001")["archived"] == 1


# ── fix round 1: a read must not write ─────────────────────────────────────


@pytest.fixture()
def legacy_dep_tasks(tmp_taskmaster):
    """Two tasks with a legacy `depends_on` and no `links` array.

    `read_entity_anywhere`'s fallback synthesizes `links` from `depends_on` on
    every read; the store must never commit that synthesis.
    """
    backlog = _bp(tmp_taskmaster)
    backlog.write_text(
        yaml.safe_dump({
            "meta": {"schema_version": 3},
            "project": "test-project",
            "epics": [{"id": "e1", "name": "E", "tasks": [
                {"id": "T-001", "title": "A", "status": "todo",
                 "depends_on": ["T-002"]},
                {"id": "T-002", "title": "B", "status": "todo"},
            ]}],
            "phases": [],
        }),
        encoding="utf-8",
    )
    store.reset_for_tests()
    return tmp_taskmaster


def test_read_entity_anywhere_does_not_commit_synthesized_links(legacy_dep_tasks):
    from taskmaster import taskmaster_v3 as v3

    root = legacy_dep_tasks
    with bs._transaction(tool="test:read-then-write") as data:
        entity = v3.read_entity_anywhere(_bp(root), "T-001")
        assert entity is not None
        assert entity.get("links"), "the read fallback should synthesize links"
        bs._mutate_and_save(data)
    committed = _tasks(root)["T-001"]
    assert not committed.get("links"), (
        "read_entity_anywhere's read-only fallback was committed by the "
        "next _mutate_and_save"
    )
    assert committed.get("depends_on") == ["T-002"]


def test_store_read_task_returns_a_copy_of_the_transaction_document(legacy_dep_tasks):
    root = legacy_dep_tasks
    with bs._transaction(tool="test:identity") as data:
        live = bs._find_task(data, "T-001")[0]
        read = bs._store_read_task(_bp(root), "T-001")
        assert read is not None
        assert read == live
        assert read is not live, "_store_read_task handed out the live tx document"
        read["title"] = "mutated by a reader"
        bs._mutate_and_save(data)
    assert _tasks(root)["T-001"]["title"] == "A"


# ── fix round 1: the viewer cannot un-archive ──────────────────────────────


@pytest.fixture()
def archived_viewer(viewer):
    base, root = viewer
    out = bs.backlog_archive_task(task_id="test-epic-001", reason="wont-fix")
    assert "Error" not in out, out
    assert _row(root, "task", "test-epic-001")["archived"] == 1
    return base, root


def _archived_file(root: Path, task_id: str = "test-epic-001") -> Path:
    return root / ".taskmaster" / "tasks" / "archive" / f"{task_id}.md"


def _live_file(root: Path, task_id: str = "test-epic-001") -> Path:
    return root / ".taskmaster" / "tasks" / f"{task_id}.md"


@pytest.mark.parametrize("method", ["PATCH", "PUT"])
def test_viewer_cannot_unarchive_a_task_with_an_illegal_transition(
    archived_viewer, method
):
    base, root = archived_viewer
    resp = _request(method, f"{base}/api/tasks/test-epic-001", {"status": "done"})
    assert resp.status == 422, resp.status
    body = json.loads(resp.read())
    assert body["ok"] is False
    assert "status" in body["errors"], body
    assert _row(root, "task", "test-epic-001")["archived"] == 1
    assert _archived_file(root).exists()
    assert not _live_file(root).exists()


def test_viewer_may_still_return_an_archived_task_to_todo(archived_viewer):
    base, root = archived_viewer
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"status": "todo"})
    assert resp.status == 200, resp.read()
    assert _row(root, "task", "test-epic-001")["archived"] == 0
    assert _live_file(root).exists()


# ── fix round 1: the viewer validates against committed store state ────────


def test_viewer_patch_validates_against_the_store_not_the_projection_file(viewer):
    base, root = viewer
    # A projection file the store still has a row for: the store is
    # authoritative, so a validation gate that reads the file sees nothing.
    _live_file(root).unlink()
    resp = _request("PATCH", f"{base}/api/tasks/test-epic-001", {"title": "From store"})
    assert resp.status == 200, (resp.status, resp.read())
    assert _tasks(root)["test-epic-001"]["title"] == "From store"


def test_viewer_post_validates_deps_against_the_store(viewer):
    base, root = viewer
    _live_file(root).unlink()
    resp = _request("POST", f"{base}/api/tasks", {
        "epic": "test-epic", "title": "New", "depends_on": ["test-epic-001"],
    })
    assert resp.status == 201, (resp.status, resp.read())
    assert "test-epic-002" in _tasks(root)
