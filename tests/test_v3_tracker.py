"""Tests for the v3 tracker entity (Jira ↔ Taskmaster integration foundation)."""
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster_v3 import (  # noqa: E402
    EXTERNAL_SYSTEMS,
    _CANONICALIZE_ITEMS,
    _ISSUE_INDEX_FIELDS,
    linked_issues_for_tracker,
    linked_tasks_for_tracker,
    list_tracker_ids,
    make_tracker_id,
    read_tracker,
    sync_tracker_index,
    tracker_dir,
    tracker_path,
    update_tracker,
    write_tracker,
)


# ── Fixtures ───────────────────────────────────────────────────


def _make_backlog(tmp_path: Path) -> Path:
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    return bp


# ── ID generation ──────────────────────────────────────────────


def test_make_tracker_id_deterministic():
    assert make_tracker_id("jira", "codemaestro", "CM-101") == "jira-codemaestro-cm-101"


def test_make_tracker_id_lowercases_all_components():
    # All three components are lowercased so re-pulling the same key is idempotent
    # regardless of how Jira/the user capitalizes things.
    assert make_tracker_id("JIRA", "CodeMaestro", "cm-101") == "jira-codemaestro-cm-101"


def test_make_tracker_id_strips_whitespace():
    assert make_tracker_id("  jira  ", " cm ", " CM-9 ") == "jira-cm-cm-9"


@pytest.mark.parametrize(
    "system,alias,key",
    [
        ("", "cm", "CM-1"),
        ("jira", "", "CM-1"),
        ("jira", "cm", ""),
        (None, "cm", "CM-1"),
    ],
)
def test_make_tracker_id_rejects_missing_components(system, alias, key):
    with pytest.raises(ValueError):
        make_tracker_id(system, alias, key)


# ── Write + read round-trip ────────────────────────────────────


def test_write_then_read_roundtrip(tmp_path):
    bp = _make_backlog(tmp_path)
    tid, path = write_tracker(
        bp,
        external_system="jira",
        instance_alias="codemaestro",
        external_key="CM-101",
        title="Welcome flow polish — magic-button styling",
        status="In Progress",
        assignee="Volodymyr Demchenko",
        url="https://codemaestro.atlassian.net/browse/CM-101",
        last_synced="2026-05-07T14:22:00Z",
        synced_hash="abc123",
        body="Description body here.\n\nMore prose.",
    )

    assert tid == "jira-codemaestro-cm-101"
    assert path == tracker_path(bp, tid)
    assert path.exists()

    fm, body = read_tracker(bp, tid)
    assert fm["id"] == "jira-codemaestro-cm-101"
    assert fm["external_system"] == "jira"
    assert fm["external_key"] == "CM-101"
    assert fm["instance_alias"] == "codemaestro"
    assert fm["title"] == "Welcome flow polish — magic-button styling"
    assert fm["status"] == "In Progress"
    assert fm["assignee"] == "Volodymyr Demchenko"
    assert fm["url"] == "https://codemaestro.atlassian.net/browse/CM-101"
    assert fm["last_synced"] == "2026-05-07T14:22:00Z"
    assert fm["synced_hash"] == "abc123"
    assert "Description body here." in body


def test_write_tracker_is_upsert_not_append(tmp_path):
    """Re-writing the same external triple overwrites, never duplicates."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="Initial title",
        status="To Do",
    )
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="Updated title",
        status="In Progress",
    )

    ids = list_tracker_ids(bp)
    assert ids == ["jira-cm-cm-1"]
    fm, _ = read_tracker(bp, "jira-cm-cm-1")
    assert fm["title"] == "Updated title"
    assert fm["status"] == "In Progress"


def test_write_tracker_rejects_empty_title(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="title is required"):
        write_tracker(
            bp,
            external_system="jira",
            instance_alias="cm",
            external_key="CM-1",
            title="   ",
            status="To Do",
        )


def test_write_tracker_rejects_unknown_system(tmp_path):
    bp = _make_backlog(tmp_path)
    with pytest.raises(ValueError, match="external_system"):
        write_tracker(
            bp,
            external_system="github",  # not in EXTERNAL_SYSTEMS yet
            instance_alias="cm",
            external_key="GH-1",
            title="x",
            status="open",
        )


# ── Validator ──────────────────────────────────────────────────


def test_validator_rejects_id_that_does_not_match_components(tmp_path):
    """The id must equal make_tracker_id(system, alias, key)."""
    bp = _make_backlog(tmp_path)
    # Manually craft a tracker file with a mismatched id.
    tracker_dir(bp).mkdir(parents=True, exist_ok=True)
    bad_path = tracker_dir(bp) / "jira-cm-cm-1.md"
    bad_path.write_text(
        "---\n"
        "id: jira-cm-cm-1\n"
        "external_system: jira\n"
        "instance_alias: cm\n"
        "external_key: CM-2\n"  # mismatch — id says CM-1
        "title: Hi\n"
        "status: To Do\n"
        "---\n",
        encoding="utf-8",
    )
    # Read returns the file; explicit validation should catch the mismatch.
    fm, _ = read_tracker(bp, "jira-cm-cm-1")
    from taskmaster_v3 import _validate_tracker
    with pytest.raises(ValueError, match="deterministic format"):
        _validate_tracker(fm)


def test_validator_rejects_missing_required_fields(tmp_path):
    from taskmaster_v3 import _validate_tracker
    with pytest.raises(ValueError, match="missing required field"):
        _validate_tracker({"id": "jira-cm-cm-1", "external_system": "jira"})


def test_external_systems_constant_includes_jira():
    assert "jira" in EXTERNAL_SYSTEMS


# ── update_tracker ─────────────────────────────────────────────


def test_update_tracker_preserves_immutable_fields(tmp_path):
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="Initial",
        status="To Do",
    )
    # Try to mutate id-derivative fields — they should be silently ignored.
    fm, _ = update_tracker(
        bp,
        "jira-cm-cm-1",
        external_system="github",
        external_key="GH-99",
        instance_alias="other",
        title="Renamed",
        status="In Progress",
    )
    assert fm["external_system"] == "jira"
    assert fm["external_key"] == "CM-1"
    assert fm["instance_alias"] == "cm"
    assert fm["title"] == "Renamed"
    assert fm["status"] == "In Progress"
    assert fm["id"] == "jira-cm-cm-1"


def test_update_tracker_preserves_body_when_not_passed(tmp_path):
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="t",
        status="s",
        body="Original body.",
    )
    update_tracker(bp, "jira-cm-cm-1", title="New title")
    _, body = read_tracker(bp, "jira-cm-cm-1")
    assert "Original body." in body


def test_update_tracker_replaces_body_when_passed(tmp_path):
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="t",
        status="s",
        body="Original body.",
    )
    update_tracker(bp, "jira-cm-cm-1", body="Refreshed body.")
    _, body = read_tracker(bp, "jira-cm-cm-1")
    assert "Refreshed body." in body
    assert "Original body." not in body


# ── Index sync ─────────────────────────────────────────────────


def test_sync_tracker_index_rebuilds_from_disk(tmp_path):
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-2",
        title="Beta",
        status="To Do",
        url="https://example/CM-2",
        last_synced="2026-05-07T10:00:00Z",
    )
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="Alpha",
        status="In Progress",
        url="https://example/CM-1",
        last_synced="2026-05-07T11:00:00Z",
    )

    backlog: dict = {"epics": []}
    sync_tracker_index(backlog, bp)

    assert "trackers" in backlog
    ids = [e["id"] for e in backlog["trackers"]]
    assert ids == ["jira-cm-cm-1", "jira-cm-cm-2"]  # sorted

    cm1 = backlog["trackers"][0]
    assert cm1["title"] == "Alpha"
    assert cm1["status"] == "In Progress"
    assert cm1["url"] == "https://example/CM-1"
    # synced_hash and assignee not in slim index
    assert "synced_hash" not in cm1
    assert "assignee" not in cm1


def test_sync_tracker_index_handles_missing_dir(tmp_path):
    bp = _make_backlog(tmp_path)
    # No trackers dir at all — sync should just produce empty list, not raise.
    backlog: dict = {"epics": []}
    sync_tracker_index(backlog, bp)
    assert backlog["trackers"] == []


# ── Reverse-map derivation ─────────────────────────────────────


def test_linked_tasks_for_tracker_derives_from_index(tmp_path):
    backlog = {
        "epics": [
            {
                "id": "auth",
                "tasks": [
                    {"id": "auth-001", "tracker_id": "jira-cm-cm-1"},
                    {"id": "auth-002"},  # no tracker
                    {"id": "auth-003", "tracker_id": "jira-cm-cm-1"},
                    {"id": "auth-004", "tracker_id": "jira-cm-cm-9"},
                ],
            }
        ]
    }
    linked = linked_tasks_for_tracker(backlog, "jira-cm-cm-1")
    assert [t["id"] for t in linked] == ["auth-001", "auth-003"]


def test_linked_issues_for_tracker_derives_from_index():
    backlog = {
        "issues": [
            {"id": "ISS-001", "tracker_id": "jira-cm-cm-1"},
            {"id": "ISS-002"},
            {"id": "ISS-003", "tracker_id": "jira-cm-cm-1"},
        ]
    }
    linked = linked_issues_for_tracker(backlog, "jira-cm-cm-1")
    assert [i["id"] for i in linked] == ["ISS-001", "ISS-003"]


# ── Layout integration ─────────────────────────────────────────


def test_canonicalize_includes_trackers():
    """Future canonicalize_layout runs should know to relocate .taskmaster/trackers/."""
    assert "trackers" in _CANONICALIZE_ITEMS


def test_issue_index_now_carries_tracker_id():
    """Issues that link a tracker have that link mirrored in the slim index."""
    assert "tracker_id" in _ISSUE_INDEX_FIELDS


# ── Issue ↔ tracker linkage round-trip ─────────────────────────


def test_write_issue_with_tracker_id_persists(tmp_path):
    """Issues accept a tracker_id and round-trip it through write/read."""
    from taskmaster_v3 import write_issue, read_issue
    bp = _make_backlog(tmp_path)
    (bp.parent / "issues").mkdir(parents=True, exist_ok=True)

    iid, _ = write_issue(
        bp,
        title="Login accepts whitespace password",
        severity="P1",
        impact="Users can authenticate with a password that is only whitespace",
        tracker_id="jira-cm-cm-101",
    )
    fm, _ = read_issue(bp, iid)
    assert fm["tracker_id"] == "jira-cm-cm-101"


# ── Edge-case hardening (post-review) ──────────────────────────


def test_sync_tracker_index_skips_malformed_yaml(tmp_path):
    """A corrupt tracker file shouldn't crash sync — it should be skipped."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="Good",
        status="To Do",
    )
    # Drop a syntactically broken tracker file alongside the good one.
    bad = tracker_dir(bp) / "jira-cm-cm-2.md"
    bad.write_text("---\nid: jira-cm-cm-2\nbroken yaml: [unclosed\n---\n", encoding="utf-8")

    backlog: dict = {"epics": []}
    sync_tracker_index(backlog, bp)
    # Good entry survived, bad one was skipped without raising.
    ids = [e["id"] for e in backlog["trackers"]]
    assert ids == ["jira-cm-cm-1"]


def test_update_tracker_can_clear_nullable_field_with_explicit_none(tmp_path):
    """Passing assignee=None must clear it (the post-review fix to the
    'silent drop None' bug). Required fields would still be caught by the
    validator, so accidentally Noning a required field fails loudly."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="t",
        status="To Do",
        assignee="Alice",
        url="https://example/CM-1",
    )
    fm, _ = update_tracker(bp, "jira-cm-cm-1", assignee=None, url=None)
    assert fm["assignee"] is None
    assert fm["url"] is None


def test_update_tracker_omitted_field_is_left_untouched(tmp_path):
    """Confirm 'omit' semantics: not passing a field leaves it as-is."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp,
        external_system="jira",
        instance_alias="cm",
        external_key="CM-1",
        title="t",
        status="To Do",
        assignee="Alice",
    )
    update_tracker(bp, "jira-cm-cm-1", status="Done")  # assignee not passed
    fm, _ = read_tracker(bp, "jira-cm-cm-1")
    assert fm["assignee"] == "Alice"
    assert fm["status"] == "Done"


def test_update_tracker_rejects_id_mismatch(tmp_path):
    """If a caller passes a tracker_id that doesn't match the file's
    frontmatter id, raise rather than write to a ghost path."""
    bp = _make_backlog(tmp_path)
    # Manually craft a file whose path stem disagrees with its frontmatter id.
    tracker_dir(bp).mkdir(parents=True, exist_ok=True)
    rogue = tracker_dir(bp) / "jira-cm-cm-1.md"
    rogue.write_text(
        "---\n"
        "id: jira-cm-cm-99\n"  # mismatches the path stem
        "external_system: jira\n"
        "instance_alias: cm\n"
        "external_key: CM-99\n"
        "title: Hi\n"
        "status: To Do\n"
        "---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match requested"):
        update_tracker(bp, "jira-cm-cm-1", status="Done")


# ── Push-dominant systems (linear-001) ─────────────────────────


def test_external_systems_constant_includes_linear():
    """Linear is registered alongside Jira so the validator accepts it."""
    assert "linear" in EXTERNAL_SYSTEMS


def test_write_linear_tracker_with_push_fields_roundtrip(tmp_path):
    """Push-dominant systems carry last_pushed + push_hash in addition to the
    common fields. Both round-trip cleanly through write/read."""
    bp = _make_backlog(tmp_path)
    tid, _ = write_tracker(
        bp,
        external_system="linear",
        instance_alias="cm",
        external_key="ENG-42",
        title="Welcome flow polish",
        status="In Progress",
        url="https://linear.app/cm/issue/ENG-42",
        last_pushed="2026-05-20T10:00:00Z",
        push_hash="deadbeef",
    )
    assert tid == "linear-cm-eng-42"
    fm, _ = read_tracker(bp, tid)
    assert fm["external_system"] == "linear"
    assert fm["last_pushed"] == "2026-05-20T10:00:00Z"
    assert fm["push_hash"] == "deadbeef"


def test_write_tracker_auto_fills_sync_direction_from_system(tmp_path):
    """sync_direction is derived from external_system, never passed by the caller.
    Linear → push, Jira → pull."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp, external_system="linear", instance_alias="cm",
        external_key="ENG-1", title="L", status="Todo",
    )
    fm_l, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm_l["sync_direction"] == "push"

    write_tracker(
        bp, external_system="jira", instance_alias="cm",
        external_key="CM-1", title="J", status="To Do",
    )
    fm_j, _ = read_tracker(bp, "jira-cm-cm-1")
    assert fm_j["sync_direction"] == "pull"


def test_write_tracker_ignores_caller_supplied_sync_direction(tmp_path):
    """sync_direction is derived, not user-set. Even if a caller passes a value,
    the system map wins so the on-disk value can never drift from the system."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp, external_system="linear", instance_alias="cm",
        external_key="ENG-1", title="L", status="Todo",
        sync_direction="pull",  # adversarial
    )
    fm, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm["sync_direction"] == "push"


def test_update_tracker_can_set_push_hash(tmp_path):
    """The push worker updates last_pushed and push_hash after a successful
    outbound mutation. update_tracker is the entry point."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp, external_system="linear", instance_alias="cm",
        external_key="ENG-1", title="L", status="Todo",
    )
    update_tracker(
        bp, "linear-cm-eng-1",
        last_pushed="2026-05-20T11:00:00Z",
        push_hash="cafe1234",
    )
    fm, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm["last_pushed"] == "2026-05-20T11:00:00Z"
    assert fm["push_hash"] == "cafe1234"


def test_update_tracker_treats_sync_direction_as_immutable(tmp_path):
    """Like external_system, sync_direction cannot be flipped via update."""
    bp = _make_backlog(tmp_path)
    write_tracker(
        bp, external_system="linear", instance_alias="cm",
        external_key="ENG-1", title="L", status="Todo",
    )
    update_tracker(bp, "linear-cm-eng-1", sync_direction="pull")
    fm, _ = read_tracker(bp, "linear-cm-eng-1")
    assert fm["sync_direction"] == "push"


def test_validator_rejects_bogus_sync_direction(tmp_path):
    """A hand-edited tracker file with a sync_direction that doesn't match the
    system map is rejected on next read+validate (update path)."""
    bp = _make_backlog(tmp_path)
    tracker_dir(bp).mkdir(parents=True, exist_ok=True)
    rogue = tracker_dir(bp) / "linear-cm-eng-1.md"
    rogue.write_text(
        "---\n"
        "id: linear-cm-eng-1\n"
        "external_system: linear\n"
        "instance_alias: cm\n"
        "external_key: ENG-1\n"
        "title: L\n"
        "status: Todo\n"
        "sync_direction: pull\n"  # wrong for linear
        "---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sync_direction"):
        update_tracker(bp, "linear-cm-eng-1", status="Done")
