# User intent: serve document detail from the store's own prose, so a native read
# path reaches a project file only as a declared fallback the answer admits to,
# and the sections answer an agent already relies on never regresses.
"""`backlog_document` and the task sections view, over the native retriever."""
from __future__ import annotations

from taskmaster import backlog_server as bs
from taskmaster.native import documents as core
from .registry import adapter
from .runtime import error_text


def sections_with_provenance(snapshot, kind, ident, sections, project_root):
    """`({section: content}, {section: provenance})` for one entity, stored prose first.

    Everything the store holds — inline task fields, body headings, imported
    external documents — comes out of `documents.retrieve`. Only a doc-backed
    section the store has never imported falls through to one explicit file read,
    and its provenance says `filesystem` (or `missing`) so the caller can tell.
    """
    from taskmaster.taskmaster_v3 import assert_canonical_sections, read_doc_section

    assert_canonical_sections(kind, sections)
    document = core.retrieve(snapshot, kind, ident, sections=sections)
    content, provenance = {}, {}
    for section in sections:
        if section in document["sections"]:
            content[section] = document["sections"][section]
            facts = document["provenance"].get(section)
            provenance[section] = ({"source": "import", **facts} if facts
                                   else {"source": "inline" if kind == "task" else "body"})
    for entry in document["unresolved"]:
        content[entry["section"]], provenance[entry["section"]] = read_doc_section(entry["path"], project_root)
    return {s: content[s] for s in sections if s in content}, provenance


def project_root():
    """The directory task `docs` paths are relative to, or None outside a project."""
    backlog = bs._backlog_path()
    return backlog.parent.parent if backlog.exists() else None


@adapter("backlog_document")
def document(call, *, kind, entity_id, sections, provenance):
    from taskmaster.taskmaster_v3 import (DOCUMENT_KINDS, document_header, render_document_body,
                                          render_sections)

    if kind not in DOCUMENT_KINDS:
        return f"Error: kind must be one of {', '.join(DOCUMENT_KINDS)}"
    if sections is not None and not sections:
        return ("Error: sections=[] requested no sections; pass sections=None for the whole document "
                "or name at least one section")
    with call.read() as snapshot:
        try:
            entity = snapshot.get(kind, entity_id, include_body=True)
        except KeyError:
            return f"Error: {kind} `{entity_id}` not found"
        header = document_header(kind, entity_id, entity["fields"].get("title") or "")
        if sections is None:
            return render_document_body(header, entity["body"] or "")
        try:
            content, facts = sections_with_provenance(snapshot, kind, entity_id, sections, project_root())
        except ValueError as exc:
            return error_text(exc)
        return render_sections(header, content, facts if provenance else None)
