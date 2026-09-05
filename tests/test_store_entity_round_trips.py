# User intent: prove step 3 actually moved every non-task entity writer onto the
# store — each kind's create/update/archive has to leave a committed row AND the
# matching exported markdown, with no writer touching the file directly.
"""Create / update / archive round trips for every non-task entity kind.

Each test asserts both halves of the contract the migration exists to enforce:
the store row is the authority, and the markdown file is its projection. A
writer that skipped the store would fail the row assertion; one that skipped the
export would fail the file assertion.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster import taskmaster_v3 as tm


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


def _row(root: Path, kind: str, ident: str):
    """The committed `(doc, body)` for one entity, read from a fresh store."""
    store.reset_for_tests()
    rows = (store.load_dict(_bp(root)).get("_rows") or {}).get(kind) or {}
    return rows.get(ident)


def _archived(root: Path, kind: str, ident: str) -> bool:
    row = _row(root, kind, ident)
    return bool(row and row[0].get("archived"))


# ── bug ──────────────────────────────────────────────────────────────────


def test_bug_create_update_archive_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    out = bs.backlog_bug_create(title="Login loops", severity="P2", body="Repro here.")
    assert "B-001" in out, out

    doc, body = _row(root, "bug", "B-001")
    assert doc["title"] == "Login loops"
    assert body.strip() == "Repro here."
    live = tm.bug_path(_bp(root), "B-001")
    assert live.exists() and "Login loops" in live.read_text(encoding="utf-8")

    bs.backlog_bug_update("B-001", "fix_commit", "abc123")
    assert "Error" not in bs.backlog_bug_update("B-001", "status", "fixed")
    assert _row(root, "bug", "B-001")[0]["status"] == "fixed"

    assert "archived" in bs.backlog_bug_archive("B-001")
    assert _archived(root, "bug", "B-001")
    assert not tm.bug_path(_bp(root), "B-001").exists()
    assert tm.bug_path(_bp(root), "B-001", archived=True).exists()


def test_bug_archive_refuses_a_non_terminal_bug(tmp_taskmaster):
    bs.backlog_bug_create(title="Still open")
    assert "Error" in bs.backlog_bug_archive("B-001")
    assert not _archived(tmp_taskmaster, "bug", "B-001")


# ── issue ────────────────────────────────────────────────────────────────


def test_issue_create_and_update_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    out = bs.backlog_issue_create(
        title="Flaky import", severity="P1", evidence="Third recurrence.", body="Notes."
    )
    assert "ISS-001" in out, out

    doc, body = _row(root, "issue", "ISS-001")
    assert doc["severity"] == "P1"
    assert body.strip() == "Notes."
    assert tm.issue_path(_bp(root), "ISS-001").exists()

    bs.backlog_issue_update("ISS-001", "impact", "Blocks releases")
    doc, _ = _row(root, "issue", "ISS-001")
    assert doc["impact"] == "Blocks releases"
    assert "Blocks releases" in tm.issue_path(_bp(root), "ISS-001").read_text(
        encoding="utf-8"
    )


def test_bug_promote_creates_the_issue_and_flips_the_bugs_together(tmp_taskmaster):
    root = tmp_taskmaster
    bs.backlog_bug_create(title="Alpha crash")
    bs.backlog_bug_create(title="Beta crash")
    out = bs.backlog_bug_promote(
        bug_ids=["B-001", "B-002"],
        title="Crash cluster",
        severity="P1",
        evidence_text="Two independent reports.",
    )
    assert "ISS-001" in out, out

    issue, _ = _row(root, "issue", "ISS-001")
    assert issue["promoted_from"] == ["B-001", "B-002"]
    for bid in ("B-001", "B-002"):
        doc, _ = _row(root, "bug", bid)
        assert (doc["status"], doc["promoted_to"]) == ("promoted", "ISS-001")


# ── handover ─────────────────────────────────────────────────────────────


def test_handover_create_supersede_and_status_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    first = bs.backlog_handover_create(tldr="Shipped the parser", body="Body one.")
    assert "Handover written" in first, first
    old_id = first.splitlines()[0].split(": ", 1)[1]

    doc, body = _row(root, "handover", old_id)
    assert doc["tldr"] == "Shipped the parser"
    assert body.strip() == "Body one."
    assert tm.handover_path(_bp(root), old_id).exists()

    second = bs.backlog_handover_create(tldr="Wired the exporter", supersedes=old_id)
    new_id = second.splitlines()[0].split(": ", 1)[1]
    doc, body = _row(root, "handover", old_id)
    assert doc["superseded_by"] == new_id
    assert doc["status"] == "superseded"
    assert body.lstrip().startswith("> **SUPERSEDED")

    bs.backlog_handover_update_status(new_id, "closed", reason="done for now")
    doc, _ = _row(root, "handover", new_id)
    assert (doc["status"], doc["status_user_set"]) == ("closed", True)
    assert "status: closed" in tm.handover_path(_bp(root), new_id).read_text(
        encoding="utf-8"
    )


def test_handover_ids_suffix_instead_of_clobbering(tmp_taskmaster):
    first = bs.backlog_handover_create(tldr="Same day same words")
    second = bs.backlog_handover_create(tldr="Same day same words")
    first_id = first.splitlines()[0].split(": ", 1)[1]
    second_id = second.splitlines()[0].split(": ", 1)[1]
    assert second_id == f"{first_id}-2"
    assert _row(tmp_taskmaster, "handover", first_id) is not None


# ── decision ─────────────────────────────────────────────────────────────


def test_decision_create_update_resolve_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    out = bs.backlog_decision_create(
        title="Pick a queue", options=["sqlite", "json"], body="Context."
    )
    assert "DEC-001" in out, out

    doc, body = _row(root, "decision", "DEC-001")
    assert doc["options"] == ["sqlite", "json"]
    assert body.strip() == "Context."
    assert tm.decision_path(_bp(root), "DEC-001").exists()

    bs.backlog_decision(action="update", decision_id="DEC-001", title="Pick the queue")
    assert _row(root, "decision", "DEC-001")[0]["title"] == "Pick the queue"

    bs.backlog_decision(
        action="resolve", decision_id="DEC-001", resolved_with=1, rationale="durable"
    )
    doc, _ = _row(root, "decision", "DEC-001")
    assert (doc["status"], doc["resolved_with"]) == ("resolved", 1)
    assert "status: resolved" in tm.decision_path(_bp(root), "DEC-001").read_text(
        encoding="utf-8"
    )


def test_decision_drop_records_the_reason(tmp_taskmaster):
    bs.backlog_decision_create(title="Pick a lane", options=["a", "b"])
    bs.backlog_decision(action="drop", decision_id="DEC-001", reason="no longer live")
    doc, _ = _row(tmp_taskmaster, "decision", "DEC-001")
    assert (doc["status"], doc["dropped_reason"]) == ("dropped", "no longer live")


# ── idea ─────────────────────────────────────────────────────────────────


def test_idea_create_update_archive_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    out = bs.backlog_idea_create(title="Batch the exports", body="Sketch.")
    assert "IDEA-001" in out, out

    doc, body = _row(root, "idea", "IDEA-001")
    assert doc["title"] == "Batch the exports"
    assert body.strip() == "Sketch."
    assert tm.idea_path(_bp(root), "IDEA-001").exists()

    bs.backlog_idea_update("IDEA-001", "status", "candidate")
    assert _row(root, "idea", "IDEA-001")[0]["status"] == "candidate"

    bs.backlog_idea_update("IDEA-001", "archived", "true")
    assert _archived(root, "idea", "IDEA-001")
    # Ideas do not move on archive; the flag is what the list filters on.
    assert tm.idea_path(_bp(root), "IDEA-001").exists()
    assert "IDEA-001" not in bs.backlog_idea_list()


# ── note ─────────────────────────────────────────────────────────────────


def test_note_create_update_archive_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    assert "NOTE-001" in bs.backlog_note(action="create", text="remember the seq")

    doc, body = _row(root, "note", "NOTE-001")
    assert doc["author"] == "claude"
    assert body.strip() == "remember the seq"
    assert tm.note_path(_bp(root), "NOTE-001").exists()

    bs.backlog_note(action="update", note_id="NOTE-001", text="remember the rev", pinned=True)
    doc, body = _row(root, "note", "NOTE-001")
    assert doc["pinned"] is True
    assert body.strip() == "remember the rev"

    bs.backlog_note(action="archive", note_id="NOTE-001")
    assert _archived(root, "note", "NOTE-001")
    assert not tm.note_path(_bp(root), "NOTE-001").exists()
    assert tm.note_path(_bp(root), "NOTE-001", archived=True).exists()


# ── area ─────────────────────────────────────────────────────────────────


def test_area_create_and_update_round_trip(tmp_taskmaster):
    root = tmp_taskmaster
    out = bs.backlog_area_create("viewer", "Viewer", description="The dashboard")
    assert "Area created" in out, out

    doc, _ = _row(root, "area", "viewer")
    assert doc["name"] == "Viewer"
    assert tm.area_path(_bp(root), "viewer").exists()

    bs.backlog_area_update("viewer", "anchors", '["viewer/**"]')
    doc, _ = _row(root, "area", "viewer")
    assert doc["anchors"] == ["viewer/**"]
    assert "viewer/**" in tm.area_path(_bp(root), "viewer").read_text(encoding="utf-8")


def test_area_create_refuses_a_duplicate_id(tmp_taskmaster):
    bs.backlog_area_create("viewer", "Viewer")
    assert "already exists" in bs.backlog_area_create("viewer", "Viewer again")


# ── tracker ──────────────────────────────────────────────────────────────


def test_tracker_link_round_trip(tm_epic_phase):
    import json

    root = tm_epic_phase
    bs.backlog_add_task(title="Sync me", epic="test-epic", phase="dev", tldr="sync")
    (root / ".taskmaster" / "linear.yaml").write_text(
        "default_workspace: cm\n"
        "workspaces:\n"
        "  - alias: cm\n"
        "    team_id: team-uuid\n"
        "    token_env: TASKMASTER_LINEAR_TOKEN_CM\n",
        encoding="utf-8",
    )
    result = json.loads(bs.backlog_linear_link("test-epic-001", "ENG-7"))
    assert result.get("ok"), result
    tracker_id = result["tracker_id"]

    doc, _ = _row(root, "tracker", tracker_id)
    assert doc["external_key"] == "ENG-7"
    assert tm.tracker_path(_bp(root), tracker_id).exists()

    store.reset_for_tests()
    trackers = store.load_dict(_bp(root)).get("trackers") or []
    assert [entry["id"] for entry in trackers] == [tracker_id]


# ── read tools take the rows, not the files ──────────────────────────────


def _forbid_reads_under(monkeypatch, directory: str) -> None:
    """Make any `Path.read_text` under `<backlog>/<directory>/` fail loudly."""
    real = Path.read_text

    def guarded(self, *args, **kwargs):
        if f"{os.sep}{directory}{os.sep}" in str(self):
            raise AssertionError(f"read the projection file {self} instead of the row")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)


def test_handover_get_reads_rows_for_live_and_archived_handovers(
    tmp_taskmaster, monkeypatch
):
    """Both a live handover and one archived by index overflow come from rows."""
    root = tmp_taskmaster
    ids = [
        bs.backlog_handover_create(tldr=f"entry {index:02d}")
        .splitlines()[0]
        .split(": ", 1)[1]
        for index in range(1, 33)
    ]
    oldest, newest = ids[0], ids[-1]

    # 32 handovers against a cap of 30, so the two oldest were archived.
    assert _archived(root, "handover", oldest), "the overflow was never archived"
    assert not _archived(root, "handover", newest)
    year = oldest[:4]
    assert (tm.handover_dir(_bp(root)) / "_archive" / year / f"{oldest}.md").exists()

    store.reset_for_tests()
    _forbid_reads_under(monkeypatch, "handovers")
    live = bs.backlog_handover_get(newest)
    archived = bs.backlog_handover_get(oldest)
    assert "entry 32" in live, live
    assert "entry 01" in archived, archived
    assert bs.backlog_handover_get("nope-not-here") == "Handover not found: nope-not-here"


def test_viewer_idea_post_uses_one_root_when_cwd_and_project_root_diverge(
    tmp_taskmaster, tmp_path, monkeypatch
):
    """The idea POST resolves the project root only, never the cwd separately.

    The handler used to open its transaction on the cwd-derived artifact root
    while the create loaded through the project root; when the two diverge that
    is a RuntimeError out of `_mutate_and_save`, not an idea.
    """
    root = tmp_taskmaster
    decoy = tmp_path.parent / "decoy-cwd"
    (decoy / ".taskmaster").mkdir(parents=True, exist_ok=True)
    (decoy / ".taskmaster" / "backlog.yaml").write_text(
        "meta:\n  schema_version: 3\nepics: []\nphases: []\n", encoding="utf-8"
    )
    monkeypatch.chdir(decoy)
    from taskmaster.taskmaster_v3 import _resolve_artifact_root

    assert _resolve_artifact_root() != _bp(root).parent, "the roots did not diverge"

    handler = bs.ViewerHandler.__new__(bs.ViewerHandler)
    handler.path = "/api/ideas"
    payload = json.dumps({"title": "From the viewer", "created_by": "user"}).encode("utf-8")
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    sent: dict = {}
    handler._send_json = lambda code, body, **kw: sent.update(code=code, body=body)

    handler.do_POST()

    assert sent["code"] == 201, sent
    iid = sent["body"]["id"]
    doc, _body = _row(root, "idea", iid)
    assert doc["title"] == "From the viewer"
    assert doc["created_by"] == "user"
    assert Path(sent["body"]["path"]) == tm.idea_path(_bp(root), iid)
    assert not (decoy / ".taskmaster" / "ideas").exists(), "wrote into the cwd root"


# ── the one-shot handover backfill ───────────────────────────────────────


def _backfill_changes(root: Path) -> int:
    """How many committed changes the one-shot backfill has ever written."""
    store.reset_for_tests()
    instance = store.open_store(_bp(root))
    return int(
        instance.connection.execute(
            "SELECT COUNT(*) FROM changes WHERE tool=?",
            ("_ensure_handover_status_backfilled",),
        ).fetchone()[0]
    )


def test_backfilled_project_never_opens_a_writer_on_a_handover_read(tmp_taskmaster):
    """Once the marker is durable, list and get take no writer lock of their own."""
    root = tmp_taskmaster
    # The flag is a module global that survives between tests; clear it so the
    # seeding create really commits the durable marker.
    bs._HANDOVER_STATUS_BACKFILL_RAN = False
    bs.backlog_handover_create(tldr="seed the marker")
    before = _backfill_changes(root)

    # Clearing the in-process flag is what an MCP server restart looks like:
    # the read must answer from the durable marker, not re-open BEGIN IMMEDIATE.
    for _ in range(2):
        bs._HANDOVER_STATUS_BACKFILL_RAN = False
        store.reset_for_tests()
        bs.backlog_handover_list()
        assert bs._HANDOVER_STATUS_BACKFILL_RAN is True, "the read did not latch the flag"
    assert _backfill_changes(root) == before, "a read opened a backfill transaction"


def test_a_rolled_back_join_leaves_the_backfill_flag_unset(tmp_taskmaster):
    """A nested backfill riding a doomed transaction must not latch the flag."""
    root = tmp_taskmaster
    bs.backlog_handover_create(tldr="seed")
    # Clear the durable marker so the backfill has real work to plan.
    with bs._transaction(tool="test:clear-marker") as data:
        data.pop("handover_status_backfilled", None)
        bs._mutate_and_save(data)
    bs._HANDOVER_STATUS_BACKFILL_RAN = False
    store.reset_for_tests()

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with bs._transaction(tool="test:doomed") as data:
            bs._ensure_handover_status_backfilled()
            raise Rollback

    assert bs._HANDOVER_STATUS_BACKFILL_RAN is False, (
        "the flag latched on a transaction that rolled back"
    )
    store.reset_for_tests()
    assert not store.load_dict(_bp(root)).get("handover_status_backfilled")
    # The next call owns its transaction, so it both backfills and latches.
    bs._ensure_handover_status_backfilled()
    assert bs._HANDOVER_STATUS_BACKFILL_RAN is True


# ── no writer outside the store ──────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "write_bug", "update_bug", "archive_bug", "promote_bugs_to_issue",
        "write_issue", "update_issue",
        "write_handover", "apply_supersession", "apply_handover_review_flag",
        "update_handover_status", "archive_handover",
        "write_decision", "update_decision", "resolve_decision", "drop_decision",
        "link_decision_to_handover",
        "write_idea", "update_idea", "_write_ideas_index",
        "write_note", "update_note", "archive_note",
        "write_area", "update_area",
        "write_tracker", "update_tracker",
        "next_bug_id", "next_issue_id", "next_decision_id", "next_idea_id",
        "next_note_id",
    ],
)
def test_taskmaster_v3_no_longer_exposes_the_deleted_writers(name):
    """`taskmaster_v3` keeps parse/render/validate helpers and nothing that writes."""
    assert not hasattr(tm, name), f"{name} survived the step-3 deletion"
