<!-- User intent: the task-level design for team-server-003. On the shared Taskmaster server every teammate's
     agent connection gets its own session, so one person's claims, current task and receipts never become
     another's; claims belong to the person, a reconnected agent can take back its own work, and nobody can
     take or release a teammate's claim by naming their session. -->

# team-server-003: per-connection sessions and claim ownership (design, 2026-11-01)

**Task.** `team-server-003`. Done when `SESSION_ID` is per MCP connection (24 uses in `backlog_server.py`,
8 other files, tools running in a thread pool), `caller_scope` is handle-prefixed on the server, claim
session arguments are checked against the principal, and a principal can adopt its own older sessions.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §3 (sessions bullet) and §7 (claims);
`docs/specs/2026-10-31-team-server-002-principals-design.md` §8 and §10 (the seams 003 picks up);
`docs/specs/2026-10-31-team-server-006-hardening-design.md` (the guarded viewer).
**Plan.** `docs/plans/2026-11-01-team-server-003-sessions.md`.
**Verified against.** FastMCP 3.4.3 and mcp 1.28.1 in the repo venv, read from source (cited with file and
line in §5). Nothing here was spiked; the plan's Step 4 tests are the first proof of D3's propagation claims.
**Status.** Rev 2, after spec review (WARN; S-I1, S-I2 and the Minors fixed). The user accepted Q1–Q6 as
recommended on 2026-11-01 (§11), and the orchestrator ruled that S-I2 closes in 003.

## 1. Problem

`SESSION_ID` (`backlog_server.py:658`) is minted once per process: `{hostname}-{pid}-{nonce}`. On a stdio
server that is one agent. Under `taskmaster serve` it is every teammate at once:

- **Claims.** Ann's `backlog_pick_task` writes `locked_by = <host session>`. Bob's `backlog_claim(release)`
  then sees `mine=True` and releases it. Bob's `pick` of Ann's task is "already in progress" for him.
- **Receipts and queue keys.** `NativeCall.execute` sends `caller_scope = session` (`coordinator/adapter.py:54`),
  so every teammate's commands share one `(caller_scope, request_id)` namespace.
- **Other per-session state is process-global too**, and the addendum doesn't name it:
  `_session_task` and `_session_bundle` (`backlog_server.py:9043-9076`; the bundle feeds handover creation,
  `native_routing/handovers.py:67`), and `_CLIENT_AGENT` (`:241`, first `initialize` wins, 002 §10).
- **Liveness.** A remote session id that happened to match `SESSION_SHAPE` (`native/claims.py:144`) would be
  judged by host PID liveness (`claims.py:187-191`). The addendum's own example, `<handle>-<nonce>`, does
  match it for a handle such as `box-1234` (handles allow digits and inner hyphens,
  `team_identity.py:22`): `box-1234-deadbeef` reads as host `box`, pid 1234.

## 2. Threat model

Attackers are authenticated teammates (a valid member key) and their misbehaving agents. Unauthenticated
callers never reach a tool (001, 002, 006). The host's own processes are trusted (002 D8 trust boundary).

| # | Threat | Today | After 003 |
|---|---|---|---|
| T1 | **Claim theft by force.** Bob runs `backlog_pick_task(ann's task, force=True)` | succeeds | refused for members unless the claim has expired; operator-role keys may (D7) |
| T2 | **Release or renew of another's claim.** Bob's `backlog_claim(release/renew)` on Ann's task | succeeds (shared session) | refused unless proven expired (release only), as between peers today |
| T3 | **Session spoofing through arguments.** A tool argument or batch command that names a session or `caller_scope` | `commands=` refuses claim operations at the adapter (`batch_commands.py:23-36`); the core trusts `arguments.session` | the coordinator checks every served command's `caller_scope` and claim `session` against the principal, nested batch commands included (D5) |
| T4 | **Shared session state.** Current task, bundle and agent leak between teammates | shared | per connection (D4) |
| T5 | **Shape confusion.** A handle crafted to look like `host-pid` gets host PID liveness | possible with the addendum's shape | the remote shape can't match `SESSION_SHAPE`, and remote holders skip the PID check by rule (D1, D6) |
| T6 | **Publishing transport ids.** `locked_by` is exported to `.taskmaster/` and the `taskmaster-state` backup branch | n/a | the session nonce is minted with `secrets`, never derived from `mcp-session-id` (D2) |
| T7 | **Adoption by another person.** Bob adopts Ann's older sessions | n/a | the handle comes from the key, never from the client (002 D8), so adoption is same-handle only (D6) |
| T8 | **Context loss on a thread.** Code on a thread without the request context falls back to the host session and the `LOCAL` origin, which is the operator | n/a | on a served path only the merge-stamp replay (host work by design, run in an empty context, D10) and `backlog_sync`'s `taskmaster-sync-job-call` (no session, scope passed as an argument) run on their own threads; a tripwire test pins the inventory |
| T9 | **Claim theft through a status move.** Bob moves Ann's in-progress task to `done` or `archived` (which releases any claim, `claims.py:44-47,316-320`), then back to a pickable status and picks it: no `force` needed | succeeds | a member command never drops or moves another person's unexpired claim; the check runs on every task write inside the command (D11). The operator may still do it |

## 3. Decisions

**D1. A remote session id is `<handle>@<nonce>`, a 16-hex nonce.** Example: `ann@3f9a0c1e5b7d2a46`.

```python
# native/claims.py, next to SESSION_SHAPE; the handle part is team_identity.HANDLE_RE (a test pins equality)
CHANNELS = frozenset({"viewer"})                  # pseudo-sessions for non-MCP callers (D9); 007 may add one
REMOTE_SESSION_RE = re.compile(
    r"(?P<handle>[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)@(?P<nonce>[0-9a-f]{16}|viewer)")   # fullmatch
def remote_handle(session: str) -> str | None     # the handle, or None for any other shape
```

- `@` is in neither the handle charset nor a hostname, so the parse is unambiguous, a host-local id never
  reads as remote, and a remote id never fullmatches `SESSION_SHAPE` (its tail is `@<hex>`, never
  `-<digits>-<8 hex>`). This is the "widened shape" the addendum allows instead of `<handle>-<nonce>` (Q1).
- 64 random bits per connection: collisions are not a concern, and the id carries nothing secret.
- Host-local ids keep today's shape and meaning, unchanged.
- Fixed internal scopes (`coordinator-identity`, `explicit-sync`, `sync-…`, `git:…`) never match
  `REMOTE_SESSION_RE`, which is what lets 004 say "a remote principal reaches only scopes with its handle".

**D2. One session per MCP connection, minted by the server.**

- A *connection* is one streamable-HTTP MCP session: one `initialize`, one `mcp-session-id`, one transport in
  `StreamableHTTPSessionManager._server_instances` (`mcp/server/streamable_http_manager.py:279-280`). A
  Claude Code process that resumes with the same `mcp-session-id` keeps its session; a restarted process
  opens a new connection and gets a new session (addendum §7).
- `taskmaster/serve/sessions.py` holds a `SessionRegistry` in the daemon. On the first tool call of a
  transport it mints `<principal.handle>@<secrets.token_hex(8)>` and records `RemoteSession(session, handle,
  key_id, role, transport_id, agent, task=None, bundle=None)`. The transport id is the map key; it is never
  exported, never put in a claim, and logged only as its first 8 characters through `log_safe`.
- A binding never changes key. A tool call whose principal's `key_id` differs from the bound one raises
  `PermissionError`. The SDK already makes that unreachable, because it binds each session to the key that
  opened it (002 D6 point 2).
- Entries for transports that are gone are pruned by 002's sweep loop (`serve/app.py:117-125`) each round.
  The registry is bounded by the SDK's live transports.

**D3. Pull, not push: the session is derived from the request on every call.**

```python
# backlog_server.py, next to SESSION_ID
SESSION_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"   # this process's host-local session
SESSION_PROVIDER = None   # Callable[[], BoundSession | None]; only the serve daemon assigns it (like client.ORIGIN_PROVIDER)

def current_session() -> str:
    bound = _bound_session()
    return bound.session if bound is not None else SESSION_ID
```

- The provider (`SessionRegistry.current`) reads FastMCP's per-request context: `get_http_request()`
  (`fastmcp/server/dependencies.py:361-376`) for the `mcp-session-id` header, `auth.current_principal()` for
  the principal, and `request_ctx.get().session.client_params.clientInfo.name` (`mcp/server/session.py:108`)
  for the agent. Those are contextvars that FastMCP copies into each sync tool's worker thread
  (`anyio.to_thread.run_sync`, `fastmcp/utilities/async_utils.py:26-34`). Async tools run on the loop in the
  request's own context.
- Nothing is cached per thread. anyio reuses worker threads across calls and connections, so a
  `threading.local` would leak one teammate's session into the next call. The derivation is a dict lookup
  under a lock.
- **Once per tool call, by a call-scoped binding (review P-I1, S-M1).**
  - The binding is `_CALL_BOUND: ContextVar`, default an `_UNBOUND` sentinel.
  - `_route_native` (`backlog_server.py:125`), only when it is outermost (`_CALL_BOUND.get() is _UNBOUND`):
    - calls the provider once;
    - sets `_CALL_BOUND` to the result, `None` included, which means the host;
    - wraps the whole tool in it (the native route *and* the legacy fallback);
    - resets it in `finally`.
  - `_bound_session()` reads `_CALL_BOUND` first, and calls the provider only when it is unbound. Unbound
    paths are the viewer handlers and code outside tools.
  - So `current_session()` and the D4 accessors (`client_agent()`, `_set_session_task`, …) see one object
    for the whole call, nested tool calls included.
  - A `ContextVar` set and reset inside the call is not a per-thread cache: it lives in the copied context
    of this one call and is reset before the worker thread is reused.
  - The session string also travels as `registry.route(…, session)` → `NativeCall.session`. Native adapters
    read `call.session`, never the global.
- **Principal lookup (S-M5).** The provider reads the key through `auth.current_key_id()` before it takes
  `auth.current_principal()`. That way it inherits 005 D8's "operator ⇒ host-local" recheck once 005 lands,
  and it never mints `operator@…` off the host.
- **Fallback.** Outside an HTTP request (stdio, CLIs, tests, internal workers) the provider returns `None`
  and the host-local `SESSION_ID` applies, exactly as today.
- **Fail closed inside a request.** In an HTTP request with no principal, or with no valid
  `mcp-session-id`, the provider raises `PermissionError`. That is unreachable behind
  `RequirePrincipalMiddleware`. Reaching it is a bug, and it must never write as the host.
- Tests that monkeypatch `bs.SESSION_ID` keep working, because the fallback reads the global at call time.

**D4. Per-connection state moves onto the session.** `_set_session_task`, `_clear_session_task`,
`_get_session_bundle`, `_set_session_bundle`, `_clear_session_bundle` and `client_agent()` use the bound
session's `task`, `bundle` and `agent` when there is one, and the module globals otherwise. A new
`_get_session_task()` replaces the direct read at `:12066`. The globals stay as the host-local state, so
stdio behaviour and the tests that monkeypatch them are unchanged. In serve mode, `_ClientInfoCapture` still
sets the process global, but nothing reads it while a session is bound.

**D5. `caller_scope` is the session, and the coordinator checks it against the principal.**

- `NativeCall.execute` already sends `caller_scope = call.session`, so every served command's scope is
  `<handle>@<nonce>`. That is the server-side handle prefix the addendum asks for, with no new code in the
  tools.
- `ServeCoordinator` is the authority, because it is the only place that knows the origin (002 D8) and that
  every write passes. `serve/sessions.py` defines:

  ```python
  def requester(origin, principals) -> claims.Requester | None
      # LOCAL -> None (host-local: unrestricted, today's behaviour).
      # principal "operator" -> Requester("operator", may_force_any=True).
      # member key id -> its active record -> Requester(record.handle, may_force_any=record.role == "operator");
      #   unknown, revoked or unreadable -> IdentityRefused (002's REVOKED / UNAVAILABLE texts).
  def check_scope(request, who: Requester) -> None
      # remote_handle(request["caller_scope"]) != who.handle -> IdentityRefused(SESSION_REFUSED).
      # For each claim operation in the request, with `batch` flattened one level (contracts.py:57-64 refuses
      # deeper nesting) (task.pick, task.claim_renew, task.claim_release):
      # arguments["session"] != caller_scope -> IdentityRefused(SESSION_REFUSED).
  ```

- `ServeCoordinator.submit` runs both before the request is queued, so a mis-scoped request never takes a
  `pending` slot. `Coordinator._requester(work)` (base: `None`) runs them again in the writer and returns the
  requester for D7. That second run catches a key revoked while the request was queued.
- **The refusal is a plain `IdentityRefused(SESSION_REFUSED)` (review S-I1), not a subclass.** Both error
  maps match on the exact type name:
  - 006's sanitizer keeps a type only when its name is in `_DOMAIN_NAMES` (`serve/sanitize.py:170-182`);
  - the client rebuilds only named types (`coordinator/client.py:148-153`).

  A subclass would arrive as an internal `ServiceUnavailable` whose text says "retry". The plain type
  needs no edit in either 006 file. The client marks it `may_have_committed=False`, and the text alone
  identifies the case. A Step 5 test sends it through the real IPC client.
- The adapter-side refusal of claim operations in `commands=` (`batch_commands.py:23-36`) stays. D5 is
  defence in depth for a future tool that forgets `call.session`.

**D6. A claim belongs to a session; a person may adopt their own sessions that are gone.**

```python
# native/claims.py
CONNECTED = None     # Callable[[str], bool] | None; only the serve daemon assigns it (SessionRegistry.connected)

def connected(session) -> bool              # False when CONNECTED is None, or for a channel pseudo-session
def adoptable(holder, session) -> bool
    # holder != session, both remote-shaped with the same handle, and either not connected(holder) or the
    # session is a channel pseudo-session (D9: the person's own view of all their sessions; review S-M2).
def foreign_holder(task, session) -> str    # as today, but "" also when adoptable(holder, session)
def read(...) -> ClaimState                 # mine = holder == session or adoptable(holder, session)
def liveness(holder, row, ...)              # remote-shaped holder: True when connected(holder), else None.
                                            # Never the PID or host check, and never False (expiry is TTL; 010 adds the short disconnected TTL).
```

- **Same handle, connection gone: yours.** `pick` transfers the claim without `force`. `renew` re-stamps it
  for the new session, and `release` releases it. That is the addendum's adoption, for the case it was written
  for: a restarted client.
- **Same handle, connection still open: protected.** That is one person's two parallel agents, which today
  are two processes protected from each other. A pick needs `force=True`, and a member may force its own
  handle (D7). The refusal names the other open session (E3). This narrows the addendum's literal rule
  (Q2). The cost is that after a hard client crash, until the SDK's idle timeout ends the old transport
  (`session_idle_seconds`, 30 min), the restarted agent must pass `force=True` once.
- **Another handle: protected**, exactly as between two sessions today: no renew, release only once proven
  expired, and `force` only per D7.
- The writer thread judges claims too. `connected` is a process-wide lookup by session id string (the
  manager's dict, read without the event loop; one atomic `dict.get`), never a contextvar.
- Solo mode can't reach any of this: no remote-shaped session exists, `CONNECTED` is `None`, and
  `adoptable` requires both ids to be remote-shaped.
- **Owner-aware comparisons.** Every "is this mine" test goes through `foreign_holder` or `read().mine`:
  - `native_routing/tasks.py:387` (`locked_by != session`) becomes `foreign_holder`;
  - `native/blockers.py:252` (`claim.holder == session`) becomes `claim.mine or claim.holder == session`,
    reading a new `Claim.mine` field filled from `ClaimState.as_blocker()`. The exact half stays for
    `Claim`s built without a `ClaimState`;
  - the legacy `backlog_pick_task`/`backlog_claim` lines `8343` and `8462` follow suit;
  - the predicate definitions themselves in `native/claims.py` (`read` at `:251`, `foreign_holder` at
    `:269`, the new `adoptable`) compare exactly by definition.

  The "already bound to exactly this session" checks stay exact on purpose (`tasks.py:392,436`,
  `workflow.py:872`, `backlog_server.py:8305`): an adoptable holder must be re-picked so that the claim moves.
  The default context focus (`queries.py:433`, `backlog_server.py:6950`) stays exact as well (§10).

**D7. `force` across handles is an operator power.**

```python
# native/claims.py
@dataclass(frozen=True)
class Requester:
    handle: str
    may_force_any: bool

def force_refusal(task_id, state: ClaimState, requester: Requester | None) -> str | None
    # None (allowed) when: requester is None (host-local/solo: today's rule), not state.holder,
    # remote_handle(state.holder) == requester.handle, requester.may_force_any, or state.expired.
    # Otherwise the E2 text.
```

- `commands.execute(connection, envelope, *, …, requester=None)` stores it on `Transaction.requester`.
  `_task_pick` and `_bundle_pick` call `force_refusal` for each held member when `force` is set, inside the
  command's own transaction, and raise `Conflict` with its text. The bundle case is covered, and the check
  can't be separated from the write.
- `Coordinator._writer` passes `requester=self._requester(work)`. That is one new line and one new base
  method in the shared `service.py`, the same footprint as 002's `_origin`.
- The pick adapter calls the same `force_refusal`, with the requester taken from the bound session, before it
  submits, so the refusal is the tool's own text rather than a relayed `Conflict`. There is one wording, in
  `claims.py`.
- **Who has `may_force_any`:** the bootstrap operator on the host, and member keys minted with
  `--role operator` (002 D7). No one may *renew* another's claim (`claims.py:344-358`: "a steal wearing a
  smaller word"). An operator frees a live claim with force-pick, then release (Q3).

**D8. Adoption is visible, and needs no new action.**

- `backlog_pick_task` on an adoptable task commits `task.pick` and answers `Picked … (adopted from your
  earlier session \`ann@…\`; locked to this session)`. That includes the adapter's in-progress branch
  (`tasks.py:386-397`), which today answers "Already in progress" without committing. The bundle adapter's
  `Use force=True to steal.` (`tasks.py:432-435`) gets the same E2/E3 variants (review P-M9).
- `backlog_claim(renew)` adds `"adopted_from": "<old holder>"` when the holder changed.
- `backlog_claim(status)` without a `task_id` lists this session's claims as today, plus
  `"adoptable": [<claim state + holder>]`: every task held by another session of the same handle that is not
  connected. It finds them with one scan of `task_operational`, filtered by `remote_handle`.
- There is no `adopt` action and no adopt-all (Q6). The tool surface, 005's allowlist and the eval scenarios
  don't change.

**D9. The viewer gets a per-person pseudo-session.** In a viewer request, `auth.VIEWER_KEY_ID` is set (002
D8, 006). The provider looks the key id up in the `PrincipalRegistry` and returns a stateless
`ChannelSession("<handle>@viewer")`.

- It is never `connected`, and `adoptable` treats every same-handle holder as the channel session's own
  (D6). So all of a person's agent claims, live or gone, read as theirs on the board, and a viewer status
  move judges them the same way.
- It never holds a claim: viewer writes strip claim fields (`claims.without_claim_fields`) and the viewer has
  no pick.
- Its writes' `caller_scope` is handle-prefixed for 004.
- An unknown or revoked key id, or an unreadable registry, raises `PermissionError` (fail closed).
- 006 sets `VIEWER_KEY_ID` only around write handlers (`serve/viewer.py:593-602`), so viewer *reads* fall back
  to the host session: `mine` markers and the board ETag (`viewer_board.py:35`) are the host's. Q5 proposes
  wrapping read handlers in `viewer_principal` too.

**D10. Merge-stamp replay stays host-local.** `registry._replay_merge_stamps` (`native_routing/registry.py:110`)
runs queued *host* hook stamps on a plain thread.

- It now gets `bs.SESSION_ID` explicitly instead of the caller's session, so a teammate's call never
  attributes host work to that teammate.
- The thread body runs under `contextvars.Context().run(...)`, an empty context (review S-M4). On CPython
  3.11-3.13 a plain thread already starts empty. Free-threaded 3.14 inherits the caller's context by
  default, which would carry the teammate's request into `ORIGIN_PROVIDER` and `SESSION_PROVIDER`.
- Its origin is therefore `LOCAL`, which team mode refuses for attribution-bearing writes. Task 8 moves
  merge stamps to commands; until then this is the existing gap, now explicit.

**D11. A member never drops or moves another person's live claim, by any write (review S-I2, orchestrator
ruling).**

```python
# native/claims.py
def move_refusal(task_id, before: dict, after: dict, *, session, connection, requester) -> str | None
    # None when requester is None or requester.may_force_any; when no CLAIM_FIELDS value differs between
    # before and after; when before has no holder; when remote_handle(holder) == requester.handle (the
    # person's own sessions, live or gone); or when read(before, …, session=session).expired (judged on the
    # pre-move document, so a claim stranded on a done row is free). Otherwise the E7 text.
```

- **One funnel.** `Transaction.replace("task", …)` (`commands.py:205`) calls it with `before_entity`'s
  fields, the new document, `self.request["caller_scope"]` and `self.requester`, and raises `Conflict` with
  the text before anything is written. Every task write passes through that call:
  - `task.patch` (`commands.py:268`);
  - every workflow writer (`workflow.py:482,1434,1449,1494,1585,1598`): update or status, complete,
    archive, batch lines, viewer update and archive.

  So the terminal branch of `after_status_change`/`keeps_claim_through` and any future writer are
  covered.
- **It is the invariant behind D7, not a second rule.** Claim operations meet it on their own terms:
  - a member's allowed force pick is same-handle or expired, so it passes;
  - release of a foreign claim needs proof of expiry, so it passes;
  - renew never re-stamps a foreign holder.

  D7's `force_refusal` stays only for its clearer pick text, which fires first.
- Non-terminal status moves already keep a peer's unexpired claim (`survives_status_change`), so they
  change no claim field and pass.
- The operator, `may_force_any`, host-local and solo callers (`requester is None`) keep today's behaviour.

## 4. `SESSION_ID` inventory

`backlog_server.py`, 24 uses:

| Line | Where | Change |
|---|---|---|
| 125 | `_route_native` → `registry.route(…, SESSION_ID, …)` | `current_session()`, once per call (D3). This one line moves every native tool, its `caller_scope`, `backlog_context`'s session and `sync`'s scope |
| 658 | definition | kept as the host-local session; comment says so; plus `SESSION_PROVIDER`, `current_session`, `_bound_session` |
| 1358 | `_release_claim_on_status_change` (legacy) | `current_session()` |
| 1901 | `_derive_context` (legacy) | `current_session()` |
| 3575 | `backlog_next_available` (legacy) | `current_session()` |
| 7147, 7152 | `_legacy_context_answer` | one `session = current_session()` local |
| 8297, 8305, 8312, 8343, 8346, 8348, 8353, 8378, 8380, 8396 | `backlog_pick_task` (legacy) | one local at the top; `:8343` becomes `foreign_holder` (D6) |
| 8457, 8462, 8463, 8495 | `backlog_claim` (legacy) | one local; `:8462` becomes `read().mine` |
| 10769 | `_status_line` in `backlog_batch_update` (legacy) | `current_session()` |
| 10902 | `backlog_batch_preview` (legacy) | `current_session()` |
| 12067 | viewer `/session` | `current_session()` and `_get_session_task()` (D4) |

The legacy rows can't run under `serve`, which requires a native store (001 exit 2). They change anyway, so
that one accessor is the only reader of the global (a lint test, plan Step 3).

The other 8 files:

| File:line | Change |
|---|---|
| `native_routing/claims.py:46` | `call.session`; status adds `adoptable` (D8) |
| `native_routing/tasks.py:365, 993` | `call.session`; `:387` → `foreign_holder`; adoption text and `force_refusal` (D6-D8) |
| `native_routing/batch.py:61, 64, 239` | `call.session` (the overlay gets it from `_lines(call, …)`) |
| `native_routing/viewer.py:37` | `bs.current_session()` (D9) |
| `native_routing/registry.py:110` | `_replay_merge_stamps(…, bs.SESSION_ID)` (D10); `:16` `_client_agent()` already reads `client_agent()`, which becomes per connection (D4) |
| `viewer_board.py:35` | ETag uses `bs.current_session()` |
| `viewer_detail.py:131` | `bs.current_session()` |
| `native/claims.py:141-152` | comment rewritten for the two shapes; D1, D6, D7 and D11 code |

`tests/` and `scripts/viewer_fixture_server.py` keep reading `bs.SESSION_ID`: they run without a provider, so
it is the session in effect.

## 5. Propagation into the thread pool, in detail

- **What travels.** FastMCP calls a sync tool through `call_sync_fn_in_threadpool` →
  `anyio.to_thread.run_sync` (`fastmcp/utilities/async_utils.py:26-34`), which runs it in a copy of the
  caller's context. The vars 003 reads are all set before that hop, on the request's task:
  - mcp's `request_ctx`, which carries the `ServerSession` and the Starlette `Request`;
  - FastMCP's `_current_context` and HTTP-request var;
  - the auth context that `get_access_token()` reads (002 relies on the same fact).
- **What doesn't travel.** A `threading.Thread` started inside a tool, and the coordinator's writer thread.
  - The writer never needs the request: the origin travels in the IPC message (002), and claim ownership
    is decided from session strings plus `claims.CONNECTED`, which is process-wide.
  - Tool code that hands work to its own thread must pass the session and origin explicitly, or run the
    work under `contextvars.copy_context().run`. On a served path there are two such threads:
    - the merge-stamp replay (D10, run in an empty context);
    - `backlog_sync`'s `taskmaster-sync-job-call` (`coordinator/client.py:434`, review S-M6). It carries
      no session; its scope is passed as an argument, and 005 D3 resolves its origin before the thread
      starts.
  - `store.py:2278` runs only on legacy stores.
  - Don't assume a plain thread starts with an empty context: free-threaded CPython 3.14 inherits it by
    default (D10).
- **Per-thread caches are banned** for anything request-derived (D3). The existing `threading.local`s
  (`_TX_STATE`, `_TOOL_DEPTH`, `registry._DEPTH`) are per-call stacks that unwind on return, and they hold
  no identity.

## 6. Solo mode, unchanged

With no daemon:

- `SESSION_PROVIDER` and `CONNECTED` are `None`, so `current_session()` is `SESSION_ID`;
- `requester` is `None`, so `force` and status moves behave as today (D7, D11 are no-ops);
- no remote-shaped id exists, so `adoptable` is always `False` and `mine` is exact equality, as today;
- `liveness` is unchanged for every host-shaped holder;
- the per-session state lives in the same module globals.

The regression set (plan §5) covers claims, bundles, context, the viewer session endpoint and the client
agent. A stdio process on a store that once was served sees remote holders as foreign peers, with unknown
liveness and the TTL as the bound. That is the safe direction.

## 7. Error shapes

| # | When | Shape |
|---|---|---|
| E1 | Coordinator: `caller_scope` or a claim `session` names another handle (D5) | plain `IdentityRefused` (not retryable, `may_have_committed=False`): `Refused: this request names a session that does not belong to your key. Nothing was written.` |
| E2 | A member's `force` pick of another handle's unexpired claim (D7) | pick: `Error: task \`X\` is claimed by bob (\`bob@…\`). Only an operator can take a teammate's claim with force. Ask bob to release it, or wait until it expires at <ts>; then \`backlog_claim(action="release", task_id="X")\` frees it. Nothing was changed.` The core raises the same text as `Conflict` |
| E3 | Pick without force, holder is the same handle's still-connected session (D6) | `lock_refusal` variant: `… is held by your other open session (\`ann@…\`). Pick a different task, or use \`backlog_pick_task(X, force=true)\` to take it over from that session.` |
| E4 | `backlog_claim` renew or release blocked (D6) | existing `claim_conflict` JSON; `hint` is requester-aware: E2's advice for another handle, E3's for your own open session, today's `force=True steals it` only with no requester or `may_force_any` |
| E5 | Provider inside a request without a principal or session id (D3) | `PermissionError("authenticated principal missing")` / `("MCP session id missing")`: the tool call fails; nothing written |
| E6 | Adoption | pick text `(adopted from your earlier session \`ann@…\`)`; renew JSON `adopted_from`; status JSON `adoptable` |
| E7 | A member's write that would drop or move another handle's unexpired claim (D11): status to `done`/`archived`, complete, archive, batch line, viewer write | `Conflict`, relayed by each tool as its `Error:` text: `task \`X\` is claimed by bob (\`bob@…\`) until <ts>. This change would release or move that claim, and only an operator can do that to a teammate's claim. Ask bob to release it first, or wait until it expires. Nothing was changed.` One E7 line rejects the whole `backlog_batch_update`: the batch is one command, so its answer is the batch's "state changed since the lines were checked" refusal carrying this text, and no line commits (review R-M2). |

No text holds a transport id, a key or a path. Session ids are not secret. Handles come from the validated
principals file.

## 8. Seams for the other lanes

- **004 (receipts, queue, request ids).** Every served `execute` has `caller_scope` `<own handle>@…`, checked
  at submit before the `pending` key exists (D5). `claims.remote_handle(scope)` is the one parser 004 uses to
  scope `receipt`, `cancel` and `linear_retry` (whose `caller_scope` argument, `native_routing/overview.py:306`,
  is caller-supplied and stays 004's). Internal scopes never parse as remote. Those IPC methods don't carry
  an origin yet (002 §13). That is 004's. Receipts are now per connection, so a restarted agent that
  retries with its *old* scope names its own handle. 004 should accept any same-handle scope, the receipt
  analogue of adoption (review S-M7).
- **010 (connection-bound renewal, short disconnected TTL).** `SessionRegistry.live()` lists connected
  sessions with `key_id`, and `on_disconnect` callbacks run from `prune()` with the `RemoteSession` that just
  went away. A renewal submits `task.claim_renew` with `caller_scope = arguments.session = that session` and
  `Origin(principal=key_id)`, which passes D5. `liveness` already answers `True` for connected holders and
  `None` otherwise; 010 adds the short expiry stamp at disconnect and the post-restart grace.
  `on_disconnect` runs on the event loop (in the sweep task). A callback must hand its `submit` off to a
  thread and never block on `.result()` there (S-M7).
- **011 (per-clone machine-local fields).** `RemoteSession` is the per-connection record. A clone identity
  for the connection belongs on it, next to `bundle`, which already carries the per-session
  `worktree`/`branch`.
- **005 (allowlist).** No new tool, argument or IPC method. Both lanes edit `serve/daemon.py` and maybe
  `serve/app.py`; whoever lands second merges.
- **006 (merged).** `build_app` gains `sessions=` and the sweep calls `sessions.prune()`. Q5 touches the
  `wrap` loop in `serve/viewer.py`.
- **007 (read endpoint).** For "mine" in hook and mod reads, 007 adds a channel word to `claims.CHANNELS`
  (for example `hook`) and passes `<handle>@hook` as the session. D6 then treats the person's disconnected
  claims as theirs.
- **009 (split brain).** Unaffected. Host-local sessions in the host clone are 009's to refuse, and the
  fallback to `SESSION_ID` in the daemon only serves host-internal code paths.
- **012 and 013.** `call.sync(caller_scope=self.session)` becomes handle-prefixed for free. Exported
  `locked_by` values become `ann@<nonce>`, which carry nothing secret (T6).

## 9. Split

The work could split by the plan's steps (review P-M8):

- **A**, plan Steps 1, 3, 4 and 5: shape and predicates, accessors and inventory, the registry, the D5
  scope check;
- **B**, plan Steps 2, 6, 7 and 8: the requester rules (D7, D11), the tool texts, end-to-end tests and
  docs.

A alone fixes T3 and T4 and unblocks 004 and 010. B is the security-sensitive half. **Decided (Q4): one
task, one branch, one review gate.**

## 10. Out of scope

- Receipt, `cancel`, `receipt`-lookup and `linear_retry` scoping (004).
- Renewal while connected, the short disconnected TTL, the grace after restart (010).
- Per-clone `worktree`/`branch` and client-side managed Git (011).
- Task authority beyond claims: a teammate may still change the status of an *unclaimed* or expired
  task. Dropping a live claim by a status move is closed by D11 (the former "gap for 015").
- In the daemon, an `execute` with no principal (a lost context, T8) still counts as host-local and keeps
  unrestricted `force` (review S-M3). The tripwire covers this; refusing it needs 005 D4's
  daemon-vs-host distinction, which is a follow-up.
- Listing remote sessions in `backlog_store_status` (the `sessions` table is legacy-written; follow-up).
- Default `backlog_context` focus on an adoptable claim after a reconnect (`queries.py:433`; follow-up).
- A cap on MCP sessions per key: the registry is bounded by the SDK's transports and their idle timeout
  (006 follow-up).

## 11. Decided questions (user ruling 2026-11-01: every recommendation accepted)

- **Q1. Shape.** Decided: `<handle>@<16 hex>`, not the addendum's `<handle>-<nonce>`, which collides with
  `SESSION_SHAPE` (§1). The addendum §3 text should be updated to match.
- **Q2. Adoption of still-connected own sessions.** Decided: adoption only for disconnected sessions, so
  one person's parallel agents stay protected from each other (D6). The cost is one `force=True` after a
  crash, within the 30 min idle window.
- **Q3. Operator override.** Decided:
  - the override is `force` on pick, for operator-role keys and the bootstrap operator;
  - nobody may renew another person's claim;
  - a live claim is freed by force-pick, then release;
  - there is no new `force` on `backlog_claim(release)`.

  D11 extends the same power to status moves: only the operator may drop a teammate's live claim that
  way. The addendum §3 phrase "renew or release … unless it is the operator" narrows accordingly.
- **Q4. Split.** Decided: one task (§9).
- **Q5. Viewer reads.** Decided: wrap 006's viewer read handlers in `viewer_principal` too, in 003, so the
  board's `mine` and ETag are per person.
- **Q6. An explicit `adopt` action or adopt-all.** Decided: no. `pick` and `renew` adopt per task, and
  `status` lists what is adoptable.

## 12. Risks

- **R1. SDK internals.** `connected` reads `_server_instances` and `is_terminated`
  (`streamable_http_manager.py:238-240,309-326`), which 002's `check_session_internals` already pins.
  `client_params` is public mcp API. Step 4 tests pin the behaviour.
- **R2. A tool path that forgets `call.session`** would write as the host (`LOCAL` origin plus the host
  session). D5 refuses a remote origin with a non-remote scope, but a `LOCAL` origin passes. Mitigated by the
  lint test (no reads of the global outside the accessor) and the thread-inventory tripwire (§5).
- **R3. Shared files.** `service.py` (one line plus a base method), `commands.py` (one keyword argument and
  the D11 call in `Transaction.replace`),
  `workflow.py` and `claims.py` are shared with team-collab branches. Keep the edits minimal.
- **R3a. Old host-shaped locks at cutover** (review R-M3). D11 also protects a `host-pid-nonce` claim written
  on another machine before the server existed: it has no stored expiry and its liveness cannot be judged
  here, so it never expires and a member cannot drop it. Only an operator (force, or a status move) can. 016's
  migration how-to says so: at cutover the operator lists old host-shaped claims (`backlog_claim status` on
  the host, or `locked_by` not matching `<handle>@…`) and clears the stale ones.
- **R3b. A reinstated handle adopts by handle** (gate Minor 2). `add-user` may add a revoked handle again.
  Adoption (D6) and same-person force (D7) go by handle alone, so the new key adopts the earlier holder's
  disconnected claims without force. Handles are person identities: re-issue one only to the same person.
- **R3c. Two meanings of "operator", both by design** (gate Minor 3). Under 003 D7 a member key with
  `role=operator` has `may_force_any` and can force a teammate's claim from off the host. Under 005 D8/M3 that
  key is not the operator for admin methods: only the host-local bootstrap token is. Do not align one with the
  other without a design decision.
- **R4. Exact-equality sites missed.** The inventory lists them by grep (`locked_by … == session`, `holder ==
  session`, `!= SESSION_ID`). A Step 6 test asserts that a grep for those patterns finds only the documented
  exact sites.
