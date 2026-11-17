<!-- User intent: the task-level design for team-server-001, the foundation of server mode. One
     `taskmaster serve` daemon on the user's Hetzner box runs the existing MCP tools over HTTP behind
     Caddy, and nobody who can reach the port can use it without a token, even before per-person keys exist. -->

# team-server-001: `taskmaster serve` (design, 2026-10-31)

**Task.** `team-server-001`. Done when `taskmaster serve --root` runs `backlog_server` over streamable
HTTP as a long-lived daemon (no idle exit, no client autostart or retire), with the coordinator
loopback-only, and with non-loopback plaintext binds refused unless `--insecure-bind` is passed.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md`: §2 shape, §4 transport security,
§11 task 1, §13 deployment profile.
**Plan.** `docs/plans/2026-10-31-team-server-001-serve.md`.
**Verified against.** FastMCP 3.4.3, uvicorn 0.51.0 and Starlette 1.3.1 in the repo venv. A spike
confirmed every FastMCP and uvicorn behaviour this design relies on (§4, §6).

## 1. Scope

In scope:

- the `serve` CLI;
- hosting the existing `backlog_server` FastMCP instance over streamable HTTP, with idle MCP sessions expired (D10);
- declaring the directly imported `uvicorn`, `starlette` and `mcp`;
- bind policy;
- one operator bootstrap token with a pluggable authenticator;
- the coordinator held in-process for the daemon's whole life, with no idle exit, no client
  autostart, and refusal of `retire` and `shutdown`;
- logging, graceful shutdown and exit codes;
- the systemd unit and Caddy shape, described here only (task 16 ships them).

Out of scope, each owned by a later task:

| Not here | Task |
|---|---|
| Per-person keys (`add-user`/`revoke-user`) and per-request identity | 002 |
| Per-connection `SESSION_ID` and handle-prefixed `caller_scope` | 003 |
| Receipt and queue scoping | 004 |
| Admin-method allowlist; `linear_bootstrap` hardening | 005 |
| Rate limit, body and header timeouts, error sanitizing, viewer login | 006 |
| Read endpoint for hooks and mods | 007 |
| Split-brain marker, refusing host-local sessions, `--force-upgrade` | 009 |
| `[project.scripts]` console entry, Linux CI lane, deployment how-to | 016 |

## 2. CLI surface

`taskmaster` means the packaged front door, `uv run <plugin>/taskmaster_cli.py` (or
`python taskmaster_cli.py`). `serve` is added to its `COMMANDS` table as `taskmaster.serve.cli`;
`python -m taskmaster.serve` works too.

```
taskmaster serve --root DIR [--host 127.0.0.1] [--port 7470] [--path /mcp]
                 [--allowed-host NAME]... [--insecure-bind] [--log-level info]
                 [--session-idle-seconds 1800]
taskmaster serve token --root DIR [--rotate]
```

- `--root` is required. A daemon never guesses its store from the working directory.
- `--host` is an IP literal or `localhost`, which binds `127.0.0.1`. Any other hostname is refused,
  because what it resolves to can change underneath the bind policy.
- `--port 0` asks the OS for a free port; tests use it.
- `--allowed-host` (repeatable) adds names the `Host` header may carry. Behind Caddy that is the
  public domain (§6).
- `--session-idle-seconds` (default 1800, range 1 to 86400) ends an MCP session that has had no
  HTTP request for that long (D10).
- `serve token` prints the operator token to stdout and creates it if missing. `--rotate` replaces
  it, and a running daemon accepts only the new token from its next request on (D5).
- `--help` never imports `taskmaster.backlog_server`, because that import starts the viewer
  (`tests/test_packaging.py:206`).
- Later tasks add `add-user`, `revoke-user` and `init` to the same sub-command table.

Exit codes:

| Code | Meaning | systemd |
|---|---|---|
| 0 | Clean stop after a signal | no restart |
| 2 | Refused configuration: bind policy, root not a native store, token file unsafe or malformed, `local/serve/` or `local/coordinator/` not owner-only (a `PermissionError` is mapped here, never left to exit 1), server module pointed elsewhere | `RestartPreventExitStatus=2`, so no restart loop |
| 69 | Unavailable: another coordinator owns the store, or the port is taken | restart after `RestartSec` |
| 75 | The coordinator stopped underneath the daemon (for example, the native authority changed) | restart |

## 3. Shape: one process, three listeners

```
Caddy :443 (TLS) ──► 127.0.0.1:7470  uvicorn ─ Starlette app ─ FastMCP (backlog_server tools)
                                          │
                                          └─ tools ─ coordinator Client ─► 127.0.0.1:<ephemeral>
                                                                           in-process ServeCoordinator
viewer: 127.0.0.1:<6800-6899>, started by importing backlog_server, never exposed (addendum §5)
```

- **D1. The coordinator runs in the daemon's own process**, as `ServeCoordinator(root).start()`.
  It isn't a launched child. The daemon therefore holds the store's kernel ownership lock
  (`ownership.py:160`) for its whole life, and that lock is exactly what task 9's "only a process
  started by `taskmaster serve` may open the host store" builds on.
  - The coordinator already binds `127.0.0.1` on an ephemeral port (`service.py:155`). It requires
    the 64-hex bearer token, which is published only in the owner-only `discovery.json`. This task
    keeps that unchanged and adds a regression test for it.
  - Tests already run coordinators in-process (`tests/native_coordinator_helpers.py`), and the
    tools reach it through the normal `Client` over loopback. Nothing in the tool layer changes.
- **D2. The tools are the existing `backlog_server` module, and it is imported before the
  coordinator starts.** `serve` sets `TASKMASTER_ROOT`, `chdir`s to the root, imports the module,
  and checks that `bs.ROOT` resolves to `--root`; a mismatch is refused with exit 2. When the
  module is already imported (in-process tests), only the check runs.
  - **Why the order matters (review I1).** `Coordinator.start()` sets `export_needed`
    (`service.py:167`). The exporter thread then imports `taskmaster.native_routing.projection`
    (`service.py:361`), which loads `taskmaster.backlog_server`. If the coordinator started first,
    that import could run before `TASKMASTER_ROOT` and the `chdir` are set, and `bs.ROOT` would
    come from whatever cwd systemd or the shell gave the process.
  - The ready line reports `server_root` (the resolved `bs.ROOT`) next to `root`. The end-to-end
    test runs the child from an unrelated cwd and asserts that both equal `--root`.
  - `taskmaster.serve.daemon` itself must not import `taskmaster.backlog_server` at module level;
    a packaging test checks this.
  - This task changes nothing in `backlog_server.py`'s code. It changes only the PEP 723 header
    line of `taskmaster/backlog_server.py` and of the root shim (§4, R1).

## 4. Hosting FastMCP streamable HTTP

- **D3. Build the app with `fastmcp.server.http.create_streamable_http_app`,** not
  `FastMCP.http_app()`.
  - `http_app()` takes auth only from `self.auth`, so using it means mutating the shared
    `bs.mcp._inner.auth`. In-process tests would leak that into every later test.
  - `create_streamable_http_app` is the function `http_app()` calls (`fastmcp/server/http.py:483`),
    with the same arguments. The cost is coupling to a module-level function, which the
    `fastmcp>=3.4,<4` pin bounds.
  - Arguments: `server=bs.mcp._inner`, `streamable_http_path=--path`, `auth=ServeTokenVerifier(...)`,
    `json_response=False`, `stateless_http=False`, `host_origin_protection=True`,
    `allowed_hosts=--allowed-host`, `allowed_origins=[]`, and
    `middleware=[RequirePrincipalMiddleware, *extra]`.
  - The session is stateful because task 3 binds sessions and claims to MCP connections (§7).
- **D4. uvicorn runs on a worker thread; the main thread owns signals.** uvicorn's
  `capture_signals` re-raises a captured SIGTERM or SIGINT after its graceful shutdown
  (`uvicorn/server.py:333-335`). With the default handler that kills the process before the
  coordinator can drain and release its lock.
  - On a non-main thread uvicorn installs no handlers (`server.py:322`).
  - The daemon installs SIGTERM, SIGINT and, on Windows, SIGBREAK handlers. Each sets
    `server.should_exit`; a second signal sets `force_exit`.
  - The daemon pre-binds the socket (`socket.create_server`; `AF_INET6` for an IPv6 literal) and
    hands it to `Server.run(sockets=[sock])`. A bind error then surfaces before the coordinator
    starts, and `--port 0` reports its real port.
- **uvicorn config.** `lifespan="on"`, `log_config=None`, `access_log=False`, `server_header=False`,
  `timeout_graceful_shutdown=GRACEFUL_SECONDS` (10). `proxy_headers` stays on with the default
  `forwarded_allow_ips=127.0.0.1`, so logs show the client address Caddy forwards. It's never used
  for any decision.
- **Direct imports are declared (review I2).** `taskmaster/serve/` imports `uvicorn`, `starlette`
  and `mcp` directly, and `tests/test_packaging.py:62` (`test_every_third_party_import_is_declared`)
  requires every third-party import to be declared. The task adds `uvicorn>=0.35,<1`,
  `starlette>=1.0.1,<2` and `mcp>=1.24,<2` to `pyproject.toml`, to the four PEP 723 headers
  (`backlog_server.py`, `taskmaster/backlog_server.py`, `taskmaster_cli.py`,
  `hooks/merge_recorder_stamp.py`, kept identical by `tests/test_packaging.py:55`), and to `uv.lock`
  (`uv lock`). The venv already holds uvicorn 0.51.0, starlette 1.3.1 and mcp 1.28.1, so nothing
  new is installed.
- **D10. MCP sessions expire when idle (review I3; orchestrator ruling: fixed here).**
  `FastMCPStreamableHTTPSessionManager` passes no `session_idle_timeout` to the SDK manager
  (`fastmcp/server/http.py:44-61`), so a stateful session lives until the process ends. A
  reviewer's spike left 21 live sessions after 20 abandoned `initialize` calls; on an internet-facing
  host that is unbounded memory.
  - The SDK manager reads `self.session_idle_timeout` when it creates each session and on every
    later request (`mcp/server/streamable_http_manager.py:259`, `:293-295`), and it ends and drops
    an idle session itself.
  - `build_app` therefore wraps the app's lifespan: once FastMCP's lifespan has built the session
    manager, it sets `session_manager.session_idle_timeout = session_idle_seconds`, before any
    request. The manager is found through the MCP route (`route.endpoint.app`, a
    `StreamableHTTPASGIApp`). If that shape isn't found, `build_app` raises, so a FastMCP change
    fails loudly instead of silently dropping the timeout.
  - The default is 1800 s, the SDK's own recommendation. A spike confirmed it: 5 abandoned sessions
    with a 1 s timeout dropped to 0 within 2.5 s.
  - Task 3 and addendum §7 tie claim renewal to connection lifetime, so this timeout is also what
    ends an abandoned connection's claim renewal.

## 5. Authentication before principals exist

**Decision (D5): every request on every path needs the operator bootstrap token, loopback
included. Non-loopback binds stay behind `--insecure-bind`, and deployment documents TLS at the
proxy.** This is the caller's suggestion, adopted. The reasons:

- **Loopback can't be trusted.** Caddy terminates TLS and connects from `127.0.0.1`, so every
  internet request arrives on loopback. Trusting loopback would make the public endpoint open.
  Other local users and processes on the host are also untrusted.
- **One token on the host matches what comes next.** Task 2's principal model already has an
  operator who may do more than members (addendum §3). The bootstrap token *becomes* that operator
  principal; task 2 adds members beside it rather than replacing it.
- **Auth runs before the body is read** (part of addendum §4), at no extra cost.
  `RequirePrincipalMiddleware` is plain ASGI. It looks only at `scope["user"]`, which Starlette's
  `AuthenticationMiddleware` set from the `Authorization` header, and it answers 401 without
  calling `receive`.
- **Deny by default on every path.** FastMCP protects only its MCP route; custom routes are
  unprotected (`http.py:574-576`). The middleware refuses any unauthenticated HTTP request on any
  path, so task 7's read endpoint and any later route start out protected. Spike results: no token
  → 401; wrong token → 401; `/other` without a token → 401; `/other` with a token → 404.

Token storage:

- **Where.** `.taskmaster/local/serve/operator.token`. The directory is made owner-only exactly
  like `local/coordinator/` (the same `ownership.private_directory`, generalized with a `name`
  argument), and `.taskmaster/local/` already carries its own `.gitignore`.
- **Format.** `tm_op_` plus `secrets.token_urlsafe(32)`, with a newline. A file that fails
  `verify_private` (`ownership.py:114`), is shorter than 32 characters, or holds characters
  outside `[A-Za-z0-9_-]` refuses startup with exit 2.
- **Who creates it.** `serve` creates it if missing and logs the path, never the value. Creation is
  exclusive (review M5): the token is written to a private temp file and published with
  `os.link(temp, path)`, which fails if the file already exists. Two concurrent creators then end
  up with one token, the loser reading the winner's file, and no reader ever sees a half-written
  file. Only `--rotate` uses `os.replace`, because rotation is meant to replace the file. The
  operator reads it on the host with `taskmaster serve token --root DIR`.
- **Comparison.** The authenticator keeps only `sha256(token)` and compares
  `hmac.compare_digest(sha256(presented), digest)`.
- **Rotation without a restart.** The authenticator re-reads the file when its
  `(st_mtime_ns, st_size, st_ino)` changes, so after `serve token --rotate` the old token stops
  working on the next request.

The pluggable seam (`taskmaster/serve/auth.py`):

```python
@dataclass(frozen=True)
class Principal:
    handle: str          # "operator" here; task 2 adds member handles
    role: str            # "operator" | "member"

class Authenticator(Protocol):
    def authenticate(self, token: str) -> Principal | None: ...

class ServeTokenVerifier(TokenVerifier):        # FastMCP's verifier interface
    def __init__(self, authenticator: Authenticator): ...
    async def verify_token(self, token) -> AccessToken | None
        # AccessToken(token=f"sha256:{hex[:16]}", client_id=handle, scopes=[], expires_at=None,
        #             claims={"taskmaster_principal": handle, "taskmaster_role": role})

def current_principal() -> Principal | None     # from fastmcp get_access_token(); for tools and middleware
```

- `AccessToken.token` carries a fingerprint, never the raw token, so nothing downstream can log
  the secret.
- **Task 2** swaps `OperatorTokenAuthenticator` for a lookup that holds both the operator token and
  member hashes; everything else stays. Task 2 should also add `operator` to the reserved handles
  (`team_identity.RESERVED_HANDLES`).
- **Task 6 and pre-auth limits (review M3).** `build_app(..., middleware=[...])` runs *after*
  authentication, so it suits per-principal limits only. Limits that must apply before
  authentication (floods of unauthenticated requests, slow headers, oversized bodies) need either an
  outer ASGI wrapper around the whole app or uvicorn settings (`limit_concurrency`,
  `timeout_keep_alive`, `h11_max_incomplete_event_size`). This task leaves both seams:
  `ServeDaemon(..., outer_middleware=[...])` wraps the finished app, outermost first, and must pass
  `lifespan` scopes through; `ServeDaemon(..., uvicorn_options={...})` is merged into the uvicorn
  config. Both are unused in 001, and one test checks that an outer wrapper sees refused requests.
- **Task 2 and remote operator use (review M4).** The bootstrap token is meant for the operator on
  the host. Once task 2 exists, the operator's own agent on a laptop should get its own key from
  `add-user`, and the bootstrap token should stay on the host. Task 2 should say so in its how-to.
- **team-identity-002's actor seam** reads `current_principal()` in a FastMCP `Middleware` or tool
  context.

## 6. Bind policy and the `Host` header

- `check_bind(host, insecure_bind)` runs first, before the token file or the store is touched.
  - Loopback means `localhost` or an IP literal with `is_loopback` (`127.0.0.0/8`, `::1`).
  - Anything else (`0.0.0.0`, `::`, LAN or public addresses) raises `BindRefused`, exit 2, unless
    `--insecure-bind` is given.
  - With the flag, the daemon prints this warning to stderr and logs it at WARNING: *"serving
    plaintext HTTP on {host}: the bearer token and all backlog data cross the network unencrypted,
    so anyone on the path can read them and replay the token. Bind 127.0.0.1 behind a TLS reverse
    proxy (Caddy) or an encrypted overlay (Tailscale, WireGuard, SSH tunnel) instead."*
  - The daemon never serves TLS itself; TLS belongs to the proxy (addendum §13).
- **The `Host` check stays on.** FastMCP's `HostOriginGuardMiddleware` runs first and answers 421
  for an unknown `Host` (spike-confirmed). Caddy forwards the public `Host` by default, so the
  deployment passes `--allowed-host tm.example.com`. That keeps DNS-rebinding protection rather
  than disabling the guard.

## 7. Daemon lifetime versus the coordinator's idle exit, autostart and retire

Today a session's coordinator is launched by `Client._start` (`client.py:234`), exits after 300 s
idle (`service.py:784-801`), and is retired by a newer client (`client.py:262`,
`service.py:546`). The daemon needs none of that:

- **No idle exit.** Idle expiry lives only in `service.main` (`service.py:796`), which `serve`
  never calls. The daemon's main loop watches `coordinator.stopping` instead (D6).
- **D6. If the coordinator stops underneath the daemon, the daemon stops too.** This covers an
  authority change (`service.py:446-449`) or any other `stop()` the daemon didn't ask for. The main
  loop then logs `coordinator stopped; exiting so the supervisor restarts the daemon`, sets
  `should_exit`, and exits 75. A restart that finds the store unusable exits 2, so it doesn't loop.
- **D7. No client autostart in the serve process.** `client.py` gains the module flag
  `AUTOSTART_ALLOWED = True`, and `Client.__init__` takes
  `self.autostart = autostart and AUTOSTART_ALLOWED`.
  - The daemon sets the flag to `False` in `start()` and restores it in `close()`.
  - Without it, a tool call that runs between a coordinator stop and process exit would
    `_launch` a stray subprocess coordinator with an idle timeout.
  - With `autostart=False`, `Client._retire` refuses before sending (`client.py:283-285`), so no
    client in the daemon can retire anything either.
  - Together with the M2 `reason` change (D8), these are the only edits to `client.py`.
- **D8. The retire fence.** `ServeCoordinator(Coordinator)` overrides `retire()` to answer
  `{'state': 'refused', 'build': ..., 'reason': SERVE_RETIRE_REFUSED}` for every build.
  - A newer session's client on the host, the only party that could ever send `retire`, then
    raises `HandshakeError('… refused to retire')` and leaves the daemon running.
  - The text says how to upgrade: stop the daemon (`systemctl stop taskmaster`), install the new
    build, start it.
  - **The guidance reaches the user (review M2).** Today `Client._retire` drops the refusal's
    `reason` and raises only `… refused to retire` (`client.py:297`). This task makes it append
    `: <reason>` whenever the answer carries one. Existing matches on `refused to retire` still pass.
  - Task 9 adds `--force-upgrade`.
- **D9. `shutdown` over coordinator IPC is refused too.** `ServeCoordinator.dispatch` checks the
  handshake, then raises `ValueError(SERVE_SHUTDOWN_REFUSED)`. `coordinator stop` would otherwise
  turn into a silent daemon restart (D6). The daemon's lifetime is signal-driven only.
  - `control_cli.stop` currently lets that `ValueError` escape as a traceback (`control_cli.py:82`).
    This task catches it and reports `{"state": "refused", "detail": <text>}` with exit 1.
  - The `ServeUnavailable` text for an already-owned store distinguishes the two cases. A session's
    coordinator: stop it with `taskmaster coordinator stop --root R`, or wait for its idle exit.
    Another `taskmaster serve`: stop that daemon, because `coordinator stop` is refused there.
- **Remote clients can't reach retire at all.** The MCP surface has no passthrough to coordinator
  methods; the HTTP app has no `/rpc` route (a valid token gets 404); and the coordinator listens
  only on loopback, behind the discovery token. Tests cover each of these.
- **Both classes live in `taskmaster/serve/daemon.py`.** Subclassing means this task doesn't edit
  `service.py`, which team-identity-002 is changing in parallel.

## 8. Logging

- Logger `taskmaster.serve`. Its handlers are stderr (journald on Linux) and a
  `RotatingFileHandler` at `.taskmaster/local/serve/serve.log` (1 MiB × 3, UTF-8).
- The same handlers are attached to `taskmaster.coordinator`, the parent of the service, git,
  identity-gate and Linear-worker loggers, so managed-Git recovery warnings land in the log too
  (review M6). They are also attached to `taskmaster.integrations.linear` and to `uvicorn.error` at
  WARNING. uvicorn's access log is off.
- `configure_logging` records each logger's previous level and handlers, and `close()` restores
  them. An in-process daemon (tests) then leaves no level changed behind it.
- Logged events:
  - start (root, bind URL, pid, build);
  - the insecure-bind warning;
  - token file created (path only);
  - each authentication refusal (peer, method, path, and `missing` or `invalid`);
  - coordinator lost;
  - stop requested (signal name);
  - stopped (exit code).
- **Never logged:** the token, the `Authorization` header, request bodies, or tool arguments.
  `RequirePrincipalMiddleware` logs a fixed format with no headers. Refusals aren't rate-limited
  in the log here; the file is size-bounded, and task 6 adds limits.
- **Ready line.** Once uvicorn reports `started`, stdout gets one JSON line:
  `{"event": "ready", "url": "http://127.0.0.1:7470/mcp", "pid": N, "root": "...", "server_root": "...", "build": "7.4.0+abcdef123456", "token_file": "..."}`.
  It never includes the token. Tests and operators parse it.

## 9. Graceful shutdown

On SIGTERM or SIGINT, or `request_stop()` in tests:

1. uvicorn stops accepting connections and waits up to `GRACEFUL_SECONDS` for in-flight requests.
2. Its lifespan exit terminates the MCP streamable-HTTP transports (`fastmcp/server/http.py:605-620`).
3. The HTTP thread ends. The main thread then calls `coordinator.stop()` and `coordinator.close()`.
   That drains the admission queue (accepted writes commit), joins the writer, exporter, Linear and
   IPC threads, waits up to 5 s for IPC handlers, and releases the ownership lock under
   `publication` (`service.py:583-600`).
4. The daemon restores `AUTOSTART_ALLOWED` and the signal handlers, logs `stopped`, and returns 0.

A tool call cut off at step 1 loses only its reply. Its command, if admitted, still commits, and
the durable receipt answers a retry, which matches the existing IPC contract.
`TimeoutStopSec=30` comfortably covers 10 s, the drain and the 5 s handler wait.

## 10. Linux systemd unit and Caddy (shape only; task 16 ships them)

```ini
# /etc/systemd/system/taskmaster.service
[Unit]
Description=Taskmaster server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=taskmaster
Group=taskmaster
WorkingDirectory=/srv/taskmaster/project
ExecStart=/opt/taskmaster/.venv/bin/python /opt/taskmaster/taskmaster_cli.py serve \
    --root /srv/taskmaster/project --host 127.0.0.1 --port 7470 --allowed-host tm.example.com
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=2
KillSignal=SIGTERM
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/srv/taskmaster/project

[Install]
WantedBy=multi-user.target
```

- `ExecStart` runs the venv's Python directly rather than `uv run`. That keeps one process in the
  signal path, and the plugin's `uv run` flow stays for local use.
- `UMask=0077`, plus the `taskmaster` user owning the clone, satisfies the owner-only checks on
  `local/coordinator/` and `local/serve/`.

```
# Caddyfile
tm.example.com {
    reverse_proxy 127.0.0.1:7470
}
```

- Caddy flushes `text/event-stream` responses immediately, so streamable HTTP's SSE works without
  `flush_interval`.
- The firewall opens only 80 and 443 (addendum §13).

Client entry (teammate or operator) in `.mcp.json`:

```json
{"mcpServers": {"tm": {"type": "http", "url": "https://tm.example.com/mcp",
  "headers": {"Authorization": "Bearer ${TASKMASTER_TOKEN}"}}}}
```

In 001, only the operator token exists. Per addendum §13, no teammate key is issued before tasks
5, 6 and 15.

## 10a. Rollback

- Stop and disable the unit (`systemctl disable --now taskmaster`).
- Delete `.taskmaster/local/serve/`, which holds the token and the log.
- Revert this task's commits.

Nothing else needs undoing: no store schema, data or projection file changes, and the next local
session starts its own coordinator as before.

## 11. Known gaps this task leaves open (by design)

- **One `SESSION_ID` per process** (`backlog_server.py:628`). Every HTTP caller shares one claim
  session until task 3. Acceptable with a single operator principal.
- **The identity gate attributes team-mode writes to the host's git identity** until
  team-identity-002 and task 2 stamp the principal.
- **One client agent name per process** (review-gate M2). `backlog_server._CLIENT_AGENT` is set by
  the first MCP `initialize`, so in serve mode every caller's writes carry that client's agent.
  Task 3 makes it per connection, alongside `SESSION_ID`.
- **Tool arguments can reach the log** (review-gate M3). FastMCP logs a tool call's arguments to
  stderr on a validation error, which contradicts the §8 "never logged" list until task 6's error
  sanitizing lands. Untrusted request text this task logs itself goes through `app.log_safe`
  (review-gate I1), which escapes control characters and caps the length.
- **The viewer stays loopback-only and unauthenticated on the host** (addendum §5). Reach it over
  an SSH port-forward; on a shared multi-user host any local user could reach it.
- **An ordinary session in the host clone of the same build can still use the daemon's
  coordinator** until task 9's marker fence.
- **Error text isn't sanitized** (`service.py:750`, FastMCP's tool-error logging). That's task 6;
  until then only the operator holds a token.
- **Nothing from 001 should be exposed to the internet beyond the operator's own use** until
  tasks 5, 6 and 15 ship (addendum §13 go-live order).

## 12. Risks

- **R1. Merge overlap with team-identity-002.**
  - Shared files: the PEP 723 `dependencies` header line of `taskmaster/backlog_server.py` (and
    the root shim). That is a one-line textual conflict if 002 touches the file's top; resolve it by
    keeping 002's code and this task's header line. `service.py` and `identity_gate.py` aren't
    touched.
  - Indirect overlap:
    - `ServeCoordinator` subclasses `Coordinator` and overrides `retire` and `dispatch`. A
      signature change there in 002 needs a matching edit.
    - 002's `Middleware.on_initialize` on `bs.mcp` runs over HTTP too, and should read the
      principal through `current_principal()` once 002 rebases.
    - `client.py` (the flag and the `_retire` reason), `control_cli.py`, `ownership.py` (one
      parameter), `pyproject.toml` and `uv.lock` are untouched by 002 as briefed.
  - Rebase whichever lands second; the step tests catch the drift.
- **R2. Coupling to FastMCP and MCP SDK internals.**
  - `create_streamable_http_app` and `bs.mcp._inner` are reached outside FastMCP's documented entry
    points. The `<4` pin bounds the first, and `tests/test_server_instructions.py` already relies on
    the second.
  - D10 relies on three internals: the MCP route's `endpoint.app` being the `StreamableHTTPASGIApp`;
    its `session_manager` being built in the lifespan; and the SDK manager reading
    `session_idle_timeout` per session. `build_app` checks the first two and raises if either is
    missing. The session-expiry test catches a change in the third. The `mcp>=1.24,<2` pin bounds
    the SDK side.
- **R3. Windows can't deliver SIGTERM gracefully to a test child**, because the conftest adds
  `CREATE_NO_WINDOW` (`_windowless_test_children` in `tests/conftest.py`). The signal path is therefore tested in-process
  by calling the handler. The subprocess end-to-end test hard-stops the process on Windows and
  sends SIGTERM on POSIX. A real Linux run is task 16.
