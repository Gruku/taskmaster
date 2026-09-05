# User intent: a tool must not tell a user their write failed when it committed. The
# "(not persisted)" marker exists to catch real losses, so it must fire only on real ones.
"""Committed-state response rendering has no false negatives (fix wave F8)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store


def _seed(root: Path) -> Path:
    backlog_path = root / ".taskmaster"
    for sub in ("tasks", "local"):
        (backlog_path / sub).mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "rendering",
        "meta": {"project": "rendering", "schema_version": 4},
        "epics": [{"id": "core", "name": "Core", "status": "active", "done_when": "n/a"}],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMASTER_ROOT", raising=False)
    root = tmp_path / "project"
    backlog_path = _seed(root)
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", backlog_path / "missing.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "missing.json")
    bs.backlog_add_task(
        "Target", "core", phase="dev", options={"anchors": "src/a.py,src/b.py"}
    )
    return backlog_path


def _committed_task(backlog_path: Path) -> dict:
    store.reset_for_tests()
    data = store.load_dict(backlog_path)
    return next(
        task
        for epic in data["epics"]
        for task in epic.get("tasks") or []
        if task["id"] == "core-001"
    )


@pytest.mark.parametrize("field", ["anchors", "phase", "release"])
def test_clearing_a_removable_field_is_not_reported_as_a_failure(project, field):
    if field == "release":
        bs.backlog_update_task("core-001", "release", "v1.0.0")

    result = bs.backlog_update_task("core-001", field, "none")

    assert bs.NOT_PERSISTED not in result, result
    assert field not in _committed_task(project)


def test_a_later_writer_does_not_make_a_committed_write_look_lost(project, monkeypatch):
    """The response renders this writer's own commit, not a later re-read."""
    real_load = bs._load
    fired = {"done": False}

    def racing_load():
        # Only outside a transaction, i.e. exactly where the response used to be
        # re-read after the commit had already landed.
        if not fired["done"] and bs._active_tx() is None:
            fired["done"] = True
            bs.backlog_update_task("core-001", "branch", "feature/later")
        return real_load()

    monkeypatch.setattr(bs, "_load", racing_load)
    result = bs.backlog_update_task("core-001", "branch", "feature/mine")
    monkeypatch.setattr(bs, "_load", real_load)

    assert bs.NOT_PERSISTED not in result, result
    assert "feature/mine" in result, result


def test_a_write_that_really_did_not_land_is_still_reported(project):
    """The marker must still fire when committed state disagrees with the write."""
    assert (
        bs._committed_field_display({}, "core-001", "branch", "feature/x")
        == bs.NOT_PERSISTED
    )
    assert (
        bs._committed_field_display(
            {("task", "core-001"): {"branch": "feature/other"}},
            "core-001",
            "branch",
            "feature/x",
        )
        == bs.NOT_PERSISTED
    )
    assert (
        bs._committed_field_display(
            {("task", "core-001"): {"branch": "feature/x"}},
            "core-001",
            "branch",
            "feature/x",
        )
        == "feature/x"
    )


# ── renderer stack, `[seq N]`, and single-writer PROGRESS.md (task 3.4) ──


def test_a_nested_tool_call_does_not_steal_the_outer_tools_response(project):
    """The outermost tool owns the answer; a nested renderer is discarded."""
    outer_seen = {}

    def nested_then_report(task_id: str) -> str:
        # A nested transactional tool registers its own renderer on the shared
        # frame; the outer call must still describe its own work.
        bs.backlog_update_task(task_id, "branch", "feature/nested")
        data = bs._load()
        task, epic = bs._tx_task(data, task_id)
        task["worktree"] = ".worktrees/outer"
        bs._tx_put_task(task, epic)
        bs._mutate_and_save(data)
        bs._render_after_commit(
            lambda committed: "outer: "
            + bs._committed_task_field(committed, task_id, "worktree")
        )
        return "outer: unrendered"

    wrapped = bs._transactional("test_outer")(nested_then_report)
    outer_seen["result"] = wrapped("core-001")

    assert outer_seen["result"].startswith("outer: .worktrees/outer"), outer_seen
    assert "nested" not in outer_seen["result"], outer_seen
    committed = _committed_task(project)
    assert committed["branch"] == "feature/nested"
    assert committed["worktree"] == ".worktrees/outer"


def test_a_nested_call_leaves_no_renderer_behind_on_the_frame(project):
    """The stack must come back to the depth the nested body found it at."""

    def probe(task_id: str) -> str:
        frame = bs._active_tx()
        depth = len(frame.renderers)
        bs.backlog_update_task(task_id, "branch", "feature/probe")
        assert len(frame.renderers) == depth
        data = bs._load()
        bs._mutate_and_save(data)
        return "probe done"

    result = bs._transactional("test_probe")(probe)("core-001")
    assert result.startswith("probe done"), result


def _outstanding_gates(task_id: str) -> list[str]:
    data = bs._load()
    task, _epic = bs._find_task(data, task_id)
    return list(bs._outstanding_required_gates(task))


def _seq_of(result: str) -> int:
    match = re.search(r"\[seq (\d+)\]$", result.strip())
    assert match, f"no [seq N] suffix on: {result!r}"
    return int(match.group(1))


def test_every_mutating_tool_reports_the_sequence_it_committed(project):
    """A result names the `changes` row that produced it, or it is unverifiable."""
    import sqlite3

    results = [
        bs.backlog_add_task("Second", "core", phase="dev"),
        bs.backlog_update_task("core-001", "priority", "high"),
        bs.backlog_update_task("core-001", tldr="a fresh tldr"),
        bs.backlog_pick_task("core-001"),
        bs.backlog_record_gate("core-001", "impl", status="done"),
        bs.backlog_record_merge("core-001", "develop", "abc1234def"),
        bs.backlog_clear_gate("core-001", "impl"),
        bs.backlog_note("create", text="a note"),
        bs.backlog_batch_update("update core-001 branch feature/batch"),
        *(
            bs.backlog_skip_gate("core-001", gate, "not applicable in this test")
            for gate in _outstanding_gates("core-001")
        ),
        bs.backlog_complete_task("core-001"),
    ]
    seqs = [_seq_of(result) for result in results]

    store.reset_for_tests()
    connection = sqlite3.connect(project / "local" / "store.db")
    try:
        known = {row[0] for row in connection.execute("SELECT seq FROM changes")}
    finally:
        connection.close()
    assert set(seqs) <= known, (seqs, sorted(known)[-10:])


def test_a_rejected_tool_call_reports_no_sequence(project):
    out = bs.backlog_update_task("core-001", "status", "not-a-status")
    assert out.startswith("Error:"), out
    assert "[seq" not in out, out


def test_context_is_derived_once_per_tool_call(project, monkeypatch):
    """Deriving it on transaction entry as well was a wasted full pass."""
    calls = []
    real = bs.regenerate_context
    monkeypatch.setattr(
        bs, "regenerate_context", lambda data: (calls.append(1), real(data))[1]
    )
    bs.backlog_update_task("core-001", "priority", "low")
    assert len(calls) == 1, calls


def test_the_server_no_longer_writes_the_progress_dashboard(project):
    """PROGRESS.md has exactly one writer, and it lives in the store."""
    assert not hasattr(bs, "regenerate_progress_dashboard")


def test_a_session_summary_reaches_progress_md_through_the_store(project):
    bs.backlog_pick_task("core-001")
    for gate in _outstanding_gates("core-001"):
        bs.backlog_skip_gate("core-001", gate, "not applicable in this test")
    out = bs.backlog_complete_task(
        "core-001", session_title="Ship it", done="- landed the thing"
    )
    assert "Error" not in out, out
    text = (project / "local" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "Ship it" in text, text
    assert "landed the thing" in text, text


def test_the_changelog_is_not_written_when_the_transaction_rolls_back(project):
    before = (project / "local" / "PROGRESS.md").read_text(encoding="utf-8")
    out = bs.backlog_complete_task(
        "core-001", session_title="Never happened", done="- nope"
    )
    assert out.startswith("Error:"), out  # core-001 is still `todo`
    after = (project / "local" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "Never happened" not in after
    assert after == before


def test_a_json_result_carries_the_sequence_as_a_field_not_a_suffix():
    """Appending the marker to a JSON payload would corrupt it for its parser."""

    class _Tx:
        seq = 42

    frame = bs._TxFrame({}, Path("."))
    frame.tx = _Tx()
    assert json.loads(bs._with_seq('{"ok": true}', frame)) == {"ok": True, "seq": 42}
    assert bs._with_seq("plain text", frame) == "plain text [seq 42]"
    assert bs._with_seq("{not json after all", frame) == "{not json after all [seq 42]"
