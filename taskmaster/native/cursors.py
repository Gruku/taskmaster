# User intent: one opaque change cursor that both stores issue and check, so an
# agent asking "what moved since I last looked" is never answered with a silent
# replay — a cursor that can no longer be honoured names its reason instead.
"""The change-feed cursor: rebuild fence, scope fingerprint, retention floor.

Deliberately **not** fenced on `event_high_water`. The entity list cursor
(`queries.Snapshot._continuation`) binds to it so a half-finished paged snapshot
read stays coherent; a change cursor that did the same would be invalid on every
call in an active project, and surviving writes is this cursor's whole job.

Pure: no sqlite, no store handle. Both the native adapter and the legacy
implementation of `backlog_changes_since` issue, check and present through here,
so the two paths cannot drift on the one contract that matters — what a cursor
promises and what a caller sees when it can no longer be kept.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from . import schema
from .migrate import encode

VERSION = 1
MAX_LENGTH = 4096
MAX_FILTER = 100
MAX_ID = 256
# Native-only `sync_state` key. Nothing prunes `domain_events` (D4): pruning it
# would break the migration oracle that compares it row-for-row against the
# legacy `changes` table, so expiry is expressed by raising this floor.
FLOOR_KEY = "change_history_floor"
MAX_LIMIT = 500
# A legacy store has no `source_digest`; it stands in for one so a cursor issued
# against a project cannot be resumed against its native copy, or the reverse.
LEGACY_DIGEST = "legacy"

# In precedence order: the most fundamental reason a cursor fails is the one named.
REASONS = ("cursor_unreadable", "store_rebuilt", "history_rewound", "scope_changed", "history_expired")


class CursorInvalid(ValueError):
    """This continuation cannot be honoured; the caller must resync.

    Never surfaced as a tool error: the change feed answers `resync_required`
    with this `reason` and a fresh cursor, because an error branch for a routine
    condition is a branch every skill would have to carry.
    """

    reason = "cursor_unreadable"


class CursorUnreadable(CursorInvalid):
    reason = "cursor_unreadable"


class StoreRebuilt(CursorInvalid):
    reason = "store_rebuilt"


class HistoryRewound(CursorInvalid):
    """The cursor points past the end of the store's history.

    A store restored from a backup keeps its identity, so the rebuild fence
    passes; only the sequence shows the history went backwards. Resuming would
    skip every event written into the gap, because the next write reuses a
    sequence the cursor already claims to have seen.
    """

    reason = "history_rewound"


class ScopeChanged(CursorInvalid):
    reason = "scope_changed"


class HistoryExpired(CursorInvalid):
    reason = "history_expired"


def _filter(name, values, vocabulary):
    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list of names")
    if len(values) > MAX_FILTER:
        raise ValueError(f"{name} may name at most {MAX_FILTER} values")
    for value in values:
        if not isinstance(value, str) or not value or len(value) > MAX_ID:
            raise ValueError(f"{name} must be a list of names")
        if vocabulary is not None and value not in vocabulary:
            raise ValueError(f"unknown entity kind: {value}")
    # Sorted and deduplicated: two callers asking the same question in a
    # different order must share one fingerprint, or a cursor would expire on a
    # cosmetic re-spelling of its own scope.
    return sorted(set(values))


def scope(kinds, ids, epic, group_commits):
    """The canonical, validated scope the fingerprint is taken over.

    `limit` is absent on purpose — paging wider or narrower does not change
    which events belong to the answer, so it must not invalidate a cursor.
    `group_commits` is present: switching it mid-chain changes how far a cursor
    advances past a commit, and a flat-then-grouped switch could re-report one.
    """
    if not isinstance(epic, str) or len(epic) > MAX_ID:
        raise ValueError("epic must be an epic id")
    return ["changes", _filter("kinds", kinds, schema.KINDS), _filter("ids", ids, None),
            epic, bool(group_commits)]


def epic_condition(table, alias, current_epic):
    """SQL for "this event belongs to the epic", as membership *at the time of the event*.

    Both stores keep the same event rows — `kind`, `id`, `seq` and `before`/`after`
    holding only the fields that changed — so one predicate serves both, with
    `current_epic` the store's own subquery for a task's epic today (correlated
    on `{alias}.id`). It takes four parameters, each the epic id.

    An event whose own `before`/`after` names the epic is in: that is a task
    being created in it, joining it or leaving it. Any other task event is in
    when the epic the task was in at that moment is this one, and the event
    stream is its own record of that: the `after` of the task's latest earlier
    event that set an epic. A task with no such earlier event (its history
    predates the log) takes the `before` of its next move, and failing that,
    the epic it is in now. Only the viewer's write reassigns an epic, and it
    always records the field, so the stream sees every move it holds.

    Derived at read time rather than recorded at write time on purpose: the
    legacy `changes` rows are compared row-for-row with `domain_events` by the
    migration oracle, and every row already written would need a backfill that
    changes what it says. A JSON `null` epic reads as "" so it ends the search
    instead of falling through to a later answer.
    """
    event = f"{alias}."
    earlier = (f"SELECT COALESCE(json_extract(h.after,'$.epic'),'') FROM {table} h "
               f"WHERE h.kind='task' AND h.id={event}id AND h.seq<{event}seq "
               "AND json_type(h.after,'$.epic') IS NOT NULL ORDER BY h.seq DESC LIMIT 1")
    later = (f"SELECT COALESCE(json_extract(h.before,'$.epic'),'') FROM {table} h "
             f"WHERE h.kind='task' AND h.id={event}id AND h.seq>{event}seq "
             "AND json_type(h.before,'$.epic') IS NOT NULL ORDER BY h.seq LIMIT 1")
    return (f"(({event}kind='epic' AND {event}id=?) OR ({event}kind='task' AND ("
            f"json_extract({event}before,'$.epic')=? OR json_extract({event}after,'$.epic')=? "
            f"OR COALESCE(({earlier}),({later}),({current_epic}))=?)))")


def page(limit):
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")
    return limit


def change(event):
    """One event as the feed reports it: what changed, never the authored values.

    `before`/`after` are unbounded authored documents and would defeat the whole
    point of a scoped query, so the feed names the fields and the caller reads the
    current value with `backlog_get_task` if it wants one.
    """
    return {"seq": event["seq"], "kind": event["kind"], "id": event["id"], "op": event["op"],
            "fields": json.loads(event["fields"]) if event["fields"] else []}


def commit(event, changes):
    """A commit as the feed reports it. `commit_seq` is the sequence a receipt
    carries for the same write, so a caller can match one against the other."""
    return {"commit_seq": event["final_seq"], "first_seq": event["first_seq"],
            "final_seq": event["final_seq"], "operation": event["operation"],
            "ts": event["ts"], "session": event["session"], "changes": changes}


def flat(event):
    return {"seq": event["seq"], "ts": event["ts"], "session": event["session"],
            "operation": event["operation"], **change(event)}


def present(answer):
    return json.dumps(answer, ensure_ascii=False)


def refusal(exc):
    """A refused change query, in the JSON shape this tool's answers always take."""
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def issue(*, store_id, source_digest, scope, last_seq):
    payload = {"v": VERSION, "store": [str(store_id), str(source_digest)],
               "scope": fingerprint(scope), "seq": int(last_seq)}
    return base64.urlsafe_b64encode(encode(payload).encode()).decode()


def resume_point(sequence, floor=0):
    """Where a cursor that starts from now resumes: the current sequence, or the
    floor when an operator has set it above every event. A cursor below the floor
    is expired the moment it is issued, and recovering with one would answer
    another resync on every call."""
    return max(int(sequence), int(floor))


def parse(cursor, *, store_id, source_digest, scope, sequence, floor=0):
    """The sequence this continuation resumes after, or a typed refusal.

    `sequence` is the store's current high-water mark. Precedence is fixed and
    tested, in `REASONS` order, so the answer names the most fundamental reason
    rather than the first one checked.
    """
    payload = _decode(cursor)
    if payload["store"] != [str(store_id), str(source_digest)]:
        raise StoreRebuilt("this cursor was issued against a different store")
    if payload["seq"] > resume_point(sequence, floor):
        raise HistoryRewound("this cursor is past the end of the store's history; "
                             "the store was restored or rolled back")
    if payload["scope"] != fingerprint(scope):
        raise ScopeChanged("this change query's scope differs from the cursor's")
    if payload["seq"] < int(floor):
        raise HistoryExpired("change history before this cursor is no longer retained")
    return payload["seq"]


def _decode(cursor):
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_LENGTH:
        raise CursorUnreadable("change cursor is missing, oversized or not a string")
    try:
        payload = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
        if payload["v"] != VERSION:
            raise ValueError("cursor version")
        if not isinstance(payload["store"], list) or len(payload["store"]) != 2 or \
                not all(isinstance(v, str) for v in payload["store"]):
            raise ValueError("cursor store")
        if not isinstance(payload["scope"], str) or len(payload["scope"]) != 64:
            raise ValueError("cursor scope")
        if type(payload["seq"]) is not int or payload["seq"] < 0:
            raise ValueError("cursor sequence")
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise CursorUnreadable("change cursor is not one this store issued") from None
    return payload


def feed(*, store_id, source_digest, sequence, scope, items, last_seq, more, group_commits):
    """The normal answer, with a continuation at the last sequence reported."""
    return {"store_id": store_id, "sequence": int(sequence),
            ("commits" if group_commits else "changes"): list(items),
            "cursor": issue(store_id=store_id, source_digest=source_digest, scope=scope, last_seq=last_seq),
            "more": bool(more), "resync_required": False, "reason": None}


def resync(exc, *, store_id, source_digest, sequence, scope, group_commits, floor=0):
    """The recovery answer: no changes, the typed reason, and a cursor at now.

    A cursor at the current sequence rather than at the start of history is the
    point — an agent that lost its place must not re-act on work it already did.
    "Now" is `resume_point`, so the recovery cursor is never itself expired.
    """
    return {"store_id": store_id, "sequence": int(sequence),
            ("commits" if group_commits else "changes"): [],
            "cursor": issue(store_id=store_id, source_digest=source_digest, scope=scope,
                            last_seq=resume_point(sequence, floor)),
            "more": False, "resync_required": True, "reason": exc.reason}
