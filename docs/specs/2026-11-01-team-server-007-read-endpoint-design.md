<!-- User intent: the task-level design for team-server-007. On a teammate's machine there is no local store, so the
     hooks and mods that read SQLite (or the local tm server) today must read the team server instead: through one
     versioned, authenticated HTTP endpoint and a small per-user disk cache, without widening what a key may read. -->

# team-server-007: versioned read endpoint and client cache (design, 2026-11-01)

**Task.** `team-server-007`. Done when the client hooks (`edit_resurface`, `merge_gate_decide`,
`merge_recorder_stamp`'s reads) and the mods read through one versioned, authenticated HTTP endpoint,
with an on-disk TTL cache in the user's cache dir, instead of opening SQLite.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §2 (version skew), §4 (client token
files), §5 (hooks, mods, merge gate, Git checks client-side), §6 (marker), §13 (deployment).
**Builds on.** 001 (`build_app`, deny-by-default `RequirePrincipalMiddleware`, D5), 002
(`Principal.key_id`, `access_token.subject`, `current_principal`), 006 (`RequestGuard`,
`PrincipalLimits`, `KeyedLimiter`, `sanitize.scrub`, D1 layer order) at feat/team-collab `748cd29`;
009's marker reader (`docs/specs/2026-11-01-team-server-009-split-brain-fence-design.md` §4-§5,
`taskmaster/server_marker.py`: `read_marker`, `server_mode`, `ServerConfigError`).
**Feeds.** 008 (merge gate fails closed; merge stamps become commands) builds on the views, error
kinds and client of this task (§13.1).
**Rulings.** Recorded in §15 (user, 2026-11-01). Review: WARN, 0 Critical / 6 Important
(`D:\scratchbook\taskmaster\ts007-review\review.md`); this revision applies I1-I6 and the Minors
marked *(M*n*)*.
**Plan.** Embedded, §18.
**Verified against.** FastMCP 3.4.3 source in the repo venv: `create_streamable_http_app` takes
`routes=` (`fastmcp/server/http.py:483`), appends them after the MCP route (`:575`), and applies the
auth middleware, `RequestContextMiddleware` (`create_base_app`, `:346`) and the caller's middleware
to every route, so a custom route sits behind 001's and 006's layers. The review confirmed from
source that `FastMCP.call_tool(..., run_middleware=True)` builds the middleware chain
(`server.py:1249-1270`) and that `get_http_request()` / `get_access_token()` resolve inside it.
Claims marked *to spike* are confirmed in Step 0 before any code depends on them.

## 1. Problem

In server mode a teammate's clone has no `.taskmaster/` store (addendum §8: code branches stop
tracking it) and 009's gate refuses to open or init one. Three hooks read `store.db` directly today,
and the mods talk to the plugin's local stdio server:

- `hooks/edit_resurface.py` opens `store.db` (`_connect_ro`, `:139`), matches `entity_paths` and
  neighbours, and writes its dedupe state and log under `.taskmaster/local/`.
- `hooks/merge_gate_decide.py` opens `store.db` (`:128`) for the project policy and the task on the
  branch, then compares the review-gate SHA with `git rev-parse` locally (`:208-240`). With no store
  it falls back to projection files (`:336`), which a teammate clone doesn't have either.
- `hooks/merge_recorder_stamp.py` opens `store.db` for the branch's task id (`:114`) and the merge
  ladder (`:151`), then writes through the local coordinator or a local queue (`:284`).
- The mods (`mods/taskmaster-mods`) open no SQLite. Every read is `$.mcp.call('plugin:taskmaster:tm',
  …)` (`register.tsx:106`, `restore/hooks.tsx:69`): eight sequential reads per refresh
  (`tm.ts:156-168`), each bounded to 3 s, plus detail, handover, phase and `backlog_query` reads. In
  server mode that local server has no store to serve, and the remote MCP server's text replies are
  produced by the host's build, not the teammate's, so the mod's text parsers meet whatever the host
  ships.

Every hook is one process per call (no connection reuse), runs under the system interpreter
(stdlib only, `test_edit_resurface_hook.py:392-410`), and sits inside a 10 s hook timeout
(`hooks.json`).

## 2. Scope

In scope:

- the read routes on the serve app: contract, versioning, auth, limits, errors (D1 to D7);
- the shared read halves of the three hooks, moved into one module used by solo hooks and by the
  server (D3);
- a stdlib client: server location through 009's marker reader, key lookup, HTTPS transport with a
  real deadline, typed failures (D8 to D10);
- the per-user disk cache: location, permissions, layout, TTLs, staleness, negative entries (D11 to
  D13);
- the hooks switched to the client in server mode; the mods' reads switched in server mode through a
  small CLI (D14);
- a `taskmaster remote set-key` command to place a key safely (D9).

Out of scope:

| Not here | Owner |
|---|---|
| Merge gate fails closed while the server is unreachable; the bypass text | 008 (007 keeps today's fail-open, §13.1) |
| Merge stamps sent to the server as commands | 008 (007 reads the target only) |
| The marker's name, format, writer, reader and latch; the gate refusing local stores | 009 (007 only calls its reader, §13.2) |
| Per-principal, per-clone `branch`/`worktree` fields | 011 (007's views take the principal so 011 changes no contract, D3a) |
| Mod writes (claim, complete, handover create) reaching the remote MCP server | follow-up task (ruling Q5) |
| Mods in desktop-app sessions in server mode | follow-up task (ruling Q6; offline until then, G1) |
| Content-based imports | 012 |
| Export commits | 013 |
| Caddy, systemd, how-to, Linux lane | 016 |
| Security review of the exposed surface | 015 |

## 3. Threat model

| Attacker | Wants to | Stopped by |
|---|---|---|
| Anyone on the internet, no key | read the backlog through `/read/` | 001 D5: every route needs a principal, 401 before any body read; 006 D6 failure budget |
| A member key | read more than its MCP tools give it: other principals' receipts or sessions, host files, the operator token, `principals.json` | views are built from rows MCP read tools already return; `tools` runs only allowlisted read tools through FastMCP's own call path under the caller's principal (D7) |
| same | read host-private bookkeeping through `backlog_query` (`sessions` holds host paths, host names, PIDs; `linear_queue` payloads and errors; `projection`), and have it cached on teammates' disks | on the read endpoint `backlog_query` runs under a stricter authorizer that refuses those tables (D7) |
| same | write through the read endpoint | views are `GET`; `tools` runs a closed read-only allowlist, and a ratchet test fails on any tool that sends a coordinator `execute` (D7) |
| same, or a runaway mod | flood heavy reads (`backlog_query` costs a full snapshot, `overview.py:75`) | one rate token per tool call in a batch, at most 16 calls, a server-side batch time budget, 006's in-flight cap (D4, D6) |
| same | learn host paths or SQL from errors | errors go through 006's `scrub` with a `ref` (D5) |
| A teammate whose branch name collides with another's | have their merge judged by someone else's review gate | the gate views take the caller's principal, prefer the caller's task, and answer `ambiguous` instead of picking one (D3a) |
| A copied bootstrap token used off-host | read as the operator | 002 D7: 403 before the route |
| A branch or PR that edits the tracked marker to `https://attacker.example` | collect a teammate's or CI's key | a key file is per canonical origin, so an unknown origin has none; the env key is sent only when `TASKMASTER_SERVER_URL` names the same canonical origin (D9) |
| Another local user on a teammate's machine | read the key or cached data | key file and cache dir are verified owner-only; a link or foreign owner disables them (D9, D11) |
| A network attacker | read or alter replies, steal the key | HTTPS only (plain `http` only to a loopback literal, never through a proxy), system trust store, no "insecure" switch; redirects refused so the key never follows a 3xx to another host (D10) |
| A slow or hostile network path (DNS stall, header drip) | hold a hook past its 10 s kill, which in 008 would turn a fail-closed gate into a hook error | the whole request runs in a worker thread joined against an absolute deadline (D10) |
| A stale cache entry | let a merge through after a verdict was revoked | `merge-gate` is never cached and never fails fast from a negative entry (D12, D13) |
| A teammate on an older plugin build | misread a newer reply | major version in the path, additive-only inside a version, unknown fields ignored (D2) |
| Malware running as the teammate | anything | out of scope: it can read `~/.claude.json`, which holds the same key for MCP |

## 4. Consumer inventory

| Consumer | Reads today | Fields it decides on | Server-mode view | Cache (D12) | On failure in 007 | 008 changes |
|---|---|---|---|---|---|---|
| `edit_resurface` (PostToolUse Edit/Write) | `entity_paths` + `entity_core` status, neighbours, `revision` | per path: open listed `(kind, id, status)`, counts of closed, prose, related; the revision for dedupe | `paths?path=` | 30 s fresh, 10 min stale | quiet, one log line (today's rule) | none |
| `merge_gate_decide` (PreToolUse Bash, via `merge_gate.py`) | project `conventions.policies.review_gate_required_for_merge`; the live task with `branch == src`: `skip_merge_gate`, `merge_gate_freshness`, `gates.review-gate.{verdict, commit_sha}` | the verdict; `git rev-parse src` stays client-side (addendum §5, Git checks) | `merge-gate?branch=` (principal-aware, D3a) | never cached | ALLOW + log, exactly as today | every non-fresh outcome and `ambiguous` block |
| `merge_recorder_stamp` (PostToolUse Bash, detached) | task id for `branch == src`; native merge ladder | the task id and the rung for the target branch | `merge-target?branch=&target=` (principal-aware) | 5 min fresh, 24 h stale | log "stamp not recorded: server mode stamps land in 008"; no local write | sends the stamp as a command; the server resolves the rung (§13.1) |
| mods refresh (`fetchSnapshot`) | 8 tool calls: `list_tasks`, `issue_list` ×2, `continuity_items` ×2, `handover_list`, `get_task`, `task_pipeline` | the tool texts its parsers read | `tools` (one batch) | 10 s fresh, 10 min stale, `--fresh` after the mod's own write | the existing offline/transient fault path | none |
| mods detail, handover summary/slim, phases, linked tasks, restore refs | `get_task`, `handover_get`, `backlog_query`, `handover_list` | same | `tools` (one-call batch) | same | same | none |
| mods writes (`actions.ts`, `binding.ts`, `handover-guard.ts:315`) | — | — | not a read; follow-up (Q5) | — | — | — |
| `merge_gate.py`, `merge_recorder.py`, `taskmaster_merge_approve.py`, `worktree_submodule_init.py` | no store read | — | — | — | — | — |
| `session-start.sh` | detects `.taskmaster/` only | — | none; 009 makes it recognise the marker (009 §8) | — | — | — |
| viewer | served by the host (006 D10) | — | — | — | — | — |

## 5. Endpoint contract and versioning

**D1. One prefix, fixed: `/read/v1/`, on the same listener as MCP.** It is independent of
`--path`; `config.validate` refuses a `--path` under `/read`. Caddy already proxies every path
(006 §9), so 016 adds nothing. Routes:

| Method and path | Parameters | `data` |
|---|---|---|
| `GET /read/v1/meta` | none | `{"supported": [1], "build": "<describe(build)>", "views": [...], "tools": [...]}` |
| `GET /read/v1/paths` | `path`: repo-relative, `/`-separated | `{"path", "listed": [{"kind", "id", "status"}], "closed", "prose", "related"}` |
| `GET /read/v1/merge-gate` | `branch` | `{"policy_required": bool, "ambiguous": bool, "candidates": [id, …], "task": null \| {"id", "mine": bool, "skip_merge_gate": bool, "merge_gate_freshness": "strict" \| "any", "review_gate": null \| {"verdict", "commit_sha"}}}` |
| `GET /read/v1/merge-target` | `branch`, `target` | `{"ambiguous": bool, "candidates": [id, …], "task_id": str \| null, "rung": str}` (a ladder rung, else `branch:<target>`, as `merge_recorder_stamp.py:254`) |
| `POST /read/v1/tools` | body `{"calls": [{"tool", "args": {}}]}` | `{"results": [{"tool", "text", "is_error"}]}`, in call order |

**D2. Versioning.**

- The major version is in the path. Inside v1, changes are additive only: new views, new optional
  parameters, new fields. Clients ignore unknown fields. Removing or renaming a field, or changing
  its meaning, is v2, and the server then serves v1 and v2 side by side for at least one minor
  release so teammates can upgrade at their own pace.
- An unknown version (`/read/v2/…` on a v1 server) answers 404
  `{"v": 1, "error": {"code": "unsupported-version", "supported": [1], …}}`. A pre-007 server answers
  Starlette's plain 404. The client maps both to the failure kind `version` (D10).
- Every success is an envelope:
  `{"v": 1, "view", "store_id", "revision", "generated_at", "data"}`. `revision` is
  `hook_reads.revision` (event high water plus graph repairs; it only grows for one store);
  `store_id` changes only if the host's store is replaced (migration, restore), and the cache drops
  everything for a server whose `store_id` changed (D12). For `tools` the revision is read **before**
  the first call runs, so the stamp is never newer than the data under it *(M12)*.
- `tools` results carry each tool's text exactly as MCP returns it (a `structured_content`
  `{"result": text}` is unwrapped first). The text format is the tool's own contract, the same one
  the mods parse over MCP today; v1 versions the envelope, not tool prose. Typed mod views are a
  later, additive change if skew bites.

**D3. One read implementation for solo and server.** The read halves of the three hooks move into
`taskmaster/native_routing/hook_reads.py` (upgraded in place; it is already the hooks' native read
module): `paths_view(con, rel)`, `merge_gate_view(con, branch, principal=None)`,
`merge_target_view(con, branch, target, principal=None)`. Each takes an open read connection,
handles both native and legacy stores exactly as the hooks do today, and returns the v1 `data` dict.
Solo hooks call them on their local `store.db` with `principal=None`; the server calls them on the
host store with the request's principal. The wire schema is therefore exercised by every solo hook
test, and the server cannot drift from the hooks. The functions stay stdlib plus `taskmaster.native`,
as `hook_reads` is now. `merge_target_view` resolves the rung on the host with
`taskmaster_v3.rung_for_branch`, imported lazily as the solo recorder already does
(`merge_recorder_stamp.py:231`), so the client side needs no ladder logic.

**D3a. Choosing the task on a branch.** `branch` is one shared field on a team server, and names
collide (`fix/login`, `wip`). `hook_reads.tasks_for_branch(con, identity, branch)` returns every live,
unarchived task on the branch, ordered by id. Then:

- `principal=None` (solo): the first by id. Today's behaviour, unchanged.
- With a principal (server): a candidate is the caller's when its `owner` equals the caller's handle
  or its live claim's `locked_by` session belongs to the caller's handle (003's session shape
  `<handle>@<16hex>`).
  - Exactly one candidate in all: that task, with `mine` saying whose it is, so 008 can word a block
    on someone else's task.
  - Several: the caller's, if exactly one of them is the caller's.
  - Otherwise `ambiguous: true`, `task: null` (`task_id: null`), and up to 10 `candidates`. Never the
    first by id: on a server that would judge one teammate's merge by another's gate.
- 011 moves `branch` into a per-principal, per-clone table. The views already take the principal,
  so 011 changes how candidates are found, not the wire contract.

**D4. Host-side reads.**

- Each view request opens `protocol.connect(root, readonly=True)` (the daemon's own read path,
  `daemon.py:279`), begins a read transaction, admits it with `hook_reads.admit`, and runs the view,
  on a worker thread (`starlette.concurrency.run_in_threadpool`) so SQLite never blocks the event
  loop. No view goes through the coordinator.
- `tools` calls `FastMCP.call_tool(name, args, run_middleware=True)` on `bs.mcp._inner`,
  sequentially, because the server answers one call at a time and parallel calls contend
  (`mods-tm-offline-codemaestro`). *To spike (Step 0):* that 006's `SanitizeErrors` and 005's tool
  gate run, and that `get_http_request()` and `get_access_token()` inside the tool resolve to the read
  request and its principal (the review found the source supports both).
- **Batch time budget** *(M8)*: 9 s from the batch's start. A call not started by then answers
  `{"is_error": true, "text": "Error: not run: batch time budget"}`. Between calls the route checks
  `request.is_disconnected()` and stops when the client has gone.

## 6. Auth, limits and body handling

**D5. Every layer 006 D1 lists applies unchanged**, because the routes are passed to
`create_streamable_http_app(routes=…)` through a new `build_app(routes=…)` parameter: `RequestGuard`
(size cap, body deadline, lingering close, request id), `HostOriginGuard`, bearer auth,
`RequirePrincipalMiddleware` (401 before any `receive`; operator token off-host 403),
`PrincipalLimits`. The route adds:

- `GET` routes never call `receive`. A `GET` with `Content-Length` > 0 or `Transfer-Encoding` gets
  400 without reading it.
- `POST /tools`: `Content-Length` is required and at most 64 KiB (`READ_BATCH_MAX_BYTES`), else
  411/413 before reading; the body must be JSON with 1 to 16 calls (`READ_BATCH_MAX_CALLS`), each
  `args` a JSON object of at most 16 KiB serialized. The combined `text` of a batch reply is capped
  at 3 MiB; past it, the remaining results are `Error: not run: reply size budget`, so the CLI's
  output stays under the mod runtime's 4 MiB read *(M10)*.
- Parameters are validated before any store access: `branch` and `target` match a Git ref-name
  subset (`[A-Za-z0-9._/@+-]`, no `..`, no leading `-` or `/`, at most 255 characters); `path` is
  `/`-separated, relative, no `..` segment, no backslash, no NUL, no leading `/` or drive, at most
  1024 characters. A bad value is 400 `bad-request` naming the parameter, never echoing it. The
  ref subset refuses some legal branch names (`#`, non-ASCII); 008 words that block *(M12)*.
- Errors are `{"v": 1, "error": {"code", "message", "ref"}}` with `code` one of `bad-request` (400),
  `unknown-view` / `unsupported-version` (404), `store-unavailable` (503, the store can't be opened
  or admitted), `internal` (500). Messages go through `sanitize.scrub(text, root)`; an internal
  error logs the traceback with the ref, as 006 D9 does for tools.
- Every response carries `Cache-Control: no-store` (no proxy or browser keeps it) and 006's
  `X-Request-Id`.

**D6. Cost.** `PrincipalLimits` takes one token per HTTP request (006 D7). A `tools` batch then takes
`len(calls) - 1` more from the same limiter before running anything, so the 006 invariant "one
token is roughly one tool call" holds whether a mod batches or not. If the bucket can't cover the
batch now, the whole batch is 429 with `Retry-After`. A batch whose cost exceeds the bucket's
capacity (`--rate-burst` can be 1) can never succeed, so it is 400 `bad-request` "batch of N calls
exceeds the rate burst B" instead of a 429 the client would retry forever *(M7)*. This needs
`KeyedLimiter.take(key, cost=1)` (upgraded in place in `serve/limits.py`; `cost=1` keeps every caller
unchanged) and `KeyedLimiter.capacity`, which exists. Views cost one token each.

## 7. Per-principal data scoping

**D7. A key reads through the endpoint what it could read through its MCP read tools, minus the
host's private bookkeeping.**

- **Entity data is team-shared by design.** The server holds one team store, and every member's MCP
  read tools already see every entity. There is no per-entity ACL to enforce, and the views add none.
- **Typed views return only fields an MCP read tool returns for the same entity:** statuses, ids,
  gate verdicts and SHAs, the merge rung, and whether a candidate task is the caller's. They return
  repo-relative paths only (the `path` the caller sent), never a host path.
- **`tools` runs a closed allowlist** (`READ_TOOLS` in `serve/read_api.py`): `backlog_list_tasks`,
  `backlog_issue_list`, `backlog_continuity_items`, `backlog_handover_list`, `backlog_handover_get`,
  `backlog_get_task`, `backlog_task_pipeline`, `backlog_query`. It is exactly the set the mods read
  (§4). Each call goes through FastMCP's own tool-call path under the request's principal, so 005's
  tool gate and Part B docs-path rule, 004's scoping and 006's sanitizer apply as they would over
  MCP; the route re-implements none of them. A tool name outside the list answers 400 `bad-request`
  (`tool not readable here`) without running.
- **`backlog_query` is stricter here than over MCP** (orchestrator ruling on review I6). In native
  mode it materializes every `query_guard.TABLES` entry (`native/compatibility.py:24-45`), including
  `sessions` (host paths, host names, PIDs), `linear_queue` (payloads, last errors, claimants) and
  `projection` (host file bookkeeping). On the read endpoint those three are refused, because the
  answer would otherwise also be cached on teammates' disks.
  - Mechanism: `query_guard.Authorizer` takes `readable=` (default `READABLE_TABLES`, so MCP is
    unchanged), and a `ContextVar` `query_guard.READABLE` that the read route sets around each
    `tools` call (`with query_guard.restricted(READ_ENDPOINT_TABLES):`). `overview.query` builds its
    authorizer from it. The variable travels into FastMCP's worker thread with the copied context;
    MCP requests never set it.
  - `READ_ENDPOINT_TABLES = READABLE_TABLES - {"sessions", "linear_queue", "projection"}`. It keeps
    `entities`, `entity_paths`, `links`, `related`, `handover_tasks`, the FTS tables and `changes`
    (which `backlog_changes_since` already serves to members). The mods read only `entities` and
    `handover_tasks`.
  - A refused table answers the tool's own `Error: not authorized: <table>` shape. The schema summary
    appended to errors is unchanged. Exposure of those tables over MCP itself is filed for 015/004.
- **Never readable here:** receipts, request queues, the `sessions`, `linear_queue` and `projection`
  tables, any file under the host root outside 005's tracked-only rule, the operator token,
  `principals.json`, `discovery.json`. Two ratchets: every `READ_TOOLS` tool, run against a stub
  client, sends no coordinator message; and the parity test (endpoint text equals MCP text for the
  same key) covers every allowlisted tool, with the `backlog_query` host-table refusals as its one
  named exemption.
- **The operator** reads like any principal; its token works only host-local (002 D7).

## 8. Client: locating the server, the key, the transport

All client code is stdlib only and lives in a new package `taskmaster/remote/`
(`locate.py`, `keys.py`, `dirs.py`, `cache.py`, `client.py`, `cli.py`), importable by hooks under
the system interpreter. `test_packaging.py` and the hooks' import tests extend to it.

**D8. Locating the server.**

- `root` is `taskmaster.root.resolve_root(cwd).root`, the rule every hook already uses (and 009's
  marker location, 009 §4.1).
- Server mode is decided by **009's `server_marker.server_mode(root)`** (ruling Q2): the marker at
  `<root>/.taskmaster-server.json`, or, when it is absent, the latch in `<root>/.git/`. A latched
  clone (an old commit checked out) is therefore still a server clone, never solo by accident.
  `ServerConfigError` (a present but unreadable or invalid marker) is the failure kind `config`.
- **Hooks call `server_mode(root)` first and import `taskmaster.remote` only when it returns a
  marker** *(M2)*. `server_marker` is stdlib-only and lives outside `remote/`, so the solo path never
  imports the client (Step 8 asserts it).
- `remote.locate(root, marker) -> ServerRef(url, origin, read_base, server_id)`. `origin` is the
  **canonical origin** *(M3)*: scheme and host lowercased, the host IDNA-encoded, the default port
  (443, 80) dropped, no path. `read_base` is `origin + "/read/v1/"` (the marker names the MCP URL).
  `server_id` is `sha256(origin)[:16]`, the name of the key file and the cache directory. `set-key`
  canonicalizes the same way, so `https://Host:443/mcp` and `https://host/mcp` name one server.
- Not server mode and `root/.taskmaster/` is a directory: solo. Neither: no project.

**D9. The key** (ruling Q3). Looked up in this order:

1. `TASKMASTER_SERVER_KEY` in the environment, **only when `TASKMASTER_SERVER_URL` is also set and
   its canonical origin equals the marker's** *(review I1)*. The marker is tracked, so any branch or
   PR can point it at another origin; an env key that isn't bound to an origin would follow it. A
   missing or different `TASKMASTER_SERVER_URL` is `no-key` ("TASKMASTER_SERVER_KEY is bound to
   <origin A>, but this clone's marker names <origin B>; nothing was sent"), and no request is made.
   Documented for CI as the less safe option.
2. `<user config dir>/taskmaster/keys/<server_id>.key`: Windows `%APPDATA%\taskmaster`, macOS
   `~/Library/Application Support/taskmaster`, else `$XDG_CONFIG_HOME/taskmaster` or
   `~/.config/taskmaster`. Its directory is owner-only and the file is checked with
   `ownership.verify_private` (addendum §4). A link, a foreign owner or loose permissions is refused
   with a message naming the fix. An origin nobody ran `set-key` for has no file, so a hostile marker
   gets `no-key`.

The key must match 002's member format (`tm_u_<key_id>_<secret>`); an operator token (`tm_op_`) is
refused on the client because it works only on the host. `key_id` is read from the key's own prefix
and used to scope the cache (D11); rotation mints a new `key_id` (`principals.py:96`), so two keys
never share a cache directory. `taskmaster remote set-key --url <mcp url>` reads the key from stdin
(never argv, so it stays out of shell history and process lists), validates it, and writes it
owner-only. `ownership.private_directory` is generalized in place (`private_directory_at(path)`, with
the existing `private_directory(root, name)` calling it) rather than copied; the review measured a
re-apply on an existing directory at about 0.5 ms, so per-hook calls are affordable.

**D10. Transport.** `client.read(server, view, params, *, policy, deadline)`:

- **A real whole-request deadline** *(review I2)*. The deadline is absolute, and for hooks it is
  `min(now + budget, process_start + 8 s)`, so a hook always answers before the host's 10 s kill
  (interpreter start under load measured 5-7 s, §16 R5). The whole request (DNS, connect, TLS,
  status line, headers, body) runs in a daemon worker thread; the caller `join`s it with the time
  remaining. On expiry the caller raises `ReadUnavailable("unreachable", "no answer within N s")`
  and abandons the thread. Hooks and the CLI exit right after, so an abandoned thread never outlives
  its process for long. The per-operation socket timeout (the remaining time) stays as a second line,
  and the body is read in chunks with the deadline checked after each.
- Budgets: `paths` 2 s, `merge-gate` 5 s, `merge-target` 4 s (the recorder's current-stamp budget,
  `merge_recorder_stamp.py:278`), `tools` 10 s, each capped by the hook's absolute limit above.
- `urllib.request` with an opener that has **no redirect handler** (a 3xx is `bad-response`),
  because urllib copies request headers, `Authorization` included, onto a redirected request, even to
  another host (*to spike*).
- `https` only, verified with `ssl.create_default_context()` against the system store; there is no
  option to turn verification off. Plain `http` only when the host is a **loopback literal**
  (`127.0.0.0/8`, `::1`), never the name `localhost`, which a hosts file can point elsewhere
  *(M5)*, matching 009's marker rule. Loopback requests use an opener with `ProxyHandler({})`, so an
  `HTTP_PROXY` variable can't carry the key through a proxy in plaintext *(M4)*. HTTPS requests keep
  the environment's proxies: TLS stays end to end through a CONNECT proxy.
- Replies are capped (256 KiB for views, 4 MiB for `tools`) and must be `application/json`.
- Requests send `Authorization: Bearer <key>`, `Accept: application/json`,
  `User-Agent: taskmaster-remote/<plugin version>`. Nothing is logged with the key or the header.
- **Outcomes** are one `ReadResult(data, revision, store_id, source, age_s)` with `source` one of
  `"server"`, `"cache"`, `"stale"`, or one exception `ReadUnavailable(kind, message,
  retry_after=None)`:

| `kind` | When | Served stale? |
|---|---|---|
| `config` | marker or latch unreadable (D8) | no |
| `no-key` | no key, env key not bound to this origin, or the key file refused (D9) | no |
| `unreachable` | DNS, connect, TLS, the deadline, a reset, 408 | per view policy |
| `refused` | 401 or 403 | no |
| `busy` | 429 (with `retry_after`) | per view policy |
| `version` | unsupported-version 404, or a plain 404 from a pre-007 server | no |
| `server` | 5xx, `store-unavailable` | per view policy |
| `bad-response` | 3xx, wrong content type, oversize, invalid JSON, `v` not 1, missing envelope fields | per view policy |
| `bad-request` | 400, 411, 413, 421 (Host guard), 431 (a client or config bug; logged with the ref) | no |

*(M6, M11.)*

## 9. Cache

**D11. Where, and who can read it.**

- `<user cache dir>/taskmaster/remote/<server_id>/<key_id>/`: Windows `%LOCALAPPDATA%\taskmaster`,
  macOS `~/Library/Caches/taskmaster`, else `$XDG_CACHE_HOME/taskmaster` or `~/.cache/taskmaster`.
  `TASKMASTER_CACHE_DIR` overrides it (tests, CI).
- Every directory from `taskmaster/remote/` down is created owner-only (POSIX 0700; on Windows the
  owner-only protected DACL `ownership.py:85` already sets, inherited by children) through the
  generalized `private_directory_at`. A link, a junction or a foreign owner anywhere on that path
  **disables the cache** for the run: reads go straight to the server, one log line says why. The
  cache is an optimization; it never decides safety.
- Files are written to a private temp file (`O_CREAT | O_EXCL`, 0600) and published with
  `os.replace`. A Windows sharing violation on replace skips the write (best effort, logged once).
- Keying by `key_id` means two keys on one machine never share entries.

**D12. What is stored, for how long.**

- One JSON file per view and parameter set:
  `v1/<view>/<sha256(canonical params)[:32]>.json` holding `{"stored_at", "store_id", "revision",
  "view", "params", "data"}`. **No secrets:** never the key, the `Authorization` header, any
  response header, or cookies. A test runs every view and greps the cache tree for the key and its
  secret part. Host-private tables never reach the cache, because the endpoint refuses them (D7).
- Policy per view, in the client (`CACHE_POLICY`), because the consumer knows what staleness costs:

| View | Fresh for | Served stale when the server fails | Why |
|---|---|---|---|
| `merge-gate` | 0 (never read from cache, never written) | never | a gate must see the latest verdict; 008 fails closed on anything not fresh |
| `merge-target` | 300 s | up to 24 h | the ladder and a branch's task rarely change; once 008 sends stamps, the server resolves the rung itself, so a stale rung is only a log line (§13.1) |
| `paths` | 30 s | up to 10 min | advisory; a burst of edits to one file costs one request |
| `tools` | 10 s, skipped by `--fresh` | up to 10 min, flagged `stale` | collapses refreshes from several sessions on one machine; the mod asks `--fresh` after its own write so it never draws its write undone |
| `meta` | 300 s | no | version probe |

- Stale is served only for the kinds the D10 table marks "per view policy". `config`, `no-key`,
  `refused`, `version` and `bad-request` never serve stale *(M11)*.
- `age = now - stored_at`; a negative age (clock moved back) counts as expired.
- **Don't regress, best effort** *(M9)*: an entry is replaced only by a reply of the same store with
  a revision not older than the one on disk. The check reads then replaces, so two writers racing can
  still leave the older one; the writer re-reads after `os.replace` and retries once if it lost. The
  cache never decides safety, so a rare regression costs at most one stale advisory line. A reply with
  a new `store_id` empties `<server_id>/` first.
- **Bounded:** each write prunes entries in its view directory older than 24 h, keeps at most 512
  files per view, and keeps the whole `<server_id>/` under 64 MiB, oldest first *(M10)*. This is the
  same scan-on-write `edit_resurface.record_seen` uses.
- A corrupt or unreadable entry is a miss and is deleted.

**D13. Negative entries**, so an offline laptop or a revoked key doesn't make every edit wait or
spend the office's failure budget:

- **`merge-gate` ignores every negative entry and always tries** *(review I4)*: a user who
  reconnects, or whose key list blipped, and retries a merge is never refused from memory.
- **Down.** After `unreachable`, `<server_id>/down.json` holds `{"until": now + 15}`. Inside it,
  cacheable views fail fast (`unreachable`, "server unreachable 4 s ago") and serve stale where
  their policy allows.
- **Refused, two strikes** *(review I4)*. One 401 isn't proof: `KeyAuthenticator` answers 401 for a
  valid key while `principals.json` is briefly unreadable (`serve/auth.py:174-180`), for example
  during `add-user` or `rotate-user`. So:
  - the first `refused` writes `<server_id>/<key_id>/refused-pending.json` with `first_at`, and fails
    only that request;
  - a second `refused` at least 5 s and at most 120 s later writes `refused.json` with the key file's
    `(mtime_ns, size)` and `until: now + 60`;
  - while `refused.json` holds, every view but `merge-gate` fails fast. It clears on expiry, on a
    change to the key file or the env key, or after any success;
  - a confirmed refusal deletes the `<key_id>/` entries apart from the negative entries, since that
    key's data shouldn't outlive it on disk *(M11)*.
  - Follow-up for 002/006, not 007: answer 503 when the registry is unavailable, so a blip isn't a
    401 at all.
- **The text for a refusal says what is known.** "The server did not accept this key: it may have
  been revoked or rotated, or the server's key list was briefly unavailable. If the operator gave you
  a new key, run `taskmaster remote set-key`." It doesn't tell the user to re-enter a key that may be
  fine.

**Hook state in server mode moves here too.** `edit_resurface`'s `hook-seen/` and every hook's
`hook.log` go to `<server_id>/hooks/` instead of `.taskmaster/local/`, because a teammate clone has
no `.taskmaster/`, and creating one would make `session-start.sh` and 009's checks see a local
project. A test asserts that no `.taskmaster/` appears in a server-mode clone after every hook runs.

## 10. The mods

**D14. In server mode the mods read through the same client, as a subprocess, one batch per
refresh** (ruling Q1).

- **Detection costs nothing in solo:** once per root per session, `$.fs.exists(<repo
  root>/.taskmaster-server.json)` and, when that is false, `$.fs.exists(<repo
  root>/.git/taskmaster-server.json)` (009's latch). `$.fs.exists` is declared in the mods API types
  (`claude-code/index.d.ts:3240` in the laid types), but no mod uses it yet, so Step 0 confirms it
  at runtime *(M1)*. If it doesn't work, the fallback is one `tm_read.py mode --root <root>` process
  per root per session, cached in `$.state`. Solo then pays one process per session, never one per
  refresh, and the MCP calls are unchanged.
- No marker and no latch: everything stays as it is today, call for call (D15).
- **Server mode:** a new seam `TmHost.callMany(calls)` replaces the reads. In solo it is the existing
  sequential `call` loop. In server mode it runs `python -I <plugin>/hooks/tm_read.py tools --root
  <repo root> [--fresh]` with the calls on stdin.
- **Interpreter choice** *(review I3)*. The PYTHONS order (`python`, `python3`, `py`) is extracted
  from `restore/scan.ts:22-47` into a shared `hooks/python.ts`, and the fallthrough rule changes for
  both users: it moves to the next interpreter **only when this one didn't start**. That means
  `$.process.run` rejected well before the timeout, or the exit code is 9009 (Windows: not found, or
  the Microsoft Store alias) or 127 (POSIX: not found). A timeout, or any other exit, **stands**: the
  script ran, and `tm_read.py` always exits 0 with JSON (Step 9). A slow server therefore costs one
  process and one batch of tokens per refresh attempt, never three. The restore scanner gets the
  same rule: a scanner crash is reported, not retried under another interpreter.
- `fetchSnapshot` sends its 8 reads as one `callMany`. The single reads (`loadDetail`,
  `readSummary`, `readHandover`, `readPhases`, `readLinkedTasks`, `resolveRefs`) send one-call
  batches. The parsers, `isRefusal`, `replyText` and the `{"result"}` unwrapping are untouched: the
  texts are the tools' own.
- **Bounds:** the process gets `TM_REMOTE_TIMEOUT_MS = 15_000`, because interpreter start alone
  measured 5-7 s under load (§16 R5). The CLI's own network deadline is 10 s from its start. A
  refresh never blocks a turn (`register.tsx:324`), so the longer bound costs only freshness. The
  CLI prints `elapsed_ms` (its own run time). When the process's wall time is more than 3 s longer
  than that, the mod logs "slow Python start (N s)" and words a timeout as "Python is slow to start"
  rather than "server unreachable".
- **Faults.** `unreachable`, `busy`, `server` → transient (the 2/4/8/16 s retry ladder,
  `refresh.ts:8`). `refused` → offline with D13's refusal text. `no-key`, `config`, `version` →
  offline with the client's own message (for example "no key for https://tm.example.com; run
  taskmaster remote set-key"). `bad-response` → unreadable. A `stale` result draws its data with the
  offline fault, so a new session opened offline still shows the last known queue.
- **Host paths in tool texts.** `parseHandovers(text, root)` reads handover file paths relative to
  the session's repo root; in server mode they are the host's paths (G4). Step 10 checks that the
  pane degrades (a path it can't relativize is shown as given, or not shown) rather than
  mis-rendering *(M12)*.
- **Why a subprocess and not `$.http.fetch`:** one client, one cache and one key lookup for hooks
  and mods; the key never enters the mod runtime; and running a Python helper from the mod is
  already the restore pane's pattern.
- **Rate:** a refresh batch is 8 tokens (D6), as eight MCP calls are today; the 10 s cache shared by
  every session on the machine is what lowers it.
- Plugin version bump for `taskmaster-mods` and the gruku-tools `marketplace.json` entry, per the
  release checklist.

## 11. Solo mode is unchanged

**D15.** Without a marker or latch:

- The hooks read their local `store.db` through the moved `hook_reads` view functions (with
  `principal=None`, so the first task by id, as today), with the same queries, the same fail-open
  rules, the same logs and dedupe files in `.taskmaster/local/`. `taskmaster.remote` is never
  imported on that path (D8; a test asserts it).
- The mods make the same MCP calls in the same order (`callMany` in solo is the old loop; the mod
  suite's existing call-order assertions are the check). Marker detection is `$.fs.exists`, no
  process (D14).
- The read routes exist only in `taskmaster serve`; the solo stdio server and the solo viewer gain
  nothing.

## 12. Failure modes

| Condition | `edit_resurface` | `merge_gate_decide` (007) | `merge_recorder_stamp` (007) | mods |
|---|---|---|---|---|
| no marker | today (solo) | today | today | today |
| marker or latch unreadable (`config`) | quiet, log | ALLOW, log (008: BLOCK) | log, no stamp | offline, reason |
| no key, env key not bound to this origin, key file refused | quiet, log | ALLOW, log (008: BLOCK naming `set-key`) | log | offline, reason |
| unreachable, deadline | stale ≤ 10 min, else quiet | ALLOW, log (008: BLOCK "Taskmaster server unreachable; retry or bypass explicitly") | stale target, log "not recorded" | transient → retries → offline; stale drawn |
| one 401/403 | quiet; refused-pending | ALLOW, log (008: BLOCK "key not accepted") | log | transient for that refresh |
| second 401/403 within 5-120 s | quiet; refused for 60 s | still tries every time (D13) | log | offline, D13 refusal text |
| 429 | stale, else quiet | one retry if `Retry-After` ≤ 2 s fits the deadline, else ALLOW (008: BLOCK) | stale, log | transient |
| version | quiet, log | ALLOW, log (008: BLOCK "upgrade the taskmaster plugin") | log | offline "server read API is v…; update the plugin" |
| 5xx, `store-unavailable` | stale, else quiet | ALLOW, log (008: BLOCK) | stale, log | transient |
| bad response | stale, else quiet | ALLOW, log (008: BLOCK) | stale, log | unreadable |
| `ambiguous` | n/a | ALLOW, log naming the candidates (008: BLOCK "several tasks use branch X; set the task's branch") | log, no stamp | n/a |
| fresh reply | the line, deduped by revision | today's verdict logic on the view's fields with local `git rev-parse` | target read; "stamps land in 008" log | drawn |

In 007 every hook outcome keeps today's fail-open discipline, so nothing gets stricter or looser
before 008. That window is safe because 008 is in the go-live set (ruling Q4): no teammate key is
issued before it merges.

## 13. Seams and files

### 13.1 With 008 (merge gate fails closed, stamps as commands)

007 hands 008 a finished read path, so 008 changes decisions, not plumbing:

- `merge_gate_decide.decide` in server mode calls `client.read(server, "merge-gate", {"branch":
  src}, policy=NO_CACHE, deadline=…)` and then today's `_verdict` on the returned task. Every
  `ReadUnavailable` kind and `ambiguous` go through one function, `on_remote_unavailable(kind,
  message) -> str`, which returns `"ALLOW"` in 007. **008 changes only that function** to return
  `BLOCK:-: <text per kind>` (the existing `merge_gate.py` approval flow is the explicit bypass), and
  adds its tests. The `ReadUnavailable.kind` set (D10) plus `ambiguous` is the contract; 008's tests
  pin one message per kind. `task.mine` lets 008 say whose task is gating the merge.
- `merge-gate` data carries everything `_verdict` reads, so 008 never needs a second read. Git
  ancestry stays client-side. The 8 s absolute hook deadline (D10) is what keeps an overrun from
  becoming a hook kill, which 008 would otherwise see as ALLOW.
- `merge_recorder_stamp.stamp` in server mode reads `merge-target` and ends at
  `_send_remote_stamp(root, server, task_id, rung, sha, merged_at)`, which in 007 only logs. 008
  fills it with the command and its client-side queue (the queue moves to the cache dir, D12). 008
  should let the server resolve the rung when it applies the stamp, so the 24 h stale rung is never
  authoritative *(M12)*.
- `remote.locate`, the key lookup and the error kinds are shared; 008 adds no client code.

### 13.2 With 009 (split-brain marker)

- 009's design is written and settles the marker (ruling Q2): `<root>/.taskmaster-server.json`,
  JSON, keys exactly `format`, `host_id`, `mode`, `url`, validated by `server_marker.parse`; a latch
  in `<root>/.git/`; `read_marker(root)` and `server_mode(root)` raise `ServerConfigError` on a
  present but invalid file (009 §4.2, §5).
- 009 owns `taskmaster/server_marker.py`. 007 calls `server_mode(root)` and uses only `url`. 007's
  Step 4 starts after 009's Step 1 is merged into the integration branch. If it isn't, 007 adds the
  module with exactly 009 §4.2's `parse`/`read_marker`/`server_mode` contract and its Step 1 tests,
  and 009 extends it in place.
- 007 never touches `native_routing/gate.py` or `root.py`. `session-start.sh` recognising the
  marker is 009's (009 §8).
- `ownership.py`: 009 adds a lock-file name parameter to `Ownership`/`ownership_held`; 007 adds
  `private_directory_at`. Different functions; a textual rebase only (009 §13).

### 13.3 Files

New:

- `taskmaster/remote/__init__.py`, `locate.py`, `keys.py`, `dirs.py`, `cache.py`, `client.py`, `cli.py`
- `taskmaster/serve/read_api.py` (routes, envelope, `READ_TOOLS`, parameter checks, batch budget)
- `hooks/tm_read.py` (the mods' CLI; inserts the repo root on `sys.path` like the other hooks)
- `mods/taskmaster-mods/hooks/remote.ts`, `mods/taskmaster-mods/hooks/python.ts`
- tests: `tests/test_hook_views.py`, `tests/test_remote_locate_keys.py`, `tests/test_remote_cache.py`,
  `tests/test_remote_client.py`, `tests/test_serve_read_api.py`, `tests/test_hooks_server_mode.py`,
  `tests/test_tm_read_cli.py`, `mods/taskmaster-mods/tests/remote.test.ts`; golden fixtures under
  `tests/fixtures/hook_views/`

Changed:

- `taskmaster/native_routing/hook_reads.py` (view functions, D3, D3a)
- `hooks/edit_resurface.py`, `hooks/merge_gate_decide.py`, `hooks/merge_recorder_stamp.py`
- `taskmaster/query_guard.py` (`Authorizer(readable=)`, `READABLE` context variable,
  `READ_ENDPOINT_TABLES`), `taskmaster/native_routing/overview.py` (`query` reads it)
- `taskmaster/coordinator/ownership.py` (`private_directory_at`, `verify_private` unchanged)
- `taskmaster/serve/app.py` (`build_app(routes=…)`), `daemon.py` (build the read API with the shared
  limiter), `limits.py` (`take(key, cost=1)`), `config.py` (refuse `--path` under `/read`)
- `taskmaster_cli.py` (`remote` command)
- `mods/taskmaster-mods/hooks/tm.ts`, `host.ts`, `register.tsx`, `restore/scan.ts`,
  `restore/hooks.tsx`, `.claude-plugin/plugin.json` (version)
- `tests/test_packaging.py` (stdlib-only closure for `taskmaster/remote/`)

### 13.4 Overlap with parallel lanes

| Lane | Shared files | Risk and handling |
|---|---|---|
| 003 per-connection sessions | `serve/app.py`, `serve/daemon.py` | Small: 007 adds one parameter and one constructor call. 003 must keep the read tools working with no MCP session, since `tools` calls them from a plain HTTP request *(M12)*; Step 7 runs them that way. D3a's "the caller's claim" uses 003's `<handle>@<16hex>` session shape |
| 005 allowlist | `serve/daemon.py`; its tool gate and Part B docs rule are what `tools` relies on | Step 7 runs an allowlisted tool as a member key through 005's gate, and probes Part B through `tools`: a `docs` path outside the tracked set is refused |
| 009 split-brain | `taskmaster/server_marker.py` (009's), `ownership.py` (different functions) | §13.2. 007 doesn't edit `root.py` or `gate.py` |
| 011 per-clone machine-local fields | `hook_reads.py` (`tasks_for_branch`), the gate views' candidate rule | 011 moves `branch` into a per-principal, per-clone table. D3a's views already take the principal, so 011 changes how candidates are found and keeps the v1 fields. Whichever lands second rebases; Step 7's two-tasks-one-branch test pins the contract |
| 012 content import | none (`backlog_document_import` is not in `READ_TOOLS`) | none |
| 013 state export | none | none |
| 016 CI/deploy | docs only | the how-to covers `taskmaster remote set-key`, the env key's `TASKMASTER_SERVER_URL` binding, the marker, and macOS python.org certificate install; the Linux lane runs the client tests |
| 008 | `hooks/merge_gate_decide.py`, `hooks/merge_recorder_stamp.py` | 008 starts from 007's tip (§13.1) |

## 14. Known gaps (by design)

- **G1. Desktop sessions:** `$.process` is CLI only, so server-mode mods in the desktop app can't run
  the helper. They show offline with that reason (ruling Q6; a follow-up is filed).
- **G2. Tool text skew** for the mods is as it is over MCP today (D2); the parsers already degrade to
  "unreadable".
- **G3. Mod writes** don't reach the server yet (ruling Q5; a follow-up is filed).
- **G4. Host paths in successful tool texts** (handover file paths) pass through `tools` as they pass
  through MCP (006 G2); 015 judges. The mod's handling is checked in Step 10 (D14).
- **G5. One process, one TLS handshake per hook call.** `edit_resurface` in server mode costs about
  one round trip plus a handshake per uncached edit. A per-machine path index view (all path claims,
  cached 30 s) would make that one request per 30 s; it's left for when the latency shows.
- **G6. No thundering-herd lock:** several sessions with an expired `tools` entry each fetch once.
  With a 10 s TTL and a handful of sessions this is a few requests.
- **G7. `backlog_query` over MCP still reads `sessions`, `linear_queue` and `projection`** (D7).
  The endpoint refuses them; the MCP surface is filed for 015/004.

## 15. Rulings (user, 2026-11-01)

- **Q1. How the mods read:** the `tools` batch through the shared Python client (D14).
- **Q2. Marker:** JSON, `.taskmaster-server.json`, as 009 specifies; `locate` calls 009's
  `server_mode()` (D8, §13.2).
- **Q3. Client key:** an owner-only key file, `taskmaster remote set-key`, and an env override
  (bound to an origin, D9).
- **Q4. Go-live:** 008 is in the go-live set, together with 003, 005, 006, 007, 009, 013, 014 and
  015 (`docs/handoffs/2026-11-01-team-server-lanes/rulings.md`).
- **Q5. Mod writes in server mode:** a follow-up task.
- **Q6. Desktop mods in server mode:** offline, accepted; a follow-up task for desktop support.
- **Review I6 (orchestrator):** restrict `backlog_query` on the read endpoint rather than accept the
  gap (D7).

## 16. Risks

- **R1. FastMCP's tool-call entry from a custom route** (D4) might skip middleware or lose the
  request context. The review found the source supports both; Step 0 spikes it. If it ever drifts,
  Step 7's parity test (endpoint text equals MCP text for the same principal, for a sanitized error
  too) fails first. The `fastmcp<4` pin bounds it.
- **R2. urllib on teammates' machines:** a python.org macOS build without its certificate step
  fails TLS. The failure is `unreachable` with the SSL reason in the log; 016's how-to names the fix.
- **R3. Windows file replace races** in the cache: best effort (D11, D12); the cache never decides
  safety.
- **R4. Shared-file edits** in `serve/` with 003 and 005, and in `hook_reads.py` with 011 (§13.4).
- **R5. Interpreter start.** The review measured `python -I` plus `ssl.create_default_context()` at
  5.1-6.9 s on this machine under load. That is why the hooks' deadline is absolute from process
  start (8 s, D10) and the mods' process bound is 15 s (D14). Step 0 measures cold and loaded start;
  if a loaded `merge_gate_decide` can't finish in 8 s, that goes back to the orchestrator before 008.
- **R6. Abandoned worker threads** (D10) keep a socket open until their own socket timeout. Only
  short-lived processes (hooks, the CLI) use the client, and they exit right after the deadline.

## 17. Rollback

Revert the step commits. There's no schema, store or projection change. The cache and key
directories are outside the repo and can be deleted by hand; nothing reads them in solo mode.
Reverting leaves server-mode hooks without a store, which is today's state, and no teammate key is
issued before 007 and 008 are in (ruling Q4).

## 18. Implementation plan (TDD)

Each step: write the named tests first and watch them fail, implement, run the proving check, commit.
Tests run from the worktree with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m
pytest …` in the foreground (the bare `python` is the wrong interpreter; memory
`suite-interpreter-trap`). Scratch work goes under `D:\scratchbook\taskmaster\ts007\`.

**Step 0. Spikes, not committed** (`D:\scratchbook\taskmaster\ts007\spikes\`):

- (a) A custom route added through `create_streamable_http_app(routes=…)` behind 001's middleware
  gets 401 before `receive` with no token.
- (b) From that route, `FastMCP.call_tool(..., run_middleware=True)` runs a FastMCP
  `Middleware.on_call_tool`, and `get_http_request()` / `get_access_token()` inside the tool see the
  route's request and principal. A `ContextVar` set in the route is visible inside a sync tool.
- (c) urllib forwards `Authorization` on a cross-host 302.
- (d) Cold and loaded `python -I hooks/tm_read.py --help` time on Windows, with `ssl` imported.
- (e) `$.fs.exists` works from a mod at runtime (a throwaway probe mod under `dev-mods/`), for a
  present file, an absent one, and a path under `.git/`.

Record the results in the step-1 commit message. A failed (a), (b) or (e) comes back to the
orchestrator before the step that depends on it (7, 7, 10).

**Step 1. Shared read views (pure move, plus D3a).**
Files: `native_routing/hook_reads.py`, the three hooks, `tests/fixtures/hook_views/`.

- **Before moving anything**, capture golden outputs from the current hooks' read functions
  (`edit_resurface._resolve`, `merge_gate_decide.decide_from_store`'s task and policy,
  `merge_recorder_stamp.task_id_for_branch` and `native_ladder`) on the existing native and legacy
  fixture stores, into `tests/fixtures/hook_views/`. Comparing the moved code with itself would be
  circular *(M12)*.
- Tests first in `tests/test_hook_views.py`:
  - each view's `data` shape;
  - `paths_view` equals the golden outputs (exact, glob, prose, closed, handover, related);
  - `merge_gate_view` with policy off, no task, no gate, a failing gate, `skip_merge_gate`,
    freshness `any`;
  - `merge_target_view` with a ladder rung and with `branch:<x>`;
  - D3a: `principal=None` takes the first by id; with a principal, one candidate (`mine` true and
    false), two candidates of which one is the caller's (by `owner`, and by a `locked_by` session
    `<handle>@<hex>`), and two that aren't the caller's (`ambiguous`, both in `candidates`).
- Proving check: the new file plus the unchanged `test_edit_resurface_hook.py`,
  `test_merge_gate_decide.py`, `test_merge_gate_hook.py`, `test_merge_recorder_hook.py`,
  `test_native_routing_hooks.py` all pass.

**Step 2. Owner-only directories anywhere.**
Files: `coordinator/ownership.py`, `remote/dirs.py`. Tests first: `private_directory_at` creates an
owner-only directory (POSIX mode, Windows DACL through the existing verify path), refuses a link or
junction, and `private_directory(root, name)` behaves exactly as before; `dirs` honours
`TASKMASTER_CACHE_DIR`, `%LOCALAPPDATA%`, `%APPDATA%`, `XDG_*`. Proving check: new tests plus
`test_native_service_ownership.py` and `test_serve_auth.py`.

**Step 3. Cache.**
Files: `remote/cache.py`. Tests first (`tests/test_remote_cache.py`):

- fresh, stale-allowed and expired per `CACHE_POLICY`; `merge-gate` is never written or read;
- `config`, `no-key`, `refused`, `version` and `bad-request` never serve stale;
- a negative age counts as expired; a corrupt entry is a miss and is deleted;
- an older revision doesn't replace a newer one, including the re-read-and-retry on a lost race;
  a new `store_id` empties the server directory;
- pruning by age, by count and by the 64 MiB byte cap;
- the down entry; the refused entry needs two strikes 5-120 s apart, lasts 60 s, and clears on a key
  file change, an env key change or a success; a confirmed refusal deletes that key's entries;
  `merge-gate` ignores the down and refused entries;
- a link in the path disables the cache;
- after a run of every view, no file under the cache dir contains the key or its secret part.

Proving check: the file passes.

**Step 4. Locate and keys.** Starts after 009's Step 1 (`server_marker`) is on the integration
branch, else adds it per §13.2.
Files: `remote/locate.py`, `remote/keys.py`, `remote/cli.py`, `taskmaster_cli.py`. Tests first
(`tests/test_remote_locate_keys.py`):

- no marker and `.taskmaster/` present → solo; a marker, or a latch alone → server, with the derived
  `origin`, `read_base` and `server_id`; an invalid marker → kind `config`;
- canonicalization: `https://Host:443/mcp`, `https://host/mcp` and `https://HOST/other` give one
  `server_id`; an IDNA host round-trips;
- the env key is used only with a matching `TASKMASTER_SERVER_URL`. Missing or different URL →
  `no-key` naming both origins, and a fake transport records that no request was made;
- a key file that isn't owner-only, is a link, is malformed, or holds `tm_op_` is refused with the
  named fix; an origin with no key file → `no-key`;
- `set-key` reads stdin, canonicalizes the URL, writes owner-only, refuses a key on argv.

Proving check: the file plus `test_root_git_discovery.py`, `test_store_root.py`.

**Step 5. Limiter cost.**
Files: `serve/limits.py`. Tests first (`tests/test_serve_limits.py`, extended): `take(key, cost=3)`
takes three tokens or none and reports the wait for the shortfall; `take(key)` is unchanged.
Proving check: `test_serve_limits.py`, `test_serve_guard.py`.

**Step 6. Client transport.**
Files: `remote/client.py`. Tests first (`tests/test_remote_client.py`), against a stdlib
`http.server` on `127.0.0.1` (plain `http` is allowed to a loopback literal):

- every row of the D10 outcome table from the matching server reply, including 408, 411, 413, 421
  and 431;
- a 302 is `bad-response`, and the redirect target never receives `Authorization`;
- a non-loopback `http` URL and `http://localhost` are refused before any connect;
- with `HTTP_PROXY` set, a loopback request doesn't go through the proxy (a fake proxy records
  nothing);
- **deadline** (I2), each failing within the deadline plus 0.5 s:
  - a resolver monkeypatched to sleep past the deadline;
  - a server that sends the status line and then one header byte a second;
  - a server that drips the body one byte a second;
  - a hook-style absolute deadline (`process_start + 8 s`) that is shorter than the view budget;
- oversize and wrong content type;
- cache hits skip the network; stale is served only for the kinds and views that allow it, and is
  flagged `stale`;
- down and refused entries fail fast; `merge-gate` ignores both and always connects.

Proving check: the file passes.

**Step 7. Serve read routes and the query restriction.**
Files: `serve/read_api.py`, `serve/app.py`, `serve/daemon.py`, `serve/config.py`,
`query_guard.py`, `native_routing/overview.py`. Tests first (`tests/test_serve_read_api.py`, using
`tests/serve_helpers.py` and an in-process `ServeDaemon` on a native fixture store, as
`test_serve_daemon.py` does):

- each route without a key is 401 before its body is sent (006's whole-stack style); a member and the
  operator on the host succeed; the operator off-host is 403; a wrong `Host` is 421 (HostOriginGuard);
  past the in-flight cap is 429 *(M12)*;
- each view's envelope fields, `Cache-Control: no-store` and `X-Request-Id`; each view's `data`
  equals the Step 1 function on the same store (parity);
- **D3a on the server:** two live tasks on one branch with different owners. Each owner's key gets
  their own task (`mine: true`); a third member gets `ambiguous` with both candidates; with one task
  owned by someone else, the caller gets it with `mine: false`;
- bad parameters are 400 without echo; an unknown view and `/read/v2/` are 404 with the documented
  codes; a `GET` with a body is 400 and the body is never read;
- `tools`:
  - a tool outside `READ_TOOLS` is 400 and doesn't run;
  - each `READ_TOOLS` tool's text equals the same tool's text over MCP for the same key, including a
    sanitized error. The one exemption is `backlog_query` on the three host tables;
  - **I6:** `SELECT * FROM sessions`, `… FROM linear_queue` and `… FROM projection` through `tools`
    answer `Error: not authorized: <table>`; the same queries over MCP still answer;
    `SELECT … FROM entities` through `tools` matches MCP;
  - 005 Part B: `backlog_get_task` with a `docs` section whose path is outside the tracked set is
    refused through `tools` as over MCP;
  - a batch of 5 with 3 tokens left is 429 and runs nothing; a batch larger than `--rate-burst` is
    400;
  - the batch time budget: a stub slow tool makes later calls answer `not run: batch time budget`;
    a client that disconnects stops the batch;
  - the reply size budget caps combined text at 3 MiB;
  - the envelope `revision` is the one read before the first call (a write landing mid-batch doesn't
    raise it);
  - the ratchet that no `READ_TOOLS` tool sends a coordinator message;
- a `--path /read/x` config is refused.

Proving check: the file plus every `tests/test_serve_*.py` and the existing `backlog_query` tests.

**Step 8. Hooks in server mode.**
Files: the three hooks. Tests first (`tests/test_hooks_server_mode.py`): a real daemon from Step 7, a
teammate clone directory holding only a marker and a key file (no `.taskmaster/`), and each hook run
as a subprocess with its real stdin.

- `edit_resurface` prints the same line the solo hook prints on the host's store, dedupes on
  revision, and writes its state under the cache dir.
- `merge_gate_decide` blocks a stale gate and allows a fresh one, with the branch tip read locally.
  It returns ALLOW with a log line for every `ReadUnavailable` kind and for `ambiguous` (the one
  function 008 changes). Against a server that never answers, it answers within 8 s of its own
  start.
- `merge_recorder_stamp` reads the target and logs the 008 hand-off.
- No hook creates `.taskmaster/` in the clone.
- In a solo project, `taskmaster.remote` is never imported (`sys.modules` checked in-process, and
  `-X importtime` on the subprocess).

Proving check: the file plus the Step 1 regression set.

**Step 9. The mods' CLI.**
Files: `hooks/tm_read.py`. Tests first (`tests/test_tm_read_cli.py`): run under `-I` with calls on
stdin, it prints the envelope plus `source`, `age_s` and `elapsed_ms`; `--fresh` bypasses the cache;
each failure kind prints `{"error": {"kind", "message"}}` with exit 0 (the mod reads stdout); `mode
--root` answers `solo`, `server` or `none`; a solo root asked for `tools` answers kind `config` "not
a server-mode project"; stdout never exceeds 4 MiB. Proving check: the file passes.

**Step 10. Mods.**
Files: `mods/taskmaster-mods/hooks/remote.ts`, `python.ts`, `tm.ts`, `host.ts`, `register.tsx`,
`restore/scan.ts`, `restore/hooks.tsx`, `plugin.json`. Tests first (`tests/remote.test.ts` with the
`claude plugin test` harness and its `world` fixture):

- with no marker and no latch, the refresh makes the same MCP calls in the same order as before, and
  no process runs (the existing refresh and call-order tests unchanged);
- with a marker, or a latch alone, one process runs with the eight calls and the snapshot draws;
- **I3:** the first interpreter times out → exactly one process ran, and the result is a transient
  fault; the first exits 9009 or 127, or rejects at once → the next is tried; the first exits 1 →
  no second process, and the reply is unreadable; the restore scanner follows the same rule;
- each error kind maps to the D14 fault and text; a refusal shows D13's text, not "re-enter your
  key"; a slow start is worded as such;
- a stale result draws with the offline fault; a write is followed by a `--fresh` read;
- handover paths from the host degrade instead of mis-rendering;
- the restore pane still finds its scanner through the shared `python.ts`.

Proving check: the mod suite (`claude plugin test` in `mods/taskmaster-mods`), re-run once on "timed
out after 5000 ms" before debugging (memory `claude-code-mods-facts`).

**Step 11. Regression set and close-out.**
Run, in the foreground: `test_edit_resurface_hook.py`, `test_merge_gate_decide.py`,
`test_merge_gate_hook.py`, `test_merge_recorder_hook.py`, `test_merge_approve_hook.py`,
`test_native_routing_hooks.py`, `test_hook_shims.py`, `test_hooks_json.py`,
`test_session_start_hook.py`, `test_worktree_submodule_init_hook.py`, every `test_serve_*.py`,
`test_packaging.py`, `test_native_service_ownership.py`, `test_root_git_discovery.py`,
`test_store_root.py`, the `backlog_query` tests, `test_server_marker.py` (009), the seven new test
files, and the taskmaster-mods and rr-tui mod suites. Then the full suite at `-n 3`. Report failures
with their output; don't mark done on a partial run.
