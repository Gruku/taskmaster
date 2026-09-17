"""User intent: prove routed tools are compatible by running the same public call
against a legacy project and a byte-identical copy activated as a native authority,
then comparing the text answers, the committed entities and the projected files.
Test-only activation: no runtime entry point makes a project native.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

import yaml

from taskmaster import backlog_server as bs
from taskmaster import store
from taskmaster.native.migrate import backfill, reconstruct_entities
from taskmaster.native_routing import projection

PREFIXES = {"bug": "B-", "issue": "ISS-", "decision": "DEC-", "idea": "IDEA-", "note": "NOTE-"}


def scaffold(root: Path) -> Path:
    """The `tmp_taskmaster` layout, at an arbitrary directory."""
    tm_dir = root / ".taskmaster"
    for subdir in ("tasks", "handovers", "issues", "ideas", "local", "local/cache"):
        (tm_dir / subdir).mkdir(parents=True, exist_ok=True)
    (tm_dir / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    (tm_dir / "local" / "PROGRESS.md").write_text("## Changelog\n", encoding="utf-8")
    backlog = {"version": 3, "project": "test-project", "meta": {"schema_version": 4},
               "epics": [], "phases": [], "context": {}}
    (tm_dir / "backlog.yaml").write_text(yaml.dump(backlog), encoding="utf-8")
    return root


def point_server_at(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(bs, "ROOT", root)
    monkeypatch.setattr(bs, "CONFIG_PATH", root / ".taskmaster" / "taskmaster.json")
    monkeypatch.setattr(bs, "LEGACY_CONFIG_PATH", root / ".claude" / "taskmaster.json")
    monkeypatch.chdir(root)
    store.reset_for_tests()
    projection.reset_for_tests()


def activate_native(root: Path) -> None:
    """Backfill a legacy project's store and mark it a ready native authority.

    Mirrors the test-only activation in `test_native_commands.native`, plus the
    id high-water import a real cutover must perform: every allocated prefix is
    seeded from the live rows and the legacy reservation file.
    """
    store.reset_for_tests()
    database = root / ".taskmaster" / "local" / "store.db"
    reserved = {}
    reservations = database.parent / "id-reservations.json"
    if reservations.exists():
        reserved = json.loads(reservations.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(database, isolation_level=None, timeout=30)) as connection:
        backfill(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        connection.execute("INSERT INTO meta(key,value) VALUES('minimum_client_protocol','2') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
        connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
        connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1') "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        for kind, prefix in PREFIXES.items():
            ids = [row[0] for row in connection.execute(
                "SELECT public_id FROM entity_core WHERE kind=?", (kind,))]
            ids += list(reserved.get(kind, []))
            high = max((int(m.group(1)) for m in (re.fullmatch(re.escape(prefix) + r"(\d+)", i) for i in ids) if m),
                       default=0)
            connection.execute("INSERT INTO id_counters VALUES(?,?,?)", (kind, prefix, high))
        for kind, ids in reserved.items():
            connection.executemany("INSERT OR IGNORE INTO id_reservations VALUES(?,?)",
                                   [(kind, ident) for ident in ids])
        connection.commit()


def is_native(root: Path) -> bool:
    database = root / ".taskmaster" / "local" / "store.db"
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"


def committed(root: Path) -> dict:
    """`{(kind, id): (doc, body, archived)}` from whichever authority owns the store."""
    store.reset_for_tests()
    database = root / ".taskmaster" / "local" / "store.db"
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        if is_native(root):
            connection.execute("BEGIN")
            try:
                entities = [(r["kind"], r["id"], r["doc"], r["body"], r["archived"], r["deleted"])
                            for r in reconstruct_entities(connection)]
            finally:
                connection.rollback()
        else:
            entities = [(k, i, json.loads(d), b, a, x) for k, i, d, b, a, x in connection.execute(
                "SELECT kind,id,doc,body,archived,deleted FROM entities")]
    return {(k, i): (d, b, bool(a)) for k, i, d, b, a, x in entities if not x}


_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?")
_SEQ = re.compile(r" \[seq \d+\]")


def normalize(value):
    """Clock readings and commit sequences are not compared; everything else is."""
    if isinstance(value, str):
        return _SEQ.sub(" [seq #]", _STAMP.sub("<ts>", value))
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k != "seq"}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def projected_files(root: Path) -> dict:
    base = root / ".taskmaster"
    files = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if path.is_file() and not rel.startswith("local/") and ".tmp." not in rel:
            files[rel] = normalize(path.read_bytes().decode("utf-8"))
    return files


class Twins:
    """A legacy project and its native copy, driven through the same public calls."""

    def __init__(self, monkeypatch, legacy: Path, native: Path):
        self.monkeypatch, self.legacy, self.native = monkeypatch, legacy, native

    @contextmanager
    def at(self, root: Path):
        point_server_at(self.monkeypatch, root)
        try:
            yield
        finally:
            store.reset_for_tests()

    def call(self, name: str, *args, **kwargs):
        results = []
        for root in (self.legacy, self.native):
            with self.at(root):
                results.append(getattr(bs, name)(*args, **kwargs))
        return tuple(results)

    def same(self, name: str, *args, **kwargs):
        legacy, native = self.call(name, *args, **kwargs)
        assert normalize(native) == normalize(legacy), f"{name}: native answer diverged\nlegacy: {legacy!r}\nnative: {native!r}"
        return legacy, native

    def assert_state_matches(self) -> None:
        legacy, native = committed(self.legacy), committed(self.native)
        assert sorted(native) == sorted(legacy), "entity sets diverged"
        for key in sorted(legacy):
            (l_doc, l_body, l_arch), (n_doc, n_body, n_arch) = normalize(legacy[key]), normalize(native[key])
            fields = sorted(f for f in set(l_doc) | set(n_doc) if l_doc.get(f, "<absent>") != n_doc.get(f, "<absent>"))
            assert not fields, f"{key} fields diverged: " + "; ".join(
                f"{f}: legacy={l_doc.get(f, '<absent>')!r} native={n_doc.get(f, '<absent>')!r}" for f in fields)
            assert (n_body, n_arch) == (l_body, l_arch), f"{key} body/archive flag diverged"

    def assert_files_match(self) -> None:
        legacy, native = projected_files(self.legacy), projected_files(self.native)
        assert sorted(native) == sorted(legacy)
        for rel in legacy:
            assert native[rel] == legacy[rel], f"projection of {rel} diverged"


def make_twins(tmp_path: Path, monkeypatch, seed=None) -> Twins:
    """Seed a legacy project through the legacy tools, copy it, activate the copy."""
    legacy = scaffold(tmp_path / "legacy")
    point_server_at(monkeypatch, legacy)
    bs.backlog_add_epic(epic_id="test-epic", name="Test Epic", done_when="all test tasks complete")
    bs.backlog_add_phase(phase_id="dev", name="Development")
    if seed is not None:
        seed()
    store.reset_for_tests()
    native = tmp_path / "native"
    shutil.copytree(legacy, native)
    for leftover in native.rglob("*.tmp.*"):
        os.remove(leftover)
    activate_native(native)
    return Twins(monkeypatch, legacy, native)
