# User intent: on a team server, remote teammates reach only what the MCP tools need from the coordinator; every host-admin method runs only for the host operator, and the Linear token's variable is never named by a caller.
"""The one classification of coordinator methods for a serve coordinator (team-server-005 design §4, D1).

Pure: no imports from `taskmaster.serve`, the client or the service, so both the serve-process client
and `ServeCoordinator.dispatch` can apply the same table.
"""
from __future__ import annotations

import re

OPERATOR = "operator"                          # == serve.auth.OPERATOR_KEY_ID (a test holds them equal)
SERVER_LINEAR_TOKEN_ENV = "TASKMASTER_LINEAR_TOKEN"

METHODS = frozenset({"retire", "status", "sync", "sync_job", "sync_status", "linear_bootstrap", "linear_retry",
                     "receipt", "execute", "flush", "cancel", "git_run", "git_status", "git_recover", "shutdown"})
# `sync` and `sync_job` import host files, which only the operator can have hand-edited (team-server-012 D1).
# linear_retry is the operator's on a team server (user ruling on the security review's m-7: Linear link/retry).
REMOTE = frozenset({"status", "execute", "flush"})
# 001 refuses these for every caller on a serve coordinator; the gate passes them through (design D5).
SERVER_REFUSED = frozenset({"shutdown", "retire"})
OPERATOR_ONLY = METHODS - REMOTE - SERVER_REFUSED

ADMIN_REFUSED = ("{what} is a host operator action on a team server: run it on the host (the taskmaster CLI, "
                 "or the operator bootstrap token over loopback); nothing was changed")
TAKE_FILE_WHAT = "adopting a flagged file from the host's disk (sync take_file)"
TAKE_FILE_HINT = '; backlog_resolve_conflict(take="store") is available to everyone'
WORKTREE_WHAT = "syncing a host checkout (sync worktree)"
UNKNOWN_WHAT = "an unclassified coordinator method"
HOST_SYNC_WHAT = "importing hand edits of the host's .taskmaster/ files (backlog_sync and the resync tools)"
REMOTE_SYNC_HINT = ("; on a team server, change the backlog with the backlog_* tools, and bring a document from your "
                    "machine with backlog_document_import(content=...)")
HOST_SYNC_REFUSED = ADMIN_REFUSED.format(what=HOST_SYNC_WHAT) + REMOTE_SYNC_HINT
RESTORE_WHAT = "restoring a document from the state backup (document.import restore)"
CLONE_RESTORE_WHAT = "restoring members' branch rows from the state backup (clone.restore)"
RESTORE_REFUSED = ADMIN_REFUSED.format(what=RESTORE_WHAT)
HOST_IMPORT_WHAT = 'importing a document as a host file read (document.import without source="upload")'
HOST_IMPORT_REFUSED = (ADMIN_REFUSED.format(what=HOST_IMPORT_WHAT)
                       + "; import the text with backlog_document_import(content=...)")
# Operations only the operator sends on a team server (user rulings 2026-11-12 on the security review's m-9 and m-7):
# the project manifest is server configuration (a member turning `team` mode off silently stopped profile creation and
# the member-import person check), and a Linear link chooses which issue the operator's token overwrites.
OPERATOR_OPERATIONS = {
    "project.set": "changing the project manifest (backlog_project_set, backlog_project_init and the other "
                   "project.yaml writes)",
    "linear.link": "linking a task to a Linear issue (linear.link)",
    "linear.unlink": "unlinking a task from a Linear issue (linear.unlink)",
}
WHAT = {
    "sync": HOST_SYNC_WHAT,
    "sync_job": HOST_SYNC_WHAT,
    "linear_bootstrap": "adding a Linear workspace (linear_bootstrap)",
    "git_run": "managed Git on the host clone (git_run)",
    "git_recover": "managed Git recovery on the host clone (git_recover)",
    "git_status": "reading the host clone's managed Git state (git_status)",
    "receipt": "reading a request's receipt by id (receipt)",
    "sync_status": "reading a sync's state by id (sync_status)",
    "cancel": "cancelling a queued request by id (cancel)",
    "linear_retry": "retrying Linear pushes (backlog_linear retry)",
}
LINEAR_TOKEN_ENV_REFUSED = (
    "on a team server the Linear token is read only from $TASKMASTER_LINEAR_TOKEN, which the operator sets in "
    "the daemon's environment; pass token_env=\"TASKMASTER_LINEAR_TOKEN\" or leave it empty; "
    "linear.yaml was not changed")

# Part B (design §9): a team server reads a document only through serve.tracked's shared rule.
SERVED_DOC_REFUSED = "{path} is not a git-tracked project file; a team server reads only tracked files"

_KEY = re.compile(r"operator|[0-9a-f]{16}")


def principal_of(origin) -> str | None:
    """The key id an IPC `origin` names; shape validation stays with 002's `_origin`."""
    if isinstance(origin, dict) and set(origin) == {"principal"} and isinstance(origin["principal"], str):
        return origin["principal"]
    return None


def host_only_sync(arguments) -> str | None:
    """What makes this `sync` a host-only action, or None. Any non-False take_file counts (fail closed)."""
    if arguments.get("take_file", False) is not False:
        return TAKE_FILE_WHAT
    if arguments.get("worktree") is not None:
        return WORKTREE_WHAT
    return None


def refusal(method, arguments, principal) -> str | None:
    """None when `principal` may send `method` with `arguments` to a serve coordinator; else the refusal text."""
    if principal == OPERATOR:
        return None
    if not isinstance(method, str):                 # also keeps an unhashable method out of the set lookups
        return ADMIN_REFUSED.format(what=UNKNOWN_WHAT)
    if method == "execute" and isinstance(arguments, dict):
        refused = _document_import_refusal(arguments.get("envelope"))
        if refused is not None:
            return refused
        if _carries(arguments.get("envelope"), "clone.restore"):
            return ADMIN_REFUSED.format(what=CLONE_RESTORE_WHAT)
        for operation, what in OPERATOR_OPERATIONS.items():
            if _carries(arguments.get("envelope"), operation):
                return ADMIN_REFUSED.format(what=what)
    if method in SERVER_REFUSED or method in REMOTE:
        return None
    if method in ("sync", "sync_job"):
        # take_file and worktree keep their own texts (005); every other form gets the remote hint (012 D1, M1).
        what = host_only_sync(arguments) if method == "sync" and isinstance(arguments, dict) else None
        if what is not None:
            return ADMIN_REFUSED.format(what=what) + (TAKE_FILE_HINT if what == TAKE_FILE_WHAT else "")
        return HOST_SYNC_REFUSED
    return ADMIN_REFUSED.format(what=WHAT.get(method, UNKNOWN_WHAT))


_TOO_DEEP = object()


def _document_import_refusal(envelope) -> str | None:
    """For anyone but the operator, every document.import in an execute envelope, batches included, must be an
    upload: a restore is the operator's (012 D10, re-review Q-M2), and an import with no source would be
    recorded as a host file read that never happened, exempt from the identity gate (Codex review, finding 2)."""
    if not isinstance(envelope, dict):
        return None
    found = _document_imports(envelope.get("operation"), envelope.get("arguments"))
    if found is _TOO_DEEP or any(isinstance(item, dict) and "restore" in item for item in found):
        return RESTORE_REFUSED
    if any(not isinstance(item, dict) or item.get("source") != "upload" for item in found):
        return HOST_IMPORT_REFUSED
    return None


def _carries(envelope, name, depth=0) -> bool:
    """True when `envelope` is, or batches (at any depth, fail closed past 8), the operation `name`."""
    if not isinstance(envelope, dict):
        return False
    operation, arguments = envelope.get("operation"), envelope.get("arguments")
    if depth > 8 or operation == name:
        return True
    if operation == "batch" and isinstance(arguments, dict) and isinstance(arguments.get("commands"), list):
        return any(_carries(item, name, depth + 1) for item in arguments["commands"])
    return False


def _document_imports(operation, arguments, depth=0):
    """The arguments of every document.import, walking batch items; _TOO_DEEP for an absurd nesting."""
    if depth > 8:
        return _TOO_DEEP                                 # refused, not walked
    if operation == "document.import":
        return [arguments]
    if operation == "batch" and isinstance(arguments, dict) and isinstance(arguments.get("commands"), list):
        found = []
        for item in arguments["commands"]:
            if isinstance(item, dict):
                inner = _document_imports(item.get("operation"), item.get("arguments"), depth + 1)
                if inner is _TOO_DEEP:
                    return _TOO_DEEP
                found.extend(inner)
        return found
    return []


def action_refusal(what, principal) -> str | None:
    """For a tool action that is not a coordinator method (design D6)."""
    return None if principal == OPERATOR else ADMIN_REFUSED.format(what=what)


def server_token_env(token_env) -> tuple[str, str | None]:
    """The tool side of design D7: empty or the fixed name maps to the fixed name; anything else is refused."""
    if token_env in ("", SERVER_LINEAR_TOKEN_ENV):
        return SERVER_LINEAR_TOKEN_ENV, None
    return token_env, LINEAR_TOKEN_ENV_REFUSED


def bootstrap_entry_refusal(entry) -> str | None:
    """The coordinator side of design D7: only an entry naming exactly the fixed variable passes."""
    if isinstance(entry, dict) and entry.get("token_env") == SERVER_LINEAR_TOKEN_ENV:
        return None
    return LINEAR_TOKEN_ENV_REFUSED


def log_method(method) -> str:
    return method if isinstance(method, str) and method in METHODS else "other"


def log_key(principal) -> str:
    if principal is None:
        return "-"
    return principal if isinstance(principal, str) and _KEY.fullmatch(principal) else "invalid"
