# User intent: `backlog_handover_resync` / `backlog_issue_resync` on a native store must
# pick up hand edits to those documents (N13 step 7), and `backlog_sync` any hand edit to
# `.taskmaster/` files on request (N17), through the one explicit import path, the
# coordinator's sync barrier, never by reading a missing file as a deletion.
"""Handover/issue resync and explicit `backlog_sync` adapters."""
from __future__ import annotations

import time

from taskmaster import backlog_server as bs
from taskmaster.coordinator import sync_files
from taskmaster.coordinator import sync_jobs
from taskmaster.coordinator.protocol import HandshakeError, ServiceUnavailable
from taskmaster.native import sync
from taskmaster.projection_parse import classify

from .handovers import _backlog_document
from .registry import adapter
from .runtime import error_text


def _documents(call, kind: str) -> tuple[list[str], list[str]]:
    """Every authored path of one kind: discovered on disk (canonical path first) and
    recorded by the store, so a deleted file is named and repaired, not forgotten.
    A second path for the same document is skipped and reported, never imported."""
    inventory = sync_files.discover(call.backlog_dir)
    warnings = [f"{rel}: {reason}" for rel, reason in inventory.refused.items()]
    chosen: dict[str, str] = {}
    with call.read() as snapshot:
        for rel, ident in snapshot.connection.execute(
                "SELECT file,id FROM projection WHERE kind=? ORDER BY file", (kind,)):
            try:
                if classify(rel) != (kind, ident):
                    raise ValueError(rel)
            except ValueError:
                warnings.append(f"{rel}: recorded path is not an importable {kind} path; skipped")
                continue
            if chosen.setdefault(ident, rel) != rel:
                warnings.append(f"{rel}: second copy of {kind} {ident} skipped; the store's file is {chosen[ident]}")
    for rel, (found, ident) in inventory.files.items():
        if found != kind:
            continue
        if chosen.setdefault(ident, rel) != rel:
            warnings.append(f"{rel}: second copy of {kind} {ident} skipped; the store's file is {chosen[ident]}")
    warnings.extend(f"{rel}: duplicate of {canonical} skipped" for rel, canonical in inventory.duplicates.items()
                    if inventory.files.get(canonical, (None,))[0] == kind)
    return sorted(chosen.values()), warnings


def _tally(call, results) -> tuple[list[str], list[str], list[str], list[str]]:
    """Imported, repaired and unsettled files, and every pending reason, across sync
    results; the call's seq advances to the highest committed import."""
    imported, repaired, uncertain, reasons = [], [], [], []
    for result in results:
        for item in result.get("imports") or []:
            if item.get("state") == "accepted":
                imported.append(item["file"])
            elif item.get("state") == "repair_pending":
                repaired.append(item["file"])
            elif item.get("state") == "uncertain":
                uncertain.append(f"{item['file']}: outcome uncertain; inspect receipt {item.get('request_id')!r} "
                                 f"in scope {item.get('caller_scope')!r}")
            if item.get("commit_seq") is not None:
                call.seq = max(call.seq or 0, item["commit_seq"])
        reasons.extend(notice.removeprefix("sync pending: ") for notice in result.get("notices") or [])
        omitted = sum(result.get(key + "_omitted", 0) for key in ("imports", "notices"))
        if omitted:
            reasons.append(f"{omitted} further result(s) omitted; inspect receipt scope {result.get('receipt_scope')}")
    return imported, repaired, uncertain, reasons


def _resync(call, kind: str, headline) -> str:
    try:
        files, warnings = _documents(call, kind)
        results = [call.sync(files[start:start + sync.MAX_FILES])
                   for start in range(0, len(files), sync.MAX_FILES)]
    except (ValueError, KeyError, OSError) as exc:
        return error_text(exc)
    imported, repaired, uncertain, reasons = _tally(call, results)
    with call.read() as snapshot:
        lines = [headline(_backlog_document(snapshot))]
    if imported:
        lines.append(f"Imported {len(imported)} edited file(s): {', '.join(imported)}")
    if repaired:
        lines.append(f"Repaired {len(repaired)} missing file(s) from the store: {', '.join(repaired)}")
    problems = uncertain + reasons + warnings
    if problems:
        lines.append("Not synchronized (files and store left as they are):")
        lines.extend(f"- {problem}" for problem in problems)
    return call.finish("\n".join(lines))


@adapter("backlog_handover_resync")
def handover_resync(call):
    if not bs._backlog_path().exists():
        return "No backlog found."
    return _resync(call, "handover", lambda data: (
        f"Handover index resynced — {len(data.get('handovers') or [])} entries in `backlog.yaml`."))


@adapter("backlog_issue_resync")
def issue_resync(call):
    if not bs._backlog_path().exists():
        return "No backlog found."
    return _resync(call, "issue", lambda data: f"Issue index resynced — {len(data.get('issues') or [])} entries.")


# The hard wall-clock limit of one `backlog_sync` call, everything included (reaching or
# starting the coordinator, the request, the wait, the reply). Clients may have fixed tool
# timeouts (the Codex manifest: `tool_timeout_sec: 30`); the sync itself runs on in the
# coordinator, and a later call with its id attaches to it.
TOOL_WAIT = 15
_SHOWN = 20


def _relative(rel) -> str:
    """A path relative to `.taskmaster/`; a leading `.taskmaster/` is accepted."""
    if not isinstance(rel, str):
        raise ValueError("files must be paths relative to .taskmaster/")
    rel = rel.replace("\\", "/")
    return rel.removeprefix("./").removeprefix(".taskmaster/")


def _listed(label: str, items: list[str], total: int) -> list[str]:
    if not total:
        return []
    shown = items[:_SHOWN]
    more = f" ({total - len(shown)} further results not listed)" if total > len(shown) else ""
    return [f"{label}: {', '.join(shown)}{more}"]


def _held_lines(held) -> list[str]:
    conflicts, quarantined = held.get("conflicts") or [], held.get("quarantined") or []
    lines = []
    if conflicts:
        lines.append("Conflicts (the file and the store both changed; nothing was merged, the store kept its version):")
        lines += [f'- {rel}: compare with backlog_resolve_conflict(file="{rel}"), then keep one with '
                  f'take="file" or take="store"' for rel in conflicts[:_SHOWN]]
        extra = held.get("conflict_count", len(conflicts)) - len(conflicts[:_SHOWN])
        if extra > 0:
            lines.append(f"- ({extra} further results not listed; see backlog_resolve_conflict())")
    if quarantined:
        lines.append("Quarantined (the file does not parse; its bytes are kept and the store's values stand):")
        lines += [f'- {rel}: fix the file and run backlog_sync(files=["{rel}"]), or '
                  f'backlog_resolve_conflict(file="{rel}", take="store")' for rel in quarantined[:_SHOWN]]
        extra = held.get("quarantined_count", len(quarantined)) - len(quarantined[:_SHOWN])
        if extra > 0:
            lines.append(f"- ({extra} further results not listed; see backlog_resolve_conflict())")
    return lines


def _counts(totals) -> str:
    """Counts that add up to the files the sync selected."""
    parts = [f"{totals.get('imported', 0)} imported", f"{totals.get('repaired', 0)} repaired",
             f"{totals.get('unchanged', 0)} unchanged",
             f"{totals.get('conflicts', 0) + totals.get('quarantined', 0)} conflicts"]
    if totals.get("pending"):
        parts.append(f"{totals['pending']} pending")
    if totals.get("not_checked"):
        parts.append(f"{totals['not_checked']} not checked")
    return f"{totals.get('selected', 0)} file(s) — " + ", ".join(parts)


def _reasons(answer) -> list[str]:
    """The last round's pending reasons, less the held files' own (listed with their next steps)."""
    held = answer.get("held") or {}
    files = (held.get("conflicts") or []) + (held.get("quarantined") or [])
    prefixes = tuple(prefix for rel in files for prefix in (f"{rel}: ", f"export pending: {rel} "))
    reasons = [notice.removeprefix("sync pending: ") for notice in answer.get("notices") or []]
    return [reason for reason in reasons
            if not (prefixes and (reason.startswith(prefixes) or reason == "projection publication incomplete"))]


def _ended(answer, stored: str) -> list[str]:
    """An ended sync, live or replayed from its record, rendered from its own final state."""
    ident, state, totals = answer["sync_id"], answer["state"], answer.get("totals") or {}
    if state == "failed":
        return [f"Sync failed (sync id {ident}){stored}: {answer.get('error')}. Start a fresh backlog_sync()."]
    held = answer.get("held") or {}
    if state == "complete":
        lines = [f"Sync complete (sync id {ident}){stored}: {_counts(totals)}."]
    else:
        lines = [f"Sync finished without synchronizing every file (sync id {ident}){stored}: {_counts(totals)}; "
                 f"the store held {held.get('conflict_count', 0)} conflict(s) and "
                 f"{held.get('quarantined_count', 0)} quarantined file(s)."]
    lines += _listed("Imported", totals.get("imported_files") or [], totals.get("imported", 0))
    lines += _listed("Repaired from the store", totals.get("repaired_files") or [], totals.get("repaired", 0))
    lines += _held_lines(held)
    if state != "complete":
        lines += _listed("Pending", totals.get("pending_files") or [], totals.get("pending", 0))
        reasons = _reasons(answer)
        if reasons:
            lines.append("Not synchronized:")
            lines += [f"- {reason}" for reason in reasons[:_SHOWN]]
            if len(reasons) > _SHOWN:
                lines.append(f"- ({len(reasons) - _SHOWN} further results not listed)")
        lines.append("Settle what is listed above, then run backlog_sync() again.")
    return lines


def _render(call, answer) -> str:
    ident, state = answer["sync_id"], answer["state"]
    totals = answer.get("totals") or {}
    if state == "interrupted":
        lines = [f"Sync {ident} did not finish: the coordinator stopped while it ran (started "
                 f"{answer.get('started_at') or 'earlier'}). Imports it committed are kept; it is not resumed. "
                 f"Call backlog_sync() to start a fresh sync."]
        if answer.get("running"):
            lines.append(f'Another sync is running now: check it with backlog_sync(sync_id="{answer["running"]}").')
        return "\n".join(lines)
    if answer.get("replay"):
        # A stored answer: say so, never stamp it as if this call had committed anything.
        stored = f" — the stored result of a sync that finished at {answer.get('finished_at') or 'an earlier time'}"
        lines = _ended(answer, stored)
        lines.append("Edits made since then need a fresh backlog_sync().")
        return "\n".join(lines)
    if isinstance(totals.get("seq"), int):
        call.seq = max(call.seq or 0, totals["seq"])
    lines = []
    if "not_started" in answer:
        lines.append("Another backlog_sync (with different files) is already running, so yours was not started; "
                     "this is that sync. Call backlog_sync again once it has finished.")
    elif answer.get("attached"):
        lines.append("A backlog_sync was already running; attached to it instead of starting another.")
    if state == "running":
        if answer.get("phase") == "publishing":
            where = f"all {answer.get('selected', 0)} files checked; publishing the store's files"
        elif answer.get("phase") in ("starting", "selecting"):
            where = "finding the files to check"
        elif answer.get("phase") == "classifying":
            where = f"{answer.get('selected', 0)} files found; checking them against Git before importing"
        else:
            where = f"{answer.get('checked', 0)} of {answer.get('selected', 0)} files checked so far"
        rounds = f" (round {answer['round']})" if answer.get("round", 0) > 1 else ""
        lines.insert(0, f"Sync running (sync id {ident}): {where}{rounds}; {totals.get('imported', 0)} imported, "
                        f"{totals.get('repaired', 0)} repaired so far. Call backlog_sync(sync_id=\"{ident}\") "
                        f"to check again.")
        return "\n".join(lines)
    lines = _ended(answer, "") + lines
    lines += [f"Warning: {warning}" for warning in (answer.get("warnings") or [])[:5]]
    return "\n".join(lines)


@adapter("backlog_sync")
def backlog_sync(call, *, files=None, sync_id=""):
    if not bs._backlog_path().exists():
        return "No backlog found."
    try:
        named = None if files is None else [_relative(rel) for rel in files]
        if sync_id and not sync_jobs.ID_PATTERN.fullmatch(sync_id):
            raise ValueError(f"{sync_id!r} is not a sync id backlog_sync issued; start a sync with backlog_sync()")
    except ValueError as exc:
        return error_text(exc)
    deadline = time.monotonic() + TOOL_WAIT
    try:
        answer = call.client.sync_job(deadline=deadline, sync_id=sync_id or None, files=named)
    except HandshakeError as exc:
        # Another build owns the coordinator: waiting or calling again cannot help.
        return f"Sync not started: {exc}. Nothing was changed."
    except ServiceUnavailable as exc:
        how = (f'check it with backlog_sync(sync_id="{sync_id}")' if sync_id else
               "call backlog_sync() again: it attaches to a sync already running rather than starting another")
        return (f"Sync not confirmed: the coordinator did not answer within {TOOL_WAIT} s ({exc}). "
                f"Nothing is lost; {how}.")
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    return call.finish(_render(call, answer))
