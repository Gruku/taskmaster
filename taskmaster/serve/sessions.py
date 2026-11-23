# User intent: one Taskmaster session per teammate's MCP connection on the shared server, minted by the server and derived from the authenticated request on every call, so no teammate's claims, current task or agent leak into another's.
"""Per-connection sessions on the shared server (team-server-003 D2, D3, D5, D9).

A session is `<handle>@<16 hex>`, minted on the first tool call of an MCP connection
(one streamable-HTTP transport) and kept for that connection's life. The daemon installs
`SessionRegistry.current` as `backlog_server.SESSION_PROVIDER` and `SessionRegistry.connected`
as `native.claims.CONNECTED`; nothing else assigns either.

Scope rules: `check_scope` (execute envelopes, 003 D5) and `check_named_scope` (every other caller-named scope on a
remote origin, team-server-004 D2) are the one place a request's `caller_scope` is judged against the key. The
vocabulary of internal scopes lives in `taskmaster.coordinator.scopes`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import secrets
import threading
import time

from mcp.server.streamable_http import GET_STREAM_KEY

from taskmaster.native import claims
from taskmaster.native.claims import Requester
from taskmaster.coordinator import scopes
from taskmaster.team_identity import IdentityRefused

from . import auth
from .principals import PrincipalRegistry, RegistryUnavailable

LOG = logging.getLogger("taskmaster.serve")
SESSION_REFUSED = "Refused: this request names a session that does not belong to your key. Nothing was written."
CLAIM_OPERATIONS = scopes.CLAIM_OPERATIONS
TRANSPORT_ID_RE = re.compile(r"[\x21-\x7e]{1,256}")   # visible ASCII, as mcp mints and checks it
VIEWER = "viewer"
READ = "read"
READ_CHANNEL_KEY = "taskmaster.read_channel"   # set in the ASGI scope by serve.read_api for its tool calls
HOOK_CHANNEL_KEY = "taskmaster.hook_channel"   # set by serve.write_api for the tool call behind a hook's write (008)
HOOK_CLONE_KEY = "taskmaster.hook_clone"       # the validated clone id of that write (or ""), read by current()


def _log_safe(value) -> str:
    from .app import log_safe   # app imports this module's users; import late to avoid a cycle
    return log_safe(value)


@dataclass
class RemoteSession:
    """One MCP connection's session and its per-session state (D4)."""
    session: str
    handle: str
    key_id: str
    role: str
    transport_id: str          # the mcp-session-id; never exported, logged only as its first 8 characters
    agent: str
    started: str
    task: dict | None = None
    bundle: dict | None = None
    clone: str = ""            # the connection's X-Taskmaster-Clone, read once at bind (team-server-011 D2)
    announced: bool = False    # the adoption notice (team-server-010 D7) has been shown on this connection
    # Local profile (local-server-002): the bridge that owns this session. The discriminator is the Claude Code session
    # id (or a bridge-lifetime random id), the nonce tells two processes of one discriminator apart.
    discriminator: str = ""
    nonce: str = ""
    cwd: str = ""
    opened: float = 0.0        # monotonic time of the last bind (a new transport has a moment to open its GET stream)

    def requester(self) -> Requester:
        return Requester(self.handle, self.role == "operator")


@dataclass
class ChannelSession:
    """A person's viewer: stateless, rebuilt per request, never connected, never holding a claim (D9)."""
    session: str
    handle: str
    role: str
    agent: str = ""
    task: dict | None = field(default=None)
    bundle: dict | None = field(default=None)
    clone: str = ""            # the hook channel's clone id (team-server-008 S1); "" for every other channel

    def __setattr__(self, name, value):
        if name in ("task", "bundle") and getattr(self, "_ready", False):
            return   # a viewer has no current task or bundle
        object.__setattr__(self, name, value)

    def __post_init__(self):
        object.__setattr__(self, "_ready", True)

    def requester(self) -> Requester:
        return Requester(self.handle, self.role == "operator")


CLONE_HEADER = "x-taskmaster-clone"


def _header_clone(request) -> str:
    """The connection's `X-Taskmaster-Clone`, or "" when absent or malformed (team-server-011 D2). The first request of a
    transport wins: `bind` reads this once, when it creates the session."""
    from taskmaster.native.clone_fields import CLONE_RE
    value = request.headers.get(CLONE_HEADER)
    if value is None:
        return ""
    if CLONE_RE.fullmatch(value):
        return value
    LOG.warning("ignored a malformed X-Taskmaster-Clone header (%s)", _log_safe(value[:40]))
    return ""


LOCAL_STREAM_GRACE_SECONDS = 10.0   # a new transport's window to open its GET stream before it reads as gone
LOCAL_RETAINED_MAX = 512      # bridge processes whose per-session state is kept across a transport re-initialize


class SessionRegistry:
    def __init__(self, principals: PrincipalRegistry | None = None, *, local_key: bytes | None = None,
                 clock=time.monotonic):
        self.principals = principals if principals is not None else PrincipalRegistry(None)
        self._lock = threading.Lock()
        self._by_transport: dict[str, RemoteSession] = {}
        self._by_session: dict[str, RemoteSession] = {}
        # Local profile: sessions are derived from the bridge's discriminator, and a bridge's liveness is its open
        # GET stream (a hard-killed bridge sends no DELETE; with the idle timeout off its transport would live forever).
        self.local_key = local_key
        self._clock = clock
        self._procs: dict[tuple[str, str], RemoteSession] = {}      # (discriminator, nonce) -> its session
        self._primary: dict[str, RemoteSession] = {}                 # discriminator -> the session with the derived id
        self._manager = None
        self.on_disconnect: list = []   # 010 seam; runs on the event loop: never block

    # ── wiring ──
    def attach(self, manager) -> None:
        self._manager = manager

    def detach(self) -> None:
        self._manager = None

    def attached(self) -> bool:
        """Whether the transport manager is attached: only then does `connected` mean anything (team-server-010)."""
        return self._manager is not None

    @property
    def local(self) -> bool:
        return self.local_key is not None

    # ── binding ──
    def bind_scope(self, transport_id: str, scope) -> "RemoteSession | None":
        """Bind a transport at its first request from the ASGI scope's bridge headers (local profile). A request
        without a discriminator (not a bridge) is left for the first tool call, which mints a transport-bound session."""
        from . import local as local_profile
        user = scope.get("user")
        principal = auth.principal_from_access_token(getattr(user, "access_token", None))
        if principal is None or not self.local:
            return None
        discriminator, nonce, cwd = local_profile.bridge_headers(scope)
        if not discriminator:
            return None
        return self.bind_local(transport_id, principal, discriminator, nonce, cwd)

    def bind_local(self, transport_id: str, principal, discriminator: str, nonce: str = "", cwd: str = "",
                   agent: str = "") -> RemoteSession:
        """The session of a bridge's transport. The id is `<handle>@<16 hex>` derived from the discriminator under the
        per-store key, so a restarted server or a re-initialized transport gives the same holder. A discriminator
        that already has a live bridge with another nonce (two processes, one id) gets a fresh random session instead
        of a merge. The per-session state (task, bundle) follows the (discriminator, nonce) across transports."""
        from . import local as local_profile
        with self._lock:
            entry = self._by_transport.get(transport_id)
            if entry is not None:
                if entry.key_id != principal.key_id:
                    raise PermissionError("session belongs to another key")
                return entry
            entry = self._procs.get((discriminator, nonce))
            if entry is not None and entry.key_id != principal.key_id:
                raise PermissionError("session belongs to another key")
            if entry is None:
                primary = self._primary.get(discriminator)
                if primary is not None and self._alive(primary):
                    session = f"{principal.handle}@{secrets.token_hex(8)}"        # a second live process: never a merge
                    while session in self._by_session:
                        session = f"{principal.handle}@{secrets.token_hex(8)}"
                else:
                    session = local_profile.derive_session_id(self.local_key, principal.handle, discriminator)
                assert claims.REMOTE_SESSION_RE.fullmatch(session), session
                entry = RemoteSession(session, principal.handle, principal.key_id, principal.role, transport_id, agent,
                                      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      discriminator=discriminator, nonce=nonce, cwd=cwd)
                if primary is None or session == local_profile.derive_session_id(self.local_key, principal.handle,
                                                                                 discriminator):
                    self._primary[discriminator] = entry
                self._procs[(discriminator, nonce)] = entry
                for old in list(self._procs):
                    if len(self._procs) <= LOCAL_RETAINED_MAX:
                        break
                    if not self._alive(self._procs[old]):
                        self._procs.pop(old)
            else:
                self._by_transport.pop(entry.transport_id, None)       # a re-initialized transport: same session
                entry.transport_id = transport_id
                entry.announced = False
            entry.cwd = cwd or entry.cwd
            entry.opened = self._clock()
            self._by_transport[transport_id] = entry
            self._by_session[entry.session] = entry
        LOG.info("session %s opened for %s (bridge %s, transport %s)", _log_safe(entry.session),
                 _log_safe(principal.handle), _log_safe(discriminator[:12]), _log_safe(transport_id[:8]))
        return entry

    def close_streams(self) -> None:
        """Close every transport's GET stream (drain). Must run on the HTTP event loop."""
        manager = self._manager
        if manager is None:
            return
        for transport in list(manager._server_instances.values()):
            try:
                transport.close_standalone_sse_stream()
            except Exception as exc:  # noqa: BLE001 - a transport mid-close is already closing
                LOG.debug("could not close a GET stream: %s", type(exc).__name__)

    def bind(self, transport_id: str, principal, agent: str, clone: str = "") -> RemoteSession:
        with self._lock:
            entry = self._by_transport.get(transport_id)
            if entry is not None:
                if entry.key_id != principal.key_id:
                    raise PermissionError("session belongs to another key")
                return entry
            while True:
                session = f"{principal.handle}@{secrets.token_hex(8)}"
                if session not in self._by_session:
                    break
            assert claims.REMOTE_SESSION_RE.fullmatch(session), session
            entry = RemoteSession(session, principal.handle, principal.key_id, principal.role, transport_id,
                                  agent, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), clone=clone)
            self._by_transport[transport_id] = entry
            self._by_session[session] = entry
        LOG.info("session %s opened for %s (key %s, transport %s)", _log_safe(session), _log_safe(principal.handle),
                 _log_safe(principal.key_id), _log_safe(transport_id[:8]))
        return entry

    def by_transport(self, transport_id: str):
        """The RemoteSession of an MCP transport id, or None. Never binds (unlike `current`)."""
        return self._by_transport.get(transport_id)

    def mark_announced(self, entry: RemoteSession) -> bool:
        """True exactly once per connection: the caller that gets True shows the adoption notice."""
        with self._lock:
            if entry.announced:
                return False
            entry.announced = True
            return True

    def current(self):
        """The session this request runs for (installed as backlog_server.SESSION_PROVIDER), or None
        outside any served request. Inside a request without a principal or session id: PermissionError."""
        from fastmcp.server.dependencies import get_http_request
        try:
            request = get_http_request()
        except RuntimeError:
            request = None
        if request is not None:
            auth.current_key_id()   # first: carries 005 D8's operator => host-local recheck once it lands
            principal = auth.current_principal()
            if principal is None:
                raise PermissionError("authenticated principal missing")
            if (getattr(request, "scope", None) or {}).get(READ_CHANNEL_KEY):
                # The read endpoint (team-server-007) runs read tools outside any MCP connection: a stateless
                # channel like the viewer, never connected, never holding a claim.
                return ChannelSession(f"{principal.handle}@{READ}", principal.handle, principal.role)
            scope = getattr(request, "scope", None) or {}
            if scope.get(HOOK_CHANNEL_KEY):
                # A teammate's hook write (team-server-008): a channel like the read one, carrying the hook's clone.
                return ChannelSession(f"{principal.handle}@hook", principal.handle, principal.role,
                                      clone=str(scope.get(HOOK_CLONE_KEY) or ""))
            transport_id = request.headers.get("mcp-session-id")
            if not transport_id or not TRANSPORT_ID_RE.fullmatch(transport_id):
                raise PermissionError("MCP session id missing")
            if self.local:
                entry = self.by_transport(transport_id) or self.bind_scope(transport_id, request.scope)
                if entry is not None:
                    entry.agent = entry.agent or _client_agent()
                    return entry
            return self.bind(transport_id, principal, _client_agent(), clone=_header_clone(request))
        key_id = auth.VIEWER_KEY_ID.get()
        if key_id is None:
            return None
        if key_id == auth.OPERATOR_KEY_ID:
            return ChannelSession(f"operator@{VIEWER}", "operator", "operator")
        try:
            record = self.principals.lookup(key_id)
        except RegistryUnavailable:
            raise PermissionError("the key list could not be read") from None
        if record is None or not record.active:
            raise PermissionError("viewer key is not active")
        return ChannelSession(f"{record.handle}@{VIEWER}", record.handle, record.role)

    # ── connection state ──
    def _alive(self, entry: RemoteSession) -> bool:
        manager = self._manager
        if manager is None:
            return False
        transport = manager._server_instances.get(entry.transport_id)
        if transport is None or getattr(transport, "is_terminated", False):
            return False
        if self.local:
            # A bridge is alive while its GET stream is open; a fresh transport gets a few seconds to open one.
            return GET_STREAM_KEY in transport._request_streams or self._clock() - entry.opened < LOCAL_STREAM_GRACE_SECONDS
        return True

    def connected(self, session: str) -> bool:
        """Installed as native.claims.CONNECTED. Read from any thread: one atomic dict lookup each."""
        entry = self._by_session.get(session)
        return entry is not None and self._alive(entry)

    def live(self) -> list[RemoteSession]:
        with self._lock:
            entries = list(self._by_session.values())
        return [entry for entry in entries if self._alive(entry)]

    def prune(self) -> list[RemoteSession]:
        """Forget sessions whose connection is gone; run each on_disconnect callback per session."""
        with self._lock:
            gone = [entry for entry in self._by_session.values() if not self._alive(entry)]
            for entry in gone:
                self._by_session.pop(entry.session, None)
                self._by_transport.pop(entry.transport_id, None)
        for entry in gone:
            LOG.info("session %s closed", _log_safe(entry.session))
            for callback in list(self.on_disconnect):
                try:
                    callback(entry)
                except Exception as exc:  # noqa: BLE001 - one callback must not stop the others or the sweep
                    LOG.error("on_disconnect callback failed: %s", _log_safe(type(exc).__name__))
        return gone


def _client_agent() -> str:
    from mcp.server.lowlevel.server import request_ctx
    from taskmaster.native import actor
    try:
        name = request_ctx.get().session.client_params.clientInfo.name
    except (LookupError, AttributeError):
        name = ""
    return actor.agent_for_client(name or "")


# ── The coordinator's checks (D5) ──


def requester(origin, principals) -> Requester | None:
    from .identity import REVOKED, UNAVAILABLE
    key_id = getattr(origin, "principal", None)
    if key_id is None:
        return None
    if key_id == auth.OPERATOR_KEY_ID:
        return Requester("operator", True)
    try:
        record = principals.lookup(key_id)
    except RegistryUnavailable:
        raise IdentityRefused(UNAVAILABLE) from None
    if record is None or not record.active:
        raise IdentityRefused(REVOKED)
    return Requester(record.handle, record.role == "operator")


claim_operations = scopes.claim_operations


def check_named_scope(scope, who: Requester) -> None:
    """A caller-named scope on a non-execute method must carry the caller's handle (team-server-004 D2).
    Same handle, any session or channel (003 `check_scope` is equally lenient); another handle, a host-shaped
    scope and every internal scope are refused."""
    if not isinstance(scope, str) or claims.remote_handle(scope) != who.handle:
        raise IdentityRefused(SESSION_REFUSED)


def check_scope(request, who: Requester) -> None:
    scope = request.get("caller_scope")
    if claims.remote_handle(scope) != who.handle:
        raise IdentityRefused(SESSION_REFUSED)
    if any(arguments.get("session") != scope for _operation, arguments in claim_operations(request)):
        raise IdentityRefused(SESSION_REFUSED)
