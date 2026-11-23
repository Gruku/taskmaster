# User intent: run the HTTP app and the project's coordinator in one long-lived process that never idle-exits, never yields its coordinator to another build, and stops gracefully on SIGTERM.
"""The `taskmaster serve` daemon: HTTP app plus the project's coordinator in one process."""
from __future__ import annotations

import contextlib
import hmac
import importlib
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from starlette.middleware import Middleware

from taskmaster import store_integrity
from taskmaster.admission import UnsupportedStoreError
from taskmaster.coordinator import allowlist, client, identity_gate, protocol, scopes
from taskmaster.coordinator.ownership import OwnershipUnavailable, ownership_held
from taskmaster.coordinator.actor_resolver import LOCAL, Origin
from taskmaster.coordinator.protocol import HandshakeError, check_handshake, describe
from taskmaster.coordinator.service import Coordinator
from taskmaster.native import claims, contracts, query_snapshots

from .app import build_app, log_safe
from .claim_keeper import AdoptionNotice, ClaimKeeper
from .auth import (KeyAuthenticator, OperatorTokenAuthenticator, ensure_operator_token, read_operator_token,
                   request_key_id, serve_directory, token_path)
from . import sessions
from .identity import ServeActorSource, ServeIdentityGate
from .sessions import SessionRegistry
from .principals import PrincipalRegistry, PrincipalsRefused, load, principals_path
from .principals import pre_team as pre_team_of
from .guard import HeavyCalls, MemberQueryScope, PrincipalLimits, RequestGuard
from .limits import failure_budget, principal_inflight, principal_limiter
from .protocol import HARDENED_KEYS, hardened_uvicorn_options
from .read_api import ReadApi
from taskmaster import query_guard, untrusted
from .framing import FrameUntrusted, drop_non_string_schemas_sync

from .write_api import WriteApi
from .sanitize import SanitizeErrors, coordinator_public_error
from .tracked import TrackedFiles
from .viewer import ViewerGuard, start_viewer, stop_viewer
from .viewer_auth import LINK_PATH, CookieLogin, LocalLinkLogin
from .local_viewer import start_band_viewer
from . import local as local_profile
from .config import (DEFAULT_EXPORT_INTERVAL, EXIT_COORDINATOR_LOST, EXIT_OK, EXIT_UNAVAILABLE, GRACEFUL_SECONDS,
                     PROFILE_LOCAL, ServeConfig,
                     ServeLeaseHeld, ServeRefused, ServeUnavailable, allowed_viewer_origins, bind_host, check_bind,
                     validate)

LOG = logging.getLogger("taskmaster.serve")
_REFUSAL_LOGGED: dict = {}


def _warn_rarely(key: str, message: str, *args) -> None:
    """One warning per key per minute: an unauthenticated local client must not roll serve.log over (review 004 m4)."""
    now = time.monotonic()
    if now - _REFUSAL_LOGGED.get(key, -61.0) >= 60.0:
        if len(_REFUSAL_LOGGED) > 256:
            _REFUSAL_LOGGED.clear()
        _REFUSAL_LOGGED[key] = now
        LOG.warning(message, *args)
SERVE_RETIRE_REFUSED = ("this coordinator belongs to `taskmaster serve` and is never retired by a client; "
                        "to upgrade, install the new build and `systemctl restart taskmaster`, or run the "
                        "new build's `taskmaster serve --force-upgrade --root <root>` on the host")
SERVE_SHUTDOWN_REFUSED = ("this coordinator belongs to `taskmaster serve`; stop the daemon instead "
                          "(systemctl stop taskmaster, or Ctrl+C)")
SERVE_RETIRE_REFUSED_LOCAL = ("this project's local taskmaster server runs an older build while other sessions still use it; it "
                            "is replaced once they end (restart those sessions to move them onto the new build)")
OWNED_TEXT = ("another coordinator owns {root}. If it is a session's coordinator, stop it with "
              "`taskmaster coordinator stop --root {root}` or wait for its idle exit; if it is another "
              "`taskmaster serve`, stop that daemon (coordinator stop is refused there)")
ORIGIN_RE = re.compile(r"operator|[0-9a-f]{16}")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def origin_of(message) -> Origin:
    """The IPC message's origin: LOCAL when absent, else exactly {"principal": <key id>}."""
    value = message.get("origin")
    if value is None:
        return LOCAL
    principal = value.get("principal") if isinstance(value, dict) and set(value) == {"principal"} else None
    if not isinstance(principal, str) or not ORIGIN_RE.fullmatch(principal):
        raise ValueError("invalid origin")
    return Origin(principal=principal)


class ServeCoordinator(Coordinator):
    def __init__(self, root, *, registry=None, local=False, **kwargs):
        registry = registry if registry is not None else PrincipalRegistry(None)
        self.principals = registry
        self.retired_for = None            # the requester build of an accepted --force-upgrade
        self.is_local = local
        if local:
            # Local profile (local-server-002): the clone's own actor source and the base identity gate, so history
            # reads as it did under stdio; no key, no served-profile attribution.
            super().__init__(root, **kwargs)
            return
        source = ServeActorSource(registry)
        super().__init__(root, actor_source=source, **kwargs)
        self.team_identity = ServeIdentityGate(self.root, store_id=self.identity["store_id"], source=source,
                                               checkpoint=lambda stage: self.checkpoint(stage))

    def _origin(self, message):
        return origin_of(message)

    def submit(self, envelope, *, origin=LOCAL):
        # team-server-003 D5: a served command's scope and claim sessions are the principal's, checked before
        # the request can take a pending slot. Envelope errors surface from the base submit as before.
        who = sessions.requester(origin, self.principals)
        if who is not None:
            try:
                request, _ = contracts.validate(envelope)
            except (ValueError, TypeError):
                request = None
            if request is not None:
                sessions.check_scope(request, who)
        return super().submit(envelope, origin=origin)

    def _requester(self, work):
        # Again in the writer: a key revoked while the request was queued is refused here.
        who = sessions.requester(work.origin, self.principals)
        if who is not None:
            sessions.check_scope(work.request, who)
        return who

    def public_error(self, exc):
        return coordinator_public_error(exc, self.root, LOG)

    def retire(self, requester, message=None):
        """Refused for every client, except a host-local `--force-upgrade` carrying the operator-token proof
        (team-server-009 section 8.2); then the base policy decides (same or older build refused, busy, retiring)."""
        refused = self._upgrade_refusal(requester, message)
        if refused is not None:
            LOG.warning("refused a retire request from build %s: %s", describe(requester), refused)
            return {"state": "refused", "build": self.build, "reason": refused}
        self.retired_for = requester            # before the base policy stops us: the main loop reads it
        answer = Coordinator.retire(self, requester, message)
        if answer.get("state") != "retiring":
            self.retired_for = None
        return answer

    def _upgrade_refusal(self, requester, message) -> str | None:
        from . import upgrade
        if not isinstance(message, dict) or "force_upgrade" not in message:
            return SERVE_RETIRE_REFUSED_LOCAL if self.is_local else SERVE_RETIRE_REFUSED
        if message.get("origin") is not None:
            return "force-upgrade is a host-local operator action"
        proof = message["force_upgrade"]
        if type(proof) is not str or not upgrade.PROOF_RE.fullmatch(proof):
            return "the force-upgrade proof is not valid"
        try:
            key = upgrade.proof_key(upgrade.read_token(self.root))
        except Exception:  # noqa: BLE001 - a missing or unsafe token proves nothing
            return "the force-upgrade proof is not valid"
        if not hmac.compare_digest(upgrade.upgrade_proof(key, self.nonce, requester), proof):
            return "the force-upgrade proof is not valid"
        return None

    def dispatch(self, message):
        if isinstance(message, dict):
            method = message.get("method")
            if method in ("shutdown", "shutdown_if_idle"):      # shutdown_if_idle is for session coordinators only (review I1)
                check_handshake(message.get("identity"), self.identity, self.nonce, self.build)
                raise ValueError(SERVE_SHUTDOWN_REFUSED)
            # The base dispatch's own handshake order, so a refusal never answers an unauthenticated message.
            try:
                check_handshake(message.get("identity"), self.identity, self.nonce, self.build,
                                any_build=method == "retire")
            except HandshakeError as exc:      # a hook, CLI or stdio server of another build: say so in the server log
                _warn_rarely(f"hs:{log_safe(method)}", "refused %s: %s", log_safe(method), log_safe(str(exc)))
                raise
            origin = origin_of(message)                  # the base dispatch parses it again for execute
            # No origin: a host process that read the owner-only discovery file, i.e. the operator.
            # In-process callers without a principal were already stopped by the client (005 D4).
            principal = allowlist.OPERATOR if origin is LOCAL else origin.principal
            refused = allowlist.refusal(method, message, principal)
            if refused is None and method == "linear_bootstrap":
                refused = allowlist.bootstrap_entry_refusal(message.get("entry"))
            if refused is not None:
                _warn_rarely(f"rf:{log_safe(method)}:{log_safe(principal)}", "refused %s for key %s", log_safe(method), log_safe(principal))
                raise ValueError(refused)
            if origin is not LOCAL and method in scopes.NAMED_SCOPE_METHODS:
                # team-server-004 D2: before any queue or jobs slot is taken.
                sessions.check_named_scope(message.get("caller_scope"), sessions.requester(origin, self.principals))
        return super().dispatch(message)


def upgrade_served_schema(root: Path) -> dict | None:
    """Bring a served store to the current native schema before the coordinator starts (security review m-4).

    A store that never went through team mode stays at schema 3, which has no author column, so every member's text
    framed as `~unattributed` until the first team write upgraded it. Runs under the host lease with no coordinator
    yet, in one transaction; None when there is nothing to do (or no native manifest, which the start refuses)."""
    from taskmaster.coordinator.protocol import connect
    from taskmaster.native import schema, upgrade
    with contextlib.closing(connect(root)) as connection:
        try:
            row = connection.execute("SELECT value FROM native_manifest WHERE key='schema_version'").fetchone()
        except Exception:  # noqa: BLE001 - no native manifest: not a native store, which the coordinator start refuses
            return None
        if row is None or int(row[0]) >= schema.VERSION:
            return None
        return upgrade.upgrade(connection)


def load_server_module(root: Path):
    name = "taskmaster.backlog_server"
    if name not in sys.modules:
        os.environ["TASKMASTER_ROOT"] = str(root)
        os.chdir(root)
    module = importlib.import_module(name)
    if Path(module.ROOT).resolve() != root:
        raise ServeRefused(f"the server module is pointed at {module.ROOT}, not {root}")
    return module


@dataclass
class LoggingSetup:
    handlers: list = field(default_factory=list)
    saved: list = field(default_factory=list)       # (logger, level, propagate)

    def close(self):
        for logger, level, propagate in self.saved:
            for handler in self.handlers:
                logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate
        for handler in self.handlers:
            handler.close()
        self.handlers, self.saved = [], []


def configure_logging(root: Path, level: str, *, stream: bool = True) -> LoggingSetup:
    setup = LoggingSetup()
    formatter = logging.Formatter(LOG_FORMAT)
    if stream:      # off in the local profile: its stderr is a file, and serve.log has its own rotating handler (M1)
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        setup.handlers.append(handler)
    try:
        rotating = RotatingFileHandler(serve_directory(root) / "serve.log", maxBytes=1024 * 1024,
                                       backupCount=3, encoding="utf-8")
    except BaseException:
        setup.close()
        raise
    rotating.setFormatter(formatter)
    setup.handlers.append(rotating)
    numeric = getattr(logging, level.upper())
    for name, lvl in (("taskmaster.serve", numeric), ("taskmaster.coordinator", numeric),
                      ("taskmaster.integrations.linear", numeric), ("uvicorn.error", logging.WARNING)):
        logger = logging.getLogger(name)
        setup.saved.append((logger, logger.level, logger.propagate))
        logger.setLevel(lvl)
        for handler in setup.handlers:
            logger.addHandler(handler)
    return setup


def bind_socket(host: str, port: int) -> socket.socket:
    try:
        return socket.create_server((host, port), family=socket.AF_INET6 if ":" in host else socket.AF_INET)
    except OSError as exc:
        raise ServeUnavailable(f"cannot bind {host}:{port}: {exc.strerror or exc}") from exc


def export_settings(root: Path, config: ServeConfig):
    """team-server-013: the state export's settings, or None when it is off.

    An explicit setting that cannot work refuses the start (exit 2). The CLI default (`export_interval=None`)
    only turns the export on in a host clone's main checkout: it is off, with a log line, on a root that is
    not a Git checkout and on a linked worktree, where another daemon may export the same branch (013 I3)."""
    from taskmaster.coordinator import git as managed
    from .state_export import ExportError, ExportRefused, Settings, check_settings, is_linked_worktree
    explicit = config.export_interval is not None
    interval = config.export_interval if explicit else DEFAULT_EXPORT_INTERVAL
    if interval == 0:
        return None
    try:
        linked = is_linked_worktree(root)
    except (managed.GitRefused, OSError) as exc:
        if explicit or config.export_push is not None:
            raise ServeRefused(f"state export: {root} is not the top level of a Git checkout ({exc})") from None
        LOG.info("state export off: %s is not a Git checkout", root)
        return None
    if linked and not explicit:
        if config.export_push is not None:          # gate M1: a requested backup push is never dropped silently
            raise ServeRefused("state export: --export-push on a linked worktree needs an explicit --export-interval "
                               "(export is off by default there, because another daemon may export the same branch)")
        LOG.info("state export off by default on a linked worktree; pass --export-interval to turn it on")
        return None
    settings = Settings(root, branch=config.export_branch, interval=interval, push=config.export_push)
    try:
        check_settings(settings)
    except (ExportRefused, ExportError) as exc:
        raise ServeRefused(f"state export: {exc}") from None
    except OSError as exc:                           # gate M4: e.g. local/serve/export cannot be created
        raise ServeRefused(f"state export: {exc.strerror or exc}") from None
    return settings


class ServeDaemon:
    def __init__(self, config: ServeConfig, *, authenticator=None, stdout=None, middleware=(),
                 outer_middleware=(), uvicorn_options=None):
        self.config = config
        self.authenticator = authenticator
        self.stdout = stdout if stdout is not None else sys.stdout
        self._middleware = list(middleware)
        self._outer_middleware = list(outer_middleware)
        self._uvicorn_options = dict(uvicorn_options or {})
        pinned = sorted(HARDENED_KEYS & set(self._uvicorn_options))
        if pinned:
            raise ValueError(f"uvicorn_options may not override the hardened options: {', '.join(pinned)}")
        self.ready = threading.Event()
        self.startup_warnings: list[str] = []
        self.exit_code = EXIT_OK
        self._lease = None                  # team-server-009 host lease; released last in _teardown
        self.marker = None
        self.coordinator = None
        self.url = self.port = self.root = self.server_root = self.principals_path = None
        self._server = None
        self._sock = None
        self._logging = None
        self._previous_autostart = None
        self._origin_installed = False
        self._previous_origin = None
        # team-server-006: each is undone in _teardown only if its start step ran.
        self._serve_env_set = False
        self._previous_serve_env = None
        self._bs = None
        self._viewer_port_saved = False
        self._saved_viewer_port = None
        self._viewer = None                 # (server, thread)
        self.viewer_port = None
        self._sanitizer = None
        # team-server-003: per-connection sessions; the hooks are restored in _teardown only if installed.
        self.sessions = None
        self._sessions_installed = False
        self._previous_session_provider = self._previous_connected = None
        self.exporter = None                # team-server-013 StateExporter, when the export is on
        self.keeper = None                  # team-server-010 ClaimKeeper
        self._notice = None                 # team-server-010 AdoptionNotice middleware
        self._member_scope = None           # team-server-026 MemberQueryScope middleware
        self._heavy = None                  # security review I-1 HeavyCalls middleware
        self.local = None                   # local-server-002 LocalRuntime, in the local profile only
        self._previous_adopt = None
        self._previous_deny = None          # query_guard.DENY_FUNCTIONS_WHEN_SERVING before start
        self.result_written = False
        self.export_settings = None
        self._stop_requested = False
        self._closed = False
        self._lock = threading.RLock()  # a signal handler may re-enter request_stop/_teardown

    # -- start ------------------------------------------------------------------------------------

    def start(self) -> "ServeDaemon":
        try:
            self._start()
        except BaseException:
            self._teardown()
            raise
        return self

    def _start(self):
        config = validate(self.config)
        warning = check_bind(config.host, insecure_bind=config.insecure_bind)
        host = bind_host(config.host)
        try:
            root = config.root.resolve(strict=True)
        except FileNotFoundError:
            raise ServeRefused(f"{config.root} is not a taskmaster project with a local store") from None
        if not (root / ".taskmaster/local").is_dir():
            raise ServeRefused(f"{root} is not a taskmaster project with a local store")
        self.root = root
        local = config.profile == PROFILE_LOCAL
        if local:
            local_profile.preflight(root)               # before anything is created or locked
        self._logging = configure_logging(root, config.log_level, stream=not local)
        self._claim_host(root)
        if warning:
            print(warning, file=sys.stderr)
            LOG.warning(warning)
        _, created = ensure_operator_token(root)
        if created:
            LOG.info("operator token created at %s; read it with: taskmaster serve token --root %s",
                     token_path(root), root)
        ppath = principals_path(root)
        try:
            records = load(ppath)            # strict: a file the daemon would refuse at runtime refuses the start
        except PrincipalsRefused as exc:
            raise ServeRefused(str(exc)) from None
        registry = PrincipalRegistry(ppath)
        authenticator = self.authenticator or (
            OperatorTokenAuthenticator(token_path(root)) if local        # the local profile: the operator token only
            else KeyAuthenticator(OperatorTokenAuthenticator(token_path(root)), registry))
        LOG.info("principals: %d active keys", sum(1 for r in records.values() if r.active))
        self.principals_path = ppath
        self._sock = bind_socket(host, config.port)
        self.port = self._sock.getsockname()[1]
        self.url = f"http://{host if ':' not in host else f'[{host}]'}:{self.port}{config.path}"
        self._previous_autostart = client.AUTOSTART_ALLOWED
        client.AUTOSTART_ALLOWED = False
        self._previous_origin, self._origin_installed = client.ORIGIN_PROVIDER, True
        client.ORIGIN_PROVIDER = request_key_id      # the only assignment, ever (design D8); both profiles: it guards the coordinator
        # team-server-034: the served-only SQL deny list follows the profile (ServeConfig.deny_functions: team True, local False).
        self._previous_deny, query_guard.DENY_FUNCTIONS_WHEN_SERVING = (
            query_guard.DENY_FUNCTIONS_WHEN_SERVING, bool(config.deny_functions))
        # The import-time viewer and backlog_open_viewer must never start an unguarded viewer here (006 D10.1).
        self._previous_serve_env, self._serve_env_set = os.environ.get("TASKMASTER_SERVE"), True
        os.environ["TASKMASTER_SERVE"] = "1"
        # Before the coordinator: its exporter thread imports the server module through
        # native_routing.projection and must find it already pointed at this root (design D2).
        bs = load_server_module(root)
        self._bs = bs
        # team-server-003 D2/D3/D6: the only assignments of SESSION_PROVIDER and claims.CONNECTED.
        self.sessions = SessionRegistry(registry, local_key=local_profile.load_session_key(root) if local else None)
        self._previous_session_provider, self._previous_connected = bs.SESSION_PROVIDER, claims.CONNECTED
        self._sessions_installed = True
        bs.SESSION_PROVIDER = self.sessions.current
        claims.CONNECTED = self.sessions.connected
        if local:
            self._previous_adopt, claims.ADOPT_ACROSS_SESSIONS = claims.ADOPT_ACROSS_SESSIONS, False
        self.server_root = Path(bs.ROOT).resolve()
        self._saved_viewer_port, self._viewer_port_saved = bs.VIEWER_PORT, True
        bs.VIEWER_PUBLIC_URL = config.viewer_origins[0] + "/" if config.viewer_origins else None
        principals = principal_limiter(config)      # one budget per key, shared by MCP and the viewer (006 D7)
        inflight = principal_inflight(config)
        failures = failure_budget()
        if config.viewer_port is not None:
            # Before the coordinator, so a bind failure is exit 69 with nothing there to undo.
            guard = ViewerGuard(authenticator=authenticator, origins=allowed_viewer_origins(config, config.viewer_port),
                                root=root, max_body_bytes=config.max_body_bytes, header_timeout=config.header_timeout,
                                principals=principals, inflight=inflight, failures=failures,
                                tracked=TrackedFiles(root), viewer_port=config.viewer_port,
                                body_timeout=config.body_timeout,
                                auth_adapter=CookieLogin(authenticator=authenticator, principals=principals,
                                                         inflight=inflight))
            server, thread, self.viewer_port = start_viewer(bs, guard, config.viewer_port)
            self._viewer = (server, thread)
            LOG.info("viewer on http://127.0.0.1:%s/ (login required)", self.viewer_port)
        elif local and config.viewer_band:
            self._start_local_viewer(bs, config, authenticator, principals, inflight, failures)
        elif local:
            bs.VIEWER_LOCAL_OFF = "The viewer is off in this server (it was started with --no-viewer)."
        # Before the coordinator, so a refused export setting is exit 2 with nothing there to undo.
        self.export_settings = export_settings(root, config)
        if not local and not ownership_held(root):        # a stray session coordinator: the start below refuses anyway
            upgraded = upgrade_served_schema(root)
            if upgraded:
                LOG.info("upgraded the store from native schema %s to %s: a served store is a team store",
                         upgraded["from"], upgraded["to"])
        try:
            self.coordinator = ServeCoordinator(root, registry=registry, local=local)
            self.coordinator.start()
        except OwnershipUnavailable:
            raise ServeUnavailable(OWNED_TEXT.format(root=root)) from None
        except UnsupportedStoreError as exc:
            raise ServeRefused(f"{root} is not an activated native store: {exc}") from exc
        except PermissionError as exc:
            raise ServeRefused(f"{exc}") from exc
        self._startup_warnings(root)
        self._integrity_thread = store_integrity.start_background(root / ".taskmaster/local/store.db", self._integrity_warning)   # POSIX only; advice only
        if self.export_settings is not None:
            from .state_export import StateExporter
            self.exporter = StateExporter(self.coordinator, self.export_settings).start()
            LOG.info("state export: branch %s every %s s%s", self.export_settings.branch, self.export_settings.interval,
                     f", pushing to remote {self.export_settings.push}" if self.export_settings.push else "")
        # team-server-010 D5/D6: restart grace first (no MCP session exists yet), then the thread.
        self.keeper = ClaimKeeper(self.coordinator, self.sessions, root)
        self.keeper.startup()
        self.keeper.start()
        self.sessions.on_disconnect.append(lambda entry: self.keeper.wake())
        # team-server-027: outermost after the client-info capture, so the sanitizer still sees raw text and the
        # framer sees the adoption block (design D1.1).
        if not local:       # the local profile serves one person's own store: no untrusted framing, notes or adoption block
            self._framer = FrameUntrusted(self.coordinator, pre_team=lambda: pre_team_of(root), non_string=drop_non_string_schemas_sync(bs.mcp._inner))
            bs.mcp._inner.add_middleware(self._framer)
            bs.mcp._inner.instructions = bs.server_instructions(server_mode=True) + " " + untrusted.SERVER_NOTE
        else:               # the local operator can run backlog_sync: the stdio wording (hardening M-2)
            bs.mcp._inner.instructions = bs.server_instructions(server_mode=False)
        self._sanitizer = SanitizeErrors(root)
        bs.mcp._inner.add_middleware(self._sanitizer)
        if not local:
            self._notice = AdoptionNotice(self.coordinator, self.sessions)
            bs.mcp._inner.add_middleware(self._notice)
            self._member_scope = MemberQueryScope()      # team-server-026
            bs.mcp._inner.add_middleware(self._member_scope)
            self._heavy = HeavyCalls()                   # security review I-1
            bs.mcp._inner.add_middleware(self._heavy)
        limits = Middleware(PrincipalLimits, limiter=principals, inflight=inflight,
                            keyer=local_profile.limit_key if local else None)
        if local:
            routes, session_idle = self._local_layers(config, token_path(root)), None
            local_layer = [Middleware(local_profile.LocalMiddleware, runtime=self.local, mcp_path=config.path)]
        else:
            # team-server-007: teammates' hooks and mods read here, behind every layer above, on the same limiter.
            reads = ReadApi(root=root, server=bs.mcp._inner, limiter=principals, build=describe(self.coordinator.build))
            # team-server-008: a teammate's merge hook sends its stamp here, as a command, on the same layers.
            writes = WriteApi(root=root, server=bs.mcp._inner, limiter=principals, build=describe(self.coordinator.build))
            routes, session_idle, local_layer = [*reads.routes(), *writes.routes()], config.session_idle_seconds, []
        app = build_app(bs.mcp._inner, authenticator, path=config.path, allowed_hosts=config.allowed_hosts,
                        middleware=[limits, *local_layer, *self._middleware], session_idle_seconds=session_idle,
                        registry=registry, sessions=self.sessions, routes=routes)
        for wrap in reversed(self._outer_middleware):
            app = wrap(app)
        # Outermost, so nothing a caller passes can sit ahead of the size cap (006 D1).
        app = RequestGuard(app, max_body_bytes=config.max_body_bytes, body_timeout=config.body_timeout,
                           failures=failures, authenticator=authenticator)
        self._server = uvicorn.Server(uvicorn.Config(app, **{
            "lifespan": "on", "log_config": None, "access_log": False, "server_header": False,
            "timeout_graceful_shutdown": GRACEFUL_SECONDS, **self._uvicorn_options,
            **hardened_uvicorn_options(config)}))
        LOG.info("starting: root=%s url=%s pid=%s build=%s", root, self.url, os.getpid(),
                 describe(self.coordinator.build))

    def _start_local_viewer(self, bs, config, authenticator, principals, inflight, failures) -> None:
        """The local profile's guarded viewer (local-server-004): the first free port of the project's band, signed in with
        a one-time link the `backlog_open_viewer` tool mints. No free port is logged and is never fatal."""
        root = self.root
        target = bs._project_port()
        login = LocalLinkLogin(authenticator=authenticator, principals=principals, inflight=inflight,
                               operator_key=lambda: read_operator_token(token_path(root)))
        guard = ViewerGuard(authenticator=authenticator, origins=allowed_viewer_origins(config, target), root=root,
                            max_body_bytes=config.max_body_bytes, header_timeout=config.header_timeout,
                            principals=principals, inflight=inflight, failures=failure_budget(), tracked=TrackedFiles(root),
                            viewer_port=target, body_timeout=config.body_timeout, auth_adapter=login,
                            charge_anonymous=False)       # its own budget: never the MCP app's (review I2)
        admit = guard.admit

        def admit_and_note(handler) -> bool:
            admitted = admit(handler)
            if admitted and handler.viewer_identity is not None and self.local is not None:
                self.local.mark_active()
            return admitted
        guard.admit = admit_and_note       # reading the viewer keeps the server up (review 004 m3)
        started = start_band_viewer(bs, guard)
        if started is None:
            bs.VIEWER_LOCAL_OFF = ("The viewer is off: every port from 6800 to 6899 is taken on this machine. Close what "
                                   "holds them and ask again.")
            return
        server, thread, self.viewer_port = started
        self._viewer = (server, thread)
        bs.VIEWER_LINK_MINTER = lambda: f"http://127.0.0.1:{self.viewer_port}{LINK_PATH}?t={login.mint_link()}"
        LOG.info("viewer on http://127.0.0.1:%s/ (opened with a one-time link from backlog_open_viewer)", self.viewer_port)

    def _local_layers(self, config: ServeConfig, token_file: Path) -> list:
        """The local profile's runtime (discovery, status, retire, idle exit) and its control routes."""
        plugin_dir = config.plugin_dir or Path(__file__).resolve().parents[2]
        self.local = local_profile.LocalRuntime(
            root=self.root, url_provider=lambda: self.url, plugin_dir=plugin_dir, token_file=token_file,
            sessions=self.sessions, coordinator_provider=lambda: self.coordinator, idle_exit=config.idle_exit_seconds)
        self.local.on_drain.append(self.keeper.request_stop)    # a draining server must not shorten claims on its way out
        return local_profile.LocalControl(self.local).routes()

    def _claim_host(self, root: Path) -> None:
        """Take the host lease before anything opens the store, then check the marker and its host_id.

        The lease is what exempts this process from the split-brain fence (team-server-009 section 6.3).
        """
        from taskmaster import server_fence, server_marker
        from . import upgrade
        config = self.config
        if config.profile == PROFILE_LOCAL:
            try:
                self._lease = server_fence.claim_host(root)
            except server_fence.HostLeaseHeld as exc:
                raise ServeLeaseHeld(str(exc)) from None
            local_profile.hand_over_session_coordinator(root)       # an idle session coordinator leaves; a busy one -> exit 68
            return
        try:
            self._lease = server_fence.claim_host(root)
        except server_fence.HostLeaseHeld as exc:
            if not config.force_upgrade:
                raise ServeUnavailable(str(exc)) from None
            self._check_marker(root)             # before retiring a healthy daemon for a start that would fail
            started = time.monotonic()
            upgrade.hand_over(root, config.upgrade_timeout)
            try:
                self._lease = server_fence.claim_host(
                    root, retry_seconds=max(1.0, config.upgrade_timeout - (time.monotonic() - started)))
            except server_fence.HostLeaseHeld as again:
                raise ServeUnavailable(str(again)) from None
        else:
            if config.force_upgrade and ownership_held(root):     # a stray session coordinator holds owner.lock
                upgrade.hand_over(root, config.upgrade_timeout, lease_is_ours=True)
        self._check_marker(root)

    def _check_marker(self, root: Path) -> None:
        """The marker (or latch) must be readable and bound to this clone's host id; sets `self.marker`."""
        from taskmaster import server_fence, server_marker
        try:
            marker = server_marker.server_mode(root)
        except server_marker.ServerConfigError as exc:
            raise ServeRefused(f"{exc}; fix or remove it, then start again") from None
        if marker is None:
            LOG.warning("no server marker in %s: ordinary sessions in this clone are not fenced; write it "
                        "with `taskmaster serve marker write`.", root)
            self.marker = None
            return
        mine = server_fence.read_host_id(root)
        if mine != marker.host_id:
            raise ServeRefused(
                f"the server marker names host_id {marker.host_id}, but this clone's is "
                f"{mine or 'missing (.taskmaster/local/serve/host-id)'}: `taskmaster serve` runs only on the host "
                f"clone the marker was written for. Run it there, or re-write the marker here with "
                f"`taskmaster serve marker write --root {root}`.")
        from .untrack import head_tracks_taskmaster          # D13: a host HEAD that tracks .taskmaster/ (team-server-014)
        tracked = head_tracks_taskmaster(root, closed=True)
        if tracked:
            raise ServeRefused(
                f"the host clone's HEAD ({tracked}) tracks .taskmaster/; Git may have overwritten live projection "
                "files. Don't serve until HEAD is back on an untracked commit: "
                "docs/runbooks/team-server-migration.md#host-head")
        self.marker = {"url": marker.url, "host_id": marker.host_id}

    def _integrity_warning(self, record: dict) -> None:
        """A failed full check joins the startup warnings (late, from the check's thread); serving continues."""
        self.startup_warnings.append("integrity_check FAILED: " + "; ".join(record["detail"]))
        LOG.warning("startup warning: store integrity_check failed; stop the server and restore from the last backup")

    def _startup_warnings(self, root: Path) -> None:
        try:
            with contextlib.closing(protocol.connect(root, readonly=True)) as connection:
                if not identity_gate.team_mode_enabled(root, project_doc=identity_gate._project_doc(connection)):
                    LOG.warning("team mode is off for %s: writes carry the key's handle, but no profiles are "
                                "created and the bootstrap token may write as operator; turn team mode on "
                                "(team.sync.mode \"manual\") before issuing teammate keys", root)
                if connection.execute("SELECT 1 FROM entity_core WHERE kind='profile' AND public_id='operator' "
                                      "AND deleted=0").fetchone():
                    LOG.warning("profile 'operator' predates the reserved handle; bootstrap-token writes are "
                                "not that person's")
        except Exception as exc:  # noqa: BLE001 - advice only; never fails the start
            LOG.warning("could not check team mode at start: %s", type(exc).__name__)

    # -- run --------------------------------------------------------------------------------------

    def serve(self) -> int:
        previous = {}
        try:
            if threading.current_thread() is threading.main_thread():
                names = ["SIGTERM", "SIGINT"] + (["SIGBREAK"] if hasattr(signal, "SIGBREAK") else [])
                for name in names:
                    sig = getattr(signal, name)
                    previous[sig] = signal.signal(sig, self._on_signal)
            thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._sock]},
                                      name="taskmaster-serve-http", daemon=True)
            thread.start()
            while thread.is_alive():
                thread.join(0.25)
                if not self.ready.is_set() and self._server.started:
                    self._announce()
                if self.local is not None and self.ready.is_set() and not self._stop_requested:
                    why = self.local.tick()
                    if why:
                        LOG.info("local server stopping: %s", why)
                        self.exit_code = EXIT_OK
                        self.request_stop(why)
                if (self.coordinator.stopping.is_set() and not self._stop_requested
                        and self.coordinator.retired_for is not None):
                    LOG.info("retired by a local --force-upgrade to build %s", describe(self.coordinator.retired_for))
                    self.exit_code = EXIT_OK
                    self.request_stop("retired")
                elif self.coordinator.stopping.is_set() and not self._stop_requested:
                    LOG.error("coordinator stopped; exiting so the supervisor restarts the daemon")
                    self.exit_code = EXIT_COORDINATOR_LOST
                    self.request_stop("coordinator lost")
            if not self.ready.is_set() and not self._stop_requested:
                self.exit_code = EXIT_UNAVAILABLE
                LOG.error("http server failed to start")
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            self.close()
        return self.exit_code

    def _announce(self):
        if self.local is not None:
            self.local.publish()             # discovery first: a bridge that sees "ready" finds it
            self._write_result("ready", url=self.url)
        line = json.dumps({"event": "ready", "url": self.url, "pid": os.getpid(), "root": str(self.root),
                           "server_root": str(self.server_root), "build": describe(self.coordinator.build),
                           "token_file": str(token_path(self.root)),
                           "principals_file": str(self.principals_path),
                           "marker": self.marker,
                           "viewer_url": f"http://127.0.0.1:{self.viewer_port}/" if self.viewer_port else None,
                           "export": None if self.export_settings is None else {
                               "branch": self.export_settings.branch, "interval": self.export_settings.interval,
                               "push": self.export_settings.push}})
        self.stdout.write(line + "\n")
        self.stdout.flush()
        self.ready.set()

    def _write_result(self, status: str, reason: str = "", *, url=None) -> None:
        """Local profile: the launch outcome for the bridge (stdout is DEVNULL after its double spawn)."""
        path = self.config.result_file
        if path is None or self.result_written:
            return
        from taskmaster import localserve
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            localserve.write_result(path, status, reason, pid=os.getpid(), url=url)
            self.result_written = True
        except Exception as exc:  # noqa: BLE001 - the bridge times out and falls back
            LOG.warning("could not write the launch result: %s", type(exc).__name__)

    def request_stop(self, reason: str = "request") -> None:
        if self.keeper is not None:
            self.keeper.request_stop()          # before uvicorn closes every transport: no shortening on the way down
        if self.exporter is not None:
            self.exporter.request_stop()        # overlaps uvicorn's graceful window (013 §5.1)
        server = self._server
        if server is None:
            return
        with self._lock:
            first = not self._stop_requested
            self._stop_requested = True
        if first:
            LOG.info("stop requested (%s)", reason)
            server.should_exit = True
        else:
            server.force_exit = True

    def _on_signal(self, signum, frame) -> None:
        self.request_stop(signal.Signals(signum).name)

    # -- stop -------------------------------------------------------------------------------------

    def close(self) -> None:
        self._teardown(announce=True)

    def _teardown_hardening(self) -> None:
        bs = self._bs
        if self._sanitizer is not None and bs is not None:
            with contextlib.suppress(ValueError):
                bs.mcp._inner.middleware.remove(self._sanitizer)
            self._sanitizer = None
        if self._notice is not None and bs is not None:
            with contextlib.suppress(ValueError):
                bs.mcp._inner.middleware.remove(self._notice)
            self._notice = None
        if getattr(self, '_framer', None) is not None and bs is not None:
            with contextlib.suppress(ValueError):
                bs.mcp._inner.middleware.remove(self._framer)
            self._framer = None
        if self._member_scope is not None and bs is not None:
            with contextlib.suppress(ValueError):
                bs.mcp._inner.middleware.remove(self._member_scope)
            self._member_scope = None
        if self._heavy is not None and bs is not None:
            with contextlib.suppress(ValueError):
                bs.mcp._inner.middleware.remove(self._heavy)
            self._heavy = None
        if self._viewer is not None:
            try:
                stop_viewer(*self._viewer)
            except Exception:
                LOG.exception("viewer did not stop cleanly")
            self._viewer = None
        if self._viewer_port_saved and bs is not None:
            bs.VIEWER_PORT = self._saved_viewer_port
            self._viewer_port_saved = False
        if self._serve_env_set:
            if self._previous_serve_env is None:
                os.environ.pop("TASKMASTER_SERVE", None)
            else:
                os.environ["TASKMASTER_SERVE"] = self._previous_serve_env
            self._serve_env_set = False
        if bs is not None:
            bs.VIEWER_PUBLIC_URL = None
            bs.VIEWER_LINK_MINTER = None
            bs.VIEWER_LOCAL_OFF = None

    def _teardown(self, announce=False):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._teardown_hardening()
        if self.keeper is not None:
            self.keeper.stop(timeout=10.0)      # before the coordinator, as the exporter
            self.keeper = None
        if self.exporter is not None:
            self.exporter.stop(timeout=10.0)    # before the coordinator: an in-flight read holds publication
            if self.exporter.thread is not None and self.exporter.thread.is_alive():
                LOG.warning("state export still running at shutdown; abandoning it")
            self.exporter = None
        if self.coordinator is not None:
            try:
                self.coordinator.stop()
                self.coordinator.close()
            except Exception:
                LOG.exception("coordinator did not close cleanly")
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._previous_autostart is not None:
            client.AUTOSTART_ALLOWED = self._previous_autostart
            self._previous_autostart = None
        query_snapshots.close_all()              # team-server-034: this store's query snapshot files
        if self._previous_deny is not None:
            query_guard.DENY_FUNCTIONS_WHEN_SERVING, self._previous_deny = self._previous_deny, None
        if self._origin_installed:
            client.ORIGIN_PROVIDER = self._previous_origin
            self._previous_origin, self._origin_installed = None, False
        if self.local is not None:
            self.local.unpublish()
        if self._sessions_installed:
            if self._bs is not None:
                self._bs.SESSION_PROVIDER = self._previous_session_provider
            claims.CONNECTED = self._previous_connected
            if self._previous_adopt is not None:
                claims.ADOPT_ACROSS_SESSIONS, self._previous_adopt = self._previous_adopt, None
            self._previous_session_provider = self._previous_connected = None
            self._sessions_installed = False
        if announce:
            LOG.info("stopped (exit %s)", self.exit_code)
        if self._lease is not None:           # last: a successor that wins the lease finds the rest free
            self._lease.release()
            self._lease = None
        if self._logging is not None:
            self._logging.close()
            self._logging = None
