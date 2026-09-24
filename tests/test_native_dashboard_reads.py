"""User intent: N16-B — the dashboard (`backlog_status`, PROGRESS.md) must stop paying
for a whole-backlog read on native stores (411 ms vs 120 ms legacy on CodeMaestro)
while rendering byte-identical text. The slim read is proven three ways: against the
full native tree, against the legacy twin, and by recording every task key the
renderers touch so a future renderer change cannot silently read a field the slim
read left out.
"""
from __future__ import annotations

from contextlib import closing
import random

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native.queries import Repository
from taskmaster.native_routing import reads
from native_twins import hand_edit_task, make_twins, native_connection

STATUSES = ("todo", "todo", "in-progress", "in-review", "blocked", "done", "archived")


def _seed(n_tasks=24, seed=3, bugs=2):
    def run():
        rng = random.Random(seed)
        bs.backlog_update_epic(epic_id="test-epic", field="status", value="active")
        bs.backlog_add_epic(epic_id="side", name="Side Quest", done_when="done", status="active")
        bs.backlog_add_epic(epic_id="later", name="Later", done_when="done")
        bs.backlog_add_phase(phase_id="ship", name="Ship", target_date="2026-10-01")
        bs.backlog_update_phase(phase_id="dev", field="status", value="active")
        ids = []
        for n in range(n_tasks):
            epic = rng.choice(["test-epic", "side", "later"])
            answer = bs.backlog_add_task(title=f"Task {n} é", epic=epic, phase=rng.choice(["dev", "ship"]),
                                         priority=rng.choice(["critical", "high", "medium", "low"]),
                                         notes="long prose " * rng.randint(0, 40))
            assert "Error" not in answer, answer
            ids.append(answer.split("`")[1])
        for ident in ids:
            status = rng.choice(STATUSES)

            def change(doc, status=status):
                doc["status"] = status
                if status == "done":
                    doc["completed"] = f"2026-09-{rng.randint(1, 16):02d}T10:00"
                if status in ("in-progress", "in-review"):
                    doc["started"] = f"2026-09-{rng.randint(1, 16):02d}"
                    doc["branch"] = f"feat/{doc['id']}"
                if status == "blocked":
                    doc["blockers"] = "waiting on vendor"
                if rng.random() < 0.3:
                    doc["depends_on"] = rng.choice([[rng.choice(ids)], "ghost-9", [rng.choice(ids), "gone"], 7])
                if rng.random() < 0.3:
                    doc["last_referenced"] = rng.choice(["2026-08-01", "2026-09-10T08:00", "not-a-date"])
                if rng.random() < 0.15:
                    doc["locked_by"] = "someone-else"
                if rng.random() < 0.15:
                    doc["priority"] = rng.choice(["P0", "P1", "P2", "P3"])
                if rng.random() < 0.1:
                    doc.pop("created", None)
                if rng.random() < 0.05:
                    doc.pop("phase", None)
                doc["order"] = rng.choice([1, 2, 2.5, 10])
            hand_edit_task(ident, change)
        for n in range(bugs):
            bs.backlog_bug_create(title=f"Bug {n}", components=["loader"])
        bs.backlog_issue_create(title="Systemic", severity="P1", evidence="three reports")
    return run


def _snapshot_texts(connection, build):
    with Repository(connection).snapshot() as snapshot:
        data = build(snapshot)
    existing = "# old\n\n## Changelog\n\nhand history\n"
    return (bs._status_text(data, False), bs._status_text(data, True),
            bs._render_progress_dashboard(data, existing, [{"text": "para"}]))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_dashboard_tree_renders_what_the_full_tree_renders(tmp_path, monkeypatch, seed):
    twins = make_twins(tmp_path, monkeypatch, _seed(seed=seed))
    with native_connection(twins.native) as connection:
        assert _snapshot_texts(connection, reads.dashboard_tree) == _snapshot_texts(connection, reads.tree)


def test_status_and_progress_match_the_legacy_twin(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed(seed=5))
    for kwargs in ({}, {"verbose": True}):
        twins.same("backlog_status", **kwargs)
    twins.same("backlog_pick_task", task_id="test-epic-001", force=True)
    twins.same("backlog_status")
    twins.assert_files_match()


class _Recording(dict):
    """A task dict that records every key a renderer reads, and refuses a whole-dict read."""

    seen: set = set()

    def __getitem__(self, key):
        _Recording.seen.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        _Recording.seen.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        _Recording.seen.add(key)
        return super().__contains__(key)

    def _whole(self, *args, **kwargs):
        raise AssertionError("a dashboard renderer read a whole task dict")

    keys = items = values = __iter__ = copy = _whole


def test_renderers_read_only_the_fields_the_slim_read_carries(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed(seed=9))
    _Recording.seen = set()
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        data = reads.tree(snapshot, context=False)
    for epic in data["epics"]:
        epic["tasks"] = [_Recording(task) for task in epic["tasks"]]
    bs._derive_context(data)
    bs._status_text(data, False)
    bs._status_text(data, True)
    bs._render_progress_dashboard(data, "", [])
    assert _Recording.seen, "the recorder saw nothing"
    assert _Recording.seen <= set(reads.DASHBOARD_TASK_FIELDS), _Recording.seen - set(reads.DASHBOARD_TASK_FIELDS)


def _statements(root, build):
    seen = []
    with native_connection(root) as connection:
        connection.set_trace_callback(seen.append)
        with Repository(connection).snapshot() as snapshot:
            build(snapshot)
        connection.set_trace_callback(None)
    return seen


def test_dashboard_read_is_a_fixed_number_of_queries_and_reads_no_task_body(tmp_path, monkeypatch):
    small = make_twins(tmp_path / "s", monkeypatch, _seed(n_tasks=6, bugs=1))
    large = make_twins(tmp_path / "l", monkeypatch, _seed(n_tasks=40, bugs=12))
    few, many = _statements(small.native, reads.dashboard_tree), _statements(large.native, reads.dashboard_tree)
    # No per-row queries: the statement count does not grow with tasks or bugs.
    assert len(few) == len(many), (len(few), len(many))
    assert not any("entity_documents" in sql and "'task'" in sql for sql in many)
