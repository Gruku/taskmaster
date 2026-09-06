"""User intent: what adoption writes, adoption must be able to read back.

The real 2.2k-task verification (task 5.3) found the opposite: an epic that
existed only as `epics/<id>.md` lost its id and title on import, the exporter
wrote `- {}` into backlog.yaml, tasks with no `epic:` key fanned into both
id-less epics, and the *second* cold open of the migrated tree died with
"task … appears twice" before any tool ran. The projection was unloadable and
the epic names were gone from every file.
"""
from __future__ import annotations

import yaml

from taskmaster import store
from taskmaster import taskmaster_v3 as v3


def _seed(tmp_path, backlog: dict):
    tm = tmp_path / ".taskmaster"
    tm.mkdir(parents=True, exist_ok=True)
    (tm / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    bp = tm / "backlog.yaml"
    bp.write_text(yaml.safe_dump(backlog, sort_keys=False), encoding="utf-8")
    store.reset_for_tests()
    return bp


def _reopen_cold(bp):
    """Drop the database and open the migrated projection from scratch.

    This is what a fresh clone does: `local/` is gitignored, so the store never
    travels with the repo and every second checkout adopts the exported files.
    """
    store.reset_for_tests()
    local = bp.parent / "local"
    for leftover in local.glob("store.db*"):
        leftover.unlink()
    return store.open_store(backlog_path=bp, session="second-cold-open")


# ── an epic that exists only as a file keeps its identity ────────────────────


def test_an_epic_only_on_disk_keeps_its_id_and_name(tmp_path):
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "listed", "name": "Listed", "status": "active", "tasks": []}],
        "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.epic_file_path(bp, "export-comply"),
        {"id": "export-comply", "title": "Export Compliance", "status": "active",
         "description": "the heavy half"},
        "epic body",
    )

    loaded = store.open_store(
        backlog_path=bp, session="epic-only-on-disk"
    ).load_dict()

    epics = {e.get("id"): e for e in loaded["epics"]}
    assert "export-comply" in epics, sorted(epics)
    recovered = epics["export-comply"]
    # The file's `title` is the readability mirror `_split_entity_for_v3`
    # writes; for an orphan it is the only surviving copy of the display name,
    # so it comes back as `name` rather than as a second field that drifts.
    assert recovered["name"] == "Export Compliance", recovered
    assert recovered["description"] == "the heavy half"
    store.reset_for_tests()


def test_the_exporter_never_writes_an_id_less_epic_into_backlog_yaml(tmp_path):
    """`- {}` in backlog.yaml is the shape that bricked the second open."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.epic_file_path(bp, "sp-v5-qa-wave2"),
        {"id": "sp-v5-qa-wave2", "title": "QA Wave 2", "description": "heavy"},
        "body",
    )

    store.open_store(backlog_path=bp, session="no-empty-epic-entry")

    exported = bp.read_text(encoding="utf-8")
    assert "- {}" not in exported, exported
    projected = yaml.safe_load(exported)
    assert all(e.get("id") for e in projected["epics"] or []), projected["epics"]
    # And the title survived into the file the user reads.
    epic_md = v3.epic_file_path(bp, "sp-v5-qa-wave2").read_text(encoding="utf-8")
    assert "QA Wave 2" in epic_md, epic_md
    store.reset_for_tests()


def test_a_second_cold_open_of_the_migrated_tree_succeeds(tmp_path):
    """The whole chain, end to end: adopt, throw the database away, reopen.

    A task with no `epic:` key used to land in the `None` bucket, which was
    handed to every id-less epic in turn, so `_flatten_backlog_dict` saw the
    same task twice and refused the import.
    """
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "listed", "name": "Listed", "status": "active", "tasks": [
            {"id": "listed-001", "title": "Kept", "status": "todo"},
        ]}],
        "phases": [], "context": {},
    })
    for stem in ("export-comply", "sp-v5-qa-wave2"):
        v3.write_task_file(
            v3.epic_file_path(bp, stem),
            {"id": stem, "title": stem.upper(), "description": "heavy"},
            "body",
        )
    # Tasks whose file carries no `epic:` at all — the real backlog had four.
    for stem in ("ac-slider-gate-006", "pb-reliability-009"):
        v3.write_task_file(
            v3.task_file_path(bp, stem),
            {"id": stem, "title": "Orphaned", "status": "todo", "order": 1.0},
            "task body",
        )

    store.open_store(backlog_path=bp, session="first-cold-open").load_dict()
    reopened = _reopen_cold(bp)

    loaded = reopened.load_dict()
    epics = {e.get("id"): e for e in loaded["epics"]}
    assert {"listed", "export-comply", "sp-v5-qa-wave2"} <= set(epics), sorted(epics)
    seen = [t.get("id") for e in loaded["epics"] for t in e.get("tasks") or []]
    assert len(seen) == len(set(seen)), seen
    store.reset_for_tests()


def test_a_task_with_no_epic_is_an_orphan_not_a_shared_bucket(tmp_path):
    """`load_v4` keyed its epic buckets by `e.get("id")`, so a `None` id was a
    real key and every task without an `epic:` joined it."""
    bp = _seed(tmp_path, {
        "version": 4, "project": "t",
        "meta": {"updated": "", "schema_version": 4},
        "epics": [{"name": "No id here", "status": "active"}],
        "phases": [],
    })
    v3.write_task_file(
        v3.task_file_path(bp, "loose-001"),
        {"id": "loose-001", "title": "No epic", "status": "todo", "order": 1.0},
        "body",
    )

    data = v3.load_v4(bp)

    assert data["epics"][0].get("tasks") == [], data["epics"][0]
    assert "loose-001" in (data.get("_orphan_tasks") or []), data.get("_orphan_tasks")


# ── adoption proves it can read back what it wrote ───────────────────────────


def test_adoption_refuses_to_commit_a_file_it_cannot_read_back(tmp_path, monkeypatch):
    """The general property behind the epic-identity bug: the 458-second
    migration rewrote every file in the project and committed the result
    without ever checking it was still loadable. When it was not, the store
    that could still answer was the only copy — and `local/` is gitignored."""
    import pytest  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "Fine", "status": "todo", "notes": "n"},
        ]}],
        "phases": [], "context": {},
    })
    baseline = bp.read_text(encoding="utf-8")

    # A renderer that drops a field is exactly the class of defect the check
    # exists for: the bytes are valid, they simply are not the entity.
    real_render = store.render_frontmatter

    def lossy(frontmatter, body):
        if frontmatter.get("id") == "e-001":
            frontmatter = {k: v for k, v in frontmatter.items() if k != "notes"}
        return real_render(frontmatter, body)

    monkeypatch.setattr(store, "render_frontmatter", lossy)

    with pytest.raises(store.AdoptionRoundTripError) as caught:
        store.open_store(backlog_path=bp, session="round-trip-refused")

    assert "e-001" in str(caught.value), str(caught.value)
    assert "Nothing was changed" in str(caught.value)
    # The tree the user started from is intact.
    assert bp.read_text(encoding="utf-8") == baseline
    assert not v3.task_file_path(bp, "e-001").exists()
    store.reset_for_tests()


def test_adoption_refuses_a_backlog_yaml_it_cannot_read_back(tmp_path, monkeypatch):
    """`backlog.yaml` is the file the epic-identity bug actually corrupted — it
    is where `- {}` was written, and its failure is what made the next cold open
    raise before any tool ran. It goes through `_replace_projection` directly,
    so the per-entity check never saw it."""
    import pytest  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "Fine", "status": "todo"},
        ]}],
        "phases": [{"id": "p", "name": "P", "order": 1}],
        "context": {},
    })
    baseline = bp.read_text(encoding="utf-8")

    real_dump = store.yaml.dump

    def lossy(data, **kwargs):
        if isinstance(data, dict) and "epics" in data:
            data = dict(data)
            data["epics"] = [{k: v for k, v in e.items() if k != "id"}
                             for e in data["epics"]]
        return real_dump(data, **kwargs)

    monkeypatch.setattr(store.yaml, "dump", lossy)

    with pytest.raises(store.AdoptionRoundTripError) as caught:
        store.open_store(backlog_path=bp, session="backlog-round-trip-refused")

    assert "backlog.yaml" in str(caught.value), str(caught.value)
    assert bp.read_text(encoding="utf-8") == baseline
    store.reset_for_tests()


def test_an_already_v4_adoption_still_verifies_backlog_yaml(tmp_path, monkeypatch):
    """A project that already reads as v4 takes the `_export_backlog(force=True)`
    branch and writes *only* `backlog.yaml`, so a check that covered entity
    files alone verified nothing at all on that path."""
    import pytest  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 4, "project": "t",
        "meta": {"updated": "", "schema_version": 4},
        "epics": [{"id": "e", "name": "E", "status": "active"}],
        "phases": [],
    })
    v3.write_task_file(
        v3.task_file_path(bp, "e-001"),
        {"id": "e-001", "title": "T", "epic": "e", "status": "todo", "order": 1.0},
        "body",
    )
    real_dump = store.yaml.dump

    def lossy(data, **kwargs):
        if isinstance(data, dict) and "epics" in data:
            data = dict(data)
            data["epics"] = [{k: v for k, v in e.items() if k != "id"}
                             for e in data["epics"]]
        return real_dump(data, **kwargs)

    monkeypatch.setattr(store.yaml, "dump", lossy)

    with pytest.raises(store.AdoptionRoundTripError):
        store.open_store(backlog_path=bp, session="v4-backlog-round-trip")

    store.reset_for_tests()


def test_adoption_refuses_an_unparseable_ideas_index(tmp_path, monkeypatch):
    """`ideas/IDEAS.md` is derived output the scan never re-parses, but it is
    written on the same adoption path and a render that cannot be read back is
    still a file the user is handed."""
    import pytest  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.idea_path(bp, "IDEA-001"),
        {"id": "IDEA-001", "title": "An idea", "status": "raw"},
        "body",
    )
    monkeypatch.setattr(store, "render_ideas_index", lambda entries: "\x00 not text")

    with pytest.raises(store.AdoptionRoundTripError) as caught:
        store.open_store(backlog_path=bp, session="ideas-round-trip-refused")

    assert "IDEAS.md" in str(caught.value), str(caught.value)
    store.reset_for_tests()


# ── the fill that recovers an orphan must not touch the rest ─────────────────


def test_a_cold_reopen_of_an_adopted_project_changes_nothing(tmp_path):
    """The V1 fill ran on *every* epic and phase import, not just the orphan
    case. `_split_entity_for_v3` mirrors a readability `title` into the heavy
    file and `_merge_entity_from_v3` deliberately ignores it coming back; the
    fill absorbed it into the row, and `title` is not a heavy field, so the
    exporter wrote it into `backlog.yaml`."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active",
                   "description": "d", "tasks": []}],
        "phases": [{"id": "p", "name": "P One", "order": 1, "description": "pd"}],
        "context": {},
    })
    store.open_store(backlog_path=bp, session="first-open").load_dict()
    after_first = yaml.safe_load(bp.read_text(encoding="utf-8"))

    _reopen_cold(bp).load_dict()

    reopened = yaml.safe_load(bp.read_text(encoding="utf-8"))
    assert reopened["epics"] == after_first["epics"], reopened["epics"]
    assert reopened["phases"] == after_first["phases"], reopened["phases"]
    assert "title" not in reopened["epics"][0], reopened["epics"][0]
    assert "title" not in reopened["phases"][0], reopened["phases"][0]
    store.reset_for_tests()


def test_renaming_an_epic_leaves_no_stale_display_field(tmp_path):
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active",
                   "description": "d", "tasks": []}],
        "phases": [], "context": {},
    })
    store.open_store(backlog_path=bp, session="rename-open")
    with store.transaction(backlog_path=bp, tool="test:rename") as tx:
        doc = tx.get("epic", "e")
        doc["name"] = "Renamed Epic"
        tx.put("epic", "e", doc)

    projected = yaml.safe_load(bp.read_text(encoding="utf-8"))
    epic = projected["epics"][0]
    assert epic["name"] == "Renamed Epic", epic
    assert "title" not in epic, epic
    store.reset_for_tests()


def test_an_orphan_with_only_a_title_recovers_it_as_its_name(tmp_path):
    """The mirror in an orphan's file is the only copy of its display name, so
    it is recovered — as `name`, the field every reader uses, not as a second
    field that then drifts."""
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [], "phases": [], "context": {},
    })
    v3.write_task_file(
        v3.epic_file_path(bp, "orphan"),
        {"id": "orphan", "title": "The Orphan", "description": "heavy"},
        "body",
    )

    loaded = store.open_store(backlog_path=bp, session="orphan-name").load_dict()

    epic = {e["id"]: e for e in loaded["epics"]}["orphan"]
    assert epic["name"] == "The Orphan", epic
    assert epic["description"] == "heavy"
    store.reset_for_tests()


def test_a_refused_adoption_leaves_no_store_behind(tmp_path, monkeypatch):
    """`open_store` creates and commits the schema before bootstrap runs, so a
    refusal left an empty-but-valid `store.db`. Both hooks read an existing
    store as the authority — the merge gate fails *open* on one it cannot use
    rather than reading the projection — so the leftover turned a refused
    adoption into a silently disabled gate."""
    import pytest  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active", "tasks": [
            {"id": "e-001", "title": "Fine", "status": "todo", "notes": "n"},
        ]}],
        "phases": [], "context": {},
    })
    real_render = store.render_frontmatter

    def lossy(frontmatter, body):
        if frontmatter.get("id") == "e-001":
            frontmatter = {k: v for k, v in frontmatter.items() if k != "notes"}
        return real_render(frontmatter, body)

    monkeypatch.setattr(store, "render_frontmatter", lossy)

    with pytest.raises(store.AdoptionRoundTripError):
        store.open_store(backlog_path=bp, session="refused-leaves-no-store")

    local = bp.parent / "local"
    leftovers = sorted(p.name for p in local.glob("store.db*")) if local.exists() else []
    assert leftovers == [], leftovers
    store.reset_for_tests()


def test_the_merge_gate_reads_the_files_after_a_refused_adoption(tmp_path, monkeypatch):
    """The consequence, end to end: with no store the gate falls back to the
    projection and can still block, instead of failing open on an empty one."""
    import importlib.util  # noqa: PLC0415
    import pytest  # noqa: PLC0415
    import textwrap  # noqa: PLC0415

    root = tmp_path
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E", "status": "active",
                   "description": "heavy", "tasks": [
                       {"id": "e-001", "title": "Fine", "status": "in-progress",
                        "branch": "feature/x"},
                   ]}],
        "phases": [], "context": {},
    })
    (bp.parent / "project.yaml").write_text(textwrap.dedent("""\
        schema_version: 1
        meta: {name: T, slug: t, kind: app}
        conventions:
          policies:
            review_gate_required_for_merge: true
    """), encoding="utf-8")
    # A complete task file with no `gates`, so the projection reader reaches its
    # confident "no review-gate -> BLOCK" answer rather than failing open for
    # want of a file.
    v3.write_task_file(
        v3.task_file_path(bp, "e-001"),
        {"id": "e-001", "title": "Fine", "epic": "e", "status": "in-progress",
         "branch": "feature/x", "order": 1.0},
        "body",
    )
    real_render = store.render_frontmatter

    def lossy(frontmatter, body):
        if frontmatter.get("id") == "e":
            frontmatter = {k: v for k, v in frontmatter.items() if k != "description"}
        return real_render(frontmatter, body)

    monkeypatch.setattr(store, "render_frontmatter", lossy)
    with pytest.raises(store.AdoptionRoundTripError):
        store.open_store(backlog_path=bp, session="refused-then-gate")
    monkeypatch.undo()
    monkeypatch.setenv("TASKMASTER_ROOT", str(root))

    decide_path = (
        __import__("pathlib").Path(store.__file__).resolve().parents[1]
        / "hooks" / "merge_gate_decide.py"
    )
    spec = importlib.util.spec_from_file_location("merge_gate_decide_r2", decide_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.decide("feature/x", root).startswith("BLOCK:e-001:")
    log = (bp.parent / "local" / "hook.log").read_text(encoding="utf-8")
    assert "no store" in log, log
    store.reset_for_tests()


def test_an_ordinary_adoption_passes_the_round_trip_check(tmp_path):
    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "e", "name": "E: colon, and #hash", "status": "active",
                   "description": "heavy", "tasks": [
                       {"id": "e-001", "title": "A 'quoted' title",
                        "status": "todo", "notes": "line one\nline two"},
                   ]}],
        "phases": [{"id": "p", "name": "P", "order": 1, "description": "d"}],
        "context": {},
    })

    loaded = store.open_store(backlog_path=bp, session="round-trip-ok").load_dict()

    task = loaded["epics"][0]["tasks"][0]
    assert task["title"] == "A 'quoted' title"
    assert task["notes"] == "line one\nline two"
    assert loaded["epics"][0]["name"] == "E: colon, and #hash"
    store.reset_for_tests()


# ── the dashboard survives an epic with no name ──────────────────────────────


def test_backlog_status_survives_an_epic_without_a_name(tmp_path, monkeypatch):
    """`backlog_status` is the first call of every session. It indexed
    `epic["name"]` unguarded while every neighbouring access used `.get`, so one
    nameless epic took the whole dashboard down with a KeyError."""
    from taskmaster import backlog_server as bs  # noqa: PLC0415

    bp = _seed(tmp_path, {
        "version": 3, "project": "t",
        "meta": {"updated": "", "schema_version": 3},
        "epics": [{"id": "nameless", "status": "active", "tasks": [
            {"id": "nameless-001", "status": "in-progress"},
        ]}],
        "phases": [], "context": {},
    })
    monkeypatch.setenv("TASKMASTER_ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    store.open_store(backlog_path=bp, session="status-nameless-epic")

    out = bs.backlog_status()

    assert "Error" not in out, out
    assert "nameless" in out, out
    store.reset_for_tests()
