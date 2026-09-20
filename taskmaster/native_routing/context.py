# User intent: serve `backlog_context` from the native core, so an agent orienting
# on a task reads the few facts that bind it instead of every task and every body.
"""`backlog_context` over the native core."""
from __future__ import annotations

from taskmaster.native import context as context_shape

from .registry import adapter


@adapter("backlog_context")
def backlog_context(call, *, focus, scope, budget_bytes, include, cursor):
    # A context question is a read: no command, no projection drain, no `[seq N]`
    # stamp. The answer's own `sequence` is what a caller correlates against, and
    # the session comes from the call rather than the database (N08 constraint 4).
    with call.read() as snapshot:
        try:
            return snapshot.context(focus, scope=scope, budget_bytes=budget_bytes,
                                    include=include, cursor=cursor, session=call.session)
        except (ValueError, KeyError) as exc:
            return context_shape.refusal(exc)
