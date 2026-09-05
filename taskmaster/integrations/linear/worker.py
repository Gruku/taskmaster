"""Linear sync worker (linear-004 piece 2/3).

Pulls TM state from the store, builds payloads via the mapper, calls the
LinearClient, writes the Tracker row on success.  The queue lives in the
store's `linear_queue` table, so it survives an MCP-server restart and a
crash mid-drain the same way every other row does.

Queue state vocabulary (`store.linear_mark`):

  pending  queued and eligible for the next drain.  A transient failure that
           still has retries left stays `pending` with `attempts` bumped.
  done     terminal success, or a definitive no-op (`skipped:unchanged`,
           `skipped:no_tracker`) -- nothing left to push.  The row is kept as
           history rather than deleted.
  failed   parked.  A permanent error, or a transient one that exhausted
           MAX_ATTEMPTS.  A routine drain never sees it again (it reads only
           `pending`), so a dead push stops burning API round-trips (B-028);
           `backlog_linear_retry` is the explicit un-park.

`attempts` counts *failed* attempts only: the store bumps it exactly when a
mark carries an error, so a first-try success never looks like a retry.

Token economy: this module imports `httpx`-backed LinearClient directly
and never touches MCP. Every push is one HTTP round-trip; the push_hash
skip avoids the round-trip entirely when TM state is unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .client import LinearAPIError, LinearClient
from .mapper import compute_push_hash, tm_task_to_linear_payload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from taskmaster.store import Store, Transaction


# Transient items are retried up to this many times before being parked as
# permanent, so a recurring blip stops burning API round-trips (B-028).
MAX_ATTEMPTS = 5

# Most pending rows one drain will process. A drain is one synchronous run of
# HTTP round-trips, so an unbounded queue would hold the tool open indefinitely;
# whatever is left stays pending for the next drain.
DRAIN_BATCH = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enqueue(
    tx: "Transaction",
    *,
    op: str,
    target_id: str,
    tracker_id: str | None = None,
) -> int:
    """Queue one push on the caller's open transaction; returns its queue seq.

    Takes the transaction rather than a path because the enqueue has to commit
    with the mutation that caused it: a queue written outside the transaction
    could survive a rolled-back edit, or be lost by one that committed.

    De-dupe on `(op, target_id)` is the store's; the drain re-reads task state,
    so stacking requests for one target is wasted work.  `enqueued_at` rides in
    the payload and records when the target first went dirty.
    """
    return tx.linear_enqueue(op, target_id, tracker_id, {"enqueued_at": _now_iso()})


def _find_workspace(config: dict[str, Any], alias: str) -> dict[str, Any] | None:
    for ws in config.get("workspaces") or []:
        if ws.get("alias") == alias:
            return ws
    return None


def _find_task(backlog_data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for epic in backlog_data.get("epics") or []:
        for task in epic.get("tasks") or []:
            if task.get("id") == task_id:
                return task
    return None


def push_task(
    backlog_path: Path,
    task_id: str,
    client: LinearClient,
    config: dict[str, Any],
    *,
    backlog_data: dict[str, Any],
) -> dict[str, Any]:
    """Push a TM task's current state to Linear.

    Statuses returned in `{"status": "..."}`:
      - "ok"                  — pushed; tracker updated
      - "skipped:no_tracker"  — task has no linear tracker_id; nothing to push
      - "skipped:not_found"   — task_id not in backlog
      - "skipped:unchanged"   — push_hash matches; no API call made
      - "error:transient"     — retry-eligible (5xx, 429, network)
      - "error:permanent"     — auth, malformed config, unmapped status, etc.

    Reads task state from `backlog_data` (caller-loaded). The Tracker is
    read fresh from disk so push_hash comparisons see the latest state.
    """
    task = _find_task(backlog_data, task_id)
    if not task:
        return {"status": "skipped:not_found"}

    tracker_id = task.get("tracker_id")
    if not tracker_id or not str(tracker_id).startswith("linear-"):
        return {"status": "skipped:no_tracker"}

    # tracker_id format: "linear-<alias>-<key-lowercased>"
    parts = str(tracker_id).split("-", 2)
    if len(parts) < 3:
        return {"status": "error:permanent", "reason": f"malformed tracker_id {tracker_id!r}"}
    alias = parts[1]
    workspace = _find_workspace(config, alias)
    if not workspace:
        return {
            "status": "error:permanent",
            "reason": f"linear.yaml has no workspace with alias {alias!r}",
        }

    # Read existing tracker for linear_issue_id + prev push_hash. The tracker is
    # a store row like everything else, so both the read and the write below go
    # through the server's entity IO rather than touching `trackers/<id>.md`.
    from taskmaster import backlog_server as _bs

    tracker_fm = _bs._store_read_entity(backlog_path, "tracker", tracker_id)
    if tracker_fm is None:
        return {
            "status": "error:permanent",
            "reason": f"cannot read tracker {tracker_id}: not found",
        }

    # Prefer the stored Linear UUID over the human external_key for the
    # issueUpdate id (B-031). The UUID is stable across team-key renames /
    # issue moves; the human key ("ENG-1") is not. Fall back to the key only
    # until the first successful push persists the UUID.
    linear_issue_id = tracker_fm.get("linear_issue_id") or tracker_fm.get("external_key")
    prev_hash = tracker_fm.get("push_hash")

    # Build payload (this may raise on unmapped status — caller treats as permanent)
    try:
        payload = tm_task_to_linear_payload(task, workspace, linear_issue_id=linear_issue_id)
    except ValueError as e:
        return {"status": "error:permanent", "reason": str(e)}

    new_hash = compute_push_hash(payload)

    # Skip if push_hash matches — the token-economy core
    if prev_hash and prev_hash == new_hash:
        return {"status": "skipped:unchanged"}

    # Push
    try:
        result = client.issue_upsert(workspace["team_id"], payload)
    except LinearAPIError as e:
        # Classify from the structured flag, not a substring of the message (B-027).
        if getattr(e, "permanent", False):
            return {"status": "error:permanent", "reason": str(e)}
        return {"status": "error:transient", "reason": str(e)}

    # Update tracker on success — write_hash + last_pushed + refreshed denorm fields.
    # Persist the returned UUID so subsequent updates address the issue by its
    # stable id rather than the human key (B-031).
    returned_uuid = result.get("id")
    try:
        _record_push_result(
            backlog_path,
            tracker_id,
            last_pushed=_now_iso(),
            push_hash=new_hash,
            linear_issue_id=returned_uuid or linear_issue_id,
            title=task.get("title", tracker_fm.get("title", "")),
            status=task.get("status", tracker_fm.get("status", "")),
        )
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        # Push succeeded but local cache update failed. Don't requeue
        # (would cause a duplicate push); surface as a stale-cache warning.
        return {
            "status": "ok",
            "warning": f"local tracker update failed: {e}",
            "linear_id": result.get("id"),
            "identifier": result.get("identifier"),
        }

    return {
        "status": "ok",
        "linear_id": result.get("id"),
        "identifier": result.get("identifier"),
    }


def _record_push_result(backlog_path: Path, tracker_id: str, **updates: Any) -> None:
    """Write one push's outcome onto the tracker, inside a short transaction.

    The tracker read before the HTTP call is a snapshot: submitting that whole
    document back would revert anything committed while the request was in
    flight, and dropping its body would erase the tracker's narrative outright.
    So the row is read again here and only the push-result fields move; the
    body and every field this push did not touch are carried through untouched.
    """
    from taskmaster import backlog_server as _bs
    from taskmaster.taskmaster_v3 import BODY_KEY, apply_tracker_updates

    def apply() -> None:
        tx = _bs._store_tx()
        current = tx.get("tracker", tracker_id)
        body = current.pop(BODY_KEY, None)
        tx.put("tracker", tracker_id, apply_tracker_updates(current, **updates),
               body=body)

    if _bs._active_tx() is not None:
        apply()
        return
    with _bs._transaction(
        tool="linear:record-push", backlog_path=Path(backlog_path)
    ) as data:
        apply()
        _bs._mutate_and_save(data)


def drain(
    store: "Store",
    client: LinearClient,
    config: dict[str, Any],
    *,
    backlog_data: dict[str, Any],
    only_targets: set[str] | None = None,
) -> dict[str, int]:
    """Process queued pushes, one short store transaction per outcome.

    Rows are *claimed* before the first push: one transaction moves them out of
    `pending` and stamps this drain as their owner.  That is what makes a mark
    of `done` truthful.  An edit that lands while a push is in flight can no
    longer be de-duped onto the row being pushed, so it queues a request of its
    own instead of being settled unsent; and a second drain running
    concurrently finds nothing pending for those rows, so no push is issued
    twice.  The claim is a lease -- a drain killed mid-push has its rows
    returned to `pending` once it expires -- and an outcome is only recorded
    while this drain still owns the row.

    Only `pending` rows are ever claimed, so an already-parked failure is never
    re-issued and never re-counted (B-028); `backlog_linear_retry` un-parks
    explicitly.  Each item is marked the moment its push returns, outside any
    transaction that spans the HTTP call, so a crash mid-drain loses at most
    the outcome of the request in flight and every other row keeps its state
    (B-029).

    If `only_targets` is given the filter runs in the query, before the row
    limit, so a target-scoped retry finds its rows even when hundreds of other
    pushes are queued ahead of them. Other targets' rows are never read, never
    called for, and never marked.

    At most `DRAIN_BATCH` rows per call; the remainder stays pending.

    Returns a count summary suitable for direct display.
    """
    backlog_path = store.backlog_path
    counts = {"ok": 0, "skipped": 0, "transient": 0, "permanent": 0, "unknown": 0}

    owner = f"drain:{uuid.uuid4().hex}"
    claimed = store.linear_claim(DRAIN_BATCH, targets=only_targets, owner=owner)
    if not claimed:
        return counts
    # Task state is read *after* the claim, replacing whatever the caller
    # passed: a snapshot taken before the claim could predate an edit already
    # folded into a row this drain is about to push and mark done. The
    # parameter is kept so the call sites do not change.
    backlog_data = store.load_dict()

    for item in claimed:
        target_id = item["target_id"]
        op = item["op"]
        if op == "task_upsert":
            result = push_task(
                backlog_path, target_id, client, config, backlog_data=backlog_data,
            )
        else:
            result = {"status": "error:permanent", "reason": f"unknown op {op!r}"}

        status = str(result.get("status", ""))
        # One more failure would exhaust the budget, so this attempt parks.
        last_chance = int(item.get("attempts") or 0) + 1 >= MAX_ATTEMPTS

        if status.startswith("ok"):
            bucket, state, error = "ok", "done", None
        elif status == "skipped:not_found":
            # The task is absent from this backlog snapshot — possibly a stale
            # snapshot or a race, so keep the pending push rather than silently
            # dropping it (B-030), but count the attempt so a task that is
            # *truly* gone eventually parks instead of looping forever (B-028).
            bucket = "skipped"
            state = "failed" if last_chance else "pending"
            error = (
                f"task not found after {MAX_ATTEMPTS} attempts"
                if last_chance
                else "task not found in backlog snapshot"
            )
        elif status.startswith("skipped"):
            # skipped:unchanged / skipped:no_tracker — nothing left to push.
            bucket, state, error = "skipped", "done", None
        elif status == "error:transient":
            bucket = "permanent" if last_chance else "transient"
            state = "failed" if last_chance else "pending"
            error = str(result.get("reason"))
        elif status == "error:permanent":
            bucket, state, error = "permanent", "failed", str(result.get("reason"))
        else:
            # Never silently drop an unrecognized push_task status (B-030); a
            # status we cannot classify is still a failed attempt, so it parks
            # rather than looping against the API forever.
            bucket = "unknown"
            state = "failed" if last_chance else "pending"
            error = f"unrecognized push status {status!r}"

        if not store.linear_mark(item["seq"], state=state, error=error, owner=owner):
            # The lease expired and another drain took the row over. Its
            # outcome is that drain's to record; counting this one would
            # double-report the push.
            continue
        counts[bucket] += 1

    return counts
