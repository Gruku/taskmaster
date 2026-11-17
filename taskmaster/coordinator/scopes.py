# User intent: one home for every fixed internal caller_scope the coordinator and daemon mint for themselves, so a
# remote member can never name one and the list can't drift (team-server-004).
"""The scope vocabulary (team-server-004 D1).

A `caller_scope` is either a person's session (`<handle>@<nonce>`, accepted only with that person's authenticated
key) or one of the fixed internal scopes below, used by server-internal work submitted in process under `LOCAL`.
No internal scope contains `@`, so none parses as a remote session (`claims.remote_handle` is None for all).
"""
from __future__ import annotations

from taskmaster.native import claims

IDENTITY = "coordinator-identity"
LINEAR_SETTLE = "linear-settle"
EXPLICIT_SYNC = "explicit-sync"
EXPLICIT_GIT = "explicit-git"
BACKLOG_SYNC = "backlog-sync"
BACKLOG_SYNC_JOB = "backlog-sync-job"
EXPORT_RESTORE = "serve-export-restore"
CLAIM_KEEPER = "claim-keeper"
SYNC_PREFIX = "sync-"
GIT_PREFIX = "git:"

INTERNAL = frozenset({IDENTITY, LINEAR_SETTLE, EXPLICIT_SYNC, EXPLICIT_GIT, BACKLOG_SYNC, BACKLOG_SYNC_JOB,
                      EXPORT_RESTORE, CLAIM_KEEPER})
INTERNAL_PREFIXES = (SYNC_PREFIX, GIT_PREFIX)
NAMED_SCOPE_METHODS = frozenset({"receipt", "cancel", "sync_status", "linear_retry", "sync", "git_run"})
CLAIM_OPERATIONS = ("task.pick", "task.claim_renew", "task.claim_release")


def is_internal(scope) -> bool:
    return isinstance(scope, str) and (scope in INTERNAL or scope.startswith(INTERNAL_PREFIXES))


def claim_operations(request) -> list:
    found = []
    items = [request]
    if request.get("operation") == "batch":
        items += list((request.get("arguments") or {}).get("commands") or [])
    for item in items:
        if isinstance(item, dict) and item.get("operation") in CLAIM_OPERATIONS:
            found.append((item["operation"], item.get("arguments") or {}))
    return found


def named_sessions(method, arguments) -> list:
    """Every session string a coordinator message names. Non-str values are returned as-is (callers refuse them)."""
    arguments = arguments or {}
    if method in NAMED_SCOPE_METHODS:
        return [arguments.get("caller_scope")]
    if method == "execute":
        envelope = arguments.get("envelope") or {}
        if not isinstance(envelope, dict):
            return []
        found = [envelope.get("caller_scope")]
        found += [args.get("session") for _op, args in claim_operations(envelope) if "session" in args]
        return found
    return []


def log_session(session) -> str:
    """A session safe to log: a remote-shaped one as-is, anything else as `invalid`."""
    return session if isinstance(session, str) and claims.REMOTE_SESSION_RE.fullmatch(session) else "invalid"
