# User intent: serve document detail from the store's own prose, so a native read
# path reaches a project file only as a declared fallback the answer admits to,
# and the sections answer an agent already relies on never regresses.
"""`backlog_document` and the task sections view, over the native retriever."""
from __future__ import annotations

import re
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


def _imported_line(section, path, body, receipt, prior, *, uploaded) -> str:
    import hashlib
    changed = bool(receipt["affected"])
    if uploaded:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        line = (f"- {section}: imported from your upload as {path} (sha256 {digest}…)" if changed
                else f"- {section}: unchanged — the store already holds this text")
    else:
        line = f"- {section}: {'imported' if changed else 'unchanged'} from {path}"
    caller = ((receipt.get("actor") or {}).get("handle") or "") if isinstance(receipt, dict) else ""
    if changed and prior is not None and prior["imported_by"] and prior["imported_by"] != caller:
        line += f" — replaced the copy {prior['imported_by']} imported at seq {prior['seq']}"
    return line


def _project_file(root: Path, path: str) -> str | None:
    """The text of a declared document, read only from inside the project.

    The path is resolved — symlinks included — before it is compared, so neither
    an absolute path, a `../` escape nor a link out of the tree reaches a file the
    project does not own: an import copies the text into the store, where every
    reader of the backlog can then see it. `None` means no file is there.

    On a team server the shared tracked-file rule decides instead (team-server-005 Part B).
    """
    from taskmaster.taskmaster_v3 import served_doc_text
    try:
        serving, text, missing = served_doc_text(path)
    except UnicodeDecodeError:
        raise _Unreadable(f"{path} is not UTF-8 text") from None
    if serving:
        if missing:
            return None                                  # "no file at <path>", as in solo
        if text is None:
            from taskmaster.coordinator.allowlist import SERVED_DOC_REFUSED
            raise _Unreadable(SERVED_DOC_REFUSED.format(path=path))
        return text
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


_HASH = re.compile(r"[0-9a-f]{64}")


def _uploaded(text) -> str:
    """An uploaded document's text, normalized as a file import reads it (team-server-012 D2)."""
    if not isinstance(text, str):
        raise _Unreadable("the uploaded text must be a string")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _Unreadable("the uploaded text is not valid UTF-8 (it holds an unpaired surrogate)") from None
    return text


def _sized(body: str, what: str) -> str:
    """The one per-document cap, shared by file imports and uploads (D3)."""
    from taskmaster.native.contracts import MAX_BYTES
    size = len(body.encode("utf-8"))
    if size > MAX_BYTES:
        raise _Unreadable(f"{what} is {size} bytes, over the {MAX_BYTES}-byte (1 MiB) limit on one imported document")
    return body


def _stored(call, entity_id, section, path) -> dict | None:
    """Who imported the copy stored under the declared path, for the D5 "replaced" note; None when none is."""
    from taskmaster.native.documents import import_origin
    with call.read() as snapshot:
        row = snapshot.connection.execute(
            "SELECT d.path,d.imported_seq FROM external_documents d JOIN entity_core c USING(entity_key) "
            "WHERE c.kind='task' AND c.public_id=? AND d.section=?", (entity_id, section)).fetchone()
        if row is None or row[0] != path:
            return None
        return {"seq": row[1], **import_origin(snapshot.connection, row[1])}


def _member_in_serve() -> bool:
    """In a serve process, whether this request is anyone but the host operator."""
    from taskmaster.coordinator import allowlist, client as coordinator_client
    serving, principal = coordinator_client.served_principal()
    return serving and principal != allowlist.OPERATOR


def _serve_hint(section) -> str:
    """In a serve process, how to bring a file the host won't read or doesn't have (D6)."""
    from taskmaster.coordinator import client as coordinator_client
    if not coordinator_client.serving():
        return ""
    return f'; to import the file from your machine, pass its text: content={{"{section}": "..."}}'


def _arguments_refusal(sections, content, expected_hash) -> str | None:
    if content is not None and sections is not None:
        return "Error: pass sections (import the declared files) or content (the text of each section), not both"
    if content is not None and (not isinstance(content, dict) or not content
                                or not all(isinstance(text, str) for text in content.values())):
        return "Error: content must map at least one section to that document's text"
    if expected_hash is not None and (not isinstance(expected_hash, dict) or not all(
            isinstance(value, str) and (value == "" or _HASH.fullmatch(value)) for value in expected_hash.values())):
        return "Error: expected_hash maps a section to the sha256 content_hash you expect stored, or \"\" for none"
    return None


@adapter("backlog_document_import")
def document_import(call, *, kind, entity_id, sections, content=None, expected_hash=None):
    from taskmaster.native.contracts import Conflict
    from taskmaster.taskmaster_v3 import TASK_DOC_SECTIONS

    if kind != "task":
        return "Error: only task documents can be imported"
    if sections is not None and not sections:
        return ("Error: sections=[] requested no sections; pass sections=None to import every "
                "document the task declares")
    refused = _arguments_refusal(sections, content, expected_hash)
    if refused is not None:
        return refused
    with call.read() as snapshot:
        try:
            declared = snapshot.get("task", entity_id, fields=["docs"])["fields"].get("docs") or {}
        except KeyError:
            return f"Error: task `{entity_id}` not found"
    if content is not None:
        wanted = sorted(content)
    else:
        wanted = sections if sections is not None else [s for s in sorted(TASK_DOC_SECTIONS) if s in declared]
    unknown = [s for s in wanted if s not in TASK_DOC_SECTIONS] + [
        s for s in (expected_hash or {}) if s not in TASK_DOC_SECTIONS and s not in wanted]
    if unknown:
        return (f"Error: section must be one of {', '.join(sorted(TASK_DOC_SECTIONS))}; "
                f"got {', '.join(unknown)}")
    if not wanted:
        return f"Error: task `{entity_id}` declares no document sections to import"
    if expected_hash is not None and set(expected_hash) - set(wanted):
        # On either form: a precondition for a section this call skips would be silently ignored (012 gate M4).
        return "Error: expected_hash names a section this call does not import"
    root = project_root() or call.backlog_dir.parent
    # Each section is its own commit, so one bad file neither undoes nor hides the
    # sections stored before it, and does not stop the ones after it: the answer
    # lists every section's outcome and leads with `Error:` if any failed.
    lines, failed = [], 0
    for section in wanted:
        path = declared.get(section)
        if not path:
            if content is not None:
                failed += 1
                lines.append(f'- {section}: FAILED — the task declares no {section} path; set one first with '
                             f'backlog_update_task(field="docs", value="{section}:<path in your checkout>") '
                             "— nothing imported")
            else:
                lines.append(f"- {section}: the task declares no path — nothing imported")
            continue
        try:
            if content is not None:
                body = _sized(_uploaded(content[section]), "the uploaded text")
            else:
                body = _project_file(root, path)
                if body is None:
                    lines.append(f"- {section}: no file at {path} — nothing imported{_serve_hint(section)}")
                    continue
                body = _sized(body, path)
            prior = _stored(call, entity_id, section, path)
            arguments = {"kind": "task", "id": entity_id, "section": section, "path": path, "body": body}
            if content is not None or _member_in_serve():
                # On a team server a member's import is always recorded as theirs: the coordinator accepts no
                # other kind from a member (Codex review finding 2), even for a tracked host file read here.
                arguments["source"] = "upload"
            if expected_hash is not None and section in expected_hash:
                arguments["expected_hash"] = expected_hash[section]
            receipt = call.execute("document.import", arguments)
        except (ValueError, KeyError, Conflict) as exc:
            failed += 1
            reason = str(exc) if isinstance(exc, _Unreadable) else error_text(exc)[len("Error: "):]
            hint = _serve_hint(section) if content is None and isinstance(exc, _Unreadable) else ""
            lines.append(f"- {section}: FAILED — {reason} — nothing imported{hint}")
            continue
        lines.append(_imported_line(section, path, body, receipt, prior, uploaded=content is not None))
    header = f"## import `{entity_id}`\n"
    if failed:
        header = (f"Error: {failed} of {len(wanted)} sections failed to import; every section listed as "
                  f"imported or unchanged is stored.\n\n{header}")
    return call.finish("\n".join([header, *lines]))
