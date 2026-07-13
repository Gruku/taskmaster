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
    _handover_index_entry,
)
from taskmaster.taskmaster_v3 import (
    THREAD_STATUSES,
    sync_handover_index,
    sync_thread_registry,
    update_handover_status,
    update_thread_status,
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
