# User intent: the shared per-person profile (users/<handle>/profile.yaml) exists only as a native entity created by a command, never a file anything writes directly.
"""The `profile` operation family (team-identity-001). Only the coordinator's
identity bootstrap issues `profile.create`; `backlog_batch_update(commands=...)`
refuses it by name (`batch_commands.INTERNAL_OPERATIONS`)."""
from __future__ import annotations

import re

from taskmaster.team_identity import DEGENERATE_NAMES, HANDLE_RE, RESERVED_HANDLES, is_degenerate

OPERATIONS = {"profile.create"}
MAX_EMAILS = 20
MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 200
_CREATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def validate(operation, arguments):
    if set(arguments) != {"handle", "name", "emails", "created"}:
        raise ValueError("profile.create requires handle, name, emails and created")
    handle = arguments["handle"]
    if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
        raise ValueError("invalid profile handle")
    if handle in RESERVED_HANDLES or handle in DEGENERATE_NAMES:
        raise ValueError(f"handle {handle!r} is reserved or a placeholder word")
    name = arguments["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME_LENGTH or is_degenerate(name):
        raise ValueError("profile name must be a real name of 1 to 200 characters")
    emails = arguments["emails"]
    if (not isinstance(emails, list) or len(emails) > MAX_EMAILS
            or not all(isinstance(e, str) and len(e) <= MAX_EMAIL_LENGTH and "@" in e for e in emails)):
        raise ValueError(f"emails must be a list of at most {MAX_EMAILS} addresses")
    if not isinstance(arguments["created"], str) or not _CREATED_RE.fullmatch(arguments["created"]):
        raise ValueError("created must be a UTC timestamp like 2026-10-31T12:00:00Z")


def apply(transaction, operation, arguments):
    # The store stamps `id` from the requested handle (Transaction.create).
    return transaction.create("profile", {"name": arguments["name"], "emails": list(arguments["emails"]),
                                          "created": arguments["created"]}, None, requested_id=arguments["handle"])


def known_profiles(snapshot) -> dict[str, list[str]]:
    """handle -> emails for every live profile in this snapshot."""
    from .queries import MAX_PAGE
    result, cursor = {}, None
    while True:
        page = snapshot.list("profile", fields=["emails"], limit=MAX_PAGE, cursor=cursor)
        for item in page["items"]:
            emails = item["fields"].get("emails")
            result[item["id"]] = [e for e in emails if isinstance(e, str)] if isinstance(emails, list) else []
        cursor = page["cursor"]
        if cursor is None:
            return result
