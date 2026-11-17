<!-- User intent: the task-level design for team-server-005. The team server goes on the internet, so a teammate's
     key (or a prompt-injected teammate agent) must reach only what the MCP tools need from the host; every host-admin
     action runs only for the operator on the host, and no caller can point the daemon at an arbitrary environment secret. -->

# team-server-005: remote method allowlist (design, 2026-11-01)

**Task.** `team-server-005`. Done when remote principals reach only the MCP tool surface;
`shutdown`, `retire`, `git_run`, `git_recover`, `sync take_file` and `linear_bootstrap` are
host-loopback operator-only; and `linear_bootstrap` refuses a caller-named `token_env` on a server.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §3 ("Method allowlist"), §8
(Linear runs on the host with the operator's token), §13 (go-live order: 5, 6 and the 15 review ship
before any teammate key is issued).
**Builds on.** 001 (`docs/specs/2026-10-31-team-server-001-serve-design.md`: the loopback
coordinator inside the daemon, `ServeCoordinator`'s retire and shutdown refusals) and 002
(`docs/specs/2026-10-31-team-server-002-principals-design.md`: `client.ORIGIN_PROVIDER`,
`serve.auth.request_key_id`, `Origin(principal=key_id)`, `OPERATOR_KEY_ID`, `is_host_local`, and
ruling D7 / §10: "admin methods go to the bootstrap principal on the host only, never a member key
with role `operator`").
**Plan.** `docs/plans/2026-11-01-team-server-005-allowlist.md`.
**Verified against.** `feat/team-collab` at `4aabb2d` (001 and 002 merged), FastMCP 3.4.3. One probe
(scratch, not committed: `D:\scratchbook\taskmaster\ts005\test_probe_docs.py`) confirmed finding F1
in §9.

## 1. Problem

In server mode the daemon runs `backlog_server`'s tools and a coordinator in one process (001 D2).
A teammate's request arrives over HTTP, runs a tool in that process, and the tool talks to the
coordinator through the in-process `Client` over loopback IPC (`coordinator/client.py:119`,
`_send`). The coordinator cannot tell who asked: 002 attaches the caller's key as `origin` on
`execute` only (`client.py:354-368`), and every other method arrives with no origin, which the
coordinator reads as `LOCAL`, the host itself.

So today a teammate's key reaches host-admin actions through ordinary tools:

| Tool, action | Coordinator method it reaches | Effect on the host |
|---|---|---|
| `backlog_resolve_conflict(file, take="file")` (`native_routing/conflicts.py:115`) | `sync` with `take_file=True` | adopts bytes from the host's disk over the store |
| `backlog_linear(action="bootstrap_apply")` (`overview.py:317-323`) | `linear_bootstrap` | rewrites the host's `linear.yaml`, naming any environment variable as the token |
| `backlog_linear(action="probe")` (`overview.py:287` → `backlog_server.py:13113`) | none: runs in the tool | reads `os.environ[token_env]` for **any** name the caller gives and sends it to `api.linear.app` as a bearer token; the "is not set" answer is an oracle for which variables exist |

`git_run`, `git_recover`, `git_status`, `receipt`, `cancel` and `sync_status` have no MCP tool
today, but nothing stops the next tool from calling them, and nothing in the coordinator would
refuse it.

## 2. Scope

In scope:

- one classification of every coordinator method for a serve coordinator (§4), enforced where every
  remote path must pass (§5, D1 to D4);
- the two tool actions that reach host-only capability without a coordinator method
  (`backlog_linear` probe and bootstrap_apply; D6);
- `token_env` on a server versus a local store (D7);
- re-checking host-locality wherever the operator principal is read (D8);
- error shapes and log lines (D9), and ratchet tests that make a new method or a new IPC path fail
  until it is classified (D10).

Out of scope (§10 lists owners): transport hardening and error sanitizing (006), receipt and queue
scoping (004), session ownership (003), the security review (015). Finding F1 (§9, host file reads
through task `docs` paths) is outside the coordinator allowlist; it is in scope as **Part B** (ruling
U1, §12), and lands after 006 provides `serve/tracked.py`.

## 3. Threat model

The host is internet-facing behind Caddy (addendum §13). 002 authenticates every request; 006
throttles and sanitizes. 005 decides what an authenticated caller may make the host do.

| Attacker | Wants to | Today | Stopped by (005) |
|---|---|---|---|
| A teammate's key, a leaked key, or a prompt-injected teammate agent | adopt host-disk bytes over the store (`take_file`) | reachable | client gate + coordinator gate (D1) |
| same | rewrite `linear.yaml`, or point the daemon at another environment variable | reachable | operator-only `linear_bootstrap` (D1) and the fixed token name (D7) |
| same | exfiltrate a host environment secret to Linear, or probe which variables exist | reachable (`probe`) | operator-only probe, fixed token name (D6, D7) |
| same, through a tool added later | run managed Git on the host clone, recover a pinned publication, cancel or read others' requests | no tool yet; nothing refuses it | default-deny allowlist; a new method fails a ratchet test until classified (D10) |
| Tool code that loses the request context (a `threading.Thread` started inside a tool) | reach an admin method with no principal, which the coordinator reads as `LOCAL` | would succeed | fail-closed client gate: no principal in the serve process means remote rules (D4) |
| A copied operator bootstrap token, used off-host | everything the operator may do | refused by 002 D7 (403 before the body) | unchanged, and re-checked where the principal is read (D8) |
| A caller who forges `origin` | claim to be the operator | tools never pass `origin`; nothing checks | the serve-process client refuses a caller-supplied `origin` (D3) |
| A process on the host as the service user | anything | trusted: it can read `store.db` and the discovery token anyway (002 §7) | unchanged: `LOCAL` is the host operator |
| The internet, directly at the coordinator | IPC | coordinator binds `127.0.0.1` and needs the owner-only discovery token (001) | unchanged |

## 4. Method inventory and classification

Every method `Coordinator.dispatch` serves (`service.py:452-538`), as seen by a **serve**
coordinator. "Remote" means any authenticated principal, including a teammate key; "operator"
means the bootstrap principal on the host or a host process (D2).

| Method | Class | Who calls it today | Reason |
|---|---|---|---|
| `execute` | remote | every write tool (`adapter.py:58`) | the tool surface itself; 002 attributes it, 003/004 scope it |
| `status` | remote | `Client._probe` before every call (`client.py:169`), `native/fresh.py:116` | the connection handshake; no tool returns its payload |
| `flush` | remote | `NativeCall.drain` (`adapter.py:85`) | a publication barrier over the caller's own commit |
| `sync` (no `take_file`, no `worktree`) | remote | resync tools (`resync.py:75`) | imports host projection files the host itself wrote; needed by `backlog_*_resync` |
| `sync_job` | remote | `backlog_sync` (`resync.py:256`) | same, as a bounded job |
| `linear_retry` | remote | `backlog_linear(retry)` (`overview.py:306`) | re-queues a push of the store's own rows with the operator's token; 004 scopes its ids |
| `sync` with `take_file=True` | **operator** | `backlog_resolve_conflict(take="file")` | overrides the store with bytes from the host's disk: an operator's hand edit, the operator's call. `take="store"` stays remote |
| `sync` with `worktree` | **operator** | no tool (CLI and managed Git only) | names a host checkout path |
| `linear_bootstrap` | **operator** | `backlog_linear(bootstrap_apply)` | rewrites authored host config and chooses which secret the daemon sends to Linear |
| `git_run` | **operator** | `taskmaster git` CLI (`git_cli.py:67`) | commits and checks out in the host clone (addendum §8: managed Git runs client-side, never for a teammate on the host) |
| `git_recover` | **operator** | `taskmaster git recover` | releases publication pins and drift on the host |
| `git_status` | **operator** | `taskmaster git status` | host Git state; no tool needs it |
| `receipt` | **operator** (until 004, ruling U2) | no tool | reads any caller's receipt by `caller_scope`/`request_id`; 004 opens it scoped to the principal |
| `sync_status` | **operator** (until 004, ruling U2) | no tool | same, for sync operations |
| `cancel` | **operator** (until 004, ruling U2) | no tool | cancels any caller's queued command by id |
| `shutdown` | **refused on a server, for everyone** | `taskmaster coordinator stop` | 001: `ServeCoordinator.dispatch` refuses it (`daemon.py:70-73`); the operator stops the daemon (systemd). Unchanged |
| `retire` | **refused on a server, for everyone** | `Client._retire` | 001: `ServeCoordinator.retire` refuses every build (`daemon.py:66-68`). Unchanged |
| any other name | operator (then "unsupported coordinator method") | none | default deny |

`shutdown` and `retire` are stricter than "operator-only": on a serve coordinator nobody can use
them, which is 001's tested behaviour. The gate passes them through to those refusals (D5) so their
messages and handshake order stay as 001 pinned them.

Tool actions that reach host-only capability **without** a coordinator method:

| Tool, action | Class on a server | Mechanism |
|---|---|---|
| `backlog_linear(probe)` | operator (ruling U4) | tool gate (D6); token name fixed (D7) |
| `backlog_linear(bootstrap_apply)` | operator | tool gate (D6, for a clear early answer) plus the coordinator gate on `linear_bootstrap` |
| `backlog_linear` link, unlink, list, show, status, retry | remote | data operations |
| `backlog_blast_radius(mode="evidence")` | operator (gate finding C3) | the tool gate (D6) in `backlog_server._blast_radius_text`, shared by the native and legacy routes. It runs git and walks files under a task's free-text `worktree` and `sub_repo`, which a teammate can point at any repository the service user can read. `mode="predictive"` reads only the store and stays remote |
| `backlog_open_viewer` | 006's (serve-mode check, 006 plan Step 6) | not 005 |
| task and epic `docs` reads, `backlog_document_import` | **Part B** (§9, ruling U1) | path rule in the serve process |
| legacy-only tools (`backlog_init`, `backlog_migrate_v3/v4`, `backlog_canonicalize_layout`, `backlog_backfill_lanes`) | already refused on a native store (`registry.py:52-74`); a serve store is always native | unchanged |

`execute` carries an operation name. Every `call.execute(operation, …)` site passes a literal or a
module constant, with one exception, found by the gate review (M2):
`backlog_batch_update(commands=[{operation, arguments}])` (`native_routing/batch.py:118-141`) takes
caller-named operations.

That channel is bounded by a denylist, not an allowlist:

- `contracts.validate_operation(in_batch=True)` refuses the sync, linear-outbox and graph-repair
  operations;
- `batch_commands.INTERNAL_OPERATIONS` refuses others.

What remains reachable is domain writes only (`task.*`, `epic.*`, `project.set`,
`linear.link/unlink`, `projection.resolve`, …), and none of them is host-admin. So no
operation-level allowlist inside `execute` is added here. For 015's checklist: the `batch commands=`
channel takes caller-named operations, and only a denylist bounds it.

## 5. Decisions

### D1. Two enforcement points, one table

The table lives in a new module, `taskmaster/coordinator/allowlist.py`, with no imports from `serve`
(the coordinator package must not import the server; `serve` imports `coordinator`). Its one
predicate is

```
refusal(method, arguments, principal) -> str | None
```

`None` means allowed. `principal` is a key id, `"operator"`, or `None` (unknown). The rule:
allowed when the principal is the operator, when the method is `shutdown` or `retire` (refused
later by 001), or when the method is in the remote class and is not a host-only `sync`. Otherwise
it returns the refusal text (D9).

It is enforced at two points, both before any work:

1. **The serve-process client, in `Client._send`** (`client.py:119`). Every coordinator IPC message
   any code in the daemon sends passes through `_send`: `call()`, `execute()`, `sync_job()`,
   `_probe()` and `_retire()` all end there, and no other module posts to `/rpc` (ratchet, D10).
   When `ORIGIN_PROVIDER` is installed (only the daemon installs it, 002 D8), `_send` applies
   `refusal(method, arguments, principal_of(arguments.get("origin")))` and raises before the admin
   message is sent (the connection probe's `status`, which `_ready()` sends first, may already have
   gone; it is a remote-class method). This is the point no in-process path can go around, and it is the only point that can
   tell "in the daemon, with no principal" from "a host process".
2. **`ServeCoordinator.dispatch`** (`daemon.py:70`). After the handshake, it parses the origin for
   **every** method (002's `_origin`, unchanged), maps `LOCAL` to the operator, and applies the same
   `refusal`. This is the authority for any IPC message that carries an origin, and the place the
   `token_env` rule lives (D7).

Why not elsewhere:

- **A FastMCP middleware** sees tool names and arguments, not what the tool will ask the
  coordinator. It would need its own list of tool/action pairs that mirror the method table, and a
  future tool calling `git_run` would get through until someone added it. The method table is
  closed and small; the tool surface is 90 tools and growing.
- **The coordinator alone** cannot work: in the daemon, a tool thread that lost its request context
  sends no origin, which is exactly what a host CLI sends. Only the client knows it runs in the
  daemon.
- **The client alone** would leave the coordinator trusting whatever origin arrives. Both points
  use one function, so they cannot disagree.

### D2. Who is the operator

- The bootstrap principal: `key_id == "operator"`. 002 D7 already lets it in only when
  `is_host_local(scope)` holds, per request, and 006 re-checks it on every operator viewer session
  (006 D10.2). A member key with role `operator` is never the operator here (002 §7, §10).
- `LOCAL` at the serve coordinator: a message with no origin. After D4 that can only come from a
  host process that read the owner-only discovery file (the `taskmaster git` and `coordinator` CLIs,
  hooks, a stdio session on the host): the service account, which 002 §7 already treats as able to
  read `store.db` directly.
- `allowlist.OPERATOR` is the string `"operator"`. A test holds it equal to
  `serve.auth.OPERATOR_KEY_ID`, because the coordinator package cannot import `serve`.

### D3. The origin travels on every call, and only the client sets it

- `Client.call` resolves the origin once, before its retry loop, exactly as `execute` does today:
  with a provider installed, a non-`None` key becomes `origin={"principal": key}` on **every**
  method, not only `execute`. `execute` drops its own copy of this logic and goes through `call`.
  `sync_job` resolves it in the calling thread before it starts its worker thread
  (`client.py:428-437`), because that thread has no request context.
- With a provider installed, a caller-supplied `origin` argument is refused: `ValueError("origin is
  set by the serve daemon, never by a caller")`. No production code passes one; tests that pass one
  run without a provider (002's `test_serve_identity.py`), so they are unaffected.
- Without a provider (solo, every host CLI, every test that does not start a daemon), `call` and
  `execute` send exactly what they send today. 002's
  `test_client_sends_the_origin_only_with_a_provider` keeps passing unchanged.
- A provider that raises (`PermissionError`, 002) propagates before anything is sent, as today.
- The base `Coordinator` ignores `origin` on every method (it reads `message.get(...)` per field),
  so solo coordinators are unaffected. The origin on non-`execute` methods is also what 004 needs to
  scope `receipt`, `cancel` and `sync_status`.

### D4. No principal in the daemon fails closed

In the serve process, a message whose origin is missing (a tool's own thread, the
`taskmaster-sync-job-call` thread, `_probe`, `_retire`, the merge-stamp replay thread at
`registry.py:133`) is judged with `principal=None`: remote-class methods pass, everything else is
refused. Every such sender today uses only `status`, `execute` or `sync_job`, so nothing that works
now stops working; the merge-stamp replay keeps its 002 attribution (`LOCAL`), which is 002's and
003's concern, not an admin path.

### D5. `shutdown` and `retire` keep 001's refusals

The gate returns `None` for them, so a teammate, the operator and a host process all reach 001's
unconditional refusals: `SERVE_SHUTDOWN_REFUSED` (after its handshake check) and
`SERVE_RETIRE_REFUSED`. This keeps `test_serve_coordinator.py` and
`test_remote_client_cannot_trigger_retire` (`test_serve_daemon.py:98`, which drives `Client._retire`
inside a running daemon and expects `HandshakeError` "refused to retire") exactly as they are.

### D6. The tool gate, for actions that are not coordinator methods

`backlog_linear(probe)` runs entirely in the tool, so the coordinator never sees it. In the native
adapter (`overview.py:281`), before `probe` and before `bootstrap_apply`:

```
serving, principal = client.served_principal()
if serving:
    refusal = allowlist.action_refusal("backlog_linear probe", principal)   # None for the operator
    ...
```

`client.served_principal()` returns `(False, None)` without a provider and `(True, provider())` with
one. `client.serving()` returns only whether a provider is installed, without calling it; it is the
predicate for code that needs "am I in the daemon" but not the principal (Part B, §9). 006 has its
own detector (`TASKMASTER_SERVE=1`) for viewer hooks; 005 does not use it, because the provider is
installed and removed together with the request context it describes. A refusal is answered in the tool's JSON error shape, before `os.environ` is read or a
`LinearClient` exists. `bootstrap_apply` is checked here too, for the same early, clear answer; the
coordinator gate stays the authority. The check lives in `overview.served_linear_guard`, which the legacy body `backlog_server.backlog_linear` also calls, so a routing fallback to the legacy path cannot run probe ungated (gate M1). `client.served_action_refusal(what)` is the shared form of the D6 check; `backlog_blast_radius` evidence mode uses it too (C3).

### D7. `token_env` on a server versus a local store

| | Local store (solo, session coordinator) | Team server (serve daemon) |
|---|---|---|
| Who may run probe / bootstrap_apply | anyone with the tools, as today | the operator only (D1, D6) |
| `token_env` the caller may pass | any environment-variable name (`linear_config.validate`, unchanged) | empty, or exactly `TASKMASTER_LINEAR_TOKEN` |
| Name the daemon reads and stores | the caller's | always `TASKMASTER_LINEAR_TOKEN` |
| Where the operator sets the token | the user's shell | the daemon's environment (the systemd unit; 016's how-to) |

- The tool (D6) maps an empty `token_env` to `TASKMASTER_LINEAR_TOKEN` and refuses any other name
  with the D9 text, before reading the environment. So the `taskmaster:linear` skill works on a
  server if it passes nothing or the fixed name.
- `ServeCoordinator.dispatch` refuses a `linear_bootstrap` whose `entry["token_env"]` is not
  exactly `TASKMASTER_LINEAR_TOKEN` (or whose `entry` is not a mapping), for every principal,
  operator included. Only `linear.yaml`'s new entry is affected: the coordinator writes nothing on a
  refusal (`linear_config.bootstrap` is never called).
- A fixed name, with no flag (ruling U3). A `--linear-token-env` flag would also have edited
  `serve/config.py` and `cli.py`, which 006 is changing now.
- Why the operator too: the daemon's environment is the host's secrets store. A prompt-injected
  agent in the operator's own loopback session should not be able to name `AWS_SECRET_ACCESS_KEY`
  either. The rule costs the operator nothing.
- Workspaces already in a migrated `linear.yaml` that name another variable are left alone: the
  Linear worker is unchanged, the daemon does not warn about them, and nothing refuses them (ruling
  U5). Only a new bootstrap is held to the fixed name.

### D8. Re-check host-locality where the operator principal is read

`serve.auth.current_key_id()` (the provider behind `request_key_id`) gains one rule: if the
principal is the bootstrap operator and `is_host_local(request.scope)` is false, raise
`PermissionError("the operator bootstrap token works only on the host")`. 002's middleware already
guarantees this, so no legitimate request changes; the check makes "operator ⇒ host-local" true at
the point the allowlist relies on it, not only at the edge, as 002 §7 said task 5 would ("Task 5's
admin check reuses it too"). The viewer path (`VIEWER_KEY_ID`) is 006's, which re-checks
`is_host_local` on every operator session request.

### D9. Error shapes and logging

| Where | Refusal | Caller sees |
|---|---|---|
| Client gate (`_send`) | `ValueError(text)`, raised before the admin message is sent | the tool's usual error shape, because tools already catch `ValueError` around these calls: `Error: <text>` from `backlog_resolve_conflict` (`error_text`), `{"error": "<text>"}` from `backlog_linear` |
| Coordinator gate | `ValueError(text)` → IPC 409 `{"error": text, "type": "ValueError"}` → the client re-raises `ValueError` (`client.py:144`) | same as above |
| Tool gate (D6) | none raised | `{"error": "<text>"}` (`backlog_linear` is a `JSON_ERRORS` tool) |

Texts (exact; the plan's tests match them):

- `ADMIN_REFUSED = "{what} is a host operator action on a team server: run it on the host (the
  taskmaster CLI, or the operator bootstrap token over loopback); nothing was changed"`, where
  `what` names the action, e.g. `adopting a flagged file from the host's disk (sync take_file)`, with
  the suffix `; backlog_resolve_conflict(take="store") is available to everyone` for `take_file`.
- `LINEAR_TOKEN_ENV_REFUSED = "on a team server the Linear token is read only from
  $TASKMASTER_LINEAR_TOKEN, which the operator sets in the daemon's environment; pass
  token_env=\"TASKMASTER_LINEAR_TOKEN\" or leave it empty; linear.yaml was not changed"`.

Why `ValueError` and not a new exception type:

- It is a clean refusal before admission, not `ServiceUnavailable`, so the client never reports
  "may have committed". `ValueError` is already in the client's error map.
- 001 already refuses `shutdown` this way (`SERVE_SHUTDOWN_REFUSED`).
- 006's coordinator sanitizer passes a domain error through by its **exact** type name
  (`DOMAIN_ERRORS`, 006 plan §2.5). A new type, even a `ValueError` subclass, would be rewritten to
  `internal coordinator error`. The D9 texts contain no paths, so 006's scrubbing leaves them as they are.
  (Part B's refusal, §9, echoes the caller's own `docs` path; 006 may scrub an absolute one to
  `<path>`, which is harmless.)

Logging: the coordinator gate logs `WARNING refused <method> for key <key_id>` on
`taskmaster.coordinator`, and the client gate logs `WARNING refused <method> in the serve process
for key <key_id or ->` on the same logger; both go to `serve.log` (001 attaches it). Both pass the
method and key through `serve.app.log_safe`'s rule: the client gate cannot import `serve`, so it
logs only values from the closed `METHODS` set or the literal `other`, and a key id that matches
`[0-9a-f]{16}|operator` or the literal `invalid`. No arguments, entries or tokens are logged.

### D10. Ratchets

- **Every dispatched method is classified.** A test reads `Coordinator.dispatch`'s source, collects
  every `method == '<name>'` literal, and requires the set to equal `allowlist.METHODS`. Adding a
  method without classifying it fails the test; an unclassified method is denied to remote callers
  anyway. (A refactor of `dispatch` away from `method == '…'` comparisons would blind this test;
  default deny still holds, and the refactor must update the test.)
- **One IPC sender.** A test scans `taskmaster/**/*.py` for the string `/rpc` and requires it only
  in `coordinator/client.py` (the sender), `coordinator/service.py` (the receiver) and
  `serve/config.py` (which forbids it as an MCP path).
- **Any tool, not just today's.** The end-to-end test registers a throwaway tool on the served
  FastMCP instance that calls each non-remote method through `Client`, and from a bare
  `threading.Thread`. A member key is refused on every one; the operator passes the gate on the
  direct calls and is refused from the bare thread (D4).

## 6. How a refused request flows

```
teammate agent --HTTPS--> Caddy --> RequestGuard (006) --> bearer auth + RequirePrincipalMiddleware (002)
  --> PrincipalLimits (006) --> FastMCP tool (SanitizeErrors, 006)
      backlog_resolve_conflict(take="file")
        NativeCall.sync([file], take_file=True)
          Client.call("sync", take_file=True)       origin = {"principal": <ann's key id>}   (D3)
            Client._send: allowlist.refusal(...)     -> ValueError(ADMIN_REFUSED)            (D1.1)
        _take_file catches ValueError -> "Error: adopting a flagged file ... nothing was changed"
  (no IPC was sent; had it been, ServeCoordinator.dispatch would refuse it the same way)    (D1.2)
```

## 7. The seam with 006

006 (transport hardening, implementing now in `.worktrees/team-server-006`) and 005 share no
function. Ownership:

| Concern | Owner |
|---|---|
| Body caps, timeouts, failure budget, per-key rate limit (`serve/guard.py`, `limits.py`, `protocol.py`) | 006 |
| Error sanitizing: `serve/sanitize.py`, `Coordinator.public_error`, `ServeCoordinator.public_error` | 006 |
| Viewer login, CSRF, CORS, `/file/` tracked-only | 006 |
| `taskmaster/serve/tracked.py`: `TrackedFiles` and the one shared path rule `tracked_file_allowed(root, rel_path) -> bool`, used by the viewer's `/file/` and by 005's Part B | 006 (moved out of `serve/viewer.py`; requested by the orchestrator after the 005 review) |
| Which coordinator methods a principal may reach (`coordinator/allowlist.py`, `Client._send`, `ServeCoordinator.dispatch`) | 005 |
| Origin on every method, caller-supplied origin refused (`client.py`) | 005 (006's plan does not touch `client.py`) |
| `backlog_linear` probe and bootstrap gates, the fixed token name (`overview.py`) | 005 |
| Host-locality re-check in `current_key_id` (`serve/auth.py`) | 005 (006's plan does not touch `auth.py`) |

Interactions:

- **Sanitizer.** 005's refusals are `ValueError`s with path-free text, so 006's
  `coordinator_public_error` and `SanitizeErrors` pass them through unchanged (D9). 005 must not add
  an exception type; if it ever does, it goes into 006's `DOMAIN_ERRORS` and the client's error map
  together.
- **Viewer writes.** 006 wraps each viewer write in `auth.viewer_principal(key_id)`, so
  `request_key_id()` returns that key and 005's client gate judges viewer-triggered IPC by it. No
  viewer route reaches a non-remote method today. An operator viewer session is host-local per 006
  D10.2.
- **Rate limits** apply before 005's gates; a refused admin call still costs the caller one token.
- **Merge conflicts.** Both branches edit `serve/daemon.py`: 006 adds `ServeCoordinator.public_error`
  and changes `ServeDaemon._start`/`_teardown`; 005 changes only `ServeCoordinator.dispatch` and adds
  one import. Expect a textual conflict in the import block at most. Nothing else overlaps:
  006 edits `service.py:_post`, which 005 does not touch; 005 edits `client.py`, `overview.py` and
  `auth.py:current_key_id`, which 006 does not touch. 006's design cites `overview.py:309` and `:349`
  as JSON-error sites; 005 inserts lines above them, so those citations shift (cosmetic).
- **Ordering.** Part A (D1 to D10) is independent of 006 and can land first or second. Whichever
  lands second re-runs both branches' serve test sets (plan §5). **Part B (§9) starts only once 006
  has merged into `feat/team-collab` with `taskmaster/serve/tracked.py`** exposing `TrackedFiles`
  and `tracked_file_allowed(root, rel_path) -> bool` (§9 states the contract). At review time
  (006 tip `32b52eb`) `TrackedFiles` and its path check still lived in `serve/viewer.py`; the
  orchestrator has asked 006 to make the move, so a document read path never imports the viewer
  and both callers share one rule. Part B calls that function and never copies or re-implements
  it. Part A's steps and commits come first on this branch; Part B's steps start after this branch
  is rebased onto that merge.
- **Go-live.** Addendum §13 requires both 005 and 006, plus 015's review, before any key is issued.

Other seams: **002** owns `ORIGIN_PROVIDER`, `request_key_id` and `_origin`; 005 only widens where the
origin is sent. **003** is unaffected (sessions and `caller_scope`). **004** opens `receipt`,
`cancel` and `sync_status` to remote principals when it scopes them, by moving them to the remote
class in `allowlist.py`; the origin they need already travels (D3).

## 8. Out of scope

| Not here | Owner |
|---|---|
| Body caps, timeouts, rate limits, error sanitizing, viewer login/CSRF/CORS | 006 |
| Receipt, cancel, sync_status and queue scoping per principal | 004 |
| Session ownership, `caller_scope` prefixes | 003 |
| Content-based `backlog_document_import` for remote callers | 012 (Part B only closes the host-file read) |
| Operation-level checks inside `execute`, including the caller-named `backlog_batch_update(commands=)` channel, which only a denylist bounds (gate M2); host paths in success results (006 G2); `merge_stamps.git_is_ancestor` argument validation; `linear.yaml` is git-tracked, so under U5 a committed `token_env` edit makes the Linear worker send that variable to `api.linear.app` (to Linear, not to the committer) | 015's review |
| A `--linear-token-env` flag; the deployment how-to naming `TASKMASTER_LINEAR_TOKEN` | 016 |
| Split-brain fencing of ordinary sessions on the host clone | 009 |

## 9. Part B (ruling U1): host files read through task `docs` paths

**F1 (Critical for go-live).** A teammate can read any file the service user can read:

1. `backlog_update_task(task_id, field="docs", value="spec:/etc/passwd")` stores the path
   unchecked (`native/workflow.py:702-704`, `:800-804` validate only the key).
2. `backlog_get_task(task_id, sections=["spec"])` reaches
   `native_routing/documents.py:35` → `taskmaster_v3.read_doc_section` (`:220`), which joins
   `project_root / doc_path`. A `pathlib` join with an absolute path returns the absolute path, and
   `..` is not checked. The probe read a file outside the project through exactly this call.
3. On the Hetzner host, `/proc/self/environ` holds the daemon's environment, including the Linear
   token. Inside the project, `.taskmaster/local/serve/operator.token`, the coordinator's
   `discovery.json` and an untracked `.env` are readable the same way.
   `backlog_document_import` (`documents.py:74-96`) is contained to the project, but not to tracked
   files, and copies what it reads into the store, where every teammate can read it.

The leaked operator token and discovery token are usable only on the host (002 D7, 001), but
`/proc/self/environ`, SSH keys and `.env` files are not so limited.

**Design.** Part B adds no path logic of its own. In the serve process, each document reader asks
one question of 006's shared rule, `taskmaster.serve.tracked.tracked_file_allowed(root, rel_path)`,
and reads the file only when it answers `True`.

**The rule's contract** (006 owns the code; 005 relies on exactly this, and plan Step 6 tests it).
It answers `True` only when all of these hold, and treats any `OSError` or `ValueError` as `False`:

- `rel_path` is a relative path: not absolute, no drive (`C:`), not UNC (`\\host\share`), no NUL
  byte, no backslash;
- `resolved = (root / rel_path).resolve()`, symlinks and `..` included, lies inside
  `root.resolve()`;
- the **resolved**, root-relative POSIX form is not under `.taskmaster/local/` or `.git/`;
- the **resolved**, root-relative POSIX form is in `TrackedFiles(root).get()` (git-tracked).

Every check after the first is made on the resolved path, never on the string the caller stored.
So a tracked symlink `docs/tok -> ../.taskmaster/local/serve/operator.token`, or `docs/env ->
../.env`, is refused: its target is under `.taskmaster/local/`, or untracked. So is a symlink or
`..` that leaves the root, and an absolute, drive or UNC path. The review probed 006's first
version against exactly those two symlinks and both were served; the contract above closes that
for the viewer's `/file/` too, because it is the same function.

**Which root (fail closed, review I3).** In the serve process the root is always the daemon's
served root, `bs.ROOT` (which `load_server_module` verified equals the store root at start), never
the `project_root` argument. `documents.project_root()` returns `None` whenever `backlog.yaml` is
missing (not yet exported, quarantined or deleted), and `read_doc_section` then reads
`Path(doc_path)` unchecked today. Falling back to `bs.ROOT` was chosen over refusing because:

- it is just as safe: the rule still decides every read against the one root the daemon serves;
- it keeps tracked documents readable while `backlog.yaml` is briefly missing, which refusing would
  break for no security gain;
- it is the same root the rule is given when `project_root` is present, so there is one code path,
  not two.

If `bs.ROOT` cannot be read (the server module isn't loaded), the read is refused. The unchecked
`Path(doc_path)` branch is never reached in the serve process.

**Where it applies.**

- `taskmaster_v3.read_doc_section`, the one place a section comes off disk. Native reads
  (`documents.py:35`, `tasks.py:760`) and the legacy `resolve_sections*` (`backlog_server.py:2597`,
  `:2792`) all end there.
- `native_routing/documents._project_file`, the importer.
- Both first check `client.serving()` (§5, D6), which doesn't call the provider, and import
  `taskmaster.serve.tracked` lazily only after it is true. Solo reads never import the serve
  package and are unchanged.

**Answers.**

- The read answers `(refused: <path> is not a git-tracked project file; a team server reads only
  tracked files)` with provenance `{"source": "refused", "path": ..., "imported": false}`.
- The import answers the existing per-section `_Unreadable` error with the same text.

The rule applies to every principal in the daemon, the operator included. Stored paths are not
rewritten.

**Why tracked-only and not just contained.** Containment alone still serves `.env`,
`operator.token` and `discovery.json`. 006 D10.7 reached the same rule for the viewer's `/file/`,
and one shared function for both paths is easier to review and to keep.

## 10. Risks

- **R1. A future sender bypasses `_send`.** Code that posts to `/rpc` itself, or calls a
  `Coordinator` method in-process, would skip the client gate. The `/rpc` ratchet catches the first.
  The second: no tool or viewer module imports `serve.daemon` or holds the coordinator object today,
  and the coordinator gate still applies to IPC. 015 should keep both in its checklist.
- **R2. Context propagation.** D3 and D8 rely on the request context reaching the tool thread, as
  002 already does for attribution. If FastMCP stops copying context into its worker threads, every
  call has `principal=None`: admin actions are refused for everyone (closed), and 002's
  attribution test fails first.
- **R3. The operator's own MCP session loses admin actions in a lost-context thread.** That is the
  intended fail-closed trade (D4). No such caller exists.
- **R4. `token_env` on a migrated store.** Existing workspaces keep their variable names, unwarned (D7,
  ruling U5).
- **R5. Shared files with 006** (`daemon.py` import block). See §7.
- **R6. Part B waits on 006.** If 006's `serve/tracked.py` slips, Part A can still be reviewed and
  merged on its own; F1 stays open, and go-live stays blocked (addendum §13) until Part B lands.

## 11. Rollback

Revert the step commits. There's no schema, store, projection or config-format change. A
`linear.yaml` entry written on a server names `TASKMASTER_LINEAR_TOKEN`, which stays valid after a
revert.

## 12. Rulings (user, 2026-11-01)

The open questions of the first draft were ruled by the user. Each is a binding decision.

- **U1 (was Q1). Part B is in 005.** F1's fix (§9; plan Steps 6 and 7) is part of this task. It
  lands after 006 provides `TrackedFiles` in `taskmaster/serve/tracked.py`, which the orchestrator
  has asked 006 to do.
- **U2 (was Q2). `receipt`, `cancel` and `sync_status` stay operator-only** until 004 scopes them by
  principal and moves them to the remote class.
- **U3 (was Q3). The Linear token variable is the fixed name `TASKMASTER_LINEAR_TOKEN`**, with no
  flag (D7).
- **U4 (was Q4). On a server, `backlog_linear(probe)` is operator-only** (D6), like
  `bootstrap_apply`.
- **U5 (was Q5). Existing `linear.yaml` workspaces that name other variables are left alone**: no
  start-up warning and no refusal in the Linear worker (D7).

No questions remain open.
