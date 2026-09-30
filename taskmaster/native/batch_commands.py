# User intent: give `backlog_batch_update` several mutations in ONE atomic
# transaction with revision preconditions, without letting that all-or-nothing
# contract touch the tool's `operations` line form, which is deliberately partial.
# Constraint: the two forms never share a code path and never merge — a call
# carrying both is refused, not resolved.
"""The `commands=` argument contract, decided before any store is opened.

Pure: no database, no filesystem, no session. Both the legacy tool body and the
native adapter call `check` first, so one set of words answers a malformed call on
either store. A call carrying `commands` is answered as JSON, because a structured
request deserves a structured answer; the line form keeps every text answer it has
today, refusals included, so its path is untouched.
"""
from __future__ import annotations

import json

from .contracts import MAX_BATCH_COMMANDS, validate_operation

# Operations that exist to serve exactly one caller with that caller's own rules,
# refused by name rather than validated, with where to go instead. The batch lines
# adapter's contract is partial apply and the viewer's writes carry a store-wide
# If-Match. The claim operations take `session` as an argument, and only a tool
# that supplies this server's own session may pass one: through `commands=` any
# caller could release, renew or force-take another session's claim. The import
# stores a body the importer read from a declared file, and provenance vouches for
# exactly that; through `commands=` the body would be whatever the caller typed.
INTERNAL_OPERATIONS = {
    "task.batch_line": "Use `operations` lines for batch lines.",
    "epic.batch_line": "Use `operations` lines for batch lines.",
    "task.viewer_create": "Use the viewer for a viewer edit.",
    "task.viewer_update": "Use the viewer for a viewer edit.",
    "task.viewer_archive": "Use the viewer for a viewer edit.",
    "task.pick": "A claim is taken for this session by `backlog_pick_task`.",
    "task.claim_renew": "A claim is renewed for this session by `backlog_claim(action=\"renew\")`.",
    "task.claim_release": "A claim is released for this session by `backlog_claim(action=\"release\")`.",
    "document.import": "Documents are imported from their declared files by `backlog_document_import`.",
}
# Arguments a tool supplies from the call itself, never from its caller: who wrote
# a note, and the merge ladder a task's merge gate is judged against. The native
# adapter fills them exactly as the owning tool does.
RESERVED_ARGUMENTS = {
    "note.create": {"author": "a note is authored as `backlog_note` authors it"},
    "task.merge": {"merge_targets": "the ladder is the project manifest's, as `backlog_record_merge` "
                                    "resolves it"},
}
# `task.update` can name any holder; the claim contract is the only writer of one.
_HOLDER_FIELD = "locked_by"

_PARTIAL = ("the `operations` line form applies what it can and reports an error per bad line")
_ATOMIC = ("the `commands` form commits in one transaction, all of it or none of it")


def refused(code: str, detail: str) -> str:
    return json.dumps({"ok": False, "error": code, "applied": False, "detail": detail})


def _message(exc: BaseException) -> str:
    return exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)


def check(operations: str, commands, expected_revisions, atomic) -> str | None:
    """The refusal this call earns, or `None` when it is well formed.

    The answer's shape follows the form the caller reached for: text for the line
    form, JSON once `commands` is present.
    """
    if commands is None:
        if atomic is not None:
            return (f"Error: `atomic` applies only to the `commands` form. Here {_PARTIAL}, and that "
                    "partial contract is deliberate — it cannot be made all-or-nothing. Pass the "
                    "mutations as `commands` for one atomic transaction. Nothing was applied.")
        if expected_revisions is not None:
            return ("Error: `expected_revisions` applies only to the `commands` form; a line carries no "
                    "revision precondition. Pass the mutations as `commands` to precondition them. "
                    "Nothing was applied.")
        return None
    if not isinstance(commands, list):
        return refused("invalid_command", "`commands` must be a list of {operation, arguments} objects")
    if operations.strip():
        return refused("both_forms", f"{_PARTIAL}, while {_ATOMIC}. The two contracts are opposite, so "
                                     "pass one form or the other — never both.")
    if not commands:
        return refused("empty_commands", "`commands` was empty; pass at least one {operation, arguments} object")
    if len(commands) > MAX_BATCH_COMMANDS:
        return refused("too_many_commands",
                       f"{len(commands)} commands were passed and one transaction holds at most "
                       f"{MAX_BATCH_COMMANDS}. Nothing was applied; split the batch.")
    if atomic is False:
        return refused("not_atomic", f"{_ATOMIC}, so `atomic=False` cannot be honoured. Omit `atomic` or "
                                     f"pass atomic=True; for per-line partial apply use `operations`, where "
                                     f"{_PARTIAL}.")
    if expected_revisions is not None and not isinstance(expected_revisions, list):
        return refused("invalid_command",
                       "`expected_revisions` must be a list of {kind, id, revision} objects")
    for item in commands:
        if not isinstance(item, dict) or set(item) - {"operation", "arguments"} or "operation" not in item:
            return refused("invalid_command",
                           f"each command is an {{operation, arguments}} object, not {item!r}")
        operation = item["operation"]
        if operation in INTERNAL_OPERATIONS:
            return refused("internal_operation",
                           f"`{operation}` serves one caller with rules this form cannot honour; it is not "
                           f"callable here. {INTERNAL_OPERATIONS[operation]}")
        arguments = item.get("arguments", {})
        try:
            validate_operation(operation, arguments, in_batch=True)
        except (ValueError, KeyError, TypeError) as exc:
            return refused("invalid_command", f"`{operation}`: {_message(exc)}")
        reserved = _reserved(operation, arguments)
        if reserved is not None:
            return refused("reserved_argument", f"`{operation}`: {reserved}")
    return None


def _reserved(operation, arguments) -> str | None:
    """Why this command names something only the call itself may supply, or `None`."""
    for name, owner in RESERVED_ARGUMENTS.get(operation, {}).items():
        if name in arguments:
            return f"`{name}` is not an argument here: {owner}."
    if operation == "task.update" and arguments.get("field") == _HOLDER_FIELD:
        return (f"`{_HOLDER_FIELD}` names a claim holder, which only the claim tools write for this "
                "session: `backlog_pick_task` and `backlog_claim`.")
    return None


def legacy_refusal() -> str:
    """A legacy store has no revisions and no single all-or-nothing transaction."""
    return refused("legacy_store",
                   "the `commands` form needs revision preconditions and one all-or-nothing transaction, "
                   "which only a native-authority store provides; this project's store is a legacy "
                   f"backlog. Use `operations` lines, where {_PARTIAL}. Nothing was applied.")


def receipt_answer(receipt: dict, count: int) -> str:
    """The committed answer: what applied, at which sequence, at which revisions."""
    return json.dumps({
        "ok": True, "atomic": True, "applied": True, "commands": count,
        "receipt": {
            "request_id": receipt["request_id"],
            "commit_seq": receipt["commit_seq"],
            # The receipt's `fields` are the whole document of every touched
            # entity; a caller that wants them reads the entity. Identity,
            # revision and sequence are what a precondition is built from.
            "affected": [{"kind": entry["kind"], "id": entry["id"],
                          "revision": entry["revision"], "last_seq": entry["last_seq"]}
                         for entry in receipt["affected"]],
        },
    })
