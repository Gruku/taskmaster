# User intent: the viewer's authentication sits behind one small interface (ViewerAuth), so the cookie login used at go-live can be swapped for the 8.0 portal's signed principal by replacing one class, not by untangling the guard.
"""The viewer's authentication adapters (team-server-028 D12, section 3.6).

`ViewerGuard` (serve/viewer.py) asks a `ViewerAuth` who a request is and never touches a cookie or a CSRF token.
`CookieLogin` is the go-live implementation: token login to an HttpOnly SameSite=Strict cookie (bootstrap token on the
host only), a per-session CSRF token, sessions held in memory and re-authenticated on every request (006 D10)."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlparse

from . import auth
from .app import log_safe
from .auth import Principal
from .config import (LOGIN_ALARM_PER_MINUTE, VIEWER_LOGIN_MAX_BYTES, VIEWER_MAX_SESSIONS, VIEWER_MAX_SESSIONS_PER_KEY,
                     VIEWER_SESSION_IDLE_SECONDS, VIEWER_SESSION_MAX_SECONDS)
from .limits import InFlight, KeyedLimiter, failure_budget, retry_after

LOG = logging.getLogger("taskmaster.serve")
COOKIE_SECURE = "__Host-tm_session"
COOKIE_PLAIN = "tm_session_{port}"             # port-suffixed: cookies aren't port-scoped (design D10.2)
OPERATOR_OFF_HOST = ("the operator bootstrap token works only on the host; "
                     "use a key from taskmaster serve add-user")     # 002 D7's text, verbatim
CSRF_HEADER = "X-Taskmaster-CSRF"
CSRF_META = '<meta name="taskmaster-csrf" content="{token}">'
NOT_ACCEPTED = "That key was not accepted."
SIGN_IN, SIGN_OUT = "/login", "/logout"
ALARM_TEXT = "viewer: more than %d failed logins in the last minute"
CLEAR_SITE_DATA = '"cache"'
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}")      # secrets.token_urlsafe(32), as SessionStore.create issues


def handler_scope(handler) -> dict:
    """The minimal ASGI-shaped scope 002's auth.is_host_local reads; the client is the raw TCP peer."""
    return {"type": "http", "client": handler.client_address,
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1", "replace"))
                        for name, value in handler.headers.items()]}


def fetch_site_ok(handler) -> bool:
    """Sec-Fetch-Site must be absent (old browsers; the Origin check still applies) or same-origin (028 section 3.3)."""
    value = handler.headers.get("Sec-Fetch-Site")
    return value is None or value.strip().lower() == "same-origin"


def origin_fits_host(handler, origins: tuple[str, ...]) -> bool:
    """A write's Origin is an allowed origin and the one this request's Host names (028 gate M1): a loopback Origin
    can't write, sign in or sign out on the public Host, nor the reverse."""
    origin = handler.headers.get("Origin")
    host = (handler.headers.get("Host") or "").strip().lower()
    return origin in origins and host in origin_hosts(origin)


def origin_hosts(origin: str) -> set[str]:
    """The Host header values an allowed origin answers to (host, host:port, and the default-port forms)."""
    parsed = urlparse(origin)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    names = {f"{host}:{port}"} if port else {host}
    if port is None or port == _DEFAULT_PORTS.get(parsed.scheme):
        names |= {host, f"{host}:{_DEFAULT_PORTS.get(parsed.scheme)}"}
    return {name.lower() for name in names}


@dataclass(frozen=True)
class ViewerIdentity:
    principal: Principal        # the same type MCP authenticates to
    csrf: str | None            # the token writes must echo, or None when the adapter needs none
    ref: object                 # adapter-private (the cookie session id); the guard never reads it


class Reply:
    """The guard's own answers, handed to an adapter so every answer is logged, charged and given headers in one place."""

    def __init__(self, handler, peer: str, *, refuse: Callable, redirect: Callable, write: Callable,
                 read_body: Callable):
        self.handler, self.peer = handler, peer
        self._refuse, self._redirect, self._write, self._read_body = refuse, redirect, write, read_body

    def refuse(self, status: int, payload: dict | None, *, headers=(), html_body: bytes | None = None,
               log: bool = True, charge: bool = True) -> None:
        self._refuse(self.handler, status, payload, headers=headers, peer=self.peer, html_body=html_body, log=log,
                     charge=charge)

    def redirect(self, location: str, headers=()) -> None:
        self._redirect(self.handler, location, headers)

    def write(self, status: int, body: bytes, content_type: str, headers=()) -> None:
        self._write(self.handler, status, body, content_type, headers)

    def read_body(self, length: int) -> bytes | None:
        return self._read_body(self.handler, length, self.peer)


class ViewerAuth(Protocol):
    PUBLIC_PATHS: tuple[str, ...]          # exact paths the adapter answers before sign-in; feeds route_allowed

    def configure(self, origins: tuple[str, ...], viewer_port: int) -> None:
        """The guard's allowed origins and (after the bind) the viewer's real port."""

    def identify(self, handler) -> ViewerIdentity | None:
        """Who is this request? Re-validated on every request: expiry, revocation, rotation, origin binding."""

    def answer_public(self, handler, path: str, peer: str, reply: Reply) -> None:
        """Answer the adapter's own PUBLIC_PATHS (the login page, the login POST)."""

    def sign_in_location(self, handler) -> str:
        """Where an anonymous page request is sent (303), and what the page meta names."""

    def check_forgery(self, handler, identity: ViewerIdentity) -> str | None:
        """The adapter's anti-forgery check on a write, after the guard's Origin check. Refusal text, or None."""

    def page_meta(self, identity: ViewerIdentity) -> str:
        """The <meta> tags index.html gets."""

    def has_identity(self, handler) -> bool:
        """Cheap, no touch: decides whether a refusal is charged."""

    def presents_credential(self, handler) -> bool:
        """Shape only: does the request carry what this adapter issues (a well-formed session id)? A 401 for such a
        request is a session that ended, not a guess, and is not charged (028 gate M2)."""


@dataclass
class ViewerSession:
    principal: Principal
    key: str                 # held in memory only; re-authenticated on every request (design D10.2)
    csrf: str
    created: float
    last_seen: float
    origin: str = ""         # the allowed origin the login's Origin matched


class SessionStore:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, ViewerSession] = {}

    @staticmethod
    def _id(sid: str) -> str:
        return hashlib.sha256(sid.encode("utf-8", "replace")).hexdigest()

    def create(self, key: str, principal: Principal, origin: str = "") -> tuple[str, ViewerSession]:
        sid = secrets.token_urlsafe(32)
        now = self._clock()
        session = ViewerSession(principal, key, secrets.token_urlsafe(32), now, now, origin)
        with self._lock:
            mine = [k for k, s in self._sessions.items() if s.principal.key_id == principal.key_id]
            while len(mine) >= VIEWER_MAX_SESSIONS_PER_KEY:
                oldest = min(mine, key=lambda k: self._sessions[k].created)
                del self._sessions[oldest]
                mine.remove(oldest)
            while len(self._sessions) >= VIEWER_MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda k: self._sessions[k].created)
                del self._sessions[oldest]
            self._sessions[self._id(sid)] = session
        return sid, session

    def _live(self, ident: str, now: float) -> ViewerSession | None:
        session = self._sessions.get(ident)
        if session is None:
            return None
        if now - session.last_seen > VIEWER_SESSION_IDLE_SECONDS or now - session.created > VIEWER_SESSION_MAX_SECONDS:
            del self._sessions[ident]
            return None
        return session

    def get(self, sid: str | None, *, touch: bool = True) -> ViewerSession | None:
        if not sid:
            return None
        now = self._clock()
        with self._lock:
            session = self._live(self._id(sid), now)
            if session is not None and touch:
                session.last_seen = now
            return session

    def drop(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(self._id(sid), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


USED_LINK_PAGE = (b"<!DOCTYPE html><html><head><meta charset=utf-8><title>Taskmaster</title></head><body>"
                  b"<p>This sign-in link was already used or has expired. Ask Claude to open the viewer again.</p></body></html>")
LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - Taskmaster</title>
<link rel="icon" href="/favicon.ico">
<link rel="stylesheet" href="/static/v3/css/tokens.css">
<link rel="stylesheet" href="/static/v3/css/components/button.css">
<link rel="stylesheet" href="/static/v3/css/login.css"></head><body>
<form method="post" action="/login">
<h1>Taskmaster</h1>
{message}<label for="token">Your key</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<button class="btn btn--primary" type="submit">Sign in</button>
</form></body></html>
"""


class CookieLogin:
    """Token login to a session cookie (006 D10). The code that lived in ViewerGuard, moved whole."""

    PUBLIC_PATHS = (SIGN_IN, SIGN_OUT)

    def __init__(self, *, authenticator, principals: KeyedLimiter, inflight: InFlight, clock=time.monotonic):
        self._clock = clock
        self._alarm = KeyedLimiter(LOGIN_ALARM_PER_MINUTE, LOGIN_ALARM_PER_MINUTE / 60.0, clock=clock)
        self._last_alarm: float | None = None
        self.authenticator = authenticator
        self.principals = principals
        self.inflight = inflight
        self.sessions = SessionStore(clock=clock)
        self.origins: tuple[str, ...] = ()
        self.viewer_port = 0

    def configure(self, origins: tuple[str, ...], viewer_port: int) -> None:
        self.origins = tuple(origins)
        self.viewer_port = viewer_port

    # -- who is this ---------------------------------------------------------------------------------

    def _cookie_sids(self, handler) -> list[str]:
        sids = []
        for raw in handler.headers.get_all("Cookie") or []:
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except CookieError:
                continue
            for name in (COOKIE_SECURE, COOKIE_PLAIN.format(port=self.viewer_port)):
                if name in jar and jar[name].value:
                    sids.append(jar[name].value)
        return sids

    def _session(self, handler, *, touch=True):
        host = (handler.headers.get("Host") or "").strip().lower()
        for sid in self._cookie_sids(handler):
            session = self.sessions.get(sid, touch=False)
            # A session is bound to the origin it logged in on: a cookie lifted from another origin is no session.
            if session is None or host not in origin_hosts(session.origin):
                continue
            if touch:
                session = self.sessions.get(sid, touch=True)
            if session is not None:
                return sid, session
        return None, None

    def has_identity(self, handler) -> bool:
        return self._session(handler, touch=False)[1] is not None

    def presents_credential(self, handler) -> bool:
        # A restart or idle expiry signs everyone out; their polls then 401 until csrf.js leaves. Charging those would
        # let one office NAT lock itself out of /login (028 gate M2). Session ids are 256-bit: not guessable.
        return any(_SESSION_ID.fullmatch(sid) for sid in self._cookie_sids(handler))

    def identify(self, handler) -> ViewerIdentity | None:
        sid, session = self._session(handler)
        if session is None:
            return None
        principal = self._reauthenticate(sid, session)
        if principal is None:
            return None
        return ViewerIdentity(principal, session.csrf, sid)

    def _reauthenticate(self, sid: str, session: ViewerSession) -> Principal | None:
        """Rotation and revocation end the session on its next request (design D10.2)."""
        try:
            principal = self.authenticator.authenticate(session.key)
        except Exception:  # noqa: BLE001 - an authenticator failure is a refusal
            principal = None
        if principal is None or principal.key_id != session.principal.key_id:
            self.sessions.drop(sid)
            return None
        session.principal = principal
        return principal

    def sign_in_location(self, handler) -> str:
        return "/login"

    def check_forgery(self, handler, identity: ViewerIdentity) -> str | None:
        sent = handler.headers.get(CSRF_HEADER) or ""
        if not hmac.compare_digest(sent.encode("utf-8", "replace"), (identity.csrf or "").encode()):
            return "missing or invalid CSRF token"
        return None

    def page_meta(self, identity: ViewerIdentity) -> str:
        return (CSRF_META.format(token=html.escape(identity.csrf or "", quote=True))
                + f'<meta name="taskmaster-sign-in" content="{SIGN_IN}">'
                + f'<meta name="taskmaster-sign-out" content="{SIGN_OUT}">')

    # -- public paths ------------------------------------------------------------------------------

    def answer_public(self, handler, path: str, peer: str, reply: Reply) -> None:
        if path == SIGN_OUT:
            self._sign_out(handler, reply)
            return
        if path != SIGN_IN:
            reply.refuse(404, {"error": "not found"})
            return
        if handler.command in ("GET", "HEAD"):
            self._login_page(handler, reply)
        elif handler.command == "POST":
            self.login(handler, peer, reply)
        else:
            reply.refuse(405, None, headers=(("Allow", "GET, HEAD, POST"),))

    def _login_page(self, handler, reply: Reply, *, status=200, message="") -> None:
        if status == 200:
            _, session = self._session(handler, touch=False)
            if session is not None:
                reply.redirect("/")
                return
        text = f'<p class="msg" role="alert">{html.escape(message)}</p>\n' if message else ""
        reply.write(status, LOGIN_PAGE.format(message=text).encode("utf-8"), "text/html; charset=utf-8")

    def login(self, handler, peer: str, reply: Reply) -> None:
        origin = handler.headers.get("Origin")
        if not origin_fits_host(handler, self.origins) or not fetch_site_ok(handler):
            reply.refuse(403, {"error": "cross-origin login refused"})
            return
        declared = (handler.headers.get("Content-Length") or "").strip()
        if not (declared.isascii() and declared.isdigit()):
            reply.refuse(411, {"error": "length required"})
            return
        length = int(declared)
        if length > VIEWER_LOGIN_MAX_BYTES:
            reply.refuse(413, {"error": "request body too large", "limit": VIEWER_LOGIN_MAX_BYTES})
            return
        body = reply.read_body(length)
        if body is None:
            return
        try:
            key = self._key_from_body(body.decode("utf-8"), handler.headers.get("Content-Type") or "")
        except (UnicodeDecodeError, ValueError):
            reply.refuse(400, {"error": "bad request"})
            return
        principal = None
        if key:
            try:
                principal = self.authenticator.authenticate(key)
            except Exception:  # noqa: BLE001
                principal = None
        if principal is None:
            self._alarm_on_failure()
            page = LOGIN_PAGE.format(message=f'<p class="msg" role="alert">{NOT_ACCEPTED}</p>\n').encode("utf-8")
            reply.refuse(401, None, html_body=page)
            return
        if principal.key_id == auth.OPERATOR_KEY_ID and not auth.is_host_local(handler_scope(handler)):
            LOG.warning("viewer refused operator token from %s: not host-local", log_safe(peer))
            reply.write(403, json.dumps({"error": OPERATOR_OFF_HOST}).encode(), "application/json; charset=utf-8")
            return
        self._establish(key, principal, origin, peer, reply)

    def _establish(self, key: str, principal: Principal, origin: str, peer: str, reply: Reply, *, hop: bool = False) -> None:
        """A login (token form or, in the local profile, the one-time link) becomes a session cookie."""
        # A login spends the key's in-flight slot and rate budget like any request (006 review I4).
        if not self.inflight.acquire(principal.key_id):
            reply.refuse(429, {"error": "too many concurrent requests"}, headers=(("Retry-After", "1"),), log=False)
            return
        try:
            wait = self.principals.take(principal.key_id)
            if wait > 0:
                LOG.info("viewer: rate limited login key=%s principal=%s", log_safe(principal.key_id),
                         log_safe(principal.handle))
                reply.refuse(429, {"error": "rate limited"}, log=False,
                             headers=(("Retry-After", str(retry_after(wait))),))
                return
            sid, _ = self.sessions.create(key, principal, origin)
        finally:
            self.inflight.release(principal.key_id)
        if origin.startswith("https://"):
            cookie = f"{COOKIE_SECURE}={sid}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400"
        else:
            cookie = f"{COOKIE_PLAIN.format(port=self.viewer_port)}={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"
        LOG.info("viewer login key=%s principal=%s from %s", log_safe(principal.key_id), log_safe(principal.handle),
                 log_safe(peer))
        if hop:
            # A link opened from a file: page is a cross-site navigation, and a 303 to "/" would stay cross-site, so a
            # SameSite=Strict cookie would not be sent. A 200 page on this origin starts the next navigation itself.
            reply.write(200, HOP_PAGE, "text/html; charset=utf-8", (("Set-Cookie", cookie),))
            return
        reply.redirect("/", (("Set-Cookie", cookie),))

    @staticmethod
    def _key_from_body(raw: str, content_type: str) -> str | None:
        if content_type.split(";", 1)[0].strip().lower() == "application/json":
            try:
                value = json.loads(raw).get("token")
            except (ValueError, AttributeError):
                return None
            return value if isinstance(value, str) else None
        values = parse_qs(raw, max_num_fields=4).get("token") or []
        return values[0] if len(values) == 1 else None

    def _alarm_on_failure(self) -> None:
        """A process-wide count of failed logins (028 section 3.1): one WARNING a minute at most, never a refusal."""
        if self._alarm.take("*") == 0.0:
            return
        now = self._clock()
        if self._last_alarm is None or now - self._last_alarm >= 60:
            self._last_alarm = now
            LOG.warning(ALARM_TEXT, LOGIN_ALARM_PER_MINUTE)

    def _sign_out(self, handler, reply: Reply) -> None:
        """POST /logout needs only an allowed Origin and Sec-Fetch-Site: signing out is never refused for lack of a
        live session or a CSRF token (028 M1). The cookie's sessions are dropped; the answer is always the same."""
        if handler.command != "POST":
            if self.has_identity(handler):
                reply.redirect("/")
            else:
                reply.redirect(self.sign_in_location(handler))
            return
        if not origin_fits_host(handler, self.origins) or not fetch_site_ok(handler):
            reply.refuse(403, {"error": "cross-origin write refused"})
            return
        for sid in self._cookie_sids(handler):
            self.sessions.drop(sid)
        expired = (("Set-Cookie", f"{COOKIE_SECURE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"),
                   ("Set-Cookie", f"{COOKIE_PLAIN.format(port=self.viewer_port)}=; HttpOnly; SameSite=Strict; "
                                  "Path=/; Max-Age=0"),
                   # Origin-scoped on purpose: "cookies" would clear the whole registrable domain (028 I1).
                   ("Clear-Site-Data", CLEAR_SITE_DATA))
        reply.redirect(self.sign_in_location(handler), expired)


LINK_PATH = "/login/link"
HOP_PAGE = (b'<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            b'<meta http-equiv="refresh" content="0;url=/"><title>Signing in</title></head>'
            b'<body><p><a href="/">Continue to the backlog</a></p></body></html>')
LINK_TTL_SECONDS = 60.0
LINK_MAX_PENDING = 8


class LocalLinkLogin(CookieLogin):
    """The local profile's login (local-server-004): the cookie login plus a one-time, short-lived sign-in link that
    `backlog_open_viewer` mints for the operator, so a person on their own machine is never asked for a token.

    Only a digest of each link's secret is kept, a link works once (it is removed on first use, valid or not expired),
    and it is honoured only on a loopback connection whose Host is one of the viewer's own origins."""

    PUBLIC_PATHS = (*CookieLogin.PUBLIC_PATHS, LINK_PATH)

    def __init__(self, *, operator_key: Callable[[], str], ttl: float = LINK_TTL_SECONDS, **kwargs):
        super().__init__(**kwargs)
        self._operator_key = operator_key
        self._ttl = float(ttl)
        self._links: dict[str, float] = {}
        # Bound on wrong links only. The 256-bit secret cannot be guessed; the bound keeps a flood of bad links from
        # filling the log and costing CPU. It is this viewer's own and never touches the MCP app's budget, and a valid
        # link is honoured even when it is spent (so a hostile page cannot lock the person out of their own link).
        self._guesses = failure_budget(self._clock)
        self._links_lock = threading.Lock()

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8", "replace")).hexdigest()

    def mint_link(self) -> str:
        """A fresh link secret, valid for `ttl` seconds and one use; the oldest pending ones are dropped past the cap."""
        secret = secrets.token_urlsafe(32)
        now = self._clock()
        with self._links_lock:
            self._links = {d: until for d, until in self._links.items() if until > now}
            while len(self._links) >= LINK_MAX_PENDING:
                self._links.pop(next(iter(self._links)))
            self._links[self._digest(secret)] = now + self._ttl
        return secret

    def _take_link(self, secret: str) -> bool:
        with self._links_lock:
            until = self._links.pop(self._digest(secret), None)     # one use, whatever the outcome
        return until is not None and until > self._clock()

    def answer_public(self, handler, path: str, peer: str, reply: Reply) -> None:
        if path != LINK_PATH:
            super().answer_public(handler, path, peer, reply)
            return
        if handler.command != "GET":
            reply.refuse(405, None, headers=(("Allow", "GET"),))
            return
        secrets_sent = parse_qs(urlparse(handler.path).query, max_num_fields=4).get("t") or []
        if not auth.is_host_local(handler_scope(handler)):
            reply.refuse(403, {"error": "sign-in links work on this machine only"})
            return
        if len(secrets_sent) != 1 or not self._take_link(secrets_sent[0]):
            self._guesses.take(peer)
            if self._guesses.blocked(peer) > 0:
                reply.refuse(429, {"error": "too many invalid sign-in links"}, log=False, charge=False)
                return
            reply.refuse(401, None, html_body=USED_LINK_PAGE, charge=False)
            return
        host = (handler.headers.get("Host") or "").strip().lower()
        principal = None
        try:
            key = self._operator_key()
            principal = self.authenticator.authenticate(key)
        except Exception:  # noqa: BLE001 - an unreadable or rotated token signs no one in
            principal = None
        if principal is None or principal.key_id != auth.OPERATOR_KEY_ID:
            reply.refuse(401, {"error": "this sign-in link is not valid or has expired; ask for a new one"}, charge=False)
            return
        self._establish(key, principal, f"http://{host}", peer, reply, hop=True)
