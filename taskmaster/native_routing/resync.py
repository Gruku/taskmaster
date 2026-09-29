# User intent: `backlog_handover_resync` / `backlog_issue_resync` on a native store must
# pick up hand edits to those documents (N13 step 7), and `backlog_sync` any hand edit to
# `.taskmaster/` files on request (N17), through the one explicit import path, the
# coordinator's sync barrier, never by reading a missing file as a deletion.
"""Handover/issue resync and explicit `backlog_sync` adapters."""
from __future__ import annotations

import json
import time
import uuid

from taskmaster import backlog_server as bs
from taskmaster.coordinator import sync_files
from taskmaster.coordinator.protocol import ServiceUnavailable
from taskmaster.coordinator.sync_worker import FINISH_TIMEOUT
from taskmaster.native import projection as outbox, sync
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


# `backlog_sync` must answer inside a client's fixed tool timeout (the Codex manifest sets
# `tool_timeout_sec: 30`). The coordinator replies within its budget + FINISH_TIMEOUT, so the
# budget is what REPLY_TARGET leaves after the coordinator is reached, at most TOOL_BUDGET; an
# exhausted budget answers pending with the sync id to continue (N16's retry-the-same-id).
TOOL_BUDGET = 15
REPLY_TARGET = 25
# One caller scope for every `backlog_sync`: the sync id alone names the operation, so another
# session, or this one after a server restart, can continue it.
SYNC_SCOPE = "backlog-sync"
_SHOWN = 20


def _relative(rel) -> str:
    """A path relative to `.taskmaster/`; a leading `.taskmaster/` is accepted."""
    if not isinstance(rel, str):
        raise ValueError("files must be paths relative to .taskmaster/")
    rel = rel.replace("\\", "/")
    return rel.removeprefix("./").removeprefix(".taskmaster/")


def _listed(label: str, items: list[str]) -> list[str]:
    if not items:
        return []
    more = f" (+{len(items) - _SHOWN} more)" if len(items) > _SHOWN else ""
    return [f"{label}: {', '.join(items[:_SHOWN])}{more}"]


@adapter("backlog_sync")
def backlog_sync(call, *, files=None, sync_id=""):
    if not bs._backlog_path().exists():
        return "No backlog found."
    try:
        named = None if files is None else [_relative(rel) for rel in files]
    except ValueError as exc:
        return error_text(exc)
    ident = sync_id or uuid.uuid4().hex
    retry = f'backlog_sync(sync_id="{ident}"' + ("" if named is None else f", files={json.dumps(named)}") + ")"
    try:
        started = time.monotonic()
        call.client.status()  # reaches (or starts) the coordinator; that time comes off the budget
        budget = max(1, min(TOOL_BUDGET, REPLY_TARGET - FINISH_TIMEOUT - (time.monotonic() - started)))
        result = call.client.sync(files=named, caller_scope=SYNC_SCOPE, request_id=ident, timeout=budget)
    except ServiceUnavailable as exc:
        return call.finish(f"Sync pending (sync id {ident}): the coordinator did not answer: {exc}\n"
                           f"Nothing is lost; continue with {retry}.")
    except (ValueError, KeyError, OSError) as exc:
        return error_text(exc)
    imported, repaired, uncertain, reasons = _tally(call, [result])
    with call.read() as snapshot:
        connection = snapshot.connection
        conflicts = list(outbox.flagged_files(connection))
        quarantined = [row[0] for row in connection.execute(
            "SELECT file FROM projection WHERE quarantined=1 ORDER BY file") if row[0] not in conflicts]
    # A held file's own notices (why it is held, its paused export, the publication it keeps
    # incomplete) are the conflict/quarantine lines below; one saying to retry is kept.
    held = tuple(prefix for rel in conflicts + quarantined for prefix in (f"{rel}: ", f"export pending: {rel} "))
    reasons = [reason for reason in reasons if "retry" in reason or not (
        (held and reason.startswith(held)) or (held and reason == "projection publication incomplete"))]
    selected = result.get("selected", 0)
    if result.get("state") == "synchronized":
        settled = len(result.get("imports") or []) + result.get("imports_omitted", 0)
        lines = [f"Sync complete (sync id {ident}): {selected} file(s) checked — {len(imported)} imported, "
                 f"{len(repaired)} repaired, {max(0, selected - settled)} unchanged, {len(conflicts)} conflicts."]
    else:
        lines = [f"Sync pending (sync id {ident}): not every file is synchronized. So far {len(imported)} imported, "
                 f"{len(repaired)} repaired; the store holds {len(conflicts)} conflict(s) and "
                 f"{len(quarantined)} quarantined file(s)."]
    lines += _listed("Imported", imported) + _listed("Repaired from the store", repaired)
    if conflicts:
        lines.append("Conflicts (the file and the store both changed; nothing was merged, the store kept its version):")
        lines += [f'- {rel}: compare with backlog_resolve_conflict(file="{rel}"), then keep one with '
                  f'take="file" or take="store"' for rel in conflicts[:_SHOWN]]
    if quarantined:
        lines.append("Quarantined (the file does not parse; its bytes are kept and the store's values stand):")
        lines += [f'- {rel}: fix the file and run backlog_sync(files=["{rel}"]), or '
                  f'backlog_resolve_conflict(file="{rel}", take="store")' for rel in quarantined[:_SHOWN]]
    problems = uncertain + reasons
    if problems:
        lines.append("Not synchronized yet:")
        lines += [f"- {problem}" for problem in problems[:_SHOWN]]
        if len(problems) > _SHOWN:
            lines.append(f"- (+{len(problems) - _SHOWN} more)")
        lines.append(f"Continue this sync with {retry}.")
    elif result.get("state") != "synchronized":
        lines.append("Once the files above are settled, run backlog_sync() again.")
    warnings = list(result.get("warnings") or [])
    lines += [f"Warning: {warning}" for warning in warnings[:5]]
    return call.finish("\n".join(lines))
