"""Thread entity: frontmatter field, registry rebuild, lifecycle, resolution."""
import sys
from pathlib import Path
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster.taskmaster_v3 import (
    normalize_thread_name,
    read_handover,
    write_handover,
    handover_path,
    _handover_index_entry,
)
from taskmaster.taskmaster_v3 import (
    THREAD_STATUSES,
    sync_handover_index,
    sync_thread_registry,
    update_handover_status,
    update_thread_status,
)
from taskmaster.taskmaster_v3 import (
    archive_handover,
    list_sessions,
    list_threads,
    resolve_thread,
)


def _setup(tmp_path):
    bp = tmp_path / "backlog.yaml"
    bp.write_text(yaml.safe_dump({"meta": {}, "epics": []}))
    (tmp_path / "handovers").mkdir()
    return bp


def test_normalize_thread_name():
    assert normalize_thread_name("Team Relayout!") == "team-relayout"
    assert normalize_thread_name("") == "untitled"
    assert normalize_thread_name("x" * 100) == "x" * 40


def test_write_handover_stamps_thread(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(
        bp, tldr="relayout M1 done", thread="Team Relayout", task_ids=["T-1"],
    )
    fm, _ = read_handover(bp, hid)
    assert fm["thread"] == "team-relayout"


def test_write_handover_without_thread_omits_field(tmp_path):
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="explored stuff")
    fm, _ = read_handover(bp, hid)
    assert "thread" not in fm


def test_index_entry_carries_thread():
    entry = _handover_index_entry({"id": "x", "tldr": "t", "thread": "team-relayout"})
    assert entry["thread"] == "team-relayout"


def _write3(bp):
    """Three handovers: two in thread A (older/newer), one in thread B.

    NOTE: written in the order a1, b1, a2 — registry recency (`last_touched`)
    keys off the real `created` timestamp, so a2 must be CREATED last for
    thread-a to be the most recently touched thread.
    """
    a1, _ = write_handover(bp, tldr="A first", thread="thread-a",
                           task_ids=["T-1"], when="2026-07-10")
    b1, _ = write_handover(bp, tldr="B only", thread="thread-b",
                           when="2026-07-11")
    a2, _ = write_handover(bp, tldr="A second", thread="thread-a",
                           task_ids=["T-2"], next_action="do T-2 tests",
                           branch="feat/a", when="2026-07-12")
    return a1, a2, b1


def test_registry_rebuild_groups_and_orders(tmp_path):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    threads = data["threads"]
    assert set(threads) == {"thread-a", "thread-b"}
    ta = threads["thread-a"]
    assert ta["handover_ids"] == [a1, a2]          # chronological
    assert ta["task_ids"] == ["T-1", "T-2"]
    assert ta["tldr"] == "A second"                # newest member wins
    assert ta["next_action"] == "do T-2 tests"
    assert ta["branch"] == "feat/a"
    assert ta["status"] == "open"


def test_registry_derived_closed_when_all_members_closed(tmp_path):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    for hid in (a1, a2):
        update_handover_status(bp, handover_id=hid, status="closed")
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    assert data["threads"]["thread-a"]["status"] == "closed"
    assert data["threads"]["thread-b"]["status"] == "open"


def test_thread_status_override_and_auto_reopen(tmp_path):
    bp = _setup(tmp_path)
    _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)

    update_thread_status(data, bp, name="thread-a", status="parked")
    assert data["threads"]["thread-a"]["status"] == "parked"
    # Override survives a rebuild (no newer handover).
    sync_thread_registry(data, bp)
    assert data["threads"]["thread-a"]["status"] == "parked"

    # A newer handover auto-reopens: override pruned on rebuild.
    write_handover(bp, tldr="A resumed", thread="thread-a", when="2026-07-13")
    sync_thread_registry(data, bp)
    assert data["threads"]["thread-a"]["status"] == "open"
    assert "thread-a" not in (data.get("thread_meta") or {})


def test_thread_status_validation(tmp_path):
    bp = _setup(tmp_path)
    _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    import pytest
    with pytest.raises(ValueError):
        update_thread_status(data, bp, name="thread-a", status="bogus")
    with pytest.raises(KeyError):
        update_thread_status(data, bp, name="no-such-thread", status="parked")


def test_sync_handover_index_populates_threads(tmp_path):
    bp = _setup(tmp_path)
    _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_handover_index(data, bp)
    assert "thread-a" in data["threads"]
    # Round-trips through YAML (registry must be plain data).
    yaml.safe_dump(data)


def test_registry_prunes_vanished_thread_and_its_override(tmp_path):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    update_thread_status(data, bp, name="thread-a", status="parked")
    assert "thread-a" in data["thread_meta"]

    for hid in (a1, a2):
        handover_path(bp, hid).unlink()

    sync_thread_registry(data, bp)
    assert "thread-a" not in data["threads"]
    assert "thread-a" not in (data.get("thread_meta") or {})


def test_newest_member_follows_handover_id_order_not_created(tmp_path):
    """A backfilled when= date must not steal 'newest' from the id ordering."""
    bp = _setup(tmp_path)
    write_handover(bp, tldr="really newest", thread="t-x", when="2026-07-12")
    # Written later in wall-clock time, but dated earlier — NOT the newest member.
    hid_older, _ = write_handover(bp, tldr="backfilled older", thread="t-x", when="2026-07-05")
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    t = data["threads"]["t-x"]
    assert t["tldr"] == "really newest"
    assert t["handover_ids"][-1].startswith("2026-07-12")
    newest_id = t["handover_ids"][-1]
    fm, _ = read_handover(bp, newest_id)
    assert t["last_touched"] == fm["created"]


def test_override_staleness_survives_z_suffix(tmp_path):
    """A legacy Z-suffixed created timestamp must not out-rank an equal-instant
    override just because 'Z' sorts lexically above '+00:00'.

    Both timestamps below denote the SAME instant (2026-07-13T10:00:00 UTC),
    just in different notations. Raw string comparison says
    "2026-07-13T10:00:00+00:00" < "2026-07-13T10:00:00Z" (since '+' < 'Z'),
    which would wrongly mark the override stale and prune it. Parsed as
    datetimes they are equal, so the tie must still honour the override.
    """
    from taskmaster.taskmaster_v3 import write_task_file, handover_path
    bp = _setup(tmp_path)
    hid, _ = write_handover(bp, tldr="only member", thread="t-z", when="2026-07-10")
    fm, body = read_handover(bp, hid)
    fm["created"] = "2026-07-13T10:00:00Z"
    write_task_file(handover_path(bp, hid), fm, body)

    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    assert data["threads"]["t-z"]["last_touched"] == "2026-07-13T10:00:00Z"

    data["thread_meta"] = {
        "t-z": {"status": "parked", "set_at": "2026-07-13T10:00:00+00:00"},
    }
    data["threads"]["t-z"]["status"] = "parked"

    sync_thread_registry(data, bp)
    # Same instant as the handover, not older → override still applies.
    assert data["threads"]["t-z"]["status"] == "parked"
    assert "t-z" in data["thread_meta"]


def test_resolve_thread_by_name_and_by_handover_id(tmp_path):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)

    assert resolve_thread(data, bp, "thread-a") == ("thread-a", a2)
    assert resolve_thread(data, bp, "Thread A") == ("thread-a", a2)  # normalized
    # A stale dated slug still lands on the thread's NEWEST handover.
    assert resolve_thread(data, bp, a1) == ("thread-a", a2)
    import pytest
    with pytest.raises(KeyError):
        resolve_thread(data, bp, "nope-never")


def test_resolve_thread_archived_handover_id(tmp_path):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    archive_handover(bp, a1)
    assert resolve_thread(data, bp, a1) == ("thread-a", a2)


def test_list_threads_board_rows(tmp_path):
    bp = _setup(tmp_path)
    _write3(bp)
    data = {"meta": {}, "epics": []}
    sync_thread_registry(data, bp)
    rows = list_threads(data)
    assert [r["name"] for r in rows] == ["thread-a", "thread-b"]  # newest-touched first
    assert rows[0]["next_action"] == "do T-2 tests"
    assert "staleness_days" in rows[0]


def test_list_sessions_one_lane_per_thread(tmp_path, monkeypatch):
    bp = _setup(tmp_path)
    a1, a2, b1 = _write3(bp)
    solo, _ = write_handover(bp, tldr="threadless legacy", when="2026-07-09")
    monkeypatch.chdir(tmp_path)
    rows = list_sessions()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"thread-a", "thread-b", solo}
    assert by_id["thread-a"]["handover_ids"] == [a1, a2]
    assert by_id["thread-a"]["kind"] == "thread"
    assert by_id["thread-a"]["status"] == "open"
    assert all("parallel_with" not in r for r in rows)
