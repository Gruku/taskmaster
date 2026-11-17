<!-- User intent: the executable, test-first plan for team-server-006, so the internet-facing team server refuses
     floods, slow clients, oversized bodies and forged browser writes, and never leaks host paths or SQL in an error,
     before any teammate is given a key. -->

# team-server-006: transport hardening (plan, 2026-10-31)

**Task.** `team-server-006` (server mode, addendum §11 task 6).
**Design.** `docs/specs/2026-10-31-team-server-006-hardening-design.md`. Its decisions D1 to D12
are binding here. The source is `docs/specs/2026-10-31-team-server-mode-addendum.md` §4, §5 and §13.
It builds on 001 (`docs/specs/2026-10-31-team-server-001-serve-design.md`).
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-006`, branch
`feature/team-server-006`, base `f691e7d`.
**Dependencies.**

- Rebase onto 001's final tip (at least `413aec6`, which adds `serve.app.log_safe`) before Step 0.
- Steps 4, 7 and 9 also need team-server-002 (`Principal.key_id`, `access_token.subject == key_id`,
  `serve.auth.is_host_local`, `serve.auth.viewer_principal`, and `serve.auth.request_key_id`
  installed by 002 as `ORIGIN_PROVIDER`; 002's design at `2163b57`; design §10). Rebase onto 002's tip before
  Step 4. If 002 isn't available yet, do Steps 0 to 3, 5, 6 and 8 first, then stop and report.
**Executor.** One engineer, test-first. For each step:

1. Write the step's tests.
2. Run them and watch them fail for the stated reason.
3. Implement.
4. Run the step's tests and the regression subset (§5).
5. Commit with the step's message.

Steps 0 to 9 are ordered by dependency; run them in order. Every decision is already made: if
something here seems wrong, stop and report it rather than redesigning.

**Done when:**

- An oversized body is refused (413) before any of it is read; a slow-header client is cut off, on
  the first request of a connection or a later one; a slow body gets 408; and a client trickling a
  body after an early 401 is disconnected.
- An unauthenticated request is answered 401 without its body being read, through the whole stack.
- A per-key rate limit and in-flight cap return 429 to one key and not to another; a failure budget
  throttles a peer (an IPv6 /64) that keeps getting refused, and never charges an authenticated
  request.
- A tool exception over HTTP returns `internal error … (ref <id>)`, and serve.log holds the
  traceback under the same id. `Error:` text, JSON `{"error": …}` results and coordinator errors
  are path-scrubbed and SQL-free.
- The server-mode viewer requires a token login that sets an `HttpOnly`, `SameSite=Strict` cookie,
  and refuses the bootstrap token from a public origin; refuses cross-origin writes and writes
  without a CSRF token; sends no `Access-Control-Allow-Origin` at all; serves only git-tracked
  files through `/file/`; and attributes its writes to the logged-in key.
- The solo viewer, solo stdio tools and the base `Coordinator` behave exactly as before.

**Out of scope:** the design's §1 table. **Not touched:** `taskmaster/coordinator/client.py`,
`identity_gate.py`, the store, and the projection code.

---

## 1. Files

Create:

| File | Header (`# User intent: …`, first line) |
|---|---|
| `taskmaster/serve/limits.py` | `bounded, thread-safe token buckets and in-flight counters, so one key or one misbehaving address cannot starve the team server.` |
| `taskmaster/serve/protocol.py` | `cut off clients that send headers too slowly, and pin uvicorn's connection limits, because uvicorn alone never times out a slow header.` |
| `taskmaster/serve/guard.py` | `the outermost request guard: a request id on every response, oversized and slow bodies refused, early answers close the connection, refused peers throttled, and per-key limits after auth, all before a body is read.` |
| `taskmaster/serve/sanitize.py` | `remote callers never see host paths, SQL or tracebacks in an error; the operator finds the detail in serve.log by its ref.` |
| `taskmaster/serve/viewer.py` | `the server-mode viewer: token login to an HttpOnly SameSite=Strict cookie (bootstrap token on the host only), Host, Origin and CSRF checks on writes, no CORS, only git-tracked files through /file/, and writes attributed to the logged-in key.` |
| `viewer/js/lib/csrf.js` | `// User intent: in server mode every viewer write carries the session's CSRF token and a lost session returns to /login; in solo mode this does nothing.` |
| `tests/test_serve_limits.py` | `token buckets trip per key, refill on the clock, never let one key spend another's tokens, and stay bounded.` |
| `tests/test_serve_protocol.py` | `a slow-header client is cut off, oversize headers are refused, normal keep-alive still works, and the hardening options cannot be overridden.` |
| `tests/test_serve_guard.py` | `bodies are capped and timed before they are read, refused peers are throttled, every response carries a request id, and no unauthenticated request reaches the body parser.` |
| `tests/test_serve_sanitize.py` | `tool and coordinator errors reach a remote caller without paths, SQL or tracebacks, with a ref the server log shares; solo callers see what they always saw.` |
| `tests/test_serve_viewer.py` | `the server-mode viewer logs in by token, refuses forged and cross-origin writes, sends no CORS, and keeps secrets out of /file/.` |
| `tests/test_viewer_serve_hooks.py` | `the viewer hooks server mode needs leave the solo viewer byte-for-byte as it was.` |
| `viewer/tests/unit/csrf.test.js` | `// User intent: the CSRF fetch wrapper adds its header only to same-origin writes, only when the page carries a token, and sends a lost session to /login.` |

Modify:

| File | Change |
|---|---|
| `pyproject.toml` | add `"h11>=0.16"` to `dependencies` (CVE-2025-43859; h11 becomes the only parser) (Step 2) |
| `taskmaster/serve/config.py` | the new defaults, constants, `ServeConfig` fields and validation (Step 0) |
| `taskmaster/serve/cli.py` | the new flags (Step 0) |
| `taskmaster/serve/app.py` | `RequirePrincipalMiddleware`'s log line gains `ref=<id>` (Step 4) |
| `taskmaster/serve/daemon.py` | `ServeCoordinator.public_error` (Step 5); composing the layers, the viewer lifetime, `TASKMASTER_SERVE`, saving and restoring `bs.VIEWER_PORT`, `viewer_url` in the ready line (Step 9). It never assigns `client.ORIGIN_PROVIDER` (002 owns it) |
| `taskmaster/coordinator/service.py:757-758` | `_post` answers `owner.public_error(exc)`; add `Coordinator.public_error` (Step 5) |
| `taskmaster/backlog_server.py` | five hooks, each a no-op in solo (Step 6): `_start_viewer_server` serve check (`:12770`), `backlog_open_viewer` (`:12813`), `ViewerHandler._cors_headers` replacing the six `Access-Control-Allow-Origin` lines (`:11714`, `:11744`, `:12042`, `:12099`, `:12636`, `:12652`), `ViewerHandler._index_head_extra` (index branch at `:11698`), `ViewerHandler._md_full_path` (`:12085`) |
| `viewer/js/main.js` | first statement: `installCsrf(document, window)` (Step 8) |
| `tests/serve_helpers.py` | `running_app(app, host=…, **uvicorn_options)`, `raw_request`, `TwoKeyAuthenticator` (Step 2; its principals gain `key_id` at Step 4) |
| `tests/test_serve_config.py`, `tests/test_serve_cli.py`, `tests/test_serve_app.py`, `tests/test_serve_coordinator.py`, `tests/test_serve_daemon.py` | extended (Steps 0, 4, 5, 9) |

---

## 2. API (exact)

### 2.1 `taskmaster/serve/config.py` (additions)

```python
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_HEADER_TIMEOUT = 10.0
DEFAULT_BODY_TIMEOUT = 30.0
DEFAULT_MAX_CONNECTIONS = 128
DEFAULT_RATE_PER_MINUTE = 240
DEFAULT_RATE_BURST = 120
DEFAULT_MAX_INFLIGHT = 32            # per key; must stay below max_connections (design D7)
DEFAULT_VIEWER_PORT = 7471           # the CLI default; ServeConfig's default is None (no viewer), so tests opt in
KEEP_ALIVE_SECONDS = 5
MAX_HEADER_BYTES = 16 * 1024
TRUSTED_PROXIES = ("127.0.0.1", "::1")
FAILURE_BUDGET = 20                  # refusals per peer key, refilled per minute (design D6)
FAILURE_PEERS = 4096
RETRY_AFTER_MAX = 3600
TRACKED_FILES_TTL = 30.0             # seconds the git-tracked set for /file/ is cached (design D10.7)
VIEWER_SESSION_IDLE_SECONDS = 8 * 3600
VIEWER_SESSION_MAX_SECONDS = 24 * 3600
VIEWER_MAX_SESSIONS = 256
VIEWER_LOGIN_MAX_BYTES = 4096
VIEWER_MAX_CONNECTIONS = 32

@dataclass(frozen=True)
class ServeConfig:                   # 001 fields unchanged, then:
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    header_timeout: float = DEFAULT_HEADER_TIMEOUT
    body_timeout: float = DEFAULT_BODY_TIMEOUT
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    rate_per_minute: int = DEFAULT_RATE_PER_MINUTE
    rate_burst: int = DEFAULT_RATE_BURST
    max_inflight: int = DEFAULT_MAX_INFLIGHT
    viewer_port: int | None = None
    viewer_origins: tuple[str, ...] = ()

VIEWER_ORIGIN_RE = re.compile(r"^(https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?$")
def allowed_viewer_origins(config, port: int) -> tuple[str, ...]
    # config.viewer_origins + (f"http://127.0.0.1:{port}", f"http://localhost:{port}"), deduplicated, order kept.
def validate(config) -> ServeConfig   # 001 checks, plus:
    # max_body_bytes 65536..67108864; header_timeout 1..120; body_timeout 1..600; max_connections 8..10000;
    # rate_per_minute and rate_burst 1..100000; max_inflight 1..10000 and < max_connections
    # ("--max-inflight 128 must be below --max-connections 128"); viewer_port None or 0..65535;
    # each viewer origin matches VIEWER_ORIGIN_RE and is https unless its host is localhost, 127.0.0.1 or [::1].
    # Each failure raises ServeRefused naming the option (for example "--viewer-origin http://tm.example.com
    # must be https").
```

### 2.2 `taskmaster/serve/limits.py`

```python
class KeyedLimiter:
    def __init__(self, capacity: int, per_second: float, *, max_keys: int | None = None, clock=time.monotonic)
    def take(self, key: str) -> float
        # Refill the key's bucket from the clock, then take one token. 0.0 = granted; otherwise the seconds
        # until a token is due (the bucket is unchanged); math.inf when per_second is 0.
    def blocked(self, key: str) -> float
        # Like take but takes nothing: 0.0 if a token is available, else seconds until one is.
    def __len__(self) -> int
    # A threading.Lock guards an O(1) section; it is never held across an await. With max_keys, inserting a
    # new key into a full table first evicts the least recently touched key (an OrderedDict, move_to_end on
    # every touch).

class InFlight:
    def __init__(self, max_per_key: int)
    def acquire(self, key: str) -> bool     # False (and no change) when the key already holds max_per_key
    def release(self, key: str) -> None     # the entry is deleted at 0, so the table holds only active keys
    def count(self, key: str) -> int
    # A threading.Lock guards each call; never held across an await.

def retry_after(wait: float) -> int
    # min(RETRY_AFTER_MAX, max(1, ceil(wait))); math.inf -> RETRY_AFTER_MAX (ceil(inf) would raise OverflowError).
def peer_key(addr: str) -> str
    # IPv4 -> itself; IPv4-mapped IPv6 -> the IPv4 address; other IPv6 -> str(IPv6Network(f"{addr}/64",
    # strict=False)), e.g. "2001:db8:1:2::/64"; unparseable (including "-") -> addr unchanged.

def principal_limiter(config) -> KeyedLimiter    # (config.rate_burst, config.rate_per_minute / 60)
def principal_inflight(config) -> InFlight       # (config.max_inflight)
def failure_budget(clock=time.monotonic) -> KeyedLimiter   # (FAILURE_BUDGET, FAILURE_BUDGET / 60, max_keys=FAILURE_PEERS)
```

### 2.3 `taskmaster/serve/protocol.py`

```python
class DeadlineH11Protocol(H11Protocol):
    header_timeout: float = DEFAULT_HEADER_TIMEOUT
    # connection_made: super(), then _arm(). _arm: self._header_timer = loop.call_later(header_timeout, _expire).
    # data_received: super(), then if conn.their_state is not h11.IDLE: cancel and clear the timer.
    # on_response_complete: super(), then if the transport is open, conn.their_state is h11.IDLE and no timer: _arm().
    # _expire: if their_state is still IDLE and the transport is open: LOG.info("closed a connection from %s that sent
    #   no complete request headers within %ss", client host, header_timeout); transport.close().
    # connection_lost: super(), then cancel the timer.

def deadline_protocol(header_timeout: float) -> type[DeadlineH11Protocol]
    # type("DeadlineH11Protocol", (DeadlineH11Protocol,), {"header_timeout": float(header_timeout)})

HARDENED_KEYS = frozenset({"http", "ws", "limit_concurrency", "h11_max_incomplete_event_size", "proxy_headers",
                           "forwarded_allow_ips", "timeout_keep_alive"})
def hardened_uvicorn_options(config) -> dict
    # {"http": deadline_protocol(config.header_timeout), "ws": "none", "limit_concurrency": config.max_connections,
    #  "h11_max_incomplete_event_size": MAX_HEADER_BYTES, "proxy_headers": True,
    #  "forwarded_allow_ips": list(TRUSTED_PROXIES), "timeout_keep_alive": KEEP_ALIVE_SECONDS}
```

### 2.4 `taskmaster/serve/guard.py`

```python
REQUEST_ID_KEY = "taskmaster.request_id"
ALWAYS_CHARGED = frozenset({401})
CHARGED_IF_UNAUTHENTICATED = frozenset({400, 408, 413, 421})   # design D6; 403 and 429 are never charged

def request_id(scope) -> str | None

class RequestGuard:
    def __init__(self, app, *, max_body_bytes: int, body_timeout: float, failures: KeyedLimiter,
                 authenticator)
    async def __call__(self, scope, receive, send)
    # lifespan: pass through untouched. Any other non-http type: close it without calling app (websocket.close
    #   or simply return for unknown types).
    # http:
    #   rid = secrets.token_hex(6); scope[REQUEST_ID_KEY] = rid. Inbound X-Request-Id is never read.
    #   peer = peer_key(scope["client"][0] if scope.get("client") else "-").
    #   body_complete = no content-length and no transfer-encoding, or content-length == 0 (design D3a).
    #   wrapped send: on http.response.start, append (b"x-request-id", rid) and record the status; if
    #     body_complete is still False, drop any connection header the app set and append (b"connection",
    #     b"close"). Once the guard has answered, drop any later app sends.
    #   1. failures.blocked(peer) > 0 -> 429 {"error": "too many refused requests"}, Retry-After =
    #      retry_after(wait). Log once per exhaustion (the request whose refusal emptied the bucket logs "peer %s
    #      exhausted its failure budget; refusing for %ss"), not per blocked request.
    #   2. content-length: not a decimal integer, or present together with transfer-encoding -> 400
    #      {"error": "bad request"}; over max_body_bytes -> 413 {"error": "request body too large",
    #      "limit": max_body_bytes}. Neither calls receive; both go out through the wrapped send, so both close.
    #   3. wrapped receive: while the body is incomplete, wait at most the remaining body_timeout (counted from the
    #      first receive call), and count bytes. Past the cap -> 413; on expiry -> 408 {"error": "request body
    #      timed out"}. Either answer is sent only if the response has not started, and the app then gets
    #      {"type": "http.disconnect"}. On a more_body=False message set body_complete = True; after it, receive
    #      is passed straight through, with no deadline.
    #   4. await app(scope, wrapped_receive, wrapped_send).
    #   5. After it: charge failures.take(peer) if status in ALWAYS_CHARGED, or status in CHARGED_IF_UNAUTHENTICATED
    #      and not self._authenticated(scope). _authenticated: the Authorization header is "Bearer <t>" and
    #      authenticator.authenticate(t) is not None (called only here, only for those statuses).
    #      Log every guard refusal as INFO "refused %s %s %s from %s ref=%s" (status, log_safe(method),
    #      log_safe(path), log_safe(peer), rid).
    # All JSON answers carry cache-control: no-store and content-type: application/json.

class PrincipalLimits:
    def __init__(self, app, *, limiter: KeyedLimiter, inflight: InFlight)
    async def __call__(self, scope, receive, send)
    # Non-http: pass through. http: key = scope["user"].access_token.subject (002: exactly the key_id).
    # A missing user, access_token or empty subject -> 500 {"error": "internal error"}, logged at ERROR; it is
    # never passed on unkeyed. handle = principal_from_access_token(...).handle, for logs only.
    # 1. inflight.acquire(key) is False -> 429 {"error": "too many concurrent requests"}, Retry-After 1, INFO
    #    "too many concurrent requests key=%s principal=%s ref=%s". No rate token is spent.
    # 2. wait = limiter.take(key); wait > 0 -> release, then 429 {"error": "rate limited"}, Retry-After =
    #    retry_after(wait), INFO "rate limited key=%s principal=%s ref=%s".
    # 3. try: await app(...) finally: inflight.release(key). An SSE GET therefore holds its slot until it ends.
    # receive is never called on refusal.
```

### 2.5 `taskmaster/serve/sanitize.py`

```python
SQL_MARKERS = ("sqlite", "constraint failed", "no such table", "no such column", "syntax error",
               "database is locked", "database disk image", "disk i/o error", "datatype mismatch",
               "readonly database", "unable to open database", "traceback (most recent call last)")
SQL_EXEMPT_TOOLS = frozenset({"backlog_query"})
LOG = logging.getLogger("taskmaster.serve")

@dataclass(frozen=True)
class Scrubbed:
    text: str
    changed: bool
    internal: bool

def new_ref() -> str                                   # secrets.token_hex(6)
def scrub(text: str, root: Path) -> Scrubbed
    # 1. Replace str(root), its resolved form and its "/"-separated forms with "<root>" (case-insensitively
    #    when os.name == "nt").
    # 2. Replace remaining absolute paths with "<path>":
    #    WINDOWS = r"(?<![\w])[A-Za-z]:[\\/][^\s'\"`,;)\]]*"
    #    POSIX   = r"(?<![\w:/.<])/(?:[^\s'\"`,;)\]/]+/)+[^\s'\"`,;)\]]*"
    # 3. internal = any(marker in text.lower() for marker in SQL_MARKERS), checked on the original text.
def internal_message(what: str, ref: str) -> str
    # f"internal error in {what} (ref {ref}); the operator can look it up in the server log"

class SanitizeErrors(fastmcp.server.middleware.Middleware):
    def __init__(self, root: Path)
    async def on_call_tool(self, context, call_next)
    # try get_http_request() except RuntimeError: return await call_next(context)   # stdio / in-memory: untouched
    # ref = request.scope.get(REQUEST_ID_KEY) or new_ref(); tool = context.message.name;
    # principal = current_principal() handle or "-".
    # On ToolError with __cause__ (or any other Exception): LOG.error("tool error ref=%s principal=%s tool=%s
    #   type=%s", ..., exc_info=cause or exc); raise ToolError("Error: " + internal_message(tool, ref)) from None.
    # On ToolError without __cause__: re-raise ToolError(scrub(str(exc), root).text) from None.
    # On a result, for each TextContent text (and a structured_content {"result": str}):
    #   a. Text starting "Error:": s = scrub(text, root). If s.internal and tool not in SQL_EXEMPT_TOOLS:
    #      text = "Error: " + internal_message(tool, ref); else text = s.text.
    #   b. Text whose lstrip() starts with "{" and json.loads to a dict with a str "error": the same rule on that
    #      field alone (internal -> internal_message(tool, ref), without the "Error: " prefix); other fields are
    #      left as they are; re-serialize with json.dumps(obj, ensure_ascii=False) only if it changed. A
    #      JSONDecodeError, a non-dict, or no str "error" -> untouched (a success result).
    #   If anything changed, LOG.info("scrubbed tool error ref=%s tool=%s original=%r", ref, tool, original).
    #   Covers the six JSON_ERRORS sites: registry.py:205, :212 (public_payload()), overview.py:309, :349,
    #   context.py:265, cursors.py:191.

DOMAIN_ERRORS: tuple[type, ...]   # contracts.Conflict, contracts.CancelledBeforeExecution, ValueError, KeyError,
                                  # HandshakeError, UnsupportedStoreError, CoordinatorStopping, IdentityRefused,
                                  # ServiceUnavailable (the client.py:144 map plus ServiceUnavailable)
def coordinator_public_error(exc, root, log) -> dict
    # type(exc).__name__ is the exact name of a DOMAIN_ERRORS type and not scrub(str(exc)).internal:
    #   {"error": scrub(str(exc), root).text, "type": type(exc).__name__}
    # else: ref = "c" + new_ref(); log.error("coordinator error ref=%s type=%s", ref, type(exc).__name__,
    #   exc_info=exc); {"error": internal_message("the coordinator", ref), "type": "ServiceUnavailable"}
```

### 2.6 `taskmaster/coordinator/service.py`

```python
class Coordinator:
    def public_error(self, exc) -> dict:
        return {'error': str(exc), 'type': type(exc).__name__}      # exactly today's answer
# _post: `except Exception as exc: self.answer(409, owner.public_error(exc))`
```

`ServeCoordinator.public_error` (in `daemon.py`) returns `coordinator_public_error(exc, self.root, LOG)`.

### 2.7 `taskmaster/backlog_server.py` hooks (each a no-op in solo)

```python
def _serve_mode() -> bool: return os.environ.get("TASKMASTER_SERVE") == "1"
VIEWER_PUBLIC_URL: str | None = None          # set by the daemon

def _start_viewer_server():                   # first statement:
    if _serve_mode():
        return VIEWER_PORT                    # the daemon owns the viewer (design D10.1)

def backlog_open_viewer():                    # first statement:
    if _serve_mode():
        return (f"The viewer is served at {VIEWER_PUBLIC_URL or f'http://127.0.0.1:{VIEWER_PORT}/'}; "
                "sign in with your key.")

class ViewerHandler:
    def _cors_headers(self) -> None: self.send_header("Access-Control-Allow-Origin", "*")
    def _index_head_extra(self) -> str: return ""
    def _md_full_path(self, resolved: Path) -> str: return str(resolved).replace("\\", "/")
# Each of the six `self.send_header("Access-Control-Allow-Origin", "*")` lines becomes `self._cors_headers()`.
# The index branch of do_GET: after the four asset replaces,
#   `extra = self._index_head_extra(); if extra: html = html.replace("</head>", extra + "</head>", 1)`.
# _serve_repo_file: `full_path = self._md_full_path(resolved)`.
```

### 2.8 `taskmaster/serve/viewer.py`

```python
COOKIE_SECURE = "__Host-tm_session"
COOKIE_PLAIN = "tm_session_{port}"             # port-suffixed: cookies aren't port-scoped (design D10.2)
OPERATOR_OFF_HOST = ("the operator bootstrap token works only on the host; "
                     "use a key from taskmaster serve add-user")     # 002 D7's text, verbatim
from taskmaster.serve import auth   # 002 (2163b57): auth.viewer_principal(key_id), auth.VIEWER_KEY_ID (tests only),
                                    # auth.request_key_id (installed by 002's daemon code as client.ORIGIN_PROVIDER)
# 006 defines no context variable, never calls VIEWER_KEY_ID.set, and assigns no module-level or process-global value
# (design D10.10).
# WRITE_METHODS: the non-GET/HEAD do_<METHOD> names the base ViewerHandler defines (POST, PUT, PATCH today), found by
# make_guarded_handler at class-build time.
CSRF_HEADER = "X-Taskmaster-CSRF"
CSRF_META = '<meta name="taskmaster-csrf" content="{token}">'
PUBLIC_PATHS = ("/login", "/favicon.ico")      # plus the prefix "/static/v3/"
SECURITY_HEADERS = (("X-Frame-Options", "DENY"), ("Content-Security-Policy", "frame-ancestors 'none'"),
                    ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"))  # was no-referrer: review C1

@dataclass
class ViewerSession:
    principal: Principal
    key: str                 # held in memory only; re-authenticated on every request (design D10.2)
    csrf: str
    created: float
    last_seen: float

class SessionStore:
    def __init__(self, *, clock=time.monotonic)
    def create(self, key, principal) -> tuple[str, ViewerSession]   # sid = token_urlsafe(32); stored by sha256(sid)
    def get(self, sid) -> ViewerSession | None
        # None when unknown, idle more than VIEWER_SESSION_IDLE_SECONDS or older than VIEWER_SESSION_MAX_SECONDS
        # (an expired entry is dropped); otherwise last_seen = now.
    def drop(self, sid) -> None
    # At most VIEWER_MAX_SESSIONS; creating one more drops the oldest by created. Thread-safe.

def handler_scope(handler) -> dict
    # {"type": "http", "client": handler.client_address (the raw TCP peer, never rewritten),
    #  "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in handler.headers.items()]}
    # The minimal ASGI-shaped scope 002's serve.auth.is_host_local reads (design D10.2).

class ViewerGuard:
    def __init__(self, *, authenticator, origins: tuple[str, ...], root: Path, max_body_bytes: int,
                 header_timeout: float, principals: KeyedLimiter, inflight: InFlight, failures: KeyedLimiter,
                 tracked: TrackedFiles, viewer_port: int, clock=time.monotonic)
    def admit(self, handler) -> bool
        # The design D10.6 order (OPTIONS -> 405 is step 4, before public paths and the session). Each refusal
        # writes a complete response through the handler, sets handler.close_connection = True, logs INFO
        # "viewer refused %s %s %s from %s" (status, log_safe(method), log_safe(path), log_safe(peer)), and
        # returns False. Charges (design D6): 401 always; 400/411/413/421 only when the request has no valid
        # session cookie (SessionStore.get, no re-authentication); never 403/429.
        # Session step: authenticate(session.key); if the principal's key_id == "operator", also
        # is_host_local(handler_scope(handler)), else 403 OPERATOR_OFF_HOST.
        # Limit step: inflight.acquire(key_id) (False -> 429 "too many concurrent requests", Retry-After 1), then
        # principals.take(key_id) (wait > 0 -> release, 429 "rate limited", Retry-After retry_after(wait)).
        # handler._inflight_key = key_id, released by the handle_one_request override.
        # On success it sets handler.principal and handler.viewer_session and returns True. It doesn't touch
        # the viewer context variable; the write-method wrappers below do, through auth.viewer_principal.
    def login(self, handler) -> None
        # GET and POST /login (design D10.2). POST: allowed Origin (else 403), authenticate (else 401, charged),
        # then key_id == "operator" and not is_host_local(handler_scope(handler)) -> 403 OPERATOR_OFF_HOST with
        # no session and no Set-Cookie, logged "viewer refused operator token from %s: not host-local", not charged.
        # Cookie name: COOKIE_SECURE on an https Origin, else COOKIE_PLAIN.format(port=viewer_port).
    def logout(self, handler) -> None         # POST /logout, after the CSRF check

class TrackedFiles:
    def __init__(self, root: Path, *, ttl: float = TRACKED_FILES_TTL, clock=time.monotonic, runner=run_bounded)
    def get(self) -> frozenset[str]
        # The "/"-separated paths from `git -C <root> ls-files -z --cached` (runner(..., timeout=10)), reloaded
        # after ttl. Not a work tree, a non-zero exit or a timeout -> frozenset(), with one WARNING per failure
        # streak "viewer /file/ disabled: cannot list git-tracked files under the root". Thread-safe.

def file_path_allowed(root: Path, rel: str, tracked: frozenset[str]) -> bool
    # True only if: rel has no "..", "\\", drive or leading "/"; (root / rel).resolve() is relative to
    # root.resolve(); rel is in tracked (exact, case-sensitive); and rel's parts don't start with
    # (".taskmaster", "local") or (".git",). An allow-list: anything untracked is refused (design D10.7).

def make_guarded_handler(base: type, guard: ViewerGuard) -> type
    # A subclass of base with:
    #   timeout = guard.header_timeout
    #   parse_request: ok = super().parse_request(); return ok and guard.admit(self)
    #   handle_one_request: try: super().handle_one_request()
    #     finally: release self._inflight_key if set (and clear it)
    #   each do_<METHOD> in WRITE_METHODS: def do_X(self): with auth.viewer_principal(self.principal.key_id):
    #     return super().do_X()     # admit has already passed login, Origin and CSRF; the context manager resets
    #                               # the variable on return or raise
    #   Any hand-off of write work to another thread uses contextvars.copy_context().run(fn, ...); the value is
    #   never set on the other thread by hand.
    #   _cors_headers: send nothing
    #   _index_head_extra: CSRF_META.format(token=html.escape(self.viewer_session.csrf))
    #   _md_full_path: the path relative to root, "/"-separated
    #   _serve_identity: {"version": VERSION} only
    #   _serve_repo_file: 404 unless file_path_allowed(root, rel, guard.tracked.get()), then super()
    #   _send_json: if payload["error"] is a str, scrub it (an SQL marker -> internal_message("the viewer", ref),
    #     logged with the original text)
    #   end_headers: send SECURITY_HEADERS, then super().end_headers()
    #   do_OPTIONS: unreachable in practice (admit answers 405 first); kept as 405 with Content-Length 0
    #   log_message: as base (silent)

class GuardedViewerServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    # process_request: a BoundedSemaphore(VIEWER_MAX_CONNECTIONS), acquired without blocking; if it is not
    # acquired, shutdown_request(request) and return. It is released in process_request_thread's finally.

def start_viewer(bs, guard: ViewerGuard, port: int) -> tuple[GuardedViewerServer, threading.Thread, int]
    # Binds ("127.0.0.1", port); an OSError -> ServeUnavailable(f"cannot bind the viewer on 127.0.0.1:{port}: ...").
    # Starts serve_forever on a daemon thread named "taskmaster-serve-viewer"; sets bs.VIEWER_PORT.
def stop_viewer(server, thread) -> None       # shutdown(), server_close(), join(5)
```

### 2.9 `taskmaster/serve/daemon.py` (changes)

```python
class ServeCoordinator(Coordinator):
    def public_error(self, exc): return coordinator_public_error(exc, self.root, LOG)

class ServeDaemon:   # same constructor; uvicorn_options must not contain HARDENED_KEYS (ValueError naming the key)
    # _start, in this order:
    #   001's validate, check_bind, root, logging, token, socket.
    #   Save os.environ.get("TASKMASTER_SERVE"); set it to "1".
    #   load_server_module (001); save bs.VIEWER_PORT; set bs.VIEWER_PUBLIC_URL = first viewer origin + "/" or None.
    #   principals = principal_limiter(config); inflight = principal_inflight(config); failures = failure_budget().
    #   If config.viewer_port is not None: start_viewer(bs, ViewerGuard(authenticator=authenticator,
    #     origins=allowed_viewer_origins(config, <port>), root=root, max_body_bytes=..., header_timeout=...,
    #     principals=principals, inflight=inflight, failures=failures, tracked=TrackedFiles(root),
    #     viewer_port=<port>), config.viewer_port). This happens before the coordinator starts, so a bind failure is
    #     exit 69 with nothing to undo there. For viewer_port 0, the origins and the cookie name use the real port
    #     after the bind: build the guard with a placeholder and set guard.origins and guard.viewer_port after bind.
    #   The coordinator starts (001), and 002 installs its ORIGIN_PROVIDER. 006 never touches ORIGIN_PROVIDER.
    #   self._sanitizer = SanitizeErrors(root); bs.mcp._inner.add_middleware(self._sanitizer).
    #   app = build_app(..., middleware=[Middleware(PrincipalLimits, limiter=principals, inflight=inflight),
    #                                    *self._middleware])
    #   for wrap in reversed(self._outer_middleware): app = wrap(app)
    #   app = RequestGuard(app, max_body_bytes=..., body_timeout=..., failures=failures, authenticator=authenticator)
    #   uvicorn.Config(app, **{001 options, **hardened_uvicorn_options(config), **self._uvicorn_options})
    # _announce: the ready line gains "viewer_url": f"http://127.0.0.1:{viewer_port}/" or null.
    # _teardown, in reverse, each step only if its start step ran: remove self._sanitizer from
    #   bs.mcp._inner.middleware if present; stop_viewer; restore bs.VIEWER_PORT to the saved value; restore TASKMASTER_SERVE (pop it
    #   if it was unset); set bs.VIEWER_PUBLIC_URL = None; then 001's teardown.
```

### 2.10 `taskmaster/serve/cli.py` (new flags)

```
--max-body-bytes N   --header-timeout S   --body-timeout S   --max-connections N
--rate-limit N (per minute)   --rate-burst N   --max-inflight N (per key)
--viewer-port N (default 7471)   --no-viewer   --viewer-origin ORIGIN (repeatable)
```

`--no-viewer` sets `viewer_port=None`. Passing both `--no-viewer` and `--viewer-port` is an argparse
error (a mutually exclusive group).

### 2.11 `viewer/js/lib/csrf.js`

```js
export function installCsrf(doc, win) {
  // meta = doc.querySelector('meta[name="taskmaster-csrf"]'); no meta -> return false (solo: nothing changes).
  // token = meta.content. original = win.fetch.bind(win).
  // win.fetch = async (input, init = {}) => {
  //   isReq = typeof win.Request === 'function' && input instanceof win.Request;
  //   method = (init.method || (isReq ? input.method : 'GET')).toUpperCase();
  //   url = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url, win.location.href);
  //   if (method !== 'GET' && method !== 'HEAD' && url.origin === win.location.origin) {
  //     headers = new Headers(init.headers !== undefined ? init.headers : (isReq ? input.headers : undefined));
  //     headers.set('X-Taskmaster-CSRF', token);       // keeps If-Match, Content-Type and the rest
  //     init = {...init, headers};
  //   }
  //   resp = await original(input, init);
  //   if (resp.status === 401 && url.origin === win.location.origin) win.location.assign('/login');
  //   return resp; };
  // return true.
}
```

---

## 3. TDD steps

### Step 0: settings and flags (`config.py`, `cli.py`)

Tests in `tests/test_serve_config.py`:

- `test_hardening_defaults`: `ServeConfig(root=p)` has `max_body_bytes == 2097152`,
  `header_timeout == 10.0`, `body_timeout == 30.0`, `max_connections == 128`,
  `rate_per_minute == 240`, `rate_burst == 120`, `max_inflight == 32`, `viewer_port is None` and
  `viewer_origins == ()`.
- `test_hardening_ranges` (parametrized): each value just outside its §2.1 range raises
  `ServeRefused` naming its flag. Each boundary value passes.
- `test_max_inflight_must_be_below_max_connections`: `max_inflight=128, max_connections=128` is
  refused naming both flags; `127, 128` passes.
- `test_viewer_origin_must_be_https_unless_loopback`:
  - `http://tm.example.com` is refused.
  - `https://viewer.tm.example.com`, `http://localhost:9000`, `http://127.0.0.1:7471` and
    `http://[::1]:7471` pass.
  - `https://x.com/path` and `ftp://x` are refused.
- `test_allowed_viewer_origins_adds_loopback`: with origins `("https://v.example",)` and port 7471:
  `("https://v.example", "http://127.0.0.1:7471", "http://localhost:7471")`.

Tests in `tests/test_serve_cli.py`:

- `test_hardening_flags_reach_the_config`: monkeypatch `ServeDaemon` to capture its config.
  `serve --root R --max-body-bytes 65536 --header-timeout 2 --body-timeout 3 --max-connections 9
  --rate-limit 5 --rate-burst 6 --max-inflight 4 --viewer-port 0 --viewer-origin https://v.example`
  produces those values.
- `test_viewer_port_defaults_to_7471_on_the_cli` and `test_no_viewer_sets_none`.
- `test_no_viewer_and_viewer_port_conflict`: exit 2 from argparse.

Fails first with `TypeError` (an unknown `ServeConfig` field) and `SystemExit` (an unknown flag).
Commit: `feat(serve): hardening settings and flags (team-server-006 Step 0)`.

### Step 1: token buckets (`limits.py`)

Tests in `tests/test_serve_limits.py`, with a fake clock (`now = [0.0]; clock=lambda: now[0]`):

- `test_bucket_trips_after_capacity`: `KeyedLimiter(3, 1.0)`. Three `take("a")` return 0.0; the
  fourth returns a value in (0, 1].
- `test_other_key_is_unaffected`: after `"a"` is drained, `take("b") == 0.0`.
- `test_refill_follows_the_clock`: drained `"a"`; advance 1.0; one `take` succeeds and the next
  doesn't.
- `test_blocked_takes_nothing`: `blocked` on a fresh key returns 0.0 ten times, and `take` still
  grants capacity tokens afterwards.
- `test_table_is_bounded`: `max_keys=4`; take for 10 keys; `len == 4`. The first key comes back
  full.
- `test_thread_safety`: 8 threads × 1000 `take("k")` on `KeyedLimiter(100, 0.0)`. Exactly 100
  grants.
- `test_factories`: `principal_limiter(ServeConfig(root=p, rate_per_minute=60, rate_burst=2))`
  grants 2, then refills 1 per second. `failure_budget()` grants 20.
  `principal_inflight(ServeConfig(root=p, max_inflight=3))` admits 3.
- `test_zero_refill_returns_inf_and_retry_after_clamps`: `KeyedLimiter(1, 0.0)` drained returns
  `math.inf`; `retry_after(math.inf) == 3600`, `retry_after(0.2) == 1`, `retry_after(2.1) == 3`,
  `retry_after(10**9) == 3600`. Nothing raises `OverflowError`.
- `test_inflight_caps_per_key_and_releases`: `InFlight(2)`. Two `acquire("a")` are True, the
  third is False and leaves `count("a") == 2`; `acquire("b")` is True. After one `release("a")`,
  `acquire("a")` is True again. Releasing to 0 removes the entry (the table's length is the number
  of active keys).
- `test_inflight_thread_safety`: 8 threads each acquire and release `"k"` 1000 times on
  `InFlight(4)`, asserting inside the critical path (a recording subclass) that the count never
  exceeds 4; it ends at 0.
- `test_peer_key` (parametrized): `"203.0.113.9"` is itself; `"2001:db8:1:2:aaaa::1"` and
  `"2001:db8:1:2:ffff::9"` both give `"2001:db8:1:2::/64"`; `"2001:db8:1:3::1"` gives a different
  key; `"::ffff:203.0.113.9"` gives `"203.0.113.9"`; `"-"` and `"not-an-ip"` are unchanged.

Fails first with `ImportError`. Commit: `feat(serve): bounded thread-safe token buckets, in-flight
counters and peer keys (Step 1)`.

### Step 2: connection deadline and pinned uvicorn options (`protocol.py`, helpers)

`tests/serve_helpers.py` changes:

- `running_app(app, host="127.0.0.1", **uvicorn_options)` passes the options into
  `uvicorn.Config`.
- `raw_request(url, data: bytes, *, read_timeout=5) -> tuple[bytes, float]`: open a socket, send
  `data`, read until close or timeout, and return what was read plus the seconds elapsed.
- `TwoKeyAuthenticator(keys: dict[str, Principal])`: an `Authenticator` for tests.

Tests in `tests/test_serve_protocol.py`, with
`running_app(asgi_ok, **hardened_uvicorn_options(ServeConfig(root=p, header_timeout=1.0)))`:

- **`test_slow_header_client_is_cut_off`.**
  - Send `GET / HTTP/1.1\r\nHost: x\r\n`, then one `X-A: b\r\n` line every 0.4 s.
  - The server closes the connection, seen as EOF or reset on `recv`, within 2.5 s of connecting.
  - A control run with plain `running_app(asgi_ok, http="h11", timeout_keep_alive=1)` is still open
    at 3 s (the design D2 premise).
- `test_complete_requests_on_one_connection_still_work`: two pipelined-in-sequence GETs on one
  socket, the second sent 0.5 s after the first response, both answered 200.
- **`test_slow_headers_on_a_second_request_are_cut_off`.** One complete GET, answered 200. Then on
  the same socket, within 0.2 s, send `GET /b HTTP/1.1\r\n` and one `X-A: b\r\n` line every 0.4 s.
  The server closes the connection within `header_timeout + 1.5` s of the first response (the
  re-armed deadline, not the 5 s keep-alive; the trickle keeps cancelling keep-alive).
- `test_idle_keep_alive_after_a_response_is_closed`: after one response, send nothing. The socket
  closes within `KEEP_ALIVE_SECONDS + 1.5`.
- `test_oversize_headers_are_refused`: one 20 KiB header value gets status 400 and a close.
- `test_websocket_upgrade_is_not_honoured`: a GET with `Upgrade: websocket` and `Connection:
  Upgrade` reaches the ASGI app as an `http` scope (the app records `scope["type"]`), not
  `websocket`.
- `test_forwarded_trust_is_pinned`: with the env var `FORWARDED_ALLOW_IPS=*` set by monkeypatch,
  `hardened_uvicorn_options(...)["forwarded_allow_ips"] == ["127.0.0.1", "::1"]`. An app recording
  `scope["client"]` sees the `X-Forwarded-For` value from a loopback peer (`203.0.113.9`).
- `test_hardened_keys_cannot_be_overridden`: `ServeDaemon(cfg, uvicorn_options={"ws": "auto"})`
  raises `ValueError` naming `ws` (constructor check only; nothing is started).
- `test_h11_is_pinned` (in `tests/test_packaging.py`): `pyproject.toml`'s dependencies include a
  requirement on `h11` whose specifier admits `0.16.0` and rejects `0.15.0`.

Add `"h11>=0.16"` to `pyproject.toml`. Fails first with `ImportError`. Commit: `feat(serve): header
deadline and pinned uvicorn connection limits; pin h11 (Step 2)`.

### Step 3: the outer request guard (`guard.py`: `RequestGuard`)

Tests in `tests/test_serve_guard.py`. ASGI-direct tests call the guard with a recording `receive`;
socket tests use `running_app(RequestGuard(inner, …), **hardened_uvicorn_options(cfg))`. The guard's
`authenticator` is a `TwoKeyAuthenticator({"ka": alice})`.

- **`test_oversized_body_is_refused_before_it_is_read`.**
  - ASGI-direct: the scope has `content-length: 3000000`, and `receive` raises `AssertionError`;
    `inner` must not be called. The captured sends start with 413 and carry
    `{"error": "request body too large", "limit": 2097152}`.
  - Socket: send only the headers of a POST with `Content-Length: 3000000`, never the body. A 413
    response arrives within 1 s.
- `test_streamed_body_over_the_cap_is_refused`: a chunked POST of 3 × 1 MiB chunks to an inner app
  that reads the whole body gets 413. The inner app saw `http.disconnect`, not the third chunk.
- `test_slow_body_gets_408`: `body_timeout=1.0`. Send headers with `Content-Length: 10` and
  5 bytes, then nothing. A 408 with `connection: close` arrives in 1.0 to 2.0 s.
- `test_sse_wait_is_not_timed`: `body_timeout=0.5`. An inner app reads its (empty) body, then
  awaits `receive()` for a disconnect while streaming one chunk every 0.3 s for 2 s. All chunks
  arrive, and there's no 408.
- `test_bad_lengths_are_400`: `Content-Length: abc`, and `Content-Length` with
  `Transfer-Encoding: chunked`, each get 400 without calling `inner`.
- **`test_early_answer_then_trickled_body_is_closed`** (design D3a; the review spike's case).
  - An inner app answers 401 at once without calling `receive`. Over a socket, send the headers of
    a POST with `Content-Length: 1000` and 10 body bytes, read the 401, then send one byte every
    1.5 s. The connection is closed (EOF or reset) within 3 s of the 401.
  - Same, but after the 401 send one byte and then nothing: closed within 3 s.
  - The 401 response carries `connection: close`, also when the inner app set
    `connection: keep-alive` itself.
  - Control: the same inner app without the guard (plain `running_app(inner, http="h11",
    timeout_keep_alive=1)`) is still open at 6 s, so the test proves the fix, not uvicorn.
- `test_complete_bodies_keep_alive`: a POST whose 10-byte body the inner app reads fully, then a
  401: the response has no `connection: close`, and a second request on the socket is answered.
  A GET with no body answered 401 at once also keeps the connection.
- `test_every_response_has_a_request_id`: 200, 404 and 413 responses each carry `x-request-id`
  matching `^[0-9a-f]{12}$`. Inbound `X-Request-Id: attacker` isn't echoed. The inner app sees the
  same id at `scope["taskmaster.request_id"]`.
- **`test_failure_budget_throttles_a_refused_peer`.**
  - An inner app always answers 401. With a fake-clock `failure_budget()`, 20 requests get 401 and
    the 21st gets 429 with `Retry-After`. The inner app was called exactly 20 times.
  - With scope `client` set to another peer, the next request gets 401, not 429.
  - Advance the clock 3 s: one more 401 gets through.
- `test_failure_budget_ignores_successes`: 100 × 200 from one peer never yields 429.
- **`test_authenticated_refusals_are_not_charged`.** Inner apps answer 400, 408, 413 or 421
  (parametrized). 30 requests carrying `Authorization: Bearer ka` from one peer each get that
  status, never 429, and `failures.blocked(peer) == 0` afterwards. The same 30 with
  `Bearer wrong`, or with no header, reach 429 at the 21st. A guard-level 413 (declared
  `Content-Length: 3000000`) with `Bearer ka` isn't charged either. 401 is charged even with a
  `Bearer` header (it didn't authenticate, by definition).
- `test_unauthenticated_check_runs_only_on_charging_statuses`: a recording authenticator is never
  called for 200, 404 or 429 responses.
- **`test_ipv6_peers_share_a_64`.** Inner app always 401; scope `client` alternates over 25
  addresses inside `2001:db8:1:2::/64`. The 21st request gets 429. A request from
  `2001:db8:1:3::1` then gets 401, and one from `203.0.113.9` gets 401.
- `test_exhaustion_is_logged_once` (caplog): 30 requests past the budget produce exactly one
  `exhausted its failure budget` line.
- `test_logged_values_are_escaped` (caplog): a refused request whose path holds `%0d%0a` and whose
  peer (scope `client`) is `"1.2.3.4\nforged"` logs one line, with no raw newline, through
  `log_safe`.
- `test_lifespan_passes_through`: a lifespan scope reaches `inner` unchanged.

Fails first with `ImportError`. Commit: `feat(serve): outer request guard - body cap, body
deadline, close on early answers, failure budget, request ids (Step 3)`.

### Step 4: per-key limits and the whole stack (`guard.py`: `PrincipalLimits`, `app.py`)

Rebase onto 002's tip first (Dependencies, above). Tests in `tests/test_serve_app.py` (extended).
The principals are `alice = Principal("alice", "member", "a1a1a1a1a1a1a1a1")`,
`alice2 = Principal("alice", "member", "a2a2a2a2a2a2a2a2")` (a second key of the same person) and
`bob = Principal("bob", "member", "b0b0b0b0b0b0b0b0")`. The app is
`RequestGuard(build_app(toy_server(), auth := TwoKeyAuthenticator({"ka": alice, "ka2": alice2,
"kb": bob}), middleware=[Middleware(PrincipalLimits, limiter=KeyedLimiter(3, 0.0),
inflight=InFlight(2))]), max_body_bytes=…, body_timeout=…, failures=failure_budget(),
authenticator=auth)`, served through `running_app(..., **hardened_uvicorn_options(cfg))`. The
toy server has a `wait` route or tool that blocks on a `threading.Event` the test sets, to hold a
request in flight.

- **`test_unauthenticated_request_never_reaches_the_body_parser`.**
  - Socket: send the headers of `POST /mcp` with `Content-Length: 1000`, the MCP `Accept` header
    and no `Authorization`, and no body. A 401 arrives within 1 s, so the server answered without
    waiting for the body.
  - ASGI-direct, through the full `RequestGuard(build_app(...))` stack: a `receive` that raises
    `AssertionError` on any call. The answer is 401, and `receive` was never called.
- **`test_rate_limit_trips_per_principal`.**
  - Count raw `initialize` POSTs, one request each. A `fastmcp.Client` call spends several
    requests (initialize, notification, call), which makes its count fragile.
  - `raw_initialize(url, "ka")` three times gives 200. The fourth gives 429 with
    `Retry-After: 3600` (the clamp: the test limiter never refills, so the wait is `inf`) and
    `{"error": "rate limited"}`.
  - `raw_initialize(url, "kb")` then still gives 200.
- **`test_limits_are_keyed_by_key_id_not_handle_or_peer`.** All keys come from `127.0.0.1`. After
  `ka` is drained, `ka2` (same handle `alice`, other key id) still gives 200, and so does `kb`.
  Refused 429s aren't charged to the failure budget (bob is never 429 because of alice). The 429
  log line holds `key=a1a1a1a1a1a1a1a1` and `principal=alice`.
- **`test_inflight_cap_per_key`.** With a fresh `KeyedLimiter(100, 0.0)`: two concurrent `wait`
  requests with `ka` are held open; a third `ka` request gets 429
  `{"error": "too many concurrent requests"}` with `Retry-After: 1` at once, and a `kb` request
  gets 200. After the test sets the event, a new `ka` request gets 200. The refused third request
  spent no rate token (the limiter's bucket for `a1a1…` holds 100 − 3).
- `test_inflight_slot_held_by_a_stream_until_it_ends`: ASGI-direct, an inner app that streams
  (`more_body=True`) until a disconnect. While it runs, `inflight.count("a1a1a1a1a1a1a1a1") == 1`;
  after the disconnect, 0. An inner app that raises also releases its slot.
- `test_rate_limited_request_body_is_not_read`: ASGI-direct; the 4th request with a raising
  `receive` gets 429, and the response carries `connection: close` (its body was never read).
- `test_unkeyed_request_is_500_not_passed`: `PrincipalLimits` called with an `http` scope that has
  no `user`, and with one whose `access_token.subject` is `""`; inner isn't called and the answer
  is 500 each time.
- `test_refusal_log_carries_the_ref` (caplog): the 401 log line contains `ref=` and the response's
  `x-request-id` value.

Implement `PrincipalLimits`. In `RequirePrincipalMiddleware`'s log format, append `ref=%s`
(`scope.get("taskmaster.request_id", "-")`). Fails first with `ImportError`, and with the log
assertion. Commit: `feat(serve): per-key rate limit and in-flight cap after auth; full-stack
body-unread test (Step 4)`.

### Step 5: error sanitizing (`sanitize.py`, `service.py`, `ServeCoordinator.public_error`)

Tests in `tests/test_serve_sanitize.py`:

- `test_scrub_replaces_root_and_other_paths`, with root `tmp_path / "proj"`:
  - `f"Error: {root}\\.taskmaster\\x.yaml missing"` becomes `Error: <root>\.taskmaster\x.yaml missing`.
  - `/srv/other/secret.db` becomes `<path>`; `C:\Users\bob\repo` becomes `<path>`.
  - `see https://tm.example.com/mcp/x` is unchanged; `/mcp` alone is unchanged.
  - `internal` is False for all of these.
- `test_scrub_flags_sql_and_tracebacks`: `UNIQUE constraint failed: tasks.id`,
  `sqlite3.OperationalError: no such table: x` and `Traceback (most recent call last):` each give
  `internal=True`.
- **`test_tool_exception_over_http_is_sanitized_and_logged`** (caplog on `taskmaster.serve`):
  - A toy FastMCP with tool `boom` raises `sqlite3.OperationalError(f"no such table: x at {root}")`
    and has `SanitizeErrors(root)` added. It is served via `RequestGuard(build_app(...))`.
  - `call_tool(..., raise_on_error=False)` text equals
    `Error: internal error in boom (ref <id>); the operator can look it up in the server log`. The
    text contains no `no such table` and no `str(root)`.
  - One ERROR record contains `ref=<id>`, `tool=boom`, `principal=operator` and
    `type=OperationalError`, and has `exc_info`. `<id>` equals the HTTP response's `x-request-id`
    (captured with an outer recording wrapper).
- `test_error_text_result_is_scrubbed`: tool `nope` returns `f"Error: {root}/.taskmaster/a.yaml is
  locked"`. Over HTTP the text is `Error: <root>/.taskmaster/a.yaml is locked`, and an INFO
  `scrubbed tool error` line holds the original.
- `test_sql_error_text_becomes_internal`: tool `nope2` returns
  `"Error: UNIQUE constraint failed: tasks.id"`. Over HTTP it gets the internal message with a ref.
- `test_backlog_query_keeps_its_sql_errors`: a toy tool named `backlog_query` returning
  `"Error: no such column: foo"` comes back unchanged. Paths are still scrubbed.
- `test_deliberate_tool_error_passes_scrubbed`: `raise ToolError(f"bad id under {root}")` gives
  `bad id under <root>`.
- **`test_json_error_results_are_scrubbed`** (parametrized over toy tools, served over HTTP):
  - `json.dumps({"error": f"cannot read {root}/.taskmaster/a.yaml"})` comes back as JSON whose
    `error` is `cannot read <root>/.taskmaster/a.yaml`.
  - `json.dumps({"error": "no such table: secret", "seq": 3})` comes back with `error` equal to
    `internal_message(tool, ref)` (no `Error: ` prefix, no `secret`) and `seq == 3`; the ref is the
    response's `x-request-id`, and an INFO `scrubbed tool error` line holds the original.
  - A `public_payload()`-shaped object (`{"error": f"conflict at {root}/x", "type": "Conflict",
    "current": {...}}`) keeps `type` and `current` and scrubs `error`.
  - `json.dumps({"tasks": [], "note": f"{root}"})` (no `error` field), `"[1, 2]"`, and the text
    `"{not json"` come back unchanged.
  - `backlog_query` returning `json.dumps({"error": "no such column: foo"})` keeps its text.
- `test_real_json_error_sites_are_scrubbed`: for one real `JSON_ERRORS` tool (the
  `registry.py:205` branch), monkeypatch its handler to raise `ValueError(f"bad {root}/x")`, call it
  over HTTP through `RequestGuard(build_app(bs.mcp, …))` with `SanitizeErrors(root)` added, and
  parse the result: `error == "bad <root>/x"`.
- **`test_solo_in_memory_call_is_unchanged`:** the same `boom` server through
  `fastmcp.Client(mcp)` (in-memory) returns FastMCP's unmasked text, containing `no such table`. No
  sanitizer log line is written.
- `test_coordinator_public_error`:
  - `coordinator_public_error(ValueError(f"bad {root}/x"), root, log)` returns
    `{"error": "bad <root>/x", "type": "ValueError"}`.
  - `sqlite3.OperationalError("database is locked")` returns
    `{"type": "ServiceUnavailable", "error": "internal error in the coordinator (ref c…)…"}`, and the
    log gets ERROR with that ref and `exc_info`.
  - `ValueError("UNIQUE constraint failed: t.id")` is internal too.
- **`test_base_coordinator_answer_is_unchanged`:** `Coordinator.public_error(Coordinator.__new__(Coordinator),
  sqlite3.OperationalError("no such table: x"))` returns
  `{"error": "no such table: x", "type": "OperationalError"}`.
- `test_serve_coordinator_sanitizes_over_ipc` (in `tests/test_serve_coordinator.py`, reusing its
  in-process coordinator fixture):
  - Monkeypatch `ServeCoordinator.dispatch` for one method to raise
    `sqlite3.OperationalError("no such table: secret")`.
  - The `Client` call raises `ServiceUnavailable`, whose text contains `internal error in the
    coordinator (ref c` and not `secret`.

Fails first with `ImportError`, then with `AttributeError` (`public_error`). Commit:
`feat(serve): sanitize tool text, JSON and coordinator errors with a correlation ref (Step 5)`.

### Step 6: viewer hooks in `backlog_server.py`, solo unchanged

Tests in `tests/test_viewer_serve_hooks.py`, using the existing solo viewer fixtures (the
`running_server` pattern of `tests/test_server_api.py`):

- `test_solo_cors_header_unchanged`: `/api/identity`, `/api/viewer/prefs`, `/v3` and one
  `/static/v3/` asset each carry `Access-Control-Allow-Origin: *`. `OPTIONS /api/x` returns 204
  with `*`, `GET, PUT, POST, OPTIONS` and `Content-Type`.
- `test_solo_index_html_unchanged`: `GET /v3` body equals the pre-change rendering. Compute the
  expected value in the test from `viewer/index.html` with the four replaces. No `taskmaster-csrf`
  meta.
- `test_solo_md_full_path_unchanged`: `GET /file/<an .md under root>` embeds the absolute
  `/`-separated path.
- `test_serve_mode_start_viewer_is_a_noop`: monkeypatch `TASKMASTER_SERVE=1`,
  `bs._viewer_started = False`, and `bs.ThreadingHTTPServer` to a class whose `__init__` raises
  `AssertionError`. The function body subclasses that module global, so any bind attempt would
  raise. `bs._start_viewer_server()` returns `bs.VIEWER_PORT`, and `bs._viewer_started` is still
  False.
- `test_serve_mode_open_viewer_never_opens_a_browser`: `webbrowser.open` is monkeypatched to raise.
  With `TASKMASTER_SERVE=1` and `VIEWER_PUBLIC_URL="https://v.example/"`, `backlog_open_viewer()`
  returns `The viewer is served at https://v.example/; sign in with your key.`
- `test_solo_open_viewer_unchanged`: without the env var (and `webbrowser.open` recorded), it opens
  `http://127.0.0.1:<port>/` as before.

Plus the existing solo suites in the regression subset (§5), which must stay green unchanged:
`tests/test_server_api.py::test_api_endpoints_set_cors_header` asserts `*` today.

Fails first with `AttributeError` (`_cors_headers`) and with the serve-mode assertions. Commit:
`feat(viewer): server-mode hooks (CORS, head extra, md path, serve-mode start/open), solo unchanged (Step 6)`.

### Step 7: the guarded viewer (`viewer.py`)

Needs 002's tip (Dependencies). Tests in `tests/test_serve_viewer.py`:

- The fixture builds `ViewerGuard(authenticator=TwoKeyAuthenticator({"ka": alice, "kb": bob,
  "kop": Principal("operator", "operator", "operator")}), origins=allowed_viewer_origins(
  cfg(viewer_origins=("https://v.example",)), port), root=root, tracked=TrackedFiles(root), …)`.
- It runs `start_viewer(bs, guard, 0)` against the native test store fixture that
  `test_native_routing_viewer.py` uses, and stops it in teardown.
- The fixture makes `root` a git work tree: `git init`, then commit `docs/a.md` and
  `.gitignore` (holding `.taskmaster/local/`), through `run_bounded` with `GIT_AUTHOR_*` and
  `GIT_COMMITTER_*` set in its env. It then writes, untracked: `.mcp.json`, `notes.md`, `.env`,
  and `.taskmaster/local/serve/operator.token`.
- "Through Caddy" in a test means sending `X-Forwarded-For: 203.0.113.7` and `Host: v.example`,
  which is what Caddy sends.
- Requests go through `http.client` with explicit `Host`, `Origin`, `Cookie` and CSRF headers.
- `login(host)` posts `token=ka` with an allowed Origin and returns `(cookie, csrf)`. The CSRF value
  is parsed from the `taskmaster-csrf` meta of `GET /`.

The tests:

- **`test_login_sets_httponly_samesite_strict_cookie`.**
  - `POST /login` with `Origin: https://v.example`, `Host: v.example` and body `token=ka` returns
    303 to `/`.
  - Its `Set-Cookie` is `__Host-tm_session=…` with `HttpOnly`, `Secure`, `SameSite=Strict`,
    `Path=/` and `Max-Age=86400`, and no `Domain`.
  - The same login from `Origin: http://127.0.0.1:<port>` sets `tm_session_<port>` with `HttpOnly`
    and `SameSite=Strict`, and no `Secure`.
- **`test_operator_token_refused_from_a_public_origin`.**
  - `POST /login` with `token=kop` through Caddy (`Origin: https://v.example`) gives 403 with the
    text `the operator bootstrap token works only on the host; use a key from taskmaster serve
    add-user`, and no `Set-Cookie`. The failure budget isn't charged (`failures.blocked` stays 0
    after 25 tries).
  - The check is 002's: monkeypatch `taskmaster.serve.auth.is_host_local` to a recorder that
    returns False, and the loopback login with `kop` is refused too; the recorder saw a scope whose
    `client` is the TCP peer and whose headers include `x-forwarded-for` when sent.
  - `kop` from `Origin: http://127.0.0.1:<port>`, `Host: 127.0.0.1:<port>`, no `X-Forwarded-For`
    logs in (303).
  - With that operator session's cookie, a `GET /api/backlog` carrying `X-Forwarded-For` gives
    403: the check runs on every request, not only at login.
  - A member key (`ka`) logs in through Caddy as before.
- `test_bad_key_is_refused_and_charged`: `token=nope` gives 401 with `That key was not accepted.`.
  Twenty bad logins from `X-Forwarded-For: 203.0.113.7`, then the 21st request, even a good one,
  gives 429. A request with `X-Forwarded-For: 203.0.113.8` still logs in. Twenty bad logins from
  addresses spread over `2001:db8:5:6::/64` exhaust one budget; `2001:db8:5:7::1` still logs in.
- `test_refusals_with_a_valid_session_are_not_charged`: with alice's valid cookie, 25 writes with
  `Content-Length: 3000000` (413) and 25 with `Transfer-Encoding: chunked` (411) from one
  `X-Forwarded-For` never yield 429, and the same peer can still log in. The same 25 without a
  cookie reach 429.
- `test_login_requires_an_allowed_origin`: `POST /login` with `Origin: https://evil.example` (and
  with no Origin) gives 403, and no `Set-Cookie`.
- `test_unauthenticated_requests`: `GET /` gives 303 to `/login`; `GET /api/backlog` gives 401
  JSON; `GET /static/v3/<a real asset>` gives 200 without a cookie; `GET /login` gives 200.
- **`test_cross_origin_write_is_refused`** (parametrized over `PUT`, `POST`, `PATCH`, `DELETE`
  and the unknown method `FROB`): with a valid cookie and CSRF token, the method on
  `/api/viewer/prefs` with `Origin: https://evil.example` gives 403 `cross-origin write refused`.
  A missing Origin gives 403. `Sec-Fetch-Site: cross-site` with an allowed Origin gives 403. The
  prefs are unchanged (a GET confirms).
- **`test_write_without_csrf_token_is_refused`** (the same five methods): a valid cookie and
  allowed Origin with no `X-Taskmaster-CSRF` gives 403 `missing or invalid CSRF token`. A wrong
  token, or bob's token on alice's cookie, gives 403. The prefs are unchanged. With the right
  token, `DELETE` and `FROB` reach the base handler and get its 501, which shows the guard runs
  before method dispatch.
- `test_valid_write_passes_through`: a valid cookie, allowed Origin and right token on
  `PUT /api/viewer/prefs` give 200, and a GET shows the change. `handler.principal` was alice: the
  test monkeypatches the base `do_PUT` to record `self.principal.handle` and then call the original.
- **`test_viewer_writes_are_credited_per_user_concurrently`** (design D10.10).
  - Monkeypatch `taskmaster.coordinator.client.Client.execute` with a wrapper that records
    `(threading.get_ident(), auth.request_key_id())` (002's provider, the value the coordinator
    will get), sleeps 0.3 s (so the two calls overlap), and then calls the original.
  - From two threads at once, alice and bob each send one viewer write that goes through
    `Client.execute` (a task write, as `test_server_task_write.py` sends), each with their own
    cookie and CSRF token.
  - The recorder holds exactly one entry per write: alice's carries `a1a1a1a1a1a1a1a1`, bob's
    `b0b0b0b0b0b0b0b0`, never swapped and never `None`. With 002's real provider installed, the
    coordinator-side origin of each write is `{"principal": <that key id>}`.
  - After both responses, `auth.VIEWER_KEY_ID.get()` is `None` in the test thread. On alice's
    kept-alive connection, a second request that `admit` refuses (no CSRF) never reaches
    `Client.execute`, and a read (the base `do_GET` monkeypatched to record
    `auth.VIEWER_KEY_ID.get()`) after her write records `None`: the write's value was reset.
  - The wrapper goes through 002: monkeypatch `auth.viewer_principal` with a recording context
    manager that delegates to the original; it is entered once per write with that user's key id.
  - Cross-thread hand-off: a write path monkeypatched to do its `Client.execute` on a new thread
    through `contextvars.copy_context().run` is still credited to its user; the same hand-off
    without `copy_context` records `None` (fails closed).
  - `client.ORIGIN_PROVIDER` is the same object before and after the test (006 never assigns it).
- **`test_no_cors_header_in_server_mode`:** across `GET /login`, `GET /`, `GET /api/backlog`, a
  static asset, a refused write, a 429, and `OPTIONS /api/x` both without a cookie and with a
  valid one, no response carries any `Access-Control-*` header. Both `OPTIONS` requests give 405
  (OPTIONS is answered before the session check, design D10.6 step 4). Every response carries
  `X-Frame-Options: DENY` and the other `SECURITY_HEADERS`.
- `test_unknown_host_is_421`: `Host: evil.example` with a valid cookie gives 421.
- `test_oversized_and_chunked_bodies`: `Content-Length: 3000000` on a write gives 413 before any
  body is sent; `Transfer-Encoding: chunked` gives 411; and a login body over 4 KiB gives 413.
- `test_rotated_or_revoked_key_ends_the_session`: after login with `ka`, remove `ka` from the
  `TwoKeyAuthenticator`. The next `GET /api/backlog` gives 401 and `GET /` gives 303 to `/login`.
- `test_session_expiry`: a fake clock moved past 8 h idle gives 401. A session kept active is
  refused past 24 h anyway.
- `test_logout`: `POST /logout` with CSRF gives 303, and the cookie no longer works.
- **`test_file_route_serves_only_tracked_files`:** `GET /file/.mcp.json`, `/file/notes.md`
  (untracked), `/file/.env`, `/file/.taskmaster/local/serve/operator.token`, `/file/.git/config`,
  `/file/../<sibling>/x` and `/file/DOCS/A.MD` (case alias) each give 404. `/file/docs/a.md` gives
  200, and its HTML contains the relative path, not `str(root)`. After `git add notes.md` and the
  cache TTL (a fake clock), `/file/notes.md` gives 200.
- `test_file_route_fails_closed_without_git`: `TrackedFiles` with a runner that raises a timeout,
  and one with a root that isn't a work tree: every `/file/` request is 404, and one WARNING
  `viewer /file/ disabled` is logged per failure streak.
- `test_identity_hides_the_root`: `GET /api/identity` has `version` and no `root`.
- `test_viewer_error_text_is_scrubbed`: monkeypatch one native viewer write to raise
  `ValueError(f"cannot write {root}/x")`. The 409 JSON `error` contains `<root>/x`.
- `test_per_principal_limit_is_shared`: with `principals=KeyedLimiter(2, 0.0)`, alice's third
  viewer GET gives 429 with `Retry-After: 3600`, and bob's first gives 200. The limiter's key is
  alice's `key_id`, so an MCP request keyed `a1a1a1a1a1a1a1a1` against the same limiter is 429 too.
- `test_viewer_inflight_is_shared_and_released`: with `inflight=InFlight(1)` and the base
  `do_GET` monkeypatched to block on an event, alice's second concurrent GET gives 429
  `too many concurrent requests`; bob's gives 200; after the event, `inflight.count(alice.key_id)`
  is 0, also when the base handler raised.
- `test_connection_cap`: `VIEWER_MAX_CONNECTIONS` is monkeypatched to 2. Two idle open sockets plus
  a third: the third is closed at once, without a response.

Fails first with `ImportError`. Commit: `feat(serve): guarded viewer - token login cookie, Host,
Origin and CSRF checks, no CORS, tracked-only /file/, per-user write credit (Step 7)`.

### Step 8: viewer JS CSRF chokepoint (`viewer/js/lib/csrf.js`, `main.js`)

Tests in `viewer/tests/unit/csrf.test.js` (`node --test`). There's no jsdom: a fake `doc` with
`querySelector`, and a fake `win` with `fetch`, `location.href`, `location.origin` and a recording
`location.assign`.

- `no meta: installCsrf returns false and win.fetch is the same function`.
- `adds the header to same-origin POST, PUT, PATCH and DELETE`, including when `init.headers` is a
  `Headers` object or a plain object, without dropping existing headers such as `If-Match`.
- `does not add it to GET or HEAD, or to cross-origin URLs`.
- `a Request input keeps its own headers and method`: `fetch(new Request('/api/x', {method:
  'PUT', headers: {'If-Match': '"e1"', 'Content-Type': 'application/json'}}))` with no `init`
  reaches the original fetch with `If-Match`, `Content-Type` and `X-Taskmaster-CSRF` all present,
  and method `PUT`. With `init.headers` given, those win over the Request's (fetch's own rule).
  Node 18+ has `Request` and `Headers` built in, so the fake `win` passes them through.
- `a 401 on a same-origin request calls location.assign('/login')`; a 401 from another origin
  doesn't.

Implement, then add `import { installCsrf } from './lib/csrf.js'` and make `installCsrf(document,
window)` the first statement of `main.js`. Check with `grep` that no viewer module calls `fetch` at
import-evaluation time; the five write sites are all inside functions.

Run `npm run test:unit` from `viewer/`, the whole unit suite, so `api.test.js` still passes.
Commit: `feat(viewer): one CSRF fetch chokepoint, inert without the server-mode meta (Step 8)`.

### Step 9: compose it in the daemon (`daemon.py`) and prove it end to end

Needs 002's tip (Dependencies). Tests in `tests/test_serve_daemon.py` (extended). The `served`
fixture now passes `viewer_port=0` and `viewer_origins=("https://v.example",)`, and the existing
001 and 002 tests stay green.

- `test_daemon_applies_the_hardened_uvicorn_options`: `daemon._server.config` has `http`, a
  `DeadlineH11Protocol` subclass with `header_timeout == 10.0`, plus `ws == "none"`,
  `limit_concurrency == 128` and `forwarded_allow_ips == ["127.0.0.1", "::1"]`.
- `test_daemon_refuses_an_oversized_body_before_auth`: the 413 socket check (Step 3) against
  `served.url`, with no token.
- `test_daemon_rate_limits_the_operator`: a daemon with `rate_burst=3, rate_per_minute=1`. The 4th
  `raw_initialize` with the operator token gives 429.
- `test_daemon_inflight_cap`: a daemon with `max_inflight=2`. Two open MCP `GET /mcp` SSE streams
  with the operator token (after `initialize`) are held; a third `GET` gives 429
  `too many concurrent requests`. Closing one stream frees a slot within 2 s.
- `test_daemon_closes_after_an_early_401`: the Step 3 trickled-body check against `served.url`
  (POST with `Content-Length: 1000`, no token): closed within 3 s of the 401.
- **`test_tool_exception_is_sanitized_over_the_real_server`.**
  - Monkeypatch `taskmaster.native_routing.registry.route` to raise
    `sqlite3.OperationalError(f"no such table: x under {root}")` for `backlog_status`.
  - `call_tool(url, token, "backlog_status", raise_on_error=False)` gives the internal message with
    a ref.
  - `serve.log` contains that ref, `principal=operator` and `OperationalError`.
- `test_sanitizer_is_removed_on_close`: after `close()`, `bs.mcp._inner.middleware` holds no
  `SanitizeErrors`. A direct in-memory call of the same failing tool returns the unmasked text.
- `test_serve_env_is_set_and_restored`: `TASKMASTER_SERVE == "1"` while the daemon runs. After
  `close()` it is back to the value before (unset, and in a second case `"0"`).
- `test_viewer_port_is_restored`: with `bs.VIEWER_PORT` set to a sentinel before `start()`, it is
  the guarded viewer's port while running and the sentinel again after `close()`, also when
  `start()` failed after the viewer bound.
- `test_daemon_never_assigns_origin_provider`: while the daemon runs and during a viewer write,
  `client.ORIGIN_PROVIDER is auth.request_key_id`. A source check asserts that no 006 file
  (`serve/viewer.py`, `serve/guard.py`, `serve/limits.py`, `serve/sanitize.py`,
  `serve/protocol.py`) contains `ORIGIN_PROVIDER` or `VIEWER_KEY_ID.set`.
- **`test_daemon_viewer_requires_login_and_sends_no_cors`.**
  - The ready line's `viewer_url` is `http://127.0.0.1:<port>/`.
  - `GET /` gives 303 to `/login`; a full login with the operator token from the loopback origin
    (`Origin` and `Host` `127.0.0.1:<port>`, no `X-Forwarded-For`), then a `PUT
    /api/viewer/prefs` with the CSRF token, gives 200. The same login with `Origin:
    https://v.example`, `Host: v.example` and `X-Forwarded-For` gives 403.
  - No `Access-Control-*` header appears on any of these responses.
  - The solo test viewer (`bs._project_port()`, if the test process started one) still answers
    `/api/identity` with `Access-Control-Allow-Origin: *`, showing the guard is per server, not
    global.
- `test_viewer_port_taken_is_exit_69`: pre-bind a socket and pass its port as `viewer_port`.
  `start()` raises `ServeUnavailable`. The coordinator never started (its ownership lock is free),
  and `TASKMASTER_SERVE` is restored.
- `test_viewer_stops_on_close`: after `close()`, connecting to the viewer port is refused.
- `test_no_viewer`: with `viewer_port=None`, `viewer_url` is null and no viewer thread runs.

Tests in `tests/test_serve_cli.py` (extended). The existing end-to-end child test passes
`--viewer-port 0`, so parallel workers never collide on 7471.

- `test_end_to_end_child_has_no_import_time_viewer`: in the child started by the e2e test, after
  the ready line, `http://127.0.0.1:<child's bs._project_port()>/api/identity` (the deterministic
  solo port, computed in the test from the root with the same md5 rule) either refuses the
  connection or answers with a root that isn't this one. And `viewer_url` from the ready line
  serves `/login`.

Implement the §2.9 wiring. Fails first on the new assertions (no hardened options, a `*` header,
no `viewer_url`). Commit: `feat(serve): compose hardening layers, guarded viewer and sanitizer in
the daemon (Step 9)`.

---

## 4. Order of work inside a step

Write the tests first and confirm they fail for the stated reason, which each step names. Don't
edit sources while a run is in progress: the build fingerprint changes and real-coordinator tests
fail spuriously (about 13 of them).

Timing tests (Steps 2, 3, 7, 9) use generous upper bounds. They assert "cut off within N s" and
"still open at M s", never exact times. Fake clocks drive every bucket and session test.

## 5. Commands

Run from the worktree root with the repo venv's interpreter. Bare `python` on this machine is the
wrong interpreter. `<scratch>` is `D:\scratchbook\taskmaster\ts006\` (as `/d/scratchbook/taskmaster/ts006`
in Git Bash). Never put scratch or `--basetemp` on C:, which ran out of space while this plan was
written. Reuse one `<scratch>` subdirectory per step.

Step tests:

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_serve_config.py \
  tests/test_serve_limits.py tests/test_serve_protocol.py tests/test_serve_guard.py tests/test_serve_app.py \
  tests/test_serve_sanitize.py tests/test_serve_coordinator.py tests/test_viewer_serve_hooks.py \
  tests/test_serve_viewer.py tests/test_serve_daemon.py tests/test_serve_cli.py \
  -q -p no:cacheprovider --basetemp="<scratch>/srv006"
(cd viewer && npm run test:unit)
```

Regression subset (after Steps 5, 6, 7 and 9, and before reporting done). The solo viewer and the
base coordinator:

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_server_api.py \
  tests/test_native_routing_viewer.py tests/test_server_task_write.py tests/test_viewer_write_gates.py \
  tests/test_server_etag.py tests/test_api_notes.py tests/test_api_handover_status.py \
  tests/test_native_service.py tests/test_native_service_routing.py tests/test_native_service_identity.py \
  tests/test_packaging.py tests/test_server_instructions.py \
  -n 3 -q -p no:cacheprovider --basetemp="<scratch>/srv006-reg"
```

Leave the full suite to the review gate.

## 6. Risks (detail in design §11 and §12)

- **R1. uvicorn and FastMCP internals.** `DeadlineH11Protocol`, the `Connection: close` fix and
  `SanitizeErrors` depend on private behaviour. `test_slow_header_client_is_cut_off`,
  `test_early_answer_then_trickled_body_is_closed` and
  `test_tool_exception_over_http_is_sanitized_and_logged` fail first if a pinned-range upgrade
  changes it.
- **R2. Shared files.** These are `backlog_server.py` (five hooks), `service.py`
  (`public_error`) and `daemon.py` (also edited by 001 and 002). If 002, 003 or 005 lands first,
  rebase; Steps 5, 6 and 9 catch drift.
- **R6. 002's names** (`viewer_principal`, `request_key_id`, `VIEWER_KEY_ID`, `is_host_local`, all
  in `serve/auth.py`, per 002's design at `2163b57`). If 002's code drifts from its design, Steps 7
  and 9 fail first on an import.
- **R3. Port 7471 on the host.** It's only the CLI default; the unit passes `--viewer-port`. Tests
  always use 0.
- **R4. Timing flakiness on Windows CI.** The bounds are generous, and the socket tests close
  everything in `finally`.
- **R5. Heuristic scrubbing** (design R4). The 015 review should probe beyond the listed markers.

## 6a. Rollback

Revert the step commits, newest first. No schema, data or projection change. Viewer sessions are
in memory. After a rollback, the addendum §13 go-live order again forbids issuing teammate keys.

## 7. Left for later tasks (mention, don't build)

- `ORIGIN_PROVIDER`, `request_key_id`, `VIEWER_KEY_ID` and `viewer_principal` (002; 006 only
  wraps its write handlers in `auth.viewer_principal(key_id)`).
- The solo viewer's cross-origin reads (`*` CORS) and writes (no Origin check) (design G1). File it
  as one bug when this task closes.
- Host paths in success results; weighting the limit per tool; uvicorn's unbudgeted WARNING lines
  (design G2, G3, G7; 015 judges).
- Shipping the Caddyfile and unit with `--viewer-origin`, a live check that Caddy streams SSE with
  the §9 timeouts, and running Caddy on the host or with host networking, never on a container
  bridge (design §9; 016).
