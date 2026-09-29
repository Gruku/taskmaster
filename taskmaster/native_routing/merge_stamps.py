"""User intent: a merge recorded by a hook on a native store is never lost and never
starts a coordinator. Hooks must not bootstrap, and native writes go only through a
coordinator, so a stamp made while none is running is queued here, durably, and the
MCP server applies it on its next native call.

The queue is `.taskmaster/local/merge-stamps-pending.jsonl`, one JSON object per line.
A replayer claims it by renaming it to `<name>.claim`, so an append made meanwhile lands
in a new queue file instead of being overwritten. While a claim exists nobody claims
again: a claim left by a replayer that died is finished by the next one. Applying an
entry twice is harmless, since an entry the rung already records is skipped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

PENDING = "merge-stamps-pending.jsonl"


def pending_path(backlog_dir: Path) -> Path:
    return Path(backlog_dir) / "local" / PENDING


def claim_path(backlog_dir: Path) -> Path:
    return Path(backlog_dir) / "local" / (PENDING + ".claim")


def has_pending(backlog_dir: Path) -> bool:
    try:
        return pending_path(backlog_dir).exists() or claim_path(backlog_dir).exists()
    except OSError:
        return False


def enqueue(backlog_dir: Path, entry: dict) -> None:
    path = pending_path(backlog_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def hook_log(backlog_dir: Path, text: str) -> None:
    """Append to the hooks' shared log, local/hook.log. Never raises."""
    try:
        path = Path(backlog_dir) / "local" / "hook.log"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()} merge_stamps: {text}\n")
    except Exception:  # noqa: BLE001 -- logging must not cost the caller
        pass


def superseded(entry: dict, current: dict | None, is_ancestor) -> bool:
    """Whether the rung already records this merge or a later one.

    `is_ancestor(old, new)` answers True/False, or None when Git cannot tell (then the
    later `merged_at` wins)."""
    if not current:
        return False
    if current.get("merge_commit") == entry["sha"]:
        return True
    verdict = is_ancestor(entry["sha"], current.get("merge_commit", ""))
    if verdict is None:
        return str(current.get("merged_at", "")) > str(entry.get("merged_at", ""))
    return bool(verdict)


def _git_is_ancestor(root: Path):
    def check(old: str, new: str):
        if not old or not new:
            return None
        try:
            completed = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", old, new],
                                       capture_output=True, timeout=10,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        return {0: True, 1: False}.get(completed.returncode)
    return check


def _claim(backlog_dir: Path) -> Path | None:
    claim = claim_path(backlog_dir)
    if claim.exists():
        return claim
    try:
        os.replace(pending_path(backlog_dir), claim)
    except OSError:
        return None  # nothing queued, or an appender holds it open (Windows): next call
    return claim


def replay(backlog_dir: Path, database: Path, session: str, *, autostart: bool, log=None) -> int:
    """Apply queued stamps in order; returns how many were applied. Re-queues what it
    could not write and raises the coordinator's ServiceUnavailable."""
    from taskmaster.coordinator.protocol import ServiceUnavailable
    from taskmaster.native_routing import reads, runtime, tasks

    log = log or (lambda text: hook_log(backlog_dir, text))
    is_ancestor = _git_is_ancestor(Path(backlog_dir).parent)
    applied = 0
    for _ in range(2):  # a stale claim first, then whatever was queued behind it
        claim = _claim(backlog_dir)
        if claim is None:
            break
        try:
            lines = claim.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue  # a concurrent replayer finished it
        entries = [json.loads(line) for line in lines if line.strip()]
        left = []
        for index, entry in enumerate(entries):
            label = f"{entry['task_id']} {entry['rung']} {entry['sha']}"
            try:
                with runtime.open_call(database, backlog_dir, session, autostart=autostart) as call:
                    with call.read() as snapshot:
                        found = reads.find_task(snapshot, entry["task_id"])
                    current = (found[0].get("merge_status") or {}).get(entry["rung"]) if found else None
                    if superseded(entry, current, is_ancestor):
                        log(f"queued merge stamp {label} is already recorded or superseded; dropped")
                        continue
                    result = tasks.record_merge(call, task_id=entry["task_id"], rung=entry["rung"],
                                                sha=entry["sha"], merged_at=entry.get("merged_at", ""))
            except ServiceUnavailable:
                left = entries[index:]
                break
            if isinstance(result, str) and result.startswith("Error"):
                log(f"queued merge stamp {label} was refused: {result}")
                continue
            applied += 1
            log(f"queued merge stamp applied: {label}")
        for entry in left:
            enqueue(backlog_dir, entry)
        try:
            claim.unlink()
        except FileNotFoundError:
            pass
        if left:
            raise ServiceUnavailable("repository coordinator unavailable; merge stamps stay queued")
    return applied
