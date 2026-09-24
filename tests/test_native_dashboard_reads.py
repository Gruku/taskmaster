"""User intent: N16-B — the dashboard (`backlog_status`, PROGRESS.md) must stop paying
for a whole-backlog read on native stores (411 ms vs 120 ms legacy on CodeMaestro)
while rendering byte-identical text. The slim read is proven three ways: against the
full native tree, against the legacy twin, and by recording every task key the
renderers touch so a future renderer change cannot silently read a field the slim
read left out.
"""
from __future__ import annotations

from collections.abc import MutableMapping
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


class _Recording(MutableMapping):
    """A task that records every key a renderer reads and refuses a whole-task read.

    Not a dict subclass on purpose: an unbound `dict.get(task, key)` (or any other
    C-level dict access) would bypass a subclass's overrides silently, and here it
    raises instead. `get`, `setdefault`, `pop` and `in` all go through the recorded
    `__getitem__`/`__contains__`."""

    seen: set = set()

    def __init__(self, doc):
        self._doc = dict(doc)

    def __getitem__(self, key):
        _Recording.seen.add(key)
        return self._doc[key]

    def __contains__(self, key):
        _Recording.seen.add(key)
        return key in self._doc

    def __setitem__(self, key, value):
        _Recording.seen.add(key)
        self._doc[key] = value

    def __delitem__(self, key):
        _Recording.seen.add(key)
        del self._doc[key]

    def __len__(self):
        return len(self._doc)

    def __iter__(self):
        raise AssertionError("a dashboard renderer read a whole task dict")

    def keys(self):
        raise AssertionError("a dashboard renderer read a whole task dict")

    items = values = copy = keys


def test_renderers_read_only_the_fields_the_slim_read_carries(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed(seed=9))
    _Recording.seen = set()
    with native_connection(twins.native) as connection, Repository(connection).snapshot() as snapshot:
        data = reads.tree(snapshot, context=False)
        plain = reads.tree(snapshot)
    for epic in data["epics"]:
        epic["tasks"] = [_Recording(task) for task in epic["tasks"]]
    bs._derive_context(data)
    texts = (bs._status_text(data, False), bs._status_text(data, True),
             bs._render_progress_dashboard(data, "", []))
    assert texts == (bs._status_text(plain, False), bs._status_text(plain, True),
                     bs._render_progress_dashboard(plain, "", []))
    assert _Recording.seen, "the recorder saw nothing"
    assert _Recording.seen <= set(reads.DASHBOARD_TASK_FIELDS), _Recording.seen - set(reads.DASHBOARD_TASK_FIELDS)


def test_the_recorder_catches_every_way_of_reading_a_field():
    _Recording.seen = set()
    task = _Recording({"a": 1, "b": 2, "c": 3})
    task.setdefault("a", 0)
    task.pop("b")
    assert "c" in task
    assert task.get("d") is None
    assert _Recording.seen == {"a", "b", "c", "d"}
    with pytest.raises(TypeError):
        dict.get(task, "e")                  # an unbound dict read cannot slip past
    with pytest.raises(AssertionError):
        dict(task)


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
