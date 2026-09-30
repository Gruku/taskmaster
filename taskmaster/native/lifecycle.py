"""Native lifecycle handlers reusing the existing pure domain functions.

Entity create/update/archive lives here; the task/epic/phase composites, gates,
claims, links, settings and the Linear outbox live in `workflow`, which shares
this module's dispatch so a caller sees one operation namespace.
"""
import inspect
from functools import lru_cache
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints
from taskmaster import taskmaster_v3 as domain
from . import documents, graph_repair, linear_outbox, projection, sync, workflow

BUILDERS = {"decision": domain.build_decision_doc, "bug": domain.build_bug_doc,
            "issue": domain.build_issue_doc, "idea": domain.build_idea_doc, "handover": domain.build_handover_doc}
PATCHERS = {"bug": domain.apply_bug_updates, "issue": domain.apply_issue_updates, "idea": domain.apply_idea_updates}
PATCH_FIELDS = {
    "bug": {"title", "status", "severity", "fix_commit", "adopted_into", "promoted_to", "components", "location", "body"},
    "issue": {"title", "status", "severity", "impact", "fixed_in_task", "duplicate_of", "components", "location", "related_tasks", "body"},
    "idea": {"title", "body", "status", "promoted_to", "tags", "related_tasks", "related_issues", "archived"},
}
ENTITY_OPERATIONS = {f"{kind}.create" for kind in BUILDERS} | {f"{kind}.update" for kind in PATCHERS} | {
    "note.update", "note.archive", "bug.archive", "decision.resolve", "decision.drop", "decision.update",
    "handover.status", "handover.supersede"}
# Arguments `handover.create` takes beyond its document builder: they shape what
# commits with the new handover, not the document the builder produces.
HANDOVER_CREATE_EXTRAS = {"flag_for_review", "review_reason"}
# `auto_link: false` on a create skips inline-mention linking, as the viewer's
# create routes (which never ran it) require.
CREATE_EXTRAS = {"auto_link"}
# Kinds whose prose is scanned for inline mentions on create and on a body edit,
# as the tools do (`auto_link_on_save`).
AUTO_LINKED = {"issue", "idea", "handover"}
# `projection.resolve` keeps the store's version of a flagged projection file (N11 S10).
# `graph.repair` is the explicit graph-table repair maintenance operation (N14).
OPERATIONS = (ENTITY_OPERATIONS | workflow.OPERATIONS | documents.OPERATIONS | linear_outbox.OPERATIONS
              | sync.OPERATIONS | graph_repair.OPERATIONS | {projection.RESOLVE})


def _accepts(value, annotation):
    if annotation is Any:
        return True
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(_accepts(value, option) for option in get_args(annotation))
    if origin is list:
        return isinstance(value, list) and all(_accepts(item, get_args(annotation)[0]) for item in value)
    if annotation in (str, int, bool, type(None)):
        return type(value) is annotation
    raise TypeError(f"Unsupported command field annotation: {annotation}")


@lru_cache(maxsize=None)
def _builder_contract(kind):
    return inspect.signature(BUILDERS[kind]), get_type_hints(BUILDERS[kind])


def validate(operation, arguments):
    from .contracts import _identifier
    if operation in sync.OPERATIONS:
        return sync.validate(operation, arguments)
    if operation in linear_outbox.OPERATIONS:
        return linear_outbox.validate(operation, arguments)
    if operation in workflow.OPERATIONS:
        return workflow.validate(operation, arguments)
    if operation in documents.OPERATIONS:
        return documents.validate(operation, arguments)
    if operation == projection.RESOLVE:
        return projection.validate_resolve(arguments)
    if operation in graph_repair.OPERATIONS:
        return graph_repair.validate(operation, arguments)
    kind, action = operation.split(".")
    if action == "create":
        parameters = dict(arguments)
        if "auto_link" in parameters and type(parameters.pop("auto_link")) is not bool:
            raise ValueError("auto_link must be boolean")
        if kind != "handover":
            parameters.pop("body", None)
        else:
            if "flag_for_review" in parameters and type(parameters.pop("flag_for_review")) is not bool:
                raise ValueError("flag_for_review must be boolean")
            if not _accepts(parameters.pop("review_reason", ""), str):
                raise ValueError("review_reason must be text")
        if parameters.get(f"{kind}_id") is not None:
            raise ValueError("explicit allocation is not part of this lifecycle slice")
        try:
            signature, annotations = _builder_contract(kind)
            signature.bind(**parameters)
        except TypeError as exc:
            raise ValueError(str(exc)) from None
        for field, value in parameters.items():
            if not _accepts(value, annotations[field]):
                raise ValueError(f"invalid type for {field}")
        if "body" in arguments and arguments["body"] is not None and not isinstance(arguments["body"], str):
            raise ValueError("body must be text or null")
    else:
        _identifier(arguments.get("id"), "entity id")
        if action == "update" and kind in PATCHERS:
            if set(arguments) != {"id", "patch"} or not isinstance(arguments["patch"], dict):
                raise ValueError("update requires id and patch")
            if "id" in arguments["patch"]:
                raise ValueError("entity identity is immutable")
            if set(arguments["patch"]) - PATCH_FIELDS[kind]:
                raise ValueError("unsupported lifecycle patch field")
            lists = {"components", "location", "related_tasks", "related_issues", "tags"}
            for field, value in arguments["patch"].items():
                annotation = list[str] | None if field in lists else bool if field == "archived" else str | None
                if not _accepts(value, annotation):
                    raise ValueError(f"invalid type for {field}")
        elif operation == "decision.update":
            if set(arguments) - {"id", "patch", "body"} or not isinstance(arguments.get("patch"), dict):
                raise ValueError("decision.update requires id and patch")
            if set(arguments["patch"]) - {"title", "options", "recommendation"}:
                raise ValueError("unsupported decision patch field")
            patch = arguments["patch"]
            for name, annotation in (("title", str), ("options", list[str]), ("recommendation", int | None)):
                if name in patch and not _accepts(patch[name], annotation):
                    raise ValueError(f"invalid type for {name}")
            if "body" in arguments and not isinstance(arguments["body"], str):
                raise ValueError("body must be text")
        elif operation == "note.update":
            if set(arguments) - {"id", "text", "pinned"}:
                raise ValueError("unknown note update argument")
            if "text" in arguments and (not isinstance(arguments["text"], str) or not arguments["text"].strip()):
                raise ValueError("note text is required")
            if "pinned" in arguments and type(arguments["pinned"]) is not bool:
                raise ValueError("pinned must be boolean")
        else:
            allowed = {"archive": {"id"}, "resolve": {"id", "resolved_with", "rationale", "resolved_in"},
                       "drop": {"id", "reason"}, "status": {"id", "status", "reason"}, "supersede": {"id", "new_id"}}[action]
            if set(arguments) - allowed:
                raise ValueError("unknown lifecycle argument")
            if action == "resolve" and type(arguments.get("resolved_with")) is not int:
                raise ValueError("resolved_with must be an integer")
            if action == "drop" and not isinstance(arguments.get("reason"), str):
                raise ValueError("drop requires reason")
            if action == "status" and arguments.get("status") not in domain.HANDOVER_STATUSES:
                raise ValueError("invalid handover status")
            if action == "supersede":
                _identifier(arguments.get("new_id"), "new handover id")
            for field in ("reason", "rationale", "resolved_in"):
                if field in arguments and not _accepts(arguments[field], str | None):
                    raise ValueError(f"invalid type for {field}")


def apply(transaction, operation, arguments):
    if operation in sync.OPERATIONS:
        return sync.apply(transaction, operation, arguments)
    if operation in linear_outbox.OPERATIONS:
        return linear_outbox.apply(transaction, operation, arguments)
    if operation in workflow.OPERATIONS:
        return workflow.apply(transaction, operation, arguments)
    if operation in documents.OPERATIONS:
        return documents.apply(transaction, operation, arguments)
    if operation == projection.RESOLVE:
        return projection.apply_resolve(transaction, arguments)
    if operation in graph_repair.OPERATIONS:
        return graph_repair.apply(transaction, operation, arguments)
    kind, action = operation.split(".")
    if action == "create":
        options = dict(arguments)
        body = options.pop("body", None) if kind != "handover" else None
        extras = {name: options.pop(name) for name in HANDOVER_CREATE_EXTRAS | CREATE_EXTRAS if name in options}
        built = BUILDERS[kind](**options)
        if kind == "handover":
            doc, body = built
        else:
            doc = built
        ident = transaction.create(kind, doc, body)
        if kind == "handover":
            _handover_created(transaction, ident, doc, extras)
        if kind in AUTO_LINKED and extras.get("auto_link", True):
            _auto_link_entity(transaction, kind, ident)
        if kind == "handover":
            workflow.archive_handover_overflow(transaction)
        return
    ident = arguments["id"]
    entity = transaction.snapshot.get(kind, ident, include_body=True)
    doc, body = entity["fields"], entity["body"]
    if operation == "note.update":
        options = {k: v for k, v in arguments.items() if k != "id"}
        if all((k == "text" and v.strip() == (body or "")) or (k == "pinned" and v == doc.get("pinned", False)) for k, v in options.items()):
            return
        doc, body = domain.apply_note_updates(doc, body or "", **options)
    elif action == "update" and kind == "decision":
        doc = domain.apply_decision_patch(doc, arguments["patch"])
        body = arguments.get("body") or body
    elif action == "update":
        patch = dict(arguments["patch"])
        linked_body = patch.get("body")
        body = patch.pop("body", body)
        doc = PATCHERS[kind](doc, **patch)
        if kind == "idea" and patch.get("archived") is False:
            # Unarchiving drops the marker, as the tool's `unarchive` does.
            doc.pop("archived", None)
        transaction.replace(kind, ident, doc, body, before_entity=entity)
        if kind in AUTO_LINKED and linked_body:
            _auto_link_entity(transaction, kind, ident)
        return
    elif operation == "note.archive":
        if entity["archived"]:
            return
        doc = domain.archive_note_doc(doc)
    elif operation == "bug.archive":
        domain.assert_bug_archivable(doc)
        doc = dict(doc, archived=True)
    elif operation == "decision.resolve":
        doc = domain.resolve_decision_doc(doc, **{k: v for k, v in arguments.items() if k != "id"})
    elif operation == "decision.drop":
        doc = domain.drop_decision_doc(doc, reason=arguments["reason"])
    elif operation == "handover.status":
        doc = domain.set_handover_status_doc(doc, status=arguments["status"], reason=arguments.get("reason", ""))
    elif operation == "handover.supersede":
        transaction.snapshot.get("handover", arguments["new_id"], fields=[])
        if ident == arguments["new_id"]:
            raise ValueError("handover cannot supersede itself")
        doc, body = domain.supersede_handover_doc(doc, body or "", new_id=arguments["new_id"])
    transaction.replace(kind, ident, doc, body, before_entity=entity)
    if kind == "handover":
        workflow.archive_handover_overflow(transaction)


def _handover_created(transaction, ident, doc, extras):
    """What commits with a new handover: its supersession, review flag and decision back-references.

    A superseded handover that does not exist is skipped, as the tool skips it
    with a warning, rather than refusing the new handover.
    """
    old = doc.get("supersedes")
    if old and old != ident:
        try:
            transaction.snapshot.get("handover", old, fields=[])
        except KeyError:
            old = None
        if old:
            apply(transaction, "handover.supersede", {"id": old, "new_id": ident})
    if extras.get("flag_for_review"):
        entity = transaction.snapshot.get("handover", ident, include_body=True)
        flagged = domain.flag_handover_doc_for_review(entity["fields"], review_reason=extras.get("review_reason") or "")
        transaction.replace("handover", ident, flagged, entity["body"], before_entity=entity)
    for decision_id in doc.get("open_decisions") or []:
        try:
            decision = transaction.snapshot.get("decision", decision_id, include_body=True)
        except KeyError:
            continue
        linked = domain.link_decision_doc_to_handover(decision["fields"], ident)
        if linked is not None:
            transaction.replace("decision", decision_id, linked, decision["body"], before_entity=decision)


def _auto_link_entity(transaction, kind, ident):
    entity = transaction.snapshot.get(kind, ident, include_body=True)
    linked = workflow._auto_link(transaction, kind, ident, entity["fields"], entity["body"])
    if linked is not entity["fields"]:
        entity = transaction.snapshot.get(kind, ident, include_body=True)
        merged = dict(entity["fields"], links=linked.get("links"))
        transaction.replace(kind, ident, merged, entity["body"], before_entity=entity)
