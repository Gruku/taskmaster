# User intent: build the HTTP app around the existing MCP tools so that every route refuses an unauthenticated request before reading its body.
"""The ASGI app: FastMCP's streamable HTTP transport behind deny-by-default bearer auth."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastmcp.server.http import StreamableHTTPASGIApp, create_streamable_http_app
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from . import auth
from .auth import LOG, OPERATOR_KEY_ID, Authenticator, ServeTokenVerifier
from .config import DEFAULT_PATH, DEFAULT_SESSION_IDLE_SECONDS
from .principals import RegistryUnavailable

REVOCATION_SWEEP_SECONDS = 2.0
REQUEST_ID_KEY = "taskmaster.request_id"   # set by guard.RequestGuard (team-server-006); "-" without it
OPERATOR_REMOTE_REFUSED = ("the operator bootstrap token works only on the host; "
                           "use a key from taskmaster serve add-user")

INTERNALS_CHANGED = ("fastmcp's streamable-HTTP app no longer has the shape taskmaster serve relies on "
                     "(MCP route -> RequireAuthMiddleware.app -> StreamableHTTPASGIApp.session_manager); "
                     "the session idle timeout cannot be set, and sessions must record their owner and idle scope (mcp>=1.28)")


def log_safe(value, limit: int = 256) -> str:
    """Render untrusted request text for a log line: control characters escaped, length capped,
    so a decoded path such as '/x
<fake record>' cannot forge log entries."""
    text = "" if value is None else str(value)
    text = text.encode("unicode_escape", "backslashreplace").decode("ascii")
    return text if len(text) <= limit else text[:limit] + "..."


class RequirePrincipalMiddleware:
    """Refuses every http request that bearer auth did not authenticate, whatever its path."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and isinstance(scope.get("user"), AuthenticatedUser):
            token = scope["user"].access_token
            if getattr(token, "subject", None) == OPERATOR_KEY_ID and not auth.is_host_local(scope):
                # scope["client"] came from the caller-controlled X-Forwarded-For (uvicorn proxy headers).
                client = scope.get("client")
                LOG.warning("refused operator token from %s: not host-local ref=%s", log_safe(client[0] if client else "-"),
                            scope.get(REQUEST_ID_KEY, "-"))
                response = JSONResponse({"error": OPERATOR_REMOTE_REFUSED}, status_code=403,
                                        headers={"Cache-Control": "no-store"})
                await response(scope, _no_receive, send)
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No websocket route exists; refuse every non-http, non-lifespan scope before any app sees it.
            LOG.warning("refused %s scope %s", scope["type"], log_safe(scope.get("path")))
            await send({"type": "websocket.close", "code": 1008})
            return
        present = any(name.lower() == b"authorization" for name, _ in scope.get("headers", []))
        client = scope.get("client")
        LOG.warning("refused unauthenticated %s %s from %s (%s token) ref=%s", log_safe(scope.get("method")),
                    log_safe(scope.get("path")), log_safe(client[0] if client else "-"),
                    "invalid" if present else "missing", scope.get(REQUEST_ID_KEY, "-"))
        response = JSONResponse({"error": "authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="taskmaster"', "Cache-Control": "no-store"})
        await response(scope, _no_receive, send)


async def _no_receive():
    raise AssertionError("an unauthenticated request's body must not be read")


def streamable_asgi(app, path) -> StreamableHTTPASGIApp:
    routes = [r for r in app.routes if getattr(r, "path", None) == path]
    if len(routes) != 1:
        raise RuntimeError(INTERNALS_CHANGED)
    asgi = getattr(routes[0].endpoint, "app", None)
    if not isinstance(asgi, StreamableHTTPASGIApp):
        raise RuntimeError(INTERNALS_CHANGED)
    return asgi


def check_session_internals(manager) -> None:
    """The revocation sweep reads the SDK's session tables; refuse to start if they moved (design R1)."""
    ok = (isinstance(getattr(manager, "_session_owners", None), dict)
          and isinstance(getattr(manager, "_server_instances", None), dict)
          and "idle_scope" in StreamableHTTPServerTransport.__init__.__code__.co_names)
    if not ok:
        raise RuntimeError(INTERNALS_CHANGED)


def end_revoked_sessions(manager, registry) -> list[str]:
    """Cut the idle scope of every open session whose key is no longer active. RegistryUnavailable propagates."""
    active = registry.active_ids()
    ended = []
    for sid, owner in list(manager._session_owners.items()):
        subject = owner.get("subject") if isinstance(owner, dict) else getattr(owner, "subject", None)
        if subject is None or subject == OPERATOR_KEY_ID or subject in active:
            continue
        transport = manager._server_instances.get(sid)
        if transport is not None and transport.idle_scope is not None:
            # anyio's clock on the asyncio backend (uvicorn's) is the loop clock; anyio is not a direct dependency
            transport.idle_scope.deadline = asyncio.get_running_loop().time()
            ended.append(sid)
            LOG.info("ended session %s of revoked key %s", log_safe(sid[:8]), log_safe(subject))
    return ended


async def _sweep(manager, registry, seconds, sessions=None) -> None:
    while True:
        await asyncio.sleep(seconds)
        if sessions is not None:
            try:
                sessions.prune()          # forget closed connections; 010's on_disconnect runs here
            except Exception as exc:  # noqa: BLE001 - the sweep must outlive any one failure
                LOG.error("session prune failed: %s", log_safe(type(exc).__name__))
        if registry is None:
            continue
        try:
            end_revoked_sessions(manager, registry)
        except RegistryUnavailable:
            continue                      # a transient read error must never end sessions (design D6)
        except Exception as exc:  # noqa: BLE001 - the sweep must outlive any one failure
            LOG.error("revocation sweep failed: %s", log_safe(type(exc).__name__))


def build_app(server, authenticator: Authenticator, *, path=DEFAULT_PATH, allowed_hosts=(), middleware=(),
              session_idle_seconds=DEFAULT_SESSION_IDLE_SECONDS, registry=None,
              sweep_seconds=REVOCATION_SWEEP_SECONDS, sessions=None, routes=()):
    if session_idle_seconds is not None and not session_idle_seconds > 0:
        raise ValueError("session_idle_seconds must be positive (or None: no idle timeout, the local profile)")
    app = create_streamable_http_app(
        server=server, streamable_http_path=path, auth=ServeTokenVerifier(authenticator),
        json_response=False, stateless_http=False, debug=False, routes=list(routes) or None,
        middleware=[Middleware(RequirePrincipalMiddleware), *middleware],
        host_origin_protection=True, allowed_hosts=list(allowed_hosts), allowed_origins=[])
    asgi = streamable_asgi(app, path)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            if asgi.session_manager is None:
                raise RuntimeError(INTERNALS_CHANGED)
            manager = asgi.session_manager
            check_session_internals(manager)
            manager.session_idle_timeout = None if session_idle_seconds is None else float(session_idle_seconds)  # design D10
            if sessions is not None:
                sessions.attach(manager)            # team-server-003: `connected` reads the manager's table
            sweeper = (asyncio.get_running_loop().create_task(_sweep(manager, registry, sweep_seconds, sessions))
                       if registry is not None or sessions is not None else None)
            try:
                yield state
            finally:
                if sweeper is not None:
                    sweeper.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sweeper
                if sessions is not None:
                    sessions.detach()

    app.router.lifespan_context = lifespan
    return app
