"""Linear sync worker (linear-004 piece 2/3).

Pulls TM state from disk, builds payloads via the mapper, calls the
LinearClient, writes the Tracker file on success. The queue file persists
across MCP-server restarts so an interrupted drain resumes safely.

Token economy: this module imports `httpx`-backed LinearClient directly
and never touches MCP. Every push is one HTTP round-trip; the push_hash
skip avoids the round-trip entirely when TM state is unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import LinearAPIError, LinearClient
from .mapper import compute_push_hash, tm_task_to_linear_payload


# Transient items are retried up to this many times before being parked as
# permanent, so a recurring blip stops burning API round-trips (B-028).
MAX_ATTEMPTS = 5


def queue_path(backlog_path: Path) -> Path:
    return backlog_path.parent / "integrations" / "linear-queue.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_queue(backlog_path: Path) -> list[dict[str, Any]]:
    path = queue_path(backlog_path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_queue(backlog_path: Path, items: list[dict[str, Any]]) -> None:
    path = queue_path(backlog_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def enqueue(
    backlog_path: Path,
    *,
    op: str,
    target_id: str,
    tracker_id: str | None = None,
) -> None:
    """Append a push request. De-dupes on (op, target_id) — the drain
    re-reads task state from disk, so stacking N requests for the same
    target is wasted work."""
    items = read_queue(backlog_path)
    for item in items:
        if item.get("op") == op and item.get("target_id") == target_id:
            return
    items.append({
        "op": op,
        "target_id": target_id,
        "tracker_id": tracker_id,
        "enqueued_at": _now_iso(),
        "attempts": 0,
        "last_error": None,
    })
    _write_queue(backlog_path, items)


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

    # Read existing tracker for linear_issue_id + prev push_hash
    from taskmaster_v3 import read_tracker, update_tracker

    try:
        tracker_fm, _ = read_tracker(backlog_path, tracker_id)
    except (OSError, ValueError) as e:
        return {"status": "error:permanent", "reason": f"cannot read tracker {tracker_id}: {e}"}

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
        update_tracker(
            backlog_path,
            tracker_id,
            last_pushed=_now_iso(),
            push_hash=new_hash,
            linear_issue_id=returned_uuid or linear_issue_id,
            title=task.get("title", tracker_fm.get("title", "")),
            status=task.get("status", tracker_fm.get("status", "")),
        )
    except (OSError, ValueError) as e:
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


def drain(
    backlog_path: Path,
    client: LinearClient,
    config: dict[str, Any],
    *,
    backlog_data: dict[str, Any],
    only_targets: set[str] | None = None,
) -> dict[str, int]:
    """Process queued pushes. Successful items and no-op skips are removed;
    failed items stay in the queue with incremented attempts and a
    `last_error`. Permanent failures (and transients that exhaust MAX_ATTEMPTS)
    get `permanent: true` and are parked — a later drain leaves them untouched
    and never re-hits the API, so a known-dead push stops burning round-trips
    (B-028). `/linear retry` is the explicit un-park action.

    If `only_targets` is given, items whose `target_id` is not in the set are
    left in the queue untouched (no API call, no mutation). This lets a
    target-scoped retry run without ever removing other targets' items from
    the canonical queue, so a crash mid-drain can't lose them (B-029).

    Returns a count summary suitable for direct display.
    """
    items = read_queue(backlog_path)
    remaining: list[dict[str, Any]] = []
    counts = {"ok": 0, "skipped": 0, "transient": 0, "permanent": 0, "unknown": 0}

    for item in items:
        # Target filter: leave non-matching items in place, untouched (B-029).
        if only_targets is not None and item.get("target_id") not in only_targets:
            remaining.append(item)
            continue

        # Already-parked permanent failures are terminal — keep them queued for
        # `/linear status` visibility but never re-issue the API call (B-028).
        if item.get("permanent"):
            counts["permanent"] += 1
            remaining.append(item)
            continue

        op = item.get("op")
        target_id = item.get("target_id")
        if op == "task_upsert":
            result = push_task(
                backlog_path, target_id, client, config, backlog_data=backlog_data,
            )
        else:
            result = {"status": "error:permanent", "reason": f"unknown op {op!r}"}

        status = str(result.get("status", ""))
        if status.startswith("ok"):
            counts["ok"] += 1
        elif status == "skipped:not_found":
            # The task is absent from this backlog snapshot — possibly a stale
            # snapshot or a race, so keep the item rather than silently dropping
            # the pending push (B-030). Count attempts so a task that is *truly*
            # gone eventually parks instead of looping forever (B-028).
            counts["skipped"] += 1
            item["attempts"] = int(item.get("attempts", 0)) + 1
            if item["attempts"] >= MAX_ATTEMPTS:
                item["permanent"] = True
                item["last_error"] = f"task not found after {MAX_ATTEMPTS} attempts"
            else:
                item["last_error"] = "task not found in backlog snapshot"
            remaining.append(item)
        elif status.startswith("skipped"):
            # skipped:unchanged / skipped:no_tracker — nothing to push; drop.
            counts["skipped"] += 1
        elif status == "error:transient":
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["last_error"] = result.get("reason")
            if item["attempts"] >= MAX_ATTEMPTS:
                # Retries exhausted — park so we stop burning API calls (B-028).
                item["permanent"] = True
                counts["permanent"] += 1
            else:
                counts["transient"] += 1
            remaining.append(item)
        elif status == "error:permanent":
            counts["permanent"] += 1
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["last_error"] = result.get("reason")
            item["permanent"] = True
            remaining.append(item)
        else:
            # Never silently drop an unrecognized push_task status (B-030).
            counts["unknown"] += 1
            item["last_error"] = f"unrecognized push status {status!r}"
            remaining.append(item)

    _write_queue(backlog_path, remaining)
    return counts
