# User intent: a flagged projection file that is hard-linked must not be readable by the conflict resolver
# (team-server-019), so a flagged name can't expose or copy a file that lives elsewhere.
import os

import pytest

from taskmaster.native_routing import projection


def test_conflict_read_refuses_a_hard_linked_file(tmp_path):
    backlog = tmp_path / "backlog"
    (backlog / "tasks").mkdir(parents=True)
    original = tmp_path / "other.md"
    original.write_bytes(b"x: 1\n")
    flagged = backlog / "tasks" / "t-001.md"
    try:
        os.link(original, flagged)
    except OSError as exc:
        pytest.skip(f"hard links unavailable here: {exc}")
    with pytest.raises(projection.UnsafeConflictFile, match="hard-linked"):
        projection.read_file(backlog, "tasks/t-001.md")
    flagged.unlink()
    flagged.write_bytes(b"x: 1\n")
    assert projection.read_file(backlog, "tasks/t-001.md") == b"x: 1\n"
