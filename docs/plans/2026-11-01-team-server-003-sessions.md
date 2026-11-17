<!-- User intent: the executable, test-first plan for team-server-003. Each teammate's agent connection to the
     shared server gets its own session; claims belong to the person, a restarted agent takes back its own work,
     and no teammate can take, renew or release another's claim by naming a session. Solo mode stays as it is. -->

# team-server-003: per-connection sessions and claim ownership (plan, 2026-11-01)

**Task.** `team-server-003` (server mode, addendum §11 task 3).
**Design.** `docs/specs/2026-11-01-team-server-003-sessions-design.md` (rev 2). Its decisions D1 to D11 are
binding here. Q1 to Q6 were decided as recommended (user ruling 2026-11-01). §7 records what a later reversal
would change.
**Rev 2.** Plan review WARN; P-I1, P-I2, P-I3 and the Minors fixed. S-I2 is closed here by D11
(orchestrator ruling).
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-003`, branch
`feature/team-server-003`, base `748cd29` (feat/team-collab with 001, 002 and 006 merged).
**Executor.** One engineer, test-first. Each step:

1. Write the step's tests.
2. Run them and watch them fail for the stated reason (§4).
3. Implement.
4. Run the step's tests and the regression subset (§5).
5. Commit with the step's message.

Steps are ordered by dependency; run them in order. In design §9's terms, Steps 1, 3, 4 and 5 are half A,
and Steps 2, 6, 7 and 8 are half B. The task is not split (Q4).
Every decision is already made: if something here seems wrong, stop and report it rather than redesigning.

**Done when:**

- Over real HTTP, two keys' connections hold claims as `ann@<nonce>` and `bob@<nonce>`. Two connections on one
  key hold two different sessions. Current task, bundle and agent are per connection.
- Bob can't pick (even with `force`), renew or release Ann's unexpired claim. He also can't drop it by
  any other write: moving it to `done` or `archived`, `backlog_complete_task`, `backlog_archive_task`, a
  batch line, or a viewer write (D11). An operator-role key can do all of these.
- Ann's restarted agent picks or renews her disconnected session's task without `force`, and the answer says
  it adopted it. Her other *open* session's task needs `force`.
- A command whose `caller_scope` or claim `session` names another handle is refused by the coordinator, and
  nothing is written.
- With no daemon, every existing claims, bundle, context, viewer-session and client-agent test passes
  unchanged.

**Out of scope:** design §10 (004 receipt scoping, 010 renewal and TTL, 011, status authority over
unclaimed tasks, store_status listing, context focus fallback, per-key MCP session caps, refusing `force`
for a context-less daemon call).
**Shared files touched minimally:**

- `coordinator/service.py`: one base method and one keyword at the `commands.execute` call.
- `native/commands.py`: one keyword plumbed to `Transaction`, plus the D11 call in `Transaction.replace`.
- `native/workflow.py`: the force rule in two pick functions.

---

## 1. Files

Create:

| File | Header (`# User intent: …`, first line) |
|---|---|
| `taskmaster/serve/sessions.py` | `one Taskmaster session per teammate's MCP connection on the shared server, minted by the server and derived from the authenticated request on every call, so no teammate's claims, current task or agent leak into another's.` |
| `tests/test_claim_ownership.py` | `claims belong to a session, a person may adopt their own sessions that are gone, and a remote session can never be judged by the host's process table.` |
| `tests/test_claim_force_rule.py` | `inside the command's own transaction, only an operator can force-take a teammate's claim or drop it by any other write; host-local and solo callers keep today's rule.` |
| `tests/test_session_scope.py` | `every tool reads the session of the connection it serves, once per call, and per-session state (current task, bundle, agent) follows the connection; without a server nothing changes.` |
| `tests/test_serve_sessions.py` | `each MCP connection gets its own server-minted session that survives the tool thread pool, stays bound to one key, and is forgotten when the connection ends.` |
| `tests/test_serve_session_guard.py` | `the server's coordinator refuses any command whose session belongs to another person, before it is queued and again before it runs.` |
| `tests/test_serve_sessions_e2e.py` | `over real HTTP, teammates hold claims as themselves, cannot take or free each other's, and a restarted agent takes back its own work.` |

Modify:

| File | Change |
|---|---|
| `taskmaster/native/claims.py` | D1 shape and `remote_handle`; D6 `CONNECTED`, `connected`, `adoptable`, `foreign_holder`, `read().mine`, `liveness`; D7 `Requester`, `force_refusal`; D11 `move_refusal`; requester-aware `conflict` hint; comment at `:140-152` (Steps 1, 2, 6) |
| `taskmaster/native/blockers.py` | `Claim.mine`; `_claim_blockers` uses it (Step 1) |
| `taskmaster/native/commands.py` | `execute(…, requester=None)`, `Transaction.requester`; `Transaction.replace("task", …)` calls `claims.move_refusal` (Step 2) |
| `taskmaster/native/workflow.py` | `force_refusal` in `_task_pick` and `_bundle_pick` (Step 2) |
| `taskmaster/coordinator/service.py` | `Coordinator._requester(work)` returns `None`; `_writer` passes `requester=` (Step 2) |
| `taskmaster/backlog_server.py` | `SESSION_PROVIDER`, `current_session`, `_bound_session`, `_get_session_task`; per-session accessors and `client_agent`; the 24 uses (design §4) (Step 3) |
| `taskmaster/native_routing/registry.py`, `claims.py`, `tasks.py`, `batch.py`, `viewer.py`; `taskmaster/viewer_board.py`, `viewer_detail.py` | design §4 rows (Steps 3 and 6) |
| `taskmaster/serve/app.py` | `build_app(…, sessions=None)`: attach the manager; the sweep calls `sessions.prune()` (Step 4) |
| `taskmaster/serve/daemon.py` | `ServeCoordinator.submit` and `_requester`; `ServeDaemon._start`/`_teardown` install and restore `SESSION_PROVIDER` and `claims.CONNECTED` and pass `sessions=` to `build_app` (all in Step 5) |
| `taskmaster/serve/viewer.py` | read handlers also run under `auth.viewer_principal` (Q5; Step 6) |
| `tests/serve_helpers.py` | `open_connection(url, token, client_name="claude-code")` (Step 4) |

Checked and **not** modified:

- `serve/sanitize.py` (`DOMAIN_ERRORS`, `:170-182`) and `coordinator/client.py` (its error map,
  `:148-153`): D5 raises a plain `IdentityRefused`, which both already carry by exact name, so neither
  006 file needs an entry (design D5, review S-I1).
- The Step 5 IPC test proves the path end to end.

---

## 2. API (exact)

### 2.1 `taskmaster/native/claims.py` (additions)

```python
REMOTE_HANDLE = r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?"            # == team_identity.HANDLE_RE.pattern (test-pinned)
CHANNELS = frozenset({"viewer"})
REMOTE_SESSION_RE = re.compile(rf"(?P<handle>{REMOTE_HANDLE})@(?P<nonce>[0-9a-f]{{16}}|{'|'.join(sorted(CHANNELS))})")
CONNECTED = None        # Callable[[str], bool] | None; assigned only by the serve daemon (SessionRegistry.connected)

def remote_handle(session) -> str | None
    # isinstance str and REMOTE_SESSION_RE.fullmatch -> the handle; else None.
def connected(session: str) -> bool
    # remote nonce in CHANNELS -> False; CONNECTED None -> False; else bool(CONNECTED(session)).
def adoptable(holder: str, session: str) -> bool
    # holder != session and remote_handle(holder) is not None and remote_handle(holder) == remote_handle(session)
    # and (not connected(holder) or REMOTE_SESSION_RE.fullmatch(session)["nonce"] in CHANNELS)   # S-M2
def foreign_holder(task, session) -> str
    # today's body; return "" also when adoptable(holder, session).
def liveness(holder, row, *, now=None, hostname=None, pid_alive=None)
    # FIRST: remote_handle(holder) is not None -> True if connected(holder) else None. Rest unchanged.
def read(...) -> ClaimState
    # mine = holder == session or adoptable(holder, session). Everything else unchanged.
ClaimState.as_blocker() -> blockers.Claim(holder=…, live=…, expired=…, mine=self.mine)

@dataclass(frozen=True)
class Requester:
    handle: str
    may_force_any: bool

def force_refusal(task_id: str, state: ClaimState, requester: "Requester | None") -> str | None
    # None when requester is None, not state.holder, state.mine, remote_handle(state.holder) == requester.handle,
    # requester.may_force_any, or state.expired. Otherwise (E2):
    # f"task `{task_id}` is claimed by {who} (`{state.holder}`). Only an operator can take a teammate's claim with "
    # f"force. Ask {who} to release it, or wait until it expires{at}; then "
    # f"`backlog_claim(action=\"release\", task_id=\"{task_id}\")` frees it."
    # who = remote_handle(holder) or "another session"; at = f" at {state.expires_at}" when set, else "".
    # The adapter prefixes "Error: " and suffixes " Nothing was changed."; the core raises Conflict(text).
def own_open_session_refusal(task_id: str, state: ClaimState) -> str
    # E3: f"Error: task `{task_id}` is held by your other open session (`{state.holder}`). Pick a different task, "
    # f"or use `backlog_pick_task({task_id}, force=true)` to take it over from that session."
def move_refusal(task_id, before: dict, after: dict, *, session, connection, requester) -> str | None
    # D11. None when: requester is None or requester.may_force_any; no CLAIM_FIELDS value differs between
    # before and after (canonical encode compare); before has no holder; remote_handle(holder) == requester.handle;
    # or read(before, task_id=task_id, session=session, connection=connection).expired (pre-move document).
    # Otherwise (E7): f"task `{task_id}` is claimed by {who} (`{holder}`){until}. This change would release or move
    # that claim, and only an operator can do that to a teammate's claim. Ask {who} to release it first, or wait
    # until it expires. Nothing was changed."   who/until as in force_refusal.
def conflict(state, requester=None) -> dict
    # As today; "hint" is HINT when requester is None or requester.may_force_any; the E3 sentence when
    # remote_handle(state.holder) == requester.handle; otherwise the E2 advice (ask / wait / release).
```

`blockers.Claim` gains `mine: bool = False`; `_claim_blockers` returns `[]` when `claim.mine or claim.holder == session`.

### 2.2 `native/commands.py`, `native/workflow.py`, `coordinator/service.py`

```python
# commands.py
class Transaction:
    def __init__(self, connection, request, identity, requester=None): ...   # self.requester = requester
def execute(connection, envelope, *, cancelled=lambda: False, checkpoint=lambda stage: None, requester=None)
    # passes requester to Transaction(…); nothing else changes. The requester is not part of the request, so
    # the payload hash and replay are unaffected.
# Transaction.replace, first thing when kind == "task" (after `entity` is resolved, before any write):
    text = claims.move_refusal(ident, entity["fields"], after, session=self.request.get("caller_scope", ""),
                               connection=self.connection, requester=self.requester)
    if text: raise contracts.Conflict(text)

# workflow.py, _task_pick: after `locked_by = claims.foreign_holder(task, session)`:
if locked_by and force:
    text = claims.force_refusal(ident, _claim_state(transaction, task, ident, session), transaction.requester)
    if text: raise Conflict(text)
# _bundle_pick: the same per member whose holder is foreign, before any write.

# service.py
class Coordinator:
    def _requester(self, work):
        return None                       # session coordinators: today's rule
# _writer, before `if self.team_identity.admit(...)`:  requester = self._requester(work)
#          and:  commands.execute(connection, request, cancelled=…, checkpoint=admitted, requester=requester)
```

### 2.3 `taskmaster/backlog_server.py`

```python
# ── Session identity ─────
SESSION_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"   # this process's host-local session
SESSION_PROVIDER = None   # Callable[[], object | None]; only the serve daemon assigns it (design D3)
_UNBOUND = object()
_CALL_BOUND: ContextVar = ContextVar("taskmaster_call_session", default=_UNBOUND)   # call-scoped (P-I1)

def _bound_session():
    # value = _CALL_BOUND.get(); value is not _UNBOUND -> value (None means host-local).
    # Else provider = SESSION_PROVIDER; None -> None; else provider() (exceptions propagate: fail closed).
    # The returned object has .session (str), .agent (str), .task, .bundle (mutable), and optionally .requester().
# _route_native's wrapper, outermost call only (_CALL_BOUND.get() is _UNBOUND):
#     token = _CALL_BOUND.set(<provider() or None>)   # once; then session = current_session()
#     try: <native route, else the legacy fn> finally: _CALL_BOUND.reset(token)
# A nested tool call sees the outer binding and does not call the provider again.
def current_session() -> str
    # bound.session if bound else SESSION_ID (read at call time).
def _session_requester():
    # bound.requester() when bound has it, else None. For the pick adapter (D7).

def _get_session_task() -> dict | None          # bound.task if bound else _session_task
# _set_session_task, _clear_session_task, _get/_set/_clear_session_bundle: the bound object's attribute when
# bound, else the module global exactly as today.
def client_agent() -> str                        # bound.agent if bound else (_CLIENT_AGENT or "")
```

The 24 uses change as in design §4. `_route_native` computes `session = current_session()` once, before
`registry.route`. `_serve_session` uses `_get_session_task()` and `current_session()`.

### 2.4 `taskmaster/serve/sessions.py`

```python
LOG = logging.getLogger("taskmaster.serve")
SESSION_REFUSED = "Refused: this request names a session that does not belong to your key. Nothing was written."
CLAIM_OPERATIONS = ("task.pick", "task.claim_renew", "task.claim_release")
TRANSPORT_ID_RE = re.compile(r"[\x21-\x7e]{1,256}")    # visible ASCII, as mcp mints and checks it

# No exception subclass: refusals are plain IdentityRefused(SESSION_REFUSED), which the 006 sanitizer and the
# IPC client both keep by exact type name (design D5, review S-I1).

@dataclass
class RemoteSession:
    session: str; handle: str; key_id: str; role: str; transport_id: str; agent: str
    started: str                                    # UTC stamp, claims.STAMP
    task: dict | None = None; bundle: dict | None = None
    def requester(self) -> claims.Requester         # Requester(handle, may_force_any = role == "operator")

@dataclass(frozen=True)
class ChannelSession:                              # D9; stateless, rebuilt per request
    session: str; handle: str; role: str
    agent: str = ""
    @property task / bundle -> None; setters ignore (a viewer has no current task)
    def requester(self) -> claims.Requester

class SessionRegistry:
    def __init__(self, principals: PrincipalRegistry): ...      # threading.Lock; dicts by transport id and by session
    def attach(self, manager) -> None                          # from build_app's lifespan; detach() on exit
    def bind(self, transport_id: str, principal: Principal, agent: str) -> RemoteSession
        # Under the lock. Existing entry: key_id must equal principal.key_id, else PermissionError("session belongs
        # to another key"); return it. New: session = f"{principal.handle}@{secrets.token_hex(8)}" (redrawn on the
        # impossible collision); assert claims.REMOTE_SESSION_RE.fullmatch; store; LOG.info("session %s opened for
        # %s (key %s, transport %s)", …) with every value through serve.app.log_safe and the transport id cut to 8.
    def current(self):                                          # installed as backlog_server.SESSION_PROVIDER
        # 1. try request = fastmcp.server.dependencies.get_http_request() except RuntimeError: request = None.
        # 2. request is not None:
        #      auth.current_key_id()  (first, so 005 D8's operator => host-local recheck applies; S-M5), then
        #      principal = auth.current_principal() or raise PermissionError("authenticated principal missing");
        #      tid = request.headers.get("mcp-session-id"); not tid or not TRANSPORT_ID_RE.fullmatch(tid) ->
        #        raise PermissionError("MCP session id missing");
        #      agent = actor.agent_for_client(name) where name is request_ctx.get().session.client_params.clientInfo.name
        #        ("" on LookupError/AttributeError);
        #      return self.bind(tid, principal, agent).
        # 3. key_id = auth.VIEWER_KEY_ID.get(): when set -> "operator" gives ChannelSession("operator@viewer",
        #      "operator", "operator"); else record = principals.lookup(key_id) (RegistryUnavailable -> PermissionError);
        #      None or not active -> PermissionError("viewer key is not active"); ChannelSession(f"{handle}@viewer", …).
        # 4. None.
    def connected(self, session: str) -> bool                  # installed as claims.CONNECTED
        # entry = by_session.get(session); manager = self._manager; either None -> False;
        # transport = manager._server_instances.get(entry.transport_id);
        # return transport is not None and not transport.is_terminated.
    def live(self) -> list[RemoteSession]                      # connected entries (010)
    on_disconnect: list[Callable[[RemoteSession], None]]       # 010 seam; runs on the event loop: never block; errors logged
    def prune(self) -> list[RemoteSession]
        # Under the lock: drop entries whose transport is gone or terminated; LOG.info("session %s closed", …);
        # then call each on_disconnect callback per dropped entry (outside the lock). Returns the dropped.

def requester(origin, principals) -> claims.Requester | None
    # origin.principal None -> None. "operator" -> Requester("operator", True).
    # Else try principals.lookup(id) except RegistryUnavailable: raise IdentityRefused(identity.UNAVAILABLE);
    # None/inactive -> IdentityRefused(identity.REVOKED); Requester(record.handle, record.role == "operator").
def claim_operations(request) -> list[tuple[str, dict]]
    # [(operation, arguments)] for the request itself, plus each command of a "batch" request's
    # arguments["commands"] (one level, as the core nests it), filtered to CLAIM_OPERATIONS.
def check_scope(request, who: claims.Requester) -> None
    # claims.remote_handle(request["caller_scope"]) != who.handle -> IdentityRefused(SESSION_REFUSED).
    # any(args.get("session") != request["caller_scope"] for _op, args in claim_operations(request)) -> same.
```

### 2.5 `taskmaster/serve/app.py`, `daemon.py`

```python
# app.py
def build_app(…, registry=None, sweep_seconds=…, sessions=None)
    # lifespan: after check_session_internals(manager): if sessions is not None: sessions.attach(manager);
    # detach in finally. The sweep task also runs when sessions is not None; each round:
    # sessions.prune() (any exception logged at ERROR, never raised), then the revocation step as today.
# daemon.py — ServeCoordinator
def __init__(self, root, *, registry=None, **kwargs)     # keeps self.principals = registry
def submit(self, envelope, *, origin=LOCAL):
    who = sessions.requester(origin, self.principals)
    if who is not None:
        sessions.check_scope(contracts.validate(envelope)[0], who)   # pure; validate errors surface as today
    return super().submit(envelope, origin=origin)
def _requester(self, work):
    who = sessions.requester(work.origin, self.principals)
    if who is not None:
        sessions.check_scope(work.request, who)
    return who
# ServeDaemon._start, next to the ORIGIN_PROVIDER pin (restored in _teardown the same way):
#   self.sessions = SessionRegistry(registry)
#   bs.SESSION_PROVIDER = self.sessions.current; claims.CONNECTED = self.sessions.connected
#   build_app(…, registry=registry, sessions=self.sessions)
# No other code assigns SESSION_PROVIDER or CONNECTED.
```

### 2.6 `tests/serve_helpers.py`

```python
class Connection:
    """One long-lived MCP connection on its own event-loop thread; .call(name, args) -> text; .close()."""
def open_connection(url, token, client_name="claude-code") -> Connection   # fastmcp Client with client_info
```

---

## 3. TDD steps

New test files start with their `# User intent:` header (§1). Use `tmp_path` and the existing fixtures. Never
use the live `.taskmaster/`. Fixtures to reuse:

- native roots: `root` from `tests/test_native_service_build.py`;
- the in-process native tool project: `native_project` in `tests/test_batch_structured_commands.py:45`;
- twins: `tests/native_twins.py`;
- the real daemon: `launch` in `tests/test_serve_principals_e2e.py:38`;
- the in-process `ServeCoordinator` world: `tests/test_serve_identity.py:28`;
- `team_on`: `tests/test_serve_identity.py:154`.

A **fake bound session** for in-process tool tests is
`monkeypatch.setattr(bs, "SESSION_PROVIDER", lambda: holder_obj)`, where `holder_obj` is a
`SimpleNamespace(session=…, agent=…, task=None, bundle=None, requester=lambda: Requester(…))`. Pair it with
`monkeypatch.setattr(claims, "CONNECTED", lambda s: s in live)`.

### Step 0: baseline

Run the regression subset (§5) on the untouched branch. Record any failures by node id in the Step 1 commit
message body: they are not this task's. Use the repo venv only: bare `python` is the wrong interpreter
(B-100). No commit.

### Step 1: the remote shape and ownership predicates (`native/claims.py`, `native/blockers.py`)

Tests in `tests/test_claim_ownership.py` (pure; `connection=None` where a row is not needed):

- `test_remote_handle_parses_only_the_remote_shape`:
  - `remote_handle("ann@" + "a"*16) == "ann"`;
  - `remote_handle("box-1234@" + "0"*16) == "box-1234"`;
  - `"ann@viewer"` gives `"ann"`.
  - `None` for each of:
    - a host-local `f"{host}-{pid}-deadbeef"`;
    - `"ann-1234-deadbeef"`;
    - `"Ann@" + "a"*16`;
    - `"ann@" + "a"*15`;
    - `"ann@" + "A"*16`;
    - `"ann@hook"`;
    - `"@" + "a"*16`;
    - `"coordinator-identity"`, `"explicit-sync"`, `"sync-" + "a"*64`, `"git:x"`;
    - `""`, `None`, `5`.
- `test_handle_pattern_is_team_identitys`: `claims.REMOTE_HANDLE == team_identity.HANDLE_RE.pattern`.
- `test_remote_ids_never_match_session_shape`: for 200 random handles drawn from the handle grammar (including
  `x-<digits>` forms) with random nonces, `SESSION_SHAPE.fullmatch(id) is None` and `identity(id) is None`.
- **`test_remote_holder_is_never_judged_by_host_pids`**:
  - holder `f"{socket.gethostname()}-1@" + "a"*16`, with `pid_alive` and `hostname` stubs that raise when
    called;
  - `liveness` is `None` with `CONNECTED` `None`;
  - with `CONNECTED = lambda s: True` it is `True`;
  - with `lambda s: False` it is `None`, never `False`.
- `test_adoptable_needs_same_handle_and_a_gone_holder` (parametrized table, `CONNECTED = lambda s: s in live`):
  - same handle, holder not live → True;
  - same handle, holder live → False;
  - other handle → False;
  - holder == session → False;
  - host-local session or holder → False;
  - session `ann@viewer`, holder `ann@<hex>` not live → True;
  - session `ann@viewer`, holder `ann@<hex>` live → True (a channel session sees all of the person's
    claims as its own; S-M2);
  - holder `ann@viewer` with any session → `connected` False, and `adoptable` True only for another `ann@…`
    session.
- `test_foreign_holder_and_mine_follow_adoption`: on task docs with `locked_by` set:
  - `foreign_holder` is `""` exactly when the holder is the session or adoptable;
  - `read(...).mine` agrees;
  - `read(...).as_blocker().mine` agrees;
  - `blockers._claim_blockers(claim, session, id) == []` for an adoptable holder, and non-empty for a live
    other handle.
- `test_solo_behaviour_is_identical` (regression oracle): with `CONNECTED` `None` and host-shaped ids only, run
  `foreign_holder`, `read`, `liveness`, `blocked_by` and `survives_status_change` over the cases of
  `test_native_claims.py`, and compare against fixed expected values captured on the base commit. Copy the
  values in; don't recompute them.

Commit: `feat(claims): remote session shape <handle>@<nonce>; a person may adopt their own sessions that are gone`.

### Step 2: the requester rules inside the command (`commands.py`, `workflow.py`, `service.py`)

Tests in `tests/test_claim_force_rule.py`:

- Seed a native root with a todo task and a two-member bundle.
- Claim with `commands.execute` directly, as `test_native_routing_claims.py:276` does, using `caller_scope =
  arguments.session = "bob@" + "b"*16`.
- `requester` is passed explicitly.

The tests:

- `test_member_cannot_force_another_handles_claim`: `task.pick` `force=True` as `ann@…` with
  `Requester("ann", False)` raises `Conflict` whose text contains `Only an operator` and `bob`. The holder is
  unchanged and `command_receipts` gained no row.
- `test_operator_role_can_force`: the same with `Requester("ann", True)` commits; the holder is `ann@…`.
- `test_same_handle_force_is_allowed`: holder `ann@<other>` (connected), `Requester("ann", False)`, force:
  commits.
- `test_expired_claim_can_be_forced_by_anyone`: Bob's claim stamped expired (`claim_expires` in the past for
  Bob). A member force commits.
- `test_bundle_force_checks_every_member`: Bob holds one member. A member force pick of the other member
  raises, and no member changes.
- `test_no_requester_is_todays_rule`: `requester=None` with force commits over Bob (solo/host-local).
- `test_requester_does_not_change_the_payload_hash`: one request executed with two different requesters
  replays the same receipt (the second call is a replay, not a conflict).
- `test_adoption_without_force_inside_the_command`: a **characterisation test** of Step 1 seen through the
  core (it passes as soon as Step 1 lands, review P-M1; run it here, don't expect it to fail first).
  - Holder `ann@<old>` is not connected. A `task.pick` without force as `ann@<new>` commits and moves
    `locked_by` and `claim_expires_for` to `ann@<new>`.
  - `task.claim_renew` as `ann@<newer>` adopts again.
  - `task.claim_release` as Bob on Ann's unexpired claim raises `Conflict`.
- `test_coordinator_passes_its_requester`: a `Coordinator` subclass whose `_requester` returns
  `Requester("ann", False)`. A submitted force pick over Bob is refused through the real writer. The base
  `Coordinator` passes `None` (the same pick commits).
- **`test_member_status_move_cannot_drop_a_teammates_claim`** (D11, S-I2; parametrized over each write):
  - `task.update` status `done`;
  - `task.update` status `archived`;
  - `task.complete` `target_status=done`;
  - `task.archive`;
  - `task.batch_line` `status <id> done`;
  - the viewer update operation with status `archived`, built as the viewer adapter builds it.

  Each runs as `ann@…` with `Requester("ann", False)` on a task Bob holds unexpired. Each raises
  `Conflict` containing `only an operator` and `bob`. The holder, the status and `command_receipts` are
  unchanged.
- `test_operator_and_host_may_drop_by_status`: the same writes with `Requester("opal", True)` and with
  `requester=None` commit and release the claim, as today.
- `test_status_move_rule_spares_own_expired_and_unclaimed`: each write commits when:
  - the holder is `ann@<other>`, live or gone (same handle);
  - Bob's claim is expired (`claim_expires` in the past);
  - Bob's claim is stranded on a `done` row (leaving terminal);
  - the task is unclaimed.

  A non-terminal move (`blocked`) of Bob's live claim commits and keeps Bob as the holder (no claim
  field changed).
- `test_patch_cannot_touch_claim_fields_of_a_teammate`: a `task.patch` that removes `locked_by`, or sets
  `claim_expires`, on Bob's live claim with a member requester raises `Conflict`. This is the funnel's
  backstop under the adapter's `HOLDER_WRITE_REFUSAL`.

Commit: `feat(coordinator): only an operator can force-take or drop a teammate's claim; checked inside the command`.

### Step 3: one session accessor, per-session state, the inventory (`backlog_server.py`, `native_routing/*`, viewers)

Tests in `tests/test_session_scope.py`:

- `test_without_a_provider_the_host_session_applies`: `current_session() == bs.SESSION_ID`, and after
  `monkeypatch.setattr(bs, "SESSION_ID", "x-1-deadbeef")` it is `"x-1-deadbeef"`.
- `test_route_native_derives_the_session_once` (P-I1). The provider is a counter that returns a fresh
  `SimpleNamespace` session on each call. One `backlog_pick_task` on `native_project`:
  - invokes the provider exactly once: the `_CALL_BOUND` binding serves `current_session()`,
    `client_agent()` and `_set_session_task` for the rest of the call;
  - every envelope it sent (spy on `adapter.Client.execute`) has that one `caller_scope`;
  - `task.pick`'s `arguments.session` and the envelope's `actor.agent` come from that object;
  - the current task landed on that same object.
  - After the call, `_CALL_BOUND.get() is _UNBOUND`.
  - A tool that calls another tool (nested) still makes one provider call.
  - A tool that raises still resets the binding.
- `test_every_routed_tool_uses_the_bound_session` (parametrized over `backlog_pick_task`,
  `backlog_claim(status)`, `backlog_next_available`, `backlog_context`, `backlog_batch_update` (one `pick`
  line), `backlog_batch_preview`):
  - with the fake bound session `ann@<hex>`, the claim written or judged is `ann@<hex>`;
  - `backlog_claim(status)` reports `"session": "ann@<hex>"`;
  - no envelope carries `bs.SESSION_ID`.
- `test_per_session_state_follows_the_bound_session`:
  - Two fake sessions A and B: A picks task 1 and B picks a bundle.
  - Switching the provider between them, `_get_session_task()` and `_get_session_bundle()` return each one's
    own.
  - With no provider, the module globals are untouched (`None`).
  - `backlog_handover_create` under B records B's bundle and under A none.
- `test_client_agent_is_the_bound_sessions`: provider agent `"codex"` gives `client_agent() == "codex"`
  while `_CLIENT_AGENT` is `"claude"`. Without a provider, `"claude"`.
- `test_merge_stamp_replay_runs_as_the_host`: write it after the `_route_native` conversion within this
  step. Before that, it passes for the wrong reason (P-M2).
  - Spy on `merge_stamps.replay`. A routed call under a fake bound session passes `bs.SESSION_ID`, not the
    bound one.
  - Inside the replay thread, `bs._bound_session()` is `None` and `auth.VIEWER_KEY_ID.get()` is `None`,
    even when the routed call ran under `viewer_principal`. The thread body runs in an empty
    `contextvars.Context()` (D10, S-M4).
- `test_viewer_session_endpoint_uses_the_accessors`: `_serve_session`'s JSON under a fake bound session has
  that `session_id` and its `task`.
- `test_provider_errors_fail_the_call`: a provider raising `PermissionError` makes a routed write tool fail.
  `adapter.Client.execute` was never called.
- `test_only_the_accessor_reads_the_global` (lint, design R2): a regex over `taskmaster/**/*.py` for
  `\bSESSION_ID\b` finds only:
  - `backlog_server.py`: its definition and `current_session`;
  - `native_routing/registry.py`: the merge-stamp replay call (D10);
  - the `native/claims.py` comment.
- `test_tool_threads_are_the_known_ones` (design §5, P-M9). `threading.Thread(` in
  `taskmaster/backlog_server.py`, `taskmaster/native_routing/*.py`, `taskmaster/coordinator/adapter.py` and
  `taskmaster/coordinator/client.py` occurs exactly at the known sites, matched by the `name=`/target text:
  - the writer-wait pump;
  - the viewer server;
  - the merge-stamp replay;
  - `taskmaster-sync-job-call`.

Regression note: `tests/test_server_sessions.py` (the `/session` endpoint), `test_bundle_pick.py`,
`test_client_agent.py`, `test_native_routing_handovers.py` and every test that monkeypatches `bs.SESSION_ID`
must pass unchanged.

Commit: `refactor(server): every tool reads its connection's session once per call; current task, bundle and agent are per session`.

### Step 4: the session registry over HTTP (`serve/sessions.py`, `serve/app.py`, `serve_helpers.py`)

Tests in `tests/test_serve_sessions.py`. The app is `build_app(server, KeyAuthenticator(op, registry),
registry=registry, sessions=sessions, sweep_seconds=0.2)` in `running_app`. `server` is a FastMCP with:

- `whosession` (sync def): returns `json.dumps({"session": sessions.current().session, "agent": …})`;
- `whosession_async` (async def): the same;
- `spawned` (sync): returns what a plain `threading.Thread` inside the tool sees from `sessions.current()`
  (`None`), and what a thread run under `contextvars.copy_context().run` sees.

Install `bs.SESSION_PROVIDER` only in the tests that need it.

- `test_a_connection_has_one_session`: one `open_connection(url, ann_key)`. Ten `whosession` calls return the
  same `ann@<16 hex>`, and it fullmatches `REMOTE_SESSION_RE`.
- `test_two_connections_have_two_sessions`: two connections on Ann's key give different sessions, both
  `ann@…`. Bob's connection gives `bob@…`.
- **`test_sessions_survive_the_thread_pool`**: two connections, Ann and Bob, call `whosession` concurrently 50
  times each, behind a `threading.Barrier`, and also `whosession_async`. Every answer is the caller's own
  session, with no cross-talk.
- `test_a_plain_thread_sees_no_session`: `spawned` reports `None` for the plain thread and the caller's
  session under `copy_context().run` (design §5).
- `test_agent_is_per_connection`: connections with `client_name` `claude-code` and `codex` report agents
  `claude` and `codex` (via `actor.agent_for_client`).
- `test_session_nonce_is_not_the_transport_id`: the session's nonce isn't a substring of the `mcp-session-id`
  (read from the raw initialize response), and the transport id isn't in the log beyond its first 8
  characters.
- `test_missing_principal_or_session_id_fails_closed`: call `sessions.current()` inside a stub request
  context: with no `scope["user"]` claims → `PermissionError`; with no `mcp-session-id` header →
  `PermissionError`. Monkeypatch `get_http_request` to return the stub.
- `test_binding_never_changes_key`: `bind(tid, ann)` then `bind(tid, bob)` → `PermissionError`.
- `test_connected_tracks_the_transport`: open a connection and capture its session. `connected(session)` is
  True. After `Connection.close()` (the client sends DELETE) it becomes False, and `prune()` drops it,
  logging `session … closed`. Poll every 0.05 s with a 10 s deadline; never a fixed sleep (P-M6).
  `on_disconnect` received it once. An unknown session or `ann@viewer` is False. A registry with no
  manager attached answers False.
- `test_idle_transport_is_pruned`: `session_idle_seconds=0.5`. Open, then poll (10 s deadline) until it is
  not connected and pruned.
- `test_prune_errors_never_stop_the_sweep`: an `on_disconnect` callback that raises is logged at ERROR. A
  second callback still runs, and the revocation step still runs (002's
  `test_revoking_a_key_ends_its_open_sessions` passes alongside).
- `test_provider_reads_the_key_through_current_key_id` (S-M5): spy on `auth.current_key_id`. `current()`
  inside a request calls it before `current_principal`. When it raises `PermissionError`, `current()`
  raises and binds nothing.
- `test_viewer_channel_session`: under `auth.viewer_principal(ann_id)` and outside any MCP request,
  `current()` is `ChannelSession("ann@viewer", …)`. With `"operator"` it is `operator@viewer`. A revoked id,
  or `RegistryUnavailable`, gives `PermissionError`. Outside both, `None`.

Commit: `feat(serve): one server-minted session per MCP connection, derived from the request in every tool thread`.

### Step 5: the coordinator's scope check and the daemon wiring (`ServeCoordinator`, `sessions.requester`/`check_scope`, `ServeDaemon`)

Tests in `tests/test_serve_session_guard.py`. The world is 002's `test_serve_identity.py` world: a
`ServeCoordinator` with Ann and Bob keys, plus an operator-role key `opal`. **Team mode is pinned off**
(`identity_gate.team_mode_enabled` returns False), so `ServeIdentityGate.admit` can't produce the refusal
for D5 (P-M3).

- `test_own_scope_commits`: `submit(task.patch, caller_scope="ann@"+hex, origin=Origin(ann_id))` commits.
- **`test_another_handles_scope_is_refused_before_queueing`**: `caller_scope="bob@…"` with origin Ann
  raises `IdentityRefused` whose text is exactly `SESSION_REFUSED`. The type is exactly `IdentityRefused`,
  not a subclass, so the 006 maps keep it. `owner.pending` never held the key: spy on `queue.put_nowait`,
  not called. Nothing commits.
- **`test_session_refusal_survives_the_ipc_client`** (P-I3, S-I1). Start the in-process `ServeCoordinator`
  with its 006 `public_error` (sanitizer). Through a real `Client(root, autostart=False)`, call `execute`
  with a mis-scoped envelope (`caller_scope="bob@…"`) and `origin={"principal": ann_id}`.
  - The raised error is exactly `IdentityRefused`.
  - `str(err)` equals `SESSION_REFUSED`.
  - `err.may_have_committed is False`, and `retry` is not in the text.
  - caplog on `taskmaster.serve` has no ERROR record and no `ref=`.
  - The same holds for a member force pick over Bob, which arrives as `Conflict` with the E2 text.
- `test_host_shaped_scope_from_a_remote_origin_is_refused`: `caller_scope=bs.SESSION_ID`, `"explicit-sync"` or
  `"coordinator-identity"` with origin Ann: refused (004's "fixed scopes can't be claimed remotely" relies on
  this for `execute`).
- **`test_claim_session_argument_must_be_the_scope`**: `task.pick` with `arguments.session="bob@…"` under
  scope `ann@…`, origin Ann: refused. The same nested inside a `batch` request: refused.
- `test_local_origin_is_unrestricted`: origin `LOCAL` with any scope and session commits as today.
- `test_writer_rechecks_and_passes_the_requester`:
  - Set `owner.checkpoint` to revoke Ann at `dequeued`. A queued Ann request is then refused (002's REVOKED),
    and `commands.execute` is not called.
  - Separately, spy on `commands.execute`: it receives `requester == Requester("ann", False)`; for `opal`,
    `may_force_any=True`; for origin `operator`, `Requester("operator", True)`; for LOCAL, `None`.
- `test_member_force_is_refused_end_to_end_in_process`: Bob holds a task. A force pick from Ann's origin and
  scope raises `Conflict` (`Only an operator`). From `opal` it commits.
- `test_daemon_installs_and_restores_the_session_hooks` (P-M4, P-M5): this step wires `ServeDaemon._start`
  (§2.5), and this test is its proof.
  - Use the in-process daemon (`served`, `tests/test_serve_daemon.py:34`). While it runs,
    `bs.SESSION_PROVIDER == daemon.sessions.current` and `claims.CONNECTED == daemon.sessions.connected`,
    and the app's sweep calls `sessions.prune` (spy).
  - After `close()`, both are back to their previous values (`None`).
  - The same holds after a start that fails after the install (monkeypatch `build_app` to raise).
  - Put it next to 002's `ORIGIN_PROVIDER` restore test.

Commit: `feat(serve): the coordinator refuses commands whose session belongs to another person; the daemon wires sessions`.

### Step 6: the tools' adoption and refusal texts; the viewer (`native_routing/claims.py`, `tasks.py`, `serve/viewer.py`)

Tests in `tests/test_session_scope.py` (tool layer, fake bound sessions on `native_project`):

- **`test_pick_adopts_a_gone_own_session`**: `ann@old` holds task T (not connected).
  - As `ann@new`, `backlog_pick_task(T)` without force commits.
  - The answer contains `adopted from your earlier session` and `ann@old`.
  - The holder is `ann@new`.
- `test_pick_adopts_an_in_progress_task` (P-M9): T is `in-progress` with holder `ann@old` (gone). The
  adapter's in-progress branch commits `task.pick` and answers with the adoption text, not `Already in
  progress`.
- **`test_pick_refuses_an_open_own_session_without_force`**: `ann@old` is connected.
  - The answer is E3 (`your other open session`), and nothing commits.
  - With `force=True` it commits.
- `test_bundle_pick_texts` (P-M9). Each case answers without `Use force=True to steal.`:
  - Bob holds one member: a force pick as Ann gives E2;
  - `ann@old` holds one member and is connected: a pick without force gives E3;
  - `ann@old` holds one member and is gone: the bundle is adopted.
- **`test_member_force_on_a_teammate_is_refused_by_the_tool`**: Bob holds T. Ann's force pick answers
  `Error: … Only an operator … Ask bob …`, with no `[seq`. The coordinator was not called (spy).
- `test_claim_status_lists_adoptable`: `ann@old1` (gone) and `ann@old2` (connected) each hold a task; Bob
  holds one.
  - `backlog_claim(status)` as `ann@new` gives `claims == []`.
  - `adoptable` lists only `ann@old1`'s task, with its holder and `expires_at`.
- `test_renew_adopts_and_says_so`: renew of `ann@old1`'s task as `ann@new` gives `ok`,
  `holder == "ann@new"` and `adopted_from == "ann@old1"`.
- `test_conflict_hints_are_requester_aware`: release of Bob's live claim as Ann gives a hint with `Ask bob`
  and no `force=True`. Renew of `ann@old2`'s (connected) claim gives the E3 hint. With `may_force_any` the
  hint is today's.
- `test_next_available_offers_adoptable_tasks`: a task held by `ann@old1` is listed for `ann@new`, and not
  for `bob@…`.
- `test_viewer_reads_and_writes_run_as_the_person` (Q5): with 006's guarded viewer (`tests/test_serve_viewer.py`
  `team` fixture):
  - a logged-in GET of the board has an ETag that differs per person;
  - a write's `caller_scope` is `<handle>@viewer` (spy on `Client.execute`).
- `test_exact_equality_sites_are_the_documented_ones` (design R4; here because Steps 3 and 6 convert the
  last sites):
  - Grep `taskmaster/` for `locked_by"?\)?\s*[!=]=\s*session|holder\s*[!=]=\s*session|[!=]= SESSION_ID`.
  - The allow-list (P-I2), matched by content, not line number:
    - `native/claims.py`'s predicate definitions: `read`'s `mine = holder == session or adoptable(…)`
      (base `:251`), `foreign_holder`'s `holder != session` (base `:269`), and `adoptable`'s
      `holder != session`. These define the rule;
    - `native/blockers.py`'s `_claim_blockers` line `claim.mine or claim.holder == session` (base
      `:252`). The exact half is for `Claim`s built without a `ClaimState`;
    - `native_routing/tasks.py`'s two bound checks (`locked_by == session` in the in-progress branch and
      in the bundle `bound`);
    - `native/workflow.py`'s bundle bound check;
    - `backlog_server.py`'s legacy bundle bound check and its `:6950` focus line.

    Any other hit fails.
  - Any new hit fails with *"use claims.foreign_holder or read().mine, or add the site to design §3 D6
    with its reason"*.

Commit: `feat(tools): pick and claim adopt a person's own gone sessions and refuse teammates' claims with the reason`.

### Step 7: end to end over HTTP (`tests/test_serve_sessions_e2e.py`)

Use `launch(users=("ann", "bob"))` plus `add_user(root, "opal", role="operator")` before start, with
`open_connection` for long-lived connections. Every write tool is a real `backlog_server` tool.

- `test_teammates_hold_claims_as_themselves`: Ann and Bob each pick a task. The `locked_by` values in the store
  are `ann@…` and `bob@…`, and the events' actors are Ann and Bob.
- `test_one_key_two_connections_are_two_sessions`: two Ann connections pick two tasks. Each `backlog_claim(status)`
  lists only its own.
- **`test_teammate_cannot_take_free_or_renew`**: Bob holds T.
  - Ann's pick, her force pick, her `backlog_claim(renew)` and her `backlog_claim(release)` are all refused.
  - The holder is still Bob's session, and `opal`'s force pick then succeeds.
- **`test_teammate_cannot_drop_a_claim_by_status`** (D11). Bob holds T. Each of these from Ann answers with
  the E7 text, writes nothing, and leaves Bob as the holder:
  - `backlog_update_task(T, "status", "archived")`;
  - `backlog_complete_task(T)`;
  - `backlog_archive_task(T)`;
  - a `backlog_batch_update` line `status T done`.

  The same `backlog_update_task` from `opal` commits and releases the claim.
- **`test_restarted_agent_adopts_its_work`**: Ann's connection 1 picks T and closes. Connection 2's
  `backlog_pick_task(T)` adopts without force, and the holder is connection 2's session.
- `test_open_own_session_needs_force`: with connection 1 still open, connection 2's pick is refused (E3);
  `force=True` takes it.
- `test_current_task_and_agent_are_per_connection`:
  - Ann (`claude-code`) and Bob (`codex`) pick different tasks.
  - Each `backlog_handover_create` names its own current task or bundle.
  - Each write's actor `agent` is the connection's own.
- `test_sessions_hold_no_transport_ids_or_keys`: no `mcp-session-id` value or key appears in `locked_by`, the
  exported `.taskmaster/` files or `serve.log`.

Commit: `test(serve): teammates' sessions, claim protection and adoption over real HTTP`.

### Step 8: docs and tool text

- `native/claims.py` module docstring: a short paragraph on the two session shapes, adoption and the force rule.
- `backlog_pick_task` and `backlog_claim` docstrings: one sentence each, *"On a shared server, a claim belongs to your
  session; your own earlier sessions that have disconnected are taken over without force."* Run
  `tests/test_server_instructions.py` and the tool-description lints. If a snapshot needs an update, update it
  in this commit and say so.

Commit: `docs(claims): sessions, adoption and the operator force rule`.

---

## 4. Order of work inside a step: expected first failures

- Steps 1, 4 and 5: `ImportError` or `AttributeError` on the new names.
- Step 2: `execute()` got an unexpected keyword `requester`. Once that exists, the D11 tests fail because a
  member's `done` or `archived` commits and drops Bob's holder.
- Step 3: the bound session is ignored (claims carry `bs.SESSION_ID`).
- Step 6: an adoptable pick is refused as locked, or a member force succeeds.
- Step 7: every connection shares one holder.

Don't edit sources while a run is in progress: the build fingerprint changes and real-coordinator tests fail
spuriously (about 13 of them).

## 5. Commands

Run from the worktree root (`C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-server-003`) with the
repo venv's interpreter, in the foreground. Every temp file, `--basetemp` and probe goes under
`D:\scratchbook\taskmaster\ts003\` (`/d/scratchbook/taskmaster/ts003/` in Git Bash), with a fresh
sub-directory per run.

Step tests:

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest tests/test_claim_ownership.py \
  tests/test_claim_force_rule.py tests/test_session_scope.py tests/test_serve_sessions.py \
  tests/test_serve_session_guard.py tests/test_serve_sessions_e2e.py \
  -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts003/step-<n>"
```

Regression subset (after Steps 0, 1, 2, 3, 5 and 7, and before reporting done):

```bash
/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python -m pytest \
  tests/test_native_claims.py tests/test_native_claim_review_d.py tests/test_native_claim_terminal_and_readers.py \
  tests/test_native_locked_by_contract.py tests/test_native_routing_claims.py tests/test_native_routing_context_invariant.py \
  tests/test_native_blockers.py tests/test_native_blocker_callers.py tests/test_batch_structured_commands.py \
  tests/test_native_routing_batch.py tests/test_bundle_pick.py tests/test_bundle_members.py tests/test_pick_task_smoke.py \
  tests/test_pick_worktree_instruction.py tests/test_client_agent.py tests/test_native_service_actor.py \
  tests/test_native_routing_handovers.py tests/test_native_routing_context.py tests/test_native_routing_tasks.py \
  tests/test_native_progress.py tests/test_native_routing_changes.py tests/test_native_service_linear.py \
  tests/test_server_sessions.py tests/test_viewer_board.py tests/test_viewer_board_oracles.py \
  tests/test_viewer_board_detail.py tests/test_viewer_reads_rows.py tests/test_native_routing_viewer.py \
  tests/test_linear_drain_claims.py tests/test_native_service.py tests/test_native_service_build.py \
  tests/test_serve_app.py tests/test_serve_app_keys.py tests/test_serve_daemon.py tests/test_serve_identity.py \
  tests/test_serve_coordinator.py tests/test_serve_viewer.py tests/test_serve_principals_e2e.py \
  tests/test_native_routing_hooks.py tests/test_serve_sanitize.py \
  tests/test_server_instructions.py tests/test_packaging.py \
  -n 3 -q -p no:cacheprovider --basetemp="/d/scratchbook/taskmaster/ts003/reg-<n>"
```

Leave the full suite to the review gate.

## 6. Risks (detail in design §12)

- **R1. SDK internals** (`_server_instances`, `is_terminated`, `client_params`): pinned by
  `test_connected_tracks_the_transport`, `test_agent_is_per_connection` and 002's
  `check_session_internals`.
- **R2. A tool path that bypasses `call.session`**: pinned by `test_only_the_accessor_reads_the_global` and
  `test_tool_threads_are_the_known_ones`.
- **R3. Shared files** (`service.py`, `commands.py`, `workflow.py`, `claims.py`): only the §2.1–2.2 edits.
- **R3a. D11 over-refusal.** A legitimate member write that changes claim fields on a teammate's task,
  where none exists today, would be refused. Pinned by
  `test_status_move_rule_spares_own_expired_and_unclaimed` and the regression subset.
- **R4. Exact-equality sites**: `test_exact_equality_sites_are_the_documented_ones`.
- **R5. Merge overlap with 005** in `serve/daemon.py` and `serve/app.py`: whichever lands second keeps both.

## 6a. Rollback

- Revert the step commits, newest first.
- Claims already written as `ann@…` stay valid data. A solo process treats them as foreign peers with unknown
  liveness until their TTL. No schema, projection or export format changes.

## 7. If a decided ruling is later reversed

Q1 to Q6 are decided as recommended (2026-11-01). Each entry below says what a reversal would change.

- **Q1** (keep `<handle>-<nonce>`): not safe without D6's explicit remote-first liveness branch and a
  different parser. Redo Step 1's shape tests; everything else is unchanged.
- **Q2** (adopt even open sessions): drop `connected` from `adoptable` (Step 1), and drop E3,
  `test_pick_refuses_an_open_own_session_without_force` and `test_open_own_session_needs_force`.
- **Q3** (release override): add `force` to `task.claim_release` and `backlog_claim`, allowed only with
  `may_force_any`, in Steps 2 and 6. The tool surface changes, so tell 005.
- **Q4** (split): A is Steps 1, 3, 4 and 5; B is Steps 2, 6, 7 and 8 (P-M8). A's done-criteria are
  the first, fourth and last bullets of "Done when".
- **Q5** (no viewer reads): drop the Step 6 viewer GET assertion and the `serve/viewer.py` edit.
- **Q6** (add `adopt`): a new `backlog_claim(action="adopt")` in Step 6, and tell 005 and the eval owners.

## 8. Left for later tasks (mention, don't build)

- 004: scope `receipt`, `cancel` and `linear_retry` by `claims.remote_handle(scope) == principal.handle`;
  carry the origin on those IPC methods; accept same-handle scopes, so a restarted agent can retry under its
  old scope.
- 010: renewal through `SessionRegistry.live()`; the short TTL from `on_disconnect`, which runs on the event
  loop and must not block; the grace after a restart.
- 011: a clone identity on `RemoteSession`.
- 007: a `hook` channel word in `claims.CHANNELS`.
- Follow-ups: remote sessions in `backlog_store_status`; context focus fallback to an adoptable claim; a
  per-key cap on MCP sessions (006); refusing `force` for a context-less `execute` inside the daemon (design
  §10, S-M3). Dropping a teammate's live claim by a status move is closed here by D11, so the "003 gap for
  015" follow-up in the lanes rulings file can be closed.
