# User intent: a native store that flags a hand-edited projection file (N11, D2) must
# be able to clear the flag with its own tool: list, compare, keep the store's version.
# Keeping the file's version needs the native importer (N13), so that side refuses.
"""`backlog_resolve_conflict` on native stores (N11 S10, decision D3)."""
from __future__ import annotations

import base64
import hashlib

from taskmaster.native import projection as outbox

from . import projection
from .registry import adapter
from .runtime import error_text

_TAKE_FILE_REFUSAL = (
    '`backlog_resolve_conflict` with take="file" cannot run here: this project\'s store is a native '
    "authority, and importing an edited file into it needs the native importer, which ships with N13. "
    "To keep the file's version, copy what you need from backlog_resolve_conflict(file=\"{file}\") into "
    "a normal edit (for example `backlog_update_task`), then resolve with take=\"store\". Nothing was changed.")


_QUARANTINED = "before activation (quarantined by the legacy store)"


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
    on_disk = projection.read_file(call.backlog_dir, file)
    on_disk_text = None if on_disk is None else on_disk.decode("utf-8", errors="replace")
    observed_text = None if observed is None else observed.decode("utf-8", errors="replace")
    parts = [f"## {file} ({kind} {ident}), flagged {flagged_at}",
             _block("File on disk now", on_disk_text, "the file is missing")]
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
        return "Error: " + _TAKE_FILE_REFUSAL.format(file=file)
    replaced = projection.read_file(call.backlog_dir, file)
    try:
        # The exact bytes go into the resolution, never a lossy decoding of them.
        call.execute(outbox.RESOLVE, {
            "file": file, "take": "store",
            "replaced_base64": None if replaced is None else base64.b64encode(replaced).decode("ascii"),
            "replaced_hash": None if replaced is None else hashlib.sha1(replaced).hexdigest()})
    except (ValueError, KeyError) as exc:
        return error_text(exc)
    message = f"Resolved {file}: kept the store version."
    if file == "backlog.yaml":
        message += (" This took the whole file: every epic and phase entry in "
                    "backlog.yaml now comes from the store version.")
    return call.finish(message)
