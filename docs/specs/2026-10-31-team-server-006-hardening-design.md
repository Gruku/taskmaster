<!-- User intent: the task-level design for team-server-006. The team server goes on the internet behind Caddy,
     and no teammate gets a key until a flood, a slow client, a leaked error or a forged browser write can no
     longer hurt the host, its store or another teammate. -->

# team-server-006: transport hardening (design, 2026-10-31)

**Task.** `team-server-006`. Done when auth is checked before the body is read, a per-principal rate
limit and header and body timeouts apply, errors no longer echo `str(exc)` paths or SQL, and the
viewer has a token-to-cookie login (`HttpOnly`, `SameSite=Strict`), Origin and CSRF checks on
writes, and no `*` CORS.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §4 (transport security), §5
(viewer), §13 (deployment profile and go-live order).
**Builds on.** `docs/specs/2026-10-31-team-server-001-serve-design.md` (D1 to D10, and its review
note M3) and the `taskmaster/serve/` package at 001's tip `413aec6` (which adds `serve.app.log_safe`
and refuses websocket scopes); `docs/specs/2026-10-31-team-server-002-principals-design.md` for
`Principal.key_id`, `access_token.subject`, `serve.auth.is_host_local` and
`serve.auth.viewer_principal` (002 at `2163b57`; §10).
**Plan.** `docs/plans/2026-10-31-team-server-006-hardening.md`.
**Verified against.** uvicorn 0.51.0, h11 0.16.0, FastMCP 3.4.3, mcp 1.28.1 and Starlette 1.3.1 in
the repo venv. Two spikes (not committed) confirmed the claims marked *spike* below; the review's
two spikes (`D:\scratchbook\taskmaster\ts006-review\`) confirmed the claims marked *review spike*.

## 1. Scope

In scope:

- the connection layer: header deadline, header size, connection cap, no WebSocket upgrade, and
  pinned proxy trust (D2, D5);
- an outer ASGI guard: request ids, the body-size cap, the body deadline and the pre-auth failure
  budget per peer (D1, D3, D4, D6);
- a per-principal rate limit and in-flight cap after auth, shared by MCP and the viewer (D7);
- error sanitizing at three chokepoints, for `Error:` text and JSON `{"error": …}` results, with a
  correlation id (D9);
- the server-mode viewer: a daemon-owned loopback listener, token login with a session cookie
  (never with the bootstrap token from a public origin), Host, Origin and CSRF checks, no CORS, a
  git-tracked-only `/file/` route, and viewer writes attributed to the logged-in principal (D10);
- the settings and their CLI flags (D8);
- what Caddy does and what taskmaster does, with a sample Caddyfile (§9).

Out of scope:

| Not here | Owner |
|---|---|
| Per-person keys, `add-user`/`revoke-user` | 002 (planned in parallel; §10 lists what 006 assumes) |
| Per-connection `SESSION_ID`, `caller_scope` prefixes | 003 |
| Admin-method allowlist, `linear_bootstrap` hardening | 005 |
| `ORIGIN_PROVIDER` and `request_key_id`, `VIEWER_KEY_ID`, `viewer_principal`, `is_host_local`, `key_id`, and the coordinator side of attribution | 002; 006 only wraps viewer writes in `auth.viewer_principal(key_id)` (D10.10, ruling b) |
| Read endpoint for hooks and mods | 007 |
| Shipping the Caddyfile, systemd unit and how-to; a Linux lane | 016 |
| The security review of the exposed surface | 015 |
| Solo-mode viewer cross-origin reads and writes (see §11, G1) | a follow-up bug, not this task |

## 2. Threat model in one table

The host is internet-facing. Caddy terminates TLS and proxies to loopback (001 §10).

| Attacker | Wants to | Stopped by |
|---|---|---|
| Anyone on the internet, no key | exhaust memory with huge bodies | Caddy `max_size`; taskmaster body cap before any read (D3) |
| same | tie up connections with slow headers or bodies, or with a trickled body after an early refusal | Caddy `read_header`; taskmaster header deadline (D2), body deadline (D4) and `Connection: close` on early answers (D3a); connection cap |
| same | guess keys, flood 401s | 256-bit keys; peer failure budget, IPv6 keyed per /64 (D6) |
| A teammate's leaked key, or a runaway agent | flood tools, starve other teammates, hold every connection slot with SSE streams | per-principal rate limit and in-flight cap, keyed by key id (D7) |
| A copied operator bootstrap token | sign in to the viewer from the internet | `is_host_local` at viewer login and on every operator-session request (D10.2) |
| same | learn host paths, SQL or schema from errors | sanitizing (D9) |
| A web page the operator or a teammate visits | write to the viewer with their cookie (CSRF) | `SameSite=Strict`, Origin check, CSRF token, no CORS (D10) |
| same | read viewer data cross-origin, or rebind DNS | no CORS headers; Host check (D10) |
| A teammate logged in to the viewer | read `.taskmaster/local/serve/operator.token`, `.mcp.json` or any other untracked file through `/file/` | `/file/` serves only git-tracked files (D10.7) |
| A local user on the host | spoof `X-Forwarded-For` to dodge the failure budget | accepted (§11, G4); loopback is not trusted for auth (001 D5) |

## 3. D1: layer order

Outermost first. Every layer before the route refuses without calling `receive`, so no refused
request has its body read.

```
TCP accept (uvicorn, http=DeadlineH11Protocol, ws="none", limit_concurrency)
 1. connection   header deadline (D2) · header size 16 KiB · connection cap → 503
 2. proxy        uvicorn ProxyHeadersMiddleware, trusted = 127.0.0.1 and ::1 only (D5)
 3. RequestGuard request id · peer failure budget → 429 · Content-Length cap → 413 ·
                 wrapped receive: running byte cap → 413, body deadline → 408 ·
                 wrapped send: Connection: close on any answer before the body is complete (D3a, D4, D6)
 4. Starlette (001/002)  HostOriginGuard → 421 · bearer AuthenticationMiddleware (headers only) ·
                 RequirePrincipalMiddleware → 401, bootstrap token off-host → 403 (never calls receive)
 5. PrincipalLimits  in-flight cap → 429, then token bucket → 429, both keyed by access_token.subject (D7)
 6. route        FastMCP streamable HTTP; the MCP SDK reads the body here
 7. tools        SanitizeErrors FastMCP middleware (D9)
```

Why this order:

- **The size check comes first** because it reads only `Content-Length`, costs nothing, and holds
  for every path, authenticated or not. Putting it after auth would buy nothing: auth reads only
  headers too.
- **Timeouts sit at the connection and at `receive`**, the only places they can. An ASGI app
  never sees a request until its headers are complete, so a header deadline can't be ASGI
  middleware (D2). The body deadline wraps `receive`, so it applies wherever the body is read.
- **Auth runs before any body read** (001 D5, unchanged). 006 adds a test of the whole stack (layers
  1 to 6) that an unauthenticated request is answered before its body is even sent.
- **The principal limit comes after auth**, because before auth there's no principal. Before auth,
  the only key is the peer address, and that is what the failure budget uses (D6).
- **`RequestGuard` is the outermost ASGI layer taskmaster owns.** The daemon wraps the caller's
  `outer_middleware` (001's test seam) *inside* it, so nothing can be placed ahead of the size cap.
  It passes `lifespan` scopes straight through and refuses any other non-`http` scope.

## 4. The pre-auth layers

### D2. Header deadline: a uvicorn protocol subclass

- **uvicorn never cuts a slow-header client** (*spike*). On a fresh connection, `H11Protocol` arms
  no timer (`h11_impl.py:92`). Its keep-alive timer starts only after a response and is cancelled by
  the first byte of the next request (`data_received`, `:172`). A client that sent one header line
  per second was still connected after 10 s with `timeout_keep_alive=1`.
- **`DeadlineH11Protocol(H11Protocol)`** arms a `header_timeout` timer at `connection_made`, and
  again whenever a response completes and the connection returns to `h11.IDLE`. It cancels the
  timer once `conn.their_state` leaves `IDLE`, that is, once the request line and headers are
  parsed. On expiry it closes the transport. No response is sent: the client hasn't finished a
  request. The *spike* version closed the connection at its 1 s deadline.
- It is passed as `uvicorn.Config(http=DeadlineH11Protocol)`, so uvicorn uses h11 even where
  `httptools` happens to be installed. One parser on Windows and Linux. Because h11 becomes the
  only parser, `pyproject.toml` pins `h11>=0.16` (0.16.0 fixes CVE-2025-43859, a chunked-trailer
  request-smuggling bug).
- **It covers every request on a connection, not only the first.** After a response, uvicorn calls
  `conn.start_next_cycle()` and the state is `IDLE` again, so the timer re-arms. A partial request
  line or partial headers leave h11 in `IDLE` (it returns `NEED_DATA`), so trickled headers on the
  second request meet the same deadline as on the first. A test pins this.
- The deadline covers the time from connection open to headers complete. The keep-alive window
  (5 s, uvicorn's default, set explicitly) is shorter, so idle keep-alive connections still close
  on keep-alive first.
- **Headers are capped at 16 KiB** (`h11_max_incomplete_event_size=16384`, h11's default, pinned so
  a dependency change can't lift it). Over that, uvicorn answers 400 and closes.
- **`limit_concurrency=max_connections`**: past it, uvicorn answers 503 before the app runs.
  Long-lived SSE `GET` streams count against it, hence the default of 128 (D8).
- **`ws="none"`**: the venv has `websockets` installed, so uvicorn would otherwise accept an
  upgrade. With `none`, an upgrade request is handled as plain HTTP and meets auth like any other.

### D3. Body-size cap

- **Declared length.** `Content-Length` over `max_body_bytes` gets 413 at once. A malformed value,
  or `Content-Length` together with `Transfer-Encoding`, gets 400. Either way `receive` is never
  called.
- **Undeclared length** (chunked). It's allowed, because some HTTP clients send it and Caddy
  forwards it. The wrapped `receive` counts bytes and, past the cap, stops passing chunks on. If the
  response hasn't started, the guard answers 413. The app sees `http.disconnect`, and the
  connection closes.
- **The MCP SDK has no size limit of its own.** It calls `await request.body()` with no bound
  (`mcp/server/streamable_http.py:491`), which is why this cap exists.
- The default is 2 MiB: a native command payload may be 1 MiB (`native/contracts.py:9`), and the
  JSON-RPC envelope and string escaping add to that.

### D3a. An answer sent before the body is complete closes the connection

- **The hole** (*review spike*, uvicorn 0.51). When the app answers before reading the body (a 401
  from auth, a 413, a 429), uvicorn keeps the connection alive and waits to drain the unread body.
  Each body byte the client then trickles cancels the keep-alive timer in `data_received`, and
  nothing re-arms it, because h11 never returns to `IDLE`. `DeadlineH11Protocol` re-arms only at
  `IDLE`, so it doesn't help either. One byte every few seconds pins a `limit_concurrency` slot
  for as long as the client likes; one byte and then silence pins it forever.
- **The fix.** The guard tracks whether the request body is complete. It is complete from the start
  when the request has no `Content-Length` and no `Transfer-Encoding`, or `Content-Length: 0`;
  otherwise it becomes complete when the wrapped `receive` passes on a message with
  `more_body=False`. If `http.response.start` is sent while the body is still incomplete, the
  wrapped send adds `connection: close` (replacing any `connection` header the app set). h11 then
  marks the connection `MUST_CLOSE`, and uvicorn closes the transport once the response completes.
  The review spike's `close` mode closed at once.
- This covers every early answer from every layer inside the guard (auth, Host guard, limits,
  route), not just the guard's own. A normal request whose body was fully read keeps keep-alive.

### D4. Body deadline

- The wrapped `receive` gives the whole body `body_timeout` seconds, counted from the first
  `receive` call. Each wait is `asyncio.wait_for(…, remaining)`.
- **It applies only while the body is incomplete**, meaning until a message with `more_body=False`.
  After that, `receive` only waits for `http.disconnect`. That is how a long-lived SSE `GET` stream
  or a streamed response waits, and those waits are never timed.
- On expiry: if the response hasn't started, the guard answers 408 `{"error": "request body timed
  out"}` with `Connection: close`. The app sees `http.disconnect`. Any later `send` from the app is
  dropped.

### D5. Client address and `X-Forwarded-For`

- **uvicorn does the forwarding** (`ProxyHeadersMiddleware`). It rewrites `scope["client"]` from
  `X-Forwarded-For` only when the TCP peer is trusted, taking the right-most untrusted entry.
- **The trusted set is pinned to `["127.0.0.1", "::1"]`** and passed explicitly as
  `forwarded_allow_ips`. Without that, uvicorn reads `FORWARDED_ALLOW_IPS` from the environment
  (`config.py`), so a stray systemd `Environment=` could widen trust. It isn't a flag either:
  addendum §13 puts Caddy on the same host, and the deployment profile has no remote proxy.
- When Caddy proxies, the peer is `127.0.0.1`, and Caddy's own `X-Forwarded-For` holds the real
  client. Caddy discards an inbound `X-Forwarded-For` unless `trusted_proxies` is set (§9), so a
  client can't inject a value that survives.
- With `--insecure-bind` and no proxy, peers aren't loopback. Their `X-Forwarded-For` is ignored,
  and the TCP address is the key.
- `X-Forwarded-Proto` is rewritten the same way. Nothing on the MCP side decides from it.
- **Inbound `X-Request-Id` is ignored.** The id is always minted by the guard (D9).

### D6. Pre-auth limiter: a failure budget per peer, not a request rate

- **Key:** `peer_key(scope["client"][0])` after D5. An IPv4 address is its own key. An IPv6
  address is keyed on its /64 (`ipaddress.IPv6Network(f"{addr}/64", strict=False)`), because one
  host usually holds a whole /64 and could otherwise rotate through 2^64 fresh budgets. An
  IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is keyed as the IPv4 address. An unparseable value
  is used as is.
- **What it counts:** refusals of requests that don't carry a working key. The guard sees the
  status through its wrapped `send`.
  - 401 is always charged; by definition the request had no working key.
  - 400, 408, 413 and 421 are charged only when the request is unauthenticated: its
    `Authorization: Bearer` value doesn't authenticate with the daemon's `Authenticator`. The guard
    checks that only for a response with one of these statuses, so the cost falls on refusals,
    not on normal traffic, and the check is one dict lookup and one `compare_digest` (002 D2).
    A teammate whose agent sends a malformed or oversized body, or a slow upload, therefore never
    spends the budget that the rest of their office NAT shares.
  - 403 (bootstrap token off-host, 002 D7) and 429 aren't charged.
  - uvicorn's own refusals (400 for a malformed request line or oversize headers, 503 past
    `limit_concurrency`) never reach the guard and aren't charged.
- **Budget:** a token bucket of 20, refilling 20 per minute (`FAILURE_BUDGET`, a constant, not a
  flag). While a peer's bucket is empty, every request from that peer gets 429 with `Retry-After`,
  before the size check or auth.
- **Why failures and not every request:** several teammates behind one office NAT share an address.
  A request-rate limit per address would make their combined legitimate traffic trip it. Counting
  only refusals leaves them alone and still caps an unauthenticated flood at 20 per minute per
  address. Authenticated volume is the principal limiter's job (D7).
- **What the budget bounds in the log, and what it doesn't.** 001's `RequirePrincipalMiddleware`
  logs each 401, and at most 20 a minute per peer key get that far. The guard logs one line when a
  peer's budget runs out, and stays silent while it remains empty. The log as a whole is *not*
  bounded: uvicorn logs a WARNING for every malformed request (`Invalid HTTP request received.`)
  and every refusal past `limit_concurrency` before any taskmaster code runs, and those aren't
  budgeted. They go to uvicorn's `uvicorn.error` logger (stderr, so journald under the 016 unit,
  whose own rate limit applies), not to serve.log. 015 judges whether that needs more.
- **Every logged value an attacker controls** (peer, method, path, `X-Forwarded-For`) goes through
  001's `serve.app.log_safe`, which escapes control characters and truncates.
- **Memory is bounded:** at most 4096 peer entries. When the table is full, the entry idle longest
  goes first. A full bucket carries no state worth keeping.

## 5. D7: per-principal rate limit and in-flight cap

- **Where:** the ASGI middleware `PrincipalLimits`, passed through 001's
  `build_app(middleware=[…])`. It runs after `RequirePrincipalMiddleware`, so every request it sees
  is authenticated.
- **Key: the principal's `key_id`, read as `scope["user"].access_token.subject`** (orchestrator
  ruling c; 002 §6 makes `subject` exactly the `key_id`, `"operator"` for the bootstrap token). Not
  the handle: a rotated key gets a fresh budget only by being a new key, and two keys of one person
  (a laptop agent and a CI agent) don't starve each other. If `scope["user"]` or its `subject` is
  missing or empty, it answers 500 rather than letting the request through unkeyed. That can only
  happen if 001/002's invariant breaks. Log lines show both the handle and the key id.
- **In-flight cap, checked first.** At most `max_inflight` requests per key are in progress at once
  (default 32, below the connection cap of 128). A request counts from the moment it passes the
  check until the app returns, so a long-lived SSE `GET` holds its slot for its whole life. One
  token per request can't bound that: an SSE stream costs one token and never times out, so without
  this cap one leaked key could open 128 streams and take every connection slot. Past the cap: 429,
  `Retry-After: 1`, `{"error": "too many concurrent requests"}`. A refused request spends no rate
  token.
- **Rate limit. Unit:** one HTTP request, any method. Each MCP JSON-RPC message is one POST, so this
  is roughly one tool call. The long-lived `GET` stream costs one token when it opens.
- **Algorithm:** a token bucket, with `rate_burst` capacity refilling at `rate_per_minute / 60` per
  second. On refusal: 429, `Retry-After` (whole seconds until the next token, clamped to 1..3600 so
  a zero refill rate in a test can't overflow `ceil(inf)`), and `{"error": "rate limited"}`.
  `receive` is never called, and D3a closes the connection if the request had a body.
- **One limiter and one in-flight counter per daemon, shared by MCP and the viewer** (D10). A
  principal has one budget across both surfaces.
- **The operator isn't exempt.** A stolen operator token is the worst case, so it gets the same
  limit. The operator can raise the flag.
- **Thread-safe:** a `threading.Lock` around an O(1) critical section. The viewer serves on
  threads, MCP on the event loop, and the lock is never held across an `await`.
- The clock is injectable, so tests don't sleep.

## 6. D8: defaults and flags

| Setting | Flag | Default | Range | Why this default |
|---|---|---|---|---|
| body cap | `--max-body-bytes` | 2097152 (2 MiB) | 65536..67108864 | 1 MiB command payload plus envelope (D3) |
| header deadline | `--header-timeout` | 10 s | 1..120 | Matches Caddy `read_header 10s`; a real client sends headers in milliseconds |
| body deadline | `--body-timeout` | 30 s | 1..600 | 2 MiB in 30 s needs only about 70 KB/s |
| connection cap | `--max-connections` | 128 | 8..10000 | Each agent holds one SSE stream plus short POSTs; room for a small team's many sessions |
| principal rate | `--rate-limit` | 240 requests/min | 1..100000 | 4/s sustained, well above an agent's tool loop |
| principal burst | `--rate-burst` | 120 | 1..100000 | Covers parallel tool calls and session start (initialize, list, calls) |
| principal in-flight | `--max-inflight` | 32 | 1..10000, and below `--max-connections` | A person running several agents, each with one SSE stream and a few concurrent POSTs, fits; one leaked key leaves 96 slots for everyone else |
| viewer port | `--viewer-port` | 7471 on the CLI; `None` (no viewer) in `ServeConfig`, so in-process tests opt in and never collide on a fixed port | 0..65535 (0 = OS-assigned; tests) | Fixed, so Caddy can point at it; next to 7470 |
| no viewer | `--no-viewer` | off | | Serve MCP only |
| viewer origins | `--viewer-origin` (repeatable) | none | `https://host[:port]`, or `http://` only for loopback hosts | The public origin(s) Caddy serves the viewer on (D10.3) |

Constants, not flags: the failure budget of 20/min and 4096 peer keys (D6); the `Retry-After`
clamp of 3600 s (D7); the keep-alive of 5 s; the
16 KiB header cap; trusted proxies (D5); the viewer session idle limit of 8 h, absolute limit of
24 h, and at most 256 sessions; the viewer's login body cap of 4 KiB. Each is a named constant in
`config.py`, so 015 can challenge a number without hunting for it.

**The rate defaults are a first guess** that 015 and the first week of use should tune. The
guard's 429 log lines are what to tune from.

## 7. D9: error sanitizing

### What the client sees and what the server logs

Every HTTP response carries `X-Request-Id: <12 hex>`, minted by `RequestGuard` and stored in
`scope["taskmaster.request_id"]`. That id is the correlation id (`ref`) in every sanitized message,
and in the log line that holds the detail.

| Source | Client sees | serve.log gets |
|---|---|---|
| An exception escapes a tool (FastMCP wraps it as `ToolError` *from* the original; *spike*) | `isError` result: `Error: internal error in <tool> (ref <id>); the operator can look it up in the server log` | ERROR `tool error ref=<id> principal=<handle> tool=<name> type=<ExcType>` with the traceback |
| A tool raises `ToolError` itself (no `__cause__`) | its message, path-scrubbed | nothing extra |
| A tool returns text starting `Error:` | path-scrubbed. If it carries an SQL or traceback marker, the whole text becomes `Error: internal error in <tool> (ref <id>)…` | INFO `scrubbed tool error ref=<id> tool=<name>` with the original text, but only when the text changed |
| A tool returns a JSON object with a string `error` field (`{"error": str(exc)}` at `registry.py:205`, `overview.py:309`, `:349`, `context.py:265`, `cursors.py:191`; `exc.public_payload()` at `registry.py:212`) | the same object with `error` path-scrubbed, or replaced by `internal error in <tool> (ref <id>)…` on an SQL or traceback marker; other fields unchanged | as for `Error:` text |
| The coordinator raises a non-domain exception (`service.py:758` today returns `str(exc)`) | `type: ServiceUnavailable`, `error: internal coordinator error (ref <cid>)` | ERROR on `taskmaster.coordinator` with `ref=<cid>` and the traceback (001 attaches serve.log to that logger) |
| The coordinator raises a domain exception (the types `client.py:144` maps, plus `ServiceUnavailable`) | its text, path-scrubbed, with the type unchanged | nothing extra |
| ASGI refusals (400, 401, 408, 413, 421, 429, 503) | fixed JSON bodies with no echo of the input, plus `X-Request-Id` | one line: status, peer, method, path, `ref`. Never headers or bodies |
| An unhandled ASGI error | Starlette's plain `Internal Server Error` (`debug=False`, already so in 001) | uvicorn's traceback, plus the guard's `ref` line |
| Viewer `_send_json` with an `error` string (D10) | path-scrubbed, with SQL markers replaced as above | as for tool text |

### Mechanics

- **Scrubbing.** `scrub(text, root)`:
  - Replace the store root, in native, forward-slash and resolved forms (case-insensitively on
    Windows), with `<root>`, so `<root>/.taskmaster/x.yaml` keeps the useful relative part.
  - Then replace any remaining absolute path (a Windows drive path, or a POSIX path of two or more
    segments not preceded by `:`, `/` or a word character, so URL paths survive) with `<path>`.
  - It returns `(text, changed, internal)`. `internal` is true when an SQL or traceback marker is
    present: `sqlite`, `constraint failed`, `no such table`, `no such column`, `syntax error`,
    `database is locked`, `database disk image`, `disk i/o error`, `datatype mismatch`, `readonly
    database`, `unable to open database`, `Traceback (most recent call last)`.
- **Tool chokepoint: the `SanitizeErrors` FastMCP middleware (`on_call_tool`).**
  - It acts only when `get_http_request()` succeeds, that is, the call arrived over HTTP (*spike*:
    an in-memory or stdio call raises `RuntimeError` there and passes through untouched). Solo
    stdio sessions are therefore unchanged.
  - The daemon adds it to `bs.mcp._inner` in `start()` and removes it in `close()`, as 001 does for
    `AUTOSTART_ALLOWED`, so in-process tests don't leak it.
  - **Two result shapes are errors.** A `TextContent` (or a `structured_content` `{"result": str}`)
    whose text starts with `Error:`; and one whose text parses as a JSON object (it starts with `{`
    after whitespace, and `json.loads` succeeds) with a string `error` field. The second shape is
    what the six `JSON_ERRORS` sites return, and checking the parsed field, not a text prefix, is
    what catches it. A JSON object with no string `error` is a success result and isn't touched.
    A changed JSON result is re-serialized with `ensure_ascii=False`; clients parse it, so the
    escaping change is harmless.
  - **`backlog_query` is exempt from SQL-marker replacement** (paths are still scrubbed). Its errors
    describe the caller's own read-only SQL against a schema it publishes on purpose
    (`overview.py:96`).
- **Coordinator chokepoint: `Coordinator.public_error(exc) -> dict`.**
  - It's a new method in `service.py`, and `_post`'s `except Exception` calls it. The base
    implementation returns exactly today's `{'error': str(exc), 'type': type(exc).__name__}`, so
    solo is unchanged.
  - `ServeCoordinator` (001, `daemon.py`) overrides it: domain types pass through, path-scrubbed;
    anything else is logged with a fresh ref and answered as `ServiceUnavailable`.
  - The client already maps unknown types to `ServiceUnavailable` (`client.py:148`), so its retry
    and `may_have_committed` behaviour is unchanged. Only the text differs.
  - The coordinator's ref is its own: coordinator IPC carries no HTTP request id. The tool layer then
    sees `internal coordinator error (ref c…)` and passes it through.
- **What isn't sanitized, and why.**
  - Success results: they legitimately carry teammates' own worktree paths. Host-path disclosure
    in success results is left to 015 (§11, G2).
  - JSON-RPC parse and validation errors from the MCP SDK: they echo only the sender's own input.
  - `export_error`: it reaches only the host-local `coordinator status` CLI (`control_cli.py:69`),
    never MCP.
- **Logging rules from 001 still hold.** No token, `Authorization` header, cookie, request body or
  tool arguments are logged. An exception's own message may echo an argument; that lands only in
  the owner-only serve.log.

## 8. D10: the viewer in server mode

Today the viewer is a stdlib `ThreadingHTTPServer` that importing `backlog_server` starts
(`backlog_server.py:12809`). It binds `127.0.0.1` on a per-root port from 6800 to 6899, may *reuse*
another process's viewer for the same root (`_check_identity`), and is unauthenticated. It sends
`Access-Control-Allow-Origin: *` at six sites (`:11714`, `:11744`, `:12042`, `:12099`, `:12636`,
and the OPTIONS preflight at `:12652`) and accepts POST, PUT and PATCH (`:12108`, `:12499`,
`:12557`).

**D10.1. The daemon owns a separate, guarded viewer listener. The import-time viewer never starts
in a serve process.**

- `ServeDaemon.start()` sets `TASKMASTER_SERVE=1` before importing `backlog_server` and restores it
  in `close()`. With it set, `_start_viewer_server()` starts nothing and returns `VIEWER_PORT`.
  That covers import time *and* `backlog_open_viewer` (`:12813`), which would otherwise lazily start
  an unguarded viewer on the host when a remote teammate called it.
- With it set, `backlog_open_viewer` also never calls `webbrowser.open` on the host. It returns
  `The viewer is served at <first --viewer-origin, else the loopback URL>; sign in with your key.`
- The daemon builds its own server from a guarded subclass:
  `GuardedViewerServer(("127.0.0.1", viewer_port), make_guarded_handler(bs.ViewerHandler, guard))`.
  It runs it on a daemon-owned thread, sets `bs.VIEWER_PORT`, and shuts it down in `close()`,
  which also restores the `bs.VIEWER_PORT` value saved before `start()`, so an in-process test's
  solo viewer port isn't left pointing at a stopped listener.
  - A bind failure is `ServeUnavailable` (exit 69), raised before the coordinator starts.
  - There's no reuse path. Reuse could hand Caddy an unguarded viewer.
- **The viewer binds `127.0.0.1` always**, whatever `--host` is. Caddy fronts it.
- **The plain `ViewerHandler` stays exactly as it is.** The guard lives only in the subclass, so a
  solo viewer, or the import-started viewer in an in-process test, never sees it.

**D10.2. Login: token in, cookie out.**

- `GET /login` returns a small self-contained form: one password field for the key, styled with the
  viewer's own CSS tokens from `/static/v3/css/`, under the user's design-system rules: no accent
  rails, no hover motion, no shadows.
- `POST /login` (form-encoded or JSON, body at most 4 KiB):
  - The Origin must be an allowed viewer origin (D10.3); that blocks login CSRF.
  - It authenticates with **the daemon's `Authenticator`**, the same object MCP uses, so any member
    key that works for MCP works here (§10).
  - **The bootstrap token signs in only from the host** (orchestrator ruling a). If the principal's
    `key_id` is `"operator"`, the login also requires 002's `serve.auth.is_host_local(scope)`, the
    same function `RequirePrincipalMiddleware` uses; the viewer keeps no copy of the rule. The
    viewer is a stdlib server, so it passes a minimal ASGI-shaped scope built from the raw request:
    `{"type": "http", "client": <TCP peer>, "headers": <lower-cased raw headers>}`. The TCP peer is
    not rewritten from `X-Forwarded-For`, and Caddy always sets that header, so a login through
    Caddy fails the check. An SSH-forwarded browser on `http://127.0.0.1:7471` passes. A refusal is
    403 with 002's text (`the operator bootstrap token works only on the host; use a key from
    taskmaster serve add-user`), no session, no cookie, and isn't charged to the failure budget.
  - **An operator session re-runs `is_host_local` on every request**, not only at login, so a
    session cookie can't be carried from the loopback origin to the public one.
  - On success it creates a session and answers 303 to `/` with the cookie.
  - On failure it answers 401 with the same form and the fixed text `That key was not accepted.`,
    and the failure is charged to the peer's failure budget (D6).
- **The cookie.**
  - `__Host-tm_session=<sid>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400` when the
    login Origin is `https`.
  - `tm_session_<viewer_port>=<sid>; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400` on a
    loopback `http` origin (the SSH-forward path, where `Secure` cookies aren't dependable across
    browsers).
  - The `sid` is `secrets.token_urlsafe(32)`. The server keys sessions by `sha256(sid)`.
- **Cookie scope on loopback.** Cookies aren't scoped by port, and a site ignores the port too.
  The loopback cookie is therefore sent to every listener on `127.0.0.1`, including solo viewers
  (6800 to 6899) and any other local service, and a page served by a solo viewer is *same-site*
  with the guarded viewer, so `SameSite=Strict` doesn't stop it sending requests.
  - What still holds: `HttpOnly` keeps the cookie from page scripts; a write from another port has
    a different `Origin` and is refused (D10.3) and carries no CSRF token (D10.4); and without
    CORS no other origin can read a response.
  - What doesn't: another local service can read the cookie from its request headers. Loopback is
    already untrusted (001 D5); the cookie is worth one session that ends at the 8 h idle limit or
    on logout.
  - The port suffix in the name keeps two serve daemons on one host from overwriting each other's
    cookie. The `__Host-` cookie needs no suffix: its host is the public name.
- **Sessions are in memory**: idle limit 8 h, absolute 24 h, at most 256 (the oldest goes first).
  A daemon restart signs everyone out; that's acceptable for a small team.
- **The session holds the presented key in memory and re-runs `authenticate(key)` on every
  request.** Rotation (001 D5) and revocation (002) therefore end viewer sessions on their next
  request, and 006 needs no new interface from 002.
  - The key never touches disk, the log or the cookie.
  - The daemon already receives raw keys on every MCP request, so this adds no new exposure class.
- `POST /logout` (CSRF-checked) drops the session and expires the cookie.
- `SameSite=Strict` means a link into the viewer from another site arrives without the cookie. In
  that case, `GET /login` with a valid cookie redirects to `/`, so a reload or one click recovers.

**D10.3. Host and Origin.**

- **Allowed origins:** each `--viewer-origin`, plus `http://127.0.0.1:<viewer_port>` and
  `http://localhost:<viewer_port>` for SSH-forward use. Allowed Hosts are the `host[:port]` of
  those origins.
- **Every request** with any other `Host` gets 421, which blocks DNS rebinding.
- **Every write** (any method but GET and HEAD) must carry an `Origin` equal to an allowed origin.
  A missing or other Origin gets 403 `cross-origin write refused`. Browsers send `Origin` on every
  non-GET fetch, and non-browser clients use MCP, not the viewer.
- `Sec-Fetch-Site: cross-site` on a write is refused too, as a second signal.

**D10.4. CSRF token.**

- Each session has its own `csrf` value (`token_urlsafe(32)`).
- When the guarded handler serves `index.html`, it injects
  `<meta name="taskmaster-csrf" content="…">` before `</head>`. That page is `no-store` already.
- Every write must send the `X-Taskmaster-CSRF` header equal to the session's value
  (`hmac.compare_digest`). Otherwise it gets 403 `missing or invalid CSRF token`.
- A custom header also forces a CORS preflight for any cross-origin attempt, and server mode
  answers no preflight (D10.5).
- **The viewer JS gets one chokepoint, `viewer/js/lib/csrf.js`.** `installCsrf(document, window)`
  runs first in `main.js`.
  - If the meta tag is present, it wraps `window.fetch` to add the header on same-origin non-GET
    and non-HEAD requests. It also sends the page to `/login` on any 401.
  - If the tag is absent (solo), it does nothing.
  - The five direct `fetch` write sites (`api.js` `http()`, `quickCapture`, `archiveBug`,
    `bugPatternScan`; `right-rail.js` `postHandoverStatus`) need no edit.
  - Header merging keeps what the caller sent: the base is `new Headers(init.headers)` when
    `init.headers` is given, else the `Request` input's own headers, so wrapping a `Request` never
    drops its `If-Match` or `Content-Type`. The method is likewise `init.method`, else the
    `Request`'s.

**D10.5. No CORS in server mode.**

- `ViewerHandler` gains one method, `_cors_headers()`, which sends `Access-Control-Allow-Origin: *`,
  as today. All six sites call it.
- The guarded subclass overrides it to send nothing.
- `OPTIONS` in server mode answers 405, with no `Access-Control-*` headers.
- The guarded subclass adds `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors
  'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin` to every response,
  in an `end_headers` override. That stops the board or the login form from being framed.
- *Corrected after the review gate (C1):* this said `no-referrer`. Under `no-referrer` a browser's
  same-origin form POST carries `Origin: null`, which D10.2's Origin check refuses, so nobody could
  sign in. `same-origin` keeps the real Origin on same-origin requests and still sends no Referer to
  other origins.
- *Added after the review gate:*
  - `/file/` responses also carry `Content-Security-Policy: sandbox allow-scripts`. Teammates'
    markdown then runs in an opaque origin, so it cannot read the viewer with the visitor's cookie
    or pass the Origin check on a write (I1). The path written into the page's link is HTML-escaped.
  - `/file/` reads the checked file through one verified handle (I5).
  - Request bodies, including `POST /login`, are read against one `body_timeout` deadline. Past it
    the answer is 408, which is charged when there is no session (I3).
  - A login spends the key's in-flight slot and rate budget. Each key holds at most
    `VIEWER_MAX_SESSIONS_PER_KEY` sessions, so one key's logins evict only its own (I4).

**D10.6. Request handling order in the guarded handler**, run from a `parse_request` override.
The stdlib calls `do_<METHOD>` only if `parse_request` returns true, so this is one chokepoint for
every method, including ones added later:

1. Host check → 421.
2. Peer failure budget → 429. The peer is the right-most `X-Forwarded-For` entry when the TCP peer
   is loopback (it always is), else the TCP peer, keyed by `peer_key` (D6: IPv6 on its /64).
3. `Transfer-Encoding` present → 411, since the stdlib handler can't read chunked bodies.
   `Content-Length` over `max_body_bytes` → 413. Neither reads the body; both set
   `close_connection`.
4. `OPTIONS` → 405, before any session check: a preflight never carries a cookie, and the answer
   must be the same with or without one.
5. Public paths are served without a session: `GET /login`, `POST /login`, `/static/v3/*` and
   `/favicon.ico`. The static files are the plugin's public code and hold no data.
6. Session cookie → re-authenticate (D10.2), plus `is_host_local` for an operator session. If that
   fails: `/api/*` and writes get 401 JSON; other GETs get 303 to `/login`; an off-host operator
   session gets 403.
7. Per-principal in-flight cap and rate limit, from the shared counter and limiter, keyed by
   `key_id` (D7) → 429. The in-flight slot is released when the base handler returns.
8. Writes (every method but GET and HEAD, including methods the base handler doesn't implement):
   Origin (D10.3), then CSRF (D10.4) → 403.
9. `/file/`: only git-tracked files (D10.7) → 404.
10. Pass to the base handler, with `self.principal` set and the viewer origin bound (D10.10).

Failure-budget charges follow D6: 401 always; 400, 411, 413 and 421 only when the request carries
no valid session cookie (a session-store lookup, no re-authentication); never 403 or 429. Every
logged value the client controls goes through `log_safe`.

The guarded handler's socket `timeout` is `header_timeout`. That is a per-`recv` idle limit; the
stdlib has no whole-request deadline, so Caddy's `read_header` is the whole-header deadline on the
public path. `GuardedViewerServer` caps concurrent connections at 32 and closes the excess at
accept.

**D10.7. `/file/` would hand any logged-in member the operator token.**

- `_serve_repo_file` (`:12057`) serves any file under `ROOT`, including
  `.taskmaster/local/serve/operator.token`. Its containment check is a string `startswith`, which
  also admits a sibling directory such as `<root>-x`.
- **In server mode `/file/` serves only git-tracked files: an allow-list, not a deny-list.** A
  deny-list has to foresee every secret (the operator token, `.mcp.json`, a `.env` variant, an
  editor's backup, a key someone dropped in the tree). The tracked set is what the team already
  shares through git, so serving it discloses nothing new.
- `file_path_allowed(root, rel, tracked)` returns true only if all of these hold:
  - `rel` has no `..` segment, no backslash, no drive or leading `/`, and `(root / rel).resolve()`
    is relative to `root.resolve()` (`Path.is_relative_to`, so a sibling such as `<root>-x` fails);
  - the `/`-separated `rel` is in the tracked set: the output of `git -C <root> ls-files -z
    --cached`, compared exactly (case-sensitively, so a case-folding filesystem can't alias an
    untracked file to a tracked name);
  - the resolved path is not a symlink to somewhere outside `root` (the resolve check covers it).
- The tracked set is cached for 30 s and loaded with the repo's bounded subprocess helper
  (`taskmaster/bounded_run.py` `run_bounded`, timeout 10 s, because `subprocess.run(timeout=…)` can hang on Windows through a `git.exe`
  grandchild). If `root` isn't a git work tree, or git fails or times out, the set is empty and
  every `/file/` request is 404, logged once at WARNING. It fails closed.
- `.taskmaster/local/` is git-ignored, so the operator token is never in the set. The check
  doesn't rely on that alone: paths under `.taskmaster/local/` and `.git/` are refused even if
  tracked.
- It also overrides `_serve_identity` to drop `root`, and scrubs `FULL_PATH` in the markdown
  template to the relative path.
- The solo route is unchanged; G1 covers it.

**D10.8. Errors.** The guarded subclass overrides `_send_json` to scrub a string `error` field
(D9). Every viewer error answer, including `handle_one_request`'s 409 `str(exc)` (`:11674`) and the write paths' 409s (`:12281`, `:12298`, `:12551`, `:12591`), goes
through `_send_json`.

**D10.9. The default exposure.** After 006, a serve process's viewer listens on `127.0.0.1:7471`
and requires a login there too, consistent with 001 D5 ("loopback can't be trusted"). Publishing
it through Caddy is the operator's choice (§9), and it is allowed only after the 015 review
(addendum §13 go-live order). `--no-viewer` turns it off.

**D10.10. Viewer writes are attributed to the logged-in principal** (orchestrator ruling b, as
refined by 002's re-review; names from 002's design at `2163b57`, §8 D8). 002 owns every
process-global piece in `taskmaster/serve/auth.py`:

- `VIEWER_KEY_ID`, the viewer key-id `ContextVar` (default `None`);
- `request_key_id()`, which returns `current_key_id() or VIEWER_KEY_ID.get()`. It is the single
  `client.ORIGIN_PROVIDER`, and 002's daemon code installs it in `start()` and restores it in
  `close()`;
- `viewer_principal(key_id)`, a context manager that validates the key id, sets `VIEWER_KEY_ID`
  for the current context and resets it on exit.

**006 never assigns `client.ORIGIN_PROVIDER`, `VIEWER_KEY_ID` or any other module-level or
process-global value, and never calls `VIEWER_KEY_ID.set` itself.** Its whole side of the contract:

- The guarded handler runs every write method (`do_POST`, `do_PUT`, `do_PATCH`, and any other
  non-GET/HEAD `do_<METHOD>` the base defines) inside
  `with auth.viewer_principal(self.principal.key_id):`, after `admit` has passed the login, Origin
  and CSRF checks. Reads don't need it and don't get it.
- The context manager resets the variable when the method returns or raises, so a kept-alive
  connection's next request never inherits it.
- Each viewer connection runs on its own thread, and a new thread starts with an empty context,
  so one user's value is never seen by another user's concurrent request. A test runs two users'
  writes concurrently and checks each is credited to its own key.
- If a viewer write hands work to another thread, the hand-off runs the work under
  `contextvars.copy_context().run(...)`, so the value travels with it. Nothing sets the value on
  the other thread by hand.
- MCP requests never enter `viewer_principal`, and `current_key_id()` returns `None` on viewer
  threads (no HTTP MCP request), so `request_key_id()` picks the viewer value there and only
  there.
- So a viewer write reaches the coordinator with `origin={"principal": key_id}`, and 002's actor
  source stamps it with that person's handle. This closes 002's §13 gap (team-mode viewer writes
  refused as `LOCAL`).
- If a viewer write path ever hands its `Client.execute` to another thread without
  `copy_context().run`, the value is lost, the write goes out as `LOCAL`, and in team mode 002
  refuses it. That fails closed, and the concurrent-credit test catches it.

## 9. D11: Caddy versus taskmaster

| Concern | Caddy | taskmaster |
|---|---|---|
| TLS, ACME, HTTP→HTTPS redirect, HSTS | yes | never serves TLS (001 §6) |
| Whole-header deadline | `read_header 10s` (primary on the public path) | `DeadlineH11Protocol` (defence in depth; the only guard on loopback or `--insecure-bind`) |
| Body deadline | not set: Go's read deadline can cut long SSE streams; task 16 verifies | `body_timeout` (D4) |
| Body size | `request_body max_size 2MB` (defence in depth) | `max_body_bytes` (D3) |
| `X-Forwarded-For` | discards inbound values (no `trusted_proxies`), sets the real client | trusts it only from 127.0.0.1 and ::1 (D5) |
| `Host` | forwards the public host | `--allowed-host` 421 check (001); viewer Host check (D10.3) |
| SSE flushing | automatic for `text/event-stream` | n/a |
| Access log | on, with `Authorization` and `Cookie` redacted (Caddy's default) | refusals and sanitized errors only (D9) |
| Auth, rate limits, sanitizing, CSRF, CORS | none | all |
| Firewall | only 80 and 443 open; 7470 and 7471 bind loopback | binds loopback by default (001) |

Sample Caddyfile (task 16 ships the real one):

```
{
	servers {
		timeouts {
			read_header 10s
			idle        2m
		}
	}
}

tm.example.com {
	request_body {
		max_size 2MB
	}
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy 127.0.0.1:7470
}

# Add only after the task 15 security review (addendum §13).
viewer.tm.example.com {
	request_body {
		max_size 2MB
	}
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy 127.0.0.1:7471
}
```

The matching unit line adds `--viewer-origin https://viewer.tm.example.com`. Don't add `encode`
to the MCP site: compression can buffer SSE.

**Containerised Caddy (for 016).** Everything above assumes Caddy runs on the host and connects
from `127.0.0.1`. A Caddy container on a bridge network connects from the bridge address (for
example `172.17.0.1`), which isn't in the trusted-proxy set, so `X-Forwarded-For` is ignored and
every teammate shares one failure-budget key; and taskmaster bound to `127.0.0.1` isn't reachable
from the container at all. 016 should run Caddy on the host or in a container with host
networking (`network_mode: host`), and not widen `TRUSTED_PROXIES` to fit a bridge.

## 10. D12: the seam with team-server-002

The orchestrator's rulings (a) to (c) fix this seam, and 002's design applies them on its side
(002 §6, §7 D7, §8 D8, §10). 006 builds on 002's tip and assumes only this:

1. **`Authenticator.authenticate(token: str) -> Principal | None`** keeps its signature. It stays
   thread-safe, and cheap enough to call on every request: the viewer calls it on each request
   (D10.2), the viewer polls every 3 s, and the guard calls it on charging refusals (D6).
2. **Revocation and rotation take effect on the next `authenticate` call.** 001's operator token
   already behaves this way.
3. **`Principal` carries `handle`, `role` and `key_id`** (002 §2). `key_id` is `"operator"` for the
   bootstrap token, else 16 lowercase hex. `handle` is plain text (no newlines, at most 64
   characters) and appears in log lines; 006 doesn't branch on `role`.
4. **Ruling (c): `access_token.subject` is exactly the `key_id`** on every MCP request (002 §6).
   `PrincipalLimits` keys the rate limit and the in-flight cap on it; the viewer keys the same
   limiter and counter on `Principal.key_id`, so one key has one budget on both surfaces.
5. **Ruling (a): `serve.auth.is_host_local(scope) -> bool`** is the one host-local check. 006
   calls it at viewer login and on every operator-session request (D10.2), with an ASGI-shaped
   scope built from the stdlib request. 006 keeps no copy of the rule.
6. **Ruling (b): 006 owns viewer-write attribution, but no global state** (D10.10). 002 defines
   `serve.auth.VIEWER_KEY_ID`, `serve.auth.request_key_id` (the single `ORIGIN_PROVIDER`, which
   002's daemon code installs and restores) and the context manager
   `serve.auth.viewer_principal(key_id)`. 006 only runs its viewer write handlers inside
   `with auth.viewer_principal(key_id):`, and carries the context across threads with
   `contextvars.copy_context().run`.
7. **002 supplies its authenticator through `ServeDaemon(authenticator=…)`.** 006 hands the same
   object to the viewer guard and to `RequestGuard`.

**Ordering.** Steps 0 to 3, 5, 6 and 8 of the plan need only 001. Steps 4, 7 and 9 need 002's tip
(after 002 has rebased onto 001's final tip); the branch rebases onto it before Step 4. If 002
changes `Principal`, `subject` or `is_host_local`, the tests named in the plan for Steps 4, 7 and 9
fail first.

## 11. Known gaps this task leaves open (by design)

- **G1. Solo viewer cross-origin reads and writes.** A solo viewer on `127.0.0.1` is open to any
  web page the user visits, both ways. Reads: `Access-Control-Allow-Origin: *` lets the page
  `fetch` `/api/backlog` and the rest and read the answers. Writes: no Origin check, and simple
  `text/plain` POSTs need no preflight. 006 leaves solo byte-for-byte unchanged, as briefed. The
  fix (drop `*` CORS, and check Host and Origin on solo requests) is a follow-up bug, worth filing
  at once.
- **G2. Host paths in success results** (for example `backlog_store_status`). They're for 015 to
  judge.
- **G3. One unit for every request.** A heavy `backlog_query` costs one token, like a cheap read.
  Weighting by tool is a later refinement.
- **G4. Local users can spoof `X-Forwarded-For`** to the loopback listeners, dodging only the
  failure budget. Auth still applies.
- **G5. In-memory viewer sessions** end on restart.
- **G7. uvicorn's own WARNING lines** for malformed requests and the concurrency limit aren't
  budgeted (D6). They go to stderr, not serve.log.
- **G8. The loopback session cookie reaches other local listeners** (D10.2). Loopback is untrusted
  already; the cookie's worth is one session.
- **G6. The stdlib viewer has no whole-request header deadline** of its own; Caddy provides it on
  the public path.

## 12. Risks

- **R1. uvicorn internals.** `DeadlineH11Protocol` relies on `connection_made`, `data_received`,
  `on_response_complete`, `conn.their_state` and `transport`. The `uvicorn>=0.35,<1` pin bounds
  them, and the slow-header test fails first if they change. D3a relies on uvicorn honouring a
  response `Connection: close` header; the trickled-body test fails first if that changes.
- **R2. FastMCP internals.** `SanitizeErrors` relies on `ToolError … from e` (`server.py:1356`) and
  on `get_http_request()` raising outside HTTP. The `<4` pin bounds both, and Step 5 tests both.
- **R3. Shared-file edits.** `backlog_server.py` gets five small hooks: the serve-mode check in
  `_start_viewer_server`, `backlog_open_viewer`, `_cors_headers`, the head-injection hook and the
  markdown path hook. `service.py` gets `public_error`. `daemon.py` is edited by 001, 002 and 006
  alike. 002, 003 and 005 are likely to touch these files too, so whichever lands second rebases,
  and the step tests catch drift.
- **R4. Heuristic scrubbing.** A path in an unforeseen shape, or an SQL error without a listed
  marker, gets through. The coordinator chokepoint removes the main source (`str(exc)` of
  `sqlite3.Error` from the writer). 015 should probe for others.
- **R5. Rate defaults may be too tight for a busy agent.** 429s surface as tool errors the agent
  retries. Tune from the log.

## 13. Rollback

Revert the step commits. There's no schema, data or projection change. Viewer sessions are in
memory. Reverting restores 001's state: loopback-only, unauthenticated viewer, unsanitized errors.
Under addendum §13, no teammate key may be issued in that state.
