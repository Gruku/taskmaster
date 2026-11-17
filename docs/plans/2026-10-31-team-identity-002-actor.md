<!-- User intent: the executable, test-first plan for team-identity-002. Every native command carries an actor
     {handle, agent, clone_id} that the coordinator derives (never trusts from the client) through one resolver
     seam that server mode can later re-source, and team stores record it per event in an opt-in schema 5. -->

# team-identity-002: actor on every command (plan, 2026-10-31)

**Task.** `team-identity-002` (claude-tools backlog, epic `team-identity`, full lane).
**Spec.** `docs/specs/2026-10-31-team-collaboration-native-design.md` rev 3.3: §4 "Actor on every command", §12 (schema steps), §15 (target modules). Review record: `docs/specs/reviews/2026-10-31-team-identity-002-spec-review.md` (I1–I4, M1–M3). The re-review pinned two points: the receipt stores the actor **inside `outcome_json`**, and the `entity_key` backfill runs **inside the schema-5 step's single upgrade transaction** (`native/upgrade.py`).
**Worktree.** `C:\Users\gruku\Files\Claude\taskmaster\.worktrees\team-identity-002`, branch `feature/team-identity-002`, base `a8d37f9` (feat/team-collab, which carries team-identity-001).
**Executor.** One engineer, test-first: write each step's tests, watch them fail for the stated reason, implement, watch them pass, run the step's regression files, commit. Steps are ordered by dependency; run them in order.

**Done when:**

- every native command reaches `commands.execute` carrying `actor = {handle, agent, clone_id}`, filled by one resolver seam in the coordinator writer after `admit()`, overwriting whatever the client sent;
- `agent` comes from FastMCP `clientInfo.name`, captured once per MCP server process and mapped to `claude-code`→`claude`, `codex-*`→`codex`, else `""`; the viewer and CLIs send `""`; internal envelopes carry `taskmaster`;
- the actor is out of the idempotency fingerprint, and the receipt records it inside `outcome_json`;
- `domain_events` gains `actor` and `entity_key` in an opt-in schema 5 (`VERSION = 5`, `SUPPORTED = {3, 4, 5}`), with `entity_key` backfilled and indexed in the same upgrade transaction;
- solo stores stay at schema 3 and remain openable (and their receipts replayable) by 7.4.x.

**Out of scope:** `assignee`/`claimed_on` (003), `author` and stamp preservation through import (004), merge-handles (005), confirmation UI (006), `team.import` and its "importing clone's actor" (team-sync), the rename primitive and its inventory test (team-ids-004, the consumer of `entity_key`), exposing the actor in `backlog_changes_since`, `backlog_query` or the viewer (D11), and server mode itself (only its seam is built here).

---

## 1. Decisions (spec gaps resolved here; plan review may overrule any of them)

| # | Decision | Reason |
|---|---|---|
| D1 | The envelope gains one optional key, `actor`: an object whose keys are a subset of `{handle, agent, clone_id}`, each a string of at most 128 characters. Clients send only `{"agent": …}`. `contracts.validate` checks the shape and hashes the request **without `request_id` and without `actor`**. | The spec's "contracts allow the advisory agent key explicitly" (`contracts.py:78-79` rejects unknown keys). Excluding the whole `actor` keeps the fingerprint of an actor-less envelope byte-identical to 7.4.x's, so a receipt written by either build replays on the other (§4.6 tests it against the real 7.4.0). |
| D2 | **The seam.** New module `taskmaster/coordinator/actor_resolver.py`: `ActorResolver(source).stamp(connection, request, origin) -> dict` returns a *copy* of the request with `actor` replaced. The source is an `IdentitySource` (`identify(connection, request, origin) -> (handle, clone_id)`). `Origin(principal=None)` is the transport-level caller, carried on `Work` and passed to `Coordinator.submit(envelope, *, origin=LOCAL)`. `Coordinator(..., actor_source=None)` defaults to `LocalCloneSource(self.team_identity)`. | Server mode (addendum §3) authenticates a token at the transport and plugs a token source in here: it constructs the coordinator with another `actor_source` and passes `origin` from the authenticated IPC message. `_writer`, `commands.execute`, the event writer and every client stay unchanged. A test proves it with a fake token source (Step 4). |
| D3 | `LocalCloneSource` takes `handle` and `clone_id` from `IdentityGate.known()`: the `local/identity.yaml` identity through the gate's existing stat cache, whenever a valid file exists, regardless of team mode. Otherwise both are `""`. It never probes git and never refuses. An invalid identity file reads as `("", "")` here; the gate still refuses attribution-bearing team writes on it. | One stat per write, already paid in team mode. Solo stores normally have no identity file, so their actor is `{"handle": "", "agent": …, "clone_id": ""}`. |
| D4 | The resolver keeps the client's advisory `agent` only if it is in `actor.AGENTS = {"claude", "codex", "taskmaster"}`; anything else becomes `""`. clientInfo mapping (`actor.agent_for_client`): exactly `claude-code` → `claude`; exactly `codex` or a name starting `codex-` → `codex` (orchestrator ruling at plan review: the spec's `codex-*` means the Codex family, bare `codex` included); anything else (including other casings) → `""`. | The literal spec mapping. Normalizing instead of refusing keeps an unknown client working; the agent is advisory. |
| D5 | Internal envelopes put `actor: {"agent": "taskmaster"}` in the envelope and pass through the resolver like any other, so they get this clone's `handle` and `clone_id`. Sites: the Linear worker (`linear_worker._envelope`), sync jobs (`sync_jobs._write`), the sync worker (`sync_worker` inner `submit`, which also serves managed Git's pre-sync, `git.py:812`), and merge-stamp replay (`merge_stamps.native_apply`, which arrives over IPC). The identity gate's `profile.create` bypasses the writer (it calls `commands.execute` itself), so it carries the full actor `{identity.handle, "taskmaster", identity.clone_id}`. | Spec §4 "Internal envelopes". An `internal=True` flag on `submit` was rejected: it can't reach merge-stamp replay, which comes in through `Client.execute`. A client could also claim `taskmaster`, which is harmless because the agent is advisory and handle/clone_id are always overwritten. |
| D6 | The receipt outcome carries `"actor": request["actor"]` on **every** store, schema 3 included, whenever the request has an actor. | Pinned by the re-review: the actor lives inside `outcome_json`, so `command_receipts` needs no column and 7.4.x reads the receipt as an ordinary extra key. "Solo stores … don't store it" (§4) is read as `domain_events` storage. **Flagged for review.** |
| D7 | Schema 5 is one step, `schema.upgrade_native(connection, 4)`, inside `upgrade.upgrade`'s single `BEGIN IMMEDIATE` transaction, in this order: `ALTER TABLE domain_events ADD COLUMN actor TEXT CHECK(actor IS NULL OR json_valid(actor))`; `ALTER TABLE domain_events ADD COLUMN entity_key INTEGER`; the backfill `UPDATE`; then `CREATE INDEX ix_domain_events_entity_key ON domain_events(entity_key,seq)`. `entity_key` has **no** foreign key. `schema.DDL` (what new stores are born with) is unchanged, so new stores are still schema 3. | One transaction means a store is never marked 5 with columns missing or rows unfilled. SQLite DDL is transactional (the failure test pins it). Without a foreign key, `PRAGMA foreign_keys=ON` in `commands.execute` can never reject an event insert. The index is built after the backfill because that is cheaper. |
| D8 | **`entity_key` rule**, the same in the backfill and the live writer: the `entity_core.entity_key` of `(kind, id)`, except that kinds in `schema.SINGLETON_KINDS = ("backlog", "project")` stay NULL, and an id with no `entity_core` row (a path such as `tasks/x.md` from `projection.resolve`, or a legacy pseudo-kind) stays NULL. Rows that name a deleted entity keep its key, because tombstones keep their `entity_core` row. | The spec says "`__backlog__` rows stay NULL", but the backlog *does* have an `entity_core` row (`workflow.py:1379`), so a plain join would fill it. The singleton rule produces the spec's NULL for a stated reason: fixed ids are never renamed, and `entity_key` exists for the rename primitive. It extends the same NULL to `__project__`. One rule for both paths means a row's value doesn't depend on when it was written. **Flagged for review.** |
| D9 | **The column-presence check is cached per command, not per process.** `commands.Transaction.__init__` sets `self.event_columns = events.columns(connection)` (one `PRAGMA table_info(domain_events)`), and every `events.append` call site passes it. A direct caller that passes nothing gets a fresh check. | The gate upgrades between commands, outside any transaction, on the writer's own connection, and the next command builds a new `Transaction`. So "re-check after a mid-life upgrade" (§4) holds by construction, with no invalidation hook to forget. A process cache keyed by connection was rejected: `sqlite3.Connection` can't be weak-referenced (verified), and `id()` reuse could serve one store's answer to another in tests. The cost is one PRAGMA per command. |
| D10 | The gate upgrades a team store to `schema.VERSION` (5). This happens at bootstrap, as today, and also on the existing-identity path of every team-mode, attribution-bearing write whose store is below 5. So a store a dev build already moved to 4 moves on to 5. `_upgrade`'s threshold changes from `KIND_SINCE["profile"]` to `schema.VERSION`. Solo stores are never upgraded. | §12: a store moves past 3 only when team mode needs the step, and team mode needs both 4 and 5. The extra cost is one manifest lookup per team-mode write. |
| D11 | No read surface changes in this task: `changes_since`, the `backlog_query` SQL compatibility view (`compatibility.py:41-43` selects named legacy columns), viewer DTOs and tool outputs don't expose `actor`. `batch_commands.receipt_answer` already picks receipt keys explicitly. | Keeps agent-facing bytes unchanged (agent-journey budgets). §6.9's "actor set to the remote author" in change feeds belongs to team-sync. |
| D12 | The actor is stored on every `domain_events` row, not once on `command_commits`. Review M3 is declined. | Spec rev 3.3 puts it on `domain_events`. The rename primitive rewrites events by `entity_key`, and one row per event keeps a single-table read. |
| D13 | `Coordinator.submit`'s queued-retry dedup compares the two validated requests **without `actor`**. | A retry whose advisory agent differs (for example, another process of the same client) must attach to the queued work, not raise `Conflict`. The writer stamps a copy and never mutates `work.request`, so the comparison stays between client requests. |

---

## 2. Flow after this task

```
client (MCP tool / viewer / CLI / merge-stamp replay)
  NativeCall.execute -> envelope + actor={"agent": a}         a = client_agent() | "" | "taskmaster"
  Client.execute -> contracts.validate (actor shape; fingerprint excludes actor) -> IPC
coordinator
  submit(envelope, origin=LOCAL) -> Work(request, origin)      dedup ignores actor (D13)
  _writer:
    team_identity.admit(connection, work.request)              001's gate; may upgrade 3/4 -> 5 (D10)
    request = actor_resolver.stamp(connection, work.request, work.origin)   handle/clone_id overwritten (D2, D3)
    commands.execute(connection, request)
      validate again (fingerprint without actor) -> receipt lookup (replay returns the stored actor)
      Transaction.event_columns = events.columns(connection)   (D9)
      events.append(..., extended=..., entity_key=...)         actor JSON + entity_key when the columns exist
      outcome["actor"] = request["actor"] -> receipts.save      (D6)
```

---

## 3. API

### 3.1 `taskmaster/native/actor.py` (new; pure, no I/O)

Header: `# User intent: one definition of the command actor {handle, agent, clone_id}: its shape in envelopes, receipts and events, and the agent slugs, shared by the contracts, the coordinator's resolver and the MCP server.`

```python
KEYS = ("handle", "agent", "clone_id")
AGENTS = frozenset({"claude", "codex", "taskmaster"})   # D4; "" means unknown or none
INTERNAL_AGENT = "taskmaster"
MAX_FIELD = 128

def make(handle: str = "", agent: str = "", clone_id: str = "") -> dict
    # {"handle": ..., "agent": ..., "clone_id": ...} in KEYS order
def check(value) -> None
    # ValueError("invalid actor") unless: dict, set(value) <= set(KEYS), every value a str of len <= MAX_FIELD
def normalize_agent(value) -> str
    # value if value in AGENTS else ""
def agent_for_client(name) -> str
    # "claude" if name == "claude-code"; "codex" if name == "codex" or (isinstance(name, str) and name.startswith("codex-")); else ""
def advisory(value) -> dict
    # {"agent": value} for client envelopes; the one place clients build their actor
```

### 3.2 `taskmaster/native/contracts.py`

- `:78-79`: add `"actor"` to the allowed envelope keys.
- After the `protocol` check: `if "actor" in request: actor.check(request["actor"])`.
- `:113`: fingerprint over `{k: v for k, v in request.items() if k not in ("request_id", "actor")}`. Module constant `UNHASHED = ("request_id", "actor")` with a one-line comment naming why: the actor is stamped after validation (spec §4), and an actor-less envelope keeps 7.4.x's fingerprint.

### 3.3 `taskmaster/native/schema.py`

```python
VERSION = 5
SUPPORTED = frozenset({3, 4, 5})
SINGLETON_KINDS = ("backlog", "project")     # D8: fixed public ids; their events keep entity_key NULL
SCHEMA_5_COLUMNS = (
    "ALTER TABLE domain_events ADD COLUMN actor TEXT CHECK(actor IS NULL OR json_valid(actor))",
    "ALTER TABLE domain_events ADD COLUMN entity_key INTEGER",
)
SCHEMA_5_BACKFILL = (
    "UPDATE domain_events SET entity_key=(SELECT c.entity_key FROM entity_core c "
    "WHERE c.kind=domain_events.kind AND c.public_id=domain_events.id) "
    "WHERE kind NOT IN ('backlog','project')")
SCHEMA_5_INDEX = "CREATE INDEX ix_domain_events_entity_key ON domain_events(entity_key,seq)"
```

`upgrade_native(connection, version)`: `3` returns (as today); `4` runs the columns, then the backfill, then the index (D7); any other version raises as today. Extend the docstring: "4 -> 5 adds domain_events.actor and entity_key (team-identity-002); the backfill runs here, inside the caller's one transaction." Assert in a test that `SCHEMA_5_BACKFILL`'s literal kind list equals `SINGLETON_KINDS`.

Measured on a prototype: on SQLite 3.47.1, 50,000 events over 5,000 entities backfill in 43 ms in memory. The plan is `SCAN domain_events` plus a `SEARCH c USING COVERING INDEX sqlite_autoindex_entity_core_1 (kind=? AND public_id=?)` per row.

### 3.4 `taskmaster/native/events.py`

```python
def columns(connection) -> bool
    # {"actor", "entity_key"} <= {row[1] for row in connection.execute("PRAGMA table_info(domain_events)")}

def append(connection, request, group, kind, ident, operation, before, after, *, entity_key=None, extended=None):
    # extended None -> columns(connection) (D9)
    # schema 3 (not extended): today's INSERT, byte for byte
    # extended: also actor = encode(request["actor"]) if request.get("actor") is not None else NULL,
    #           entity_key = _entity_key(connection, kind, ident, entity_key)
def _entity_key(connection, kind, ident, known):
    # None if kind in schema.SINGLETON_KINDS; known if known is not None;
    # else SELECT entity_key FROM entity_core WHERE kind=? AND public_id=? (None when absent)  (D8)
```

Keep `from datetime import datetime, timezone` as it is: `tests/native_twins.py:37` patches this module's clock.

### 3.5 `taskmaster/native/commands.py`

- `Transaction.__init__` (`:134-147`): `self.event_columns = events.columns(connection)`.
- `_changed` (`:160`) and `import_document` (`:244`): pass `entity_key=key, extended=self.event_columns`.
- `execute` (`:321`): after building `outcome`, `if request.get("actor") is not None: outcome["actor"] = request["actor"]` (D6), before `receipts.save`.
- `native/projection.py:1004` (`projection.resolve`) and `native/sync.py:307` (`sync.apply`): pass `extended=transaction.event_columns`. They pass no `entity_key`, so the writer looks it up or leaves it NULL (D8).

### 3.6 `taskmaster/coordinator/actor_resolver.py` (new)

Header: `# User intent: the coordinator, never the client, decides who a command is from: one seam fills handle and clone_id after admission; today from this clone's git-derived identity, in server mode from the caller's token.`

```python
@dataclass(frozen=True)
class Origin:
    principal: str | None = None     # transport-level caller; None = this host's own clients
LOCAL = Origin()

class IdentitySource(Protocol):
    def identify(self, connection, request: dict, origin: Origin) -> tuple[str, str]: ...   # (handle, clone_id)

class LocalCloneSource:
    def __init__(self, gate: IdentityGate): ...
    def identify(self, connection, request, origin):
        identity = self.gate.known()
        return (identity.handle, identity.clone_id) if identity is not None else ("", "")

class ActorResolver:
    def __init__(self, source: IdentitySource): ...
    def stamp(self, connection, request: dict, origin: Origin = LOCAL) -> dict:
        advisory = (request.get("actor") or {}).get("agent", "")
        handle, clone_id = self.source.identify(connection, request, origin)
        return {**request, "actor": actor.make(handle, actor.normalize_agent(advisory), clone_id)}
```

### 3.7 `taskmaster/coordinator/identity_gate.py`

- New `IdentityGate.known() -> TeamIdentity | None`: `try: return self._load_identity()`; on `IdentityRefused` **or `OSError`**, return `None` (plan review M3: a stat error must not fail solo writes, which never touched the file before). It reads no git and refuses nothing.
- `_upgrade` (`:201-205`): the threshold becomes `int(version) < schema.VERSION` (D10). The checkpoint name stays `"schema_upgraded"`.
- `admit` (`:128-130`) stays `return self._ensure_profile(connection, identity)`. The upgrade must never run before the profile refusals (plan review I1): `_ensure_profile` keeps `_check_profile` and the deleted-profile refusal (`:211-222`) *before* any upgrade. The missing-row path already upgrades after `_check_profile`. The live-row path gains the D10 upgrade of a store at 4: `if row is not None and not row[0]: self._upgrade(connection); return False`. A placeholder handle (`claude`/`test`/`admin`) or a deleted profile therefore refuses and leaves the store at its schema. Never edit `tests/test_identity_gate.py:490` (`..._refuses_and_keeps_schema_3`) to make it pass.
- `_ensure_profile`'s envelope (`:228-230`) gains `"actor": actor.make(identity.handle, actor.INTERNAL_AGENT, identity.clone_id)`. Because the actor isn't in the fingerprint, a profile request committed before this change still replays.

### 3.8 `taskmaster/coordinator/service.py`

- `Work` (`:35`): add `origin: Origin = LOCAL`.
- `__init__` (`:79`): new keyword `actor_source=None`. After `self.team_identity` (`:125`), add `self.actor_resolver = ActorResolver(actor_source or LocalCloneSource(self.team_identity))`.
- `submit(self, envelope, *, origin=LOCAL)` (`:193`): `Work(request, origin=origin)`. The dedup compares `_without_actor(old.request) != _without_actor(request)` (D13), where `_without_actor(r)` returns `{k: v for k, v in r.items() if k != "actor"}`.
- `_writer` (`:277-280`): after the `admit` block, add `request = self.actor_resolver.stamp(connection, work.request, work.origin)`, then `commands.execute(connection, request, …)`. An exception from `stamp` takes the existing `except BaseException` path.
- The IPC `execute` handler (`:488-495`) is unchanged: origin `LOCAL`.

### 3.9 Clients

- `coordinator/adapter.py`: `NativeCall.__init__(…, agent="")` stores `self.agent` (it must be a `str`, else `ValueError`). `execute` (`:50`) adds `'actor': actor.advisory(self.agent)` to the envelope. `open_call(…, agent="")` passes it through.
- `native_routing/registry.py:95`: `runtime.open_call(database, backlog_dir, session, agent=_client_agent())`, where `_client_agent()` is `sys.modules.get("taskmaster.backlog_server")`'s `client_agent()`, or `""` when that module isn't loaded. `dispatch` is only reached from `backlog_server._route_native`, so the module is normally loaded; the fallback keeps hook-only callers safe.
- `native_routing/merge_stamps.py:362`: `runtime.open_call(…, agent=actor.INTERNAL_AGENT)`.
- `native_routing/viewer.py:36-37`: unchanged. The default `""` is the viewer's agent, even though the viewer thread runs inside the MCP process.
- `coordinator/linear_worker.py:59-61` (`_envelope`), `coordinator/sync_jobs.py:126`, `coordinator/sync_worker.py:348`: add `actor=actor.advisory(actor.INTERNAL_AGENT)`.
- `tests/native_projection_oracle.py:26-66` (the engine-oracle double for `runtime.open_call`): accept `agent=""` in `open_call` and `NativeCall.__init__`, and add the same `actor` key to its envelope. Otherwise `registry.dispatch` raises `TypeError` under `engine_oracle=True` twins.

### 3.10 `taskmaster/backlog_server.py` (clientInfo capture)

Near `SESSION_ID` (`:628`); the middleware registration goes after `mcp = …` (`:235`):

```python
from fastmcp.server.middleware import Middleware
from taskmaster.native import actor as _actor

_CLIENT_AGENT: str | None = None     # first MCP initialize of this process wins

def _capture_client_agent(name) -> None:
    global _CLIENT_AGENT
    if _CLIENT_AGENT is None:
        _CLIENT_AGENT = _actor.agent_for_client(name)

def client_agent() -> str:
    """The agent slug of this server's MCP client ("" before initialize, in CLIs and tests)."""
    return _CLIENT_AGENT or ""

class _ClientInfoCapture(Middleware):
    async def on_initialize(self, context, call_next):
        try:
            name = context.message.params.clientInfo.name
        except AttributeError:
            name = ""
        _capture_client_agent(name)
        return await call_next(context)

mcp.add_middleware(_ClientInfoCapture())   # _GuardedToolRegistrar delegates to FastMCP
```

FastMCP 3.4.3 passes `responder.request.root` (an `InitializeRequest`) as `context.message`. A scratch probe confirmed that `Client(app, client_info=Implementation(name="claude-code", version="1"))` reaches `on_initialize` with `params.clientInfo.name == "claude-code"`.

---

## 4. TDD steps

Each step: write the tests, see them fail for the stated reason, implement, run the step's tests plus the listed regression files (§6), then commit with the step's message. New test files start with a `# User intent:` header. Use `tmp_path` and the native twin fixtures (`tests/native_twins.py`), never the live `.taskmaster/`.

### Step 1: the actor shape and the contract (`native/actor.py`, `native/contracts.py`)

Tests in `tests/test_native_actor.py` (new):

- `test_make_has_exactly_the_three_keys`: `actor.make("jdoe", "claude", "3f9a2c4e5b6d7e8f") == {"handle": "jdoe", "agent": "claude", "clone_id": "3f9a2c4e5b6d7e8f"}`, and `actor.make() == {"handle": "", "agent": "", "clone_id": ""}`.
- `test_agent_for_client` (parametrized): `claude-code`→`claude`; `codex-mcp-client`→`codex`; `codex-cli`→`codex`; `codex`→`codex`; `Claude-Code`→`""`; `cursor`→`""`; `""`→`""`; `None`→`""`.
- `test_normalize_agent`: `claude`, `codex` and `taskmaster` are kept; `gpt`, `""`, `None` and `1` become `""`.
- `test_contract_accepts_an_advisory_or_full_actor`: `contracts.validate` accepts a `task.patch` envelope with no `actor`, with `{"agent": "claude"}`, and with all three keys (`handle` and `clone_id` may be `""`).
- `test_contract_rejects_a_malformed_actor` (parametrized; `ValueError` matching `invalid actor`): a list; `{"role": "x"}`; `{"agent": 1}`; `{"handle": "a" * 129}`.
- `test_actor_is_outside_the_fingerprint`: the same envelope without `actor`, with `{"agent": "claude"}`, and with `{"handle": "mallory", "agent": "codex", "clone_id": "x"}` gives one fingerprint.
- `test_actorless_fingerprint_is_unchanged_from_7_4`: for an envelope without `actor`, the fingerprint equals `hashlib.sha256(encode({k: v for k, v in request.items() if k != "request_id"}).encode()).hexdigest()`. That is the 7.4.0 formula (`git show 6ef360b:taskmaster/native/contracts.py`), computed in the test.
- `test_queued_retry_ignores_the_actor` goes in Step 4 (it needs the service).

Implement §3.1 and §3.2.

Regression: `tests/test_native_contracts.py tests/test_native_commands.py tests/test_native_lifecycle.py`.

Commit: `feat(native): command actor shape; contracts allow it and keep it out of the fingerprint`.

### Step 2: schema 5 (columns, backfill, index) in the single upgrade transaction

Changes: §3.3, plus these updates to team-identity-001's tests and docs for `VERSION = 5`:

| File | Change |
|---|---|
| `tests/test_native_schema_upgrade.py:67` | `schema.VERSION == 5` and `schema.SUPPORTED == frozenset({3, 4, 5})` |
| `tests/test_native_schema_upgrade.py:84-92` (`test_upgrade_moves_3_to_4_once_and_changes_nothing_else`) | call `upgrade.upgrade(connection, to=4)` both times; the expected values stay `{'from': 3, 'to': 4}` and `{'from': 4, 'to': 4}` |
| `tests/test_native_profile_kind.py:64` | `schema.KIND_SINCE["profile"] == 4` and `schema.VERSION == 5` |
| `tests/test_identity_gate.py:181`, `:200`, `:449`, `:459` | the expected manifest `"4"` becomes `"5"` (the gate upgrades to `VERSION`, D10; that lands in Step 4, so these four stay red until then, see the note below) |
| `tests/test_native_service_identity.py:86` | `"4"` becomes `"5"` (same note) |
| `tests/test_identity_gate.py:180` | the expected `stages` list's `"4"` becomes `"5"` (same note) |
| `tests/test_native_schema_upgrade.py:223` (slow) | `manifest_version == '5'` |
| `tests/test_native_schema_upgrade.py:257` | `client.identity['schema'] == 5` (`protocol.py:85` reports `schema.VERSION`) |
| `docs/native-store.md:583-586`, `:590`, `:594-595` | Compatibility rows: "Native schema 4 or 5 (team mode was turned on)" for this build; 7.4.x refuses schema 4 **or 5**; "A different native build … must support the store's native schema (3, 4 or 5)". The sentence becomes "Turning team mode on upgrades the store to native schema 5; …" |

**Note on order.** The gate already calls `upgrade.upgrade(connection)` with the default `to=schema.VERSION`, so once `VERSION = 5` the gate's bootstrap goes straight to 5 even before Step 4. Run the five gate and service assertions above in this step; they should pass already. If one doesn't, it's because of the threshold in `_upgrade` (`< KIND_SINCE["profile"]`), which Step 4 changes. Then leave it red and note it in the commit body.

Tests in `tests/test_native_schema5.py` (new). Fixture: the `root` twin of `tests/test_native_schema_upgrade.py:36-41` (seed one task). Helpers: `manifest_version`, `native_connection`, `commit_only`.

- `test_schema_5_constants`: `VERSION == 5`; `SUPPORTED == {3, 4, 5}`; `BASE_VERSION == 3`; `SINGLETON_KINDS == ("backlog", "project")`; the `NOT IN` list in `SCHEMA_5_BACKFILL` names exactly `SINGLETON_KINDS`.
- `test_new_stores_have_no_actor_columns`: a fresh twin is at `'3'` and `events.columns(connection) is False` (Step 3 adds `columns`; until then, use `PRAGMA table_info` inline).
- `test_upgrade_3_to_5_adds_columns_backfills_and_indexes`:
  - Before the upgrade, on the schema-3 twin, write through `commit_only`: `task.create` ×2 (A, B), a `task.patch` on A, a `task.archive` of B, a `thread.update` (it writes the `backlog`/`__backlog__` row, `workflow.py:1376-1396`; take the arguments from the `backlog_thread_update` tool's native adapter) and a `project.set` (`workflow.py:255`). Set B's `entity_core.deleted=1` with a raw UPDATE as well, to stand in for a tombstone: no native operation deletes today.
  - Insert two raw rows with `INSERT INTO domain_events(ts,session,tool,kind,id,op) VALUES(...)`: kind `task`, id `tasks/x.md`, op `resolve` (a path id); and kind `session`, id `s-1` (a kind with no `entity_core` row).
  - `upgrade.upgrade(connection) == {"from": 3, "to": 5}` and the manifest is `'5'`.
  - Every row for A and B (B archived and marked deleted included) has `entity_key == entity_core.entity_key` of that task.
  - The backlog, project, path and session rows have `entity_key IS NULL`.
  - Every row has `actor IS NULL`.
  - `PRAGMA index_list(domain_events)` contains `ix_domain_events_entity_key`.
  - `EXPLAIN QUERY PLAN SELECT seq FROM domain_events WHERE entity_key=?` mentions `ix_domain_events_entity_key`.
- `test_upgrade_4_to_5_runs_only_the_new_step`: `upgrade.upgrade(connection, to=4)` then `upgrade.upgrade(connection) == {"from": 4, "to": 5}`. Apart from the two new columns, the new index and the manifest row, `iterdump()` is unchanged. Compare row counts per table, and `SELECT seq,ts,session,tool,kind,id,op,fields,before,after,commit_key FROM domain_events` before and after.
- `test_failed_schema_5_step_rolls_back_the_columns`: run `upgrade.upgrade(connection, to=4)` first. Monkeypatch `schema.SCHEMA_5_BACKFILL` to `"UPDATE domain_events SET no_such_column=1"`. `upgrade.upgrade(connection)` raises `sqlite3.OperationalError`; the manifest stays `'4'`; `PRAGMA table_info(domain_events)` has neither new column; there's no open transaction.
- `test_actor_column_rejects_non_json`: after the upgrade, a raw insert with `actor='not json'` raises `sqlite3.IntegrityError`.
- `test_schema_5_store_is_refused_by_a_schema_3_build`: reuse `_as_older_build` from `tests/test_native_schema_upgrade.py` (import it). After `upgrade.upgrade`, `db.assert_native` inside `BEGIN` raises `UnsupportedStoreError` matching `Unsupported native staging schema or protocol`, and `iterdump()` is unchanged.
- `test_schema_5_backfill_at_codemaestro_scale`: Measured at plan review on a copy of the real CodeMaestro store: 11,107 events, about 2.9 KB average payload (max 266 KB), backfill 0.36 s, 16.8 MB of WAL. This test uses about 10× the events with realistic payload sizes (mix 1–4 KB rows with a few 250 KB rows), and keeps the 5 s bound.
  - On the twin at 3, in one transaction, insert 5,000 raw `entity_core` rows (`kind='task'`, `public_id=f'scale-{n:05d}'`, `revision=1`, `last_seq=0`). Then insert 50,000 raw `domain_events` rows: 47,000 that name those tasks (round-robin), 2,000 with `id='__backlog__'` and `kind='backlog'`, and 1,000 path ids.
  - Time `upgrade.upgrade(connection)` with `time.perf_counter()`.
  - Assert `COUNT(entity_key) == 47_000 + <events the seed wrote for real entities>`. Count those seed rows before inserting the synthetic ones.
  - Assert the elapsed time is under 5.0 s, and report it with `record_property("schema5_backfill_seconds", elapsed)`.
  - Leave it unmarked: the prototype ran in 43 ms in memory. If it takes over 2 s on this machine, mark it `@pytest.mark.slow` and say so in the commit body.

Regression: `tests/test_native_schema_upgrade.py tests/test_native_profile_kind.py tests/test_native_schema.py tests/test_native_migration.py tests/test_native_service_build.py`.

Commit: `feat(native): opt-in schema 5 — domain_events actor and entity_key, backfilled and indexed in the upgrade transaction`.

### Step 3: the event writer and the receipt

Changes: §3.4 and §3.5.

Tests, appended to `tests/test_native_actor.py`. Envelopes are built like `tests/test_native_schema_upgrade.py:54-57` (`patch(root, key, title)`) plus an `actor` key, and run with `commands.execute` on `native_connection(root)`:

- `test_schema_3_events_have_no_actor_but_the_receipt_does`: on the store at `'3'`, a patch with `actor = make("jdoe", "claude", "c" * 16)`. The receipt has `["actor"]` equal to that dict. The `command_receipts.outcome_json` row decodes to the same `actor`. `events.columns(connection) is False`. The INSERT path didn't touch the missing columns (no exception is the evidence).
- `test_receipt_without_an_actor_has_no_actor_key`: an envelope with no `actor` gives a receipt with no `"actor"` key, so direct engine callers and 7.4.x-shaped callers see the old shape.
- `test_schema_5_events_store_actor_and_entity_key`: after `upgrade.upgrade`:
  - A patch on `test-epic-001` stores `actor` JSON equal to `encode(make(...))`, and `entity_key` equal to that task's key.
  - A `task.create` (new id) stores its new key on the create event.
  - A `thread.update` event (kind `backlog`) and a `project.set` event store NULL `entity_key` (D8).
- `test_schema_5_event_without_an_actor_stores_null`: an envelope without `actor` after the upgrade stores `actor IS NULL`.
- `test_direct_append_for_a_path_id_stores_null_entity_key`: run `BEGIN`, then `events.append(connection, request, None, "task", "tasks/x.md", "resolve", {}, {})` with no `extended` (it checks itself), then read the row back and `ROLLBACK`. `entity_key IS NULL`.
- `test_upgrade_mid_life_on_the_same_connection_is_seen_by_the_next_command` (D9): on one connection, a patch at schema 3 (no columns), then `upgrade.upgrade(connection)` on **the same connection**, then a second patch. The second event's `actor` and `entity_key` are set. Monkeypatch `events.columns` with a counting wrapper: it was called once per `commands.execute`.
- `test_replay_returns_the_original_actor`: commit envelope E with actor X, then re-execute E with actor Y (same `request_id`). The result is the first receipt (`actor == X`), there's no new `domain_events` row, and no `Conflict`.

Regression: `tests/test_native_commands.py tests/test_native_lifecycle.py tests/test_native_projection_drain.py tests/test_native_sync_tool.py tests/test_native_compatibility_sql.py tests/test_native_cursors.py`.

Commit: `feat(native): events record actor and entity_key on schema 5; receipts carry the actor`.

### Step 4: the resolver seam in the coordinator, and internal envelopes

Changes: §3.6, §3.7, §3.8, and the internal envelope sites of §3.9 (Linear worker, sync jobs, sync worker, gate `profile.create`).

Tests in `tests/test_actor_resolver.py` (new), as unit tests on a twin connection (no service):

- `test_local_source_reads_the_identity_file`: write `identity.yaml` with `team_identity.write_identity` (handle `jdoe`). `LocalCloneSource(IdentityGate(root, store_id=…)).identify(conn, {}, LOCAL) == ("jdoe", identity.clone_id)`. Monkeypatch `identity_gate.read_git_identity` to raise, proving git is never read.
- `test_local_source_without_identity_is_empty`: `("", "")`.
- `test_local_source_with_an_invalid_identity_file_is_empty`: a file with `handle: JDoe` gives `("", "")`, and doesn't raise.
- `test_stamp_overwrites_handle_and_clone_id_and_keeps_a_known_agent`: the request's actor `{"handle": "mallory", "clone_id": "forged", "agent": "codex"}` gives `{"handle": "jdoe", "agent": "codex", "clone_id": <clone_id>}`. The input dict is unchanged (compare it with a `deepcopy` taken first).
- `test_stamp_drops_an_unknown_agent`: `{"agent": "gpt"}` gives `agent == ""`, and a request with no actor gives `agent == ""`.
- `test_gate_upgrades_a_schema_4_team_store_to_5`:
  - On a twin, run `upgrade.upgrade(connection, to=4)`, then `profile.create` for `jdoe` through `commit_only`, then write `identity.yaml` for `jdoe`.
  - With `team_mode_enabled` monkeypatched to True, `IdentityGate.admit` on a `task.patch` request upgrades the store to `'5'` and returns `False` (the profile exists).
- `test_gate_profile_create_carries_the_internal_actor`: a bootstrap on a schema-3 twin (team on, git stub `John Doe`) leaves a `profile.create` event whose `actor` is `{"handle": "jdoe", "agent": "taskmaster", "clone_id": <identity clone_id>}`.

Tests in `tests/test_native_service_actor.py` (new), through a real `Coordinator` (the fixtures of `tests/test_native_service_identity.py:19-50`: `twins`, `git_stub`, `team_on`, `_client`):

- `test_tool_write_is_stamped_in_solo_mode`: `bs.backlog_add_task(…)` with the default `bs._CLIENT_AGENT` (None). The store stays at `'3'`, and the latest receipt (`SELECT outcome_json FROM command_receipts ORDER BY commit_seq DESC LIMIT 1`) has `actor == {"handle": "", "agent": "", "clone_id": ""}`.
- `test_tool_write_is_stamped_in_team_mode`: team on and git stub. After `bs.backlog_add_task`, the store is at `'5'`. The task's create event has `actor` with handle `jdoe` and the identity's `clone_id`, and `entity_key` equal to the task's key. The receipt carries the same actor.
- `test_client_sent_handle_is_overwritten`: team on. `_client(root).execute(envelope)` with `actor = {"handle": "mallory", "clone_id": "forged-clone-01", "agent": "codex"}` returns a receipt and event actor of `{"handle": "jdoe", "agent": "codex", "clone_id": <identity clone_id>}`. In solo mode (another twin, team off), the same envelope gives `{"handle": "", "agent": "codex", "clone_id": ""}`.
- `test_retry_after_a_handle_change_matches_its_receipt`: team on.
  1. Commit envelope E (`request_id="r1"`) through `_client(root).execute`, and keep receipt R1 (actor handle `jdoe`).
  2. Rewrite `identity.yaml` with handle `johnd` (same `clone_id`, new mtime, via `dataclasses.replace` and `write_identity`).
  3. `_client(root).execute(E)` again: it returns a receipt equal to R1 (handle still `jdoe`), raises no `Conflict`, and the `domain_events` max seq is unchanged.
  4. A **new** request then stamps handle `johnd`, and the gate creates profile `johnd`, which is 001's behaviour.
- `test_queued_retry_ignores_the_actor` (D13):
  - Pause the writer: increment `owner.pauses` under `owner.admission`, as the sync barrier does.
  - `owner.submit(E with {"agent": "claude"})` and `owner.submit(E with {"agent": "codex"})` return the **same** future.
  - Release the pause; the future resolves once.
- `test_placeholder_handle_refuses_and_keeps_schema_3` (plan review I1): team on, schema-3 store, identity handle `admin`. A write refuses with `IdentityRefused`, and `manifest_version` is still `"3"`.
- `test_deleted_profile_refuses_and_keeps_schema_4` (plan review I1, re-check): team on, a store at schema 4, because profile rows exist only from 4 (`KIND_SINCE["profile"] == 4`). The identity's profile row is deleted. A write refuses, and `manifest_version` stays `"4"`; it must not reach 5.
- `test_internal_sync_writes_carry_taskmaster`:
  - Team on, bootstrap with one tool write, then hand-edit the seed task's file (as in `tests/test_native_sync_tool.py`), then `bs.backlog_sync()`.
  - At least one `sync.apply` row was written by this sync (assert it, so the check can't pass vacuously), and every `domain_events` row with `tool IN ('sync.apply', 'sync.job')` written by this sync has `json_extract(actor,'$.agent') = 'taskmaster'` and handle `jdoe`.
- `test_linear_envelopes_carry_taskmaster`: `LinearWorker(SimpleNamespace(identity={"store_id": "s"}))._envelope("scope", "id", "linear.settle", {})["actor"] == {"agent": "taskmaster"}`.
- `test_sync_job_records_carry_taskmaster`: monkeypatch a fake `owner` whose `submit` records envelopes. `sync_jobs._write(owner, job, record)` sends `actor == {"agent": "taskmaster"}`. Build `job` the way `tests/test_native_service_sync.py` does; if no helper exists, a `SimpleNamespace(id="j1")` is enough for `_write`.
- `test_an_identity_source_replaces_the_git_source_without_touching_callers` (the server-mode seam):
  - `class TokenSource: identify(conn, request, origin) -> {"tok-ann": ("ann", "client-7f3a")}.get(origin.principal, ("", ""))`.
  - Run `Coordinator(root, actor_source=TokenSource()).start()`. `owner.submit(patch_envelope, origin=Origin(principal="tok-ann")).result(timeout=30)` gives a receipt with `actor == {"handle": "ann", "agent": "", "clone_id": "client-7f3a"}`.
  - The same coordinator, through `Client.execute` (origin `LOCAL`), gives `("", "")`.
  - Close the owner in `finally`. Use a solo twin: the token source must work without team mode.

Regression: `tests/test_identity_gate.py tests/test_native_service_identity.py tests/test_native_service.py tests/test_native_service_sync.py tests/test_native_service_linear.py tests/test_linear_worker.py tests/test_native_service_build.py tests/test_native_service_adapter.py`.

Commit: `feat(coordinator): actor resolver seam stamps every command after admission; internal writes act as taskmaster`.

### Step 5: client agents (MCP clientInfo, NativeCall, merge-stamp replay)

Changes: §3.9 (`adapter.py`, `registry.py`, `merge_stamps.py`, the oracle double) and §3.10.

Tests in `tests/test_client_agent.py` (new):

- `test_initialize_captures_the_client_agent_once`:
  - Run `monkeypatch.setattr(bs, "_CLIENT_AGENT", None)`.
  - `asyncio.run` an `async with Client(bs.mcp._inner, client_info=mcp.types.Implementation(name="claude-code", version="1"))` block (the pattern of `tests/test_server_instructions.py:11-15`). Then `bs.client_agent() == "claude"`.
  - A second client with `codex-mcp-client` leaves it at `"claude"`.
- `test_unknown_client_maps_to_empty`: with a fresh `None` and the name `cursor`, `bs.client_agent()` is `""`.
- `test_tool_writes_send_the_captured_agent`: run `monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")` on a native twin (`make_twins(..., visibility='legacy')` and `twins.at(native)`). `bs.backlog_add_task(…)` stores a receipt whose `actor.agent == "claude"`.
- `test_native_call_sends_its_agent`: monkeypatch `adapter.Client` with a recorder whose `execute(envelope)` returns a minimal outcome (`{"receipt": {"affected": [], "commit_seq": 0}, "projection": {"state": "unchanged", "through": 0, "notices": []}}`). `open_call(db, backlog_dir, "s", agent="codex")` then `call.execute("task.patch", …)` sends `envelope["actor"] == {"agent": "codex"}`, and the default sends `{"agent": ""}`.
- `test_native_call_rejects_a_non_text_agent`: `NativeCall(conn, dir, "s", agent=None)` raises `ValueError`.
- `test_viewer_writes_send_no_agent`: with `bs._CLIENT_AGENT = "claude"`, a viewer `PATCH /api/tasks/<id>` against a native twin stores a receipt with `actor.agent == ""`. Drive `native_routing.viewer.write` the way the existing native viewer write tests do (find them with `grep -l "viewer.write\|_native_viewer" tests`); if that harness is too heavy, assert instead that `viewer._open(database)` yields a `NativeCall` with `agent == ""`.
- `test_merge_stamp_replay_acts_as_taskmaster`: monkeypatch `runtime.open_call` with a recorder that captures `kwargs["agent"]` and raises `ServiceUnavailable` after recording. `merge_stamps.native_apply(db, backlog_dir, "s", autostart=False)({"task_id": "x", "rung": "r", "sha": "a" * 40})` raises `merge_stamps.Unavailable`, and the recorded agent is `"taskmaster"`.
- `test_engine_oracle_double_accepts_an_agent`: `tests.native_projection_oracle.open_call(db, dir, "s", agent="claude")` constructs without error.

Regression: `tests/test_server_instructions.py tests/test_native_routing_hooks.py tests/test_merge_stamp_contract.py tests/test_native_service_routing.py tests/test_native_service_adapter.py`, plus one engine-oracle twin file (`grep -l "engine_oracle=True" tests | head -1`).

Commit: `feat(server): capture MCP clientInfo once per process and send the agent with every native write`.

### Step 6: compatibility with 7.4.x for solo stores

Tests, appended to `tests/test_native_service_actor.py`:

- `test_solo_store_stays_at_schema_3`:
  - With `bs._CLIENT_AGENT = "claude"`, run 5 tool writes (add, update, note, `backlog_sync`, batch update) and a coordinator restart (`client_module`/`compatibility_client` close and reopen, as `tests/test_native_schema_upgrade.py:70-79` does).
  - The manifest is `'3'` throughout. `PRAGMA table_info(domain_events)` has neither new column. There's no `identity.yaml`. Every receipt's `actor.handle == ""`.
- `test_7_4_shaped_build_opens_a_solo_store_this_build_wrote`:
  - After the writes above, apply `_as_older_build` (imported from `test_native_schema_upgrade`).
  - `db.assert_native` inside `BEGIN`, `db.verified_snapshot`, and a `commands.execute` of an actor-less `task.patch` all succeed.
  - `commands.execute` of the **actor-less** form of a request this build committed (same `request_id`, same arguments) returns that receipt (same `commit_seq`), with no new event.
- `test_released_7_4_0_build_reads_and_replays_a_solo_store_this_build_wrote` (`@pytest.mark.slow`). Reuse `_released_build` and the probe style of `tests/test_native_schema_upgrade.py:145-195`, with a new probe script that takes a JSON envelope as `argv[3]`:
  1. Open the store with the old build and run `assert_native` inside `BEGIN`.
  2. List task ids and titles.
  3. Run the old `commands.execute(connection, envelope)`, where the envelope is the actor-less form of the request this build committed through the coordinator with `actor = {"agent": "claude"}`.
  4. Print `{"tasks": …, "commit_seq": receipt["commit_seq"], "actor": receipt.get("actor")}`.

  Assertions:
  - The old build lists the patched title.
  - It returns this build's `commit_seq`, and the stored `actor` comes back with the receipt (`{"handle": "", "agent": "claude", "clone_id": ""}`).
  - `MAX(seq)` of `domain_events` is unchanged.
  - The database file's SHA-256 is unchanged by the old build's run, compared with the existing `_settled_digest` helper (it checkpoints the WAL first, so the check doesn't flake).

Regression: the Step 2 and Step 4 lists.

Commit: `test(native): solo stores stay at schema 3 and keep replaying on 7.4.x with actor-stamped receipts`.

---

## 5. Files

Create:

- `taskmaster/native/actor.py`
- `taskmaster/coordinator/actor_resolver.py`
- `tests/test_native_actor.py`
- `tests/test_native_schema5.py`
- `tests/test_actor_resolver.py`
- `tests/test_native_service_actor.py`
- `tests/test_client_agent.py`

Modify:

- `taskmaster/native/contracts.py`
- `taskmaster/native/schema.py`
- `taskmaster/native/events.py`
- `taskmaster/native/commands.py`
- `taskmaster/native/projection.py` (one call site)
- `taskmaster/native/sync.py` (one call site)
- `taskmaster/coordinator/identity_gate.py`
- `taskmaster/coordinator/service.py`
- `taskmaster/coordinator/adapter.py`
- `taskmaster/coordinator/linear_worker.py`
- `taskmaster/coordinator/sync_jobs.py`
- `taskmaster/coordinator/sync_worker.py`
- `taskmaster/native_routing/registry.py`
- `taskmaster/native_routing/merge_stamps.py`
- `taskmaster/backlog_server.py` (capture only, §3.10)
- `tests/native_projection_oracle.py`
- `tests/test_native_schema_upgrade.py`
- `tests/test_native_profile_kind.py`
- `tests/test_identity_gate.py`
- `tests/test_native_service_identity.py`
- `docs/native-store.md` (Compatibility table and sentence only)

Not touched: `native/upgrade.py` (its loop already runs every step to `schema.VERSION` in one transaction), `native/receipts.py` (the outcome is opaque JSON), `native/db.py` (it reads `schema.SUPPORTED`), `coordinator/client.py`, `native_routing/viewer.py`, `project.py`, and the skills.

## 6. Commands

Run from the worktree root in Git Bash, always with the main checkout's venv interpreter. Never use bare `python`, which is the wrong interpreter on this machine. The worktree's own `taskmaster/` is imported first (checked: `taskmaster.__file__` resolves into the worktree).

```bash
PY=/c/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python
# one step's tests (unique basetemp per run)
"$PY" -m pytest tests/test_native_actor.py -q -p no:cacheprovider --basetemp="$TEMP/tm-id002-s1-$(date +%s)"
# step regression subset (example: Step 4)
"$PY" -m pytest tests/test_actor_resolver.py tests/test_native_service_actor.py tests/test_identity_gate.py \
  tests/test_native_service_identity.py tests/test_native_service.py tests/test_native_service_sync.py \
  tests/test_native_service_linear.py tests/test_linear_worker.py tests/test_native_service_build.py \
  tests/test_native_service_adapter.py -n 2 -q -p no:cacheprovider --basetemp="$TEMP/tm-id002-s4-$(date +%s)"
# slow tests of this task (Step 6's real-7.4.0 probe, and Step 2's scale test if it was marked)
"$PY" -m pytest tests/test_native_service_actor.py tests/test_native_schema5.py tests/test_native_schema_upgrade.py \
  -m slow -q -p no:cacheprovider --basetemp="$TEMP/tm-id002-slow-$(date +%s)"
```

- Before reporting done, run every new file and every regression file named in Steps 1–6 together, with `-n 2`.
- Don't run the full suite: one is running in the main checkout, and the review gate runs the full suite.
- Don't edit sources while a run is in progress: the build fingerprint changes and about 13 real-coordinator tests fail spuriously.

## 7. Risks

- **R1 One-way upgrade, now to 5.** Turning team mode on moves a store to schema 5, and 7.4.x refuses it cleanly. Solo stores stay at 3, and their actor-stamped receipts still replay on 7.4.x (D1, D6, Step 6). The release notes must say this, as 001's R1 does.
- **R2 Dev stores at 4.** A store that turned team mode on under the 001 build sits at 4 and moves to 5 on its next team-mode write (D10). No released build ever wrote schema 4.
- **R3 Event INSERT cost.** On schema 5, each event pays one indexed `entity_core` lookup when the caller gave no key (only `projection.resolve` and `sync.apply`), and every command pays one `PRAGMA table_info` (D9). Both are microseconds against the `synchronous=FULL` commit.
- **R4 Receipt shape.** Receipts gain an `actor` key. Tool outputs choose their receipt keys explicitly (`batch_commands.receipt_answer`), so agent-facing bytes don't change. A test that compares whole receipts against a literal would need the key; none was found (`grep -rn "set(receipt)\|receipt.keys()" tests`).
- **R5 Merge overlap.** `store-perf-001` touches the writer path in `service.py`. The change there is a single line, after the `admit` block; rebase onto it if it lands first.
- **R6 FastMCP API.** The capture uses `Middleware.on_initialize` (FastMCP 3.4.3; `pyproject.toml` pins `fastmcp>=3.4,<4`). If a 3.x release renames it, the agent falls back to `""` and nothing else breaks. `test_initialize_captures_the_client_agent_once` catches it.

## 7a. Rollback

Reverting this task's code is safe for schema-3 (solo) stores: they never move. A store already upgraded to 5 is refused by both the team-identity-001 build and 7.4.x, and there is no 5→3 (or 5→4) path. Recovery for such a store is restoring its pre-upgrade backup. Dev stores are the only ones at risk, because no released build turns team mode on. The release notes carry this with R1.

## 8. Left for later tasks (mention, don't build)

- `team.import` events carrying the importing clone's actor (team-sync), and change feeds exposing `actor` (§6.9).
- The rename primitive rewriting `domain_events` by `entity_key`, and the inventory schema test listing `domain_events.id` (team-ids-004).
- Server mode: the token `IdentitySource`, an IPC `execute` that passes `Origin(principal=…)`, and the per-request gate (addendum §3). D2's seam covers the actor only: the identity gate itself is not behind it, and in server mode `admit` would still bootstrap from the host's git identity. team-server-002 replaces the gate's source too (plan review M2).
- Viewer and `backlog_query` display of actors (team-viewer).
