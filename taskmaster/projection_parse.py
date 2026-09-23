"""Pure authored-projection parsing shared by legacy and native import.

There is no Store, database, filesystem or publisher dependency here. A caller
supplies a snapshot lookup for split epic/phase ownership. Bytes passed here are
the observed bytes, never a freshly rendered substitute for a merge base.
"""
from __future__ import annotations

from copy import deepcopy
import fnmatch
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Mapping

from taskmaster import yaml_io
from taskmaster.taskmaster_v3 import (
    BODY_KEY, EPIC_HEAVY_FIELDS, PHASE_HEAVY_FIELDS, _v4_strip_private_fields,
    parse_frontmatter, task_v4_from_file,
)

BACKLOG_ID = "__backlog__"
PROJECT_ID = "__project__"
Document = tuple[dict[str, Any], str | None]
Lookup = Callable[[str, str], Document | None]

# Canonical path first; an older duplicate path is an import fallback only.
ENTITY_FILE_SPECS = (
    ("task", ("tasks/*.md", "tasks/archive/*.md")),
    ("epic", ("epics/*.md",)),
    ("phase", ("phases/*.md",)),
    ("bug", ("bugs/*.md", "bugs/archive/*.md")),
    ("issue", ("issues/*.md", "issues/archive/*.md")),
    ("handover", ("handovers/*.md", "handovers/_archive/*/*.md", "handovers/archive/*.md")),
    ("decision", ("decisions/*.md",)),
    ("idea", ("ideas/IDEA-*.md",)),
    ("note", ("notes/NOTE-*.md", "notes/_archive/NOTE-*.md")),
    ("area", ("areas/*.md",)),
    ("tracker", ("trackers/*.md", "integrations/trackers/*.md")),
)


def classify(rel: str) -> tuple[str, str | None]:
    """An authored path's identity, without looking at the filesystem."""
    if not isinstance(rel, str) or not rel or any(char in rel for char in (":", "\\", "\0")):
        raise ValueError(f"unsafe projection path {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != rel:
        raise ValueError(f"unsafe projection path {rel!r}")
    if rel in {"backlog.yaml", "project.yaml"}:
        return rel.removesuffix(".yaml"), None
    if not (path.name.startswith(".") or ".tmp." in path.name or ".corrupt-" in path.name):
        for kind, patterns in ENTITY_FILE_SPECS:
            for pattern in patterns:
                parts = PurePosixPath(pattern).parts
                if len(parts) == len(path.parts) and all(fnmatch.fnmatch(a, b) for a, b in zip(path.parts, parts)):
                    return kind, path.stem
    raise ValueError(f"not an authored projection: {rel!r}")


def clean_doc(doc: Mapping[str, Any]) -> dict[str, Any]:
    return _v4_strip_private_fields(dict(doc), preserve_body=False)


def normal_body(body: str | None) -> str | None:
    """A body as the parser yields it: one trailing newline is file framing, not prose.

    Stored bodies written by 6.x may still end in a newline. Every merge input
    (base, database, file) must pass through this, or an untouched body differs
    from its own base and a pure append looks like an overlapping edit.
    """
    return (body.removesuffix("\n") or None) if isinstance(body, str) else body


def split_body(doc: Mapping[str, Any]) -> Document:
    materialized = deepcopy(dict(doc))
    body = normal_body(materialized.pop(BODY_KEY, None))
    return clean_doc(materialized), body


def schema_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a schema integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("schema value must be an integer")


def validate_backlog(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("backlog.yaml must be a mapping")
    meta = raw.get("meta")
    if meta is None:
        meta = {}
    elif not isinstance(meta, dict):
        raise ValueError("backlog.yaml meta must be a mapping")
    for field in ("schema_version", "projection_schema"):
        if field in meta:
            try:
                schema_integer(meta[field])
            except ValueError as exc:
                raise ValueError(f"backlog.yaml meta.{field} must be an integer") from exc


def flatten_backlog(data: Mapping[str, Any]) -> dict[tuple[str, str], Document]:
    result: dict[tuple[str, str], Document] = {}

    def claim(key, value):
        if key in result:
            raise ValueError(f"{key[0]} {key[1]} appears twice in the backlog dict; a create "
                             "cannot reuse an existing id")
        result[key] = value

    backlog_doc = {key: deepcopy(value) for key, value in data.items()
                   if key not in {"epics", "phases", "context"}
                   and not (isinstance(key, str) and key.startswith("_"))}
    if isinstance(backlog_doc.get("meta"), dict):
        backlog_doc["meta"].pop("updated", None)
    claim(("backlog", BACKLOG_ID), (clean_doc(backlog_doc), None))
    for epic in data.get("epics") or []:
        epic_doc, body = split_body({key: deepcopy(value) for key, value in epic.items() if key != "tasks"})
        ident = str(epic_doc.get("id") or "")
        if ident:
            claim(("epic", ident), (epic_doc, body))
        for task in epic.get("tasks") or []:
            task_doc, task_body = split_body(task)
            task_id = str(task_doc.get("id") or "")
            if task_id:
                task_doc.setdefault("epic", ident)
                claim(("task", task_id), (task_doc, task_body))
    for phase in data.get("phases") or []:
        phase_doc, body = split_body(phase)
        ident = str(phase_doc.get("id") or "")
        if ident:
            claim(("phase", ident), (phase_doc, body))
    return result


# Git writes a conflict's outer markers as whole lines at column 0: seven `<` or
# `>` followed by a space and label, or by nothing. `=======` and diff3's
# `|||||||` only ever appear between them, so on their own they are prose (a
# setext heading underline, a quoted pytest summary rule) and never a conflict.
_CONFLICT_MARKER = re.compile(r"^(?:<{7}|>{7})(?:[ \t].*)?\r?$", re.MULTILINE)


def has_conflict_markers(raw: str) -> bool:
    return _CONFLICT_MARKER.search(raw) is not None


def entity_text(kind: str, raw: str) -> Document:
    if has_conflict_markers(raw):
        raise ValueError("git conflict markers")
    fm, body = parse_frontmatter(raw)
    if not fm or not isinstance(fm, dict):
        raise ValueError("missing or invalid frontmatter")
    if kind == "task":
        return split_body(task_v4_from_file(fm, body.removesuffix("\n")))
    return clean_doc(fm), normal_body(body)


def validate_identity(kind: str, ident: str, doc: Mapping[str, Any]) -> None:
    declared = doc.get("id")
    if declared is not None and str(declared) != ident:
        raise ValueError(f"{kind} path id {ident!r} does not match frontmatter id {declared!r}")


def authored_rows(kind: str, ident: str | None, content: bytes) -> dict[tuple[str, str], Document] | None:
    """What these bytes literally say, before split ownership is applied.

    The comparison point for "did the author change something this file does
    not own?": an epic document's `title` mirror, a claim written into a task
    file, a derived index or heavy field typed into backlog.yaml.
    """
    text = content.decode("utf-8")
    if kind == "backlog":
        raw = yaml_io.safe_load(text)
        raw = {} if raw is None else raw
        validate_backlog(raw)
        return flatten_backlog(raw)
    if kind == "project":
        doc = yaml_io.safe_load(text)
        doc = {} if doc is None else doc
        if not isinstance(doc, dict):
            raise ValueError("project.yaml must be a mapping")
        return {("project", ident or PROJECT_ID): (doc, None)}
    if not ident:
        return None
    doc, body = entity_text(kind, text)
    validate_identity(kind, ident, doc)
    return {(kind, ident): (doc, body)}


def owned_rows(kind: str, rows: dict[tuple[str, str], Document] | None,
               lookup: Lookup = lambda *_: None) -> dict[tuple[str, str], Document] | None:
    """Authored rows overlaid on a caller-provided current snapshot (not mutated).

    An epic/phase document owns heavy fields and prose only; backlog.yaml owns
    its slim fields. Absence in an entity file removes a heavy field. No absent
    file or absent backlog member is represented as an entity deletion here.
    """
    if rows is None:
        return None
    result = {key: (deepcopy(doc), body) for key, (doc, body) in rows.items()}
    for key, (doc, body) in list(result.items()):
        if key[0] not in {"epic", "phase"}:
            continue
        current = lookup(*key)
        if current is None:
            continue
        heavy = EPIC_HEAVY_FIELDS if key[0] == "epic" else PHASE_HEAVY_FIELDS
        if kind == "backlog":
            for field in heavy:
                if field in current[0]:
                    doc[field] = deepcopy(current[0][field])
                else:
                    doc.pop(field, None)
            result[key] = (doc, current[1])
        else:
            merged = deepcopy(current[0])
            for field in heavy:
                if field in doc:
                    merged[field] = doc[field]
                else:
                    merged.pop(field, None)
            result[key] = (merged, body)
    return result


def projected_file(kind: str, ident: str | None, content: bytes,
                   lookup: Lookup = lambda *_: None) -> dict[tuple[str, str], Document] | None:
    """Rows these bytes own, overlaid on a caller-provided current snapshot."""
    return owned_rows(kind, authored_rows(kind, ident, content), lookup)
