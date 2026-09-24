# User intent: let the N15 cutover tests run before track B's primitives land. Test-only
# placeholders for migrate.{snapshot_carryover,verify_carryover,import_id_state,
# reconcile_progress} (installed only when missing) and a quiet taskmaster.native.quiesce.
"""Seams for `taskmaster.native.cutover`; never imported by production code."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import types

PREFIXES = {"bug": "B-", "issue": "ISS-", "decision": "DEC-", "idea": "IDEA-", "note": "NOTE-"}
CARRY_TABLES = ("linear_queue", "projection", "projection_base", "sessions", "changes")


def snapshot_carryover(connection) -> dict:
    return {table: [list(r) for r in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
            for table in CARRY_TABLES}


def verify_carryover(connection, before) -> list[str]:
    after = json.loads(json.dumps(snapshot_carryover(connection), default=repr))
    return [f"{table} changed" for table in CARRY_TABLES if after.get(table) != before.get(table)]


def import_id_state(connection, root) -> None:
    reserved = {}
    reservations = Path(root) / ".taskmaster" / "local" / "id-reservations.json"
    if reservations.exists():
        reserved = json.loads(reservations.read_text(encoding="utf-8"))
    for kind, prefix in PREFIXES.items():
        ids = [row[0] for row in connection.execute("SELECT public_id FROM entity_core WHERE kind=?", (kind,))]
        ids += list(reserved.get(kind, []))
        high = max((int(m.group(1)) for m in (re.fullmatch(re.escape(prefix) + r"(\d+)", i) for i in ids) if m),
                   default=0)
        connection.execute("INSERT INTO id_counters VALUES(?,?,?)", (kind, prefix, high))
    for kind, ids in reserved.items():
        connection.executemany("INSERT OR IGNORE INTO id_reservations VALUES(?,?)", [(kind, i) for i in ids])


def reconcile_progress(connection) -> None:
    return None


def quiet_quiesce() -> types.ModuleType:
    module = types.ModuleType("taskmaster.native.quiesce")
    module.live_owner = lambda root: None
    module.open_writers = lambda db_path: False
    module.scan_processes = lambda root: []
    return module


PLACEHOLDERS = {"snapshot_carryover": snapshot_carryover, "verify_carryover": verify_carryover,
                "import_id_state": import_id_state, "reconcile_progress": reconcile_progress}


def install(monkeypatch=None):
    """Fill missing track-B functions and install a quiet quiesce module; returns the latter."""
    from taskmaster.native import migrate
    quiesce = quiet_quiesce()
    for name, function in PLACEHOLDERS.items():
        if not hasattr(migrate, name):
            if monkeypatch is None:
                setattr(migrate, name, function)
            else:
                monkeypatch.setattr(migrate, name, function, raising=False)
    if monkeypatch is None:
        sys.modules["taskmaster.native.quiesce"] = quiesce
    else:
        monkeypatch.setitem(sys.modules, "taskmaster.native.quiesce", quiesce)
    return quiesce
