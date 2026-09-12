"""Explicit stored-prose retrieval, with honest unimported-file provenance."""
from taskmaster.taskmaster_v3 import (
    CANONICAL_SECTIONS, TASK_INLINE_SECTIONS, TASK_DOC_SECTIONS, _split_body_by_heading,
)


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
