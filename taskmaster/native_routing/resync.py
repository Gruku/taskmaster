# User intent: `backlog_handover_resync` / `backlog_issue_resync` on a native store must
# pick up hand edits to those documents (N13 step 7) through the one explicit import path,
# the coordinator's sync barrier, never by reading a missing file as a deletion.
"""Handover and issue resync adapters."""
from __future__ import annotations

from taskmaster import backlog_server as bs
from taskmaster.coordinator import sync_files
from taskmaster.native import sync

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
            chosen.setdefault(ident, rel)
    for rel, (found, ident) in inventory.files.items():
        if found != kind:
            continue
        if chosen.setdefault(ident, rel) != rel:
            warnings.append(f"{rel}: second copy of {kind} {ident} skipped; the store's file is {chosen[ident]}")
    warnings.extend(f"{rel}: duplicate of {canonical} skipped" for rel, canonical in inventory.duplicates.items()
                    if inventory.files.get(canonical, (None,))[0] == kind)
    return sorted(chosen.values()), warnings


def _resync(call, kind: str, headline) -> str:
    try:
        files, warnings = _documents(call, kind)
        results = [call.sync(files[start:start + sync.MAX_FILES])
                   for start in range(0, len(files), sync.MAX_FILES)]
    except (ValueError, KeyError, OSError) as exc:
        return error_text(exc)
    imported, repaired, reasons, uncertain = [], [], [], []
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
