"""Explicit stored-prose retrieval, with honest unimported-file provenance.

The import that fills `external_documents` is narrow by decision (N09 D7): a caller
names the document, this module stores its body and hash, and nothing here watches
a file or imports one on read. Only task `docs` sections are importable, because
`retrieve` consults `external_documents` for tasks alone — producer and consumer
have to agree about that or an imported row would never be served. Staleness
against the file on disk is N13's policy, not this module's.
"""
from taskmaster.taskmaster_v3 import (
    CANONICAL_SECTIONS, TASK_INLINE_SECTIONS, TASK_DOC_SECTIONS, _split_body_by_heading,
)

OPERATIONS = {"document.import"}


def validate(operation, arguments):
    from .contracts import _identifier
    if set(arguments) != {"kind", "id", "section", "path", "body"}:
        raise ValueError("document.import requires kind, id, section, path and body")
    if arguments["kind"] != "task":
        raise ValueError("only task documents can be imported")
    _identifier(arguments["id"], "task id")
    if arguments["section"] not in TASK_DOC_SECTIONS:
        raise ValueError(f"section must be one of {', '.join(sorted(TASK_DOC_SECTIONS))}")
    if not isinstance(arguments["path"], str) or not arguments["path"].strip():
        raise ValueError("path is required")
    if not isinstance(arguments["body"], str):
        raise ValueError("body must be text")


def apply(transaction, operation, arguments):
    ident, section, path = arguments["id"], arguments["section"], arguments["path"]
    entity = transaction.snapshot.get("task", ident, fields=["docs"])
    # Prose for a path the task does not declare could never be retrieved, since
    # `retrieve` matches the stored row against the task's current `docs` entry.
    if (entity["fields"].get("docs") or {}).get(section) != path:
        raise ValueError(f"task `{ident}` does not declare {section}: {path}")
    transaction.import_document("task", ident, section, path, arguments["body"])


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
                        provenance[section] = {"path": path, "content_hash": imported[1], "imported_seq": imported[2]}
    else:
        body_sections = _split_body_by_heading(entity["body"] or "")
        selected = {s: body_sections[s] for s in sections if s in body_sections}
    return snapshot._envelope(kind=kind, id=ident, revision=entity["revision"], sections=selected,
                              unresolved=unresolved, provenance=provenance, incomplete=bool(unresolved))
