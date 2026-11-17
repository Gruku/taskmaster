<!-- User intent: the task-level spec and TDD plan for team-server-004. On the shared Taskmaster server a teammate can
     reach only the receipts, queued work and request ids filed under their own handle, never another person's and
     never the server's fixed internal scopes, and a request that lost its key inside the server is refused, not run as the host. -->

# team-server-004: receipt, queue and request-id scoping per person (spec and plan, 2026-11-07, rev 2)

**Task.** `team-server-004` (lane full). Done when every coordinator method that names a `caller_scope` checks
that scope against the caller's principal, the server's fixed internal scopes can't be reached remotely, and the
remaining caller-named scope (`backlog_linear(retry, caller_scope=…)`) is closed.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §3 (receipts and work queues bullet).
`docs/specs/2026-11-01-team-server-003-sessions-design.md` §8 (the 004 seam, review S-M7).
`docs/specs/2026-11-01-team-server-005-allowlist-design.md` D3/D4 (origin on every method; no principal fails
closed).
**Rulings** (`docs/handoffs/2026-11-01-team-server-lanes/rulings.md`): "005/015: a member can pass any
caller_scope to `linear_retry`; check_scope doesn't cover it (low impact)" is closed here. "003 S-M3: refuse
`force` for an execute that lost its request context" is follow-up team-server-020; Part A D4 closes most of it,
and Q1 asks whether to count that as closing 020.
**Builds on** `feat/team-collab` 87977af (001, 002, 003, 005, 006, 007, 009, 012, 013, 014 merged).
**Verified against.** Code read at 87977af; cited as file:line. Nothing was spiked; plan Step 2 and Step 4 tests
are the first proof.
**Review history.** Rev 1 (406c576): the spec and the plan both passed review. Q1-Q5 were accepted, and Q1 counts
D4 as closing team-server-020's detectable case. Rev 2 folds in the seven Minors, M1-M7, each tagged at its place.
**Shape.** Part A is the spec (spec-review gate). Part B is the TDD plan (plan-review gate). The shared seam with
team-server-010 is settled in Part A §8, and the 010 design builds on it.

---

# Part A: spec

## 1. What is already scoped, and what is left

003 and 005 did most of the addendum's bullet. 004 is the remainder plus the inventory that proves it.

| Surface | Key | State at 87977af | 004 |
|---|---|---|---|
| `execute` receipts (`command_receipts`) | `(store_id, caller_scope, request_id)` (`native/receipts.py:10-12`) | the serve coordinator refuses any remote request whose `caller_scope` isn't `<own handle>@…`, and any claim `session` that isn't the scope, before queueing and again in the writer (003 D5, `serve/daemon.py:87-105`, `serve/sessions.py:224-229`) | nothing new; inventory test (§6) |
| `execute` queue (`Coordinator.pending`) | `(caller_scope, request_id)` (`coordinator/service.py:212`) | same check runs before the `pending` slot is taken | nothing new |
| `receipt`, `cancel`, `sync_status` | caller-named `caller_scope` + `request_id` (`service.py:506-526,540-541`) | operator-only on a server (005 `allowlist.REMOTE`, `coordinator/allowlist.py:17`) | D2: scope check on any remote origin, so opening them later is safe |
| `linear_retry` receipts and `LinearWorker.jobs` | caller-named `(caller_scope, request_id)` (`linear_worker.py:31-53`) | **remote-reachable and unchecked**: `backlog_linear(action="retry", caller_scope=…)` passes any scope (`native_routing/overview.py:322-327`); a member can attach to another person's in-flight retry future or record a retry under their scope | D2 closes it |
| `sync`, `git_run` | caller-named scope | operator-only (005) | D2 as defence in depth |
| `sync_job` | server-minted `sync_id` | operator-only (005) | none (no scope) |
| fixed internal scopes | see §3 | unreachable for `execute` by shape (003 D1), but nothing pins the list | D1: one table, tests |
| in-daemon request that lost its key | origin missing, scope `<handle>@…` | runs as `LOCAL`: no D5 check, unrestricted `force` (003 §10, T8) | D4 refuses it in the client funnel |

Not in scope here: `backlog_query` already excludes `command_receipts` (`query_guard.py:24-27`). `changes.session`
showing session ids to teammates is 007 M3 and belongs to 015; 004 adds no new value there.

## 2. Threat model

Attackers are authenticated members and their agents (001, 002, 006 keep everyone else out). Host processes are
trusted (002 D8).

| # | Threat | Today | After 004 |
|---|---|---|---|
| S1 | Bob reads Ann's Linear retry result, or attaches to her in-flight retry, with `backlog_linear(retry, caller_scope="ann@…", request_id=<hers>)` | succeeds | refused before `LinearWorker.submit` (D2, E1) |
| S2 | Bob files his retry under Ann's scope, so its receipt and `linear:` claim owner name her | succeeds | refused (D2) |
| S3 | A member names an internal scope (`linear-settle`, `coordinator-identity`, …) on any method | `execute`: refused by shape. `linear_retry`: accepted | refused on every method (D1, D2) |
| S4 | Code in the daemon sends a person's request without its key (a tool's own thread; 003 T8): no D5 check, `force` unrestricted, writes as the host | possible | refused before it leaves the daemon's client (D4, E2) |
| S5 | A restarted agent recovers its own earlier retry under its old session's scope | n/a (any scope passes) | accepted: any same-handle scope (D2; 003 S-M7, the receipt analogue of adoption) |

## 3. The internal scope table

Every fixed `caller_scope` the coordinator or the daemon mints for itself, read from the code:

| Scope | Minted by | Site |
|---|---|---|
| `coordinator-identity` | identity gate `profile.create` | `coordinator/identity_gate.py:33`, `serve/identity.py:77` |
| `linear-settle` | `LinearWorker` settle commands | `coordinator/linear_worker.py:110,113` |
| `explicit-sync` | `Client.sync` default (host CLIs) | `coordinator/client.py:441` |
| `explicit-git` | `Client.git_run` default (host CLI) | `coordinator/client.py:517` |
| `backlog-sync`, `backlog-sync-job` | `backlog_sync` jobs | `coordinator/sync_jobs.py:19,21` |
| `sync-<sha256>` (prefix) | `sync_worker.operation_scope` | `coordinator/sync_worker.py:285-288` |
| `git:<scope>` (prefix) | managed Git's pre-sync | `coordinator/git.py:830` |
| `serve-export-restore` | 013 document restore | `serve/state_export.py:1237` |
| `claim-keeper` | team-server-010's claim keeper (new, §8) | `serve/claim_keeper.py` |

None contains `@`, so none fullmatches `claims.REMOTE_SESSION_RE` (`native/claims.py:167-168`), and a remote
principal, whose scope must parse as its own handle, never reaches one. `git:ann@…` doesn't parse either (`:` is
outside the handle charset).

## 4. Decisions

**D1. One module owns the scope vocabulary: `taskmaster/coordinator/scopes.py` (new, pure).**

```python
# coordinator/scopes.py: imports only taskmaster.native.claims (itself pure apart from a lazy store import)
IDENTITY = "coordinator-identity"; LINEAR_SETTLE = "linear-settle"; EXPLICIT_SYNC = "explicit-sync"
EXPLICIT_GIT = "explicit-git"; BACKLOG_SYNC = "backlog-sync"; BACKLOG_SYNC_JOB = "backlog-sync-job"
EXPORT_RESTORE = "serve-export-restore"; CLAIM_KEEPER = "claim-keeper"
INTERNAL = frozenset({...the eight above...})
INTERNAL_PREFIXES = ("sync-", "git:")
NAMED_SCOPE_METHODS = frozenset({"receipt", "cancel", "sync_status", "linear_retry", "sync", "git_run"})
CLAIM_OPERATIONS = ("task.pick", "task.claim_renew", "task.claim_release")   # moved from serve/sessions.py

def is_internal(scope) -> bool                    # exact member of INTERNAL, or starts with an INTERNAL_PREFIXES entry
def claim_operations(request) -> list             # moved verbatim from serve/sessions.py:213-221
def named_sessions(method, arguments) -> list[str]
    # every session string a coordinator message names: arguments["caller_scope"] for NAMED_SCOPE_METHODS;
    # for "execute", envelope["caller_scope"] plus each claim operation's arguments["session"]
    # (batch flattened one level, as claim_operations does). Non-str values are returned as-is (callers refuse them).
```

- The constants replace the literals at every §3 site (mechanical edits). `identity_gate.CALLER_SCOPE` stays as an
  alias, because `serve/identity.py` and tests import it.
- `serve/sessions.py` re-exports `claim_operations` and `CLAIM_OPERATIONS` from here, so 003's callers and tests
  keep their imports. This is "upgrade, don't fork": one copy of the batch walk.
- A lint test pins it: no `caller_scope=` string literal, and no `'caller_scope': '<literal>'`, in `taskmaster/`
  outside `scopes.py`.

**D2. On a server, every caller-named scope must carry the caller's handle, on every method.**

```python
# serve/sessions.py, next to check_scope
def check_named_scope(scope, who: Requester) -> None:
    if not isinstance(scope, str) or claims.remote_handle(scope) != who.handle:
        raise IdentityRefused(SESSION_REFUSED)
```

- `ServeCoordinator.dispatch` (`serve/daemon.py:140-159`), after the allowlist refusal and before
  `super().dispatch`: when the origin isn't `LOCAL` and `method in scopes.NAMED_SCOPE_METHODS`, it takes
  `who = sessions.requester(origin, self.principals)` and calls `check_named_scope(message.get("caller_scope"), who)`.
  - The allowlist runs first, so a member's `receipt` keeps 005's text (method before arguments).
  - `requester` raises `IdentityRefused(REVOKED/UNAVAILABLE)` for a revoked key or an unreadable key list,
    exactly as for `execute`.
  - It runs before `LinearWorker.submit`, so a refused retry never takes a `jobs` slot or a queue entry.
- **Same handle, any session (S5).** The check compares handles, not session ids. A restarted agent that passes
  its old session's `caller_scope` and `request_id` gets that retry's receipt back. Another handle, a channel of
  another handle (`bob@viewer`), a host-shaped scope or an internal scope is refused.
- **The operator is strict too (Q3).** Over MCP the bootstrap operator's sessions are `operator@…`
  (`sessions.requester` gives `Requester("operator", True)`), so a remote-origin operator message must name an
  `operator@…` scope, as 003 D5 already requires for `execute`. Host processes with no origin (`LOCAL`, the
  operator's CLIs) are unrestricted, as today.
- `may_force_any` (member keys with `--role operator`) is a claim power and widens nothing here (003 R3c).
- **The caller's own channel scopes pass (M2).** `ann@viewer` and `ann@read` parse as ann's, exactly as in 003's
  `check_scope`. That is harmless: a channel only ever names its owner. Keep the two checks identical. Don't
  "fix" this in one of them only. The inventory test has a row for it.
- **No re-check in the writer for `linear_retry` (M1, accepted).** Unlike `execute`, a retry whose key is revoked
  while it waits in `LinearWorker.queue` still runs. The impact is low, because the outbox is project-wide (D5)
  and the scope was checked at dispatch.
- The `execute` path is unchanged: 003's `check_scope` in `submit` and `_requester` already is this rule for
  envelopes.

**D3. The tool side needs no new check.** `backlog_linear(retry)` defaults `caller_scope` to `call.session`
(`overview.py:324`). A foreign scope comes back from the coordinator as `IdentityRefused`, a `ValueError`, which
the adapter already turns into `{"error": <text>}` (`overview.py:326-327`). 006's sanitizer keeps the type
(`serve/sanitize.py` `_DOMAIN_NAMES`), and the client rebuilds it (`coordinator/client.py:191-198`). One
authority, no second wording.

**D4. A person's request without its key never leaves the daemon (closes the detectable half of 020).**

The daemon's client funnel already judges a message with no origin as remote for the method allowlist
(`Client._send`, `coordinator/client.py:153-163`; 005 D4). D4 adds the matching argument rule in `Client.call`,
right after `_origin_arguments` and before `_ready()` (M4). That way the refusal comes before any probe or
connection. Every named-scope method and `execute` go through `call()`, and `sync_job` names no scope.

```python
# coordinator/client.py, Client.call, after `arguments = _origin_arguments(arguments)`
if ORIGIN_PROVIDER is not None and allowlist.principal_of(arguments.get('origin')) is None:
    named = [s for s in scopes.named_sessions(method, arguments) if claims.remote_handle(s) is not None]
    if named:
        LOG.error('refused a %s naming session %s without an authenticated key (lost request context)',
                  allowlist.log_method(method), scopes.log_session(named[0]))
        raise IdentityRefused(LOST_CONTEXT_REFUSED)
```

`scopes.log_session` is a local helper in the style of `allowlist.log_key` (M3). It prints a remote session as-is
(it matched `REMOTE_SESSION_RE`), and anything else as `invalid`. `coordinator/` must not import `serve.app`'s
`log_safe`.

- **Why the client, not the coordinator.** The coordinator sees `LOCAL` both from a trusted host process (the
  operator's CLI) and from daemon code that lost its context. It can't tell them apart. The in-daemon client can:
  only it runs with `ORIGIN_PROVIDER` installed.
- **What it catches.** `NativeCall` captures `call.session` (`<handle>@…`) when the tool starts, but the origin is
  read when the message is sent (`_origin_arguments`, `client.py:89-98`). Work handed to a plain thread therefore
  arrives with a person's scope and no key. That is 003 T8 and 020's case. Today it runs as `LOCAL`: no D5
  check and unrestricted `force`.
- **What it can't catch.** If the session itself was also read on the context-less thread, it falls back to the
  host `SESSION_ID`, and the request is indistinguishable from host work (the merge-stamp replay is legitimate
  host work in that shape, 003 D10). 003's lint ("only the accessor reads the global") and its thread tripwire
  (`tests/test_session_scope.py::test_tool_threads_are_the_known_ones`) cover that case.
- In-process submits that bypass the client stay as they are: `LinearWorker`'s claim (`LOCAL`, the caller's
  checked scope), the settle, the sync worker, the identity gate, and 010's keeper (`claim-keeper`, `LOCAL`).
- Solo and host CLIs (`ORIGIN_PROVIDER is None`) are unchanged.

**D5. The Linear outbox stays project-wide.** A member's retry claims and pushes everyone's queued rows with the
operator's token (005 made `linear_retry` remote). 004 scopes the retry's *identity* (receipt, job key, claim
owner hash), not which rows it pushes. The retry's `linear.claim` command keeps running as `LOCAL` inside the
coordinator. It is an identity-exempt machine-local operation (`team_identity._exempt`), and its scope was already
checked by D2 (Q4).

## 5. Error shapes

| # | When | Shape |
|---|---|---|
| E1 | Remote origin names another handle's or an internal scope on a `NAMED_SCOPE_METHODS` method (D2) | plain `IdentityRefused(SESSION_REFUSED)`, 003's text: `Refused: this request names a session that does not belong to your key. Nothing was written.` Through `backlog_linear` it is `{"error": "<that text>"}`. `may_have_committed=False` |
| E2 | In-daemon message naming a remote session with no origin (D4) | plain `IdentityRefused`: `Refused: this request names a person's session but carries no authenticated key (it ran outside its request). Nothing was sent.` plus one ERROR log line naming the method and session (via `scopes.log_session`, M3) |
| E3 | Revoked key / unreadable key list on a named-scope method | 002's `REVOKED` / `UNAVAILABLE` texts, as for `execute` |

Plain `IdentityRefused` on purpose (003 S-I1): both error maps match by exact type name.

## 6. Acceptance criteria (the inventory test)

A table test, `tests/test_serve_scoping.py::test_scope_inventory`, drives a real `ServeCoordinator` through
`Client.call` and asserts, for each origin (member `ann`, operator-role member `opal`, bootstrap `operator`,
`LOCAL`):

1. `execute` and `linear_retry` with an own-handle scope (current and older session) pass.
2. Another handle's scope, `bob@viewer`, a host-shaped scope and every entry of `scopes.INTERNAL` plus one per
   prefix are refused with E1 for every remote origin and pass for `LOCAL`. The caller's own channel scopes
   (`ann@viewer`, `ann@read`) pass for ann, for both `execute` and `linear_retry` (M2).
3. `receipt`, `cancel`, `sync_status`, `sync` and `git_run` from a member are refused with 005's allowlist text
   (unchanged); from the bootstrap operator with a remote origin, a foreign scope is refused with E1; from `LOCAL`
   anything passes.
4. Nothing refused took a `pending`, `LinearWorker.jobs` or queue slot.

## 7. Solo and host behaviour

Unchanged. The base `Coordinator.dispatch` has no scope checks. `check_named_scope` runs only in
`ServeCoordinator`. D4 runs only with `ORIGIN_PROVIDER` installed. The constants refactor (D1) changes no value.

## 8. Shared seam with team-server-010 (settled here)

010 adds a server-side writer, the claim keeper, that renews and shortens remote sessions' claims. It needs a
rule for how server-internal work names a session it acts on. The rule, which follows from D1, D2 and D4:

- **A remote-shaped `caller_scope` means "this person, with their key".** It is accepted only with an
  authenticated origin of the same handle (003 D5 for `execute`, D2 for named-scope methods). In-daemon client
  traffic can't send one without a key (D4).
- **Server-internal work uses a fixed internal scope and `LOCAL` origin**, submitted to the coordinator in
  process, and names the person's session only as a command argument (`arguments.session`). 010's keeper
  submits `task.claim_renew` with `caller_scope = scopes.CLAIM_KEEPER`, `arguments.session = <holder>`, origin
  `LOCAL`, and `expected_revisions` pinning the task. It works after the holder's key is revoked, never takes a
  person's identity, and is attributed to the host (`operator`, internal agent). `changes.session` shows
  `claim-keeper`, so it adds nothing to 007 M3.
- **Files both lanes touch:** `serve/sessions.py` (004: `check_named_scope`; 010: `attached()` and the announce
  flag), `serve/daemon.py` (004: `ServeCoordinator.dispatch`; 010: `ServeDaemon` wiring),
  `coordinator/scopes.py` (004 creates it; 010 adds nothing, since `CLAIM_KEEPER` is listed from the start).
  One lane builds them in order: 004 Steps 1-5, then 010.

## 9. Out of scope

- Opening `receipt`, `cancel` or `sync_status` to members (Q2).
- Per-principal fairness on `LinearWorker.queue` (16 slots) and the admission queue: 006's per-principal in-flight
  limit is the bound.
- Attribution of `linear.claim` to the member (Q4).
- Masking session ids or host names in `changes.session` / `locked_by` for members (007 M3, 015).
- The fully-lost-context case of 020 (D4, "what it can't catch").
- Receipt GC (`native/receipts.py:16-17`: none runs).

## 10. Risks

- **R1. Shared files.** `coordinator/client.py` (one block in `call`), `linear_worker.py`, `sync_jobs.py`,
  `sync_worker.py`, `git.py`, `identity_gate.py` and `state_export.py` take constant swaps only. Keep the diffs to
  that.
- **R2. D4 false positives.** Any in-daemon path that legitimately sends a remote scope without an origin breaks.
  The inventory says there is none: viewer writes and reads run under `viewer_principal`, the read endpoint inside
  its HTTP request, `sync_job` captures its origin before its thread (`client.py:468-469`), and the merge-stamp
  replay uses the host session. Step 4 runs the serve, viewer and read-endpoint sets to prove it.
- **R3. Tests that drive `LinearWorker` or `Client.call("linear_retry", …)` with a made-up scope under a remote
  origin.** Grep `tests/` for `linear_retry` and fix them the way 003 fixed `serve-tests` (use the key's own scope).

## 11. Open questions (defaults are the recommendations; none blocks the build)

- **Q1. Does D4 close team-server-020?** Recommendation: yes for the case that can happen (session captured, origin
  lost), and close 020 with a pointer to D4. The fully-lost case is indistinguishable from host work and stays
  covered by 003's lint and tripwire. If the lead prefers to keep 020 separate, drop Plan Step 4; nothing else
  depends on it.
- **Q2. Let members call `receipt`/`cancel`/`sync_status` for their own handle?** Recommendation: no. No tool
  calls them, and 005's allowlist stays as it is. D2 makes opening them later a one-line allowlist change.
- **Q3. Strict scopes for the bootstrap operator over MCP.** Recommendation: strict (own `operator@…` scope), the
  same as `execute`. The operator's host CLIs (`LOCAL`) remain unrestricted.
- **Q4. Attribute a member's `linear.claim` to them** (carry the origin into `LinearWorker`)? Recommendation: no,
  not in 004. It is an exempt machine-local op, its scope is checked, and the change would touch the worker's
  queue tuple.
- **Q5. Consolidate the scope literals into `coordinator/scopes.py`** (seven files, constant swaps), or keep only a
  table and tests? Recommendation: consolidate (house rule "consolidate what you touch"). The lint test then stops
  a new literal from drifting.

---

# Part B: TDD plan

Rules for every step:
- Tests run with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest` from the worktree
  `.worktrees\team-server-004`, in the foreground, `-n 2` at most, `--basetemp D:\scratchbook\taskmaster\ts004\pt<step>`.
  Never the full suite on this machine (rulings).
- Each step is one commit with its tests, red first. Commit messages `feat(team-server-004): …` / `test(…)`.
- Fixtures to reuse: `tests/test_serve_session_guard.py`'s `world` (a started `ServeCoordinator` with `ann`, `bob`,
  `opal`) and `envelope`; `test_serve_principals_e2e.launch` for over-HTTP checks; `serve_helpers.open_connection`.
- Team mode is off in coordinator-level tests (monkeypatch `identity_gate.team_mode_enabled`), so the identity gate
  is never what refuses (003 P-M3).

**Step 0. Baseline.** Run §B.1's set and record the counts in the lane notes
(`docs/handoffs/2026-11-01-team-server-lanes/team-server-004.md`). Report what is already red; don't fix it.

**Step 1. `coordinator/scopes.py` and the constant swap (no behaviour change).**
Files: `coordinator/scopes.py` (new, with a user-intent header), `serve/sessions.py` (re-export),
`coordinator/identity_gate.py`, `linear_worker.py`, `client.py`, `sync_jobs.py`, `sync_worker.py`, `git.py`,
`serve/state_export.py`. Tests first in `tests/test_scopes.py`:
1. each `INTERNAL` entry and `"sync-" + "0"*64`, `"git:ann@" + "a"*16` give `claims.remote_handle(...) is None`;
   `is_internal` is true for them and false for `ann@…`, `ann@viewer`, `host-1-aaaaaaaa`;
2. `named_sessions("execute", {"envelope": batch_with_pick})` returns the scope and the pick's session;
   `named_sessions("linear_retry", {"caller_scope": "x"}) == ["x"]`; `named_sessions("status", {}) == []`;
3. `serve.sessions.claim_operations is scopes.claim_operations`;
4. lint: an AST walk over `taskmaster/**/*.py`, outside `coordinator/scopes.py`, finds:
   - no string-literal value for a `caller_scope` keyword or dict key (the `client.py` signature defaults
     reference the constants);
   - no string literal equal to a `scopes.INTERNAL` value, or starting with an `INTERNAL_PREFIXES` entry and
     used as a scope, anywhere. This catches positional uses such as `self._envelope('linear-settle', …)` and the
     SQL parameter at `linear_worker.py:111` (M7). Allow the prefixes' own definitions in `sync_worker` and `git`
     to import them from `scopes`.
Proving check: `tests/test_scopes.py`, `tests/test_serve_session_guard.py`, `tests/test_session_scope.py`,
`tests/test_linear_worker.py`, `tests/test_native_service*.py`, `tests/test_serve_state_export*.py`,
`tests/test_serve_identity.py`, `tests/test_identity_gate.py`.

**Step 2. `check_named_scope` in `ServeCoordinator.dispatch` (D2).**
Files: `serve/sessions.py`, `serve/daemon.py`. Tests first, appended to `tests/test_serve_session_guard.py`
(they need its `world`):
1. `linear_retry` with `origin={"principal": ann}` and `caller_scope=ANN` reaches `LinearWorker.submit` (spy:
   monkeypatch `owner.linear.response` to record and return `{"ok": True}`);
2. the same with `caller_scope=BOB`, `"bob@viewer"`, `"host-1-aaaaaaaa"`, each `scopes.INTERNAL` entry, a
   non-string and `""` raises `IdentityRefused` with `SESSION_REFUSED`, and the spy saw nothing;
3. `caller_scope = "ann@" + "f"*16` (an older session of ann) passes (S5);
4. bootstrap operator origin with `caller_scope=ANN` is refused; with `"operator@" + "0"*16` passes;
5. opal (`--role operator`) with `caller_scope=ANN` is refused (R3c);
6. a revoked key gets the `REVOKED` text before the spy;
7. `LOCAL` (no origin) with any scope passes;
8. a member's `receipt` still gets 005's `ADMIN_REFUSED` text (ordering), not `SESSION_REFUSED`;
9. through `Client(world.root, autostart=False).call("linear_retry", …, origin=…)`: the error arrives as
   `IdentityRefused`, `may_have_committed is False`, no ERROR log line (the 003 IPC pattern,
   `test_session_refusal_survives_the_ipc_client`).
Proving check: `tests/test_serve_session_guard.py`, `tests/test_serve_allowlist*.py`, `tests/test_serve_coordinator.py`,
`tests/test_linear_worker.py`, `tests/test_linear_drain_claims.py`.

**Step 3. Over real HTTP: the tool path.**
Files: none expected (D3), or test fixes only (R3). Tests first in `tests/test_serve_scoping.py` with `launch`
(two keys) and `open_connection`:
1. ann calls `backlog_linear(action="retry", caller_scope=<bob's session>)`: the JSON `error` is
   `SESSION_REFUSED`, and `linear_queue` has no row claimed by `LinearWorker._owner(bob_scope, request_id)` (M5:
   derive it with the worker's own helper, whose separators are `(',', ':')`);
2. with `caller_scope` omitted, the answer's `caller_scope` is ann's session;
3. ann closes the connection, opens a new one, and retries with the old `caller_scope` and `request_id`: no new
   push is started (the receipt answers), and nothing is refused.
   With no `linear.yaml`, `_drain` raises in `_config()` before it submits, so no receipt would exist. From the
   start, monkeypatch `LinearWorker._config` to return a config, with an empty queue, so the first retry commits
   its receipt and the second can replay it (M6).
Proving check: `tests/test_serve_scoping.py`, `tests/test_serve_allowlist_tools.py`, `tests/test_serve_allowlist_e2e.py`.

**Step 4. The lost-context refusal in the client funnel (D4; drop this step if Q1 is answered "keep 020").**
Files: `coordinator/client.py` (`call`, `LOST_CONTEXT_REFUSED`), `coordinator/scopes.py` (`log_session`). Tests first in `tests/test_serve_lost_context.py`:
1. `monkeypatch.setattr(client, "ORIGIN_PROVIDER", lambda: None)`; `Client.call("execute", envelope=<scope ANN>)`
   raises `IdentityRefused` with E2's text. Neither `Client._ready` nor `http.client.HTTPConnection` was called
   (spies): the check sits before `_ready()` (M4). One ERROR record names `execute` and the session;
2. the same for a batch envelope whose scope is host-shaped but whose nested `task.pick` names `ANN`;
3. `linear_retry` with `caller_scope=ANN` and no origin is refused; `status` and `flush` pass;
4. a host-shaped scope (`bs.SESSION_ID`) and no origin passes (the merge-stamp replay shape);
5. an origin present (`lambda: ann_key_id`) passes D4 (the coordinator then judges it);
6. `ORIGIN_PROVIDER = None` (host CLI, solo) with scope `ANN` passes D4;
7. in-daemon: a toy FastMCP tool (the `tests/test_serve_sessions.py` `toy` pattern) that runs
   `NativeCall.execute` on a `threading.Thread` gets E2 back, and the store's `MAX(seq)` is unchanged;
   the same tool without the thread commits as ann.
Then extend the comment on `test_tool_threads_are_the_known_ones` to point at D4.
Proving check: `tests/test_serve_lost_context.py`, `tests/test_serve_session_guard.py`, `tests/test_session_scope.py`,
`tests/test_serve_viewer.py`, `tests/test_viewer_serve_hooks.py`, `tests/test_serve_read_api.py`,
`tests/test_serve_remote_sync.py`, `tests/test_serve_sessions_e2e.py`.

**Step 5. The inventory test, docs and regression.**
Files: `tests/test_serve_scoping.py` (`test_scope_inventory`, §6 of Part A), docstrings of `serve/sessions.py`
and `coordinator/scopes.py`, and one line in the 003 resume file pointing at this design.
- Run §B.1. Record the counts against Step 0.
- Then one fresh reviewer (Opus floor: security-sensitive) checks the code against Part A before the review gate.

## B.1 Regression set

Foreground, `-n 2`, in slices (memory): before Step 1 and after Step 5.
- `tests/test_serve_*.py` (two or three slices, as 013 and 007 did);
- `tests/test_session_scope.py`, `tests/test_claim_ownership.py`, `tests/test_claim_force_rule.py`;
- `tests/test_linear_worker.py`, `tests/test_linear_drain_claims.py`;
- `tests/test_native_service*.py`, `tests/test_viewer_serve_hooks.py`, `tests/test_scopes.py`.

The full suite runs once on `feat/team-collab` before release (rulings), not in this lane.
