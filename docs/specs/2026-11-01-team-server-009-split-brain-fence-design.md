<!-- User intent: in a server-mode project, no teammate clone and no stray process on the host may quietly
     open or create its own local store and fork the team's backlog; only the `taskmaster serve` process that
     holds the host lease may open the host clone's store, and only the operator on the host can hand it over to a new build (team-server-009). -->

# team-server-009: split-brain fence (design, 2026-11-01)

**Task.** `team-server-009`. Done when a repo-root server marker makes native routing refuse to open or
init any local store, except in the serve process that holds the server ownership lock, and the daemon
refuses `retire` from other builds unless the operator runs `--force-upgrade` locally.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §6 (also §5, §8, §9, §13).
**Builds on.** 001 (`ServeDaemon`, `ServeCoordinator`, D1 in-process coordinator, D7 no autostart, D8
retire refusal), 002 (operator token, `local/serve/`), 006 (guarded viewer, `TASKMASTER_SERVE`). Code
read at `feat/team-collab` 748cd29. 005's branch (`feature/team-server-005`, in review) was read for
its `ServeCoordinator.dispatch` changes.
**Plan.** Embedded in §16; regression set in §17.
**Review.** Design review 2026-11-01: WARN (0 Critical, 1 Important, 12 Minor). I1 (marker looked up at
the store root instead of the enclosing repo root) and every Minor are folded in, each tagged at its
place; Q1-Q5 are decided (§15).

## 1. Problem

In server mode, one host owns the team's store and the teammates' agents call its tools over HTTPS.
Every other copy of the backlog is a fork waiting to happen:

- **A teammate's clone.** The plugin's local `tm` server still loads in every Claude Code session. Asked
  to `backlog_init`, or pointed at an old checkout that still tracks `.taskmaster/`, it builds a local
  store and the agent writes there. Nothing reaches the team, and nobody notices.
- **The host clone.** An ordinary session there resolves to the team's own store. Today it can open it
  directly, start its own coordinator when the daemon is down, or (same build) send commands to the
  daemon's coordinator with no principal (001 §11 lists this gap).
- **Another build on the host.** Today `ServeCoordinator` refuses every retire (001 D8), so the
  operator has no in-band way to hand the store to a new build except stopping the daemon.

The fence makes all three impossible from 009's build on, without changing anything for a project that
has no marker.

## 2. Threat and failure model

The fence protects against **mistakes and stale software**, not a hostile local account. A process
running as the host's `taskmaster` user can read `store.db` with `sqlite3` whatever this code does; the
OS account is that boundary (001 §10, `UMask=0077`).

| # | Failure | Fenced by |
|---|---|---|
| F1 | A teammate's session (local `tm` server, CLI, viewer) opens or inits a local store, from the repo root or any subdirectory | marker at the enclosing repo root (§4.1, §7) |
| F2 | A teammate checks out a pre-marker commit (old tag, bisect) whose `.taskmaster/` is still tracked | the latch (§5) |
| F3 | An ordinary session or CLI in the host clone opens the team store, or starts a coordinator on it | marker plus the host lease (§6) |
| F4 | A child of the daemon (git, a pre-commit hook) opens the store as a separate process | non-inheritable lease; children are not exempt (§6) |
| F5 | `taskmaster serve` is started on the wrong clone (the operator's laptop, which also has a store) | `host_id` binding (§4.3) |
| F6 | A client of another build retires the daemon's coordinator | retire fence (§8) |
| F7 | A build older than 009 ignores the marker | not fenceable in code; rollout rule (§9) |
| F8 | Same-user code calls the exemption API in-process | out of scope (OS account boundary) |

Fail closed everywhere: a marker that exists but can't be read refuses exactly like a valid one.

## 3. Decisions

- **D1.** The marker is `.taskmaster-server.json` at the repo root, JSON, git-tracked, read with the
  standard library only (hooks run without `yaml`). It names the server URL and the host clone's
  `host_id`.
- **D2.** A clone that has once seen the marker keeps a **latch** in its git dir, so an old checkout
  without the marker stays fenced until the user clears it explicitly.
- **D3.** The exemption is a **kernel lock**, `.taskmaster/local/serve/host.lock`, held by the process
  for its whole life and registered in that process's memory. No environment variable, file content or
  argument grants it. `TASKMASTER_SERVE` (006) stays a viewer switch and is never read by the fence.
- **D4.** The check is one function, `server_fence.refusal(path)`. It looks for the marker at the path
  it's given *and* at that path's enclosing repository root (§4.1), so no caller can pick the wrong
  root. It is called at four choke points: the MCP tool wrapper and the native routing gate, the
  legacy store's open, the coordinator's `connect`, and the cutover entry points. Every store open in
  `taskmaster/`, every create and every write passes at least one of them (§7). The three hooks'
  direct reads don't (D7).
- **D5.** With no marker and no latch, the check costs two `lstat` calls once the path's repository
  root is cached (four when the path is a subdirectory of its repo). No read, no write. Solo mode is
  unchanged.
- **D6.** `ServeCoordinator.retire` accepts only a request carrying a `--force-upgrade` proof: an HMAC
  over the daemon's generation nonce, keyed by the operator token, sent from a host process with no
  served origin. Then the base retire policy applies (newer or same-release-other-code, idle only).
- **D7.** 009 doesn't edit the three store-reading hooks; 007 moves them to the read endpoint in server
  mode. 009's fence still refuses any hook write, because writes need the coordinator.

## 4. The marker

### 4.1 Name, location, tracking

- **Name.** `.taskmaster-server.json`, matching 007's Q2 recommendation (the addendum left the name open
  and suggested YAML; JSON is what hooks can parse).
- **Location.** `<repo root>/.taskmaster-server.json`, where the repo root is the git common dir's
  parent (`root._git_common_root`, `root.py:263`), so every linked worktree of a clone sees its main
  checkout's marker. Outside `.taskmaster/`, because 014 untracks `.taskmaster/` from code branches
  (addendum §8) and a teammate clone has no `.taskmaster/` at all.
- **Which roots are checked (review I1).** A choke point holds whatever project path its caller
  resolved: `backlog_server.ROOT` (the cwd or `TASKMASTER_ROOT`, possibly a subdirectory), an
  `explicit` store resolution rooted in a subdirectory (`store.py:943-962`), or a root-layout
  `backlog.yaml`'s own directory. `server_fence.fence_roots(path)` returns `path` plus, when it
  differs, the path's enclosing repo root, and `refusal` checks the marker and the latch at each.
  - The repo root is computed once per normalized path and cached in `server_fence` (a dict, cleared by
    `reset_for_tests`). `_git_common_root` reads the filesystem and spawns git only in its rare
    `_ASK_GIT` cases (`root.py:189-260`).
  - Not in a git repository: `path` alone. A server-mode project is a git repository (§14).
- **Tracking.** The operator commits it on every code branch (014 automates this with the untrack
  flow). The fence never asks git whether it is tracked: an untracked marker fences just the same.
  `serve marker show` reports the tracking state as a diagnostic.

### 4.2 Format

```json
{
  "format": 1,
  "host_id": "9f0c3e7a5b1d4e2f8a6c0b9d7e5f3a1c",
  "mode": "server",
  "url": "https://tm.example.com/mcp"
}
```

- **Canonical rendering.** Sorted keys, two-space indent, LF, trailing newline, UTF-8, so a rewrite with
  the same values is byte-identical and diffs stay quiet.
- **Validation** (`server_marker.parse`):
  - a JSON object of at most 4 KiB;
  - `format` is an int; above 1 means "newer than this build: update the taskmaster plugin" (refuse);
  - with format 1, the keys are exactly `format`, `host_id`, `mode`, `url`;
  - `mode == "server"`;
  - `url` is at most 2048 characters, with no userinfo, query or fragment, and either `https://` or
    `http://` to a loopback literal (tests and SSH port-forwards);
  - `host_id` is 32 lowercase hex characters.
- **Presence** is `os.path.lexists`, so a dangling symlink or a directory at that path counts as present
  and unreadable. That's fail closed, never solo by accident (the same rule as 007 D8).
- **Reading (review M4).** `lstat` first: anything but a regular file (a symlink, which git can track, a
  FIFO, a directory or a device) is unreadable without being opened. Then `os.open` with `O_NOFOLLOW`
  where the platform has it (and `O_NONBLOCK` on POSIX), `fstat` again to confirm a regular file, and
  read at most 4 KiB + 1 bytes; more is unreadable. A committed link to `/dev/zero` or a huge file can
  therefore never hang or bloat a tool call.
- **Reader API** (the one 007 asked for, §13.2): `read_marker(root) -> Marker | None`. `None` only when
  nothing exists at the path; otherwise a `Marker` or `ServerConfigError(path, reason)`.

### 4.3 `host_id`: binding the marker to one host clone

The marker is committed, so every clone carries it, including the operator's laptop, whose store may be
the very solo store that 014 copied to the host. A `store_id` binding wouldn't separate them, because
`store_id` is the store's `creation_token` (`native/db.py:61`) and a copy keeps it.

- `serve marker write` creates `.taskmaster/local/serve/host-id` (owner-only directory, exclusive
  create; 32 hex characters) if it is missing and puts the same value in the marker.
- The daemon, at start, with a marker present: refuses with exit 2 unless the local `host-id` file exists
  and equals `marker.host_id` (§6.3). On the laptop it doesn't exist, so a mistaken `taskmaster serve`
  there can't split the store.
- A host restored from backup keeps `local/serve/`, and with it `host-id`. A host rebuilt without it
  runs `serve marker write --rotate-host-id` and commits the new marker.

### 4.4 Writer

`server_marker.write_marker(root, url, host_id)` validates through `parse`, then writes a temp file next
to the target and `os.replace`s it. It writes nothing else and never commits. The CLI (§10) prints the
`git add` and commit commands. 014 calls the same function.

## 5. The latch

Root-only detection misses F2: in a teammate's clone, `git checkout v7.3` brings back a tree with no
marker and a tracked `.taskmaster/`, and the next tool call would bootstrap a store from it.

- **Where.** `<repo root>/.git/taskmaster-server.json`, checked at each fence root (§4.1) and written
  only at a root whose `.git` is a directory (a normal clone or main checkout). It is never tracked, it survives checkouts, and every linked worktree shares
  it, since their root is the main checkout. With no `.git` directory (a separate git dir, or
  `TASKMASTER_ROOT` pointing at a linked worktree), there is no latch and the marker alone applies.
- **Written** by `refusal(root)` the first time it sees a valid marker there and no latch: the marker's
  bytes plus `first_seen`, by exclusive create, best effort (a failure is ignored). `serve marker write`
  writes it too. The plugin's MCP server and hooks run at the first session in a clone, so the latch is
  normally in place long before anyone checks out an old commit.
- **Read** only when the marker is absent, with the marker's reading rules (§4.2). A latch alone
  refuses with its own text (§7.1). A latch that can't be read still refuses, with the URL given as
  "unknown".
- **Cleared** only by `taskmaster serve marker clear-latch --root R`, which refuses while a marker is
  present. That covers a project that has really left server mode.
- **Residual.** A clone whose first action, before any taskmaster process runs, is checking out a
  pre-marker commit isn't latched. Accepted; §14 Q2.

## 6. The exemption: the host lease

### 6.1 What proves it

`server_fence.claim_host(root) -> HostLease` opens and locks `.taskmaster/local/serve/host.lock`
non-blocking, with the same primitive as the coordinator's ownership lock (`msvcrt.locking` /
`fcntl.flock`, `ownership.py:162-182`). `Ownership` is generalized in place with a lock-file name
rather than copied. On success it records the lease, with `os.getpid()`, in the process-global
`_HOST_LEASES` keyed by the normalized root (`os.path.normcase(os.path.realpath(root))`). `exempt(path)`
is true only while that entry's descriptor is open **and** its recorded pid equals `os.getpid()`, for
`path` or any of its fence roots.

- **Fork (review M6).** A plain `os.fork()` (or `multiprocessing`'s fork start method) copies both the
  registry and the locked descriptor. The pid check makes the child non-exempt. Nothing in `taskmaster/`
  forks today; this keeps a future fork from becoming a hole.
- **A probe can race a start (review M11).** `ownership_held` probes by taking the lock and releasing it
  (`ownership.py:128-153`), so a `coordinator status` or `serve marker show` at the wrong instant could
  make a starting daemon see the lease as held. `claim_host` retries for 200 ms (every 20 ms) before
  raising `HostLeaseHeld`.

Why this and nothing weaker:

- **Exclusive.** At most one process per root can hold it. A probe on Windows confirmed that a second
  handle in the *same* process is refused too (errno 13), as is another process, and that closing
  releases it (`D:\scratchbook\taskmaster\ts009\probe_lock.py`).
- **Not forgeable from outside.** No environment variable, argument or file content is consulted.
  The registry lives in memory, so another process can't set it, and a process can't claim the lease
  while the daemon holds it.
- **Not inherited.** Python opens descriptors non-inheritable (PEP 446), Windows `Popen` defaults to
  `close_fds=True`, and the registry isn't copied to a spawned child. A git child or a pre-commit hook
  started by the daemon is an ordinary process: fenced (F4). The lease also dies with the daemon, not
  with its longest-lived child.
- **Released on any exit,** like the ownership lock: crash, kill, or `close()`.

### 6.2 Who may claim it

Only `taskmaster serve` code paths, all inside `with server_fence.host_lease(root):`:

- the daemon (`ServeDaemon`), for its whole life;
- 013's `serve export run` and 014's `serve init --from`, which open the store while no daemon runs (§13).

`serve add-user`, `rotate-user`, `revoke-user`, `list-users` and `token` don't claim it: they run beside
a live daemon and don't need the store (§7.2).

### 6.3 Daemon start order

`ServeDaemon._start` (`daemon.py:191`) changes in one place. Right after `configure_logging` (which
creates `local/serve/`) and before the operator token, the principals, the socket, the server-module
import or any store access:

1. `claim_host(root)`. If it is held: without `--force-upgrade`, exit 69 with "another `taskmaster serve`
   (daemon, export run or init) holds R; stop it first". With the flag, §8.3.
2. `server_marker.server_mode(root)`, which honours the latch (review M1). `ServerConfigError` → exit
   2. If it returns a marker (from the file or from the latch), check the `host_id` binding (§4.3) →
   exit 2 on a mismatch, naming both ids and the fix. A laptop that is latched but checked out on a
   pre-marker commit therefore can't serve either. If it returns none, log a WARNING: "no server marker
   in R: ordinary sessions in this clone are not fenced; write it with `taskmaster serve marker
   write`." (001 behaviour, and what tests use.)
3. The rest of today's `_start`, unchanged.
4. `_teardown` releases the lease **last**, after `coordinator.close()` has released the ownership lock
   and the socket is closed, so a successor that wins the lease finds both free.

The ready line gains `"marker": {"url": ..., "host_id": ...}` or `null`.

## 7. Where the fence sits

### 7.1 The check

```python
# taskmaster/server_fence.py (stdlib only, plus taskmaster.admission and coordinator.ownership)
class ServerModeRefused(UnsupportedStoreError): ...   # .root, .kind in {"marker", "latch", "unreadable"}

def fence_roots(path) -> tuple[Path, ...]:   # path, plus its enclosing repo root when different (cached)

def refusal(path) -> str | None:
    # 1. for r in fence_roots(path): lexists(r/MARKER) or lexists(r/.git/LATCH)?  None found -> None (D5)
    # 2. exempt(path)?                                                            Yes -> None
    # 3. the text for the marker, its unreadable state, or the latch; record the latch (section 5)

def check(path) -> None:      # raise ServerModeRefused(refusal) when refusal(path) is not None
```

There is no memo: a marker that arrives with `git pull` fences the next call (the gate's own rule,
`gate.py:1-3`). The texts, all ending in "nothing was changed":

- **Marker:** "This project uses a Taskmaster server (https://tm.example.com/mcp, from
  .taskmaster-server.json). Its backlog lives on the server, so this clone never opens or creates a
  local store. Use the server's MCP tools with your own API key (the operator mints it with `taskmaster
  serve add-user`)."
- **Unreadable:** "…the server marker at <path> can't be used (<reason>); no local store is opened until
  it is fixed or removed."
- **Latch:** "This clone has seen Taskmaster server mode (<url>), but <root> has no
  .taskmaster-server.json (an older checkout?). The local store stays closed. If the project has left
  server mode, run `taskmaster serve marker clear-latch --root <root>`."

### 7.2 Choke points

| Choke point | Covers | Behaviour when refused |
|---|---|---|
| **G1a** the `_route_native` wrapper (`backlog_server.py:115`), first statement, on `backlog_server.ROOT` | every MCP tool: `_GuardedToolRegistrar` wraps every tool (`backlog_server.py:181-197, 235`), so this runs before any tool body, any `_backlog_path()` resolution and any legacy transaction, and for nested calls too | answers in `registry.route`'s error shape (`Error: …`, or `{"error": …}` for `JSON_ERRORS` tools). `backlog_init`, `backlog_project_init` and `backlog_migrate_v3`/`_v4` are refused before they write any scaffold (review I1, case A: with no `.taskmaster/`, `_resolve_paths` falls to `ROOT/backlog.yaml`, `backlog_server.py:592-628`) |
| **G1b** `gate.native_database` (`gate.py:53`), first statement. Path: `store.resolve_location(backlog_path).root` (cached); on `LegacyLayoutError`, the backlog directory's parent when it is named `.taskmaster` or `.claude`, else the directory itself (a root-layout `backlog.yaml`). The repo root then comes from `fence_roots` either way | `registry.route` callers that don't come through G1a, and the viewer's native reads (`native_routing/viewer.py:33`) | raises `ServerModeUnavailable(NativeUnavailable)`, which `route` already renders |
| **G2** `store.open_store` and `store.read_only_status` (`store.py:976-1006`), after `_resolve_for` | the legacy `Store`, its bootstrap (which creates `store.db`), `fresh.activate_fresh` step 1 (`fresh.py:87`), any non-MCP legacy caller (e.g. `merge_recorder_stamp`'s `_bs.backlog_record_merge`) | raises `ServerModeRefused`; `_guard_legacy_layout` (`backlog_server.py:51`) catches it beside `LegacyLayoutError` and renders it as G1 does |
| **G3** `coordinator.protocol.connect` (`protocol.py:65`) | every native read (`adapter.open_call`), `identify` (so `Client.__init__`, hence no launch and no retire), `Coordinator.__init__` (so no session coordinator acquires `owner.lock`), the daemon's own reads (exempt), `principals.inspect_store` | raises `ServerModeRefused` |
| **G4** `cutover.dry_run`, `cutover`, `rollback`, `fence_state` (`cutover.py:1024, 1086, 1391, 1531`) | the operator cutover, whose helpers and `quiesce` open SQLite directly | `check(root)` first; `cutover.main` prints `{"state": "refused", "detail": …}` and exits 2 |

Direct `sqlite3.connect` sites outside these: `gate.py:71` (G1 itself), `fresh.py:61` (`state`, called
only from `backlog_init`, which G1 refuses), `cutover.py:144/150` and `quiesce.py:100` (G4),
`integrity.py:25` (only from inside an opened `Store`), `store.py`'s internal connects (behind G2),
`git_hook.py:114` (§7.3), and the hooks (§7.3).

Because G2 and G3 check `fence_roots`, a subdirectory root (I1 case B: `backlog_init` run from
`repo/sub`, or `TASKMASTER_ROOT=repo/sub`, scaffolding `sub/.taskmaster/` under an `explicit`
resolution) is refused by the marker at the repo root, at every choke point.

**Ratchet (review M3).** A test scans `taskmaster/`, `hooks/`, `scripts/` and `evals/` and fails when a
call site appears that isn't in its allowlist, with each entry naming its choke point or why it is
exempt: `sqlite3.connect(`, `Store(`, `Ownership(` and `private_directory(`. The last two create
`.taskmaster/local/…` without any `sqlite3.connect` (`fresh.activate_fresh` → `cutover._ownership`).

### 7.3 Entry points

"Refused" means the §7.1 text, and that nothing is created: no `.taskmaster/`, no `store.db`, no `local/`
files, no `owner.lock`, no viewer port.

| Entry point | Marker or latch present, process not exempt |
|---|---|
| Plugin `tm` stdio MCP server, every tool (teammate clone or host clone, session at the repo root or in any subdirectory, with or without `TASKMASTER_ROOT`) | refused per call (G1a; G1b, G2 and G3 as backstops). The server still starts, so the error reaches the agent instead of a "tm failed to connect" |
| Import-time viewer and `backlog_open_viewer` | `_start_viewer_server` (`backlog_server.py:12793`) returns without binding, right after the `_serve_mode()` check, using `refusal(ROOT)`; the tool is refused by G1a |
| Viewer data handlers (when a viewer is already up for another reason) | refused (G1b, G2) |
| `adapter.Client(root)` in any process | `ServerModeRefused` from `identify` (G3) before any discovery read, IPC, `_launch` or `_retire` |
| `python -m taskmaster.coordinator.service` (`Client._launch`) | refused in `Coordinator.__init__` (G3), before `Ownership.acquire`; exits nonzero, and `owner.lock` is never created |
| `taskmaster coordinator status` | doesn't build a `Client`: prints `{"state": "server-mode", "detail": text, "host_lease_held": bool, "owner_held": bool}`, exit 0. It reads only the two locks, never the store |
| `taskmaster coordinator stop` | `{"state": "refused", "detail": "… stop the daemon (systemctl stop taskmaster)"}`, exit 1 |
| `taskmaster git …` (`git_cli`) | refused at `Client` (G3), reported as JSON, exit 2 |
| `taskmaster git-hook pre-commit` (`git_hook.check`, `git_hook.py:87`) | never opens the store. If `.taskmaster/` projection files are staged: "server mode: `.taskmaster/` isn't committed from this clone; the server's state is backed up on `taskmaster-state` (013)". Nothing staged: allow, as today |
| `taskmaster cutover …` | refused, exit 2 (G4) |
| `taskmaster serve` (daemon) | exempt once it holds the lease (§6.3) |
| `taskmaster serve add-user` | `inspect_store` is refused by G3 inside its best-effort `try`. The CLI says "profile check skipped: server mode; the daemon checks on first write" instead of today's "could not read the store" |
| `serve rotate-user`, `revoke-user`, `list-users`, `token` | unchanged: they touch `principals.json` and `operator.token`, not the store |
| `serve marker write / show / clear-latch` | new (§10); none of them opens the store |
| Hooks `edit_resurface`, `merge_gate_decide` | unchanged by 009 (D7). A fresh teammate clone has no `store.db`, so they read nothing. Where a `store.db` is left over (the host clone; the operator's laptop, §12; a teammate who used solo mode before the switch), they read it read-only until 007 routes them to the endpoint. On a leftover store, that means the merge gate decides on stale gates (accepted gap, §15; 007 §12, 008) |
| Hook `merge_recorder_stamp` | its read is as above; its write goes through `merge_stamps.native_apply` → `Client` (G3) → refused. Its own handling then queues the stamp into the local `.taskmaster/local/` and logs it (`merge_recorder_stamp.py:294-299`). Nothing ever replays it usefully (accepted gap). 007/008 replace the server-mode path |
| `hooks/session-start.sh` | the walk-up loop (`:31-44`) checks `.taskmaster-server.json` before `.taskmaster/`, and also at `$TASKMASTER_ROOT`. Context: "This project uses a shared Taskmaster server (URL in .taskmaster-server.json). Use the server's MCP tools with your own key. The local tm tools refuse in this clone; don't run init-taskmaster here." |
| Mods | they call the `tm` tools, which are refused, so the status line shows the reply-unreadable or offline fault until 007's read path |
| Tests | no marker in any temporary root, so the fence is inert (§11) |

## 8. Retire fence and `--force-upgrade`

### 8.1 Who can send `retire` today

Only a process on the host that read the owner-only `discovery.json` can reach the coordinator: it binds
loopback (`service.py:165`) behind the discovery token, the MCP surface has no passthrough (001 §7),
and 005 routes the method to `ServeCoordinator` for every principal (`allowlist.SERVER_REFUSED`). From
009 on, G3 stops every non-exempt `Client` on a marker repo before it sends anything. What is left is an
older build on the host, or the operator.

### 8.2 The daemon side

- `Coordinator.dispatch` passes the message to `retire`: `self.retire(build, message)`, with
  `retire(self, requester, message=None)`. The base class ignores `message`.
- **A stray session coordinator stops itself (review M10).** A ≥009 session coordinator that started
  before the marker arrived keeps `owner.lock` until its idle timeout, and blocks the daemon's start.
  Its `dispatch` already re-runs `identify(self.root)` on every message (`service.py:458`), which G3 now
  refuses, but only `_connect` calls `stop()` (`service.py:146`). `dispatch` therefore catches
  `ServerModeRefused` from that `identify`, calls `self.stop()` and re-raises. The next message of any
  kind makes it leave (in the solo service, `main` then closes it and releases the lock). Those two
  `dispatch` lines and the `retire` signature are the only edits to `service.py`.
- `ServeCoordinator.retire(requester, message)`:
  1. No `force_upgrade` field → today's refusal, with the text updated: "…to upgrade, install the new
     build and `systemctl restart taskmaster`, or run the new build's `taskmaster serve --force-upgrade
     --root R` on the host."
  2. The message carries an `origin` (a served request) → refused: "force-upgrade is a host-local
     operator action".
  3. The proof isn't a 64-character lowercase hex `str` (checked before `hmac.compare_digest`, which
     raises `TypeError` on non-ASCII text; review M5), or doesn't verify for the build this handshake
     carries → refused, and WARNING logged with the requester's build.
  4. Otherwise it sets `self.retired_for = requester` and returns the **base** `Coordinator.retire`
     policy unchanged (`service.py:565-591`): the same build is refused ("nothing to retire"), an older
     requester is refused (never a downgrade), a busy owner answers `busy`, and an idle one stops and
     answers `retiring`. If the answer isn't `retiring`, `retired_for` is cleared.
- **The proof.** `upgrade_proof(key, nonce, requester_build) = HMAC-SHA256(key, b"taskmaster-force-upgrade\0"
  + nonce + b"\0" + canonical_json(requester_build)).hexdigest()`, where `key = sha256(operator token)`.
  - The daemon re-reads the token file at verify time through `auth` (owner-only check included), so a
    rotated token works, and compares with `hmac.compare_digest`.
  - The nonce is this daemon generation's, so a proof can't be replayed to a later daemon.
  - It proves the sender could read `local/serve/operator.token` on the host, and that it came from the
    deliberate `--force-upgrade` path, which is the only code that computes it.
- **The daemon's exit.** The main loop (`daemon.py:303-310`) tells the two stops apart. If
  `coordinator.retired_for` is set, it logs "retired by a local --force-upgrade to build X" and exits
  `EXIT_OK` (0), so systemd's `Restart=on-failure` doesn't restart it. Any other unrequested stop is
  still 75.

### 8.3 The new build's side

`taskmaster serve --force-upgrade [--upgrade-timeout 120]` (`serve/upgrade.py`, `hand_over(root,
timeout)`), run when `claim_host` finds the lease held, or when it is free but `owner.lock` is held:

0. **Is anyone listening (review M8)?** If `ownership_held(root)` (the coordinator's `owner.lock`) is
   false, the lease is held by a serve process that runs no coordinator: 013's `serve export run` or
   014's `serve init` between steps. Any `discovery.json` on disk is then stale, and its port may be
   reused. Exit 69: "the host lease is held by a non-daemon serve process (export or init); wait for it
   to finish". Never send anything on a stale record.
1. Read the running owner's `discovery.json` through `_DiscoveryClient`, a private subclass of
   `Client` inside `serve/upgrade.py` (review M12). It skips `Client.__init__`'s `identify`, takes its
   identity from the record (root, store_id, schema, protocol, service_protocol), so the store is never
   opened, and it reuses `Client._discovery` and `_send` unchanged. Its only public method is
   `retire(proof)`, so this G3 bypass can't serve any other method. `client.py` is untouched.
2. Same build → exit 2 ("a daemon of this build already serves R"). Their build newer → exit 2 ("will
   not downgrade; stop it and start the older build deliberately").
3. Send `retire` with `force_upgrade = upgrade_proof(...)`. The operator token is read without creating
   it; missing or unsafe → exit 2.
4. `retiring` → poll `claim_host` every 100 ms until it succeeds; then start normally (§6.3 from step 2).
   `busy` → retry every 250 ms within the timeout. A refusal carrying 001's
   `SERVE_RETIRE_REFUSED` (a daemon from 001 to 008) → exit 69: "the running daemon predates
   --force-upgrade; stop it (`systemctl stop taskmaster`) and start this build". Any other refusal →
   exit 2 with its reason.
5. Out of time → exit 69: "build X is still draining after N s; retry, or stop it".
6. Lease free but `owner.lock` held by a stray session coordinator: the same retire, with no proof
   needed by the base policy. If it runs a pre-009 build, the base policy retires it once it is idle. If
   it runs a ≥009 build of the same release, the base policy refuses ("nothing to retire"), but its
   `dispatch` has met G3 and stopped (§8.2, M10). Either way, wait for `owner.lock` to be released
   within the timeout.

**Systemd stays `systemctl restart`.** systemd waits for the old process to exit before starting the new
one, so a supervised host never needs the flag. The flag is for a daemon run by hand (tmux, the Windows
dev host). It can't help a **wedged** daemon (review M9), which can't answer `retire`: kill that one,
and the lease is released on exit. After a hand-run takeover, the hand-run daemon holds the lease, so a
later `systemctl start` exits 69 and restarts until the hand-run one stops. 016's how-to says all three.

## 9. Upgrade and retire across builds

- **Teammates.** They reach the host's single build through remote MCP, so the tools have no client
  build skew. Their local plugin matters only for the fence and the hooks. Every build from 009 on
  refuses on a marker. 007's read endpoint is versioned on its own.
- **Older than 009 (F7).** Those builds don't know the marker. Their mitigations:
  - **Rollout rule:** every teammate runs ≥009 before the marker is committed (016's how-to; 014's
    `serve init` prints it).
  - **014's untrack:** with `.taskmaster/` gone from code branches, an old build sees no backlog and
    creates one only on an explicit `backlog_init`.
  - On the host, an old build's session that reaches the daemon's coordinator either runs on the same
    build (writes land in the team store, unattributed but not forked), or is a foreign build and gets
    refused by the retire fence.
- **Marker format.** A future format 2 makes 009 builds refuse with "update the plugin", fail closed.
- **Session coordinators** (solo, and stray ones on the host) keep their retire policy unchanged.

## 10. Operator commands (`taskmaster serve marker`)

Subcommands of `serve/cli.py`. They import only `server_marker`/`server_fence`, never uvicorn or the
server module (a packaging test checks this, like 001's `--help` rule).

```
taskmaster serve marker write --root R --url URL [--rotate-host-id]
taskmaster serve marker show  --root R [--json]
taskmaster serve marker clear-latch --root R
```

- `write`:
  - requires `R/.taskmaster/local/` (the host clone);
  - creates `host-id` if missing (or replaces it with `--rotate-host-id`), writes the marker and the
    latch, and prints `git add .taskmaster-server.json` plus the commit hint;
  - refuses an invalid URL (exit 2);
  - doesn't need the daemon stopped: a running daemon is exempt, and every other host process is fenced
    from its next call.
- `show`: the marker (or its error), the latch, whether `host-id` matches, whether the lease is held, and
  whether git tracks the marker (`git ls-files --error-unmatch`, best effort).
- `clear-latch`: refuses while a marker is present; otherwise deletes the latch. Exit 0 when there was
  nothing to delete.

## 11. Test suite and solo mode

- **Inert without a marker.** Every test root is a `tmp_path` with no marker and no latch, so `refusal`
  returns after two `lstat` calls. There is no conftest change to the store fixtures.
- **The repo itself.** A test asserts the taskmaster repo root carries no `.taskmaster-server.json`. A
  stray commit of one would otherwise fence every test that resolves to the repo.
- **Leases.** An autouse guard in `tests/conftest.py`, next to `_native_coordinator_isolation`, fails a
  test that leaves an entry in `server_fence._HOST_LEASES` and then releases it. In-process daemons make
  the test process exempt *for their root only*, so refusal tests run the refused party as a subprocess,
  or against a second root.
- **Latch writes** happen only in temporary git repos the fence tests create.
- **Solo unchanged.** The proving checks are the unchanged regression set (§16) and the full suite.
  One test counts filesystem calls on the no-marker path (monkeypatched `os.lstat`/`os.path.lexists`
  wrapper): exactly two, nothing created.

## 12. Migration for existing repos

- **Solo repos.** Nothing: no marker, no latch, no change.
- **Hosts already running a 001-008 daemon** (operator-only trials). Install ≥009, `systemctl restart`,
  then `serve marker write --url …`, commit, push. After that teammates pull, and their clones are
  fenced, latched, and sent to the remote MCP.
- **The operator's own solo store** on their laptop (the source 014 copies from) is fenced once they pull
  the marker. It isn't deleted; 014 decides whether to archive it.
- **Leaving server mode.** Remove the marker from the code branches. Each clone then runs `serve marker
  clear-latch` once (the latch text says so).

## 13. Seams and overlap

### 13.1 Interfaces other tasks use

| Task | Seam |
|---|---|
| **014** `serve init --from` | Runs inside `with server_fence.host_lease(target_root):`, so it may open the host clone's store even if the clone already has the marker, and no daemon can start meanwhile. Writes the marker with `server_marker.write_marker` and `server_fence.ensure_host_id` (the same functions as `serve marker write`). Commits the marker with its untrack flow on code branches. Needs no `store_id` re-minting, because the binding is `host_id`. Prints the ≥009 rollout rule. |
| **013** state export | The exporter runs in the daemon (exempt). `serve export run` claims `host_lease` like the daemon (the "same serve-ownership entry" its spec names). Export commits use `commit-tree` plumbing, which runs no git hooks; if 013 ever commits through porcelain, the pre-commit validator's marker branch (§7.3) would refuse staged `.taskmaster/` paths. |
| **007** read endpoint | 007 is implementing `taskmaster/server_marker.py` now with exactly the `read_marker` / `ServerConfigError` / `server_mode()` API below; 009's implementation extends that file in place (format checks, the §4.2 reading rules, the writer, the latch writer). 009 owns the file's design. `read_marker(root) -> Marker | None` and `ServerConfigError` are exactly the API 007 §13.2 asked for, with `mode` and `url` present. If 007 merges first with a minimal reader, 009 extends that file in place. 009 adds `server_marker.server_mode(root)`, which also honours the latch (returning the latched marker), and recommends that 007's `remote.locate` call it, so a latched old checkout reads remotely too. 009 doesn't touch the hooks or `root.py`. |
| **008** | None directly; through 007. |
| **005** allowlist | Both edit `ServeCoordinator` in `serve/daemon.py`. 009's `retire(requester, message)` sits behind 005's dispatch block unchanged (005 passes `retire` through to the coordinator for every principal). 009 rebases on 005 if 005 merges first. **Effect on 005's host-local operator (review M12):** 005 treats a host process with no origin as the operator (`OPERATOR_ONLY`: `git_run`, `git_status`, `git_recover`, `sync_status`, `receipt`, …). On a marker root, G3 refuses every non-daemon `Client`, so those methods are reachable only from inside the daemon, or over MCP with the operator token where a tool exists. That is the fence working. The one route that matters is managed-Git recovery: the daemon runs it itself at startup (`service.py:186`, `git.startup`). A manual `taskmaster git recover` on a marker host is refused. Managed Git doesn't run on a server host (addendum §8, 011), so a pin can only come from a pre-marker run. The route for that is to stop the daemon, remove the marker in the host clone's working tree, recover, then restore it. A lease-holding `serve git-recover` is a follow-up if it's ever needed. |
| **016** deploy | The how-to adds `serve marker write`, the ≥009 rollout rule, `systemctl restart` for upgrades, when `--force-upgrade` applies, that a wedged daemon is killed rather than force-upgraded, and that `systemctl start` loops on exit 69 while a hand-run daemon holds the lease (§8.3). The Linux lane runs `test_server_fence.py` and `test_serve_upgrade.py` (the `flock` path). |
| **003, 012** | No functional seam. |

### 13.2 Files 009 touches

| File | Change | Parallel lanes in the same file |
|---|---|---|
| `taskmaster/server_marker.py` (new) | marker and latch | 007 (only if it lands first; extend in place) |
| `taskmaster/server_fence.py` (new) | `refusal`, `check`, leases, `host-id` | — |
| `taskmaster/serve/upgrade.py` (new) | `hand_over`, `upgrade_proof` | — |
| `taskmaster/native_routing/gate.py` | G1b, `ServerModeUnavailable` | — (007 says it never touches it) |
| `taskmaster/store.py` | G2 in `open_store`, `read_only_status` | — |
| `taskmaster/coordinator/protocol.py` | G3 in `connect` | — |
| `taskmaster/coordinator/ownership.py` | lock-file name parameter on `Ownership` and `ownership_held` | 007 (`private_directory_at`): different functions, textual only |
| `taskmaster/coordinator/service.py` | `retire(requester, message=None)`; dispatch passes the message, and stops on `ServerModeRefused` | — |
| `taskmaster/coordinator/client.py` | none (`_DiscoveryClient` lives in `serve/upgrade.py`) | — |
| `taskmaster/coordinator/control_cli.py`, `git_cli.py`, `git_hook.py` | §7.3 rows | — |
| `taskmaster/native/cutover.py` | G4 | — |
| `taskmaster/backlog_server.py` | G1a in `_route_native`; `_guard_legacy_layout` catch; `_start_viewer_server` skip | 003 (`SESSION_ID`, far away), 012 (tool bodies): different hunks |
| `taskmaster/serve/daemon.py` | lease, marker binding, retire, retired exit | 005 (`dispatch`), 003, 007, 013 (`_start`): rebase, textual |
| `taskmaster/serve/cli.py`, `config.py`, `auth.py` | `--force-upgrade`, `--upgrade-timeout`, `marker` subcommands, add-user text; token digest helper | 007, 013 (`config.py`, `cli.py` parser): textual |
| `hooks/session-start.sh` | marker detection | — |
| `tests/conftest.py` | lease-leak guard | — |

## 14. Out of scope

- Refusing at the MCP server's startup, or announcing server mode in its `initialize` instructions.
  Per-call refusal plus session-start context is enough here; a follow-up can make the local server
  advertise the remote URL.
- Making the local `tm` server proxy to the remote one.
- Hooks' server-mode reads (007), merge gate fail-closed and stamps as commands (008).
- Root resolution for a non-git copy of a server-mode project: the marker is found only at the resolved
  root. A server-mode project is a git repository by definition.
- `store_id` re-minting on copy (014 may still want it, for receipts; the fence doesn't need it).
- Hostile same-user code on the host (§2 F8).

## 15. Rulings (decided 2026-11-01)

The design review (WARN: 0 Critical, 1 Important, 12 Minor, all folded in above) accepted Q1-Q5 as
recommended, and found that none of them opens a hole. They are recorded as decided:

- **Q1. Marker name and format.** *Decided: `.taskmaster-server.json`, JSON, keys `format`/`mode`/`url`/
  `host_id`.* It answers 007's Q2 the same way; the addendum's `.yaml` suggestion can't be parsed by the
  hooks.
- **Q2. The latch.** *Decided: ship it.* Without it, a teammate's `git checkout <old tag>` or a
  bisect across the migration silently reopens a local store from the tracked `.taskmaster/` of the old
  tree. Cost: one extra `lstat` per check and a `clear-latch` command for leaving server mode.
- **Q3. `host_id` binding.** *Decided: ship it, checked through `server_mode` so the latch counts (M1).* It stops `taskmaster serve` on the wrong clone,
  above all the operator's laptop, whose store shares its `store_id` with the host copy. Cost: one file
  in `local/serve/` and an exit-2 path.
- **Q4. `--force-upgrade` shape.** *Decided: the new build's `serve --force-upgrade` hands over from
  the old daemon,* with the operator-token HMAC proof and the base retire policy (no downgrades, idle
  only), the retired daemon exiting 0, and systemd hosts keeping `systemctl restart`. The alternative,
  "the flag only unlocks a retire from any newer client", lets an ordinary host session swap the daemon
  for a session coordinator. That is the F3 split the fence exists to stop.
- **Q5. Builds older than 009 (F7).** *Decided: a written rollout rule* (all teammates ≥009 before the
  marker is committed) *in 014's `serve init` output and 016's how-to,* and no code: an old build can't
  be made to read a file it doesn't know about.

Accepted gaps:

- Until 007 merges, a hook in the *host* clone can still read the team store read-only. Writes are fenced.
- **Leftover local stores and the hooks (review M2).** On the operator's laptop, or a teammate's clone
  that used solo mode before the switch, an old `store.db` remains. Until 007 (reads) and 008 (merge
  gate fail-closed, stamps as commands) land, `merge_gate_decide` and `edit_resurface` read that stale
  store, so the merge gate can allow or block on stale gates. `merge_recorder_stamp` also queues stamps
  into the local `.taskmaster/local/` that are never replayed anywhere useful. No data forks: every
  write is fenced. See 007 §12 and 008; no teammate key is issued before them (addendum §13).
- `serve add-user` can't check for an existing profile while the marker is present. The daemon still
  checks on first write.

## 16. Implementation plan (TDD)

Run every test with `C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest`, in the
foreground, from the worktree. Scratch goes under `D:\scratchbook\taskmaster\ts009\`. Each step is one
commit with its tests.

**Step 0. Baseline.** Run the §17 regression set on the branch tip and record the pass count in the
lane notes. Don't edit anything that is already red; report it.

**Step 1. `server_marker`.** Files: `taskmaster/server_marker.py` (extend 007's file in place if it has
merged; keep its `read_marker` / `ServerConfigError` / `server_mode` signatures and its tests green).
Tests first in `tests/test_server_marker.py`:
- `parse` accepts the §4.2 example; `render(parse(x)) == x` byte for byte; key order and whitespace
  variants render canonically;
- each validation rule refuses with a reason naming the field: newer format, extra key, wrong mode,
  userinfo/query/fragment, `http://` to a non-loopback host, a bad `host_id`, more than 4 KiB, non-UTF-8,
  not an object;
- `read_marker`: absent → `None`; a directory, a dangling symlink, or a symlink to a valid marker file
  → `ServerConfigError` (§4.2, M4); a 4 KiB + 1 file → `ServerConfigError` with at most 4097 bytes read
  (a spy on `os.read`); on POSIX, a FIFO at the path → `ServerConfigError` without blocking (the test
  has a 2 s timeout); the same cases for the latch, which still refuses with URL "unknown";
- `write_marker` is atomic (no temp file left; a failed validation writes nothing);
- the latch: written only when `<root>/.git` is a directory, by exclusive create, never raising
  (read-only dir); `server_mode(root)` returns the latched marker when the marker is absent; `clear_latch`;
- an import test: the module text contains no `from taskmaster`, `import yaml` or `import fastmcp`
  (the `test_root_module_imports_only_stdlib` pattern).

Proving check: the new file and `tests/test_edit_resurface_hook.py::test_root_module_imports_only_stdlib`.

**Step 2. `server_fence` core and the lease.** Files: `taskmaster/server_fence.py`,
`taskmaster/coordinator/ownership.py` (lock-name parameter on `Ownership` and `ownership_held`,
defaulting to `owner.lock`). Tests first in `tests/test_server_fence.py`:
- no marker, no latch → `None`, with exactly two filesystem probes once the repo root is cached (four
  for a subdirectory path), and no file created;
- `fence_roots`: the repo root for a path at it, a subdirectory, a linked worktree and a root-layout
  `backlog.yaml` directory; just the path outside git; cached (a second call spawns nothing and does no
  walk);
- a marker at the repo root refuses a subdirectory path;
- marker → the text names the URL; unreadable → the unreadable text; latch only → the latch text; the
  first refusal writes the latch in a git root;
- `claim_host` → `exempt` true for every spelling of the root (relative, trailing separator, different
  case on Windows), false for another root, false after `release`;
- a second `claim_host` in the same process raises `HostLeaseHeld` (after its 200 ms retry); a
  subprocess's `claim_host` raises while ours is held and succeeds after release; a probe
  (`ownership_held` on `host.lock`) running in a tight loop while `claim_host` runs doesn't make the
  claim fail (M11);
- **lease outlives no one but its holder (M7):** a subprocess claims the lease, starts a long-lived
  grandchild (`sleep`-like Python child), then exits; a third process's `claim_host` succeeds while the
  grandchild is still alive. This fails if the descriptor were inherited;
- **pid binding (M6):** after `claim_host`, monkeypatch `os.getpid` to another value → `exempt` is false;
  on POSIX, a real `os.fork()` child calls `refusal` on the marker root and reports a refusal;
- regression guard, not proof: a subprocess with `TASKMASTER_SERVE=1` and `TASKMASTER_ROOT` set is
  refused;
- `ensure_host_id` creates an owner-only 32-hex file by exclusive create, is idempotent, and
  `rotate=True` replaces it.

Proving check: the new file, `tests/test_native_service_ownership.py`, `tests/test_serve_auth.py`.

**Step 3. Choke points G1-G4.** Files: `gate.py`, `store.py`, `protocol.py`, `cutover.py`,
`backlog_server.py` (`_route_native`, `_guard_legacy_layout`). Tests first in
`tests/test_server_fence_choke.py`, each on a marker root:
- G1a: with `backlog_server.ROOT` at a teammate-style root (marker, no `.taskmaster/`), calling the
  wrapped `backlog_init` in-process returns the refusal, and no `.taskmaster/` (no `backlog.yaml`,
  `taskmaster.json`, `PROGRESS.md` or directories) appears (I1 case A). The same for
  `backlog_project_init` and a JSON-error tool's shape;
- G1b: a teammate-style root and a host-style root (marker plus a native fixture store), plus a
  root-layout `backlog.yaml` in a marker repo (the `LegacyLayoutError` branch). `gate.native_database`
  raises `ServerModeUnavailable`; `registry.route` returns `Error: …` for a text tool and
  `{"error": …}` for a JSON tool;
- subdirectory roots (I1 case B): `store.open_store(repo/sub/.taskmaster/backlog.yaml)` (the `explicit`
  resolution), `protocol.connect(repo/sub)` and `gate.native_database(repo/sub/.taskmaster)` all refuse
  on the marker at `repo/`, and nothing appears under `repo/sub/`;
- G2: `store.open_store` and `read_only_status` raise, and neither `store.db` nor `.taskmaster/local/`
  appears;
- G3: `protocol.connect` raises; `Client(root)` raises with `_launch` monkeypatched to fail the test if
  called; `Coordinator(root)` raises and `owner.lock` is never created;
- G4: `cutover.main(["dry-run", …])` exits 2 with the JSON refusal;
- with the lease held in-process, all four pass on the host-style root (exempt);
- the ratchet (§7.2, M3): every `sqlite3.connect(`, `Store(`, `Ownership(` and `private_directory(` call
  site in `taskmaster/`, `hooks/`, `scripts/` and `evals/` is in an allowlist naming its choke point or
  its exemption; an unlisted one fails the test;
- M10: a session `Coordinator` started on a root *before* its marker is written stops (`stopping` set)
  on the first message it gets afterwards, and `dispatch` raises `ServerModeRefused`.

Proving check: the new file, `test_native_gate_lookups.py`, `test_native_cutover*.py`, `test_native_service*.py`.

**Step 4. The MCP server end to end.** Tests first in `tests/test_server_fence_entrypoints.py`: a
subprocess stdio `backlog_server` (the existing subprocess-server helper the packaging tests use) in
(a) a teammate clone: `git init`, a marker, no `.taskmaster/`; (b) a host clone with a native store; and
(c) the subdirectory cases of I1: the same clone as (a), with the server started with cwd
`repo/sub/deeper` and no `TASKMASTER_ROOT`, and again with cwd elsewhere and `TASKMASTER_ROOT=repo/sub`.
- `backlog_init`, `backlog_status`, `backlog_list_tasks` and `backlog_open_viewer` each return the
  refusal;
- (a) has no `.taskmaster/` afterwards; in (c), neither `repo/sub/deeper/.taskmaster/` nor
  `repo/sub/.taskmaster/` exists, and no `.taskmaster/` exists anywhere under `repo/` (a recursive scan);
- in (b), the sha256 of `store.db`, `-wal` and `-shm` and the `.taskmaster/` file listing are unchanged;
- no viewer port is bound: the root's deterministic viewer port (`_project_port`) is still free while
  the server runs;
- the latch exists in (a) after the first call; then remove the marker (simulating an old checkout), and
  the next call returns the latch text.

Proving check: the new file and `test_packaging.py`.

**Step 5. CLIs and the pre-commit validator.** Files: `control_cli.py`, `git_cli.py`, `git_hook.py`,
`serve/cli.py` (add-user text). Tests first, extending `tests/test_server_fence_entrypoints.py`:
- `coordinator status` on a marker root prints `server-mode` with both lock states, and never opens the
  store (`sqlite3.connect` monkeypatched to fail);
- `coordinator stop` is refused with exit 1; `git status` is refused with exit 2;
- `git_hook.check` with a staged `.taskmaster/tasks/x.yaml` → `False` plus the server-mode text; with
  nothing staged → `True`; the store is never opened;
- `serve add-user` on a marker root prints "profile check skipped: server mode".

Proving check: `test_native_git_hook.py`, `test_native_git*.py`, `test_native_init.py`,
`test_serve_coordinator.py` (its `coordinator stop` cases), `test_serve_cli.py`.

**Step 6. `session-start.sh`.** Tests first in `tests/test_session_start_hook.py`: the marker at the
cwd, at an ancestor, and at `$TASKMASTER_ROOT` → the server-mode context; a marker beside
`.taskmaster/` → server mode wins; no marker → output byte-identical to today's (all three existing
cases). Proving check: that file and `test_hooks_json.py`.

**Step 7. The daemon's exemption.** Files: `serve/daemon.py`, `serve/config.py`. Tests first in
`tests/test_serve_daemon.py`, reusing its `served` fixture:
- the lease is claimed before any store open (a spy on `protocol.connect` sees `exempt` true on its
  first call);
- an unreadable marker → exit 2; a `host_id` mismatch or a missing `host-id` → exit 2 naming both ids;
  the same with the marker removed but the latch present (M1);
- no marker → the WARNING is logged and the ready line has `"marker": null`; with a marker, the ready
  line carries it;
- while an in-process daemon serves a marker root: an MCP tool call over HTTP works (exempt); a
  subprocess running `protocol.connect(root)` is refused; a subprocess `Client(root)` is refused; a
  second `taskmaster serve` subprocess on the same root exits 69 with the lease-held text;
- after `close()`, the lease is released and the registry is empty;
- `host_lease(root)` as a context manager works with no daemon (the 013/014 seam).

Proving check: `test_serve_daemon.py`, `test_serve_coordinator.py`, `test_serve_viewer.py`, `test_serve_cli.py`.

**Step 8. The retire fence.** Files: `service.py` (signature), `serve/daemon.py`, `serve/upgrade.py`
(`upgrade_proof`), `serve/auth.py` (token digest read without creating). Tests first in
`tests/test_serve_upgrade.py`, using the build-patching `owners` pattern of
`test_native_service_build.py`:
- no proof → refused with the new text (the existing `test_serve_coordinator_refuses_retire_from_any_build`
  still passes);
- a wrong proof, a proof for another nonce, a proof computed for a different requester build than the
  handshake carries, or a valid proof carrying an `origin` → refused, with a WARNING logged;
- a proof that isn't a 64-character lowercase hex `str` (non-ASCII text, bytes, an int, 63 or 65
  characters, uppercase) → refused as a bad proof, never a `TypeError` (M5);
- a valid proof: from a newer build and idle → `retiring`, and the daemon exits 0 logging "retired";
  busy → `busy`; same build or older → refused;
- a rotated operator token: an old-token proof is refused, a new-token proof accepted.

Proving check: the new file, `test_native_service_build.py`, `test_serve_coordinator.py`, `test_serve_allowlist*.py`
(once 005 has merged).

**Step 9. `--force-upgrade` end to end.** Files: `serve/upgrade.py` (`hand_over`), `serve/cli.py`,
`serve/config.py` (`force_upgrade`, `upgrade_timeout`). Tests first in `tests/test_serve_upgrade.py`:
- an old daemon (patched older build) in-process and a new `ServeDaemon(force_upgrade=True)` in a thread:
  the old one drains and exits 0, the new one prints its ready line, the lease moves, and
  `_DiscoveryClient` never opened the store (connect spy);
- `_DiscoveryClient` exposes only `retire` (a test asserts its public method set);
- the lease held by a `host_lease` holder with no coordinator (013/014 shape), plus a stale
  `discovery.json` naming a port where a test server listens → exit 69 with the export/init text, and
  nothing sent to that port (M8);
- the old daemon refuses with 001's text → exit 69 with guidance;
- same build → exit 2; old daemon newer → exit 2;
- busy beyond `--upgrade-timeout 1` → exit 69;
- lease free but `owner.lock` held by a session coordinator of an older build → it is retired, then the
  new daemon starts;
- lease free but `owner.lock` held by a same-build session coordinator that started before the marker →
  `hand_over`'s retire meets its G3 refusal, it stops, and the new daemon starts within the timeout (M10);
- POSIX only (skipped on Windows; 016's lane runs it): the same handoff across two real processes.

Proving check: the new file and Step 8's set.

**Step 10. `serve marker` commands.** Files: `serve/cli.py`. Tests first in
`tests/test_serve_marker_cli.py`:
- `write` creates `host-id` and the canonical marker plus the latch, and prints the `git add` hint;
  rerunning it is byte-identical; `--rotate-host-id` changes both; a bad URL → exit 2 and nothing
  written; no `.taskmaster/local/` → exit 2;
- `show --json` reports the fields in §10;
- `clear-latch` refuses while a marker is present, then removes the latch;
- `python -I taskmaster_cli.py serve marker show --help` doesn't import uvicorn or
  `taskmaster.backlog_server`.

Proving check: the new file and `test_packaging.py`.

**Step 11. Suite guards.** Files: `tests/conftest.py`. Tests:
- the lease-leak guard's check function, called directly, fails on a leaked lease and releases it (the
  suite doesn't use `pytester`);
- the repo root has no `.taskmaster-server.json`, and the repo's git dir has no `taskmaster-server.json`
  latch (a stray latch is invisible to `git status` and would fence every test that resolves to the repo).

Proving check: §17 in full.

**Step 12. Fresh-eyes check against this spec.** One reviewer, per the review policy, before the review
gate.

## 17. Regression set

Run in the foreground before Step 1 (baseline) and after Step 11:

- `tests/test_native_gate_lookups.py`, `tests/test_native_service*.py`, `tests/test_native_service_build.py`,
  `tests/test_native_schema_upgrade.py`, `tests/test_native_cutover*.py`, `tests/test_native_first_sync.py`
- `tests/test_serve_*.py` (daemon, coordinator, cli, config, auth, principals, sanitize, viewer, and
  allowlist once 005 has merged)
- `tests/test_session_start_hook.py`, `tests/test_hooks_json.py`, `tests/test_edit_resurface_hook.py`,
  `tests/test_merge_gate_decide.py`, `tests/test_merge_gate_hook.py`, `tests/test_merge_recorder_hook.py`
- `tests/test_packaging.py`, `tests/test_root_git_discovery.py`, `tests/test_client_agent.py`
- then the full suite (`-n 3`), which is the proof that solo mode is unchanged.
