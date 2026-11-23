# User intent: the outermost request guard: a request id on every response, oversized and slow bodies refused, early answers close the connection, refused peers throttled, and per-key limits after auth, all before a body is read.
"""ASGI guards (design D1, D3, D3a, D4, D6, D7). RequestGuard wraps the whole app; PrincipalLimits runs after auth."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time

from fastmcp.server.middleware import Middleware

from taskmaster import query_guard

from .app import REQUEST_ID_KEY, log_safe
from .config import HEAVY_PER_KEY, HEAVY_QUEUE_SECONDS, HEAVY_TOOLS, HEAVY_TOTAL, MAX_HEADER_BYTES
from .limits import InFlight, KeyedLimiter, peer_key, retry_after

LOG = logging.getLogger("taskmaster.serve")
ALWAYS_CHARGED = frozenset({401})
# design D6; 403 and 429 are never charged. 431 (a complete head over the header cap) is charged like 400.
CHARGED_IF_UNAUTHENTICATED = frozenset({400, 408, 413, 421, 431})


def request_id(scope) -> str | None:
    return scope.get(REQUEST_ID_KEY)


def _json_response(status: int, payload: dict, extra=()) -> tuple[dict, dict]:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
               (b"content-length", str(len(body)).encode()), *extra]
    return ({"type": "http.response.start", "status": status, "headers": headers},
            {"type": "http.response.body", "body": body, "more_body": False})


def _header_values(scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers") or [] if key.lower() == name]


class _Request:
    """Per-request state shared by the wrapped receive and send."""

    def __init__(self, scope, send, rid: str, body_complete: bool):
        self.scope = scope
        self.raw_send = send
        self.rid = rid
        self.body_complete = body_complete
        self.started = False             # http.response.start has gone out
        self.status: int | None = None
        self.guard_answered = False

    async def send(self, message) -> None:
        if self.guard_answered:
            return                       # the guard answered; the app's late sends are dropped
        await self._send(message)

    async def _send(self, message) -> None:
        if message["type"] == "http.response.start":
            headers = [(k, v) for k, v in message.get("headers") or [] if k.lower() != b"x-request-id"]
            headers.append((b"x-request-id", self.rid.encode()))
            if not self.body_complete:
                # design D3a: an answer before the body is complete must not leave a drain-wait open.
                headers = [(k, v) for k, v in headers if k.lower() != b"connection"]
                headers.append((b"connection", b"close"))
            message = {**message, "headers": headers}
            self.started = True
            self.status = message["status"]
        await self.raw_send(message)

    async def answer(self, status: int, payload: dict, extra=()) -> None:
        start, body = _json_response(status, payload, extra)
        await self._send(start)
        await self._send(body)
        self.guard_answered = True


class RequestGuard:
    def __init__(self, app, *, max_body_bytes: int, body_timeout: float, failures: KeyedLimiter, authenticator):
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        self.body_timeout = float(body_timeout)
        self.failures = failures
        self.authenticator = authenticator

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":
            LOG.warning("refused %s scope %s", log_safe(kind), log_safe(scope.get("path")))
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        rid = secrets.token_hex(6)
        scope[REQUEST_ID_KEY] = rid
        client = scope.get("client")
        peer = peer_key(client[0] if client else "-")
        lengths = _header_values(scope, b"content-length")
        chunked = bool(_header_values(scope, b"transfer-encoding"))
        body_complete = not chunked and (not lengths or lengths == [b"0"])
        request = _Request(scope, send, rid, body_complete)
        try:
            await self._handle(scope, receive, request, peer, lengths, chunked)
        finally:
            self._charge(scope, request, peer)

    async def _handle(self, scope, receive, request: _Request, peer: str, lengths, chunked: bool) -> None:
        wait = self.failures.blocked(peer)
        if wait > 0:
            # Silent while the budget stays empty: the one "exhausted" warning was logged (design D6).
            await request.answer(429, {"error": "too many refused requests"},
                                 [(b"retry-after", str(retry_after(wait)).encode())])
            return
        head = (len(scope.get("raw_path") or b"") + len(scope.get("query_string") or b"")
                + sum(len(k) + len(v) + 4 for k, v in scope.get("headers") or []))
        if head > MAX_HEADER_BYTES:
            await self._refuse(request, 431, {"error": "request headers too large"}, (), peer)
            return
        if lengths:
            if len(set(lengths)) != 1 or not lengths[0].isdigit() or chunked:
                await self._refuse(request, 400, {"error": "bad request"}, (), peer)
                return
            if int(lengths[0]) > self.max_body_bytes:
                await self._refuse(request, 413, {"error": "request body too large", "limit": self.max_body_bytes},
                                   (), peer)
                return
        received = 0
        deadline = None
        disconnect = {"type": "http.disconnect"}

        async def wrapped_receive():
            nonlocal received, deadline
            if request.guard_answered:
                return disconnect
            if request.body_complete:
                return await receive()   # only http.disconnect is left; an SSE wait is never timed
            now = time.monotonic()
            if deadline is None:
                deadline = now + self.body_timeout
            try:
                message = await asyncio.wait_for(receive(), max(0.0, deadline - now))
            except asyncio.TimeoutError:
                if not request.started:
                    await self._refuse(request, 408, {"error": "request body timed out"}, (), peer)
                request.guard_answered = True
                return disconnect
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    if not request.started:
                        await self._refuse(request, 413, {"error": "request body too large",
                                                          "limit": self.max_body_bytes}, (), peer)
                    request.guard_answered = True
                    return disconnect
                if not message.get("more_body", False):
                    request.body_complete = True
            return message

        try:
            await self.app(scope, wrapped_receive, request.send)
        except Exception:
            if not request.guard_answered:
                raise
            # The app failed on the disconnect the guard handed it after answering; the client has its answer.
            LOG.debug("app raised after the guard answered ref=%s", request.rid, exc_info=True)

    async def _refuse(self, request: _Request, status: int, payload: dict, extra, peer: str) -> None:
        scope = request.scope
        LOG.info("refused %s %s %s from %s ref=%s", status, log_safe(scope.get("method")), log_safe(scope.get("path")),
                 log_safe(peer), request.rid)
        await request.answer(status, payload, extra)

    def _charge(self, scope, request: _Request, peer: str) -> None:
        status = request.status
        if status in ALWAYS_CHARGED or (status in CHARGED_IF_UNAUTHENTICATED and not self._authenticated(scope)):
            self.failures.take(peer)
            wait = self.failures.blocked(peer)
            if wait > 0:
                LOG.warning("peer %s exhausted its failure budget; refusing for %ss", log_safe(peer),
                            retry_after(wait))

    def _authenticated(self, scope) -> bool:
        values = _header_values(scope, b"authorization")
        if len(values) != 1:
            return False
        scheme, _, token = values[0].decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return False
        try:
            return self.authenticator.authenticate(token.strip()) is not None
        except Exception:  # noqa: BLE001 - an authenticator failure means "not authenticated" here
            return False


class PrincipalLimits:
    """Per-key in-flight cap, then token bucket (design D7). Runs after RequirePrincipalMiddleware."""

    def __init__(self, app, *, limiter: KeyedLimiter, inflight: InFlight, keyer=None):
        self.app = app
        self.limiter = limiter
        self.inflight = inflight
        self.keyer = keyer      # local profile: (scope, key) -> the bucket key (a bridge's discriminator, not "operator")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from .auth import principal_from_access_token
        user = scope.get("user")
        token = getattr(user, "access_token", None)
        key = getattr(token, "subject", None)
        rid = scope.get(REQUEST_ID_KEY, "-")
        if not isinstance(key, str) or not key:
            LOG.error("refused a request with no principal key after auth ref=%s", rid)
            await _send_json(send, 500, {"error": "internal error"})
            return
        principal = principal_from_access_token(token)
        handle = log_safe(principal.handle if principal else "-")
        if self.keyer is not None:
            key = self.keyer(scope, key)
        if not self.inflight.acquire(key):
            LOG.info("too many concurrent requests key=%s principal=%s ref=%s", log_safe(key), handle, rid)
            await _send_json(send, 429, {"error": "too many concurrent requests"}, [(b"retry-after", b"1")])
            return
        try:
            wait = self.limiter.take(key)
            if wait > 0:
                LOG.info("rate limited key=%s principal=%s ref=%s", log_safe(key), handle, rid)
                await _send_json(send, 429, {"error": "rate limited"},
                                 [(b"retry-after", str(retry_after(wait)).encode())])
                return
            await self.app(scope, receive, send)
        finally:
            self.inflight.release(key)


async def _send_json(send, status: int, payload: dict, extra=()) -> None:
    start, body = _json_response(status, payload, extra)
    await send(start)
    await send(body)


class MemberQueryScope(Middleware):
    """Served MCP tool calls: a member's SQL reads never see the host's private tables (team-server-026, review I1).
    The operator, and a call outside any HTTP request (stdio, in-memory), stay unrestricted. Inside an HTTP request a
    missing or unreadable principal is treated as a member: fail closed. Same table set as the read endpoint."""

    async def on_call_tool(self, context, call_next):
        from fastmcp.server.dependencies import get_http_request
        from .auth import current_principal
        try:
            get_http_request()
        except RuntimeError:  # no request context (stdio, in-memory): solo behaviour
            return await call_next(context)
        try:
            principal = current_principal()
        except Exception:  # noqa: BLE001 - unreadable principal inside a request: restrict
            principal = None
        if principal is not None and principal.role == "operator":
            return await call_next(context)
        with query_guard.restricted(query_guard.READ_ENDPOINT_TABLES):
            return await call_next(context)


HEAVY_PER_KEY_TEXT = ("Error: rate limited: at most {n} {tool} calls may run at once per key, and this key has {n} "
                      "running. Wait for one to answer, then retry.")
HEAVY_BUSY_TEXT = ("Error: rate limited: the server is running its limit of {n} heavy calls for the team and none "
                   "finished within {s:g} s. Retry in a few seconds.")


class HeavyCalls(Middleware):
    """Served heavy tool calls (security review I-1): per key at most `per_key` in flight (more are refused at once),
    on the whole server at most `total` (more wait up to `queue_seconds`, then are refused). Counts MCP calls and the
    read endpoint's batched calls alike, since both run through this middleware. The operator and calls outside an
    HTTP request (stdio, in-memory) are not limited; inside a request a missing principal shares one bucket."""

    def __init__(self, *, tools=None, per_key: int | None = None, total: int | None = None,
                 queue_seconds: float | None = None, poll: float = 0.05, clock=time.monotonic):
        from .limits import InFlight
        self.tools = frozenset(HEAVY_TOOLS if tools is None else tools)
        self.per_key = InFlight(HEAVY_PER_KEY if per_key is None else per_key)
        self.total = int(HEAVY_TOTAL if total is None else total)
        self.queue_seconds = float(HEAVY_QUEUE_SECONDS if queue_seconds is None else queue_seconds)
        self._poll = poll
        self._clock = clock
        self._lock = threading.Lock()
        self.running = 0

    def _take(self) -> bool:
        with self._lock:
            if self.running >= self.total:
                return False
            self.running += 1
            return True

    def _give(self) -> None:
        with self._lock:
            self.running -= 1

    async def on_call_tool(self, context, call_next):
        name = getattr(context.message, "name", None)
        if name not in self.tools:
            return await call_next(context)
        from fastmcp.exceptions import ToolError
        from fastmcp.server.dependencies import get_http_request
        from .auth import current_principal
        try:
            get_http_request()
        except RuntimeError:  # no request context (stdio, in-memory): solo behaviour
            return await call_next(context)
        try:
            principal = current_principal()
        except Exception:  # noqa: BLE001 - unreadable principal inside a request: limited, in one shared bucket
            principal = None
        if principal is not None and principal.role == "operator":
            return await call_next(context)
        key = principal.key_id if principal is not None else "-"
        if not self.per_key.acquire(key):
            LOG.info("heavy call refused: key=%s has %s running tool=%s", log_safe(key), self.per_key.max_per_key,
                     log_safe(name))
            raise ToolError(HEAVY_PER_KEY_TEXT.format(n=self.per_key.max_per_key, tool=name))
        try:
            until = self._clock() + self.queue_seconds
            while not self._take():
                if self._clock() >= until:
                    LOG.info("heavy call refused: %s running on the server key=%s tool=%s", self.total,
                             log_safe(key), log_safe(name))
                    raise ToolError(HEAVY_BUSY_TEXT.format(n=self.total, s=self.queue_seconds))
                await asyncio.sleep(self._poll)
            try:
                return await call_next(context)
            finally:
                self._give()
        finally:
            self.per_key.release(key)
