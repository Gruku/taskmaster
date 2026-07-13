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
