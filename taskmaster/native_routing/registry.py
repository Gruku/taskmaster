# User intent: one explicit table of which public tools the native core serves,
# so an unrouted tool on a native store refuses loudly instead of reaching a legacy
# writer, and the remaining fallback count is something a test can ratchet to zero.
"""Tool dispatch for native-authority stores."""
from __future__ import annotations

import inspect
import json
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
    from . import (batch, changes, claims, context, documents, epics_phases, handovers,  # noqa: F401
                   links_areas, notes, overview, records, tasks)


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
    ("backlog_index_status", None): (
        f"{_NATIVE}, which keeps no separate derived index to report or rebuild: its search and graph "
        "tables are maintained inside every command. Use `backlog_store_status` for store health."),
    ("backlog_handover_resync", None): (
        f"{_NATIVE}: handovers are database rows, edited handover files are never read back, and the "
        "next write to a handover re-exports its file over any hand edit. The handover index and its cap are maintained on every "
        "write. Make changes with `backlog_handover_update_status` or `backlog_handover_supersede`."),
    ("backlog_issue_resync", None): (
        f"{_NATIVE}: issues are database rows and the issue index is derived from them on every read, "
        "so there is nothing to resync, and edited issue files are never read back. Make changes with "
        "`backlog_issue_update`."),
    ("backlog_link", "reconcile"): (
        f"{_NATIVE}, where every link write records its inverse in the same transaction. Run "
        "`backlog_link(action=\"validate\")` to list any asymmetric or orphaned links, and fix them "
        "with `backlog_link` create or remove."),
    ("backlog_linear", "bootstrap_apply"): (
        f"{_NATIVE}, and adding a Linear workspace still writes `linear.yaml` under the legacy store's "
        "configuration lock. Add the workspace to `.taskmaster/linear.yaml` by hand, using the team "
        "and state ids from `backlog_linear(action=\"probe\")`; `backlog_validate` checks the file."),
    ("backlog_linear", "retry"): (
        f"{_NATIVE}, and pushing queued changes to Linear needs native synchronization, which has not "
        "shipped yet. Queued changes are kept; `backlog_linear(action=\"status\")` lists them."),
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
    with runtime.open_call(database, backlog_dir, session) as call:
        return handler(call, **arguments)


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
    return dispatch(tool, legacy, database, database.parent.parent, session, args, kwargs)
