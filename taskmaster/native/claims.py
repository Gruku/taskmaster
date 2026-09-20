"""User intent: let several agents work one backlog at once without two of them
picking the same task — an atomic claim with a TTL, renewable, releasable, and
reclaimable when its holder dies. Constraint: a claim that cannot be *proven*
expired is never stealable, because the cost of guessing is two agents editing
one task, and that cost is paid silently.

The contract, in one place, so the native command layer, the native adapter and
the legacy tool cannot drift on it (D8's dual path):

- **The holder is `locked_by`.** `claim_expires` is only ever read beside it; a
  `claim_expires` with no `locked_by` is not a claim, it is a leftover. That is
  why nothing here has to chase the dozen shipped sites that drop `locked_by` —
  dropping the holder drops the claim.
- **Expiry is liveness *or* the stored TTL, with liveness as the authority**
  (D6). Liveness is three-valued: proven live, proven dead, or unknown. Only a
  proven-dead holder expires a claim early; an unknown one waits out the TTL,
  and a claim with no stored TTL and an unjudgeable holder stands until a human
  passes `force=True` — exactly today's behaviour for a migrated `locked_by`.
- **Renew is `now + ttl`**, never `expires + ttl`, so a chatty agent cannot push
  a claim arbitrarily far into the future.
- **Claims are fully visible** (D6 iv): `locked_by` already renders everywhere,
  and a live peer's claim is mandatory context, not a detail.

Storage is `entity_extensions` (D6), i.e. an ordinary authored field on the task
document, on both stores. No schema bump, no re-backfill; the cost is that
operational state lives in the authored-field bag until someone promotes it to a
`task_operational` column, which is a deliberate migration with its own test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import blockers

HOLDER_FIELD = "locked_by"
EXPIRES_FIELD = "claim_expires"

# Four hours (D6 ii): the unit of work here is a task carried across a long
# session, not a job in a queue. A short TTL would expire claims mid-review-gate
# and turn renew into a heartbeat the agent has to remember, which is the
# bookkeeping N09 exists to remove. Liveness gives the fast path for dead
# processes; the TTL is the slow safety net for everything else.
DEFAULT_TTL_SECONDS = 4 * 60 * 60
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
# The heartbeat window `store_status` already treats as "recently seen".
HEARTBEAT_WINDOW_SECONDS = 60
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(STAMP)


def parse_stamp(value):
    """The instant a stored expiry names, or None when it cannot be read.

    Unreadable is not "expired": a value this code cannot parse proves nothing
    about the holder, and the whole contract is that unproven means untouched.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ttl_seconds(value) -> int:
    """A validated TTL in seconds; 0 means the configured default."""
    if type(value) is not int or isinstance(value, bool):
        raise ValueError(f"ttl_seconds must be an integer number of seconds, "
                         f"0 for the default of {DEFAULT_TTL_SECONDS}")
    if value == 0:
        return DEFAULT_TTL_SECONDS
    if not MIN_TTL_SECONDS <= value <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be 0 or between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}")
    return value


def expiry(ttl: int, *, now=None) -> str:
    return stamp((now or now_utc()) + timedelta(seconds=ttl))


# ── Liveness: three-valued on purpose ───────────────────────────────────────


def _local(name):
    """One of `store`'s local-machine predicates, fetched lazily.

    `store` owns what "this machine" and "this pid" mean — it is what writes the
    `sessions` rows being judged — so the answers are imported rather than
    copied. Lazily, because the native core is not allowed to name `socket` and
    must not depend on the legacy store package at import time.
    """
    from taskmaster import store
    return getattr(store, name)


def session_row(connection, session: str):
    """The `sessions` row for a session, or None. `sessions` is retained local
    state on a native store (`schema.RETAINED_TABLES`) and the same table on a
    legacy one, so one query serves both."""
    if not session:
        return None
    row = connection.execute(
        "SELECT session,pid,host,started,last_seen,cwd,current_tool FROM sessions WHERE session=?",
        (session,)).fetchone()
    return None if row is None else dict(zip(
        ("session", "pid", "host", "started", "last_seen", "cwd", "current_tool"), row))


def liveness(row, *, now=None, hostname=None, pid_alive=None):
    """True (proven live), False (proven dead), or None (cannot be judged).

    `store_status` reports the same signal as a two-valued one, where anything
    it cannot confirm reads as "not live". That is right for a dashboard and
    wrong for a claim: absence of a heartbeat is not proof of death, and a claim
    may only be broken on proof. So the unconfirmable case is its own answer,
    and it keeps the claim.

    The one difference from `store_status`'s rule is deliberate: it gates the pid
    check on `current_tool`, because a session holding no tool is idle. A live
    process is a live holder whether or not it is mid-call, so that gate is not
    applied here.
    """
    if row is None:
        return None
    now = now or now_utc()
    cutoff = (now - timedelta(seconds=HEARTBEAT_WINDOW_SECONDS)).isoformat()
    if (row.get("last_seen") or "") >= cutoff:
        return True
    if row.get("host") == (hostname or _local("_local_host")()) and row.get("pid"):
        return bool((pid_alive or _local("_local_pid_alive"))(row["pid"]))
    # Another machine, or a row with no pid: nothing here proves anything.
    return None


def holder_liveness(connection, holder: str, *, now=None):
    return liveness(session_row(connection, holder), now=now)


# ── Claim state ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimState:
    task_id: str
    holder: str
    expires_at: str
    live: "bool | None"
    expired: bool
    mine: bool

    @property
    def blocking(self) -> bool:
        """Held by someone else and not expired — the fact that must never be
        reported as clear."""
        return bool(self.holder) and not self.mine and not self.expired

    def as_blocker(self):
        """This state as the mandatory resolver's `claim` fact, or None."""
        if not self.holder:
            return None
        return blockers.Claim(holder=self.holder, live=self.live, expired=self.expired)

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "holder": self.holder, "expires_at": self.expires_at,
                "live": self.live, "expired": self.expired,
                "state": "held" if self.holder else "released"}


def read(task, *, task_id: str, session: str, connection, now=None) -> ClaimState:
    """One task document's claim, judged against this store's `sessions` table."""
    now = now or now_utc()
    holder = task.get(HOLDER_FIELD) or ""
    if not isinstance(holder, str):
        # A legacy document can carry any shape here. An unreadable holder is a
        # holder: it blocks, and it is not silently cleared.
        holder = str(holder)
    if not holder:
        return ClaimState(task_id, "", "", None, False, False)
    raw = task.get(EXPIRES_FIELD)
    expires_at = raw if isinstance(raw, str) else ""
    live = holder_liveness(connection, holder, now=now)
    deadline = parse_stamp(expires_at)
    expired = live is False or (deadline is not None and now >= deadline)
    return ClaimState(task_id, holder, expires_at, live, bool(expired), holder == session)


def held(doc, ttl: int, *, session: str, now=None) -> dict:
    """Stamp a claim onto a task document: this session, expiring in `ttl`."""
    doc[HOLDER_FIELD] = session
    doc[EXPIRES_FIELD] = expiry(ttl, now=now)
    return doc


def released(doc) -> dict:
    doc.pop(HOLDER_FIELD, None)
    doc.pop(EXPIRES_FIELD, None)
    return doc


# ── The tool's answers, shared by both stores ───────────────────────────────


HINT = 'backlog_pick_task("{task_id}", force=True) steals it'


def blocked_by(states, *, release):
    """The claim that refuses this change, or None. One rule, two renderings.

    A peer's claim always refuses a renew — renewing someone else's lease is a
    steal wearing a smaller word. A release goes through only once the claim is
    *proven* expired, which is the reclaim path for a dead holder that does not
    need `force`.
    """
    for state in states:
        if state.mine or not state.holder:
            continue
        if release and state.expired:
            continue
        return state
    return None


def lock_refusal(task_id: str, state) -> str:
    """`backlog_pick_task`'s refusal, plus the expiry fact when there is one.

    The refusal stays a refusal even for a dead holder: a pick hands out
    worktree instructions, and two agents acting on them is the failure this
    whole contract exists to prevent. What expiry buys is an *informed* choice —
    the text now says the claim is free to take and how to take it.
    """
    text = (f"Error: task `{task_id}` is locked by another session (`{state.holder}`). "
            f"It is already in-progress elsewhere. Pick a different task, or use "
            f"`backlog_pick_task({task_id}, force=true)` to reclaim it for this session.")
    if not state.expired:
        return text
    why = "its process is gone" if state.live is False else f"its claim expired at {state.expires_at}"
    return (f"{text} That claim has expired ({why}), so reclaiming it is safe — "
            f"`backlog_claim(action=\"release\", task_id=\"{task_id}\")` frees it without force.")


def ok(state: ClaimState, *, renewed_from: str = "", members=()) -> dict:
    answer = dict(state.as_dict(), ok=True)
    if renewed_from:
        answer["renewed_from"] = renewed_from
    if members:
        answer["members"] = list(members)
    return answer


def conflict(state: ClaimState) -> dict:
    return {"ok": False, "error": "claim_conflict", "task_id": state.task_id,
            "holder": state.holder, "live": state.live, "expires_at": state.expires_at,
            "hint": HINT.format(task_id=state.task_id)}


def refusal(error: str, message: str, **extra) -> dict:
    return {"ok": False, "error": error, "message": message, **extra}
