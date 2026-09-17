# User intent: one explicit table of which public tools the native core serves,
# so an unrouted tool on a native store refuses loudly instead of reaching a legacy
# writer, and the remaining fallback count is something a test can ratchet to zero.
"""Tool dispatch for native-authority stores."""
from __future__ import annotations

import inspect
from typing import Callable

from . import gate, runtime

# tool name -> adapter(call, **bound_arguments). Action routers are routed per
# action: ACTIONS[tool] names the actions its adapter serves.
ADAPTERS: dict[str, Callable] = {}
ACTIONS: dict[str, frozenset[str]] = {}


def adapter(tool: str, *, actions: "tuple[str, ...] | None" = None):
    def register(fn):
        if tool in ADAPTERS:
            raise RuntimeError(f"duplicate native adapter for {tool}")
        ADAPTERS[tool] = fn
        if actions is not None:
            ACTIONS[tool] = frozenset(actions)
        return fn
    return register


def _load_families() -> None:
    # Imported for their registrations; each module is one routed family.
    from . import notes, tasks  # noqa: F401


def unrouted_message(tool: str, action: str | None = None) -> str:
    target = f"`{tool}` action `{action}`" if action else f"`{tool}`"
    return (f"Error: {target} is not yet routed through the native core, and this store is a "
            "native authority that the legacy writer cannot open. Nothing was changed.")


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
            return f"Error: unknown action {arguments['action']!r}"
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
        return f"Error: {exc}"
    if database is None:
        return NotImplemented
    return dispatch(tool, legacy, database, database.parent.parent, session, args, kwargs)
