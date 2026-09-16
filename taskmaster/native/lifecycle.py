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
from . import workflow

BUILDERS = {"decision": domain.build_decision_doc, "bug": domain.build_bug_doc,
            "issue": domain.build_issue_doc, "idea": domain.build_idea_doc, "handover": domain.build_handover_doc}
PATCHERS = {"bug": domain.apply_bug_updates, "issue": domain.apply_issue_updates, "idea": domain.apply_idea_updates}
PATCH_FIELDS = {
    "bug": {"title", "status", "severity", "fix_commit", "adopted_into", "promoted_to", "components", "location", "body"},
    "issue": {"title", "status", "severity", "impact", "fixed_in_task", "duplicate_of", "components", "location", "related_tasks", "body"},
    "idea": {"title", "body", "status", "promoted_to", "tags", "related_tasks", "related_issues", "archived"},
}
ENTITY_OPERATIONS = {f"{kind}.create" for kind in BUILDERS} | {f"{kind}.update" for kind in PATCHERS} | {
    "note.update", "note.archive", "bug.archive", "decision.resolve", "decision.drop", "handover.status", "handover.supersede"}
OPERATIONS = ENTITY_OPERATIONS | workflow.OPERATIONS


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
    if operation in workflow.OPERATIONS:
        return workflow.validate(operation, arguments)
    kind, action = operation.split(".")
    if action == "create":
        parameters = dict(arguments)
        if kind != "handover":
            parameters.pop("body", None)
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
    if operation in workflow.OPERATIONS:
        return workflow.apply(transaction, operation, arguments)
    kind, action = operation.split(".")
    if action == "create":
        options = dict(arguments)
        body = options.pop("body", None) if kind != "handover" else None
        built = BUILDERS[kind](**options)
        if kind == "handover":
            doc, body = built
        else:
            doc = built
        ident = transaction.create(kind, doc, body)
        if kind == "handover" and doc.get("supersedes"):
            apply(transaction, "handover.supersede", {"id": doc["supersedes"], "new_id": ident})
        return
    ident = arguments["id"]
    entity = transaction.snapshot.get(kind, ident, include_body=True)
    doc, body = entity["fields"], entity["body"]
    if operation == "note.update":
        options = {k: v for k, v in arguments.items() if k != "id"}
        if all((k == "text" and v.strip() == (body or "")) or (k == "pinned" and v == doc.get("pinned", False)) for k, v in options.items()):
            return
        doc, body = domain.apply_note_updates(doc, body or "", **options)
    elif action == "update":
        patch = dict(arguments["patch"])
        body = patch.pop("body", body)
        doc = PATCHERS[kind](doc, **patch)
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
