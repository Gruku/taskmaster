# User intent: a native store that flags a hand-edited projection file (N11, D2) must
# be able to clear the flag with its own tool: list, compare, keep either version.
# Keeping the file's version imports its observed bytes through the N13 sync barrier.
"""`backlog_resolve_conflict` on native stores (N11 S10, decision D3; N13 step 7)."""
from __future__ import annotations

import base64
import hashlib

from taskmaster.coordinator import sync_files
from taskmaster.native import projection as outbox

from . import projection
from .registry import adapter
from .runtime import error_text

_QUARANTINED = "before activation (quarantined by the legacy store)"
# Replaced bytes above this go by hash: base64 inflates 4/3, and the command envelope is 1 MiB (012 D7).
RESOLVE_INLINE_BYTES = 640 * 1024
# Above this, base64 alone exceeds the envelope, so bytes edited after flagging cannot be kept at all.
RESOLVE_INLINE_CEILING = 768 * 1024


def _too_large(file: str, replaced: bytes) -> str:
    return (f"Error: {file} changed after it was flagged (or has no captured copy), and at "
            f"{len(replaced) / (1024 * 1024):.2f} MiB it is too large to keep in the resolution's history; "
            "resolve it on the host. Nothing was changed.")


def flag_notice(conflict: dict) -> str:
    """The line every native result carries while a file stays flagged: the legacy
    notice, since the native resolver now keeps either side (N13 step 7)."""
    from taskmaster import store
    return store.projection_conflict_notice(conflict)


def _conflicts(call) -> list[dict]:
    """Flagged files, oldest first, then quarantines inherited from the legacy store."""
    with call.read() as snapshot:
        connection = snapshot.connection
        flagged = [] if not outbox.flagged_files(connection) else [
            dict(zip(("file", "kind", "id", "flagged_at"), row)) for row in connection.execute(
                "SELECT file,kind,id,flagged_at FROM projection_conflict ORDER BY flagged_at,file")]
        seen = {c["file"] for c in flagged}
        quarantined = [{"file": file, "kind": kind, "id": ident, "flagged_at": _QUARANTINED}
                       for file, kind, ident in connection.execute(
                           "SELECT file,kind,id FROM projection WHERE quarantined=1 ORDER BY file")
                       if file not in seen]
    return flagged + quarantined


def _block(label: str, text: str | None, absent: str) -> str:
    if text is None:
        return f"### {label}\n\n({absent})"
    return f"### {label}\n\n````\n{text.rstrip(chr(10))}\n````"


def _detail(call, file: str) -> str:
    listed = {c["file"]: c for c in _conflicts(call)}
    if file not in listed:
        return f"Error: {file} is not flagged."
    with call.read() as snapshot:
        connection = snapshot.connection
        kind, ident, observed = outbox.resolvable(connection, file)
        store_text, store_path = projection.store_version(connection, call.backlog_dir, kind, ident, file)
    flagged_at = listed[file]["flagged_at"]
    absent_on_disk = "the file is missing"
    try:
        on_disk = projection.read_file(call.backlog_dir, file)
    except projection.UnsafeConflictFile as exc:
        on_disk, absent_on_disk = None, f"the file can't be read safely: {exc}; compare it on the host"
    on_disk_text = None if on_disk is None else on_disk.decode("utf-8", errors="replace")
    observed_text = None if observed is None else observed.decode("utf-8", errors="replace")
    parts = [f"## {file} ({kind} {ident}), flagged {flagged_at}",
             _block("File on disk now", on_disk_text, absent_on_disk)]
    if observed_text is not None and observed_text != on_disk_text:
        parts.append(_block("File as last seen by the store", observed_text, ""))
    absent = (f"the store writes this entity to {store_path} instead" if store_path
              else "the store would write no file here: the entity is deleted or has no file of its own")
    parts.append(_block('Store version (what take="store" writes)', store_text, absent))
    whole = (" Either choice applies to the whole of backlog.yaml, every epic and phase entry in it, "
             "not to one entity." if file == "backlog.yaml" else "")
    parts.append(f'Keep one with backlog_resolve_conflict(file="{file}", take="file") or take="store".{whole}')
    return "\n\n".join(parts)


@adapter("backlog_resolve_conflict")
def resolve_conflict(call, *, file, take):
    if not file:
        conflicts = _conflicts(call)
        if not conflicts:
            return "No flagged files."
        return "\n".join([f"{len(conflicts)} flagged file(s):"] + [
            f"- {c['file']} ({c['kind']} {c['id']}, flagged {c['flagged_at']})" for c in conflicts])
    if not take:
        return _detail(call, file)
    if take not in {"file", "store"}:
        return f"Error: take must be 'file' or 'store', not {take!r}"
    if file not in dict.fromkeys(c["file"] for c in _conflicts(call)):
        return f"Error: {file} is not flagged; there is nothing to resolve"
    if take == "file":
        return _take_file(call, file)
    try:
        replaced = projection.read_file(call.backlog_dir, file)
    except projection.UnsafeConflictFile as exc:
        return (f"Error: {file} can't be read safely ({exc}), so its bytes can't be kept in the resolution's "
                "history; resolve it on the host. Nothing was changed.")
    arguments = {"file": file, "take": "store", "replaced_base64": None, "replaced_hash": None}
    large = False
    if replaced is not None and len(replaced) > RESOLVE_INLINE_BYTES:
        # Inlined, bytes this size may overflow the 1 MiB command envelope. They go by hash alone when they
        # still equal the bytes captured at flagging, which the resolution's event keeps (012 D7, Q-M1);
        # otherwise they are inlined if they can still fit (012 gate M1), and refused above that.
        with call.read() as snapshot:
            found = outbox.resolvable(snapshot.connection, file)
        flagged = None if found is None else found[2]
        digest = hashlib.sha1(replaced).hexdigest()
        if flagged is not None and hashlib.sha1(flagged).hexdigest() == digest:
            large = True
            arguments = {"file": file, "take": "store", "replaced_hash": digest, "replaced_omitted": True}
        elif len(replaced) > RESOLVE_INLINE_CEILING:
            return _too_large(file, replaced)
    if replaced is not None and not large:
        # The exact bytes go into the resolution, never a lossy decoding of them.
        arguments.update(replaced_base64=base64.b64encode(replaced).decode("ascii"),
                         replaced_hash=hashlib.sha1(replaced).hexdigest())
    try:
        call.execute(outbox.RESOLVE, arguments)
    except (ValueError, KeyError) as exc:
        if replaced is not None and not large and "encoded byte limit" in str(exc):
            return _too_large(file, replaced)
        return error_text(exc)
    message = f"Resolved {file}: kept the store version."
    if large:
        message += (f" The file's {len(replaced) / (1024 * 1024):.1f} MiB were too large to inline; the history "
                    "keeps them as the copy captured when it was flagged, which they still equal.")
    if file == "backlog.yaml":
        message += (" This took the whole file: every epic and phase entry in "
                    "backlog.yaml now comes from the store version.")
    return call.finish(message)


def _take_file(call, file: str) -> str:
    """Keep the file's version: an explicit import of exactly the bytes on disk.

    The coordinator observes the file, parses it (identity included), fences the
    candidate against the projection manifest (flag, trusted base, publication)
    and every entity revision, rechecks the bytes before submitting and after
    committing, and imports only the entities the file names. Nothing is deleted
    because the file omits it; the observed bytes stay in the event history.
    """
    try:
        result = call.sync([file], take_file=True)
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    imported = next((item for item in result.get("imports") or [] if item.get("file") == file), None)
    reasons = [notice.split(": ", 2)[-1] for notice in result.get("notices") or []
               if notice.startswith(f"sync pending: {file}: ")]
    if imported is None:
        if _already_taken(call, file):
            # A retry of the same sync id (a lost reply) finds the bytes already
            # imported and reports them unchanged: the first attempt committed.
            return _resolved(call, file, result)
        if result.get("imports_omitted"):
            return (f"Error: the outcome of taking {file} is unknown: its import receipt is not in the bounded "
                    f"result. Inspect the receipts in scope {result.get('receipt_scope')!r} (or "
                    f"backlog_resolve_conflict() for the flag) before retrying.")
        reason = "; ".join(reasons) or "; ".join(result.get("notices") or []) or "it was not imported"
        return (f"Error: {file} cannot be taken: {reason}. The file and the store were left as they are "
                f"and the flag stays. Nothing was changed.")
    if imported.get("state") != "accepted":
        return (f"Error: {file} was not taken ({imported.get('state')}): "
                f"{'; '.join(reasons) or imported.get('reason', '')}. Inspect receipt "
                f"{imported.get('request_id')!r} in scope {imported.get('caller_scope')!r} before retrying.")
    call.seq = imported.get("commit_seq")
    return _resolved(call, file, result)


def _already_taken(call, file: str) -> bool:
    """The flag is gone and the store records exactly the bytes on disk."""
    try:
        observed = sync_files.observe(call.backlog_dir, file)
    except (ValueError, OSError):
        return False
    if observed is None:
        return False
    with call.read() as snapshot:
        connection = snapshot.connection
        if outbox.held_file(connection, file):
            return False
        record = connection.execute("SELECT content_hash FROM projection WHERE file=?", (file,)).fetchone()
    return record is not None and record[0] == observed.digest


def _resolved(call, file: str, result: dict) -> str:
    for notice in result.get("notices") or []:
        if notice not in call.notices:
            call.notices.append(notice)
    message = f"Resolved {file}: kept the file version."
    if file == "backlog.yaml":
        message += (" This took the whole file: every epic and phase entry in "
                    "backlog.yaml now comes from the file version.")
    return call.finish(message)
