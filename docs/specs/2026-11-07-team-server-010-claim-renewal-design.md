<!-- User intent: the task-level design and TDD plan for team-server-010. On the shared Taskmaster server a teammate's
     claims stay theirs for as long as their agent's connection is open, without the agent having to renew them;
     when the connection goes, the claims lapse in about 15 minutes instead of 4 hours; a server restart never costs anyone a claim. -->

# team-server-010: connection-bound claim renewal and the short disconnected TTL (design and plan, 2026-11-07, rev 2)

**Task.** `team-server-010`. Done when the server renews a remote session's claims while its MCP connection is
open, a disconnected session's claims fall to a short TTL (15 min), and after a restart the claims of sessions
that were alive get one grace TTL.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §7.
`docs/specs/2026-11-01-team-server-003-sessions-design.md` §8 (the 010 seam: `SessionRegistry.live()`,
`on_disconnect`, "never block the loop").
**Shared seam.** `docs/specs/2026-11-07-team-server-004-principal-scoping-design.md` Part A §8: server-internal
writes use a fixed internal scope (`scopes.CLAIM_KEEPER`) and `LOCAL` origin, and name the person's session only
as `arguments.session`. Build 004 first.
**Builds on** `feat/team-collab` 87977af.
**Verified against.** Code read at 87977af. The MCP SDK idle rule was read in the repo venv:
`mcp/server/streamable_http_manager.py:258-260,289-308` (each request pushes the idle deadline back, and an open
GET stream doesn't). Nothing was spiked; Steps 3-5 are the first proof.
**Plan.** Embedded in §11; the regression set is §12.
**Review history.** Rev 1 (4f5ebdf): design review FAIL, with 2 Important and 6 Minor findings for 010
(`D:\scratchbook\taskmaster\ts004-review\design-review.md`). Rev 2 fixes:
- **I1** (a mixed-holder bundle makes every keeper write a `Conflict`, which was silenced as a lost race): the scan
  detects mixed bundles and skips them with one WARNING per (bundle, holder). DEBUG is kept for revision conflicts
  only (D3a, E1, E6, Steps 3.4-3.7).
- **I2** (one failing write ended the startup pass, and "no member can write yet" was wrong): every group is
  isolated in both passes, and the writers that can run at start are named (D5, Step 4.4-4.5).
- Minors M8-M13 are folded in, each tagged at its place. Q1-Q7 were accepted as recommended.

## 1. Problem

Claims carry `claim_expires = now + ttl` (4 h by default, `native/claims.py:79`), stamped by pick and renew. A
remote holder's liveness is `True` while its connection is open, else `None` (`claims.py:245-248`). `read()`
expires a claim when its stamp passes *whether or not the holder is live* (`claims.py:313-316`). So on a server
today:

- an agent working past 4 h loses its claim mid-task unless it remembers `backlog_claim(renew)`;
- an agent that crashed, or whose user closed the laptop, keeps its claims for up to 4 h;
- after a daemon restart every old session is gone, and its claims stand for whatever was left of 4 h, or lapse
  if the outage outlasted them.

Two facts shape the fix:

- **A reconnect is a new session** (003 D2): a restarted Claude Code process, a server restart, or a transport that
  went idle for `session_idle_seconds` (1800 s, `serve/config.py:14`) all mint a new `<handle>@<nonce>`. The old
  session's claims become *adoptable* by the same handle (003 D6): `pick` or `renew` moves them without `force`.
- **The SDK keeps a crashed client's transport for up to the idle timeout.** Only requests push the deadline back,
  so a crashed client reads as connected for at most 30 min; a clean exit sends `DELETE` and ends it at once.

## 2. Decisions

**D1. One server-side writer, the claim keeper, owns renewal, shortening and the restart grace.**
`taskmaster/serve/claim_keeper.py` (new) runs one daemon thread, `taskmaster-claim-keeper`. It is created only by
`ServeDaemon`, so solo and host stdio processes never run it. It never blocks the event loop: the sweep's
`on_disconnect` callback only sets the keeper's wake event (003 S-M7).

**D2. The keeper writes through the coordinator as host work (the 004 §8 seam).** Each action is one envelope
submitted in process with `owner.submit(envelope).result(SUBMIT_TIMEOUT)` (origin `LOCAL`):

```python
{"protocol": 2, "store_id": <store>, "caller_scope": scopes.CLAIM_KEEPER, "request_id": uuid4().hex,
 "operation": "task.claim_renew",
 "arguments": {"id": <task>, "session": <holder>, "ttl_seconds": <ttl>},
 "expected_revisions": [{"kind": "task", "id": <member>, "revision": <rev>} for each member in the group],
 "actor": actor.advisory(actor.INTERNAL_AGENT)}
```

- `task.claim_renew` with `session = holder` is the holder's own claim (`mine` by exact equality), so
  `_claim_change` (`native/workflow.py:905-942`) re-stamps `now + ttl` for the holder and every bundle member it
  holds. That reuses the one claim writer, its bundle rule and its `CLAIM_FIELDS` discipline. There is no new
  operation.
- The renew covers the whole bundle (`_claim_targets`), and it refuses if any member is held by someone the
  holder doesn't own (`claims.blocked_by`), an expired foreign holder included. So the scan submits only groups
  in which every held member has exactly this holder (D3a).
- Members held by the same person's *other* disconnected session would be adopted silently by the renew
  (`adoptable` makes them `mine`). D3a treats them as mixed too, so the keeper never adopts on an agent's behalf
  (M9).
- **`expected_revisions` closes the scan-to-write race.** If the agent released, adopted or re-picked between the
  keeper's read and its write, the revision moved, the command raises `Conflict`, and the keeper skips. This
  matters for adoption: without the pin, a renew naming the old session on a claim already adopted by another
  disconnected same-handle session would read as `mine` through `adoptable` and move the claim *back*.
- `LOCAL` origin: `requester` is `None`, so 003 D5/D11 don't apply and the write works after the holder's key is
  revoked. `task.claim_renew` is identity-exempt (`team_identity._exempt`), so team mode doesn't refuse it.
  The actor is the host (`operator`, internal agent), and `changes.session` reads `claim-keeper` (no host names;
  007 M3 isn't made worse).

**D3. The rules, as one pure function.**

```python
# serve/claim_keeper.py
@dataclass(frozen=True)
class Row:                 # one claimed task, read from a snapshot
    task_id: str; revision: int; holder: str; status: str; bundle: str; expires: datetime | None
    # expires: the holder's own stamp (claim_expires when claim_expires_for == holder), else None

@dataclass(frozen=True)
class Action:
    kind: str              # "renew" | "shorten" | "grace"
    holder: str; ttl: int; members: tuple[tuple[str, int], ...]   # (task_id, revision) of the group

def decide(group: list[Row], *, now, connected: bool, outage: Outage | None = None) -> str | None
    # One decision per group (M8): one write re-stamps every member, so the rule is judged on the whole group.
    # `earliest` is the smallest member stamp (None when any member has none).
```

| Holder | Situation (judged on the group, M8) | Action | New stamp |
|---|---|---|---|
| not remote-shaped (`host-pid-nonce`, a migrated `locked_by`) or a channel (`@viewer`, `@read`) | any | none (host liveness rules stay, addendum §7) | |
| any | status `done`/`archived` | none (expired by rule) | |
| remote, connected | `earliest is None` or `earliest - now < RENEW_BELOW` (1 h) | `renew` | `now + DEFAULT_TTL` (4 h) |
| remote, connected | otherwise | none | |
| remote, disconnected, steady state | every member's stamp is None or `> now + DISCONNECTED_TTL` | `shorten` | `now + DISCONNECTED_TTL` (15 min) |
| remote, disconnected, steady state | otherwise (a member already short or expired) | none: never extends, never revives | |
| remote, startup pass (§D5) | holder in `outage.connected`, or every member's stamp is None or `> outage.at` | `grace` | `now + DISCONNECTED_TTL` |
| remote, startup pass | otherwise (a member lapsed before the outage) | none | |

- Rows are grouped by `(holder, bundle or task_id)`, so a bundle is written once, with every member's revision
  pinned (`_claim_targets` renews them as a unit). In practice a bundle's members carry one stamp, because pick
  and renew stamp them together. The group rule matters only for a member added to the bundle later.

**D3a. The scan (M13) and mixed bundles (I1).** Each pass reads one read-only snapshot:

```sql
SELECT c.public_id, c.revision, json_extract(t.locked_by_json,'$') FROM task_operational t
JOIN entity_core c ON c.entity_key = t.entity_key
WHERE c.kind='task' AND c.deleted=0 AND json_extract(t.locked_by_json,'$') LIKE '%@%'
  AND COALESCE(json_extract(c.status_json,'$'),'') NOT IN ('done','archived')
```

- Python drops the rows whose holder isn't remote-shaped or is a channel (`claims.remote_handle`,
  `claims._is_channel`). Only the rows left (claimed tasks, usually a handful) read their document through
  `Repository(connection).snapshot().get("task", id)`, for `claim_expires`, `claim_expires_for` and `bundle`.
- **Mixed bundles.** For a row that has a bundle, the scan lists the members with the writer's own rule,
  `workflow._bundle_members(connection, slug)` (`workflow.py:385-399`: live and unarchived, done members included).
  It reads each member's `locked_by` with one `IN (…)` query on `task_operational`. The group is **mixed** if any
  member has a non-empty holder other than the group's holder. A mixed group:
  - is skipped, and nothing is written;
  - is logged as E6 at WARNING once per `(bundle, holder)`. The keeper remembers the pair in memory and forgets it
    once the bundle is no longer mixed, so a later recurrence warns again. A restart also warns again, once;
  - is then protected only by D4 while connected. After a disconnect it expires on its stored stamp, and it gets
    no restart grace. E6 says so and names the remedy.
- A `Conflict` from a write is classified by its text.
  - `revision conflict for …` (`native/commands.py:346`) is the scan-to-write race: DEBUG (E1).
  - Any other `Conflict` means the scan's rule missed a case: WARNING (E2). It is logged once per (task, holder),
    like E6, so it can't repeat silently on every pass.
- `DISCONNECTED_TTL_SECONDS = 15 * 60` and `RENEW_BELOW_SECONDS = 60 * 60` live in `native/claims.py` next to
  `DEFAULT_TTL_SECONDS`, so the claim contract stays in one place. `KEEP_SECONDS = 60.0` (the pass cadence) and
  `SUBMIT_TIMEOUT = 30.0` live in `claim_keeper.py`.
- A renewal happens about every 3 h of connection per claim, so most claims (picked and done inside 3 h) are
  never renewed at all.
- **The agent's `ttl_seconds` is advisory on a server (Q2).** While connected, the keeper keeps at least 1 h on
  the stamp, so an agent's short TTL is raised to 4 h; on disconnect any longer TTL is cut to 15 min. On a
  server the connection, not the number, says whether the holder is still there.

**D4. A connected remote holder never expires by its stamp (backstop).** In `claims.read` (`claims.py:315-316`):

```python
held_open = live is True and remote_handle(holder) is not None
expired = (task.get("status") in TERMINAL_STATUSES or live is False
           or (deadline is not None and now >= deadline and not held_open))
```

- With the keeper healthy this changes nothing: connected stamps are always ≥ 1 h out. If the keeper falls behind
  or its thread dies, a working teammate's claim still can't read as expired, which is the unsafe direction
  this whole contract avoids (module docstring: "unproven means untouched").
- Host-shaped holders keep today's rule: a live stdio process still expires at its TTL. Solo can't reach the
  branch (`CONNECTED is None`, so no remote holder is ever live).
- The keeper still writes renewals because the stored stamp is what survives a crash (D5) and what the exported
  files and the `taskmaster-state` backup show.

**D5. Restart: one grace TTL for the claims of sessions that were alive.**

- **Heartbeat.** Each keeper pass writes `.taskmaster/local/serve/claim-keeper.json` (owner-only, via
  `principals.replace_private` and `auth.serve_directory`):
  `{"version": 1, "store_id": "<id>", "at": "<UTC stamp>", "connected": ["ann@…", …]}`. `connected` lists the
  sessions of `SessionRegistry.live()` that hold at least one claim (bounded by the claims, not the connections).
- **At start**, after `coordinator.start()` and before uvicorn serves (`ServeDaemon._start`, after
  `serve/daemon.py:378`), the daemon reads the heartbeat into `Outage(at, connected)` and runs one startup pass
  with `decide(..., outage=…)`. No MCP session is connected yet, because the registry is empty and not
  attached, so no agent can pick or renew during the pass.
- **Writers that can run during the pass (I2).**
  - The guarded viewer is already serving: `start_viewer` runs before the coordinator in `_start`.
  - Host processes can reach the coordinator's IPC port as soon as `coordinator.start()` returns.
  - So a viewer status move, or a host CLI's claim change, can land between the scan and a grace write. The
    revision pins make that safe: the grace write gets `Conflict` and the group is skipped. That is the right
    outcome, because the other writer has just decided that claim's fate.
- **Every group is isolated (I2)**, in the startup pass and the steady pass alike. `apply` runs each group in its
  own `try`:
  - a revision `Conflict` is DEBUG (E1);
  - any other exception is WARNING for that task (E2);
  - either way, the pass goes on to the next group.

  E3 is kept for a failure of the scan, or of the heartbeat read, itself. That ends only that pass.
- This is the addendum's "expiry clocks resume, and claims of disconnected sessions get one grace TTL":
  - a claim that was live or counting down at the outage gets `now + 15 min`, however long the outage lasted;
  - a claim that had lapsed before the outage stays lapsed;
  - a connected-at-crash claim with a 4 h stamp is cut to 15 min, because its session is gone (the steady-state
    rule would do the same on the first pass).
- **No usable heartbeat** (first start of a 010 build, an unreadable file, a `store_id` that isn't this store's,
  as after a 014 init) gives `Outage(at=None)`, and every non-terminal remote claim gets the grace (Q3). That is
  the safe direction: a claim stands 15 min too long rather than lapsing unseen. An `at` in the future
  (clock skew) is clamped to now.
- A startup-pass failure never fails the start. A failed group keeps its stored stamp. A failed scan (E3)
  leaves every claim as it was, and the first steady pass then applies the steady rules.

**D6. Wiring in `ServeDaemon`.**

- `_start`: build `ClaimKeeper(coordinator, sessions, root)`, run `keeper.startup()` (D5), then
  `keeper.start()` before `build_app`. Register `sessions.on_disconnect.append(lambda entry: keeper.wake())`.
- The keeper acts only while `sessions.attached()` (new: `self._manager is not None`). Before the lifespan
  attaches the manager, and after it detaches, `connected` is `False` for everyone, and acting then would shorten
  live claims.
- `request_stop` calls `keeper.request_stop()` first, before its `if server is None: return` check (M11). That
  way uvicorn's graceful shutdown, which terminates every
  transport, can't make the keeper shorten every live claim on the way down. A restart then grants D5's grace
  from the last heartbeat instead.
- `_teardown` stops and joins the keeper (bounded, 10 s) before `coordinator.stop()`, as it does the exporter.
- Each group's outcome is logged as D3a and D5 say: a revision `Conflict` at DEBUG, anything else at WARNING. A
  pass that raises outside any group is E3, and the loop continues. Nothing is logged at ERROR unless the thread
  itself would die.
- A coordinator that is `refusing` (014) or stopping makes submits fail; the keeper logs once per pass and goes
  on.

**D7. A new connection is told what it can take back (Q4).** Without this, every reconnect silently drops the
person's claims to 15 min, and the agent has no reason to re-pick them.

- `RemoteSession` gains `announced: bool = False`.
- `serve/claim_keeper.AdoptionNotice` is a FastMCP middleware, added beside 006's `SanitizeErrors`
  (`serve/daemon.py:391-392`). In `on_call_tool`, after `call_next` and only over HTTP and for a successful
  result:
  - it finds the entry by the request's `mcp-session-id` (`SessionRegistry.by_transport`, new; never `current()`,
    which binds);
  - when it is a `RemoteSession` not yet announced, it marks it announced under the registry lock;
  - in `anyio.to_thread.run_sync` (never on the loop), it lists the adoptable tasks with the existing
    `native_routing.claims._adoptable(connection, session)` on a read-only connection, with their stamps.
    `_adoptable` doesn't filter by status, so the notice drops tasks in a terminal status, and it words a claim
    whose stamp has passed as lapsed (E5, M10);
  - if there are any, it appends **one extra text block** to the result. `content[0]` is untouched, so JSON tools
    and every test that reads the first block are unaffected.
- The read endpoint (`<handle>@read`) and the viewer (`<handle>@viewer`) are channel sessions, never
  `RemoteSession`s, so they never get it.
- Residual: if the old session's transport still lingers (a crash within the 30 min idle window), its claims
  aren't adoptable yet at the first call, so no notice is shown. The E3 text covers a pick in that window.

## 3. Timeline of one claim

| Event | Stamp afterwards |
|---|---|
| ann picks at 09:00 | 13:00 (4 h) |
| keeper pass at 12:01 (59 min left) | 16:01 |
| ann's agent exits cleanly at 14:00 (`DELETE`); the sweep prunes within 2 s, the keeper wakes | 14:15 |
| 14:15 passes | expired: a teammate may release it, ann's next session may still adopt it |
| ann's agent crashes at 14:00 instead | still 16:01 while the transport lingers (connected); pruned about 14:30, then 14:45 |
| the server restarts at 10:00 after a 2 h outage, ann was connected | grace: 10:15 |
| ann's new connection's first call | E5 notice: "your earlier session held X until 10:15" |

## 4. Error and notice shapes

| # | When | Shape |
|---|---|---|
| E1 | Keeper write lost the race: a `Conflict` whose text starts `revision conflict for` (D2, D3a) | no user-visible text; DEBUG `claim keeper: task X moved since its scan; skipped` |
| E2 | Keeper write refused otherwise, per group (D5) | WARNING `claim keeper: <kind> of task X failed: <ExceptionType>` (no `str(exc)`, 006's rule); a non-revision `Conflict` is logged once per (task, holder) |
| E3 | The scan or heartbeat read failed (not a group) | WARNING `claim keeper: <startup grace / pass> could not read the claims (<ExceptionType>); claims keep their stored expiry` |
| E4 | Heartbeat unreadable or for another store | INFO `claim keeper: no usable heartbeat; every remote claim gets the restart grace` |
| E5 | D7 notice (second text block) | `Note: your earlier session \`ann@…\` holds \`X\` (until 10:15Z) and \`Y\` (lapsed at 09:40Z). An unexpired claim lapses at its time unless this session takes it over; a lapsed one can still be taken back: \`backlog_pick_task("X")\` or \`backlog_claim(action="renew", task_id="X")\`.` At most 10 tasks are named, then `and N more; backlog_claim(action="status") lists them.` Terminal tasks are never named (M10) |
| E6 | Mixed bundle skipped (D3a, I1) | WARNING once per (bundle, holder): `claim keeper: bundle B has members held by another session (\`bob@…\` on \`Y\`), so \`ann@…\`'s claims on it are not renewed or shortened, and they get no restart grace; release \`Y\`'s claim or take \`Y\` out of the bundle` |

## 5. Solo and host mode

Unchanged. No daemon means no keeper, no middleware and no heartbeat. `CONNECTED is None`, so D4's branch is
unreachable. Host-shaped holders are never touched by the keeper, so a store moved back to stdio sees ordinary
stamps.

## 6. Costs

- Writes: one `task.claim_renew` per claim per about 3 h of connection, one per disconnect, and one per restart.
  Each is a domain event, a projection job, and later a line in 013's export commit. Each also leaves a receipt
  under `claim-keeper`, and receipts are never collected (`native/receipts.py:16-17`). For five people with two
  claims each, that is about 100 rows a day.
- A renewal moves the task's revision. A teammate's `backlog_batch_preview` that touches the task between preview
  and apply then gets the usual "state changed since the lines were checked" and re-previews. That is rare, and
  it is the existing contract.
- The `changes` feed shows keeper rows (`session = claim-keeper`, actor `operator`). Hooks and mods that resurface
  "a teammate changed X" should treat that actor as housekeeping. That is a note for 007/008's readers, not a
  change here.

## 7. Reuse map

| Need | Reused |
|---|---|
| the claim write, bundle rule, `CLAIM_FIELDS` | `task.claim_renew` / `workflow._claim_change` |
| scan-to-write race | `expected_revisions` (`native/commands.py:343-346`) |
| who is connected | `SessionRegistry.connected` / `live()` (003), `claims.connected` |
| adoptable tasks | `native_routing.claims._adoptable` (003 D8) |
| owner-only atomic file | `serve.principals.replace_private`, `serve.auth.serve_directory` |
| middleware placement and HTTP-only rule | 006 `SanitizeErrors` pattern |
| internal scope | `coordinator.scopes.CLAIM_KEEPER` (004 D1) |
| stamps | `claims.stamp`, `parse_stamp`, `now_utc` |

## 8. Seams

- **004:** the scope and origin rule above, and the scope constant. Both lanes edit `serve/sessions.py` and
  `serve/daemon.py`; 004 lands first.
- **011 (per-clone machine-local fields):** unaffected. The keeper writes only claim fields.
- **013:** more export lines; the export of claim fields is unchanged.
- **014:** laptop-held host-shaped claims "expire rather than being released" (rulings O4). The keeper ignores
  host shapes, so that stays the operator's cleanup (003 R3a).
- **015:** the keeper is a new host-side writer on the exposed surface. It takes no input from the network beyond
  the session strings already in `locked_by`.
- **016:** the how-to should say that a restart grants teammates 15 min to reconnect and re-pick. It should also
  say that `--session-idle-seconds` bounds how long a crashed agent's claims stand: the idle timeout plus 15 min
  (M11, and the review's note on Q7).

## 9. Out of scope

- Releasing a revoked person's claims at once (Q6): revocation ends their sessions (002 sweep,
  `serve/app.py:99-114`), and the claims fall to 15 min like any disconnect.
- A `--disconnected-ttl` flag (Q1).
- Auto-adoption on reconnect: one person's two agents stay protected from each other (003 Q2), so a new
  connection can't know which claims are "its" own.
- Host-shaped holders and `sessions`-table liveness.
- Receipt GC.

## 10. Open questions (defaults are the recommendations; none blocks the build)

- **Q1. Disconnected TTL.** 15 min, a constant (the addendum's proposal), not a flag. Revisit if the team finds it
  short over lunch breaks: the SDK's 30 min idle timeout plus 15 min means claims lapse about 45 min after an
  agent's last request.
- **Q2. The agent's `ttl_seconds` on a server is advisory** (D3): raised while connected, cut at disconnect.
  Alternative: honour short TTLs while connected. That would need the TTL stored per claim, a new claim field.
- **Q3. No heartbeat means grace for every remote claim** (D5), the safe direction. Alternative: `at = now`, which
  would let claims that lapsed during an unrecorded outage stay lapsed.
- **Q4. Include the adoption notice (D7, Step 6).** Recommended: without it, reconnects quietly cost claims. If
  declined, drop Step 6; nothing else depends on it.
- **Q5. Keeper writes are host work** (`LOCAL`, `claim-keeper`, actor `operator`), not attributed to the person.
  This is the 004 seam. Attributing them would need the person's key, which fails after revocation.
- **Q6. Revocation is a disconnect** (15 min), not an immediate release. The operator can still force-pick and
  release.
- **Q7. D4 backstop** (a connected remote holder never expires by its stamp). It changes the shared claim
  contract for remote holders only. Recommended because it makes a keeper failure safe; the reviewer should weigh
  it.

## 11. Implementation plan (TDD)

Rules for every step:
- Tests run with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest` from the worktree
  `.worktrees\team-server-004` (the same lane, after 004), in the foreground, `-n 2` at most,
  `--basetemp D:\scratchbook\taskmaster\ts004\pt010-<step>`. Never the full suite.
- Each step is one commit with its tests, red first: `feat(team-server-010): …`.
- Time is injected (`now=` on `decide`, the pass and the startup pass; `claims.read` already takes `now=`). No test
  sleeps for a TTL.
- Coordinator-level fixture: `tests/test_serve_session_guard.py`'s `world` (team mode off). Daemon-level fixture:
  `test_serve_principals_e2e.launch`, with `claim_keeper.KEEP_SECONDS` monkeypatched to 0.2 and
  `serve.app.REVOCATION_SWEEP_SECONDS` as it is (2 s).
- Claims are seeded the way `tests/test_serve_session_guard.py` does: `task.pick` envelopes with remote-shaped
  sessions under the matching origin, and `claims.CONNECTED` monkeypatched to a set lookup.

**Step 0. Baseline.** Run §12 and record the counts in the lane notes. 004's Step 0 counts may be reused if no
commit landed in between.

**Step 1. Constants and the D4 backstop.** Files: `native/claims.py` (constants, `read`, one docstring paragraph
on connection-bound claims). Tests first in `tests/test_claim_ownership.py`:
1. remote holder, connected, stamp in the past: `expired is False`, `live is True`;
2. remote holder, disconnected, stamp in the past: `expired is True`;
3. host-shaped holder proven live, stamp in the past: `expired is True` (unchanged). `read` takes no `pid_alive`,
   so monkeypatch `claims._local` to return a stub for `_local_host` and `_local_pid_alive` (M12);
4. `DISCONNECTED_TTL_SECONDS == 900`, `RENEW_BELOW_SECONDS == 3600`, both inside `MIN_TTL_SECONDS..MAX_TTL_SECONDS`.
Proving check: `tests/test_claim_ownership.py`, `tests/test_claim_force_rule.py`, `tests/test_native_claims.py`,
`tests/test_native_claim_review_d.py`, `tests/test_native_claim_terminal_and_readers.py`,
`tests/test_native_routing_claims.py`, `tests/test_session_scope.py`. If a 003 test expected a connected holder
to expire by TTL, report it rather than editing it: that is Q7's trade.

**Step 2. Pure rules and the heartbeat.** Files: `serve/claim_keeper.py` (`Row`, `Action`, `Outage`, `decide`,
`group`, `read_heartbeat`, `write_heartbeat`). Tests first in `tests/test_claim_keeper.py`:
1. every row of D3's table, with `now` fixed, on single-member groups:
   - connected: 59 min left → renew; 61 min → none; no stamp → renew;
   - disconnected: 4 h left → shorten; 10 min → none; expired → none;
   - host-shaped → none; `ann@viewer` → none; status `done` → none;
   - two-member groups (M8): disconnected with stamps of 4 h and 5 min → none (never extends); connected with
     stamps of 4 h and 30 min → renew; startup with one member lapsed before `outage.at` → none;
2. startup: in `outage.connected` with a stamp before `outage.at` → grace; stamp after `outage.at` → grace; stamp
   before `outage.at` and not listed → none; `Outage(at=None)` → grace for every non-terminal remote row;
3. `group` puts two members of one bundle with one holder into one `Action` with both revisions, and keeps two
   holders apart;
4. heartbeat round trip; owner-only on POSIX (skip on Windows, as 013's tests do); another `store_id`, bad JSON,
   a missing file or a wrong version → `None`; a future `at` is clamped to `now`.
Proving check: `tests/test_claim_keeper.py`.

**Step 3. The pass against a real coordinator.** Files: `serve/claim_keeper.py` (`scan(connection)` with D3a's query
and mixed-bundle detection, `ClaimKeeper.run_pass(now=)`, `apply` isolated per group). Tests first,
`tests/test_claim_keeper.py` with `world`:
1. ann's connected claim with 30 min left is renewed to `now + 4 h`. The event's `session` is `claim-keeper`,
   its actor handle is `operator`, and `locked_by` is unchanged;
2. ann's disconnected claim with 3 h left is shortened to `now + 15 min`; the next pass changes nothing (no
   churn);
3. a bundle of two members is written by one command (count receipts under `claim-keeper`);
4. race: ann adopts the task from a second session between `scan` and `apply` (monkeypatch `apply` so the adoption
   runs first). The keeper's write raises a `revision conflict` `Conflict` and is skipped at DEBUG, with no
   WARNING. The holder is the new session;
5. mixed bundle (I1): bundle B has `X` held by `ann@a…` (disconnected, 3 h left) and `Y` held by `bob@b…`. Build it
   by picking both, then `task.patch` `bundle` onto each as the host. The pass writes nothing (receipt count under
   `claim-keeper` is unchanged), and logs exactly one E6 WARNING naming B, `ann@a…` and `Y`. A second pass logs
   nothing new. After `Y` is released, the next pass shortens `X`, and a new mix warns again;
6. mixed by the same person (M9): `Y` held by ann's other disconnected session `ann@f…` is also skipped with E6,
   and `Y`'s holder is unchanged (no silent adoption);
7. isolation: two groups, where the first group's submit is monkeypatched to raise `RuntimeError`. The second is
   still shortened, and one E2 WARNING names the first task;
8. revoke ann's key, then run the pass: her disconnected claim is still shortened (`LOCAL` works after
   revocation);
9. a member envelope with `caller_scope = "claim-keeper"` is still refused (004's rule, the seam pinned from this
   side);
10. after `world.owner.refuse_writes("…")` (M12), each group fails with E2 and the pass returns normally;
11. the scan touches no unclaimed task: on a store with 200 unclaimed tasks and one claim, spy `snapshot.get` and
    see one call (M13).
Proving check: `tests/test_claim_keeper.py`, `tests/test_serve_session_guard.py`, `tests/test_scopes.py`.

**Step 4. The restart grace.** Files: `serve/claim_keeper.py` (`ClaimKeeper.startup(now=)`). Seed the stamps by
picking at real time, then pass an injected `now` and `outage.at` hours later to `startup`, so no stamp is
hand-written (M12). Tests first:
1. claims for `ann@a…` (listed in the heartbeat, stamp before `at`), `bob@b…` (stamp after `at`) and `cat@c…` (stamp
   before `at`, not listed). After `startup`, ann and bob are at `now + 15 min`, and cat is untouched;
2. no heartbeat: all three get the grace. A heartbeat for another `store_id` behaves the same;
3. a host-shaped claim and a `done` task are untouched;
4. isolation (I2): three groups, where the first group's submit is monkeypatched to raise `RuntimeError` and the
   second to raise a `revision conflict` `Conflict`. The third still gets `now + 15 min`. There is one E2 WARNING
   (the first) and one DEBUG (the second), and `startup` returns normally;
5. a concurrent writer (I2): between the scan and the grace writes (monkeypatch `apply`'s first submit to run a
   host `task.claim_release` on bob's task first), bob's group is skipped as a revision conflict, bob's task stays
   released (the grace doesn't bring it back), and ann still gets her grace;
6. the scan itself raising (monkeypatch `scan`) logs E3, returns normally, and changes no claim.
Proving check: `tests/test_claim_keeper.py`.

**Step 5. Daemon wiring, over real HTTP.** Files: `serve/daemon.py` (`_start`, `request_stop`, `_teardown`),
`serve/sessions.py` (`attached()`, `by_transport()`). Tests first in `tests/test_serve_claim_keeper.py` with
`launch`:
1. ann connects and picks; `claim_expires` stays at the pick's stamp while connected;
2. ann closes the connection: within 5 s (sweep 2 s, pass 0.2 s) `claim_expires` is within 5 s of `now + 900`;
3. with `RENEW_BELOW_SECONDS` monkeypatched above the TTL, a connected claim is renewed while the connection
   stays open;
4. `request_stop` with ann still connected: after the thread joins, ann's stamp is the pick's (no shortening on
   the way down). The heartbeat file exists and lists ann's session;
5. restart: a second `launch(users=())` on the same root (after the first stops; `users=()` because the keys
   already exist, M12) grants ann's claim `now + 15 min` before the first request is served (read the store right
   after `ready`);
6. `sessions.attached()` is False before the lifespan and after it ends; a pass in either state writes nothing
   (direct call on a detached registry).
Proving check: `tests/test_serve_claim_keeper.py`, `tests/test_serve_sessions.py`, `tests/test_serve_sessions_e2e.py`,
`tests/test_serve_daemon.py`, `tests/test_serve_principals_e2e.py`.

**Step 6. The adoption notice (D7; drop this step if Q4 is declined).** Files: `serve/claim_keeper.py`
(`AdoptionNotice`), `serve/sessions.py` (`RemoteSession.announced`), `serve/daemon.py` (add and remove the
middleware beside `SanitizeErrors`). Tests first in `tests/test_serve_claim_keeper.py`:
1. ann picks X on connection A and closes it. On connection B, the first tool call's result has `content[0]`
   exactly as without the middleware, and a second text block naming `X`, its stamp and both remedies;
2. B's second call carries no notice;
3. a connection with nothing adoptable gets no notice;
4. `/read/v1/tools` and a viewer request never carry it (no `RemoteSession`);
5. the middleware does no DB read on the event loop. Spy that `_adoptable` runs off the loop thread
   (`threading.current_thread() is not` the loop thread).
Use the `Connection` helper from `serve_helpers`, extended with a `call_all` that returns every text block. Don't
change `.call`.
Proving check: `tests/test_serve_claim_keeper.py`, `tests/test_serve_sanitize.py`, `tests/test_serve_read_api.py`,
`tests/test_serve_viewer.py`.

**Step 7. Docs, regression and fresh-eyes check.**
- One paragraph in the `native/claims.py` docstring (connection-bound claims, D3/D4). No tool docstring changes:
  `test_server_instructions.py` snapshots stay as they are.
- A line in the lane notes for 016's how-to: a restart grants 15 min to reconnect and re-pick.
- Run §12 and record the counts against Step 0.
- Then one fresh reviewer checks the code against this design before the review gate. 004 and 010 can share one
  review gate pass if the lead prefers. The reviewer is Opus, because 004's half is security-sensitive.

## 12. Regression set

Foreground, `-n 2`, in slices: before Step 1 and after Step 6.
- `tests/test_serve_*.py` (two or three slices);
- `tests/test_claim_*.py`, `tests/test_native_claim*.py`, `tests/test_native_routing_claims.py`,
  `tests/test_session_scope.py`;
- `tests/test_claim_keeper.py`, `tests/test_scopes.py`, `tests/test_native_service*.py`,
  `tests/test_viewer_serve_hooks.py`.

The full suite runs once on `feat/team-collab` before release (rulings), not in this lane.
