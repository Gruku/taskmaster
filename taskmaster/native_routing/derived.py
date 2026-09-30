# User intent: the backlog row's bug/issue/tracker/handover indexes and thread
# registry are read projections of the entity rows; on a native store derive them
# at read and export time instead of trusting the copy frozen at backfill (N08).
"""Derived backlog-document fields for native reads and the backlog.yaml export.

The legacy tools rewrite these keys into the backlog row whenever they touch the
kind they summarise, so the row always looked current. Native commands do not
maintain them there — they are derived state, and a stored copy is only ever
stale. `apply` recomputes them from the live rows with the tools' own sync
functions, so the rendered document is the one the legacy row would have held.

A key exists exactly when the legacy row would carry it: once the tools have
synced that kind at least once, which native stores observe as the key already
present or a row of that kind existing.
"""
from __future__ import annotations

import json

from taskmaster.native.migrate import encode
from taskmaster.taskmaster_v3 import (
    sync_bug_index,
    sync_handover_index,
    sync_issue_index,
    sync_tracker_index,
)

INDEXES = (("bug", "bugs", sync_bug_index), ("issue", "issues", sync_issue_index),
           ("tracker", "trackers", sync_tracker_index))
# `threads` and `thread_meta` ride along with the handover index.
KEYS = ("bugs", "issues", "trackers", "handovers", "threads", "thread_meta")
KINDS = ("bug", "issue", "tracker", "handover")


def live_rows(connection, snapshot, kind) -> list:
    """`(id, fields, None)` for every live, unarchived row of a kind, in id order.

    Paged (`list` orders by kind then id and filters archived and deleted rows
    exactly as the old per-id `get` loop did), so the cost is a few queries per
    kind rather than one `get` per row.
    """
    from taskmaster.native.queries import MAX_PAGE
    out, cursor = [], None
    while True:
        result = snapshot.list(kind, fields=None, limit=MAX_PAGE, cursor=cursor)
        out.extend((entity["id"], entity["fields"], None) for entity in result["items"])
        cursor = result["cursor"]
        if cursor is None:
            return out


def _any(connection, kind) -> bool:
    return connection.execute("SELECT 1 FROM entity_core WHERE kind=? AND deleted=0 LIMIT 1",
                              (kind,)).fetchone() is not None


def apply(snapshot, data: dict) -> dict:
    """Recompute the derived keys on a backlog document in place; keys stay sorted."""
    connection = snapshot.connection
    for kind, key, sync in INDEXES:
        if key in data or _any(connection, kind):
            sync(data, live_rows(connection, snapshot, kind))
    if "handovers" in data or _any(connection, "handover"):
        sync_handover_index(data, live_rows(connection, snapshot, "handover"))
    # The legacy row is stored as sorted JSON and read back before it renders, so
    # every nested mapping arrives key-sorted; the sync functions build theirs in
    # field order.
    ordered = {key: (json.loads(encode(value)) if key in KEYS else value) for key, value in sorted(data.items())}
    data.clear()
    data.update(ordered)
    return data
