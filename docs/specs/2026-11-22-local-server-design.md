<!-- User intent: the design for taskmaster's local server mode - one shared server per project replacing per-session stdio servers - checked against the user's rulings. -->
# Local server mode: one taskmaster server per project

Task local-server-001 (epic `local-server`, claude-tools backlog). Status: rev 2 after spec review 1 (FAIL: C1, C2, I1-I5; review at D:\scratchbook\taskmaster\local-server\spec-review-001.md), 2026-11-22.
Inputs: IDEA-033 spike (2026-11-22, CodeMaestro store copy), research reports in D:\scratchbook\taskmaster\local-server\explore-{serve,client,mods}.md, bridge spike results (D:\scratchbook\taskmaster\spike-bridge\results.md, 2026-11-22), rulings in docs/handoffs/2026-11-22-local-server-lanes/rulings.md.

## User rulings (requirements; every review checks the result against these)

- **R1** Local mode becomes a locally run taskmaster server, one per project, shared by every Claude Code session on that project; it replaces per-session stdio as the default local mode.
- **R2** The point is RAM: one server for all sessions instead of a process per session.
- **R3** Seamless: no manual start step; opening a session on the project just works.
- **R4** No separate terminal or console window, ever.
- **R5** The taskmaster Claude mod (taskmaster-mods) works with it.
- **R6** The release also carries the team-server leftovers B-111, 029, 033, 034, 021, 019, 022, 023 and 018.
- Standing: server-first (local is a deployment of the server, not a separate code path); no push or master merge without the user's go-ahead.

## 1. Shape

```
Claude session 1 ──stdio── tm bridge (stdlib, ~10-25 MB) ─┐
Claude session 2 ──stdio── tm bridge ─────────────────────┼─HTTP 127.0.0.1──▶ taskmaster serve --local (one per store root)
taskmaster-mods  ──$.mcp.call('plugin:taskmaster:tm')─────┘   (MCP + viewer + coordinator in-process)
hooks ──read-only SQLite── store.db        CLI / old stdio servers ──coordinator LOCAL protocol──▶ same process
```

- The plugin's `tm` entry in `.mcp.json` stops being the full server and becomes a **bridge**: a standard-library-only stdio⇄streamable-HTTP relay. Claude Code still sees a stdio server named `tm`, so tool names (`mcp__plugin_taskmaster_tm__*`), skills and CLAUDE.md references are unchanged, and there is no static-URL problem (a plugin-wide .mcp.json cannot know a per-project port, and an http entry cannot fall back when the server is down).
- The bridge finds the project's local server through a discovery file, starts it hidden if absent, and relays. When the server cannot be had, it falls back to running today's stdio server as a child and relaying to that (behaviour identical to 7.5.0).
- **Mods ride the bridge for free**: they call `$.mcp.call('plugin:taskmaster:tm', ...)`, i.e. the session's bridge, so their reads and writes reach the shared server with no mod change (R5). 018 then only has to cover the team-server case and the desktop app (§7).
- The server is the existing `serve` daemon under a **local profile**, not a new server (server-first rule; reuse before writing).

## 2. serve --local profile (task local-server-002)

A `--local` flag (ServeConfig.profile = "local") changes only these, and refuses to start where they cannot hold:

| Area | Team profile (today) | Local profile |
|---|---|---|
| Bind | 127.0.0.1:7470 fixed, or --insecure-bind | 127.0.0.1, port 0 (OS-chosen); never non-loopback (no override) |
| Marker / latch | required for clients to route | refuses to start if `.taskmaster-server.json` or the latch exists (that clone belongs to a team server); never writes either; guard test |
| Schema | `upgrade_served_schema` 3 → 5, one-way | not called; the store stays at the schema a stdio server would use, so older plugin caches and a stdio fallback keep working |
| Actor source / identity gate | ServeActorSource: writes attributed to the key's handle | the local clone actor source (same author fields as stdio today), so history reads the same as before; principal is the local operator. No schema-5 column is needed for sessions, claims or receipts (review answer) |
| Untrusted framing, SERVER_NOTE, AdoptionNotice, MemberQueryScope, HeavyCalls | on | off: one person's own store. (If a later version serves several local people from one store, that is team mode.) |
| State export | 900 s to `taskmaster-state` in a Git checkout | off (export_interval 0); the store's file exports stay as today |
| Rate limits | 240/min per key, burst 120, inflight 32 | keyed per session discriminator (§3) instead of per principal (daemon.py:393-394); sized for ~10 sessions + mods |
| Viewer | separate port 7471 or off | serve's **guarded** viewer (CookieLogin, daemon.py:396-407) on the project's hashed 6800-6899 port, one per project, replacing the per-session viewer thread. No prompt: `backlog_open_viewer` mints a one-time, short-lived login link (loopback, operator) and opens it. The solo viewer (unauthenticated POST, `Access-Control-Allow-Origin: *`, backlog_server.py:11819/12274) is not moved into the long-lived server; it remains only on the stdio fallback path and gets its own CSRF fix (local-server-006). Security-reviewed with 034. |
| Coordinator | ServeCoordinator in-process with ServeActorSource + ServeIdentityGate (daemon.py:86-89) | ServeCoordinator in-process with the local clone actor source (`LocalCloneSource`, coordinator/actor_resolver.py:22-29) and the base IdentityGate: a constructor switch. An idle session coordinator holding owner.lock is handed over (`upgrade.hand_over(..., lease_is_ours=True)`, daemon.py:490-491); a busy one makes the launch exit with its own code (§4). Stdio servers, CLI and hooks reach the in-process coordinator via coordinator discovery as today |
| Idle / session timeout | daemon never exits; transports time out after 1800 s idle | MCP session idle timeout **off**: a bridge's transport lives as long as the bridge (its open GET stream is the liveness signal, §4). The server exits `--idle-exit` (default 600 s) after the **last bridge disconnects**, with no inflight call and no coordinator client; immediately if the store root disappears |
| Auth | operator token host-local + per-person keys | operator token only (owner-only file), loopback only; no add-user |

Exit codes: 69 = host lease held (another server won the race: re-read discovery); 68 = store owned by a busy session coordinator (fall back, say so); 2 = refused (marker present, non-loopback, legacy store).

Serve chdirs to the store root (daemon.py:193-194) while stdio used the session cwd (backlog_server.py:236): 002 inventories every cwd/ROOT-relative behaviour (e.g. backlog_server.py:3938, 11122) and carries the session cwd in an `X-Taskmaster-Cwd` header where one depends on it.

Discovery record, written atomically at ready and removed on clean exit: `<root>/.taskmaster/local/serve/local.json` (owner-only, checked with the coordinator's `verify_private`): `{format:1, url, pid, root, token_file, build:{version,digest}, started, nonce}`. Liveness is the host lease (`serve/host.lock` kernel lock), not the pid; a record whose lease is free is stale.

A local status route `GET /local/v1/status` (operator token, loopback) answers build, root, session count, idle seconds; `POST /local/v1/retire` asks the server to exit after inflight calls finish (§4).

## 3. Sessions, claims and identity

The stdio contract to keep: a claim is protected while its holder's process lives, and becomes free when that process is gone (holder `host-pid-nonce`, native/claims.py:256-270). Every local session sharing the handle `operator` would break it, because `claims.adoptable` (claims.py:207-217) lets any same-handle session take over a claim whose holder's connection is gone (review C2). So in the local profile:

- **One session per bridge, identified by a discriminator.** The bridge sends `X-Taskmaster-Session`: the Claude Code session id when its environment has one (`CLAUDE_CODE_SESSION_ID`; verified live in 003), else a random id fixed for the bridge's life, plus a per-process nonce. serve reads it once per transport where it already reads `X-Taskmaster-Clone` (`sessions._header_clone`). `sessions.bind()` keeps the existing `<handle>@<16 hex>` shape (asserted in serve/sessions.py and relied on by `check_scope`, daemon.py:94-112, and the claim rules) and derives the 16 hex from the discriminator (a keyed hash of it) instead of `token_hex(8)` (sessions.py:133), so a reconnecting bridge gets the same `operator@<hex>` id. A discriminator that already has a live transport with a different nonce (two processes with one id, e.g. `claude --resume` twice) gets a fresh session, never a merge.
- **Claims are per session, not per handle.** The claim holder stays the session id (`operator@<hex>`). In the local profile, adoption and liveness are judged by **session**, not handle: `claims.adoptable` (claims.py:207-217, native_routing/tasks.py:518) and the claim keeper treat another session's claim as protected while that session is alive, even though the handle matches. A claim is free when its session has had no live bridge for the claim keeper's grace (the server-side equivalent of "the process is gone"); otherwise taking it needs `force` as today.
- **Liveness is the bridge, not traffic.** Each bridge keeps the streamable-HTTP GET stream open (it also receives server notifications, §4). In the local profile `SessionRegistry._alive` judges a session by that open stream, not by the transport object's existence (with the idle timeout off, a killed bridge's transport would otherwise look alive forever). The session idle timeout is off in the local profile, so quiet Claude sessions are never cut. A bridge that loses upstream reconnects **eagerly** (immediately, with backoff), not on its next call.
- **Per-session state.** In-memory session state (`RemoteSession.task`, `.bundle`, sessions.py:47-58; backlog_server.py:9142-9187) is keyed by discriminator, so a reconnect within one server's life keeps it. A server restart loses it; the bridge's replay re-sends nothing for it, so after a restart a session's bound task is re-derived the way a fresh stdio session derives it (documented; acceptance checks session-start resurfacing still works). The `sessions` heartbeat table gets one row per bridge session, not one per server pid, so store_status and resurfacing see real sessions.
- Session-scoped features the stdio server has today (session log, SESSION_ID in records, session-start resurfacing) must behave the same per Claude session; 002's review compares each against stdio output.

## 4. Bridge and lifecycle (task local-server-003)

`tm_bridge.py` at the plugin root, standard library only (it may import `taskmaster.root` and other stdlib-only modules; a test pins its import set: no fastmcp, pydantic, starlette, yaml).

1. Resolve the store root with `taskmaster.root` (the same rule as every entry point: TASKMASTER_ROOT → git common dir → nearest backlog → cwd). Worktrees of one repo share one server because they share one store.
2. No backlog at the root, `TASKMASTER_LOCAL_SERVER=0`, or the root is a team-server clone (marker/latch present) → **fallback** (below). (Team clones: 018 may later forward to the team server; until then fallback = today.)
3. Read `local.json`; probe `/local/v1/status` with the token, then apply the **skew matrix**. The bridge compares SemVer and the code digest: `package_digest` is pure hashlib/pathlib, so it moves to a stdlib-only module that coordinator/protocol.py and the bridge both import (one implementation).

   | Server vs this bridge | Action |
   |---|---|
   | same digest | use it |
   | same version, other digest (dev checkout vs cache, or an in-place update) | retire only when no other bridge is connected, else use it and say once that this session's hooks/CLI from the other install will be refused until the server restarts |
   | server older SemVer | ask `retire`; the server drains inflight calls and exits; launch from this plugin dir. Other bridges reconnect (step 6). A bridge that finds the server draining **without having asked for the retire** does not launch from its own plugin dir while a successor may be coming: it waits (bounded, 30 s) for a successor to appear in discovery and applies this matrix to it; only after that wait does it launch. If the retiring bridge finds an older build serving after its retire, it retires again (bounded) and logs. |
   | server newer SemVer (this is an old session) | use it as is (MCP is the contract) |

   Old-plugin stdio servers, hooks and CLI from before an upgrade are refused by the newer in-process coordinator (coordinator/client.py:318-341), as with coordinator upgrades today. They must say so plainly ("taskmaster was upgraded: restart this session"), and hooks that write through the coordinator (merge_recorder) must surface a refused write instead of failing open silently (004).
4. Absent or stale → launch `taskmaster_cli.py serve --local --root <root>` from the bridge's own plugin directory. The server's environment's python is launched **directly** (resolved once through uv and cached), not under a resident `uv run` (which would keep ~40 MB of uv.exe alive as its parent). Launch is a **double spawn**: bridge → short-lived launcher → server, the launcher exits at once, so the server's parent is not the bridge (a parent-PID tree kill of the bridge doesn't reach it). The server's env has the `CLAUDE_*` session variables removed. Hidden: Windows `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (the coordinator `_launch` pattern), POSIX `start_new_session`, stdio to DEVNULL (log goes to `.taskmaster/local/serve/serve.log`), env `LEFTOVERS_KEEP=1`, `LEFTOVERS_NAME=tm-local:<project>` so the session-end leftover sweep doesn't kill a server other sessions use; breakaway from the parent's job object attempted. A launch race is settled by the host lease: the loser exits 69 and the bridge re-reads discovery; exit 68 (busy session coordinator) → fallback for this session, with that reason. Wait up to 15 s for ready (spike: cold start incl. spawn ~4.2 s).
   Process lifetime is verified under **real Claude Code** first in 003 (job flags, how Claude Code ends a stdio server, the env it passes, its MCP startup timeout vs a 4.2 s cold start): the first session exits, `/mcp` reconnect, Claude Code killed. The spike's evidence came from the MCP Python SDK's job (KILL_ON_JOB_CLOSE), not Claude Code. If the server cannot outlive its launching session on a platform, local mode stays **off by default there** (fallback) until fixed: restarting per session end is not acceptable (each restart cuts claims and in-flight writes). Any alternative launcher (WMI, scheduled task) must start the process with SW_HIDE and no console (R4).
5. Relay: newline JSON-RPC on stdin → POST /mcp (JSON or SSE responses, 202 notifications, `Mcp-Session-Id`), responses to stdout; the bridge also holds the GET stream open and forwards server-initiated messages. Timeouts per method: none for `tools/call` (managed git and sync run up to 600 s, client.py:528), short for initialize/list. Cancellation notifications are forwarded.
6. Reconnect (eager, on upstream loss): re-run 3-4, replay the cached `initialize` + `notifications/initialized` upstream, and emit `notifications/tools/list_changed` to the client if the server's build changed. **Retry rule (review C1):** a request is retried only when it provably never ran: connection refused before the request was sent, or HTTP 404 "Session not found" (returned before dispatch). Any other failure of a `tools/call` (reset after send, timeout, broken response) is returned to the client as an error saying the call may have committed, never retried: write tools mint a fresh request id per call (coordinator/adapter.py:53-56), so a retry would run twice. The error text matches the mods' transient "connecting" matcher (tm.ts) so a restart blip shows as connecting, not offline. (Idempotency keys from the client to the adapter's request_id are a follow-up: local-server-007.)
7. **Fallback**: spawn today's server (`uv run backlog_server.py`, unchanged) as a child and relay stdio both ways, so a broken local server never leaves a session without tm. The fallback is logged once to stderr (shown in Claude Code's MCP log) with the reason.
8. Bridge exits when stdin closes; it never kills the server.

Interpreter for the bridge (`.mcp.json` command), decided in L2's spike (rev 4): `uv run --no-project --python ">=3.11" python ${CLAUDE_PLUGIN_ROOT}/tm_bridge.py`. It is the only static command that works on every OS: a bare `bash` resolves to the WSL launcher on Windows machines with WSL (Git Bash is not on PATH), and `py`/`python3` are OS-specific. Measured on Windows: 57.7 MB private per session (uv.exe 38.7 MB stays resident as the parent + bridge 17.8 MB) against 119 MB today; on POSIX uv execs, so the bridge alone (~18 MB) is expected (to be measured on the Linux lane). A Windows hand-off (the bridge re-spawns itself detached with the same pipe handles so uv can exit; ~19 MB) is built behind TASKMASTER_BRIDGE_HANDOFF=1 and becomes the default only if the live Claude Code gate shows the session survives its direct child exiting. The 25 MB target holds on POSIX and with the hand-off; without it Windows lands at ~58 MB.

Discovery, launch, the start lock and retire reuse the shape of coordinator/client.py (`_connect_ready`, `_start`, `_retire`, lines 243-370); stdlib-safe pieces are shared, not copied.

## 5. Hooks, CLI, coordinator, viewer (task local-server-004)

- Hooks keep reading `store.db` read-only and writing through the coordinator with autostart off (latency budget 10-30 s; no HTTP hop). Verify each hook (session-start, merge_gate, merge_recorder, edit_resurface, merge_approve, worktree_submodule_init) against a running local server: reads see the server's writes; coordinator writes land; nothing launches a second coordinator.
- merge_recorder and any hook that writes through the coordinator surface a refused or other-build write (a visible line in the hook output and the server log) instead of failing open silently.
- CLI (`taskmaster_cli.py git ... commit` and friends) and any remaining stdio server reach the server's in-process coordinator over the LOCAL protocol, as the spike showed.
- Viewer: served once by the server, guarded, with the one-time login link (§2). The stdio server's viewer thread stays for the fallback path only.
- The separate coordinator service process is no longer started while a local server runs (it lives inside the server): one process per project instead of server + coordinator.

## 6. Defaults and migration

- Default on for every project with a native store and no team marker (legacy stores, such as a pre-cutover schema-1 store, keep the fallback), from this release. Opt-out: `TASKMASTER_LOCAL_SERVER=0` (env, machine-wide) or `local_server: off` in the project's `.taskmaster/local` settings (per project, takes effect at the next session) → fallback path.
- No migration step: the server serves the store in place. Nothing is committed or untracked; Git sees no change.
- Projects without a backlog keep today's behaviour (fallback). Follow-up idea, not in scope: a bridge-only answer for backlog-less projects so they cost no full server either.

## 7. Mods (team-server-018)

- Local projects: no change needed beyond tests: mod calls go through the bridge. The per-call 3 s bound (tm.ts) is measured against the shared server under 8 sessions + mods; raise it only if the probe shows misses.
- Team clones: writes today go to the local stdio store (the 018 gap). Route them to the team server: the mod's write path uses the same reader process family as reads (a `tm_write.py` beside `tm_read.py`, through `taskmaster.remote` with the user's key and /write/v1), or the bridge forwards to the team server for team clones. Decide in 018's design review; the bridge option makes the plugin's tm work in team clones too.
- Desktop app: verify what the desktop app does with plugin MCP servers and `$.process.run` (console flash), and fix the `tm_read.py` lookup (`$.plugin.root/../../hooks` breaks for the desktop upload copy).
- Bump `mods/taskmaster-mods/.claude-plugin/plugin.json` with the marketplace entry and rr-tui dep range.

## 8. Tests and acceptance

- Unit: local profile refusals (marker present, non-loopback), discovery record privacy, idle exit, retire semantics (older/newer/same build), bridge import set, bridge relay framing (JSON, SSE, 202), reconnect replay, header-based session continuity, fallback trigger.
- Integration (Windows and the Linux lane): two bridges on one root share one server (one serve process, one viewer port); a claim taken in session A is refused to session B while A's bridge lives, including after 40 quiet minutes and across a server restart within the grace; it is free once A's bridge has been gone past the grace; a write whose response is cut is reported 'may have committed' and not run twice; hooks against a running server; an old-build stdio server against a new server gets the plain restart message; an idle session coordinator is handed over at launch.
- Live Claude Code (Windows), 003's gate: first launching session exits and the server stays; `/mcp` reconnect; Claude Code killed; no console window at any point; CLAUDE_CODE_SESSION_ID reaches the bridge.
- Acceptance probe (records numbers in the resume file): 8 bridges + the mods' read batch on a large synthetic native store (never a copy of a work project's store): total RAM vs 8 stdio servers (target ≤ 25% of today; server launched without a resident uv), p95 of the mods' read calls under the 3 s bound, no console window (checked by process inspection), server gone 10 min after the last bridge exits.
- Leftovers: the `/leftovers` sweep lists the server as kept (`tm-local:<project>`), never as a session leftover.

## 9. Team-server leftovers in this release

B-111 (Linux sweep + lane count), 029 (POSIX integrity check, design in lane-b111's resume file), 033 (claim-holder status changes; design from lane-hardening), 034 (shared query snapshot, own design + Opus security review), 021, 019, 022, 023. Separate tasks, separate lanes; they share only the release (local-server-005).

## 10. Decisions taken here (flag in review)

1. Bridge over an http .mcp.json entry (static URL, no fallback, header source).
2. Local profile keeps the stdio actor source and skips the schema upgrade and framing.
3. Discovery file + host lease, not a hashed MCP port; the viewer keeps its hashed port.
4. Idle exit 10 min after the last bridge disconnects; session idle timeout off; LEFTOVERS_KEEP + CLAUDE_* scrub + double spawn for the server.
6. Claims are per bridge session in the local profile (holder `operator@<hex>` from the discriminator; no cross-session adoption).
7. Bridge retries only provably-unsent requests.
8. The guarded viewer with a one-time login link; the solo viewer stays on the fallback only and gets a CSRF fix.
5. Default on, env opt-out.

## Follow-ups (filed)

- local-server-006: solo viewer CSRF (Origin/Host check, drop the `*` CORS header). Exists today; in this release because the fallback keeps that viewer.
- local-server-007: idempotency key from MCP clients (bridge, mods, HTTP) to the adapter's request_id, so ambiguous writes can be retried safely. Not in this release.

## Resolved review questions (spec review 1)

- Schema 5: not needed for sessions, claims or receipts; skipping the upgrade is consistent with the local actor source and framing off.
- Write retry: not idempotent today; hence the retry rule in §4 step 6.
- CREATE_NO_WINDOW grandchildren share the hidden console; the console risk is only in alternative launchers.
