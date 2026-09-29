"""User intent: a merge recorded by a hook on a native store is never lost, never starts
a coordinator, and can never wedge. Hooks must not bootstrap, and native writes go only
through a coordinator, so a stamp made while none is running is queued here, durably,
and applied by the MCP server once a coordinator is running.

Files, all in `.taskmaster/local/`:
  merge-stamps-pending.jsonl   the queue, one JSON entry per line, oldest first
  merge-stamps.claim           the batch one replayer is applying; line 1 is its owner
  merge-stamps.lock            O_EXCL lock around every queue or claim change (owner pid, time)
  merge-stamps-rejected.jsonl  lines that cannot be parsed or applied, kept for a human

A replayer takes the queue into its claim under the lock, applies it without the lock
(coordinator calls can take seconds), then puts back what it could not apply ahead of
anything queued meanwhile, again under the lock. It never removes a claim another
replayer took over. A lock or claim whose owner is dead, or that is older than
STALE_SECONDS, is taken over.

Whether a queued stamp is still wanted is decided against a snapshot of the task, and
the write carries that snapshot's task revision as its expected revision, so the
coordinator refuses it (Conflict) if anything recorded a merge in between; the replayer
then decides again from fresh state.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

PENDING = "merge-stamps-pending.jsonl"
CLAIM = "merge-stamps.claim"
LOCK = "merge-stamps.lock"
REJECTED = "merge-stamps-rejected.jsonl"
STALE_SECONDS = 600.0
MAX_ATTEMPTS = 5
LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 256 * 1024
REQUIRED = ("task_id", "rung", "sha")


class Unavailable(Exception):
    """No coordinator can take the write now; the stamp stays queued."""


def _local(backlog_dir) -> Path:
    return Path(backlog_dir) / "local"


def pending_path(backlog_dir) -> Path:
    return _local(backlog_dir) / PENDING


def claim_path(backlog_dir) -> Path:
    return _local(backlog_dir) / CLAIM


def rejected_path(backlog_dir) -> Path:
    return _local(backlog_dir) / REJECTED


def has_pending(backlog_dir) -> bool:
    try:
        return pending_path(backlog_dir).exists() or claim_path(backlog_dir).exists()
    except OSError:
        return False


def hook_log(backlog_dir, text: str) -> None:
    """Append to the hooks' shared log, capped at LOG_MAX_BYTES. Never raises."""
    try:
        path = _local(backlog_dir) / "hook.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.write_bytes(path.read_bytes()[-LOG_KEEP_BYTES:])
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()} merge_stamps: {text}\n")
    except Exception:  # noqa: BLE001 -- logging must not cost the caller
        pass


# ── the lock ────────────────────────────────────────────────────────────────


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owner_record() -> dict:
    return {"pid": os.getpid(), "at": time.time(), "token": uuid.uuid4().hex}


def _stale(record) -> bool:
    if not isinstance(record, dict):
        return True
    at = record.get("at")
    if not isinstance(at, (int, float)) or time.time() - at > STALE_SECONDS:
        return True
    return not _pid_alive(record.get("pid"))


def _read_record(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None


@contextmanager
def queue_lock(backlog_dir, timeout: float = 10.0):
    """Exclusive, cross-process, for short queue/claim changes only."""
    path = _local(backlog_dir) / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = _owner_record()
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = _read_record(path)
            if held is not None and _stale(held):
                # Break only the lock we judged stale: re-read right before removing it.
                if _read_record(path) == held:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"merge-stamp queue lock held: {held}")
            time.sleep(0.05)
            continue
        try:
            os.write(fd, (json.dumps(mine) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        if _read_record(path) == mine:
            try:
                os.unlink(path)
            except OSError:
                pass


def _write_atomic(path: Path, lines: list[str]) -> None:
    if not lines:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(line if line.endswith("\n") else line + "\n" for line in lines))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def _append(path: Path, lines: list[str]) -> None:
    """One os.write of complete lines; callers hold the queue lock."""
    if not lines:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, "".join(line + "\n" for line in lines).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def enqueue(backlog_dir, entry: dict, *, timeout: float = 10.0) -> None:
    entry = dict(entry, queued_at=entry.get("queued_at") or time.time())
    _local(backlog_dir).mkdir(parents=True, exist_ok=True)
    with queue_lock(backlog_dir, timeout):
        _append(pending_path(backlog_dir), [json.dumps(entry, sort_keys=True)])


def _reject(backlog_dir, line: str, reason: str, log) -> None:
    try:
        with queue_lock(backlog_dir):
            _append(rejected_path(backlog_dir), [json.dumps({"line": line, "reason": reason,
                                                             "at": time.time()}, sort_keys=True)])
    except Exception:  # noqa: BLE001 -- the log line below still records it
        pass
    log(f"merge stamp moved to {REJECTED}: {reason}: {line[:300]}")


# ── replay ──────────────────────────────────────────────────────────────────


def _take(backlog_dir):
    """Move the queue into a claim this replayer owns. None: nothing to do, or a live
    replayer owns the claim."""
    claim, pending = claim_path(backlog_dir), pending_path(backlog_dir)
    with queue_lock(backlog_dir):
        queued = _read_lines(pending)
        if claim.exists():
            owner = _read_record(claim)
            if owner is not None and not _stale(owner):
                return None
            carried = _read_lines(claim)[1:]  # a dead replayer's batch: older than the queue
        else:
            carried = []
        entries = carried + queued
        if not entries:
            _write_atomic(claim, [])
            return None
        mine = _owner_record()
        _write_atomic(claim, [json.dumps(mine)] + entries)
        _write_atomic(pending, [])
        return mine, entries


def _finish(backlog_dir, mine, left: list[str], log) -> None:
    claim, pending = claim_path(backlog_dir), pending_path(backlog_dir)
    with queue_lock(backlog_dir):
        if _read_record(claim) != mine:
            log("another replayer took over this merge-stamp batch; leaving it to them")
            return
        # What this batch could not apply is older than anything queued meanwhile.
        _write_atomic(pending, left + _read_lines(pending))
        _write_atomic(claim, [])


def replay_queue(backlog_dir, apply, *, log=None, deadline=None) -> dict:
    """Apply queued stamps in order through `apply(entry)`, which returns "applied",
    "dropped" (already recorded or superseded), "retry" (keep queued) or
    ("reject", reason), or raises Unavailable. Never raises for a bad entry. At `deadline`
    (time.monotonic()) it stops taking entries and keeps the rest queued."""
    log = log or (lambda text: hook_log(backlog_dir, text))
    counts = {"applied": 0, "dropped": 0, "retry": 0, "rejected": 0, "unavailable": False}
    taken = _take(backlog_dir)
    if taken is None:
        return counts
    mine, lines = taken
    left: list[str] = []
    for index, line in enumerate(lines):
        if deadline is not None and time.monotonic() >= deadline:
            left.extend(lines[index:])
            break
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry[k]
                                                  for k in REQUIRED):
                raise ValueError(f"needs {', '.join(REQUIRED)}")
        except ValueError as exc:
            _reject(backlog_dir, line, f"unreadable entry ({exc})", log)
            counts["rejected"] += 1
            continue
        label = f"{entry['task_id']} {entry['rung']} {entry['sha']}"
        try:
            outcome = apply(entry)
        except Unavailable:
            counts["unavailable"] = True
            left.extend(lines[index:])
            break
        except Exception as exc:  # noqa: BLE001 -- one entry must not wedge the queue
            outcome = ("error", repr(exc))
        if outcome == "applied":
            counts["applied"] += 1
            log(f"queued merge stamp applied: {label}")
        elif outcome == "dropped":
            counts["dropped"] += 1
            log(f"queued merge stamp {label} is already recorded or superseded; dropped")
        elif isinstance(outcome, tuple) and outcome[0] == "reject":
            counts["rejected"] += 1
            _reject(backlog_dir, line, f"refused: {outcome[1]}", log)
        else:
            attempts = int(entry.get("attempts", 0)) + 1
            if attempts >= MAX_ATTEMPTS:
                counts["rejected"] += 1
                _reject(backlog_dir, line, f"gave up after {attempts} attempts: {outcome}", log)
            else:
                counts["retry"] += 1
                left.append(json.dumps(dict(entry, attempts=attempts), sort_keys=True))
    _finish(backlog_dir, mine, left, log)
    return counts


def superseded(entry: dict, current: dict | None, is_ancestor) -> bool:
    """Whether the rung already records this merge or a later one.

    Git ancestry decides. Without it (`is_ancestor` answers None) the recorded
    `merged_at` decides, at its minute precision: a record from the same minute or
    later wins, so a queued stamp never overwrites a record it cannot prove older."""
    if not current:
        return False
    if current.get("merge_commit") == entry["sha"]:
        return True
    verdict = is_ancestor(entry["sha"], current.get("merge_commit", ""))
    if verdict is None:
        return str(current.get("merged_at", "")) >= str(entry.get("merged_at", ""))
    return bool(verdict)


def git_is_ancestor(root: Path):
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


def native_apply(database, backlog_dir, session: str, *, autostart: bool, client_timeout=None):
    """`apply` for a native store: decide from a snapshot, write with that snapshot's
    task revision as the expected revision, re-decide on a Conflict."""
    from taskmaster.coordinator.protocol import ServiceUnavailable
    from taskmaster.native.contracts import Conflict
    from taskmaster.native_routing import reads, runtime
    from taskmaster.native_routing.tasks import _merge_targets

    is_ancestor = git_is_ancestor(Path(backlog_dir).parent)

    def apply(entry):
        for _ in range(3):
            try:
                with runtime.open_call(database, backlog_dir, session, autostart=autostart,
                                       client_timeout=client_timeout) as call:
                    with call.read() as snapshot:
                        entity = reads.get(snapshot, "task", entry["task_id"])
                        targets = _merge_targets(snapshot) if entity is not None else []
                    if entity is None:
                        return ("reject", f"task {entry['task_id']} not found")
                    current = (entity["fields"].get("merge_status") or {}).get(entry["rung"])
                    if superseded(entry, current, is_ancestor):
                        return "dropped"
                    call.execute("task.merge", {"id": entry["task_id"], "rung": entry["rung"], "sha": entry["sha"],
                                                "merged_at": entry.get("merged_at", ""), "merge_targets": targets},
                                 expected=[{"kind": "task", "id": entry["task_id"],
                                            "revision": entity["revision"]}])
                    return "applied"
            except ServiceUnavailable as exc:
                raise Unavailable(str(exc)) from exc
            except Conflict:
                continue  # something changed the task since the snapshot: decide again
            except (ValueError, KeyError) as exc:
                return ("reject", str(exc))
        return "retry"

    return apply


def replay(backlog_dir, database, session: str, *, autostart: bool, log=None, deadline=None,
           client_timeout=None) -> dict:
    apply = native_apply(database, backlog_dir, session, autostart=autostart, client_timeout=client_timeout)
    return replay_queue(backlog_dir, apply, log=log, deadline=deadline)
