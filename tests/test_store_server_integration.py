"""User intent: prove the MCP server's load/mutate/save boundary really runs
through the SQLite store, so concurrent tool calls stop losing writes and a
tool that returns an error cannot leave a half-applied mutation behind.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store


def _bp(root: Path) -> Path:
    return root / ".taskmaster" / "backlog.yaml"


def _max_seq(root: Path) -> int:
    return store.status(_bp(root)).max_seq


def _committed_task(root: Path, task_id: str) -> dict:
    """Read a task straight out of a freshly opened store (no server caches)."""
    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    for epic in data.get("epics", []):
        for task in epic.get("tasks", []):
            if task.get("id") == task_id:
                return task
    raise AssertionError(f"task {task_id} not found in committed state")


@pytest.fixture()
def two_tasks(tm_epic_phase):
    root = tm_epic_phase
    first = bs.backlog_add_task(
        title="First", epic="test-epic", phase="dev", tldr="first task"
    )
    second = bs.backlog_add_task(
        title="Second", epic="test-epic", phase="dev", tldr="second task"
    )
    assert "Error" not in first, first
    assert "Error" not in second, second
    return root, "test-epic-001", "test-epic-002"


# ── the compatibility boundary itself ────────────────────


def test_module_level_snapshot_and_lock_are_gone():
    """The store owns concurrency now; the stale globals must not survive."""
    assert not hasattr(bs, "_LOAD_SNAPSHOT")
    assert not hasattr(bs, "_backlog_lock")


def test_mutate_and_save_outside_a_transaction_raises(tmp_taskmaster):
    data = bs._load()
    with pytest.raises(RuntimeError):
        bs._mutate_and_save(data)


def test_save_outside_a_transaction_raises(tmp_taskmaster):
    data = bs._load()
    with pytest.raises(RuntimeError):
        bs._save(data)


def test_mutate_and_save_rejects_a_foreign_dict(tmp_taskmaster):
    with bs._transaction(tool="test") as data:
        assert isinstance(data, dict)
        with pytest.raises(RuntimeError):
            bs._mutate_and_save({"epics": [], "phases": [], "meta": {}})


def test_nested_load_inside_a_transaction_is_identity_stable(tmp_taskmaster):
    with bs._transaction(tool="test") as data:
        assert bs._load() is data
        assert bs._load() is bs._load()


def test_unlatched_transaction_rolls_back_in_memory_mutations(two_tasks):
    root, task_id, _ = two_tasks
    before = _max_seq(root)
    with bs._transaction(tool="test") as data:
        data["epics"][0]["tasks"][0]["title"] = "never committed"
    assert _max_seq(root) == before
    assert _committed_task(root, task_id)["title"] == "First"


def test_latched_transaction_commits(two_tasks):
    root, task_id, _ = two_tasks
    with bs._transaction(tool="test") as data:
        data["epics"][0]["tasks"][0]["title"] = "committed"
        bs._mutate_and_save(data)
    assert _committed_task(root, task_id)["title"] == "committed"


# ── real tool call sites ─────────────────────────────────


def test_update_task_persists_through_the_store(two_tasks):
    root, task_id, _ = two_tasks
    out = bs.backlog_update_task(task_id, field="priority", value="high")
    assert "Error" not in out, out
    assert _committed_task(root, task_id)["priority"] == "high"


def test_update_task_also_lands_in_the_projection_file(two_tasks):
    root, task_id, _ = two_tasks
    bs.backlog_update_task(task_id, field="notes", value="projected note")
    text = (root / ".taskmaster" / "tasks" / f"{task_id}.md").read_text(encoding="utf-8")
    assert "projected note" in text


def test_validation_error_rolls_back_the_touch_it_already_applied(two_tasks):
    """`_touch_task` mutates before the status guard rejects; nothing may persist."""
    root, task_id, _ = two_tasks
    before = _max_seq(root)
    out = bs.backlog_update_task(task_id, field="status", value="not-a-status")
    assert out.startswith("Error:"), out
    assert _max_seq(root) == before


def test_unknown_task_error_writes_nothing(two_tasks):
    root, _, _ = two_tasks
    before = _max_seq(root)
    out = bs.backlog_update_task("no-such-task", field="priority", value="high")
    assert out.startswith("Error:"), out
    assert _max_seq(root) == before


def test_update_task_response_is_rendered_from_committed_state(two_tasks):
    root, task_id, _ = two_tasks
    out = bs.backlog_update_task(task_id, tldr="a committed tldr")
    assert "a committed tldr" in out
    assert _committed_task(root, task_id)["tldr"] == "a committed tldr"


def test_add_epic_and_add_task_persist_through_the_store(tmp_taskmaster):
    root = tmp_taskmaster
    bs.backlog_add_epic(epic_id="alpha", name="Alpha", done_when="done")
    bs.backlog_add_phase(phase_id="p1", name="Phase 1")
    out = bs.backlog_add_task(title="Fresh", epic="alpha", phase="p1", tldr="t")
    assert "Error" not in out, out
    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    assert [e["id"] for e in data["epics"]] == ["alpha"]
    assert [t["title"] for t in data["epics"][0]["tasks"]] == ["Fresh"]


def test_complete_task_nested_loads_stay_identity_stable(two_tasks):
    root, task_id, _ = two_tasks
    bs.backlog_update_task(task_id, field="status", value="in-progress")
    for gate in ("design-review", "review-gate"):
        assert "Error" not in bs.backlog_skip_gate(task_id, gate, "not needed in test")
    seen: list[int] = []
    original = bs._load

    def spy():
        data = original()
        seen.append(id(data))
        return data

    bs._load = spy
    try:
        out = bs.backlog_complete_task(task_id, patchnote="done it")
    finally:
        bs._load = original
    assert out.startswith("Completed"), out
    assert len(set(seen)) == 1, "nested _load() returned a different dict"
    assert _committed_task(root, task_id)["status"] == "done"


def test_auto_link_lands_in_the_same_commit_as_the_note(two_tasks):
    """Inline mentions are only recognized for uppercase-prefixed ids."""
    root, first, _ = two_tasks
    created = bs.backlog_add_task(
        title="Target", epic="test-epic", phase="dev", tldr="target",
        options={"task_id": "T-900"},
    )
    assert "Error" not in created, created

    before = _max_seq(root)
    out = bs.backlog_update_task(first, field="notes", value="follows on from T-900")
    assert "Error" not in out, out
    assert _max_seq(root) > before

    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    tasks = {t["id"]: t for t in data["epics"][0]["tasks"]}
    assert {"type": "references", "target": "T-900"} in (tasks[first].get("links") or [])
    assert {"type": "referenced_by", "target": first} in (tasks["T-900"].get("links") or [])


# ── the write-loss defect ────────────────────────────────


def test_two_threads_updating_different_tasks_both_persist(two_tasks):
    root, first, second = two_tasks
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def work(task_id: str, value: str) -> None:
        try:
            barrier.wait(timeout=30)
            bs.backlog_update_task(task_id, field="notes", value=value)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=(first, "note-one")),
        threading.Thread(target=work, args=(second, "note-two")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    notes = {t["id"]: t.get("notes") for t in data["epics"][0]["tasks"]}
    assert notes[first] == "note-one"
    assert notes[second] == "note-two"


def test_two_threads_updating_different_epics_both_persist(tmp_taskmaster):
    """The classic lost update: both writers read, both write, one wins."""
    root = tmp_taskmaster
    bs.backlog_add_epic(epic_id="alpha", name="Alpha", done_when="a")
    bs.backlog_add_epic(epic_id="beta", name="Beta", done_when="b")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def work(epic_id: str, name: str) -> None:
        try:
            barrier.wait(timeout=30)
            bs.backlog_update_epic(epic_id, "name", name)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=("alpha", "Alpha renamed")),
        threading.Thread(target=work, args=("beta", "Beta renamed")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    names = {e["id"]: e["name"] for e in data["epics"]}
    assert names == {"alpha": "Alpha renamed", "beta": "Beta renamed"}


def test_two_threads_adding_tasks_do_not_collide_on_one_id(tm_epic_phase):
    """Concurrent id allocation must not hand the same id to both writers."""
    root = tm_epic_phase
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def work(title: str) -> None:
        try:
            barrier.wait(timeout=30)
            results.append(
                bs.backlog_add_task(
                    title=title, epic="test-epic", phase="dev", tldr=title
                )
            )
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"T{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert all("Error" not in r for r in results), results

    store.reset_for_tests()
    data = store.load_dict(_bp(root))
    tasks = data["epics"][0]["tasks"]
    ids = [t["id"] for t in tasks]
    assert len(ids) == 2, ids
    assert len(set(ids)) == 2, ids
    assert sorted(t["title"] for t in tasks) == ["T0", "T1"]
