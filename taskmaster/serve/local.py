# User intent: the local profile of `taskmaster serve` (local-server-002): one hidden server per project that every Claude
# Code session on the store shares, so RAM is paid once; the contract with the stdio bridge lives in docs/plans/2026-11-22-local-server-plan.md section 0.
"""Local-profile pieces of `taskmaster serve`: refusals, store take-over, discovery, status/retire, idle exit.

Session identity lives in `sessions.py` (the registry's local mode); the profile switches that change what the daemon
installs (coordinator constructor, middlewares, hooks) are in `daemon.py`. Shared names (paths, headers, statuses) come
from `taskmaster.localserve`, which the bridge imports too.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import closing
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.routing import Route

from taskmaster import localserve
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.coordinator.protocol import build_identity

from .config import ServeRefused, StoreOwnedBusy

LOG = logging.getLogger("taskmaster.serve")
HAND_OVER_WAIT_SECONDS = 5.0         # a busy session coordinator is retried this long before status store_owned_busy
HAND_OVER_POLL_SECONDS = 0.25
SESSION_HEARTBEAT_SECONDS = 15.0     # under store.HEARTBEAT_INTERVAL_SECONDS (20) and the 60 s "live" window
DISCRIMINATOR_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
NONCE_RE = re.compile(r"[0-9a-f]{8,64}")
MAX_CWD_CHARS = 4096
NO_STORE = {"Cache-Control": "no-store"}
SESSION_NOT_FOUND = {"jsonrpc": "2.0", "id": "server-error", "error": {"code": -32600, "message": "Session not found"}}


# -- refusals (before the host lease is taken; nothing is written) ------------------------------------------------

def preflight(root: Path) -> None:
    """Refuse a root the local profile must not serve: a team-server clone (marker or latch) or a store that is not
    a ready native one. Read-only: it never writes a marker or latch (spec section 2, guard test)."""
    reason = localserve.server_mode_reason(root)
    if reason is not None:
        raise ServeRefused(f"local server refused: {reason}")
    if localserve.legacy_store(root):
        raise ServeRefused(f"local server refused: {root} has no ready native store (a legacy or missing store keeps "
                           "the stdio fallback)")


# -- taking the store over from a session coordinator ------------------------------------------------------------

def hand_over_session_coordinator(root: Path, *, wait: float = HAND_OVER_WAIT_SECONDS, clock=time.monotonic,
                                  sleep=time.sleep) -> None:
    """Called with the host lease held. Ask the session coordinator that holds owner.lock to leave, once it is idle.

    A same-build coordinator refuses `retire` ("nothing to retire"), so an idle one is asked to `shutdown` (what
    `coordinator stop` does); one with queued work, syncs or Linear jobs is retried for `wait` seconds and then
    reported as busy (StoreOwnedBusy: the bridge falls back for this session). Another build goes through the
    existing retire hand-over."""
    from taskmaster.coordinator.protocol import HandshakeError, ServiceUnavailable, same_build
    from . import upgrade
    deadline = clock() + wait
    while ownership_held(root):
        try:
            client = upgrade._DiscoveryClient(root)
        except FileNotFoundError:
            client = None                                  # owner.lock held, address not published yet
        except (HandshakeError, ServiceUnavailable, OSError) as exc:
            raise StoreOwnedBusy(f"cannot read the running owner's address: {exc}") from None
        if client is not None:
            if not same_build(client.record.get("build"), client.build):
                try:
                    upgrade.hand_over(root, max(1.0, deadline - clock()), lease_is_ours=True)
                except ServeRefused as exc:
                    raise StoreOwnedBusy(str(exc)) from None
                return
            identity = dict(client.identity, schema=client.record.get("schema"))
            try:
                # One step in the owner, under its guards and by its own busy predicate (running Git work and sync jobs
                # between rounds count); a busy owner answers "busy" and is retried until the deadline.
                client._send(client.record, "shutdown_if_idle", identity=identity)
            except ValueError as exc:                      # a serve coordinator refuses: the store belongs to another server
                raise StoreOwnedBusy(str(exc)) from None
            except (ConnectionError, TimeoutError, ServiceUnavailable, OSError, HandshakeError):
                pass                                       # it may just have exited; the lock check decides
        if not ownership_held(root):
            return
        if clock() >= deadline:
            raise StoreOwnedBusy(f"a session coordinator is busy and still owns {root}; the local server stays out "
                                 "of its way and this session uses its own stdio server")
        sleep(HAND_OVER_POLL_SECONDS)


# -- session identity helpers -------------------------------------------------------------------------------------

def load_session_key(root: Path) -> bytes:
    """The per-store key the 16-hex session id is derived under; created once, owner-only (plan section 0)."""
    path = localserve.session_key_path(root)
    record = localserve.read_private_json(path)
    key = record.get("key") if record else None
    if not (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)):
        key = secrets.token_hex(32)
        localserve.write_private_json(path, {"format": 1, "key": key})
    return bytes.fromhex(key)


def derive_session_id(key: bytes, handle: str, discriminator: str) -> str:
    return f"{handle}@{hmac.new(key, discriminator.encode('utf-8'), hashlib.sha256).hexdigest()[:16]}"


def _header(scope, name: bytes):
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def bridge_headers(scope) -> tuple[str, str, str]:
    """(discriminator, nonce, cwd) from a request's bridge headers; "" for each one absent or malformed."""
    discriminator = _header(scope, localserve.HEADER_SESSION.lower().encode())
    if discriminator is not None and not DISCRIMINATOR_RE.fullmatch(discriminator):
        LOG.warning("ignored a malformed %s header", localserve.HEADER_SESSION)
        discriminator = None
    nonce = _header(scope, localserve.HEADER_SESSION_NONCE.lower().encode()) or ""
    if nonce and not NONCE_RE.fullmatch(nonce):
        nonce = ""
    cwd = _header(scope, localserve.HEADER_CWD.lower().encode()) or ""
    if cwd:
        cwd = localserve.decode_cwd(cwd)
        if len(cwd) > MAX_CWD_CHARS or "\x00" in cwd or not os.path.isabs(cwd):
            cwd = ""
    return discriminator or "", nonce, cwd


def limit_key(scope, subject: str) -> str:
    """The rate-limit and in-flight bucket of a request in the local profile: the bridge's discriminator (every local
    request carries the operator token, so the token's id would put all sessions in one bucket)."""
    discriminator, _nonce, _cwd = bridge_headers(scope)
    return f"bridge:{discriminator}" if discriminator else subject


# -- the runtime: discovery, status, retire, drain, idle exit ----------------------------------------------------

class LocalRuntime:
    """State of one local server generation. The daemon owns one and asks it each loop tick whether to stop."""

    def __init__(self, *, root: Path, url_provider, plugin_dir, token_file, sessions, coordinator_provider,
                 idle_exit: float, clock=time.monotonic):
        self.root, self.sessions, self.idle_exit, self.clock = Path(root), sessions, float(idle_exit), clock
        self._url = url_provider
        self._plugin_dir, self._token_file = Path(plugin_dir), Path(token_file)
        self._coordinator = coordinator_provider
        self._beat_at = 0.0
        self.build = build_identity()
        self.nonce = secrets.token_hex(16)
        self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.state = localserve.STATE_SERVING
        self.inflight = 0
        self._lock = threading.Lock()
        self._active_at = clock()
        self.on_drain: list = []
        self.published = False

    # discovery
    def publish(self) -> None:
        localserve.write_discovery(self.root, state=self.state, url=self._url(), pid=os.getpid(),
                                   plugin_dir=self._plugin_dir, token_file=self._token_file, build=self.build,
                                   started=self.started, nonce=self.nonce)
        self.published = True

    def unpublish(self) -> None:
        """Remove local.json on a clean exit, but only our own generation's: a successor's stays."""
        if not self.published:
            return
        try:
            record = localserve.read_private_json(localserve.discovery_path(self.root))
        except (PermissionError, OSError):
            return
        if record is None or record.get("nonce") == self.nonce:
            localserve.remove_discovery(self.root)

    # traffic
    def enter(self) -> None:
        with self._lock:
            self.inflight += 1

    def leave(self) -> None:
        with self._lock:
            self.inflight -= 1
        self._active_at = self.clock()

    def mark_active(self) -> None:
        """A signed-in viewer request is activity: the server must not idle-exit under a person who is reading it."""
        self._active_at = self.clock()

    @property
    def draining(self) -> bool:
        return self.state == localserve.STATE_DRAINING

    def bridges(self) -> list:
        return self.sessions.live()

    def _coordinator_activity(self) -> float:
        coordinator = self._coordinator()
        return getattr(coordinator, "last_activity", 0.0) if coordinator is not None else 0.0

    def _coordinator_busy(self) -> bool:
        """Every busy item (commands, Linear jobs, syncs, a Git run, a sync job between rounds), not only the queue."""
        coordinator = self._coordinator()
        if coordinator is None:
            return False
        busy = getattr(coordinator, "busy_now", None)
        return bool(busy() if busy is not None else coordinator.pending)

    def idle_seconds(self) -> float:
        now = self.clock()
        if self.bridges() or self.inflight > 0 or self._coordinator_busy():
            return 0.0
        return max(0.0, now - max(self._active_at, self._coordinator_activity()))

    def status(self) -> dict:
        return {"format": localserve.FORMAT, "state": self.state, "build": dict(self.build),
                "plugin_dir": str(self._plugin_dir), "root": str(self.root), "bridges": len(self.bridges()),
                "inflight": self.inflight, "idle_seconds": round(self.idle_seconds(), 1)}

    def heartbeat_sessions(self) -> int:
        """One `sessions` row per live bridge session (not one per server pid), so store_status and the resurfacing
        hooks see real sessions; the row's cwd is the bridge's. Returns the rows written. Best effort: a busy store skips."""
        entries = self.bridges()
        if not entries:
            return 0
        from taskmaster import store
        from taskmaster.coordinator.protocol import connect
        try:
            with closing(connect(self.root, busy_seconds=0)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for entry in entries:
                        store.upsert_session_row(connection, session=entry.session, cwd=entry.cwd or None)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except Exception as exc:  # noqa: BLE001 - a heartbeat must never stop the server (a busy writer just skips one)
            LOG.debug("session heartbeat skipped: %s", type(exc).__name__)
            return 0
        return len(entries)

    # retire / drain
    def retire(self, *, only_if_no_other_bridges: bool, requester) -> tuple[int, dict]:
        with self._lock:
            if self.state == localserve.STATE_DRAINING:
                return 202, {"state": self.state}
            if only_if_no_other_bridges:
                others = [entry for entry in self.bridges() if not requester or entry.discriminator != requester]
                if others:
                    return 409, {"reason": f"{len(others)} other bridge(s) are connected"}
            self.state = localserve.STATE_DRAINING
        LOG.info("draining: retire requested (only_if_no_other_bridges=%s)", only_if_no_other_bridges)
        self.begin_drain()
        return 202, {"state": self.state}

    def begin_drain(self) -> None:
        """Publish the draining state, stop the claim keeper's shortening, and close every GET stream so the graceful
        shutdown does not wait on them (the HTTP loop's thread runs this: the SDK's streams are loop-bound)."""
        for callback in list(self.on_drain):
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - one callback must not stop the drain
                LOG.error("drain callback failed: %s", type(exc).__name__)
        try:
            self.publish()
        except Exception as exc:  # noqa: BLE001 - the state is also in /local/v1/status
            LOG.warning("could not publish the draining state: %s", type(exc).__name__)
        self.sessions.close_streams()

    def tick(self) -> str | None:
        """Why the daemon should stop now, or None. Called every loop turn from the daemon's main thread."""
        if not (self.root / ".taskmaster" / "local").is_dir():
            return "the store root disappeared"
        if self.clock() - self._beat_at >= SESSION_HEARTBEAT_SECONDS:
            self._beat_at = self.clock()
            self.heartbeat_sessions()
        if self.draining:
            return "retired" if self.inflight <= 0 and not self._coordinator_busy() else None
        if self.idle_exit > 0 and self.idle_seconds() >= self.idle_exit:
            return f"idle for {self.idle_exit:g} s with no bridge connected"
        if self.bridges() or self.inflight > 0 or self._coordinator_busy():
            self._active_at = self.clock()
        return None


# -- the ASGI layer: traffic accounting, drain refusal, session binding at initialize ----------------------------------

class LocalMiddleware:
    """Counts non-stream MCP requests in flight, refuses new MCP traffic while draining, and binds a transport's
    session at its `initialize` response (the first request of the transport; plan section 0, review I3)."""

    def __init__(self, app, *, runtime: LocalRuntime, mcp_path: str):
        self.app, self.runtime, self.mcp_path = app, runtime, mcp_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != self.mcp_path.rstrip("/"):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        transport_id = _header(scope, b"mcp-session-id")
        if self.runtime.draining:
            await self._refuse_draining(scope, receive, send, method, transport_id)
            return
        counted = method in ("POST", "DELETE")
        if counted:
            self.runtime.enter()
        try:
            await self.app(scope, receive, self._binding_send(scope, send, method, transport_id))
        finally:
            if counted:
                self.runtime.leave()

    async def _refuse_draining(self, scope, receive, send, method, transport_id) -> None:
        if transport_id:
            # Before dispatch, so the bridge may retry it on a new server (its 404 rule).
            response = JSONResponse(SESSION_NOT_FOUND, status_code=404, headers=NO_STORE)
        else:
            response = JSONResponse({"error": "this local server is draining; it exits once its calls finish"},
                                    status_code=503, headers={**NO_STORE, "Retry-After": "1"})
        await response(scope, receive, send)

    def _binding_send(self, scope, send, method, transport_id):
        if method != "POST" or transport_id:
            return send
        sessions = self.runtime.sessions

        async def wrapped(message):
            if message.get("type") == "http.response.start" and message.get("status") == 200:
                for name, value in message.get("headers") or []:
                    if name.lower() == b"mcp-session-id":
                        try:
                            sessions.bind_scope(value.decode("latin-1"), scope)
                        except Exception as exc:  # noqa: BLE001 - binding again on the first tool call still works
                            LOG.warning("could not bind the session at initialize: %s", type(exc).__name__)
                        break
            await send(message)
        return wrapped


class LocalControl:
    """GET /local/v1/status and POST /local/v1/retire (operator token; loopback is the listener's only address)."""

    def __init__(self, runtime: LocalRuntime):
        self.runtime = runtime

    def routes(self) -> list:
        return [Route(localserve.PATH_STATUS, self.status, methods=["GET"]),
                Route(localserve.PATH_RETIRE, self.retire, methods=["POST"])]

    @staticmethod
    def _operator(request) -> bool:
        from . import auth
        principal = auth.principal_from_access_token(getattr(request.scope.get("user"), "access_token", None))
        return principal is not None and principal.key_id == auth.OPERATOR_KEY_ID

    async def status(self, request):
        if not self._operator(request):
            return JSONResponse({"error": "operator token required"}, status_code=403, headers=NO_STORE)
        return JSONResponse(self.runtime.status(), headers=NO_STORE)

    async def retire(self, request):
        if not self._operator(request):
            return JSONResponse({"error": "operator token required"}, status_code=403, headers=NO_STORE)
        try:
            body = json.loads((await request.body()) or b"{}")
        except ValueError:
            body = None
        only = body.get("only_if_no_other_bridges", False) if isinstance(body, dict) else None
        who = body.get("requester") if isinstance(body, dict) else None
        if not isinstance(only, bool) or not (who is None or isinstance(who, str)):
            return JSONResponse({"error": "body must be {only_if_no_other_bridges: bool, requester: str|null}"},
                                status_code=400, headers=NO_STORE)
        status, payload = self.runtime.retire(only_if_no_other_bridges=only, requester=who)
        return JSONResponse(payload, status_code=status, headers=NO_STORE)
