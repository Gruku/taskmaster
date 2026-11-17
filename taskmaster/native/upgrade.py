# User intent: move an activated native store to a newer schema only when a team feature needs it, atomically and under the single writer, so solo stores stay downgradable and older builds refuse upgraded ones cleanly.
"""Opt-in native schema upgrade (team-identity-001 D21).

Never called at coordinator start or on open. The coordinator's writer thread
calls it (the identity gate, right before the first schema-4 row), and so does
`taskmaster serve` before its coordinator starts (a served store is a team store,
security review m-4); it needs no lock beyond SQLite's own `BEGIN IMMEDIATE`.
"""
from __future__ import annotations

import sqlite3

from . import db, schema
from .migrate import _put_manifest


def upgrade(connection: sqlite3.Connection, to: int = schema.VERSION) -> dict:
    """One transaction from the store's schema to `to`; {"from": v, "to": w}.

    Already at `to` or above: returns without writing. Any failure rolls back,
    leaving the store at its old version.
    """
    if connection.in_transaction:
        raise RuntimeError("native upgrade requires no open transaction")
    if to not in schema.SUPPORTED:
        raise ValueError(f"native schema {to} is not supported by this build")
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = int(db.manifest(connection, authorities=("native",))["schema_version"])
        if current >= to:
            connection.rollback()
            return {"from": current, "to": current}
        for version in range(current, to):
            schema.upgrade_native(connection, version)
        _put_manifest(connection, schema_version=to)
        connection.commit()
        return {"from": current, "to": to}
    except BaseException:
        connection.rollback()
        raise
