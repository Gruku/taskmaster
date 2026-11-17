# User intent: on the shared server a teammate's claims stay theirs while their agent's connection is open, lapse in
# 15 minutes once it is gone, and survive a server restart with one grace TTL (team-server-010).
"""The claim keeper: connection-bound claim renewal, the short disconnected TTL and the restart grace.

`decide` is the pure rule (design D3). The scan, the writes and the thread are below it.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import threading
import uuid

import anyio
from fastmcp.server.middleware import Middleware
from mcp.types import TextContent

from taskmaster.native import actor, claims, workflow
from taskmaster.native.contracts import Conflict
from taskmaster.native.queries import Repository

LOG = logging.getLogger("taskmaster.serve")
HEARTBEAT_FILE = "claim-keeper.json"
HEARTBEAT_VERSION = 1
KEEP_SECONDS = 60.0          # the pass cadence
SUBMIT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Row:
    """One claimed task, read from a snapshot. `expires` is the holder's own stamp, else None."""
    task_id: str
    revision: int
    holder: str
    status: str
    bundle: str
    expires: "datetime | None"


@dataclass(frozen=True)
class Action:
    kind: str                                  # "renew" | "shorten" | "grace"
    holder: str
    ttl: int
    members: tuple                             # (task_id, revision) of the group


@dataclass(frozen=True)
class Outage:
    """What the last heartbeat knew. `at` None means no usable heartbeat: every remote claim gets the grace."""
    at: "datetime | None"
    connected: frozenset = frozenset()


def _live_rows(group):
    return [row for row in group if row.status not in claims.TERMINAL_STATUSES]


def decide(group, *, now, connected: bool, outage: "Outage | None" = None) -> "str | None":
    """One decision per group: one write re-stamps every member, so the rule is judged on the whole group."""
    rows = _live_rows(group)
    if not rows:
        return None
    holder = rows[0].holder
    if claims.remote_handle(holder) is None or claims._is_channel(holder):
        return None                            # host liveness rules stay (addendum section 7)
    stamps = [row.expires for row in rows]
    if outage is not None:
        if outage.at is None or holder in outage.connected or all(s is None or s > outage.at for s in stamps):
            return "grace"
        return None
    if connected:
        if any(s is None for s in stamps):
            return "renew"
        return "renew" if min(stamps) - now < timedelta(seconds=claims.RENEW_BELOW_SECONDS) else None
    horizon = now + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS)
    return "shorten" if all(s is None or s > horizon for s in stamps) else None


def group(rows):
    """Rows grouped by (holder, bundle or task id): a bundle is written once, every member pinned."""
    groups: dict = {}
    for row in rows:
        groups.setdefault((row.holder, row.bundle or row.task_id), []).append(row)
    return list(groups.values())


_TTL = {"renew": claims.DEFAULT_TTL_SECONDS, "shorten": claims.DISCONNECTED_TTL_SECONDS,
        "grace": claims.DISCONNECTED_TTL_SECONDS}


def plan(rows, *, now, is_connected, outage: "Outage | None" = None) -> list:
    actions = []
    for members in group(rows):
        holder = members[0].holder
        kind = decide(members, now=now, connected=bool(is_connected(holder)), outage=outage)
        if kind is not None:
            live = _live_rows(members)
            actions.append(Action(kind, holder, _TTL[kind], tuple((row.task_id, row.revision) for row in live)))
    return actions


# -- the heartbeat (D5) ------------------------------------------------------------------------------------------

def heartbeat_path(root) -> Path:
    from .auth import serve_directory
    return serve_directory(Path(root)) / HEARTBEAT_FILE


def write_heartbeat(root, store_id, *, now, connected) -> None:
    from .principals import replace_private
    payload = {"version": HEARTBEAT_VERSION, "store_id": store_id, "at": claims.stamp(now),
               "connected": sorted(connected)}
    replace_private(heartbeat_path(root), json.dumps(payload).encode("utf-8"))


def read_heartbeat(root, store_id, *, now) -> "Outage | None":
    """The last heartbeat as an Outage, or None when it is missing, unreadable, for another store or the wrong
    version. An `at` in the future (clock skew) is clamped to `now`."""
    try:
        data = json.loads(heartbeat_path(root).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != HEARTBEAT_VERSION or data.get("store_id") != store_id
                or not isinstance(data.get("connected"), list)):
            return None
        at = claims.parse_stamp(data.get("at"))
        if at is None:
            return None
        sessions = frozenset(s for s in data["connected"] if isinstance(s, str))
    except (OSError, ValueError):
        return None
    return Outage(min(at, now), sessions)


# -- the scan (D3a) ----------------------------------------------------------------------------------------------

REVISION_CONFLICT = "revision conflict for"
SCAN_SQL = (
    "SELECT c.public_id, c.revision, json_extract(t.locked_by_json,'$') FROM task_operational t "
    "JOIN entity_core c ON c.entity_key = t.entity_key "
    "WHERE c.kind='task' AND c.deleted=0 AND json_extract(t.locked_by_json,'$') LIKE '%@%' "
    "AND COALESCE(json_extract(c.status_json,'$'),'') NOT IN ('done','archived')")


def _safe(value) -> str:
    from .app import log_safe     # app imports this module's users; import late to avoid a cycle
    return log_safe(value)


def scan(connection):
    """(rows, mixed) from one read-only snapshot. `mixed` maps (bundle, holder) to (other holder, member) for
    bundles in which another session holds a member: those rows are left out and nothing is written for them."""
    rows, mixed = [], {}
    with Repository(connection).snapshot() as snapshot:
        found = [(ident, revision, holder) for ident, revision, holder in connection.execute(SCAN_SQL).fetchall()
                 if claims.remote_handle(holder) is not None and not claims._is_channel(holder)]
        for ident, revision, holder in found:
            fields = snapshot.get("task", ident)["fields"]
            try:
                bundle = workflow._bundle_slug(fields, ident)
            except ValueError:
                LOG.debug("claim keeper: task %s has a malformed bundle; skipped", _safe(ident))
                continue
            stamp = fields.get(claims.EXPIRES_FIELD) if fields.get(claims.EXPIRES_FOR_FIELD) == holder else None
            rows.append(Row(ident, revision, holder, fields.get("status", ""), bundle, claims.parse_stamp(stamp)))
        for bundle in sorted({row.bundle for row in rows if row.bundle}):
            members = workflow._bundle_members(connection, bundle)
            if not members:
                continue
            held = dict(connection.execute(
                "SELECT c.public_id, json_extract(t.locked_by_json,'$') FROM task_operational t "
                "JOIN entity_core c ON c.entity_key=t.entity_key WHERE c.kind='task' AND c.deleted=0 "
                f"AND c.public_id IN ({','.join('?' for _ in members)})", members).fetchall())
            for holder in sorted({row.holder for row in rows if row.bundle == bundle}):
                other = next(((who, member) for member in members
                              for who in [held.get(member)] if who and who != holder), None)
                if other is not None:
                    mixed[(bundle, holder)] = other
    rows = [row for row in rows if (row.bundle, row.holder) not in mixed]
    return rows, mixed


# -- the keeper (D1, D2, D5, D6) ---------------------------------------------------------------------------------

class ClaimKeeper:
    def __init__(self, coordinator, sessions, root, *, clock=claims.now_utc):
        self.coordinator, self.sessions, self.root, self.clock = coordinator, sessions, Path(root), clock
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._thread = None
        self._warned_mixed: set = set()
        self._warned_failed: set = set()

    # -- reading and writing -------------------------------------------------------------------------------------
    def scan(self):
        with closing(self.coordinator._connect(readonly=True)) as connection:
            return scan(connection)

    def _note_mixed(self, mixed) -> None:
        for key, (other, member) in mixed.items():
            if key in self._warned_mixed:
                continue
            self._warned_mixed.add(key)
            bundle, holder = key
            LOG.warning("claim keeper: bundle %s has members held by another session (`%s` on `%s`), so `%s`'s claims "
                        "on it are not renewed or shortened, and they get no restart grace; release `%s`'s claim or "
                        "take `%s` out of the bundle", _safe(bundle), _safe(other), _safe(member), _safe(holder),
                        _safe(member), _safe(member))
        self._warned_mixed &= set(mixed)

    def envelope(self, action: Action) -> dict:
        from taskmaster.coordinator import scopes
        return {"protocol": 2, "store_id": self.coordinator.identity["store_id"], "caller_scope": scopes.CLAIM_KEEPER,
                "request_id": uuid.uuid4().hex, "operation": "task.claim_renew",
                "arguments": {"id": action.members[0][0], "session": action.holder, "ttl_seconds": action.ttl},
                "expected_revisions": [{"kind": "task", "id": ident, "revision": revision}
                                       for ident, revision in action.members],
                "actor": actor.advisory(actor.INTERNAL_AGENT)}

    def apply(self, action: Action) -> None:
        self.coordinator.submit(self.envelope(action)).result(SUBMIT_TIMEOUT)

    def _apply_all(self, actions) -> None:
        """Every group on its own: one failure never ends the pass (design I2)."""
        for action in actions:
            if self._stop.is_set():
                return
            task = action.members[0][0]
            try:
                self.apply(action)
            except Conflict as exc:
                if str(exc).startswith(REVISION_CONFLICT):
                    LOG.debug("claim keeper: task %s moved since its scan; skipped", _safe(task))
                elif (task, action.holder) not in self._warned_failed:
                    self._warned_failed.add((task, action.holder))
                    LOG.warning("claim keeper: %s of task %s failed: Conflict", action.kind, _safe(task))
            except Exception as exc:  # noqa: BLE001 - isolated per group
                LOG.warning("claim keeper: %s of task %s failed: %s", action.kind, _safe(task), _safe(type(exc).__name__))

    # -- passes --------------------------------------------------------------------------------------------------
    def run_pass(self, *, now=None) -> None:
        """One steady-state pass: renew connected claims, shorten disconnected ones. Idle unless the sessions
        registry is attached to a running transport manager (before that, everyone reads as disconnected)."""
        if self._stop.is_set() or not self.sessions.attached():
            return
        now = now or self.clock()
        try:
            rows, mixed = self.scan()
        except Exception as exc:  # noqa: BLE001 - E3
            LOG.warning("claim keeper: pass could not read the claims (%s); claims keep their stored expiry",
                        _safe(type(exc).__name__))
            return
        self._note_mixed(mixed)
        self._apply_all(plan(rows, now=now, is_connected=self.sessions.connected))
        self._heartbeat(rows, now)

    def _heartbeat(self, rows, now) -> None:
        if self._stop.is_set() or not self.sessions.attached():
            return
        try:
            connected = {row.holder for row in rows if self.sessions.connected(row.holder)}
            write_heartbeat(self.root, self.coordinator.identity["store_id"], now=now, connected=connected)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("claim keeper: could not write its heartbeat (%s)", _safe(type(exc).__name__))

    def startup(self, *, now=None) -> None:
        """One grace TTL for the claims of sessions that were alive at the last heartbeat (D5)."""
        now = now or self.clock()
        outage = read_heartbeat(self.root, self.coordinator.identity["store_id"], now=now)
        if outage is None:
            LOG.info("claim keeper: no usable heartbeat; every remote claim gets the restart grace")
            outage = Outage(None)
        try:
            rows, mixed = self.scan()
        except Exception as exc:  # noqa: BLE001 - E3
            LOG.warning("claim keeper: startup grace could not read the claims (%s); claims keep their stored expiry",
                        _safe(type(exc).__name__))
            return
        self._note_mixed(mixed)
        self._apply_all(plan(rows, now=now, is_connected=lambda holder: False, outage=outage))

    # -- the thread ----------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="taskmaster-claim-keeper", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(KEEP_SECONDS)
            self._wakeup.clear()
            if self._stop.is_set():
                return
            try:
                self.run_pass()
            except Exception as exc:  # noqa: BLE001 - the loop outlives any one pass (E3)
                LOG.warning("claim keeper: pass failed (%s)", _safe(type(exc).__name__))

    def wake(self) -> None:
        self._wakeup.set()

    def request_stop(self) -> None:
        self._stop.set()
        self._wakeup.set()

    def stop(self, timeout: float = 10.0) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)


# -- the adoption notice (D7) ------------------------------------------------------------------------------------

NOTICE_LIMIT = 10


def _clock(moment) -> str:
    return moment.strftime("%H:%MZ") if moment is not None else "no expiry"


def adoptable_notice(connection, session, now) -> "str | None":
    """E5: the tasks a person's earlier, disconnected sessions still hold, with the remedies. Terminal tasks are
    never named. None when there is nothing to say."""
    from taskmaster.native_routing.claims import _adoptable
    found = []
    with Repository(connection).snapshot() as snapshot:
        for ident in _adoptable(connection, session):
            fields = snapshot.get("task", ident)["fields"]
            if fields.get("status") in claims.TERMINAL_STATUSES:
                continue
            holder = fields.get(claims.HOLDER_FIELD) or ""
            stamp = claims.parse_stamp(fields.get(claims.EXPIRES_FIELD)) \
                if fields.get(claims.EXPIRES_FOR_FIELD) == holder else None
            found.append((ident, holder, stamp))
    if not found:
        return None
    named = [f"`{ident}` ({'lapsed at' if stamp is not None and stamp <= now else 'until'} {_clock(stamp)})"
             for ident, _holder, stamp in found[:NOTICE_LIMIT]]
    holders = sorted({holder for _ident, holder, _stamp in found})
    text = (f"Note: your earlier session{'s' if len(holders) > 1 else ''} "
            f"{' and '.join(f'`{holder}`' for holder in holders)} "
            f"holds {', '.join(named[:-1]) + ' and ' + named[-1] if len(named) > 1 else named[0]}")
    if len(found) > NOTICE_LIMIT:
        text += f" and {len(found) - NOTICE_LIMIT} more; backlog_claim(action=\"status\") lists them"
    first = found[0][0]
    return (text + f". An unexpired claim lapses at its time unless this session takes it over; a lapsed one can still "
            f"be taken back: `backlog_pick_task(\"{first}\")` or `backlog_claim(action=\"renew\", task_id=\"{first}\")`.")


class AdoptionNotice(Middleware):
    """Over HTTP only: a connection's first successful tool call gets one extra text block naming the claims its
    person's earlier sessions still hold. `content[0]` is never touched. Channel sessions never get it."""

    def __init__(self, coordinator, sessions):
        self.coordinator, self.sessions = coordinator, sessions

    async def on_call_tool(self, context, call_next):
        from fastmcp.server.dependencies import get_http_request
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        try:
            request = get_http_request()
        except RuntimeError:
            return result
        entry = self.sessions.by_transport(request.headers.get("mcp-session-id") or "")
        if entry is None or not self.sessions.mark_announced(entry):
            return result
        try:
            text = await anyio.to_thread.run_sync(self._notice, entry.session)
        except Exception as exc:  # noqa: BLE001 - a notice must never fail the person's call
            LOG.warning("claim keeper: could not build the adoption notice (%s)", _safe(type(exc).__name__))
            return result
        content = getattr(result, "content", None)
        if text and isinstance(content, list):
            content.append(TextContent(type="text", text=text))
        return result

    def _notice(self, session) -> "str | None":
        with closing(self.coordinator._connect(readonly=True)) as connection:
            return adoptable_notice(connection, session, claims.now_utc())
