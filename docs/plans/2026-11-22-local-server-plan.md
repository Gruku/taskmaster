<!-- User intent: the build plan for local server mode (local-server-001 spec), split into lanes that can run in parallel against one fixed contract. -->
# Local server mode: implementation plan

Spec: docs/specs/2026-11-22-local-server-design.md (rev 3, spec review PASS). Rulings: docs/handoffs/2026-11-22-local-server-lanes/rulings.md. Base branch: feat/local-server (taskmaster repo, private). Each lane works in its own worktree under D:\scratchbook\taskmaster\wt-<lane>, branch off feat/local-server, and ends with a resume file in docs/handoffs/2026-11-22-local-server-lanes/<lane>.md. The orchestrator merges lanes one at a time into feat/local-server after each lane's review gate.

## 0. The contract (fixed here so lanes L1 and L2 can build in parallel)

Rev 2 after plan review 1 (WARN, 6 Important; D:\scratchbook\taskmaster\local-server\plan-review-001.md).

- **Shared module first (step 0, before any lane forks):** `taskmaster/localserve.py`, landed on feat/local-server by the orchestrator's prep lane. It holds what both sides need and imports only the standard library and existing stdlib-only taskmaster modules (`coordinator/protocol.py` and `server_fence.py` import no third-party module, measured): discovery path / read / atomic owner-only write (reusing `verify_private`), the result-file helpers, header names, status values, the host-lease probe, the marker/latch check, the legacy-store check, and thin re-exports of `package_digest`, `declared_version`, `compare_versions`, `same_build`. Nothing moves; the test pins **third-party** imports (no fastmcp, pydantic, starlette, uvicorn, yaml) for localserve and the bridge. The bridge requires Python ≥ 3.11 (the package's floor); a test runs the bridge's import set under that floor.
- **CLI:** `taskmaster_cli.py serve --local --root <root> --result-file <path> [--idle-exit 600] [--plugin-dir <dir>]`. In the local profile the viewer is the guarded one on the hashed band port (L3 landed it; no 7471 bind) and add-user stays refused.
- **Launch result channel:** stdout is DEVNULL after the double spawn, so the server writes its outcome to `--result-file` (owner-only, atomic): `{status, reason, pid, url?}` with status one of `ready`, `lease_held` (another server won: re-read discovery), `store_owned_busy` (a busy session coordinator; hand-over retried for up to 5 s first), `refused` (marker/latch, legacy store, non-loopback, config), `error`. Exit codes stay informational; the bridge reads only the result file (wait ≤ 15 s, then treat as `error`). Every outcome other than `ready`/`lease_held` → fallback for this session, with the reason logged once; `lease_held` re-reads discovery at most 3 times, then fallback (no relaunch loop).
- **Discovery:** `<root>/.taskmaster/local/serve/local.json`, owner-only, atomic: `{format:1, profile:"local", state:"serving"|"draining", url, pid, root, plugin_dir, token_file, build:{version,digest}, started, nonce}`; removed on clean exit; stale when the host lease is free. A bridge that finds `draining` waits for the lease to free, then launches.
- **Endpoints (operator token, loopback, local profile only):** `GET /local/v1/status` → `{format:1, state, build, plugin_dir, root, bridges, inflight, idle_seconds}`; `POST /local/v1/retire` body `{only_if_no_other_bridges: bool, requester: <discriminator or null>}` → 202 (state → draining; drain closes the GET streams so uvicorn's graceful shutdown doesn't wait on them; exit after inflight calls) or 409 `{reason}`.
- **Headers (bridge → serve):** `Authorization: Bearer <operator token>`, `X-Taskmaster-Session: <discriminator>`, `X-Taskmaster-Session-Nonce: <per-process hex>`, `X-Taskmaster-Cwd: <percent-encoded UTF-8 path>`. Ignored outside the local profile.
- **Session identity (review I3):** the server binds the session on the transport's **first request** (initialize), not lazily on the first tool call. The 16 hex = HMAC of the discriminator under a per-store key persisted owner-only at `.taskmaster/local/serve/session.key` (created once), so the same discriminator maps to the same `operator@<hex>` across restarts.
- **Claims in the local profile:** `adoptable` is **false across sessions** (no same-handle adoption at all). Another session's claim becomes free only when the claim keeper releases it after its session has had no live bridge stream for the grace, or by `force` - never by a second session adopting it while the first is reconnecting.
- **Bridge liveness:** the GET stream, held open for the bridge's life; a bridge hard-killed without DELETE is dead when its stream drops.
- **Viewer bind (L3):** non-fatal: the local server walks the 6800-6899 band with the identity check (a fallback session's solo viewer may hold the hashed port) and runs without a viewer if none is free, logging it.

## Lanes

### L1 - serve --local (task local-server-002), Sonnet, serve/ + native/claims
1. Profile + refusals: ServeConfig.profile, `--local` flag; refuse marker/latch, non-loopback, legacy store (exit 2); port 0; export off; rate limits keyed per discriminator.
2. Coordinator: constructor switch to LocalCloneSource + base IdentityGate (daemon.py:86-89); skip upgrade_served_schema; idle session coordinator → hand_over (daemon.py:490-491); busy → hand-over retried up to 5 s, then result-file status `store_owned_busy` (§0). Every launch outcome goes to `--result-file` per §0; exit codes are informational.
3. Middlewares off in local: FrameUntrusted, SERVER_NOTE, AdoptionNotice, MemberQueryScope, HeavyCalls (daemon.py:438-447).
4. Discovery file + status/retire endpoints + drain-then-exit; idle exit 600 s after the last bridge stream closes; session idle timeout off.
5. Sessions: `bind()` derives the 16 hex from a keyed hash of the discriminator; same discriminator + other nonce with a live stream → fresh session; `_alive` by open GET stream; per-session state keyed by discriminator; sessions heartbeat one row per bridge.
6. Claims in the local profile per §0: `adoptable` is false across sessions (claims.py:207-217, native_routing/tasks.py:518); the claim keeper frees another session's claim only after that session has had no live bridge stream for the grace (claim_keeper.py); `force` as today.
7. cwd inventory: list ROOT/cwd-relative behaviours (backlog_server.py:236, 3938, 11122 and any others), carry X-Taskmaster-Cwd where needed.
Tests (each must fail on the base where it tests new behaviour): unit per item; integration with an HTTP client playing two bridges: claims refused across sessions; still refused after 40 simulated quiet minutes (clock injected); a hard-killed bridge (no DELETE) frees its claim only after the grace; a server restart within the grace keeps A's claim and B is refused while A reconnects; status/retire incl. draining; idle exit requires no bridge stream, no inflight and no coordinator client, and fires at once when the store root disappears; hand-over of an idle session coordinator and the busy case's result file; refusals incl. "never writes a marker or latch" (guard test); one heartbeat row per bridge; stdio parity for session-scoped outputs (session log, SESSION_ID in records, session-start resurfacing) compared against a stdio run. Targeted runs; the full serve tier only when no other lane runs heavy tests. L1 forks after lane-hardening (033 touches claims.py/daemon.py) merges.

### L2 - bridge + launcher (task local-server-003), Sonnet, plugin root + .mcp.json
1. `tm_bridge.py` (stdlib only; imports taskmaster.localserve and taskmaster.root only): root resolution, opt-outs (env TASKMASTER_LOCAL_SERVER=0, project setting `local_server: off`, no backlog, legacy store, marker/latch) → fallback.
2. Discovery + status probe + skew matrix (spec §4 step 3) + retire.
3. Launch: interpreter of the server env resolved once via uv and cached (cache keyed by plugin dir + dependency hash, per step 7), double spawn (bridge → launcher → server), CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP + breakaway attempt, CLAUDE_* scrubbed, LEFTOVERS_KEEP/NAME, read the outcome from `--result-file` (≤ 15 s): `ready` → connect; `lease_held` → re-read discovery, at most 3 times; anything else or timeout → fallback with the reason (§0).
4. Relay: POST /mcp (JSON + SSE), 202, Mcp-Session-Id, GET stream held open and forwarded, cancellation forwarded, per-method timeouts (none for tools/call).
5. Reconnect: eager, replay initialize + initialized, tools/list_changed on build change; retry only provably-unsent requests; other tools/call failures → "may have committed" error worded for the mods' transient matcher.
6. Fallback relay: spawn `uv run backlog_server.py` and pipe both ways; log the reason once to stderr.
7. **Spike first, decision recorded in the resume file:** the `.mcp.json` command that works on every OS and finds a Python ≥ 3.11 without staying resident in a way that breaks the 25 MB budget or shows a console (candidates: `uv run --no-project --python 3.11 --script` measured at ~48 MB = over budget; Windows `py -3` launcher, which stays resident; an MSYS `bash` wrapper; a cached interpreter path written by the first fallback run; `uv python find` once then a direct path). Measure each one's resident cost and console behaviour; pick; no Python → run the fallback directly. Interpreter cache keyed by plugin dir + dependency hash, re-resolved when the uv env is pruned.
8. **Gate (on L1+L2 merged), two parts.** Scripted, by the lane: two concurrent `env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir <worktree>` runs on a scratch project copy (never `claude -p` without unsetting the key: it bills the API) that call a tm tool and wait; check one serve process, both bridges, CLAUDE_CODE_SESSION_ID reaching the bridge (its log), the first run's exit leaving the server alive, killing the remaining claude process tree leaving the server alive and idling out after --idle-exit (shortened for the test), cold start under Claude Code's MCP startup timeout, RAM per session vs the 25 MB target; **no console window** checked by a probe that enumerates visible top-level windows (EnumWindows) and conhost children for every PID in the bridge/launcher/server trees during the whole run. User steps (a short checklist the orchestrator gives the user): two interactive sessions, `/mcp` reconnect, the mod band in each. Fail branch: if the server can't outlive its launching session or any window appears, local mode ships default-off on that platform (env/setting opt-in) and the finding goes to the user. Until L1 lands, develop against plain `serve` on a **scratch store copy only** (plain serve upgrades schema 3→5 and starts the 900 s export).
Tests: bridge unit tests with a fake HTTP server (framing, SSE, 202, retry rule, reconnect, skew matrix, opt-outs, draining, the result-file outcomes and the 3-try lease_held bound); "may have committed" end to end (a fake server that drops the connection after receiving a write: the client gets the error and the write ran once); the real fallback relay (spawn the stdio server, call a tool through it); the double spawn and CLAUDE_* scrub (inspect the server's parent and environment); the import-set pin; a two-bridge integration test against real serve --local once L1 is merged; the Linux lane (serve + bridge tests) on the merged branch.

### L3 - hooks, CLI, viewer (task local-server-004), Sonnet, after L1 merges
- Each hook against a running local server (session-start, merge_gate, merge_recorder, edit_resurface, merge_approve, worktree_submodule_init): reads see server writes, coordinator writes land, nothing launches a second coordinator; merge_recorder and coordinator-writing hooks surface refused/other-build writes.
- Old-build stdio server/CLI against a newer server: plain "taskmaster was upgraded: restart this session".
- Guarded viewer in the local profile on the hashed port; `backlog_open_viewer` mints a one-time short-lived login link (loopback, operator) and opens it.
- The separate coordinator service is not started while a local server runs.

### L4 - solo viewer CSRF (task local-server-006), Sonnet, now (independent)
Origin/Host check and JSON Content-Type on every solo-viewer write, drop `Access-Control-Allow-Origin: *`, keep the viewer's own pages working; tests for cross-origin refusal. Opus review (security).

### L5 - mods (task team-server-018), Sonnet, after L2 merges
- Tests that every mod call path (tm.ts readMany, actions.ts, handover-guard.ts, restore) works through the bridge; check the 3 s bound under 8 bridges.
- Desktop app: what it does with plugin MCP servers and `$.process.run` (console flash); fix the tm_read.py lookup for the desktop upload copy.
- Team clones: route mod writes to the team server (design decision in 018's design review: bridge forwarding vs tm_write.py).
- Version bumps: taskmaster-mods plugin.json + marketplace + rr-tui dep range.

### Team-server leftovers (separate lanes, already running or queued)
- lane-b111: B-111 + 029 design (running). 029 implementation after its design review.
- lane-hardening: 021, 019, 022, 023, 033 (done, in review).
- 034 shared query snapshot: Opus design + security review, then Sonnet build; independent of L1-L5 (serve/query_worker.py).

## Risks and rollback

- Rollback per user: `TASKMASTER_LOCAL_SERVER=0` or the project setting → today's stdio path, unchanged code. Per release: revert the .mcp.json change (the bridge is the only entry point); no data migration happens (no schema upgrade, no marker), so nothing needs undoing in stores.
- Risks: the server dying with its launching session (live gate, fail branch above); claim continuity (L1 tests); a resident wrapper eating the RAM win (L2 spike); version skew on upgrade day (plain restart message); the 13.5k-line backlog_server.py edited by L1 and L4 (different regions; L4 merges first).

## Acceptance probe (owner: orchestrator, run by a Sonnet probe lane after L3 merges)

8 bridges + the mods' read batch on a large synthetic native store (~20-40 MB, generated under D:\scratchbook\taskmaster; never a copy of a work project's store such as CodeMaestro's). Pass: total RAM ≤ 25% of 8 stdio servers measured the same way; the mods' read calls p95 under 3 s with all 8 bridges issuing calls; zero visible windows (EnumWindows probe); server gone within idle-exit + 60 s after the last bridge exits; numbers recorded in docs/handoffs/2026-11-22-local-server-lanes/acceptance.md.

## Order and concurrency

Step 0: localserve.py on feat/local-server; lane-hardening and L4 merge first. Then L1 and L2 in parallel. Only one lane runs broad test tiers at a time; the others run targeted files. Then L3 and L5 after their dependencies merge, the L2 live gate on L1+L2, the 8-bridge acceptance probe, then release (local-server-005): version bump (repo plugin.json/pyproject still say 7.4.0 while 7.5.0 shipped: fix), CHANGELOG, docs (native-store.md, a local-server section in README), squash to master without the lanes folder, user go-ahead for every push.

## Review gates

One fresh-context reviewer per lane (Opus for L1, L2, L4 and 034 because they touch claims, auth, process spawning or a security fix; Sonnet acceptable for L3 and L5 unless they grow). Only Critical/Important block; Minors batched once per branch. Two fix rounds, then escalate to the user.
