# User intent: one explicit table of which public tools the native core serves,
# so an unrouted tool on a native store refuses loudly instead of reaching a legacy
# writer, and the remaining fallback count is something a test can ratchet to zero.
"""Tool dispatch for native-authority stores."""
from __future__ import annotations

import inspect
import json
import threading
from typing import Callable

from . import gate, runtime

# tool name -> adapter(call, **bound_arguments). Action routers are routed per
# action: ACTIONS[tool] names the actions its adapter serves.
ADAPTERS: dict[str, Callable] = {}
ACTIONS: dict[str, frozenset[str]] = {}
# How each router words an action it does not know; routers disagree (text or JSON).
UNKNOWN_ACTION: dict[str, Callable[[str], str]] = {}


def adapter(tool: str, *, actions: "tuple[str, ...] | None" = None, unknown=None):
    def register(fn):
        if tool in ADAPTERS:
            raise RuntimeError(f"duplicate native adapter for {tool}")
        ADAPTERS[tool] = fn
        if actions is not None:
            ACTIONS[tool] = frozenset(actions)
        if unknown is not None:
            UNKNOWN_ACTION[tool] = unknown
        return fn
    return register


def _load_families() -> None:
    # Imported for their registrations; each module is one routed family.
    from . import (batch, changes, claims, conflicts, context, documents, epics_phases,  # noqa: F401
                   handovers, links_areas, notes, overview, records, resync, tasks)


_NATIVE = "this project's store is a native authority"
# What an operator sees for each public tool/action the native core does not serve:
# why it cannot run on a native store, and what to do instead. A test holds this
# table equal to the unrouted inventory, so a new unrouted tool needs its own entry.
GUIDANCE: dict[tuple[str, "str | None"], str] = {
    ("backlog_init", None): (
        f"{_NATIVE}, so the project is already initialized. Use `backlog_status` to see it."),
    ("backlog_migrate_v3", None): (
        f"{_NATIVE}, which is past every legacy migration; adopting it into the legacy store would "
        "undo that, and the legacy store refuses native databases. No migration is needed. "
        "Use `backlog_store_status` to inspect the store."),
    ("backlog_migrate_v4", None): (
        f"{_NATIVE}, which is past every legacy migration; adopting it into the legacy store would "
        "undo that, and the legacy store refuses native databases. No migration is needed. "
        "Use `backlog_store_status` to inspect the store."),
    ("backlog_canonicalize_layout", None): (
        f"{_NATIVE}, and a native store can only live under `.taskmaster/`, so the layout is "
        "already canonical. No action is needed."),
    ("backlog_backfill_lanes", None): (
        f"{_NATIVE}, and the one-time lane backfill has only a legacy implementation. Set a lane per "
        "task with `backlog_update_task(task_id, field=\"lane\", value=...)`, and use "
        "`backlog_skip_gate` for gates in-flight work has already passed."),
    ("backlog_link", "reconcile"): (
        f"{_NATIVE}, where every link write records its inverse in the same transaction. Run "
        "`backlog_link(action=\"validate\")` to list any asymmetric or orphaned links, and fix them "
        "with `backlog_link` create or remove."),
}
# Routers that answer errors as JSON rather than text.
JSON_ERRORS = frozenset({"backlog_changes_since", "backlog_claim", "backlog_context", "backlog_link",
                         "backlog_linear"})


def unrouted_message(tool: str, action: str | None = None) -> str:
    target = f"`{tool}` action `{action}`" if action else f"`{tool}`"
    reason = GUIDANCE.get((tool, action), f"{_NATIVE}, which the legacy writer cannot open, and the "
                                           "native core does not serve this tool.")
    text = f"{target} cannot run here: {reason} Nothing was changed."
    return json.dumps({"error": text}) if tool in JSON_ERRORS else f"Error: {text}"


def dispatch(tool: str, legacy: Callable, database, backlog_dir, session: str, args, kwargs):
    _load_families()
    bound = inspect.signature(legacy).bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    handler = ADAPTERS.get(tool)
    if handler is None:
        return unrouted_message(tool)
    if tool in ACTIONS and arguments.get("action") not in ACTIONS[tool]:
        if "action" in arguments and not _known_action(legacy, arguments["action"]):
            unknown = UNKNOWN_ACTION.get(tool, lambda action: f"Error: unknown action {action!r}")
            return unknown(arguments["action"])
        return unrouted_message(tool, arguments.get("action"))
    if not getattr(_DEPTH, "calls", 0):
        _replay_merge_stamps(database, backlog_dir, session)
    with runtime.open_call(database, backlog_dir, session) as call:
        outermost = not getattr(_DEPTH, "calls", 0)
        _DEPTH.calls = getattr(_DEPTH, "calls", 0) + 1
        try:
            result = handler(call, **arguments)
        finally:
            _DEPTH.calls -= 1
        return _with_flag_notices(call, result) if outermost else result


def _replay_merge_stamps(database, backlog_dir, session):
    """Apply merge stamps a hook queued while no coordinator ran (merge_stamps.py). The
    server may start one; a hook may not. Advisory: never costs the caller its call."""
    from . import merge_stamps
    if not merge_stamps.has_pending(backlog_dir):
        return
    try:
        merge_stamps.replay(backlog_dir, database, session, autostart=True)
    except Exception as exc:  # noqa: BLE001 -- the stamps stay queued for the next call
        merge_stamps.hook_log(backlog_dir, f"replaying queued merge stamps failed ({exc!r}); kept queued")


# Nesting depth of native dispatches on this thread: only the outermost call names
# the flagged files, as the legacy wrapper leaves a nested tool's result alone.
_DEPTH = threading.local()


def _with_flag_notices(call, result):
    """Name every flagged file on every native result, until it is resolved, as the
    legacy `_attach_conflict_notices` does for a legacy store. Advisory: a failure
    to look never costs the caller the result."""
    from taskmaster import backlog_server as bs
    from taskmaster.native import projection as outbox
    try:
        connection = call.connection
        if connection.in_transaction or not outbox.flagged_files(connection):
            return result
        conflicts = [dict(zip(("file", "kind", "id"), row)) for row in connection.execute(
            "SELECT file,kind,id FROM projection_conflict ORDER BY flagged_at,file")]
    except Exception:  # noqa: BLE001 -- advisory, see docstring
        return result
    from .conflicts import flag_notice
    return bs._with_conflict_notices(result, conflicts, flag_notice)


def _known_action(legacy, action) -> bool:
    annotation = inspect.signature(legacy).parameters["action"].annotation
    return action in getattr(annotation, "__args__", ())


def route(tool: str, legacy: Callable, backlog_path, session: str, args, kwargs):
    """The native answer for this call, or `NotImplemented` for a legacy store."""
    try:
        database = gate.native_database(backlog_path)
    except gate.NativeUnavailable as exc:
        # Same seam as an unrouted tool, so the same contract: the JSON routers
        # parse whatever comes back, and a bare "Error: ..." crashes them.
        return json.dumps({"error": str(exc)}) if tool in JSON_ERRORS else f"Error: {exc}"
    if database is None:
        return NotImplemented
    from taskmaster.coordinator.protocol import ServiceUnavailable
    try:
        return dispatch(tool, legacy, database, database.parent.parent, session, args, kwargs)
    except ServiceUnavailable as exc:
        return json.dumps(exc.public_payload()) if tool in JSON_ERRORS else f"Error: {exc}"
