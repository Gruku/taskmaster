# User intent: prove, deterministically, that the five reported write-loss defects are gone —
# that real public Taskmaster tools driven from many threads and many OS processes at once never
# lose a write, never reuse an id, and always leave the SQLite rows and the file projection in
# agreement. These tests are the acceptance evidence for the SQLite store migration.
"""Concurrency and defect-regression tests for the SQLite-authoritative store.

Scenario map (design spec `docs/specs/2026-09-04-sqlite-store-design.md` §1):

| Defect | Covered by |
|---|---|
| 1 full-tree rewrite / open-handle failures | `test_export_contention_*` |
| 2 no cross-process lock | `test_two_processes_*`, `test_mixed_public_tool_operations_*` |
| 3 module-global load snapshot | `test_sequential_*`, `test_thread_pool_*` |
| 4 success reported from the request, not the commit | every assertion reads committed state |
| 5 duplicate task ids | `test_mixed_public_tool_operations_*` |
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from taskmaster import backlog_server as bs
from taskmaster import store

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# The committed profile the plan specifies.  Overridable for local debugging only.
STRESS_PROCESSES = int(os.environ.get("TM_STRESS_PROCESSES", "8"))
STRESS_OPS = int(os.environ.get("TM_STRESS_OPS", "200"))


# ── shared helpers ────────────────────────────────────────────────────────


def _seed_project(root: Path, *, epics: tuple[str, ...] = ("core",)) -> Path:
    """Write a v4 projection on disk, with no store bootstrap and no monkeypatch.

    Written by the test itself, so the conftest bypass guard classifies it as
    test seeding rather than a production write.
    """
    backlog_path = root / ".taskmaster"
    (backlog_path / "tasks").mkdir(parents=True, exist_ok=True)
    (backlog_path / "local").mkdir(parents=True, exist_ok=True)
    (backlog_path / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (backlog_path / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    document = {
        "version": 4,
        "project": "concurrency-tests",
        "meta": {"project": "concurrency-tests", "schema_version": 4},
        "epics": [
            {"id": ident, "name": ident.title(), "status": "active", "done_when": "n/a"}
            for ident in epics
        ],
        "phases": [{"id": "dev", "name": "Development", "status": "active"}],
    }
    (backlog_path / "backlog.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return backlog_path


def _worker_env(root: Path) -> dict[str, str]:
    """A subprocess environment that resolves the store to `root`, never this repo.

    `TASKMASTER_ROOT` pins both `backlog_server.ROOT` and `store.resolve_root`;
    `PYTHONPATH` carries only the plugin package so the worker can import it.
    """
    env = dict(os.environ)
    env["TASKMASTER_ROOT"] = str(root)
    env["PYTHONPATH"] = str(PLUGIN_ROOT)
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _committed(backlog_path: Path) -> dict:
    """A fresh committed read: drop every cached connection, reopen, load."""
    store.reset_for_tests()
    return store.load_dict(backlog_path)


def _tasks(data: dict) -> dict[str, dict]:
    return {
        task["id"]: task
        for epic in data.get("epics", []) or []
        for task in epic.get("tasks", []) or []
    }


def _rows(backlog_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(store.db_path(backlog_path)) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(sql, params))


def _file_task(backlog_path: Path, task_id: str) -> dict:
    from taskmaster.taskmaster_v3 import parse_frontmatter

    path = backlog_path / "tasks" / f"{task_id}.md"
    if not path.exists():
        path = backlog_path / "tasks" / "archive" / f"{task_id}.md"
    return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


def _totals(results: list[dict], key: str) -> dict[str, int]:
    """Sum one worker-report counter dict across every worker."""
    totals: dict[str, int] = {}
    for result in results:
        for name, count in (result.get(key) or {}).items():
            totals[name] = totals.get(name, 0) + count
    return totals


def _doc(row) -> dict:
    """The stored JSON document of one `entities` row."""
    return json.loads(row["doc"])


def _assert_entity_files_match_row_content(backlog_path: Path) -> None:
    """Every task file must hold exactly the frontmatter and body of its row.

    Matching the recorded content hash only proves the file agrees with whatever
    was last exported.  Re-rendering the live row proves it agrees with what the
    store holds now, which is what "rows and files agree" is supposed to mean.
    """
    from taskmaster.taskmaster_v3 import (
        BODY_KEY,
        render_frontmatter,
        task_v4_to_file,
    )

    for row in _rows(
        backlog_path,
        "SELECT id,archived,doc,body FROM entities WHERE kind='task' AND deleted=0",
    ):
        document = json.loads(row["doc"])
        if row["body"]:
            document = document | {BODY_KEY: row["body"]}
        frontmatter, body = task_v4_to_file(document)
        expected = render_frontmatter(frontmatter, body)
        relative = (
            f"tasks/archive/{row['id']}.md"
            if row["archived"]
            else f"tasks/{row['id']}.md"
        )
        actual = (backlog_path / relative).read_text(encoding="utf-8")
        assert actual == expected, (
            f"{relative} disagrees with the row the store holds for {row['id']}"
        )


def _assert_projection_matches_rows(backlog_path: Path) -> None:
    """Row -> file and file -> row must agree, in both directions.

    Forward: every non-quarantined `projection` row names a file that exists and
    whose bytes hash to the recorded digest.  Backward: every live task/epic/phase
    row has its projection file on disk, and every entity file on disk is a row.
    """
    for row in _rows(backlog_path, "SELECT file,content_hash,quarantined FROM projection"):
        path = backlog_path / row["file"]
        if row["quarantined"]:
            continue
        assert path.exists(), f"projection row {row['file']} has no file"
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        assert digest == row["content_hash"], f"{row['file']} drifted from its row hash"

    tracked = {row["file"] for row in _rows(backlog_path, "SELECT file FROM projection")}
    document = yaml.safe_load((backlog_path / "backlog.yaml").read_text(encoding="utf-8")) or {}
    inline = {
        kind: {entry["id"] for entry in (document.get(f"{kind}s") or [])}
        for kind in ("epic", "phase")
    }
    for row in _rows(
        backlog_path,
        "SELECT kind,id,archived FROM entities "
        "WHERE kind IN ('task','epic','phase') AND deleted=0",
    ):
        if row["kind"] == "task":
            rel = f"tasks/archive/{row['id']}.md" if row["archived"] else f"tasks/{row['id']}.md"
            assert (backlog_path / rel).exists(), f"task {row['id']} has no projection file"
            assert rel in tracked, f"{rel} exists but the store does not track it"
            continue
        # An epic or phase with no heavy fields and no body lives inline in
        # backlog.yaml and deliberately has no file of its own.
        rel = f"{row['kind']}s/{row['id']}.md"
        if (backlog_path / rel).exists():
            assert rel in tracked, f"{rel} exists but the store does not track it"
        else:
            assert row["id"] in inline[row["kind"]], (
                f"{row['kind']} {row['id']} is in neither a file nor backlog.yaml"
            )

    for directory in ("tasks", "tasks/archive", "epics", "phases"):
        for path in sorted((backlog_path / directory).glob("*.md")):
            rel = path.relative_to(backlog_path).as_posix()
            assert rel in tracked, f"untracked projection file {rel}"

    _assert_entity_files_match_row_content(backlog_path)

    leftovers = [
        path.name
        for path in backlog_path.rglob("*.tmp.*")
        if path.is_file()
    ]
    assert not leftovers, f"temp files left behind: {leftovers}"


# ── 1. same process, same thread ──────────────────────────────────────────


@pytest.fixture()
def three_tasks(tm_epic_phase):
    ids = []
    for index in range(1, 4):
        bs.backlog_add_task(title=f"Task {index}", epic="test-epic", phase="dev")
        ids.append(f"test-epic-{index:03d}")
    return tm_epic_phase, ids


def test_sequential_tool_calls_never_compose_from_an_older_snapshot(three_tasks):
    """Defect 3: a later write must build on the previous commit, not a stale tree.

    Every call is asserted against committed state the moment it returns, and the
    whole interleaved sequence is asserted once more from a cold reopen, so a
    write that was later reverted by a stale snapshot cannot pass.
    """
    root, ids = three_tasks
    backlog_path = root / ".taskmaster"
    expected: dict[str, dict[str, str]] = {task_id: {} for task_id in ids}

    sequence = [
        (ids[0], "branch", "feature/one"),
        (ids[1], "human_action", "review the plan"),
        (ids[2], "priority", "high"),
        (ids[0], "human_action", "sign the release"),
        (ids[1], "branch", "feature/two"),
        (ids[2], "branch", "feature/three"),
        (ids[0], "priority", "low"),
        (ids[1], "priority", "high"),
    ]
    for task_id, field, value in sequence:
        result = bs.backlog_update_task(task_id, field, value)
        assert not result.startswith("Error"), result
        expected[task_id][field] = value
        live = _tasks(bs._load())
        for other, fields in expected.items():
            for name, wanted in fields.items():
                assert live[other].get(name) == wanted, (
                    f"after {task_id}.{field}, {other}.{name} reverted"
                )

    committed = _tasks(_committed(backlog_path))
    for task_id, fields in expected.items():
        for name, wanted in fields.items():
            assert committed[task_id].get(name) == wanted
            assert _file_task(backlog_path, task_id).get(name) == wanted
    _assert_projection_matches_rows(backlog_path)


def test_a_second_load_in_the_same_thread_sees_the_previous_commit(three_tasks):
    """No module-level snapshot survives a write: the next `_load` is committed state."""
    root, ids = three_tasks
    before = bs._load()
    assert _tasks(before)[ids[0]].get("branch") in (None, "")

    bs.backlog_update_task(ids[0], "branch", "feature/second-load")

    after = bs._load()
    assert after is not before
    assert _tasks(after)[ids[0]]["branch"] == "feature/second-load"


# ── 2. same process, thread pool ──────────────────────────────────────────


def test_thread_pool_status_human_action_and_branch_writes_all_persist(tm_epic_phase):
    """Defect 2/3: the three fields the field reports saw revert, at pool width.

    FastMCP runs sync tools in a thread pool, so this is the shape of a single
    session issuing parallel tool calls.
    """
    root = tm_epic_phase
    backlog_path = root / ".taskmaster"
    count = 24
    for index in range(1, count + 1):
        bs.backlog_add_task(title=f"Pool task {index}", epic="test-epic", phase="dev")
    ids = [f"test-epic-{index:03d}" for index in range(1, count + 1)]

    def edit(task_id: str) -> list[str]:
        errors = []
        for field, value in (
            ("branch", f"feature/{task_id}"),
            ("human_action", f"confirm {task_id}"),
            ("status", "in-progress"),
        ):
            result = bs.backlog_update_task(task_id, field, value)
            if result.startswith("Error"):
                errors.append(result)
        return errors

    with ThreadPoolExecutor(max_workers=8) as pool:
        failures = [item for batch in pool.map(edit, ids) for item in batch]
    assert not failures, failures

    committed = _tasks(_committed(backlog_path))
    for task_id in ids:
        task = committed[task_id]
        assert task["branch"] == f"feature/{task_id}"
        assert task["human_action"] == f"confirm {task_id}"
        assert task["status"] == "in-progress"
        assert _file_task(backlog_path, task_id)["branch"] == f"feature/{task_id}"
    _assert_projection_matches_rows(backlog_path)


def test_thread_pool_writes_to_one_task_all_land_in_the_change_log(tm_epic_phase):
    """Eight threads writing eight distinct fields of the SAME task keep all eight.

    Contention on one row is the case a per-entity file lock would serialise and a
    stale snapshot would silently collapse to one winner.
    """
    root = tm_epic_phase
    backlog_path = root / ".taskmaster"
    bs.backlog_add_task(title="Hot task", epic="test-epic", phase="dev")
    task_id = "test-epic-001"
    fields = {
        "branch": "feature/hot",
        "worktree": ".worktrees/hot",
        "human_action": "approve",
        "priority": "high",
        "estimate": "L",
        "stage": "2",
        "release": "alpha-1.0",
        "patchnote": "Hot task shipped.",
    }

    def edit(item: tuple[str, str]) -> str:
        return bs.backlog_update_task(task_id, item[0], item[1])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(edit, sorted(fields.items())))
    assert not [item for item in results if item.startswith("Error")], results

    committed = _tasks(_committed(backlog_path))[task_id]
    persisted = _file_task(backlog_path, task_id)
    for name, value in fields.items():
        assert str(committed.get(name)) == value, f"{name} lost: {committed.get(name)!r}"
        assert str(persisted.get(name)) == value, f"{name} missing from the file"
    _assert_projection_matches_rows(backlog_path)


# ── 3. two processes ──────────────────────────────────────────────────────


_TWO_PROCESS_WORKER = '''
import json, sys, time
from pathlib import Path

backlog_path = Path(sys.argv[2])
role = sys.argv[1]
ready = backlog_path.parent / f"ready-{role}"
peer = backlog_path.parent / ("ready-store" if role == "compat" else "ready-compat")


def rendezvous():
    ready.write_text("ok", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not peer.exists():
        if time.monotonic() > deadline:
            raise SystemExit("peer never arrived")
        time.sleep(0.01)


if role == "compat":
    from taskmaster import backlog_server as bs

    assert bs._backlog_path().parent == backlog_path, bs._backlog_path()
    rendezvous()
    out = [
        bs.backlog_update_task("core-001", "branch", "feature/from-compat"),
        bs.backlog_add_task(title="Compat created", epic="core", phase="dev"),
    ]
else:
    from taskmaster import store

    rendezvous()
    with store.transaction(tool="direct-store", backlog_path=backlog_path) as tx:
        task = tx.get("task", "core-002")
        task["human_action"] = "from the direct store"
        tx.put("task", "core-002", task)
        tx.create("epic", {"id": "made-by-store", "name": "Made by store", "status": "active"})
    out = ["direct ok"]

print(json.dumps(out))
'''


def test_two_processes_compat_and_direct_store_transactions_both_survive(tmp_path):
    """Defect 2: two OS processes, two write paths, no lost field and no lost creation."""
    root = tmp_path / "repo"
    backlog_path = _seed_project(root)
    for number, title in ((1, "Alpha"), (2, "Beta")):
        (backlog_path / "tasks" / f"core-{number:03d}.md").write_text(
            "---\n"
            f"id: core-{number:03d}\ntitle: {title}\nstatus: todo\nepic: core\n"
            f"order: {float(number)}\npriority: medium\n"
            "---\n\n## Notes\n",
            encoding="utf-8",
        )
    worker = tmp_path / "two_process_worker.py"
    worker.write_text(_TWO_PROCESS_WORKER, encoding="utf-8")

    processes = [
        subprocess.Popen(
            [sys.executable, str(worker), role, str(backlog_path)],
            cwd=str(root),
            env=_worker_env(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for role in ("compat", "store")
    ]
    outputs = [process.communicate(timeout=180) for process in processes]
    for process, (out, err) in zip(processes, outputs):
        assert process.returncode == 0, f"stdout={out}\nstderr={err}"

    data = _committed(backlog_path)
    tasks = _tasks(data)
    assert tasks["core-001"]["branch"] == "feature/from-compat"
    assert tasks["core-002"]["human_action"] == "from the direct store"
    assert "core-003" in tasks, "the compatibility process lost its creation"
    assert {epic["id"] for epic in data["epics"]} >= {"core", "made-by-store"}
    assert _file_task(backlog_path, "core-001")["branch"] == "feature/from-compat"
    assert _file_task(backlog_path, "core-002")["human_action"] == "from the direct store"
    _assert_projection_matches_rows(backlog_path)


# ── 4. eight processes, mixed real public tools ───────────────────────────


_STRESS_WORKER = '''
"""One stress worker: `ops` real public tool calls against the shared store."""
import json, random, re, sys, time
from pathlib import Path

worker = int(sys.argv[1])
ops = int(sys.argv[2])
backlog_path = Path(sys.argv[3])
report_path = Path(sys.argv[4])
start_gate = backlog_path.parent / "stress-go"

from taskmaster import backlog_server as bs
from taskmaster import store

assert bs._backlog_path().parent == backlog_path, bs._backlog_path()

deadline = time.monotonic() + 120
while not start_gate.exists():
    if time.monotonic() > deadline:
        raise SystemExit("start gate never opened")
    time.sleep(0.005)

rng = random.Random(worker)
own_epic = f"w{worker}"
mine = []                 # live task ids this worker created and still tracks
created = []              # (kind, id) creations, to prove ids are globally unique
branches = {}             # task id -> asserted branch
human_actions = {}        # task id -> asserted human_action
priorities = {}           # task id -> asserted priority
epic_notes = {}           # epic id -> asserted description
phase_notes = {}          # phase id -> asserted description
archived = set()          # task ids this worker archived
archived_epics = []       # [epic id, the task the archive must have cascaded to]
statuses = {}             # task id -> status this worker last drove it to
gates = {}                # task id -> gate whose "done" record must survive
merges = {}               # task id -> merge commit sha that must survive
ok = {}                   # tool -> successful call count
errors = {}               # tool -> refused call count
error_samples = []
seqs = []
ID_PATTERN = re.compile(r"Added `([^`]+)`")


def note_seq():
    seqs.append(store.status(backlog_path).max_seq)


def call(_tool, *args, **kwargs):
    """Run a real public tool.  A refused call is recorded, never swallowed."""
    result = getattr(bs, _tool)(*args, **kwargs)
    if isinstance(result, str) and result.startswith("Error"):
        errors[_tool] = errors.get(_tool, 0) + 1
        if len(error_samples) < 8:
            error_samples.append(_tool + ": " + result[:160])
        return None
    ok[_tool] = ok.get(_tool, 0) + 1
    note_seq()
    return result


def add_task(epic, label):
    result = call("backlog_add_task", title=label, epic=epic, phase="dev")
    if not result:
        return None
    match = ID_PATTERN.search(result)
    if not match:
        return None
    created.append(["task", match.group(1)])
    return match.group(1)


def forget(task_id):
    branches.pop(task_id, None)
    human_actions.pop(task_id, None)
    priorities.pop(task_id, None)
    statuses.pop(task_id, None)


# Weighted mix, plus a deterministic warm-up sweep so every operation class is
# attempted at least once even on a reduced debugging profile.  The interleaving
# across processes stays racy; only the coverage is pinned.
WEIGHTS = [
    ("add", 0.30), ("branch", 0.16), ("human", 0.09), ("pick", 0.07),
    ("gate", 0.06), ("merge", 0.05), ("complete", 0.05), ("archive", 0.04),
    ("batch", 0.04), ("phase", 0.04), ("epic", 0.05), ("epic_archive", 0.05),
]
SWEEP = ["add", "add", "branch", "human", "pick", "gate", "merge", "add",
         "complete", "archive", "add", "batch", "phase", "epic", "epic_archive"]


def pick_op(index):
    if index < len(SWEEP):
        return SWEEP[index]
    roll = rng.random()
    for name, weight in WEIGHTS:
        roll -= weight
        if roll < 0:
            return name
    return "add"


for index in range(ops):
    op = pick_op(index)
    if op == "add" or not mine:
        epic = own_epic if rng.random() < 0.5 else "shared"
        task_id = add_task(epic, f"w{worker} task {index}")
        if task_id:
            mine.append(task_id)
            statuses[task_id] = "todo"
        continue
    task_id = rng.choice(mine)
    if op == "branch":
        value = f"feature/w{worker}-{index}"
        if call("backlog_update_task", task_id, "branch", value):
            branches[task_id] = value
    elif op == "human":
        value = f"check {worker}/{index}"
        if call("backlog_update_task", task_id, "human_action", value):
            human_actions[task_id] = value
    elif op == "pick":
        if call("backlog_pick_task", task_id):
            statuses[task_id] = "in-progress"
    elif op == "gate":
        if call("backlog_record_gate", task_id, "impl", status="done"):
            gates[task_id] = "impl"
    elif op == "merge":
        sha = f"{worker:02d}{index:038d}"
        if call("backlog_record_merge", task_id, "develop", sha):
            merges[task_id] = sha
    elif op == "complete":
        # The full completion ladder, as a real session drives it.
        if call("backlog_pick_task", task_id):
            statuses[task_id] = "in-progress"
        call("backlog_record_gate", task_id, "impl", status="done")
        call("backlog_skip_gate", task_id, "design-review", reason="stress")
        call("backlog_skip_gate", task_id, "review-gate", reason="stress")
        if call("backlog_complete_task", task_id, session_title=f"w{worker} {index}"):
            statuses[task_id] = "done"
            human_actions.pop(task_id, None)   # completion clears it by design
    elif op == "archive":
        # Only a done/blocked/todo task may be archived; picking one keeps the
        # call legal, so a refusal here would be a real regression.
        archivable = [
            ident for ident in mine
            if statuses.get(ident, "todo") in ("todo", "done", "blocked")
        ]
        if archivable:
            task_id = rng.choice(archivable)
            if call("backlog_archive_task", task_id, reason="wont-fix"):
                archived.add(task_id)
                forget(task_id)
                mine.remove(task_id)
    elif op == "batch":
        value = "high" if index % 2 else "low"
        result = call("backlog_batch_update", f"update {task_id} priority {value}")
        if result and "1 applied" in result:
            priorities[task_id] = value
    elif op == "phase":
        phase_id = f"p{worker}-{index}"
        if call("backlog_add_phase", phase_id=phase_id, name=f"Phase {worker}-{index}"):
            created.append(["phase", phase_id])
            note = f"phase from w{worker} op {index}"
            if call("backlog_update_phase", phase_id, "description", note):
                phase_notes[phase_id] = note
    elif op == "epic":
        epic_id = f"w{worker}-e{index}"
        if call("backlog_add_epic", epic_id=epic_id, name=f"Epic {worker}-{index}",
                done_when="stress epic complete"):
            created.append(["epic", epic_id])
            note = f"epic from w{worker} op {index}"
            if call("backlog_update_epic", epic_id, "description", note):
                epic_notes[epic_id] = note
    else:
        # An epic created, populated and archived through real tools; the
        # archive must cascade to the task the epic owns.
        epic_id = f"w{worker}-a{index}"
        if not call("backlog_add_epic", epic_id=epic_id,
                    name=f"Archived {worker}-{index}",
                    done_when="stress epic archived"):
            continue
        created.append(["epic", epic_id])
        cascaded = add_task(epic_id, f"w{worker} cascade {index}")
        if cascaded and call("backlog_archive_epic", epic_id, reason="wont-fix"):
            archived_epics.append([epic_id, cascaded])

report_path.write_text(
    json.dumps(
        {
            "worker": worker,
            "created": created,
            "branches": branches,
            "human_actions": human_actions,
            "priorities": priorities,
            "epic_notes": epic_notes,
            "phase_notes": phase_notes,
            "archived": sorted(archived),
            "archived_epics": archived_epics,
            "statuses": statuses,
            "gates": gates,
            "merges": merges,
            "ok": ok,
            "errors": errors,
            "error_samples": error_samples,
            "seqs": seqs,
        }
    ),
    encoding="utf-8",
)
'''


# Tools whose arguments the worker always constructs legally.  A refusal from one
# of these is a regression, not a business rule, so zero is the bound.
MUST_NOT_ERROR = (
    "backlog_add_task",
    "backlog_update_task",
    "backlog_add_epic",
    "backlog_update_epic",
    "backlog_archive_epic",
    "backlog_add_phase",
    "backlog_update_phase",
)

# Every field class the stress test asserts survival for.  A class that comes back
# empty would make its survival loop iterate over nothing and pass vacuously, so
# each one has to be non-empty across the run.
ASSERTED_CLASSES = (
    "branches",
    "human_actions",
    "priorities",
    "epic_notes",
    "phase_notes",
    "archived",
    "archived_epics",
    "statuses",
    "gates",
    "merges",
)

# Operations that must have succeeded at least once somewhere in the run.
REQUIRED_OPS = (
    "backlog_add_task",
    "backlog_update_task",
    "backlog_pick_task",
    "backlog_record_gate",
    "backlog_record_merge",
    "backlog_complete_task",
    "backlog_archive_task",
    "backlog_batch_update",
    "backlog_add_epic",
    "backlog_update_epic",
    "backlog_archive_epic",
    "backlog_add_phase",
    "backlog_update_phase",
)


@pytest.mark.slow
def test_mixed_public_tool_operations_across_processes_never_lose_a_write(tmp_path):
    """The acceptance case: N processes x M real public tool calls on one store.

    Every worker drives the real MCP tool functions in a real OS process — task
    add / update / pick / gate / merge / complete / archive / batch update, phase
    add and update, epic add, update and archive — against one shared store.
    Afterwards every committed sequence must exist, every id must be unique
    (defect 5), every asserted field must have survived (defects 2-4), no dirty or
    temp file may remain, and rows and files must agree both ways.

    Anti-vacuity is enforced explicitly: each asserted field class must be
    non-empty across the run, each required operation must have succeeded at least
    once, and the tools whose arguments are always legal must have refused nothing.
    Without that, a mix that silently stopped producing expectations would leave
    the survival loops iterating over nothing and the test would pass on air.

    Profile is 8 processes x 200 operations by default; override with
    `TM_STRESS_PROCESSES` / `TM_STRESS_OPS` when debugging locally.

    Marked `slow`: it is roughly half the suite's wall clock, so the fast
    development loop is `uv run pytest -q -m "not slow"`.  Nothing deselects it
    otherwise — the full run, and CI, still execute it at the default profile.
    """
    workers, ops = STRESS_PROCESSES, STRESS_OPS
    root = tmp_path / "repo"
    backlog_path = _seed_project(
        root, epics=("shared", *(f"w{index}" for index in range(workers)))
    )
    worker_script = tmp_path / "stress_worker.py"
    worker_script.write_text(_STRESS_WORKER, encoding="utf-8")
    reports = [tmp_path / f"report-{index}.json" for index in range(workers)]

    # Bootstrap the store once from the parent so the workers all race a store
    # that already exists — the interesting contention, not first-open serialisation.
    store.reset_for_tests()
    store.load_dict(backlog_path)
    store.reset_for_tests()

    started = time.monotonic()
    processes = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(index), str(ops),
             str(backlog_path), str(reports[index])],
            cwd=str(root),
            env=_worker_env(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for index in range(workers)
    ]
    (root / "stress-go").write_text("go", encoding="utf-8")
    outputs = [process.communicate(timeout=1800) for process in processes]
    elapsed = time.monotonic() - started
    for index, (process, (out, err)) in enumerate(zip(processes, outputs)):
        assert process.returncode == 0, f"worker {index} failed\nstdout={out}\nstderr={err}"

    results = [json.loads(path.read_text(encoding="utf-8")) for path in reports]

    # ── the mix actually ran, and ran legally ────────────────────────────
    samples = [line for result in results for line in result["error_samples"]]
    refused = _totals(results, "errors")
    illegal = {tool: count for tool, count in refused.items() if tool in MUST_NOT_ERROR}
    assert not illegal, (
        f"tools whose arguments are always legal were refused: {illegal}; samples: {samples[:8]}"
    )
    succeeded = _totals(results, "ok")
    missing_ops = [tool for tool in REQUIRED_OPS if not succeeded.get(tool)]
    assert not missing_ops, (
        f"these operations never succeeded, so the mix did not exercise them: "
        f"{missing_ops}; successes: {succeeded}; refusals: {refused}; samples: {samples[:8]}"
    )
    sizes = {name: sum(len(result[name]) for result in results) for name in ASSERTED_CLASSES}
    empty = [name for name, size in sizes.items() if size == 0]
    assert not empty, (
        f"no expectations were recorded for {empty}, so their survival checks would "
        f"assert nothing; class sizes: {sizes}; refusals: {refused}; samples: {samples[:8]}"
    )
    # Refusals from the state-dependent tools are legitimate (a gate out of order,
    # a completion blocked) but must stay a minority of what the run attempted.
    attempted = sum(succeeded.values()) + sum(refused.values())
    assert sum(refused.values()) <= attempted // 2, (
        f"{sum(refused.values())} of {attempted} tool calls were refused; "
        f"breakdown {refused}; samples: {samples[:8]}"
    )

    # ── ids are globally unique: no two workers received the same new id ──
    seen: dict[tuple[str, str], int] = {}
    for result in results:
        for kind, ident in result["created"]:
            key = (kind, ident)
            assert key not in seen, (
                f"{kind} id {ident} handed to workers {seen[key]} and {result['worker']}"
            )
            seen[key] = result["worker"]
    assert len(seen) > workers, f"only {len(seen)} entities created — the mix did not run"

    data = _committed(backlog_path)
    tasks = _tasks(data)
    rows = {
        (row["kind"], row["id"]): row
        for row in _rows(
            backlog_path,
            "SELECT kind,id,archived,doc FROM entities WHERE deleted=0",
        )
    }
    archived_rows = {
        ident for (kind, ident), row in rows.items() if kind == "task" and row["archived"]
    }

    for result in results:
        for kind, ident in result["created"]:
            assert (kind, ident) in rows, f"{kind} {ident} vanished from the store"
            if kind == "task":
                assert ident in tasks or ident in archived_rows, f"task {ident} vanished"
        for task_id, branch in result["branches"].items():
            assert tasks[task_id]["branch"] == branch, f"{task_id} branch reverted"
            assert _file_task(backlog_path, task_id)["branch"] == branch
        for task_id, action in result["human_actions"].items():
            assert tasks[task_id].get("human_action") == action, f"{task_id} human_action reverted"
            assert _file_task(backlog_path, task_id).get("human_action") == action
        for task_id, priority in result["priorities"].items():
            assert tasks[task_id].get("priority") == priority, f"{task_id} priority reverted"
        for epic_id, note in result["epic_notes"].items():
            assert _doc(rows[("epic", epic_id)]).get("description") == note, (
                f"epic {epic_id} description reverted"
            )
        for phase_id, note in result["phase_notes"].items():
            assert _doc(rows[("phase", phase_id)]).get("description") == note, (
                f"phase {phase_id} description reverted"
            )
        for task_id in result["archived"]:
            assert task_id in archived_rows, f"{task_id} archive did not reach the row"
            assert (backlog_path / "tasks" / "archive" / f"{task_id}.md").exists()
        for task_id, status in result["statuses"].items():
            if task_id in result["archived"]:
                continue
            assert tasks[task_id].get("status") == status, (
                f"{task_id} is {tasks[task_id].get('status')!r}, not the {status!r} "
                f"this worker drove it to"
            )
        for task_id, gate in result["gates"].items():
            document = tasks.get(task_id) or _doc(rows[("task", task_id)])
            recorded = document.get("gates") or {}
            assert recorded.get(gate, {}).get("status") == "done", (
                f"{task_id} lost the `{gate}` gate it recorded"
            )
        for task_id, sha in result["merges"].items():
            document = tasks.get(task_id) or _doc(rows[("task", task_id)])
            landed = {
                entry.get("merge_commit")
                for entry in (document.get("merge_status") or {}).values()
                if isinstance(entry, dict)
            }
            assert sha in landed, f"{task_id} lost the merge it recorded ({sha[:7]})"
        for epic_id, task_id in result["archived_epics"]:
            assert rows[("epic", epic_id)]["archived"], f"epic {epic_id} archive did not reach the row"
            assert _doc(rows[("epic", epic_id)]).get("status") == "archived"
            assert task_id in archived_rows, f"epic {epic_id} archive did not cascade to {task_id}"
            assert (backlog_path / "tasks" / "archive" / f"{task_id}.md").exists()

    # Every sequence a worker was told about is a real committed change row.
    committed_seqs = {row["seq"] for row in _rows(backlog_path, "SELECT seq FROM changes")}
    for result in results:
        missing = [seq for seq in result["seqs"] if seq not in committed_seqs]
        assert not missing, f"worker {result['worker']} saw uncommitted sequences {missing[:5]}"

    status = store.status(backlog_path)
    assert status.dirty_files == (), status.dirty_files
    assert status.quarantined_files == (), status.quarantined_files
    _assert_projection_matches_rows(backlog_path)
    print(
        f"\nstress profile: {workers} processes x {ops} ops, {len(seen)} entities created, "
        f"{attempted} tool calls ({sum(refused.values())} legitimately refused), "
        f"asserted {sizes}, {elapsed:.1f}s wall clock"
    )


# ── 5. Windows open-handle contention ─────────────────────────────────────


def test_export_contention_inside_the_retry_window_still_converges(three_tasks):
    """Defect 1/4: a held handle is retried, and the tool only reports a real commit."""
    root, ids = three_tasks
    backlog_path = root / ".taskmaster"
    target = backlog_path / "tasks" / f"{ids[0]}.md"
    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(src, dst, **kwargs):
        if Path(dst) == target and attempts["count"] < 3:
            attempts["count"] += 1
            raise PermissionError(13, "held open by a peer", str(dst))
        return real_replace(src, dst, **kwargs)

    # A nested context, not the shared `monkeypatch` fixture: undoing that one
    # would also undo the project-root patch and the bypass guard.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "replace", flaky_replace)
        result = bs.backlog_update_task(ids[0], "branch", "feature/retried")
    assert not result.startswith("Error"), result

    assert attempts["count"] == 3, "the retry loop never ran"
    assert store.status(backlog_path).dirty_files == ()
    assert _tasks(_committed(backlog_path))[ids[0]]["branch"] == "feature/retried"
    assert _file_task(backlog_path, ids[0])["branch"] == "feature/retried"
    _assert_projection_matches_rows(backlog_path)


def test_export_contention_past_the_retry_window_commits_db_truth_then_converges(
    three_tasks,
):
    """Past the window the DB is still the truth, the file is dirty, and it drains."""
    root, ids = three_tasks
    backlog_path = root / ".taskmaster"
    target = backlog_path / "tasks" / f"{ids[0]}.md"
    real_replace = os.replace
    holding = {"on": True}

    def held_replace(src, dst, **kwargs):
        if holding["on"] and Path(dst) == target:
            raise PermissionError(13, "held open by a peer", str(dst))
        return real_replace(src, dst, **kwargs)

    # The real 2 s retry window is allowed to expire — no clock is faked, so this
    # is the genuine "a peer held the handle for longer than we wait" case.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "replace", held_replace)
        result = bs.backlog_update_task(ids[0], "branch", "feature/held")
    assert not result.startswith("Error"), result

    assert f"tasks/{ids[0]}.md" in store.status(backlog_path).dirty_files
    assert _tasks(store.load_dict(backlog_path))[ids[0]]["branch"] == "feature/held"

    holding["on"] = False
    bs.backlog_update_task(ids[1], "branch", "feature/drain")

    assert store.status(backlog_path).dirty_files == ()
    assert _file_task(backlog_path, ids[0])["branch"] == "feature/held"
    _assert_projection_matches_rows(backlog_path)


# ── 6. linked worktree ────────────────────────────────────────────────────


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


_WORKTREE_WORKER = '''
import json, sys
from pathlib import Path

from taskmaster import backlog_server as bs
from taskmaster import store

opened = bs._store()
print(json.dumps({
    "server_backlog_path": str(bs._backlog_path()),
    "store_backlog_path": str(opened.backlog_path),
    "db_path": str(opened.db_path),
    "result": bs.backlog_update_task("core-001", "branch", "feature/from-worktree"),
}))
'''


def test_server_launched_in_a_linked_worktree_writes_only_the_main_checkout(tmp_path):
    """A worktree session must open the main checkout's store and leave its own tree alone."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    _git("init", "-b", "main", str(main))
    _git("config", "user.email", "store-tests@example.invalid", cwd=main)
    _git("config", "user.name", "Store Tests", cwd=main)
    backlog_path = _seed_project(main)
    (backlog_path / "tasks" / "core-001.md").write_text(
        "---\nid: core-001\ntitle: Alpha\nstatus: todo\nepic: core\norder: 1.0\n"
        "priority: medium\n---\n\n## Notes\n",
        encoding="utf-8",
    )
    (main / ".taskmaster" / ".gitignore").write_text("local/\n", encoding="utf-8")
    _git("add", "-A", cwd=main)
    _git("commit", "-m", "seed backlog", cwd=main)
    _git("worktree", "add", "-b", "feature/worktree-store", str(linked), cwd=main)

    tracked = {
        path: path.read_bytes()
        for path in sorted((linked / ".taskmaster").rglob("*"))
        if path.is_file()
    }
    assert tracked, "the worktree should carry a tracked .taskmaster copy"

    worker = tmp_path / "worktree_worker.py"
    worker.write_text(_WORKTREE_WORKER, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PLUGIN_ROOT)
    env.pop("TASKMASTER_ROOT", None)
    completed = subprocess.run(
        [sys.executable, str(worker)],
        cwd=str(linked),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert Path(payload["server_backlog_path"]).parent == (linked / ".taskmaster")
    assert Path(payload["store_backlog_path"]) == (main / ".taskmaster").resolve()
    assert Path(payload["db_path"]) == store.db_path((main / ".taskmaster").resolve())
    assert not payload["result"].startswith("Error"), payload["result"]
    assert not (linked / ".taskmaster" / "local" / "store.db").exists()

    for path, content in tracked.items():
        assert path.read_bytes() == content, f"the worktree copy of {path.name} was modified"
    assert _git("status", "--porcelain", cwd=linked).strip() == ""

    committed = _tasks(_committed(main / ".taskmaster"))
    assert committed["core-001"]["branch"] == "feature/from-worktree"


# ── 7. viewer and MCP tools share one transaction path ────────────────────


def test_viewer_write_and_mcp_tool_write_in_parallel_both_persist(tm_epic_phase):
    """The viewer is not a second writer: its PATCH and an MCP tool call both land."""
    root = tm_epic_phase
    backlog_path = root / ".taskmaster"
    for index in range(1, 3):
        bs.backlog_add_task(title=f"Viewer task {index}", epic="test-epic", phase="dev")
    viewer_id, tool_id = "test-epic-001", "test-epic-002"

    etag_before = bs._viewer_etag()
    errors: list[BaseException] = []

    def viewer_write() -> None:
        try:
            bs._viewer_update_task(viewer_id, {"title": "Edited in the viewer"})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def tool_write() -> None:
        try:
            bs.backlog_update_task(tool_id, "branch", "feature/from-the-tool")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(viewer_write), pool.submit(tool_write)]:
            future.result()
    assert not errors, errors

    committed = _tasks(_committed(backlog_path))
    assert committed[viewer_id]["title"] == "Edited in the viewer"
    assert committed[tool_id]["branch"] == "feature/from-the-tool"
    assert _file_task(backlog_path, viewer_id)["title"] == "Edited in the viewer"
    assert bs._viewer_etag() != etag_before
    _assert_projection_matches_rows(backlog_path)
