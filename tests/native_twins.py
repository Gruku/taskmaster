"""User intent: prove routed tools are compatible by running the same public call
against a legacy project and a byte-identical copy activated as a native authority,
then comparing the text answers, the committed entities and the projected files.
Test-only activation: no runtime entry point makes a project native.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import datetime as _datetime
import importlib
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
from taskmaster.native_routing.derived import KEYS as DERIVED_BACKLOG_KEYS



# ── A shared clock ──────────────────────────────────────────────────────────
# The twins run one call against the legacy project and then the native one, a
# few milliseconds apart. Second-resolution timestamps then order entities
# differently whenever a second boundary falls between the two runs, and a
# comparison of index order flakes. Both runs of a call read the same instant.

_REAL_DATETIME, _REAL_DATE = _datetime.datetime, _datetime.date
CLOCK = {"at": _REAL_DATETIME(2026, 9, 17, 12, 0, 0, tzinfo=_datetime.timezone.utc), "tick": True}
CLOCK_MODULES = ("taskmaster.taskmaster_v3", "taskmaster.native.domain", "taskmaster.native.events",
                 "taskmaster.native.claims",
                 "taskmaster.native.workflow", "taskmaster.backlog_server", "taskmaster.store",
                 "taskmaster.native_routing.epics_phases")


class _RealCheck(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, cls.__real__)

    def __subclasscheck__(cls, subclass):
        return issubclass(subclass, cls.__real__)


def _instant():
    at = CLOCK["at"]
    if CLOCK["tick"]:
        CLOCK["at"] = at + _datetime.timedelta(seconds=1)
    return at


class FakeDatetime(_REAL_DATETIME, metaclass=_RealCheck):
    __real__ = _REAL_DATETIME

    @classmethod
    def now(cls, tz=None):
        at = _instant()
        return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return _instant().replace(tzinfo=None)


class FakeDate(_REAL_DATE, metaclass=_RealCheck):
    __real__ = _REAL_DATE

    @classmethod
    def today(cls):
        return _instant().date()


def install_clock(monkeypatch) -> None:
    """Every module that timestamps entities reads `CLOCK`; seeding ticks per read."""
    CLOCK.update(at=_REAL_DATETIME(2026, 9, 17, 12, 0, 0, tzinfo=_datetime.timezone.utc), tick=True)
    for name in CLOCK_MODULES:
        module = importlib.import_module(name)
        if getattr(module, "datetime", None) is _REAL_DATETIME:
            monkeypatch.setattr(module, "datetime", FakeDatetime)
        if getattr(module, "date", None) is _REAL_DATE:
            monkeypatch.setattr(module, "date", FakeDate)


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
    # The one-shot legacy handover-status backfill is a process-global latch, so it
    # would run in whichever test first touched a handover. Native stores never run
    # it (a cutover must), and twins must not depend on test order.
    monkeypatch.setattr(bs, "_HANDOVER_STATUS_BACKFILL_RAN", True)
    # The session bundle is process-global too, and `test_bundle_pick` leaves it set:
    # it names every auto-derived handover thread, so it must not leak in.
    monkeypatch.setattr(bs, "_session_bundle", None)
    store.reset_for_tests()
    projection.reset_for_tests()


def activate_native(root: Path) -> dict:
    """Backfill a legacy project's store and flip it to a ready native authority
    through the production activation core (`native.cutover.activate`), which runs
    the production ID import (`carryover.import_id_state`) and progress reconcile.

    Fast path for fixtures: no fence, backup or process checks — but authority
    flips through exactly the function the real cutover commits with. With
    `TASKMASTER_TWINS_VERIFY=1` the full carry-over oracle runs too: a snapshot
    before backfill (JSON round-tripped, as the cutover journals it) and
    `verify_carryover` inside the activation transaction. Returns the activation result
    (its `seeded_bases` are the merge bases activation added after verifying, N16).
    """
    from taskmaster.native import carryover, cutover
    store.reset_for_tests()
    database = root / ".taskmaster" / "local" / "store.db"
    with closing(sqlite3.connect(database, isolation_level=None, timeout=30)) as connection:
        before = None
        if os.environ.get("TASKMASTER_TWINS_VERIFY") == "1":
            before = json.loads(json.dumps(carryover.snapshot_carryover(connection)))
        backfill(connection)
        return cutover.activate(connection, root, before=before)


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


_SEQ = re.compile(r" \[seq \d+\]")


def normalize(value):
    """Commit sequences are not compared; everything else is, timestamps included —
    both runs of a call read one shared clock (see `CLOCK`)."""
    if isinstance(value, str):
        return _SEQ.sub(" [seq #]", value)
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

    def __init__(self, monkeypatch, legacy: Path, native: Path, *, visibility='legacy', engine_oracle=False):
        self.monkeypatch, self.legacy, self.native = monkeypatch, legacy, native
        self.visibility, self.engine_oracle = visibility, engine_oracle

    @contextmanager
    def at(self, root: Path):
        point_server_at(self.monkeypatch, root)
        try:
            with self.monkeypatch.context() as local:
                if root == self.native:
                    if self.engine_oracle:
                        from taskmaster.native_routing import runtime
                        from tests.native_projection_oracle import open_call
                        local.setattr(runtime, 'open_call', open_call)
                    elif self.visibility == 'legacy':
                        from taskmaster.coordinator import adapter
                        from tests.native_coordinator_helpers import compatibility_client
                        local.setattr(adapter, 'Client', compatibility_client)
                yield
        finally:
            store.reset_for_tests()

    def call(self, tool: str, /, *args, **kwargs):
        results = []
        # One instant for both runs of this call, a minute after the last one.
        instant = CLOCK["at"] + _datetime.timedelta(minutes=1)
        for root in (self.legacy, self.native):
            CLOCK.update(at=instant, tick=False)
            with self.at(root):
                results.append(getattr(bs, tool)(*args, **kwargs))
        CLOCK.update(at=instant, tick=False)
        return tuple(results)

    def _rooted(self, value, root):
        """An answer with its own project root spelled `<root>`: the twins live apart."""
        if isinstance(value, str):
            for spelling in {str(root.resolve()), str(root)}:
                value = value.replace(spelling, "<root>")
        return value

    def same(self, tool: str, /, *args, **kwargs):
        legacy, native = self.call(tool, *args, **kwargs)
        assert normalize(self._rooted(native, self.native)) == normalize(self._rooted(legacy, self.legacy)), (
            f"{tool}: native answer diverged" + chr(10) + f"legacy: {legacy!r}" + chr(10) + f"native: {native!r}")
        return legacy, native

    def assert_state_matches(self, ignore=None) -> None:
        """`ignore` names `{(kind, id): {field, ...}}` a test has already asserted differ."""
        legacy, native = committed(self.legacy), committed(self.native)
        for key, fields in (ignore or {}).items():
            for side in (legacy, native):
                if key in side:
                    doc, body, archived = side[key]
                    side[key] = ({k: v for k, v in doc.items() if k not in fields}, body, archived)
        assert sorted(native) == sorted(legacy), "entity sets diverged"
        for key in sorted(legacy):
            (l_doc, l_body, l_arch), (n_doc, n_body, n_arch) = normalize(legacy[key]), normalize(native[key])
            if key == ("backlog", "__backlog__"):
                # Derived on native stores; compared through the rendered backlog.yaml.
                l_doc = {k: v for k, v in l_doc.items() if k not in DERIVED_BACKLOG_KEYS}
                n_doc = {k: v for k, v in n_doc.items() if k not in DERIVED_BACKLOG_KEYS}
            fields = sorted(f for f in set(l_doc) | set(n_doc) if l_doc.get(f, "<absent>") != n_doc.get(f, "<absent>"))
            assert not fields, f"{key} fields diverged: " + "; ".join(
                f"{f}: legacy={l_doc.get(f, '<absent>')!r} native={n_doc.get(f, '<absent>')!r}" for f in fields)
            assert (n_body, n_arch) == (l_body, l_arch), f"{key} body/archive flag diverged"

    def assert_files_match(self, ignore=()) -> None:
        """`ignore` names projection files a test has already asserted differ."""
        legacy, native = projected_files(self.legacy), projected_files(self.native)
        assert sorted(native) == sorted(legacy)
        for rel in legacy:
            if rel in ignore:
                continue
            assert native[rel] == legacy[rel], f"projection of {rel} diverged"


def hand_edit_entity(kind: str, ident: str, change) -> None:
    """`hand_edit_task` for any kind: one row of the legacy store, edited raw."""
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db",
                                 isolation_level=None)) as connection:
        doc = json.loads(connection.execute(
            "SELECT doc FROM entities WHERE kind=? AND id=?", (kind, ident)).fetchone()[0])
        change(doc)
        connection.execute("UPDATE entities SET doc=? WHERE kind=? AND id=?",
                           (json.dumps(doc), kind, ident))
    store.reset_for_tests()


def native_database(root: Path) -> Path:
    return root / ".taskmaster" / "local" / "store.db"


@contextmanager
def native_connection(root: Path):
    """An autocommit connection to a native project's store, as the runtime opens one."""
    with closing(sqlite3.connect(native_database(root), isolation_level=None, timeout=30,
                                 check_same_thread=False)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        yield connection


_REQUESTS = iter(range(1, 1 << 30))


def commit_only(connection, operation: str, arguments: dict) -> dict:
    """Run one native command and commit it, without the export drain a tool call adds."""
    from taskmaster.native import commands
    store_id = connection.execute("SELECT value FROM native_manifest WHERE key='store_id'").fetchone()[0]
    return commands.execute(connection, {
        "protocol": 2, "store_id": store_id, "caller_scope": "tests", "request_id": f"t-{next(_REQUESTS)}",
        "operation": operation, "arguments": arguments, "expected_revisions": []})


def hand_edit_task(ident: str, change) -> None:
    """Hand-edit one task row of the legacy project the server points at, the way
    a hand-edited or migrated backlog arrives — e.g. a `locked_by` no tool may
    write any more. Run it inside a seed, before the twins are copied, so both
    stores carry the edit."""
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db",
                                 isolation_level=None)) as connection:
        doc = json.loads(connection.execute(
            "SELECT doc FROM entities WHERE kind='task' AND id=?", (ident,)).fetchone()[0])
        change(doc)
        connection.execute("UPDATE entities SET doc=? WHERE kind='task' AND id=?",
                           (json.dumps(doc), ident))
    store.reset_for_tests()


def hand_set_holder(ident: str, holder: str) -> None:
    """`hand_edit_task` writing only `locked_by`: a holder no pick stamped."""
    hand_edit_task(ident, lambda doc: doc.__setitem__("locked_by", holder))


def make_twins(tmp_path: Path, monkeypatch, seed=None, *, visibility='legacy', engine_oracle=False) -> Twins:
    """Seed a legacy project through the legacy tools, copy it, activate the copy."""
    install_clock(monkeypatch)
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
    return Twins(monkeypatch, legacy, native, visibility=visibility, engine_oracle=engine_oracle)
