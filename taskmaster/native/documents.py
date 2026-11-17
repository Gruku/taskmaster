"""Explicit stored-prose retrieval, with honest unimported-file provenance.

The import that fills `external_documents` is narrow by decision (N09 D7): a caller
names the document, this module stores its body and hash, and nothing here watches
a file or imports one on read. Only task `docs` sections are importable, because
`retrieve` consults `external_documents` for tasks alone — producer and consumer
have to agree about that or an imported row would never be served. Staleness
against the file on disk is N13's policy, not this module's.
"""
import json
import re

from taskmaster.taskmaster_v3 import (
    CANONICAL_SECTIONS, TASK_INLINE_SECTIONS, TASK_DOC_SECTIONS, _split_body_by_heading,
)

OPERATIONS = {"document.import"}
_REQUIRED = {"kind", "id", "section", "path", "body"}
# team-server-012: an upload names itself (D4); a precondition guards a replace (D5); the restore form
# replays 013's backup as the host operator (D10). A file import sends none of them.
_OPTIONAL = {"source", "expected_hash"}
_RESTORE = {"restore", "source", "imported_by", "original_seq"}
_HASH = re.compile(r"[0-9a-f]{64}")


def validate(operation, arguments):
    from .contracts import _identifier
    keys = set(arguments)
    if not _REQUIRED <= keys:
        raise ValueError("document.import requires kind, id, section, path and body")
    if "restore" in arguments:
        _validate_restore(arguments, keys - _REQUIRED)
    else:
        if keys - _REQUIRED - _OPTIONAL:
            raise ValueError("document.import takes kind, id, section, path and body, and optionally "
                             "source and expected_hash")
        if "source" in arguments and arguments["source"] != "upload":
            raise ValueError('document.import source may only be "upload"; a file import sends none')
        if "expected_hash" in arguments:
            expected = arguments["expected_hash"]
            if not isinstance(expected, str) or not (expected == "" or _HASH.fullmatch(expected)):
                raise ValueError("expected_hash must be a sha256 hex digest, or empty for no stored copy")
    if arguments["kind"] != "task":
        raise ValueError("only task documents can be imported")
    _identifier(arguments["id"], "task id")
    if arguments["section"] not in TASK_DOC_SECTIONS:
        raise ValueError(f"section must be one of {', '.join(sorted(TASK_DOC_SECTIONS))}")
    if not isinstance(arguments["path"], str) or not arguments["path"].strip():
        raise ValueError("path is required")
    if not isinstance(arguments["body"], str):
        raise ValueError("body must be text")


def _validate_restore(arguments, extra):
    if extra - _RESTORE:
        raise ValueError("a document restore takes source, imported_by and original_seq, and never expected_hash")
    if arguments["restore"] is not True:
        raise ValueError("restore must be true")
    if arguments.get("source") not in ("upload", "file"):
        raise ValueError('a document restore needs source "upload" or "file"')
    from taskmaster.team_identity import HANDLE_RE
    handle = arguments.get("imported_by")
    if not isinstance(handle, str) or (handle and not HANDLE_RE.fullmatch(handle)):
        raise ValueError("a document restore needs imported_by: the original handle, or empty when unknown")
    seq = arguments.get("original_seq")
    if "original_seq" in arguments and (type(seq) is not int or seq < 1):
        raise ValueError("original_seq must be a positive integer")


def apply(transaction, operation, arguments):
    ident, section, path = arguments["id"], arguments["section"], arguments["path"]
    entity = transaction.snapshot.get("task", ident, fields=["docs"])
    # Prose for a path the task does not declare could never be retrieved, since
    # `retrieve` matches the stored row against the task's current `docs` entry.
    if (entity["fields"].get("docs") or {}).get(section) != path:
        raise ValueError(f"task `{ident}` does not declare {section}: {path}")
    restore = None
    if "restore" in arguments:
        restore = {"restored_source": arguments["source"], "imported_by": arguments["imported_by"]}
        if "original_seq" in arguments:
            restore["original_seq"] = arguments["original_seq"]
    transaction.import_document("task", ident, section, path, arguments["body"],
                                source=None if restore else arguments.get("source"),
                                expected_hash=arguments.get("expected_hash"), restore=restore)


def import_origin(connection, seq):
    """Where the copy imported at `seq` came from: the one lookup provenance, the importer's answers and
    team-server-013's backup share (team-server-012 D4, D10).

    `{"origin": "file"|"upload"|"restore", "source": "file"|"upload", "imported_by": handle or "",
    "original_seq": int or None}`. `source` and `imported_by` are what a backup records: for a restored
    copy they are the values it was restored with, so backing it up again reproduces them.
    """
    from .events import columns
    extended = columns(connection)
    row = connection.execute(
        "SELECT after" + (",actor" if extended else ",NULL") + " FROM domain_events WHERE seq=? AND op='document.import'",
        (seq,)).fetchone()
    after = _json(row[0]) if row is not None else {}
    actor = _json(row[1]) if row is not None and row[1] else {}
    handle = actor.get("handle") if isinstance(actor.get("handle"), str) else ""
    source = after.get("source")
    if source == "restore":
        return {"origin": "restore", "source": after.get("restored_source") or "file",
                "imported_by": after.get("imported_by") or "", "original_seq": after.get("original_seq")}
    origin = "upload" if source == "upload" else "file"
    return {"origin": origin, "source": origin, "imported_by": handle, "original_seq": None}


def _origin_facts(connection, seq) -> dict:
    """Provenance keys for uploads and restores only; a file import's provenance stays as it was (012 M3)."""
    facts = import_origin(connection, seq)
    if facts["origin"] == "file":
        return {}
    extra = {"origin": facts["origin"]}
    if facts["origin"] == "restore":
        extra["restored_origin"] = facts["source"]
    if facts["imported_by"]:
        extra["imported_by"] = facts["imported_by"]
    if facts["original_seq"] is not None:
        extra["original_seq"] = facts["original_seq"]
    return extra


def _json(text):
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def retrieve(snapshot, kind, ident, *, sections=None):
    if sections is None:
        entity = snapshot.get(kind, ident, fields=[], include_body=True)
        return snapshot._envelope(kind=kind, id=ident, revision=entity["revision"], body=entity["body"], incomplete=False)
    if not isinstance(sections, (list, tuple)) or any(s not in CANONICAL_SECTIONS.get(kind, ()) for s in sections):
        raise ValueError(f"sections must be canonical section names for {kind}")
    entity = snapshot.get(kind, ident, fields=list(sections) + ["docs"], include_body=kind != "task")
    selected, unresolved = {}, []
    provenance = {}
    if kind == "task":
        fields = entity["fields"]
        for section in sections:
            if section in TASK_INLINE_SECTIONS and fields.get(section):
                value = fields[section]
                selected[section] = value if isinstance(value, str) else str(value)
            elif section in TASK_DOC_SECTIONS:
                path = (fields.get("docs") or {}).get(section)
                if path:
                    imported = snapshot.connection.execute(
                        "SELECT d.body,d.content_hash,d.imported_seq FROM external_documents d "
                        "JOIN entity_core c USING(entity_key) WHERE c.kind=? AND c.public_id=? AND d.section=? AND d.path=?",
                        (kind, ident, section, path)).fetchone()
                    if imported is None:
                        unresolved.append({"section": section, "path": path, "reason": "not_imported"})
                    else:
                        selected[section] = imported[0]
                        provenance[section] = {"path": path, "content_hash": imported[1], "imported_seq": imported[2],
                                               **_origin_facts(snapshot.connection, imported[2])}
    else:
        body_sections = _split_body_by_heading(entity["body"] or "")
        selected = {s: body_sections[s] for s in sections if s in body_sections}
    return snapshot._envelope(kind=kind, id=ident, revision=entity["revision"], sections=selected,
                              unresolved=unresolved, provenance=provenance, incomplete=bool(unresolved))
