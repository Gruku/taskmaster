<!-- User intent: let a team share one Taskmaster backlog through an optional server the user hosts (e.g. on their Hetzner box), with a per-person API key for each teammate's agent (task team-sync-015). -->
# Team collaboration: server mode (addendum to rev 3.3, adopted)

Status: **adopted 2026-10-31** (user ruling). Server mode is an **opt-in, per-project** team
mechanism; solo projects keep the local coordinator. The rev 3.3 Git-branch sync (§6, §7 rename
guard) is **deferred** as a possible later offline/hybrid option; its merged resolver stays as
dormant code. This reverses rev 3.3 §16 "out of scope: a hosted server".

Review history: draft 1 → Opus review (adopt after fixes; C1, C2, I1-I8) → draft 2 → scoped
re-review (adopt after fixes; I4/I6 partial, 3 new Importants) → 2.1 applies them; no Criticals open.

## 1. Question

Rev 3.2 shares a backlog by exchanging entity state over an orphan `taskmaster-state` branch. Every
clone keeps a full store, works offline, and merges teammates' edits. In server mode, **one host
owns the team's store and runs the tools; teammates' agents call them over the network.** There is
one writer, so nothing ever needs merging.

## 2. Shape: remote MCP on the host (review I8)

Draft 1 kept the tools on each client and made the coordinator remote. Every native tool reads
through a local SQLite snapshot (`coordinator/adapter.py:93`, 85 `@adapter` tools, about 100
snapshot sites), and the client handshake binds to a local `root`, `store_id` and nonce
(`client.py:61`, `protocol.py:184-198`). Draft 1 therefore meant rewriting the read layer, and
mixed builds would disagree on client-side domain logic (review C1, I2).

Draft 2 runs the **existing MCP server on the host**, next to its coordinator, and exposes it as a
remote HTTP MCP server (FastMCP streamable-HTTP). Claude Code and Codex connect to a remote MCP
URL with an auth header. The tools, the read snapshots and the coordinator stay local *to the
host*, unchanged. What changes:

| Area | Change |
|---|---|
| Transport | `taskmaster serve` runs `backlog_server` over HTTP. The coordinator stays loopback-only on the host |
| Client config | Teammates add the remote MCP server to their Claude Code config (`.mcp.json` / user settings) with the URL and their token. Nothing about the server lives in `.taskmaster/` (review I3) |
| Version skew | It goes away for tools, because one build runs on the host. Hooks and the mods (below) talk to a versioned HTTP read endpoint |
| Viewer | Served by the host behind the same auth |

## 3. Principal model (review C2)

Each person gets a token: `taskmaster serve add-user <handle>` stores a hash, and `revoke-user`
deletes it. The token is the **principal**, and the server derives everything identity-bearing
from it:

- **Actor.** The handle comes from the token, and the coordinator stamps it after admit. The
  `team-identity-002` actor-resolver seam is where this plugs in.
- **Sessions.** `SESSION_ID` (`backlog_server.py:628`) becomes per MCP connection:
  `<handle>-<connection nonce>`. It must still match `SESSION_SHAPE` (`claims.py:144`), or the
  shape is widened. `caller_scope` (`adapter.py:50`) is prefixed with the handle on the server.
  Claim session arguments (`workflow.py:830,905`; `claims.py:86`) are checked against the
  principal: a caller may renew or release only its own sessions, unless it is the operator.
- **Receipts and work queues** (`service.py:478-498`) are scoped to the principal's
  `caller_scope` prefix. Fixed internal scopes such as `coordinator-identity` can't be claimed
  remotely.
- **Method allowlist.** Remote principals reach the MCP tool surface only. Admin methods —
  `shutdown`, `retire`, `git_run`, `git_recover`, `sync take_file`, `linear_bootstrap`
  (`service.py:451-524`) — are reachable only from the host's loopback by the operator. The
  coordinator stays bound to loopback, and the MCP layer exposes no admin passthrough.
  `linear_bootstrap` on a server additionally refuses caller-named `token_env`.
- **Identity gate.** The gate becomes per-request: principal → handle and profile, not one Git
  identity per process (`identity_gate.py:112-153`).

## 4. Transport security (review I1)

Allowed: loopback plus an **encrypted** overlay (Tailscale `serve`, WireGuard, or an SSH tunnel),
or a TLS reverse proxy the operator runs. Binding a non-loopback address without TLS needs
`--insecure-bind`, which prints a warning naming the risk. "Private interface" alone is not enough,
because a plain-HTTP bearer token can be sniffed on a LAN. Hardening: auth before body read, a
per-principal rate limit, header and body timeouts, and sanitized errors (no `str(exc)` with paths
or SQL, `service.py:750`). Token files on clients get the owner-only check that `discovery.json`
gets (`ownership.py:114`).

## 5. Hooks, the viewer and the mods

Hooks run on the teammate's machine as one process per call. Today they read SQLite directly
(`edit_resurface`, `merge_gate_decide`, `merge_recorder_stamp`, which also writes `mode=rw`).
In server mode:

- They call one versioned HTTP read endpoint on the host, through a small on-disk cache under
  the user's cache dir with a TTL (review I5). Merge stamps are sent as commands.
- **Merge gate fails closed** while the server is unreachable. The hook blocks with "Taskmaster
  server unreachable; retry or bypass explicitly" instead of silently falling back to projection
  files (`merge_gate_decide.py:24-27,334`).
- The mods read the same endpoint.
- **Viewer (re-review new-1).** Today the viewer has unauthenticated POST, PUT and PATCH handlers
  (`backlog_server.py:12046`, `:12437`, `:12495`) and `Access-Control-Allow-Origin: *`
  (`:11652`, `:12591-12592`). Exposed, it is a CSRF hole. Server mode requires:
  - a browser login that exchanges the token for an `HttpOnly`, `SameSite=Strict` session cookie;
  - an Origin check plus a CSRF token on every write;
  - removal of the `*` CORS headers.
  Until that ships, the viewer stays loopback-only on the host.
- **Git checks (re-review new-2).** Server-side tools that check Git ancestry
  (`native_routing/merge_stamps.py:340`) would see the host's clone, which lacks teammates' unpushed
  commits. Ancestry and branch checks run in client hooks, and their results are sent as command
  arguments. The host never decides from its own clone whether a teammate's commit exists.

## 6. Fencing split brain (review I4)

A teammate's clone must never quietly write to a local store. A tracked marker, outside
`.taskmaster/`, records `{mode: server, url}` (e.g. `.taskmaster-server.yaml` at the repo root;
the exact name is open). When it is present, `native_routing/gate.py` refuses to open or init any
local store and points to the remote MCP. The host is exempt because of the process, not the
file. Only a process started by `taskmaster serve`, which holds the server ownership lock, may open
the host clone's store. An ordinary session in the host clone is refused like any other clone
(re-review I4). The host's daemon refuses `retire` from any other build unless the operator uses
`--force-upgrade` locally.

## 7. Claims (review I6)

TTL and renew already exist (`claims.py:65`, 4 h default). For remote principals the server renews
a session's claims while its MCP connection is alive. On disconnect, the claims fall to a short TTL
(proposed 15 min) instead of 4 h. Host PID liveness (`claims.py:163-193`) applies only to host-local
sessions. After a server restart, expiry clocks resume, and claims of disconnected sessions get one
grace TTL.

Streamable-HTTP connections aren't durable: a Claude Code reconnect is a new connection, so it
gets a new session. A principal may therefore **adopt, renew or release any session under its own
handle**. `pick` on a task claimed by the same handle's older session transfers the claim instead
of refusing. Claims held by other handles stay protected (re-review I6).

## 8. Files, history and machine-local fields (review I3, I7)

- **Export and history.** The host's checkout is a dedicated clone. Its `.taskmaster/` export is
  committed to the `taskmaster-state` branch (the rev 3.2 R3 transport becomes the backup branch)
  and optionally pushed. Code branches stop tracking `.taskmaster/`.
- **Host-side files.** `PROGRESS.md`, handovers, `backlog_sync` and `backlog_document_import`
  operate on files on the host. Imports from a teammate's machine take content, not paths: the
  tool gains a `content` argument for remote callers.
- **Machine-local fields.** `worktree`, `branch` and managed-Git arguments (`checkouts.py:105-124`)
  describe a teammate's checkout. They move to a per-principal, per-clone table. Managed Git runs
  client-side through hooks, never on the host.
- **Linear.** Linear runs on the host with the operator's token. Rev 3.2's `team.linear_clone`
  falls away.

## 9. Migration

`taskmaster serve init --from <existing project>` copies a solo store (schema 3/4) into the host
clone. It runs the untrack flow for `.taskmaster/` on code branches, which rev 3.2 left out of
scope and which must now exist, and then writes the §6 marker. Teammates then add the remote MCP
server and their token.

## 10. Retired and kept

Retired: the §6 sync engine, the §6.5 resolver (`native/team_merge.py`, merged as team-sync-004 —
dormant), §6.6 triggers, §6.8 unattended Git, the §7 rename guard and ID collision handling, the
§6.7 join flow, the sync-health panel and the `team.*` sync_state keys.

Kept: §4 identity (the token is its source), §5 planning, assignment and claims, the §10 Team
screen, §11 `project.yaml` (the server settings go in the §6 marker instead), §12 migration
(schema 4/5 opt-in), the team-sync-001 `team:` block and the team-identity-001 profile kind.

## 11. Tasks (re-estimated: about 15)

1. `serve`: HTTP MCP transport, daemon lifetime, loopback coordinator, `--insecure-bind`.
2. Principals: `add-user`/`revoke-user`, hashed tokens, per-request identity gate.
3. Per-connection `SESSION_ID`, handle-prefixed `caller_scope`, claim session ownership checks and
   same-handle adoption. Risk: `SESSION_ID` is a module global used 24 times in `backlog_server.py`
   and across 8 files, and FastMCP runs tools in a thread pool, so this needs context-variable
   plumbing (re-review new-3). It may split into two tasks.
4. Receipt, queue and request-id scoping per principal.
5. Remote method allowlist; admin loopback-only; `linear_bootstrap` hardening.
6. Transport hardening: auth before body, rate limit, timeouts, error sanitizing; viewer login
   cookie, Origin/CSRF checks, drop `*` CORS.
7. Versioned HTTP read endpoint and on-disk cache for hooks and mods.
8. Merge gate fail-closed; `merge_recorder_stamp` to commands.
9. Split-brain marker and gate refusal; retire fence.
10. Connection-bound claim renewal and the short disconnected TTL.
11. Per-clone machine-local fields; client-side managed Git.
12. Content-based `document_import`/`backlog_sync` for remote callers.
13. Export commits to `taskmaster-state` and optional push.
14. `serve init --from` migration plus the `.taskmaster/` untrack flow.
15. Two-host test harness and eval coverage; security review of the exposed surface (Opus floor).

## 12. Trade-off and recommendation

| | Git sync (rev 3.2) | Server mode (draft 2) |
|---|---|---|
| Offline writes | Yes | No |
| Conflicts | Resolver plus rename guard | None |
| Ops | None | One always-on host and an encrypted overlay |
| Remaining build | About 20 tasks; the subtle ones are the push race, rename guard and ID collisions | About 15 tasks; the subtle ones are the principal model and hook caching |
| Security surface | Git remote permissions | A network-reachable tool server, principal-scoped |
| Teammate visibility | On sync | Immediate |

**Recommendation:** adopt server mode if the team accepts no offline writes and one host to run.
The remaining build is now comparable in size to Git sync. What decides it is the kind of risk:
correctness under concurrent offline edits (Git sync) against operating and securing one service
(server mode). For a small team on a shared overlay, the second is easier to get right and to test.

## 13. Deployment profile (user ruling 2026-10-31)

- **Host:** the user's Hetzner server (Linux). Taskmaster is developed and tested on Windows, so
  a Linux test run of the server path is part of the work (task 16 below).
- **Edge:** a public domain behind Caddy with automatic TLS, terminating HTTPS and proxying to
  `taskmaster serve` on loopback. No overlay network; teammates need only the URL and their key.
- **API keys:** one per person, minted by `taskmaster serve add-user <handle>` (the §3 principal
  token). Each teammate puts it in their agent's MCP config as a bearer header.
- **Go-live order:** because the host is internet-facing, tasks 5 (allowlist) and 6 (hardening)
  plus the task 15 security review ship **before** any teammate key is issued. The viewer stays
  loopback-only on the host (reach it over SSH port-forward) until its login and CSRF checks ship.
- **Operations:** a systemd unit runs `taskmaster serve`; the host firewall opens 80/443 only;
  backups are the task 13 export commits pushed to a Git remote.
- Additional task 16: Linux CI/test lane for the server, plus `serve` packaging and a deployment
  how-to (systemd unit, Caddyfile, `add-user`).

## 14. Open questions for the user

Resolved 2026-10-31: server mode is adopted as opt-in; Git sync is deferred (so offline writes
are given up only in server-mode projects); the host is the user's Hetzner box behind HTTPS + API
keys; the resolver stays dormant. None open.
