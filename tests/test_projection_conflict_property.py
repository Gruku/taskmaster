"""User intent: prove over generated histories, not hand-picked ones, that no value
the store or a hand edit wrote is ever lost unless an explicit resolution chose
the other side, and that the store never overwrites or deletes a quarantined or
flagged file (B-089). Covers task, epic, backlog.yaml and project.yaml files.

`TM_CONFLICT_PROPERTY_CASES` and `TM_CONFLICT_PROPERTY_SEED` widen the run.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from taskmaster import store as store_mod
from taskmaster.taskmaster_v3 import parse_frontmatter, render_frontmatter

from test_store_bug_cluster import _build_projection


CASES = int(os.environ.get("TM_CONFLICT_PROPERTY_CASES", "40"))
FIRST_SEED = int(os.environ.get("TM_CONFLICT_PROPERTY_SEED", "0"))
STEPS = 30
MARKERS = "\n<<<<<<< HEAD\nfoo: [\n=======\n>>>>>>> other\n"

# file key -> (entity kind, entity id, store field)
FILES = {
    "task": ("task", "core-001", "title"),
    "epic": ("epic", "core", "description"),
    "backlog": ("phase", "build", "name"),
    "project": ("project", "__project__", "name"),
}


@dataclass
class Side:
    token: str
    superseded: bool = False


@dataclass
class Model:
    store: dict[str, Side] = field(default_factory=dict)
    hand: dict[str, Side] = field(default_factory=dict)
    good_bytes: dict[str, bytes] = field(default_factory=dict)
    counter: int = 0

    def fresh(self, side: str) -> str:
        self.counter += 1
        return f"tok{side}{self.counter:04d}x"


def _paths(backlog_path: Path, key: str) -> list[Path]:
    if key == "task":
        return [
            backlog_path / "tasks" / "core-001.md",
            backlog_path / "tasks" / "archive" / "core-001.md",
        ]
    return [
        {
            "epic": backlog_path / "epics" / "core.md",
            "backlog": backlog_path / "backlog.yaml",
            "project": backlog_path / "project.yaml",
        }[key]
    ]


def _existing(backlog_path: Path, key: str) -> Path | None:
    return next((p for p in _paths(backlog_path, key) if p.exists()), None)


def _rel(backlog_path: Path, path: Path) -> str:
    return path.relative_to(backlog_path).as_posix()


def _get_value(key: str, text: str) -> str | None:
    if key in {"task", "epic"}:
        fm, _body = parse_frontmatter(text)
        return fm.get(FILES[key][2])
    doc = yaml.safe_load(text) or {}
    if key == "backlog":
        return doc["phases"][0].get("name")
    return doc.get("name")


def _set_value(key: str, text: str, value: str) -> str:
    if key in {"task", "epic"}:
        fm, body = parse_frontmatter(text)
        fm[FILES[key][2]] = value
        return render_frontmatter(fm, body)
    doc = yaml.safe_load(text) or {}
    if key == "backlog":
        doc["phases"][0]["name"] = value
    else:
        doc["name"] = value
    return yaml.safe_dump(doc, sort_keys=False)


def _db(store_obj, query, args=()):
    connection = sqlite3.connect(store_obj.db_path)
    try:
        return connection.execute(query, args).fetchall()
    finally:
        connection.close()


def _store_value(store_obj, key: str):
    kind, ident, name = FILES[key]
    (doc,) = _db(store_obj, "SELECT doc FROM entities WHERE kind=? AND id=?", (kind, ident))[0]
    return json.loads(doc).get(name)


def _protected_files(store_obj, backlog_path: Path) -> dict[str, bytes | None]:
    rels = {row[0] for row in _db(store_obj, "SELECT file FROM projection WHERE quarantined=1")}
    rels |= {row[0] for row in _db(store_obj, "SELECT file FROM projection_conflict")}
    out = {}
    for rel in rels:
        path = backlog_path / rel
        out[rel] = path.read_bytes() if path.exists() else None
    return out


def _setup(tmp_path: Path):
    backlog_path = _build_projection(tmp_path)
    (backlog_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "Initial project"}), encoding="utf-8"
    )
    store_obj = store_mod.open_store(backlog_path=backlog_path)
    store_obj.load_dict()
    with store_obj.transaction(tool="setup") as tx:
        epic = tx.get("epic", "core")
        epic["description"] = "Initial description"
        tx.put("epic", "core", epic)
    model = Model()
    for key in FILES:
        value = _store_value(store_obj, key)
        model.store[key] = Side(value)
        model.hand[key] = Side(value)
    return backlog_path, store_obj, model


def _scan(store_obj):
    store_obj._last_read_scan_clock = None
    store_obj.load_dict()


def _op_store_edit(rng, backlog_path, store_obj, model, key, log):
    kind, ident, name = FILES[key]
    token = model.fresh("S")
    with store_obj.transaction(tool="prop-store-edit") as tx:
        doc = tx.get(kind, ident)
        if doc.get(name) == model.hand[key].token:
            model.hand[key].superseded = True
        doc[name] = token
        tx.put(kind, ident, doc)
    model.store[key] = Side(token)
    log.append(f"store_edit {key} -> {token}")


def _op_hand_edit(rng, backlog_path, store_obj, model, key, log):
    path = _existing(backlog_path, key)
    if path is None:
        log.append(f"hand_edit {key}: no file")
        return
    raw = path.read_bytes()
    if MARKERS.encode() in raw:
        source = model.good_bytes.get(_rel(backlog_path, path))
        if source is None:
            return
        text = source.decode("utf-8")
        restore_only = rng.random() < 0.3
    else:
        text = raw.decode("utf-8")
        restore_only = False
    if _get_value(key, text) == model.store[key].token:
        model.store[key].superseded = True
    if restore_only:
        path.write_text(text, encoding="utf-8")
        log.append(f"repair {key} by restoring {_get_value(key, text)}")
        if _get_value(key, text) != model.hand[key].token:
            # Restoring an older copy is the user discarding their own later
            # edit, which only the user can do.
            model.hand[key].superseded = True
        return
    token = model.fresh("H")
    path.write_text(_set_value(key, text, token), encoding="utf-8")
    model.hand[key] = Side(token)
    log.append(f"hand_edit {key} -> {token}{' (repair)' if MARKERS.encode() in raw else ''}")


def _op_break(rng, backlog_path, store_obj, model, key, log):
    path = _existing(backlog_path, key)
    if path is None:
        return
    raw = path.read_bytes()
    if MARKERS.encode() in raw:
        return
    model.good_bytes[_rel(backlog_path, path)] = raw
    path.write_bytes(raw + MARKERS.encode())
    log.append(f"break {key}")


def _op_archive(rng, backlog_path, store_obj, model, key, log):
    with store_obj.transaction(tool="prop-archive") as tx:
        if rng.random() < 0.5:
            tx.archive("task", "core-001")
            log.append("archive task")
        else:
            tx.unarchive("task", "core-001")
            log.append("unarchive task")


def _op_resolve(rng, backlog_path, store_obj, model, key, log):
    conflicts = store_obj.projection_conflicts()
    if not conflicts:
        return
    conflict = rng.choice(conflicts)
    rel = conflict["file"]
    key = next(
        k for k in FILES
        if any(_rel(backlog_path, p) == rel for p in _paths(backlog_path, k))
    )
    take = rng.choice(["file", "store"])
    try:
        store_obj.resolve_projection_conflict(rel, take)
    except ValueError as exc:
        log.append(f"resolve {rel} {take} refused: {exc}")
        return
    if take == "file":
        model.store[key].superseded = True
    else:
        model.hand[key].superseded = True
    log.append(f"resolve {rel} take={take}")


OPS = [
    (_op_store_edit, 4),
    (_op_hand_edit, 4),
    (_op_break, 2),
    (_op_archive, 1),
    (_op_resolve, 2),
]


def _check(backlog_path, store_obj, model, log, seed):
    for key in FILES:
        store_value = _store_value(store_obj, key)
        on_disk = b"".join(
            p.read_bytes() for p in _paths(backlog_path, key) if p.exists()
        ).decode("utf-8")
        for side_name, side in (("store", model.store[key]), ("hand", model.hand[key])):
            if side.superseded:
                continue
            assert side.token == store_value or side.token in on_disk, (
                f"seed {seed}: {side_name} value {side.token!r} for {key} was lost "
                f"(store holds {store_value!r}); history:\n  " + "\n  ".join(log)
            )


def _run(seed: int, tmp_path: Path) -> set[str]:
    rng = random.Random(seed)
    backlog_path, store_obj, model = _setup(tmp_path)
    log: list[str] = []
    ever_flagged: set[str] = set()
    functions = [op for op, weight in OPS for _ in range(weight)]
    for _step in range(STEPS):
        op = rng.choice(functions)
        key = rng.choice(list(FILES))
        protected = _protected_files(store_obj, backlog_path)
        before = len(log)
        op(rng, backlog_path, store_obj, model, key, log)
        _scan(store_obj)
        touched_by_user = op in (_op_hand_edit, _op_break)
        resolved = [
            entry.split()[1] for entry in log[before:]
            if entry.startswith("resolve ") and "take=store" in entry
        ]
        for rel, content in protected.items():
            path = backlog_path / rel
            if rel in resolved:
                continue
            if touched_by_user and any(
                _rel(backlog_path, p) == rel for p in _paths(backlog_path, key)
            ):
                continue
            current = path.read_bytes() if path.exists() else None
            assert current == content, (
                f"seed {seed}: the store changed protected file {rel}; history:\n  "
                + "\n  ".join(log)
            )
        _check(backlog_path, store_obj, model, log, seed)
        ever_flagged.update(c["file"] for c in store_obj.projection_conflicts())
    return ever_flagged


def test_generated_histories_lose_nothing_and_never_touch_protected_files(tmp_path):
    flagged: set[str] = set()
    for seed in range(FIRST_SEED, FIRST_SEED + CASES):
        flagged |= {
            "tasks/core-001.md" if rel.startswith("tasks/") else rel
            for rel in _run(seed, tmp_path / f"seed-{seed}")
        }
    # The generator has to reach the state under test for every file kind, or
    # it proves nothing about that kind.
    assert flagged >= {"tasks/core-001.md", "epics/core.md", "backlog.yaml", "project.yaml"}, flagged
