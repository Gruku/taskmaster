# User intent: every linked Git checkout keeps its own observed projection bases and
# holds (N13 step 9), so a linked file is never judged against the main checkout's
# bytes, and bases are bounded and never dropped while their checkout still exists.
"""Per-checkout projection bases and holds for linked worktrees.

The main checkout keeps `projection`/`projection_base`. A linked checkout's base is
the exact bytes last observed or published there, stored content-addressed
(`checkout_blob`) because they are nearly always the published bytes shared by
several checkouts. A hold (drift, conflict, quarantined) stops publication into
that one path of that one checkout. Callers own the transaction.
"""
from __future__ import annotations

import hashlib
import json

DDL = (
    "CREATE TABLE IF NOT EXISTS checkout_blob(digest TEXT PRIMARY KEY, content BLOB NOT NULL)",
    "CREATE TABLE IF NOT EXISTS checkout_base(checkout TEXT NOT NULL, file TEXT NOT NULL, digest TEXT NOT NULL, "
    "PRIMARY KEY(checkout,file))",
    "CREATE TABLE IF NOT EXISTS checkout_hold(checkout TEXT NOT NULL, file TEXT NOT NULL, reason TEXT NOT NULL, "
    "digest TEXT, PRIMARY KEY(checkout,file))",
)
STATE_PREFIX = "checkout."
MAIN = "main"
HOLD_REASONS = {"drift", "conflict", "quarantined"}


def digest(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def ensure(connection) -> None:
    for statement in DDL:
        connection.execute(statement)


def _exists(connection) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='checkout_base'").fetchone() \
        is not None


def base(connection, checkout: str, rel: str) -> tuple[str, bytes] | None:
    """(digest, bytes) of the checkout's base for `rel`, or None."""
    if not _exists(connection):
        return None
    row = connection.execute("SELECT b.digest,c.content FROM checkout_base b JOIN checkout_blob c ON c.digest=b.digest "
                             "WHERE b.checkout=? AND b.file=?", (checkout, rel)).fetchone()
    return None if row is None else (row[0], bytes(row[1]))


def trusted_bases(connection, checkout: str) -> dict[str, str]:
    """{rel: digest} of the bases `base` would return (their blob is retained)."""
    if not _exists(connection):
        return {}
    return dict(connection.execute("SELECT b.file,b.digest FROM checkout_base b JOIN checkout_blob c "
                                   "ON c.digest=b.digest WHERE b.checkout=?", (checkout,)))


def bases(connection, checkout: str) -> dict[str, str]:
    if not _exists(connection):
        return {}
    return dict(connection.execute("SELECT file,digest FROM checkout_base WHERE checkout=?", (checkout,)))


def set_base(connection, checkout: str, rel: str, content: bytes | None) -> None:
    """Record (or, with None, forget) the base; unreferenced blobs go in the same transaction."""
    ensure(connection)
    old = connection.execute("SELECT digest FROM checkout_base WHERE checkout=? AND file=?", (checkout, rel)).fetchone()
    if content is None:
        connection.execute("DELETE FROM checkout_base WHERE checkout=? AND file=?", (checkout, rel))
    else:
        value = digest(content)
        connection.execute("INSERT OR IGNORE INTO checkout_blob(digest,content) VALUES(?,?)", (value, content))
        connection.execute("INSERT INTO checkout_base(checkout,file,digest) VALUES(?,?,?) ON CONFLICT(checkout,file) "
                           "DO UPDATE SET digest=excluded.digest", (checkout, rel, value))
    if old is not None:
        _collect(connection, old[0])


def _collect(connection, value: str) -> None:
    if connection.execute("SELECT 1 FROM checkout_base WHERE digest=? LIMIT 1", (value,)).fetchone() is None:
        connection.execute("DELETE FROM checkout_blob WHERE digest=?", (value,))


def holds(connection, checkout: str) -> dict[str, tuple[str, str | None]]:
    if not _exists(connection):
        return {}
    return {rel: (reason, value) for rel, reason, value in connection.execute(
        "SELECT file,reason,digest FROM checkout_hold WHERE checkout=?", (checkout,))}


def hold(connection, checkout: str, rel: str) -> tuple[str, str | None] | None:
    if not _exists(connection):
        return None
    row = connection.execute("SELECT reason,digest FROM checkout_hold WHERE checkout=? AND file=?",
                             (checkout, rel)).fetchone()
    return None if row is None else (row[0], row[1])


def set_hold(connection, checkout: str, rel: str, reason: str | None, value: str | None = None) -> None:
    ensure(connection)
    if reason is None:
        connection.execute("DELETE FROM checkout_hold WHERE checkout=? AND file=?", (checkout, rel))
        return
    if reason not in HOLD_REASONS:
        raise ValueError(f"invalid checkout hold {reason!r}")
    connection.execute("INSERT INTO checkout_hold(checkout,file,reason,digest) VALUES(?,?,?,?) "
                       "ON CONFLICT(checkout,file) DO UPDATE SET reason=excluded.reason,digest=excluded.digest",
                       (checkout, rel, reason, value))


def forget(connection, checkout: str) -> None:
    """Drop every row of a checkout Git no longer has (its admin directory is gone)."""
    if not _exists(connection):
        return
    digests = [row[0] for row in connection.execute("SELECT DISTINCT digest FROM checkout_base WHERE checkout=?",
                                                    (checkout,))]
    connection.execute("DELETE FROM checkout_base WHERE checkout=?", (checkout,))
    connection.execute("DELETE FROM checkout_hold WHERE checkout=?", (checkout,))
    for value in digests:
        _collect(connection, value)
    connection.execute("DELETE FROM sync_state WHERE key=?", (STATE_PREFIX + checkout,))


def token(connection, checkout: str, rel: str) -> str:
    """The linked-checkout analogue of `sync.manifest_token`: its base and hold."""
    found = base(connection, checkout, rel)
    held = hold(connection, checkout, rel)
    state = {"checkout": checkout, "base": None if found is None else found[0],
             "hold": None if held is None else list(held)}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record(connection, checkout: str) -> dict | None:
    row = connection.execute("SELECT value_json FROM sync_state WHERE key=?", (STATE_PREFIX + checkout,)).fetchone()
    return None if row is None else json.loads(row[0])


def put_record(connection, checkout: str, value: dict | None) -> None:
    if value is None:
        connection.execute("DELETE FROM sync_state WHERE key=?", (STATE_PREFIX + checkout,))
        return
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                       "value_json=excluded.value_json", (STATE_PREFIX + checkout, json.dumps(value, sort_keys=True)))


def records(connection) -> dict[str, dict]:
    return {key[len(STATE_PREFIX):]: json.loads(value) for key, value in connection.execute(
        "SELECT key,value_json FROM sync_state WHERE key>=? AND key<?", (STATE_PREFIX, STATE_PREFIX[:-1] + "/"))}
