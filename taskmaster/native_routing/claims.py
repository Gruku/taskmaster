# User intent: serve `backlog_claim` from the native core, so an agent can hold,
# keep and give back a task without two sessions ever landing on the same one —
# and so a dead session's claim can be reclaimed without a human forcing it.
"""`backlog_claim` over the native core."""
from __future__ import annotations

import json

from taskmaster import backlog_server as bs
from taskmaster.native import claims

from . import reads
from .registry import adapter
from .runtime import error_text


def _members(snapshot, task):
    """The bundle this task is claimed with, as ids, or [] when it stands alone."""
    slug = task.get("bundle")
    if not isinstance(slug, str) or not slug:
        return []
    return sorted(member["id"] for member in reads.bundle_members(snapshot, slug))


def _held_by(connection, session):
    """Every task this session holds, by public id.

    One scan of `task_operational`, which is one row per task and carries
    `locked_by` as its own column — the claim question never needs a document.
    """
    return [row[0] for row in connection.execute(
        "SELECT c.public_id FROM task_operational t JOIN entity_core c ON c.entity_key=t.entity_key "
        "WHERE c.kind='task' AND c.deleted=0 AND json_extract(t.locked_by_json,'$')=? "
        "ORDER BY c.public_id", (session,))]


def _state(snapshot, ident, session):
    return claims.read(reads.get(snapshot, "task", ident)["fields"], task_id=ident,
                       session=session, connection=snapshot.connection)


# Routed as one tool, not per action: the legacy body is a single command, so the
# contract inventory has no per-action targets to route against.
@adapter("backlog_claim")
def backlog_claim(call, *, action, task_id, ttl_seconds):
    session = bs.SESSION_ID

    def answer(payload):
        return call.finish(json.dumps(payload))

    if action not in ("renew", "release", "status"):
        return answer(claims.refusal(
            "unknown_action", f"unknown action {action!r}; expected renew, release or status"))
    try:
        ttl = claims.ttl_seconds(ttl_seconds)
    except ValueError as exc:
        return answer(claims.refusal("invalid_ttl", str(exc)))
    if action != "status" and not task_id:
        return answer(claims.refusal("task_required", f"`{action}` needs a task_id"))
    with call.read() as snapshot:
        if not task_id:
            return answer({"ok": True, "session": session,
                           "claims": [_state(snapshot, ident, session).as_dict()
                                      for ident in _held_by(snapshot.connection, session)]})
        found = reads.find_task(snapshot, task_id)
        if not found:
            return answer(claims.refusal("not_found", f"task `{task_id}` not found"))
        # A bundle is picked as a unit, so it is renewed and released as a unit.
        members = _members(snapshot, found[0])
        states = [_state(snapshot, ident, session) for ident in (members or [task_id])]
    state = next((s for s in states if s.task_id == task_id), states[0])
    if action == "status":
        return answer(claims.ok(state, members=members))
    # The same rule the command enforces inside its own transaction, run here so
    # the refusal is this tool's JSON rather than a bare `Error:` sentence. The
    # command still re-checks it: only its transaction can make the check atomic.
    blocker = claims.blocked_by(states, release=action == "release")
    if blocker is not None:
        return answer(claims.conflict(blocker))
    if not any(s.holder for s in states):
        if action == "renew":
            return answer(claims.refusal(
                "not_claimed", f"task `{task_id}` is not claimed; `backlog_pick_task` takes a claim",
                task_id=task_id))
        return answer(claims.ok(claims.ClaimState(task_id, "", "", None, False, False), members=members))
    arguments = {"id": task_id, "session": session}
    if action == "renew":
        arguments["ttl_seconds"] = ttl
    try:
        call.execute("task.claim_renew" if action == "renew" else "task.claim_release", arguments)
    except (ValueError, KeyError) as exc:
        return answer(claims.refusal("claim_refused", error_text(exc)))
    with call.read() as snapshot:
        after = _state(snapshot, task_id, session)
    return answer(claims.ok(after, renewed_from=state.expires_at if action == "renew" else "",
                            members=members))
