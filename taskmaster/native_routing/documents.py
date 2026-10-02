# User intent: serve document detail from the store's own prose, so a native read
# path reaches a project file only as a declared fallback the answer admits to,
# and the sections answer an agent already relies on never regresses.
"""`backlog_document` and the task sections view, over the native retriever."""
from __future__ import annotations

from pathlib import Path

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
            return render_document_body(header, entity["body"] or "", entity["fields"])
        try:
            content, facts = sections_with_provenance(snapshot, kind, entity_id, sections, project_root())
        except ValueError as exc:
            return error_text(exc)
        return render_sections(header, content, facts if provenance else None)


class _Unreadable(ValueError):
    """A declared document this importer will not read; nothing was stored for it."""


def _project_file(root: Path, path: str) -> str | None:
    """The text of a declared document, read only from inside the project.

    The path is resolved — symlinks included — before it is compared, so neither
    an absolute path, a `../` escape nor a link out of the tree reaches a file the
    project does not own: an import copies the text into the store, where every
    reader of the backlog can then see it. `None` means no file is there.
    """
    base = root.resolve()
    candidate = (base / path).resolve()
    if not candidate.is_relative_to(base):
        raise _Unreadable(f"{path} is outside the project, and only project files are imported")
    if not candidate.exists():
        return None
    if not candidate.is_file():
        raise _Unreadable(f"{path} is not a file")
    try:
        return candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise _Unreadable(f"{path} is not UTF-8 text") from None
    except OSError as exc:
        raise _Unreadable(f"{path} could not be read ({exc.strerror or exc})") from None


@adapter("backlog_document_import")
def document_import(call, *, kind, entity_id, sections):
    from taskmaster.native.contracts import MAX_BYTES, Conflict
    from taskmaster.taskmaster_v3 import TASK_DOC_SECTIONS

    if kind != "task":
        return "Error: only task documents can be imported"
    if sections is not None and not sections:
        return ("Error: sections=[] requested no sections; pass sections=None to import every "
                "document the task declares")
    with call.read() as snapshot:
        try:
            declared = snapshot.get("task", entity_id, fields=["docs"])["fields"].get("docs") or {}
        except KeyError:
            return f"Error: task `{entity_id}` not found"
    wanted = sections if sections is not None else [s for s in sorted(TASK_DOC_SECTIONS) if s in declared]
    unknown = [s for s in wanted if s not in TASK_DOC_SECTIONS]
    if unknown:
        return (f"Error: section must be one of {', '.join(sorted(TASK_DOC_SECTIONS))}; "
                f"got {', '.join(unknown)}")
    if not wanted:
        return f"Error: task `{entity_id}` declares no document sections to import"
    root = project_root() or call.backlog_dir.parent
    # Each section is its own commit, so one bad file neither undoes nor hides the
    # sections stored before it, and does not stop the ones after it: the answer
    # lists every section's outcome and leads with `Error:` if any failed.
    lines, failed = [], 0
    for section in wanted:
        path = declared.get(section)
        if not path:
            lines.append(f"- {section}: the task declares no path — nothing imported")
            continue
        try:
            body = _project_file(root, path)
            if body is None:
                lines.append(f"- {section}: no file at {path} — nothing imported")
                continue
            size = len(body.encode("utf-8"))
            if size > MAX_BYTES:
                raise _Unreadable(f"{path} is {size} bytes, over the {MAX_BYTES}-byte (1 MiB) limit on one "
                                  "imported document")
            receipt = call.execute("document.import", {"kind": "task", "id": entity_id, "section": section,
                                                       "path": path, "body": body})
        except (ValueError, KeyError, Conflict) as exc:
            failed += 1
            reason = str(exc) if isinstance(exc, _Unreadable) else error_text(exc)[len("Error: "):]
            lines.append(f"- {section}: FAILED — {reason} — nothing imported")
            continue
        lines.append(f"- {section}: {'imported' if receipt['affected'] else 'unchanged'} from {path}")
    header = f"## import `{entity_id}`\n"
    if failed:
        header = (f"Error: {failed} of {len(wanted)} sections failed to import; every section listed as "
                  f"imported or unchanged is stored.\n\n{header}")
    return call.finish("\n".join([header, *lines]))
