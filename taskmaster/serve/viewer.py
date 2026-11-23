# User intent: the server-mode viewer: token login to an HttpOnly SameSite=Strict cookie (bootstrap token on the host only), Host, Origin and CSRF checks on writes, no CORS, only git-tracked files through /file/, and writes attributed to the logged-in key.
"""The serve daemon's guarded viewer (design D10). The solo ViewerHandler is untouched; every guard lives in a
subclass built by make_guarded_handler and served by a daemon-owned GuardedViewerServer on 127.0.0.1."""
from __future__ import annotations

import html
import io
import json
import re
import logging
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import auth
from .app import log_safe
from .config import DEFAULT_BODY_TIMEOUT, ServeUnavailable
from .config import VIEWER_MAX_SESSIONS_PER_KEY  # noqa: F401  (re-exported for tests)
from .config import VIEWER_MAX_CONNECTIONS  # noqa: F401  (re-exported; GuardedViewerServer reads it at init)
from .limits import InFlight, KeyedLimiter, peer_key, retry_after
from .sanitize import internal_message, new_ref, scrub
from .tracked import TrackedFiles, read_checked, tracked_file_path
from .viewer_auth import (CSRF_HEADER, NOT_ACCEPTED, OPERATOR_OFF_HOST, CookieLogin, Reply,  # noqa: F401  (re-exported)
                          ViewerAuth, ViewerSession, SessionStore, fetch_site_ok, handler_scope, origin_fits_host,
                          origin_hosts)

LOG = logging.getLogger("taskmaster.serve")
STATIC_PREFIX = "/static/v3/"
# The route allow-list (team-server-028 D2): Caddy's @viewer matcher lists exactly caddy_matcher_forms(), and a test pins it.
PUBLIC_EXACT = ("/favicon.ico", *CookieLogin.PUBLIC_PATHS)
SESSION_EXACT = ("/",)
ROUTE_PREFIXES = ("/static/v3", "/api", "/file")     # each serves "<prefix>/..." only, never "<prefix>" itself
REFUSED_EXACT = frozenset({"/api/session"})
STATIC_DIRS = frozenset({"css", "js", "vendor"})
STATIC_EXTENSIONS = frozenset({".css", ".js", ".woff2", ".woff", ".svg", ".ico", ".png"})
STATIC_BARRED_SEGMENTS = frozenset({"_dormant", "dev", "node_modules", "tests"})
_NUL, _BACKSLASH = chr(0), chr(92)
KINDS = ("app", "static", "file-md", "file-raw")
_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


def response_headers(kind: str, origin: str | None) -> tuple[tuple[str, str], ...]:
    """The security headers of one response (design 028 sections 4.2-4.3): exactly one CSP, chosen by response kind.
    `origin` is the allowed origin whose host the request used; scripts, styles and fonts may come only from its
    /static/v3/ directory, never from 'self', because 'self' would also cover teammates' /file/ content."""
    src = f"{origin}/static/v3/" if origin else "'none'"
    if kind == "app":
        csp = (f"default-src 'none'; script-src {src}; style-src {src}; font-src {src}; img-src 'self'; "
               "connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; "
               "frame-ancestors 'none'")
    elif kind == "file-md":
        # A sandbox gives the page an opaque origin: it cannot read the viewer with the visitor's cookie or pass the
        # Origin check on a write (006 review I1).
        csp = (f"sandbox allow-scripts; default-src 'none'; script-src {src}; style-src {src}; font-src {src}; "
               "img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; "
               "frame-ancestors 'none'")
    elif kind == "file-raw":
        csp = "sandbox; default-src 'none'; frame-ancestors 'none'"
    else:
        csp = "default-src 'none'; frame-ancestors 'none'"
    # same-origin, not no-referrer: under no-referrer a browser's same-origin form POST carries `Origin: null`, which
    # the login's Origin check must refuse, so nobody could sign in (006 review C1).
    headers = [("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
               ("Referrer-Policy", "same-origin")]
    if kind != "static":
        headers.append(("Cross-Origin-Opener-Policy", "same-origin"))
    if kind in ("app", "file-raw"):          # never static: the sandboxed file page is a cross-origin requester
        headers.append(("Cross-Origin-Resource-Policy", "same-origin"))
    if kind in ("app", "file-md"):
        headers.append(("Permissions-Policy", _PERMISSIONS_POLICY))
    if kind in ("file-md", "file-raw"):
        headers.append(("Cache-Control", "no-store"))
    return tuple(headers)


READ_METHODS = frozenset({"GET", "HEAD"})
ALWAYS_CHARGED = frozenset({401})
CHARGED_WITHOUT_SESSION = frozenset({400, 404, 408, 411, 413, 421})     # design D6/D10.6; never 403 or 429
_DIGITS = re.compile(r"[0-9]+")


def route_allowed(path: str, public: tuple[str, ...] = PUBLIC_EXACT) -> bool:
    """Is this decoded path one of the viewer's routes (design 028 section 1.4)? Everything else is a 404."""
    if path in REFUSED_EXACT:
        return False
    if path in public or path in SESSION_EXACT:
        return True
    return any(path.startswith(prefix + "/") and len(path) > len(prefix) + 1 for prefix in ROUTE_PREFIXES)


def caddy_matcher_forms() -> list[str]:
    """What Caddy's @viewer path matcher must list, exactly (design 028 sections 1.4, 2.3)."""
    return [*PUBLIC_EXACT, *SESSION_EXACT, *(prefix + "/*" for prefix in ROUTE_PREFIXES)]


def static_asset_allowed(rel: str) -> bool:
    """May this path under /static/v3/ be served to anyone (design 028 section 1.2)? `rel` is decoded exactly once, so
    a percent sign left in it can only be an attempt at a second decode."""
    if not rel or any(c in rel for c in (_BACKSLASH, _NUL, "%")):
        return False
    segments = rel.split("/")
    if segments[0] not in STATIC_DIRS or len(segments) < 2:
        return False
    for segment in segments:
        if segment in ("", ".", "..") or segment.startswith(".") or segment in STATIC_BARRED_SEGMENTS:
            return False
    name = segments[-1].lower()
    return Path(name).suffix in STATIC_EXTENSIONS and not name.endswith("-demo.js")


class ViewerGuard:
    def __init__(self, *, authenticator, origins: tuple[str, ...], root: Path, max_body_bytes: int,
                 header_timeout: float, principals: KeyedLimiter, inflight: InFlight, failures: KeyedLimiter,
                 tracked: TrackedFiles, viewer_port: int, clock=time.monotonic,
                 body_timeout: float = DEFAULT_BODY_TIMEOUT, auth_adapter: ViewerAuth | None = None,
                 charge_anonymous: bool = True):
        # False (local profile): requests with no session (404s, bad Hosts, anonymous 401s) never spend the budget; any
        # page in the person's browser can send them from 127.0.0.1 (local-server-004 review I2).
        self.charge_anonymous = charge_anonymous
        self.authenticator = authenticator
        self.body_timeout = float(body_timeout)
        self.root = Path(root)
        self.max_body_bytes = int(max_body_bytes)
        self.header_timeout = float(header_timeout)
        self.principals = principals
        self.inflight = inflight
        self.failures = failures
        self.tracked = tracked
        self.auth: ViewerAuth = auth_adapter or CookieLogin(authenticator=authenticator, principals=principals,
                                                            inflight=inflight, clock=clock)
        self.public_paths = ("/favicon.ico", *self.auth.PUBLIC_PATHS)
        self.origins: tuple[str, ...] = ()
        self.hosts: frozenset[str] = frozenset()
        self.viewer_port = viewer_port
        self._set_origins(tuple(origins))

    def _set_origins(self, origins: tuple[str, ...]) -> None:
        self.origins = tuple(dict.fromkeys(origins))
        hosts: set[str] = set()
        for origin in self.origins:
            hosts |= origin_hosts(origin)
        self.hosts = frozenset(hosts)
        self.auth.configure(self.origins, self.viewer_port)

    def origin_for_host(self, host: str | None) -> str | None:
        """The allowed origin whose host set holds this Host header (any admitted request has one)."""
        wanted = (host or "").strip().lower()
        for origin in self.origins:
            if wanted in origin_hosts(origin):
                return origin
        return None

    def set_port(self, port: int) -> None:
        """After the bind: the loopback origins and the cookie name use the real port (viewer_port 0 in tests)."""
        old = self.viewer_port
        loopback_old = {f"http://127.0.0.1:{old}", f"http://localhost:{old}"}
        public = tuple(o for o in self.origins if o not in loopback_old)
        self.viewer_port = port
        self._set_origins((*public, f"http://127.0.0.1:{port}", f"http://localhost:{port}"))

    # -- request facts ---------------------------------------------------------------------------

    @staticmethod
    def peer(handler) -> str:
        tcp = handler.client_address[0]
        address = tcp
        try:
            import ipaddress
            loopback = ipaddress.ip_address(tcp).is_loopback
        except ValueError:
            loopback = False
        if loopback:
            forwarded = handler.headers.get_all("X-Forwarded-For") or []
            entries = [e.strip() for value in forwarded for e in value.split(",") if e.strip()]
            if entries:
                address = entries[-1]
        return peer_key(address)

    # -- answers -------------------------------------------------------------------------------

    def _write(self, handler, status: int, body: bytes, content_type: str, headers=()) -> None:
        handler.close_connection = True
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            for name, value in headers:
                handler.send_header(name, value)
            handler.end_headers()
            if handler.command != "HEAD":
                handler.wfile.write(body)
        except OSError:
            pass

    def _refuse(self, handler, status: int, payload: dict | None, *, headers=(), peer: str | None = None,
                html_body: bytes | None = None, log: bool = True, charge: bool = True) -> bool:
        charge = charge and (self.charge_anonymous or status not in CHARGED_WITHOUT_SESSION)
        if status == 401 and not self.charge_anonymous and not self.auth.presents_credential(handler):
            charge = False
        peer = peer if peer is not None else self.peer(handler)
        if log:
            # DEBUG: with nothing charged (local profile) a page could roll the log with refused requests.
            LOG.log(logging.INFO if self.charge_anonymous else logging.DEBUG, "viewer refused %s %s %s from %s", status, log_safe(handler.command),
                     log_safe(urlparse(handler.path).path), log_safe(peer))
        # Charge before answering, so a client that reads the refusal never sees the budget unspent.
        if charge and (status in ALWAYS_CHARGED
                       or (status in CHARGED_WITHOUT_SESSION and not self.auth.has_identity(handler))):
            self.failures.take(peer)
            wait = self.failures.blocked(peer)
            if wait > 0:
                LOG.warning("peer %s exhausted its failure budget; refusing for %ss", log_safe(peer), retry_after(wait))
        if html_body is not None:
            self._write(handler, status, html_body, "text/html; charset=utf-8", headers)
        elif payload is None:
            self._write(handler, status, b"", "text/plain; charset=utf-8", headers)
        else:
            self._write(handler, status, json.dumps(payload).encode(), "application/json; charset=utf-8", headers)
        return False

    def _redirect(self, handler, location: str, headers=()) -> None:
        self._write(handler, 303, b"", "text/plain; charset=utf-8", (("Location", location), *headers))

    # -- the chokepoint (design D10.6) ---------------------------------------------------------

    def admit(self, handler) -> bool:
        handler.principal = None
        handler.viewer_identity = None
        handler._inflight_key = None
        # Per-request state, reset here so a future keep-alive can't carry one answer's CSP kind into the next (gate M5).
        handler.viewer_kind = "app"
        handler._checked_file = None
        method = handler.command
        words = (getattr(handler, "requestline", "") or "").split()
        target = words[1] if len(words) == 3 else handler.path     # the stdlib collapses a leading "//" in .path
        path = unquote(urlparse(handler.path).path)         # decoded once; every later check and the static hook use this
        handler.viewer_path = path
        peer = self.peer(handler)
        wait = self.failures.blocked(peer)
        if wait > 0:
            # Silent while the budget stays empty: the one "exhausted" warning was logged (design D6).
            return self._refuse(handler, 429, {"error": "too many refused requests"}, peer=peer, log=False,
                                headers=(("Retry-After", str(retry_after(wait))),))
        host = (handler.headers.get("Host") or "").strip().lower()
        if len(handler.headers.get_all("Host") or []) != 1 or host not in self.hosts:
            return self._refuse(handler, 421, {"error": "misdirected request"}, peer=peer)
        if handler.headers.get("Transfer-Encoding"):
            return self._refuse(handler, 411, {"error": "length required"}, peer=peer)
        lengths = handler.headers.get_all("Content-Length") or []
        if lengths:
            if len(set(lengths)) != 1 or not _DIGITS.fullmatch(lengths[0].strip()):
                return self._refuse(handler, 400, {"error": "bad request"}, peer=peer)
            if int(lengths[0]) > self.max_body_bytes:
                return self._refuse(handler, 413, {"error": "request body too large", "limit": self.max_body_bytes},
                                    peer=peer)
        if method == "OPTIONS":
            handler.close_connection = True
            return self._refuse(handler, 405, None, peer=peer, headers=(("Allow", "GET, HEAD, POST, PUT, PATCH"),),
                                log=False)
        if (not target.startswith("/") or target.startswith("//") or _NUL in path or _BACKSLASH in path
                or ".." in path.split("/")):
            return self._refuse(handler, 400, {"error": "bad request"}, peer=peer)
        if not route_allowed(path, self.public_paths) or (path.startswith(STATIC_PREFIX)
                                       and not static_asset_allowed(path[len(STATIC_PREFIX):])):
            return self._refuse(handler, 404, {"error": "not found"}, peer=peer)
        reply = Reply(handler, peer, refuse=self._refuse, redirect=self._redirect, write=self._write,
                      read_body=self._read_body)
        if path in self.auth.PUBLIC_PATHS:
            self.auth.answer_public(handler, path, peer, reply)
            return False
        if method in READ_METHODS and (path == "/favicon.ico" or path.startswith(STATIC_PREFIX)):
            return True
        identity = self.auth.identify(handler)
        writing = method not in READ_METHODS
        if identity is None:
            if writing or path.startswith("/api/"):
                return self._refuse(handler, 401, {"error": "sign in at /login"}, peer=peer,
                                    charge=not self.auth.presents_credential(handler))
            self._redirect(handler, self.auth.sign_in_location(handler))
            return False
        principal = identity.principal
        if principal.key_id == auth.OPERATOR_KEY_ID and not auth.is_host_local(handler_scope(handler)):
            LOG.warning("viewer refused operator session from %s: not host-local", log_safe(peer))
            return self._refuse(handler, 403, {"error": OPERATOR_OFF_HOST}, peer=peer, log=False)
        key = principal.key_id
        if not self.inflight.acquire(key):
            LOG.info("viewer: too many concurrent requests key=%s principal=%s", log_safe(key), log_safe(principal.handle))
            return self._refuse(handler, 429, {"error": "too many concurrent requests"}, peer=peer,
                                headers=(("Retry-After", "1"),), log=False)
        wait = self.principals.take(key)
        if wait > 0:
            self.inflight.release(key)
            LOG.info("viewer: rate limited key=%s principal=%s", log_safe(key), log_safe(principal.handle))
            return self._refuse(handler, 429, {"error": "rate limited"}, peer=peer,
                                headers=(("Retry-After", str(retry_after(wait))),), log=False)
        handler._inflight_key = key
        if writing:
            if not origin_fits_host(handler, self.origins) or not fetch_site_ok(handler):
                return self._refuse(handler, 403, {"error": "cross-origin write refused"}, peer=peer)
            refusal = self.auth.check_forgery(handler, identity)
            if refusal is not None:
                return self._refuse(handler, 403, {"error": refusal}, peer=peer)
        handler.principal = principal
        handler.viewer_identity = identity
        length = int(lengths[0]) if lengths else 0
        if length:
            # The stdlib handler reads with only a per-recv idle timeout; read here against one deadline instead.
            body = self._read_body(handler, length, peer)
            if body is None:
                return False
            handler.rfile = io.BytesIO(body)        # the base handler reads it from here; the connection closes after
        return True

    def _read_body(self, handler, length: int, peer: str) -> bytes | None:
        """The whole body within body_timeout (design D4, for the viewer: 006 review I3), or a refusal: 408 when
        the deadline passes, 400 when the client stops short. Either is charged when there is no session."""
        deadline = time.monotonic() + self.body_timeout
        chunks, got, failure = [], 0, None
        sock = handler.connection
        try:
            while got < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = 408
                    break
                sock.settimeout(remaining)
                chunk = handler.rfile.read1(length - got)
                if not chunk:
                    failure = 400
                    break
                chunks.append(chunk)
                got += len(chunk)
        except OSError:                              # includes the socket timeout at the deadline
            failure = 408
        try:
            sock.settimeout(self.header_timeout)     # before answering, so the answer gets a normal write timeout
        except OSError:
            pass
        if failure == 408:
            self._refuse(handler, 408, {"error": "request body timed out"}, peer=peer)
            return None
        if failure == 400:
            self._refuse(handler, 400, {"error": "bad request"}, peer=peer)
            return None
        return b"".join(chunks)

def repo_relative(value, root: Path):
    """Every string in an API answer that names a path under the served root, made root-relative, at any depth (028
    gate, Codex minor): a member needs the file's place in the repository, never the host's directory layout."""
    prefixes = tuple(dict.fromkeys(p for p in (str(root), str(root).replace(chr(92), "/")) if p))

    def walk(item):
        if isinstance(item, str):
            for prefix in prefixes:
                if item.startswith(prefix) and item[len(prefix):len(prefix) + 1] in ("/", chr(92)):
                    return item[len(prefix) + 1:].replace(chr(92), "/")
                if item == prefix:
                    return "."
            return item
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(child) for child in item]
        return item
    return walk(value)


class _DiscardBody:
    """Where a HEAD answer's body goes once its headers are out (028 gate M4)."""
    closed = False

    def write(self, data) -> int:
        return len(data)

    def flush(self) -> None:
        pass


def _write_methods(base: type) -> list[str]:
    return sorted(name for name in dir(base)
                  if name.startswith("do_") and name[3:] not in READ_METHODS and name != "do_OPTIONS"
                  and callable(getattr(base, name)))


def make_guarded_handler(base: type, guard: ViewerGuard) -> type:
    module = sys.modules[base.__module__]

    class GuardedViewerHandler(base):
        timeout = guard.header_timeout
        principal = None
        viewer_identity = None
        _inflight_key = None
        viewer_path = ""
        viewer_kind = "app"
        server_version = "taskmaster"
        sys_version = ""
        _solo_frame_headers = False       # response_headers() below sends them

        def version_string(self):
            return self.server_version        # the stdlib joins in sys_version, leaving a trailing space

        def parse_request(self):
            return super().parse_request() and guard.admit(self)

        def handle_one_request(self):
            try:
                super().handle_one_request()
            finally:
                key, self._inflight_key = self._inflight_key, None
                if key is not None:
                    guard.inflight.release(key)

        def _cors_headers(self):
            return None

        def _solo_admit(self):
            return True          # the guard's admit() is this viewer's gate (login, Origin, CSRF token)

        def _index_head_extra(self):
            identity = self.viewer_identity
            return guard.auth.page_meta(identity) if identity is not None else ""

        def _md_editor_link(self, resolved):
            return ""            # a vscode:// link to a host path means nothing to a remote member, and would show the path

        def _static_rel(self, clean_path):
            # The exact value admit decoded and static_asset_allowed checked: no second decode (design 028 I2).
            self.viewer_kind = "static"
            if self.viewer_path == "/favicon.ico":
                return "vendor/favicon.ico"
            return self.viewer_path[len(STATIC_PREFIX):]

        def _serve_identity(self):
            self._send_json(200, {"version": module.VERSION})

        def _serve_repo_file(self, clean_path):
            rel = clean_path[len("/file/"):]
            checked = tracked_file_path(guard.root, rel, guard.tracked.get())
            self.viewer_kind = "file-raw"
            if checked is None:
                self.send_error(404)
                return
            self._checked_file = checked
            self.viewer_kind = "file-md" if Path(checked).suffix.lower() == ".md" else "file-raw"
            return super()._serve_repo_file(clean_path)

        def _read_repo_file(self, resolved):
            # Read the checked file through one handle that is verified to be it (006 review I5), never by name.
            checked = getattr(self, "_checked_file", None)
            if checked is None or Path(resolved) != checked:
                raise PermissionError("the file changed after it was checked")
            text = read_checked(guard.root, checked).decode("utf-8")
            return text.replace("\r\n", "\n").replace("\r", "\n")  # as read_text does

        def _send_json(self, status, payload, etag=None, *, timings=None):
            payload = repo_relative(payload, guard.root)
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                original = payload["error"]
                scrubbed = scrub(original, guard.root)
                if scrubbed.internal or status >= 500:
                    ref = new_ref()
                    text = internal_message("the viewer", ref)
                    LOG.info("scrubbed viewer error ref=%s original=%r", ref, original)
                else:
                    text = scrubbed.text
                if text != original:
                    payload = {**payload, "error": text}
            return super()._send_json(status, payload, etag, timings=timings)

        def end_headers(self):
            origin = guard.origin_for_host(self.headers.get("Host") if getattr(self, "headers", None) else None)
            for name, value in response_headers(self.viewer_kind, origin):
                self.send_header(name, value)
            super().end_headers()
            if self.command == "HEAD" and getattr(self, "_head_wfile", None) is not None:
                self.wfile = _DiscardBody()

        def do_HEAD(self):
            # HEAD is a read (design 028 section 1.1): the GET answer's status and headers, Content-Length included,
            # and no body. The base handler has no do_HEAD, so this answered 501 before (gate M4).
            self._head_wfile = self.wfile
            try:
                self.do_GET()
            finally:
                self.wfile, self._head_wfile = self._head_wfile, None

        def do_OPTIONS(self):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def wrap(name):
        def guarded(self):
            # admit has passed the login, Origin and CSRF checks; the context manager resets on return or raise.
            with auth.viewer_principal(self.principal.key_id):
                return getattr(super(GuardedViewerHandler, self), name)()
        guarded.__name__ = name
        return guarded

    def wrap_read(name):
        def guarded(self):
            # team-server-003 Q5: a logged-in read runs as the person's viewer session, so "mine" and the
            # board's ETag are that person's. The login page and other pre-login reads have no principal.
            if self.principal is None:
                return getattr(super(GuardedViewerHandler, self), name)()
            with auth.viewer_principal(self.principal.key_id):
                return getattr(super(GuardedViewerHandler, self), name)()
        guarded.__name__ = name
        return guarded

    for name in _write_methods(base):
        setattr(GuardedViewerHandler, name, wrap(name))
    for name in sorted(f"do_{method}" for method in READ_METHODS):
        if callable(getattr(base, name, None)):
            setattr(GuardedViewerHandler, name, wrap_read(name))
    return GuardedViewerHandler


class GuardedViewerServer(ThreadingHTTPServer):
    # POSIX: SO_REUSEADDR lets a restart bind over the TIME_WAITs every closed viewer connection leaves, and still never
    # admits a second live listener. Windows: SO_REUSEADDR would let another process bind the port, so the bind stays
    # exclusive there (028 gate I1).
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(sys.modules[__name__].VIEWER_MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def start_viewer(bs, guard: ViewerGuard, port: int):
    try:
        server = GuardedViewerServer(("127.0.0.1", port), make_guarded_handler(bs.ViewerHandler, guard))
    except OSError as exc:
        raise ServeUnavailable(f"cannot bind the viewer on 127.0.0.1:{port}: {exc.strerror or exc}") from exc
    bound = server.server_address[1]
    guard.set_port(bound)
    thread = threading.Thread(target=server.serve_forever, name="taskmaster-serve-viewer", daemon=True)
    thread.start()
    bs.VIEWER_PORT = bound
    return server, thread, bound


def stop_viewer(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(5)

