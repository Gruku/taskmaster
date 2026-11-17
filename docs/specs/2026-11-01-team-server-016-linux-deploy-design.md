<!-- User intent: the task-level design and plan for team-server-016. The server path gets a real Linux test run,
     a deployable package and a tested how-to for the user's Hetzner host (systemd, Caddy, firewall, keys), so the
     first teammate key is issued onto a host whose settings were checked, not guessed. -->

# team-server-016: Linux lane, serve packaging and Hetzner deployment (design, 2026-11-01)

**Task.** `team-server-016`. Done when the server path's tests pass on Linux and a how-to covers the systemd
unit, the Caddy TLS config, the firewall and issuing API keys.
**Source.** `docs/specs/2026-10-31-team-server-mode-addendum.md` §13 (deployment profile, go-live order).
**Builds on.** 001 §2 (exit codes), §10 (unit and Caddy shape); 002 §7 D7 (`is_host_local`), §12a (notes for 016);
006 §4 D2 to D6 (deadlines, linger, proxy trust), §9 D11 (Caddy versus taskmaster); 005's design §5 (Linear token
name). Code at `748cd29` on `feat/team-collab` (001, 002 and 006 merged).
**Verified on this machine (2026-11-01).** See §3.1: the serve tests already run and pass on WSL Ubuntu 24.04.

## 1. Problem

Server mode is built and tested only on Windows. Three things stand between it and the Hetzner host:

1. **Linux has never run it.** The POSIX branches are unexercised: owner-only modes and `st_uid` checks
   (`ownership.py:98-125`, `principals.py`), `fcntl.flock` serialisation, the `O_NOFOLLOW`/`openat` walk in
   `serve/tracked.py:93-117`, SIGTERM delivery to a child (001 R3), and the symlink tests that skip without the
   Windows privilege.
2. **Nothing is packaged for a server.** `taskmaster_cli.py` is a plugin front door run by `uv run`; there is no
   console entry and no install layout. The package also finds its viewer assets and version through the checkout
   (`backlog_server.py:204` `SCRIPT_DIR`, `protocol.py:119` `declared_version`), so a plain wheel install would serve
   no viewer and report version `unknown`.
3. **The deployment settings are scattered and untested.** 001, 002 and 006 each left notes for 016. Several
   settings are security-relevant: a wrong one exposes the operator token's host-local guarantee, the loopback
   viewer, or the failure budget. None has met a real Caddy yet.

## 2. Scope

In scope:

- a Linux test lane that runs here (WSL), on any Linux box, and optionally in GitHub Actions;
- the `taskmaster-serve` console entry and the install layout;
- the shipped `deploy/` files (systemd unit, Caddyfile, environment example) and tests that keep them in step with
  the code's defaults;
- a smoke suite against a real Caddy, and a root rehearsal of the whole how-to in a throwaway WSL distro (Q1);
- the how-to, `docs/runbooks/team-server-deploy.md`: install, unit, Caddy, firewall, keys, backups, upgrades,
  rollback and the pre-go-live checklist.

Out of scope:

| Not here | Owner |
|---|---|
| Creating the host store from an existing project (`serve init --from`, the untrack flow) | 014 |
| The state export and its push (the backup) | 013 |
| The read endpoint path that Caddy must also pass | 007 |
| Refusing host-local sessions, `--force-upgrade` | 009 |
| Judging the exposed surface | 015 |
| A health endpoint, metrics, alerting | later; nothing unauthenticated exists today (001 D5) |
| Fixing non-serve failures the full Linux suite finds | bugs filed from the lane report (§3.4) |
| Multi-host or container deployment | not planned; §6.2 explains why Caddy stays on the host |

## 3. The Linux lane

### 3.1 What runs here

Probed on 2026-11-01 (not committed; logs in `D:\scratchbook\taskmaster\ts016\`):

- **WSL 2, Ubuntu 24.04.2, systemd 255 as PID 1, Python 3.12.3, uv 0.11.2, git.** `wsl -u root` gives root without
  a password; `sudo` inside the distro asks for one.
- **Docker Desktop is installed but its engine is stopped.** It isn't needed.
- **`/mnt/c` and `/mnt/d` are 9p drvfs mounts without `metadata`,** so `chmod` doesn't stick there. The lane must run
  on the distro's ext4 (`~`), or the owner-only checks fail for the wrong reason.
- **Result.** A clone of `748cd29` on ext4, `uv sync --frozen --extra dev`, then
  `pytest tests/test_serve_*.py tests/test_viewer_serve_hooks.py tests/test_packaging.py`:
  **481 passed, 1 skipped, in 3 min 40 s.** The skip is the Playwright login test (no `node_modules`). The POSIX
  mode, `flock`, `O_NOFOLLOW` and symlink tests that skip on Windows all ran and passed.
- The coordinator and native regression set (§3.3 tier `regression`) on the same clone: **339 passed, 2 skipped,
  in 5 min 45 s.** Both skips are Windows-only tests in `test_native_service_sync.py:230` (`_winapi`), which is
  correct on Linux.

So the lane is a script and a test list, not a porting effort.

### 3.2 The lane script

**D1. One script, `scripts/linux_lane.sh`, is the lane everywhere.** WSL, a plain Linux box, the Hetzner host
before go-live, and the optional GitHub workflow (Q2) all run it the same way.

```
bash scripts/linux_lane.sh [--source REPO] [--ref REF] [--tier serve|regression|deploy|full]...
                           [--python 3.11|3.12|PATH] [--dir DIR] [--log-dir DIR]
```

From Windows: `wsl -d Ubuntu -- bash scripts/linux_lane.sh --source /mnt/c/.../taskmaster --ref <sha>
--log-dir /mnt/d/scratchbook/taskmaster/<lane>`.

What it does:

- **It tests a commit, never a working tree.** It resolves `--ref` (default `HEAD` of `--source`, default the repo
  the script sits in) to a SHA and clones the repository's common git dir into `--dir/<sha12>-<epoch>`, then
  checks out that SHA detached. Uncommitted edits are not tested, and the report says which SHA was.
- **It refuses a directory where modes don't stick.** Before cloning it creates a file in `--dir`, `chmod 600`s it
  and reads the mode back. On a drvfs or 9p mount that fails, and it exits 2 with *"run the lane on a Linux
  filesystem (e.g. ~/...); /mnt/* ignores chmod"*. The default `--dir` is
  `${TASKMASTER_LANE_DIR:-$HOME/scratchbook/taskmaster/linux-lane}`. That is the user's scratchbook convention moved
  to ext4, because `D:` can't hold a POSIX-mode test. Logs and the JUnit XML are copied to `--log-dir`, which may
  be on `D:`.
- **The environment comes from the lock.** `uv sync --frozen --extra dev --python <spec>`. The default is the
  system `python3` (3.12 on Ubuntu 24.04, the same as the host, Q3). `--python 3.11` makes uv fetch 3.11 into the
  user's uv cache (no root), which checks the `requires-python` floor.
- **Every skip must be expected, or the lane fails** (review M1). Each tier runs with `--junitxml`. A small Python
  check reads the XML and fails the lane on any skip that isn't in an explicit allowlist in the script. The
  allowlist names each test by node id and pins its reason:
  - the Playwright login test, with reason "node or @playwright/test is not installed". CI runners have `node`
    but no `viewer/node_modules`;
  - the two Windows-only `_winapi` tests in `test_native_service_sync.py`.

  Matching reason text alone would let a future `reason="needs fcntl"` through, so the check doesn't rely on it.
- **Tests run serially by default.** The serve tests bind ports and spawn daemons, and the Windows memory notes
  (`test-suite-ram`) apply. `full` uses `-n 3`.
- Exit codes: 0 green; 1 a tier failed; 2 refused (bad arguments, wrong filesystem); 3 setup failed (clone, uv).
- It deletes nothing. Old run directories stay until the user clears them; the report prints their total size.

### 3.3 Tiers

| Tier | Runs | Root | Gate for 016 |
|---|---|---|---|
| `serve` | `tests/test_serve_*.py`, `tests/test_viewer_serve_hooks.py`, `tests/test_packaging.py`, `tests/test_deploy_files.py` (§5.4) | no | **yes** |
| `regression` | the coordinator and native files the daemon is built on: `tests/test_native_service*.py`, `test_identity_gate.py`, `test_team_identity.py`, `test_native_claims.py`, `test_native_routing_claims.py`, `test_bounded_run.py`, 009's `test_server_fence.py` once 009 lands (its `test_serve_upgrade.py`, including the
POSIX two-process handoff, is in `serve` by glob), and 006's viewer set (`test_server_api.py`, `test_server_etag.py`, `test_server_instructions.py`, `test_server_task_write.py`, `test_viewer_write_gates.py`, `test_api_notes.py`, `test_api_handover_status.py`, `test_native_routing_viewer.py`) | no | **yes** |
| `deploy` | `tests/test_serve_deploy_smoke.py` against a real Caddy and the shipped Caddyfile (§3.5), plus `systemd-analyze verify` and `systemd-analyze security --offline=yes` on the shipped unit | no | **yes** |
| `full` | the whole suite, `-n 3` | no | no: informational (§3.4) |
| rehearsal | `scripts/linux_rehearsal.sh`: the how-to's commands, as root, in a throwaway Ubuntu 24.04 WSL distro (§3.6, Q1) | yes | **yes** |

The tier lists live in the script, one array per tier. Lanes that add serve test files (003, 005, 007, 009, 012,
013) are picked up by the `test_serve_*.py` glob without an edit. A lane that adds a non-serve file the daemon
depends on adds it to `regression`.

### 3.4 The full suite on Linux

The full suite has, as far as the repo shows, never run on Linux. It will likely find Windows assumptions outside
the server path. 016 runs it once, reports pass, fail and skip counts with the failing node ids, and hands the list
to the lead for bugs. Fixing them is out of scope: the done criterion is the server path.

### 3.5 The real-Caddy smoke (`tier deploy`)

002 §12a and 006 §9 asked for proof against a real Caddy, not forged headers. `tests/test_serve_deploy_smoke.py`
skips unless `TASKMASTER_CADDY` names a Caddy binary; the lane sets it.

- **Caddy comes pinned.** The lane downloads one Caddy release's `linux_amd64` tarball from the project's GitHub
  releases into `--dir/cache/`, checks it against a SHA-512 **committed in the script** (not one fetched from the
  same place), and reuses it. Bumping Caddy is a one-line edit of version and hash. The how-to names the same
  version as the tested one.
- **It runs the shipped Caddyfile.** The test copies `deploy/caddy/Caddyfile` and makes these textual
  substitutions. Each is asserted to match exactly once, so drift in the shipped file fails the test:
  - the domain `tm.example.com` → `tm.localhost`;
  - the site's `tls { issuer acme … }` block → `tls internal`;
  - the global block gains `https_port <high port>`, `http_port <another high port>` (so a non-root Caddy starts
    even if it binds HTTP; whether it does is the rehearsal's `ss` check) and `skip_install_trust`;
  - `admin off` and everything else stay as shipped.
- **The setup matches production on all three D7 signals (review I1).** A `localhost` site would make the smoke
  vacuous:
  - `localhost` is in `HOST_LOCAL_NAMES` (`auth.py:253,265`) and in FastMCP's default allowed hosts;
  - a loopback client makes the forwarded peer loopback too.

  So:
  - **Host.** The site is `tm.localhost`, which Caddy certifies from its internal CA with no ACME, but which is
    neither a host-local name nor a FastMCP default. The client connects with SNI and `Host: tm.localhost` (an
    httpx transport pinned to the address, like `curl --resolve`). The test trusts Caddy's root from its data dir
    (`XDG_DATA_HOME` under `tmp_path`).
  - **Peer.** Caddy's high port listens on all interfaces (its default bind). The client connects to the
    machine's own non-loopback address (WSL's or the runner's `eth0`, found at test start; the test skips with a
    clear reason if there is none). So the forwarded peer, `scope["client"]`, is non-loopback, as in production.
- **It runs the real daemon** as a child (`taskmaster-serve --root <fixture> --port 0 --allowed-host
  tm.localhost:<https_port>`), with an in-process-made member key.

Cases. Each is a requirement from an earlier design, and each names the mutation that must make it fail:

| # | Request through Caddy | Expect | From | Fails under |
|---|---|---|---|---|
| 1 | MCP `initialize` + one tool call with a member key | 200; the tool answers | 001 | wrong upstream |
| 2 | the bootstrap token | 403 `…works only on the host…` | 002 D7 | `header_up -X-Forwarded-For` + `header_up Host 127.0.0.1` (all three D7 signals then read host-local) |
| 3 | the bootstrap token with `Connection: X-Forwarded-For`, then `Connection: Host`, then both | still 403; the daemon's log shows `Host` = `tm.localhost:<port>` and an XFF entry | 002 §12a hop-by-hop | a Caddy that honours the strip |
| 4 | the bootstrap token straight to `127.0.0.1:<port>` (no Caddy) | 200 | 002 D7 (the host path still works) | — |
| 5 | a bad key, 25 times, each with a **different** non-loopback spoof, `X-Forwarded-For: 203.0.113.<n>` | the 21st gets 429; the refusal log lines name the real source address, and no `203.0.113.` appears in the log | 006 D5, D6 | `trusted_proxies static private_ranges` (each spoof then gets a fresh budget, and the log shows them) |
| 6 | a `GET` SSE stream held open longer than Caddy's `idle` (the test sets `idle 3s`, holds 8 s) | the stream stays open and still delivers | 006 §9 (no Caddy body deadline) | `read_body 3s` |
| 7a | a declared `Content-Length` of 3 MiB | 413 from the daemon's guard before any body read; the guard logs the 413 (review I2) | 006 D3 | daemon cap raised to Caddy's |
| 7b | a chunked 3 MiB body | 413 from the daemon's running byte count; the client reads it, not a reset or 502 | 006 D3, D3a | — (recorded; see §6.2) |
| 8 | headers trickled past `read_header` | Caddy closes the connection | 006 D2 | `read_header` removed |
| 9 | a revoked key's POST with a 1 MiB body | the client reads 401, not a reset or 502 | 006 D3a, linger | `--linger-seconds 0` |
| 10 | `/rpc`, `/`, `/mcp/../x`, `/MCP/../x` and the viewer port's paths | 404 from Caddy's path allow-list | §6.2 | `@taskmaster` widened to `/*` |
| 11 | two requests 6 s apart on Caddy's pooled upstream connection | both succeed (no 502 from a connection uvicorn closed at 5 s) | §6.2 keep-alive | `keepalive 2m` |
| 12 | a member key with `Host: tm.localhost`, to a second daemon started **without** the matching `--allowed-host` | 421 | 001 §6 | — (it is the mutation) |

- Step 4 runs each "fails under" mutation once and records that the case failed.
- Case 7b depends on how Caddy and the daemon behave together when the daemon answers mid-body. If the client
  sees a reset or a 502 instead of the 413, that goes to R1 and §6.2 is revised; it doesn't silently pass.

The `systemd-analyze` part renders the shipped unit with the lane's paths, runs `verify` (syntax errors fail;
"user does not exist" warnings don't) and `security --offline=yes`, and fails above an exposure score of 2.0 (Q6).

### 3.6 The root rehearsal (`scripts/linux_rehearsal.sh`)

The smoke proves Caddy and the daemon together. It can't prove the unit's sandboxing, the service user, the file
ownership or the restart policy: those need root and a real systemd. The rehearsal runs the how-to's own commands
in order, as root, on a fresh distro:

- create the `taskmaster` user, the `/opt` release and the `/srv` clone (a fixture project made with the native
  test helpers, because 014 doesn't exist yet), the env file, the unit and Caddy (with `tm.localhost` and
  internal TLS, by the §3.5 substitutions);
- start both and run the §3.5 cases against them from the distro's non-loopback address, as in §3.5;
- check the properties only root can see:
  - `ss -ltnp` shows only Caddy (443) and SSH on non-loopback addresses, and nothing on port 80 (§6.3);
  - every file under `/srv/taskmaster` is owned by `taskmaster` and not group- or world-readable;
  - `/opt/taskmaster` is root-owned and not writable by `taskmaster`;
  - `systemd-analyze security taskmaster` meets the threshold;
  - `systemctl show -p Environment taskmaster` holds no secret;
- check the exit-code policy for real:
  - `systemctl stop` gives exit 0 and no restart;
  - a refused config (a bad `--allowed-host`) gives exit 2 and no restart loop. This is one of several exit-2
    paths; a broad `RuntimeError` at start also maps to 2 (§5.2);
  - `kill -9` restarts after 5 s;
  - holding port 7470 gives exit 69, restarts, then `failed` after the start limit.

It follows the how-to literally, so a step that fails here is a how-to bug.

**Where it runs (Q1, decided).**
- A throwaway WSL distro imported from the official Ubuntu 24.04 WSL image, the host's OS (Q3):
  `wsl --import ts016-rehearsal D:\wsl\ts016 <rootfs>`. The rootfs download and the distro go under `D:`, about
  1 GB in all.
- A fresh rootfs needs `[boot] systemd=true` in `/etc/wsl.conf` and a `wsl --terminate ts016-rehearsal` before
  `systemctl` works. The script does that first.
- The script runs inside it as root. When the run ends, pass or fail, logs are copied to
  `D:\scratchbook\taskmaster\ts016\rehearsal\`, and the distro is removed with `wsl --unregister ts016-rehearsal`.
  The removal touches only that distro.
- The user's own Ubuntu distro is never modified.

## 4. Packaging

**D2. The install is a git checkout of the lane-tested commit plus `uv sync --frozen`, not a wheel.**

- The checkout is by full SHA, because the repo has no release tags after `v4.2.0` (review M4).
- It is the SHA the lane reported green (checklist item 10), so what was tested is what runs.

- **Why not a wheel.** `SCRIPT_DIR` (`backlog_server.py:204`) serves the viewer from `<checkout>/viewer`, and
  `declared_version` reads `.claude-plugin/plugin.json` beside the package. A wheel has neither. Making both work
  from a wheel would change two shared modules for no gain on a single host.
- **Why editable is correct here.** `uv sync` installs the project editable (`uv.lock:1311`,
  `source = { editable = "." }`), so the package imports from the checkout and finds both. A non-editable install
  breaks both; the how-to says not to pass `--no-editable`.
- **Why `--frozen`.** The venv is exactly `uv.lock`, with hashes checked. No resolution happens on the host.

**D3. Releases live side by side; a symlink picks the live one.**

```
/opt/taskmaster/releases/7.5.0-1a2b3c4d5e6f/        root:root 0755, `git checkout --detach <full sha>`
/opt/taskmaster/releases/7.5.0-1a2b3c4d5e6f/.venv/  built by root: UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never
                                                     UV_LINK_MODE=copy uv sync --frozen; then python -m compileall
/opt/taskmaster/current -> releases/7.5.0-1a2b3c4d5e6f
```

- The directory name is `<version>-<sha12>`. The how-to checks that `git rev-parse HEAD` equals the tested SHA
  before building.
- `compileall` runs as root at build time because the service can't write `__pycache__` into a root-owned tree.
  Without it, every start compiles in memory. The build digest hashes `*.py` only (`protocol.py:96-104`), so the
  bytecode doesn't change it.

- **Why not upgrade in place.** The build digest is taken at import (`protocol.py:141-143`), but many modules
  import lazily. Checking out a new tag under a running daemon makes a later lazy import load new code into an old
  process: a mixed build the digest doesn't show. A new directory, a symlink switch and a restart never mix.
  Rollback is the same switch back.
- **Root owns the code.** The service user can't write it, so a compromised daemon can't change its own code.
- **The venv's Python is the distro's.**
  - A uv-managed Python lives under the installing user's home (`/root/.local/share/uv`). `ProtectHome=yes` hides
    that from the service, and the venv's interpreter link would dangle.
  - The distro Python also gets security updates through apt.
  - `UV_LINK_MODE=copy` keeps the release directory self-contained, rather than hard-linked into root's uv cache.
- **uv itself** is installed from a pinned release with its published checksum (the how-to has the commands). The
  host needs nothing else beyond git and the distro's Python (3.12 on Ubuntu 24.04).

**D4. A console entry: `[project.scripts] taskmaster-serve = "taskmaster.serve.cli:main"`.**

- 001 §1 assigned it to 016. It gives the unit and the operator one stable path,
  `/opt/taskmaster/current/.venv/bin/taskmaster-serve`.
- A console script puts the venv's `bin/` on `sys.path`, not the cwd. That matters because the unit's working
  directory is the served project, and that project may itself contain a `taskmaster/` package (the user's own
  repo does). `python -m taskmaster.serve` from that cwd would import the project's copy. The how-to never uses
  `-m` on the host for this reason.
- `cli.main(argv=None)` already returns an exit code, so the generated wrapper's `sys.exit(main())` keeps the
  documented codes.
- The name is scoped to the serve path. A general `taskmaster` entry would mean moving `taskmaster_cli.py`'s
  dispatcher into the package. That is more churn, and nothing on the host needs it.
- `pyproject.toml` is the only file that changes. The four PEP 723 headers carry dependencies, not scripts. The
  plan checks `uv lock --check`.

## 5. Deployment architecture

### 5.1 Layout

```
internet ──443/tcp──► Caddy (user caddy; on the host, not in a bridge container; admin API off)
                        │ path allow-list: /mcp, /mcp/*, + 007's read path
                        ▼
                      127.0.0.1:7470  taskmaster-serve (user taskmaster, systemd sandbox)
                        ├─ 127.0.0.1:7471 guarded viewer (not proxied until 015; SSH -L only)
                        └─ 127.0.0.1:<ephemeral> coordinator IPC (discovery token, owner-only)
SSH 22/tcp (keys only, ideally source-restricted) ── operator: CLI as the service user; viewer forward
port 80: closed (§6.3); ACME uses TLS-ALPN-01 on 443
outbound, at run time: Let's Encrypt (Caddy), api.linear.app (if Linear), the backup remote (013)
outbound, at build time: GitHub (the checkout, the uv release), PyPI (uv sync), Caddy's apt repository
```

- The build-time needs are why an egress filter belongs on the unit (`IPAddressDeny`, §5.2), never on the host
  (review M9).
- If 013 ever writes outside the project (for example a separate backup clone), `ReadWritePaths` grows, and the
  parity test "`ReadWritePaths` equals `--root`" changes with it.

| Path | Owner, mode | Holds |
|---|---|---|
| `/opt/taskmaster/releases/<v>/`, `current` | root, 0755 | code and venv (D3) |
| `/srv/taskmaster/` | taskmaster, 0700 | the service user's home: `.gitconfig`, `.ssh/` (the backup repository's push key for 013; a **read-only** deploy key for the code repository; a pinned `known_hosts`). No code-repository write credential lives on the host (§6.6) |
| `/srv/taskmaster/project/` | taskmaster, files 0600/0700 | the host clone; `.taskmaster/local/{serve,coordinator}/` hold the operator token, `principals.json`, `serve.log`, the discovery token, and the store |
| `/etc/taskmaster/taskmaster.env` | root, 0600 | `TASKMASTER_LINEAR_TOKEN` (005), if Linear is used |
| `/etc/systemd/system/taskmaster.service` | root, 0644 | the unit; no secrets |
| `/etc/caddy/Caddyfile` | root, 0644 | no secrets |

- The user is created with
  `useradd --system --home-dir /srv/taskmaster --create-home --shell /usr/sbin/nologin taskmaster`.
- The home directory then needs an explicit `chmod 0700 /srv/taskmaster`, because `useradd` uses `HOME_MODE` from
  `login.defs` (0750 on Ubuntu 24.04).
- The clone is made as that user under `umask 077`.

### 5.2 The systemd unit (`deploy/systemd/taskmaster.service`)

It extends 001 §10's shape. Each security-relevant line is in §6.1 with its reason.

```ini
[Unit]
Description=Taskmaster team server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=taskmaster
Group=taskmaster
WorkingDirectory=/srv/taskmaster/project
EnvironmentFile=-/etc/taskmaster/taskmaster.env
ExecStart=/opt/taskmaster/current/.venv/bin/taskmaster-serve --root /srv/taskmaster/project \
    --host 127.0.0.1 --port 7470 --allowed-host tm.example.com --viewer-port 7471
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=2
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
UMask=0077
LimitNOFILE=4096
MemoryMax=1G
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ReadWritePaths=/srv/taskmaster/project
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectProc=invisible
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# With no Linear and no backup push (013), also: IPAddressDeny=any / IPAddressAllow=localhost

[Install]
WantedBy=multi-user.target
```

The exit-code policy, from 001 §2 and `config.py:15`:

| Exit | Meaning | Unit behaviour |
|---|---|---|
| 0 | stopped after a signal | `Restart=on-failure` doesn't restart |
| 2 | refused configuration: bind, unsafe files, not a native store, FastMCP internals changed. `cli.py:111-114` maps **any** `RuntimeError` at start to 2, so a transient one ("can't start new thread" under `MemoryMax`) also lands here (review M10; proposed as a bug for a narrower exception) | `RestartPreventExitStatus=2`: no loop, and the unit shows `failed` until the operator acts |
| 69 | unavailable: port taken, store owned elsewhere | restart every 5 s, at most 5 starts in 300 s, then `failed` |
| 75 | coordinator lost | restart |
| signal or OOM kill | | restart |

- `KillMode=mixed` sends SIGTERM to the daemon alone, so it can drain (001 §9). Git children it is waiting on get
  SIGKILL only if the 30 s run out.
- The default `control-group` would SIGTERM a running export's `git` at the same moment as the daemon.

### 5.3 The Caddyfile (`deploy/caddy/Caddyfile`)

```
{
	admin off
	email ops@example.com
	auto_https disable_redirects
	servers {
		protocols h1 h2
		timeouts {
			read_header 10s
			idle 2m
		}
	}
}

tm.example.com {
	tls {
		issuer acme {
			disable_http_challenge
		}
	}
	request_body {
		max_size 4MiB
	}
	header Strict-Transport-Security "max-age=31536000"
	header -Server
	log {
		output file /var/log/caddy/taskmaster-access.log {
			roll_interval 24h
			roll_keep 14
			roll_keep_for 336h
		}
	}
	@taskmaster path /mcp /mcp/*
	handle @taskmaster {
		reverse_proxy 127.0.0.1:7470 {
			transport http {
				keepalive 4s
			}
		}
	}
	handle {
		respond 404
	}
}

# The viewer site goes live only after the 015 review (addendum §13). Then: add the block below and
# --viewer-origin https://viewer.tm.example.com to the unit.
# viewer.tm.example.com { tls { issuer acme { disable_http_challenge } } request_body { max_size 4MiB }
#                         header Strict-Transport-Security "max-age=31536000" reverse_proxy 127.0.0.1:7471 }
```

- **Port 80 is closed (review I3).**
  - `disable_http_challenge` leaves ACME to TLS-ALPN-01 on 443, and `disable_redirects` drops the HTTP→HTTPS
    redirect server, so Caddy has no reason to listen on 80. The firewall closes 80 too (§6.3).
  - A client misconfigured with `http://` then fails at TCP connect, before it sends its key in cleartext. With
    a redirect it would already have sent the key by the time it saw the 308.
  - If the pinned Caddy still binds :80 regardless, the firewall is what holds. The rehearsal's `ss` check and the
    live "port 80 refuses" check show which.
- **The body cap is 4 MiB, twice the daemon's 2 MiB (review I2).**
  - Caddy's `request_body` only wraps the body reader (`http.MaxBytesReader`). It doesn't pre-check
    `Content-Length`, and mid-proxy its overflow surfaces as a 502, not a 413 (`statusError` in
    `reverseproxy.go`).
  - With the cap above the daemon's, the daemon's own 413 (006 D3) answers first for chunked bodies. For a declared
    oversize body (and a revoked key's large POST) the daemon answers before reading, then drains only
    `--linger-bytes` (default 64 KiB); Caddy is still sending and answers 502. **Found by smoke 7a (2026-11-07): the unit
    therefore sets `--linger-bytes 4194304`, Caddy's cap**, and a parity test keeps it at or above that cap.
  - Caddy's cap is defence in depth: it bounds what Caddy reads if the daemon's flag is ever raised. Raise both
    together; a parity test keeps Caddy's above the daemon's.
- **Access logs:** `roll_interval 24h` with `roll_keep 14` bounds retention to about 14 days. `roll_keep_for` prunes
  only when a roll happens (review M5). The `deploy` tier runs `caddy validate` on a copy of the shipped file in
  which only the log path is rewritten to `tmp_path`, because validate opens the log file and `/var/log/caddy` is
  root-only. The test asserts that this is the only substitution. A pinned version that lacks `roll_interval`
  fails there. The fallback would be a small `roll_size`, with the bound stated
  as size × count.
- 007's read endpoint path joins `@taskmaster` when 007 lands. This is the seam: 007's design names the path, and
  016's smoke case 10 fails until the matcher lists it.
- **Installing Caddy.**
  - The how-to installs it from Caddy's own signed apt repository, at the version the lane tested, then runs
    `apt-mark hold caddy`, so a routine `apt upgrade` can't move it (review M7). Ubuntu 24.04's archive carries
    2.6.2.
  - A Caddy upgrade means: bump the lane's pin, re-run tier `deploy`, then unhold, upgrade and hold again.
  - The packaged `caddy.service`'s `ExecReload` uses the admin API, so with `admin off`, `systemctl reload caddy`
    fails. Always `systemctl restart caddy`.

### 5.4 Keeping the files honest: `tests/test_deploy_files.py`

The unit and the Caddyfile repeat numbers the code owns. Parity tests run on Windows and Linux alike:

- The unit's `ExecStart` arguments parse with the real `serve` argument parser (`cli.run_main`'s parser, factored
  out so it can be called without starting anything). An unknown or renamed flag fails.
- `--host` is loopback; `--insecure-bind` is absent; `--port` equals `DEFAULT_PORT`; and `--viewer-port` equals
  `DEFAULT_VIEWER_PORT`.
- `RestartPreventExitStatus` equals `EXIT_REFUSED`. `TimeoutStopSec` exceeds `GRACEFUL_SECONDS` plus 15.
- `ReadWritePaths` equals `--root`, and `UMask` is `0077`.
- No `Environment=` line exists; secrets go through `EnvironmentFile=`.
- Caddy's `max_size` is above `DEFAULT_MAX_BODY_BYTES` (the daemon answers first, §5.3), and `read_header` equals
  `DEFAULT_HEADER_TIMEOUT`.
- The upstream `keepalive` is below `KEEP_ALIVE_SECONDS`.
- `admin off`, `protocols h1 h2`, `auto_https disable_redirects` and `disable_http_challenge` are present.
- The unit's `--allowed-host` equals the Caddyfile's site address (review M3). The two placeholders change
  together, or every request gets 421.
- Forbidden, matched as case-insensitive regexes on non-comment lines, not as substrings (review M2):
  - `trusted_proxies`, `client_ip_headers`, `log_credentials` and `encode`;
  - `(header_up|request_header)\s+[-+]?(host|x-forwarded-[a-z]+|forwarded|x-real-ip)\b`, which also catches the
    removal forms such as `header_up -X-Forwarded-For`;
  - any `reverse_proxy` to port 7471.
- The how-to: every `deploy/…` path it names exists, and every `taskmaster-serve …` command line in it parses.
  The how-to points at the files and doesn't paste them, so it can't drift from them.

## 6. Security-relevant settings

### 6.1 The host and the unit

| Setting | Value | Why | Checked by |
|---|---|---|---|
| Service user | `taskmaster`, system, `nologin`, home `/srv/taskmaster` 0700 | Owner-only checks (`ownership.py:110,124`) need one owner. Other local users can't read the store, the token or the keys | rehearsal |
| Code ownership | root, read-only to the service (D3) | A compromised daemon can't rewrite its own code | rehearsal |
| `--host` | `127.0.0.1`; never `--insecure-bind` | Only Caddy faces the network; plaintext bearer tokens never leave the host (001 §6) | parity, rehearsal `ss` |
| `--allowed-host` | exactly the public domain, the same as the Caddyfile's site | Keeps the 421 DNS-rebinding guard on (001 §6) | parity (both files), smokes 1 and 12 |
| Secrets | `EnvironmentFile=` root 0600; no `Environment=` | Unit files are world-readable, and `systemctl show` prints `Environment=` | parity, rehearsal |
| `FORWARDED_ALLOW_IPS` | never set | Ignored anyway (006 D5 pins trust), but its presence would signal a misread | rehearsal |
| `UMask=0077` | | Files the daemon creates are owner-only from birth | parity, rehearsal |
| Capabilities | none; `NoNewPrivileges` | Binds high ports; needs no privilege | `systemd-analyze security` |
| `ProtectSystem=strict` + `ReadWritePaths` = the clone | | The only writable path is the project; `/opt`, `/etc` and the rest are read-only | rehearsal |
| `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `ProtectProc=invisible`, the kernel and namespace protections, `SystemCallFilter=@system-service` | | They shrink what a code-execution bug in a dependency can reach. `ProtectProc=invisible` hides other users' processes; claim PID liveness only concerns host-local sessions, which 009 refuses | `systemd-analyze security` ≤ threshold; rehearsal runs the smoke under them |
| `MemoryDenyWriteExecute=yes` | | Blocks W+X memory. CPython, pydantic-core and SQLite need none; ctypes callbacks are used only on Windows (`tracked.py:138`). The rehearsal proves it; if it fails, the line goes and the reason is recorded | rehearsal |
| `MemoryMax=1G`, `LimitNOFILE=4096` | | A leak or flood can't starve Caddy or SSH. EMFILE under a flood is a transient 401 (002 D6), not a lockout | rehearsal |
| `KillMode=mixed`, `TimeoutStopSec=30` | | The daemon drains (001 §9) before its children are killed | rehearsal stop |
| Restart policy | §5.2 table | Exit 2 must not loop; 69 must eventually stop | rehearsal |
| Admin CLI | always `sudo -u taskmaster …` under `umask 077` | Run as root, `private_directory` refuses (`st_uid` check), or creates root-owned files the daemon then refuses (002 §12a) | how-to; rehearsal runs `add-user` this way |
| SSH viewer forward | `ssh -L 7471:127.0.0.1:7471`, same local port | The allowed viewer origins are `http://127.0.0.1:<viewer_port>` and `http://localhost:<viewer_port>` (`config.py:93-96`); another local port gets 421 | how-to |

### 6.2 Caddy

| Setting | Value | Why | Checked by |
|---|---|---|---|
| Placement | on the host; never a bridge-network container | A bridge peer isn't in `TRUSTED_PROXIES`: `X-Forwarded-For` would be ignored, and every teammate would share one failure budget. Don't widen the trust to fit (006 §9) | how-to |
| `admin off` | | The admin API listens on `localhost:2019` by default. Any local process, the service user included, could otherwise rewrite routes: publish 7471, or add `trusted_proxies`. Changes then take `systemctl restart caddy` | parity |
| `protocols h1 h2` | | No HTTP/3, so UDP 443 stays closed, and the header deadline below applies to every protocol in use | parity, firewall |
| `read_header 10s` | equals `--header-timeout` | Caddy is the whole-header deadline on the public path (006 D2, G6) | parity, smoke 8 |
| `idle 2m`; no `read_body` or write timeout | | Go's read and write deadlines can cut long SSE streams. The body deadline is taskmaster's (006 D4) | smoke 6 |
| `request_body max_size 4MiB` | above `--max-body-bytes` (2 MiB), in MiB not MB | **The daemon's D3 check is the primary limit** (review I2). Caddy only wraps the body reader: there is no `Content-Length` pre-check, and a mid-proxy overflow is a 502. With Caddy's cap higher the daemon's 413 answers first, provided the unit's `--linger-bytes` covers Caddy's cap (else a declared oversize body gets Caddy's 502). Caddy's cap is defence in depth, bounding what Caddy reads if the daemon's flag is raised | parity, smokes 7a and 7b |
| No port 80: `disable_http_challenge`, `auto_https disable_redirects` | | A client misconfigured with `http://` fails at connect instead of sending its key in cleartext before a redirect (review I3). ACME uses TLS-ALPN-01 on 443 | parity, rehearsal `ss`, live check |
| Path allow-list | `/mcp`, `/mcp/*`, + 007's path; everything else `respond 404` | Fewer paths reach the daemon; scanners don't spend the failure budget or fill `serve.log`. The daemon still denies by default (001 D5) | smoke 10 |
| Upstream `127.0.0.1:7470` | an IPv4 literal | `localhost` may resolve to `::1`, where nothing listens | parity |
| Upstream `keepalive 4s` | below uvicorn's 5 s (`KEEP_ALIVE_SECONDS`) | Otherwise Caddy reuses a connection uvicorn just closed, and a POST tool call gets a 502 | parity, smoke 11 |
| No `trusted_proxies`, no `client_ip_headers` | | Caddy then replaces a client-sent `X-Forwarded-For` with the real peer (006 D5) | parity, smoke 5 |
| No `header_up`/`request_header` on `Host`, `X-Forwarded-*`, `Forwarded` or `X-Real-IP`, including removals | | Rewriting `Host` to loopback, or stripping XFF, removes one of D7's three signals (002 R6). Caddy's source removes hop-by-hop headers before it adds its own XFF; add `header_up X-Forwarded-For {remote_host}` only if smoke 3 shows otherwise on the pinned version | parity (regex), smokes 2-3 |
| No `log_credentials` | | It would write every `Authorization` header, so every key, to the access log (002 §12a) | parity |
| Access log | on; Caddy redacts `Authorization` and `Cookie`; `roll_interval 24h`, `roll_keep 14` | Peers and paths for incident review. IP addresses are personal data, so retention is bounded by time, not only by size (review M5) | parity, `caddy validate` in tier `deploy` |
| `apt-mark hold caddy`; restart, never reload | | The tested version stays the running one; `reload` needs the admin API, which is off | how-to, rehearsal |
| No `encode` | | Compression can buffer SSE (006 §9) | parity |
| HSTS, `-Server` | | Standard; no version banner | smoke 1 |
| Viewer site | absent until 015 passes; then `viewer.<domain>` plus `--viewer-origin` | Addendum §13 go-live order; 006 D10.9 | how-to, parity (no live `7471` proxy) |

### 6.3 Firewall

| Rule | Why |
|---|---|
| Default deny inbound, IPv4 and IPv6 (`ufw default deny incoming`, `IPV6=yes`) | Hetzner hosts get a public /64 as well as IPv4 |
| Allow 443/tcp only; **80/tcp stays closed** | HTTPS, and ACME TLS-ALPN-01 (§5.3). An open 80 with a redirect would let an `http://` client send its key in cleartext before seeing the redirect (review I3) |
| Allow 22/tcp, from the operator's addresses when they are stable | Operator access and the viewer forward. Keys only (`PasswordAuthentication no`) |
| No 443/udp | HTTP/3 is off |
| Never open 7470, 7471 or the coordinator port | They bind loopback anyway; the rehearsal's `ss` check is the real proof |
| Mirror the same rules in the Hetzner Cloud Firewall attached to the VM (Q3) | A second layer that a host misconfiguration can't remove |
| No Docker port publishing on this host | Docker writes iptables rules that bypass ufw |

### 6.4 Keys, rotation and backups

- **Issuing.** The operator runs the command over SSH themselves:
  `sudo -u taskmaster /opt/taskmaster/current/.venv/bin/taskmaster-serve add-user ann --root /srv/taskmaster/project`.
  The key prints once (002 §3) and goes to Ann over a private channel (a password manager share, not chat). When
  an agent runs the command, it adds `--key-file`, so the key never enters a transcript.
- **The teammate's side.** `.mcp.json` carries `"Authorization": "Bearer ${TASKMASTER_TOKEN}"`, and the key lives
  in the teammate's environment or OS secret store, never in a committed file. Codex uses its streamable-HTTP
  bearer-token-from-env setting. The how-to gives both, checked against each client's docs at implementation.
- **The URL must be `https://`** (review I3). Port 80 is closed, so an `http://` URL fails to connect. The how-to
  says: if a key was ever configured with an `http://` URL, against this host or any other, treat it as exposed
  and `rotate-user` it.
- **The operator's laptop** gets its own key (`add-user <me> --role operator`). The bootstrap token never leaves
  the host (002 D7); `serve token` is only for host-local checks.
- **Rotation and revocation.**
  - `rotate-user` when a key may have leaked: the old key fails on the next request.
  - `revoke-user` when someone leaves: their sessions end within 2 s (002 D6).
  - `list-users` and `journalctl -u taskmaster` are the audit trail.
- **Backups (013).**
  - The backup is 013's state export, committed and pushed to a Git remote. It also carries uploaded documents
    (`external_documents`), because after 012 the upload is their only copy.
  - The host's only push credential is a deploy key in `/srv/taskmaster/.ssh/`, with a pinned `known_hosts`, scoped
    to the backup repository. Decided as a recommendation to 013 (Q5): a dedicated backup repository, so a
    compromised host can't push to the team's code repository.
  - The code repository gets a second, read-only deploy key, for the host clone's `clone` and `pull`. The host
    holds no write credential for it (§6.6).
  - `principals.json` and `operator.token` are **not** backed up, by design (002 D3): a restore must not bring
    back revoked keys.
  - Neither is 009's `local/serve/host-id`. A host rebuilt without it runs
    `serve marker write --rotate-host-id --url <url>`. The operator commits and pushes the new marker from their
    laptop (§6.6); the host can't push to the code repository.
- **Restores.**
  - A restored Hetzner snapshot does bring back revoked keys, so the how-to's restore section ends with
    `rotate-user` for every active handle and `serve token --rotate`.
  - A restore drill (rebuild from 013's export onto a scratch host) is a go-live item once 013 defines the restore.

### 6.5 Upgrades and rollback

- **Upgrade.**
  1. Run the lane on the new SHA. It must be green.
  2. Build the new release directory (D3) for that SHA while the old one runs.
  3. Switch `current`, then `systemctl restart taskmaster`.
  4. Check the journal's ready line for the new `build`.
- **The lane never runs on the production host** (review M8):
  - as root, the owner-only checks behave differently and the lane writes under `/root`;
  - as `taskmaster`, its test daemons and stores land beside the live one.

  It runs in WSL, in CI (Q2), or in the throwaway rehearsal distro.
- **A supervised host never needs `--force-upgrade`** (009 §8).
  - Under systemd the upgrade is a restart, because the old daemon exits first.
  - `taskmaster serve --force-upgrade` is for a daemon run by hand, or a wedged unit that won't stop. It is run
    on the host, as the service user, with the new build.
  - Against a daemon older than 009 it exits 69 ("stop it and start this build"), so use `systemctl stop`.
- Clients reconnect after a restart, and their open MCP sessions are lost. Claims follow addendum §7.
- **Rollback.** Switch `current` back and restart. 001 §10a and 002 §11 hold for task-level rollback.

### 6.6 The server marker and the rollout rule (009)

The how-to carries 009's operator steps:

- **The host never holds a push credential for the team's code repository** (re-review R-I1). Its only push
  credential is the backup repository's deploy key (Q5). Its code-repository access is a separate, **read-only**
  deploy key, used to clone and `git pull`. So the marker is written on the host but committed elsewhere:
  1. **Write it on the host.** Run `sudo -u taskmaster … taskmaster-serve marker write --root
     /srv/taskmaster/project --url https://tm.example.com/mcp`. `serve marker show` reports its state.
  2. **Copy it to the operator's laptop.** Use `scp` of `.taskmaster-server.json`, or the JSON from
     `serve marker show --json`.
  3. **Check it.** The `host_id` and URL must match what the host printed.
  4. **Commit and push from the laptop,** after the rollout check below.
  5. **`git pull` on the host clone** with the read-only key. `serve marker show` then reports it tracked.
- **014 is a seam.** 014's `serve init --from` "writes and commits" the marker and runs the untrack flow on code
  branches. On the host it can commit locally, but it must not push. Getting those commits to the code
  repository is the operator's laptop's job, as above. 014's design must say so.
- The marker's URL uses the same domain placeholder as the unit and the Caddyfile.
- **The rollout rule (009 design, F7 and its Q5).** Builds older than 009 don't know the marker, so they would open a local store
  in a teammate's clone. **Every teammate must be on a build that includes 009 before the marker is committed.**
  - The how-to puts this as a check before the commit step: each teammate reports their plugin version.
  - 014's `serve init` prints the same rule.
- **Leaving server mode.** Remove the marker from the code branches. Each clone then runs
  `serve marker clear-latch --root <clone>` once.
- **Rebuilding a host without `local/serve/`** gives a new `host-id`, so `marker write --rotate-host-id` on the
  host, then commit and push from the laptop as above (§6.4).

## 7. Pre-go-live checklist (the how-to's last section)

**Blockers.** No teammate key is issued until every one is true:

The blocking tasks were decided on 2026-11-01 (Q4).

*Security:*

1. **005** (the allowlist; in review now) is merged.
2. **006** (transport hardening) is merged. Done: `748cd29`.
3. **015's security review** passed, with its Critical and Important findings closed. 015 gets this design and
   the how-to as input.

*Function:*

4. **003**, per-connection sessions, is merged. Until then every teammate shares one claim session and one agent
   name (001 §11).
5. **007**, the hooks' read endpoint, is merged, and its path is in Caddy's `@taskmaster` matcher (smoke case 10).
6. **008**, the fail-closed merge gate, is merged. Teammates' hooks run in every session.
7. **009**, the split-brain fence, is merged. Until then a teammate's clone can write to a local store unnoticed.
   Before the marker is committed, every teammate has confirmed a plugin build that includes 009 (§6.6).
8. **013**, backups, is merged:
   - the export pushes to the dedicated backup repository with its repo-scoped deploy key (Q5);
   - one restore was drilled onto a scratch host.
9. **014** is merged, and the host store was created with `serve init --from`. There is no manual path.

*016's own:*

10. **The lane is green on the exact commit being deployed**: tiers `serve`, `regression` and `deploy`.
11. **The root rehearsal passed** in the throwaway Ubuntu 24.04 distro (Q1), on that same commit.

**Checks on the live host:**

- `ss -ltnp`: only Caddy (443) and sshd are on public addresses, and nothing listens on 80. Ports 7470, 7471 and
  the coordinator port are on 127.0.0.1.
- `systemd-analyze security taskmaster` is at or under the threshold.
- `systemctl show -p Environment taskmaster` is empty.
- The journal's ready line shows the expected `build`, and `server_root` equals `root`.
- From outside:
  - a TCP connect to port 80 is refused or times out, from outside the Hetzner network (I3);
  - the certificate is valid;
  - `/` and `/rpc` give 404;
  - `/mcp` with no key gives 401;
  - a member key completes a tool call;
  - after `revoke-user`, that key gets 401.
- On the host, through the public path, so the bootstrap token never leaves the host (review M6):
  - `curl --resolve <domain>:443:<public IP> -H "Authorization: Bearer $(sudo -u taskmaster … serve token
    --root …)" https://<domain>/mcp` gives 403;
  - the peer is then the public IP and `Host` is the domain, as for a remote caller.
- `serve marker show` reports the marker as committed, with the right URL and `host_id` (§6.6).
- The SSH viewer forward signs in with a member key, and the bootstrap token signs in there too, because that is
  host-local.
- Housekeeping:
  - time sync is active;
  - unattended security upgrades are on;
  - SSH password authentication is off;
  - the Hetzner Cloud Firewall mirrors ufw;
  - the box runs nothing but taskmaster, Caddy and sshd: no Docker, no other login users (Q3).

## 8. Seams with the parallel lanes

| Lane | Touches the same files? | Seam |
|---|---|---|
| 003 sessions | no | none for deploy; a go-live blocker (§7 item 4) |
| 005 allowlist | no (005 doesn't edit `pyproject.toml`) | the unit's `EnvironmentFile` carries `TASKMASTER_LINEAR_TOKEN` (005 design §5); no `--linear-token-env` flag. The lane's `serve` tier picks up 005's `test_serve_allowlist*.py` |
| 007 read endpoint | maybe `pyproject.toml`, headers and `uv.lock`, if it adds a dependency | its path joins Caddy's `@taskmaster` matcher and smoke case 10. If both touch `pyproject.toml`, it's a one-line merge: 016 adds `[project.scripts]` only |
| 009 split-brain | yes: `serve/cli.py` (`--force-upgrade`, `--upgrade-timeout`, `marker` subcommands) | 016 only factors out the parser (§5.4); whichever lands second rebases, textually. The how-to carries 009's marker steps, rollout rule and `--force-upgrade` guidance (§6.5, §6.6). The lane runs `test_server_fence.py` (`regression`) and `test_serve_upgrade.py` (`serve`, including the POSIX handoff). `add-user`'s read-only profile check opens the store; 009's fence must allow that read-only connect, or accept the CLI's "could not read the store" note. If 009 adds flags to `serve`, the unit's parity test parses them for free |
| 012 content import | no | none |
| 013 state export | no | the deploy key in `/srv/taskmaster/.ssh/`, outbound access (why `IPAddressDeny` is optional), Q5's backup repository, uploaded documents in the backup (from 012), the restore drill, and `ReadWritePaths` if 013 writes outside the project |
| 014 `serve init` | no (not started) | the how-to's "create the host store" section is a placeholder that points at 014. 014 commits the marker and the untrack flow on the host but never pushes: the host has only a read-only code-repository key, so the operator pushes from their laptop (§6.6) |
| 015 security review | n/a | gets §6 and the how-to; the threshold in Q6 is its to tighten |

Files 016 adds or changes:

- **New:** `scripts/linux_lane.sh`, `scripts/linux_rehearsal.sh`, `deploy/systemd/taskmaster.service`,
  `deploy/caddy/Caddyfile`, `deploy/taskmaster.env.example`, `tests/test_deploy_files.py`,
  `tests/test_serve_deploy_smoke.py`, `docs/runbooks/team-server-deploy.md`, `.gitattributes`, and
  `.github/workflows/linux-serve.yml` (Q2).
- **Changed:** `pyproject.toml` (`[project.scripts]`); `taskmaster/serve/cli.py` (factor out the argument parser
  into `build_parser()`; no behaviour change); `tests/test_packaging.py` (the console entry test).

## 9. Decisions and open questions

Rulings of 2026-11-01 (user, through the lead) settle Q1 to Q6. None is open.

- **Q1. Where does the root rehearsal run? Decided: a throwaway WSL distro.**
  - It is created with `wsl --import ts016-rehearsal D:\wsl\ts016 <rootfs>` from the official Ubuntu 24.04 WSL
    image, the same OS as the host (Q3).
  - It takes about 1 GB on `D:` and is removed afterwards with `wsl --unregister ts016-rehearsal`.
  - Nothing in the user's own Ubuntu distro changes. The rehearsal installs software as root inside the throwaway
    distro only (Caddy from its apt repository); the ruling covers that.
- **Q2. A GitHub Actions workflow? Decided: yes.**
  - `.github/workflows/linux-serve.yml` runs `linux_lane.sh --tier serve --tier regression --tier deploy` on
    `ubuntu-24.04`, for pushes and PRs that touch `taskmaster/**`, `deploy/**`, `tests/**`, `scripts/linux_lane.sh`,
    `pyproject.toml` or `uv.lock`.
  - The repository is public, so: `permissions: contents: read`, every action pinned by full SHA, no secrets, and
    no `pull_request_target`.
  - It doesn't run until the user pushes. The local WSL lane stays the source of truth.
- **Q3. Host facts. Decided: a Hetzner Cloud VM running Ubuntu 24.04, dedicated to taskmaster.**
  - The production Python is the distro's 3.12, which is what the venv uses (D3). The lane's `--python 3.11` run
    stays as a check of the `requires-python` floor only.
  - The outer firewall is the Hetzner Cloud Firewall (§6.3).
  - No other services, Docker or other login users are on the box. The checklist verifies it.
  - The domain isn't chosen. The shipped files and the how-to keep the placeholder `tm.example.com`, and the
    how-to's first step replaces it in the unit (`--allowed-host`), the Caddyfile and the marker URL. A parity test
    keeps the unit and the Caddyfile equal (§5.4).
- **Q4. Blockers for the first teammate key. Decided: 005, 006 and 015, plus 003, 007, 008, 009, 013 and 014.**
  §7 lists them all as blockers.
- **Q5. Where 013's backup lands. Decided, as a recommendation to 013:** a dedicated private backup repository with
  a repo-scoped deploy key, not the code repository's `taskmaster-state` branch, so a compromised host can push
  only backups. 013 owns the export and the push; 016's how-to documents this setup.
- **Q6. Thresholds. Decided: `systemd-analyze security` exposure 2.0 or lower ("OK"), and `MemoryMax=1G`.**
  - 015 can tighten either.
  - If `MemoryDenyWriteExecute` or `ProtectProc` breaks something in the rehearsal, that line is dropped and the
    reason recorded, rather than the threshold raised.

## 10. Risks

- **R1. Caddy behaviour assumed from its source.** The review read Caddy's master-branch source, not the pinned
  version. The assumed behaviours, and the smoke case that tests each one:

  | Behaviour | Smoke |
  |---|---|
  | Hop-by-hop headers are removed before Caddy adds its own `X-Forwarded-For` | 3 |
  | An untrusted peer's XFF is replaced, and `Host` passes through unchanged | 2, 3, 5 |
  | `idle` doesn't cut an active SSE response | 6 |
  | `request_body` is only a body-reader wrap with no `Content-Length` pre-check, and a mid-proxy overflow is a 502 (why Caddy's cap sits above the daemon's) | 7a, 7b |
  | The path matcher cleans and lowercases before matching | 10 |
  | Pooled upstream connections close before uvicorn's keep-alive | 11 |
  | The pinned version accepts `roll_interval` | `caddy validate` |
  | Nothing binds :80 with the HTTP challenge and the redirects off | rehearsal `ss` |

  If one fails on the pinned version, the Caddyfile changes (for example
  `header_up X-Forwarded-For {remote_host}`) and the design notes it.
- **R2. The full suite on Linux may be noisy.** It is informational and kept apart from the server-path gate
  (§3.4).
- **R3. WSL isn't Hetzner.** The kernel (5.15 WSL) and the network differ. The lane proves the code. The
  rehearsal proves the configuration on the same OS release and systemd as the host. Only the live-host checks in
  §7 cover what differs: the Hetzner network, the Cloud Firewall, real ACME and the real domain.
- **R4. `systemd-analyze security --offline` versus the live score.** They can differ slightly. The rehearsal and
  checklist read the live one.
- **R5. Network during the lane.** It needs the Caddy download once (cached and hash-pinned), PyPI through uv
  (cached), and, for `--python 3.11`, uv's Python download once. With an empty cache and no network the `deploy`
  tier fails at setup (exit 3), never as a test failure.

## 11. Implementation plan

The steps run in this order. Every step is committed separately on `feature/team-server-016`. Tests use the repo
venv on Windows (`C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python -m pytest`, from the worktree,
serially, `--basetemp` under `D:\scratchbook\taskmaster\ts016\`). The Linux lane runs as
`wsl -d Ubuntu -- bash scripts/linux_lane.sh …` with `--log-dir /mnt/d/scratchbook/taskmaster/ts016/lane`.

| Step | Files | What | Check that proves it |
|---|---|---|---|
| 0 | `.gitattributes` | `*.sh text eol=lf`, `deploy/** text eol=lf` | `git ls-files --eol scripts/*.sh deploy` shows `i/lf w/lf` on a fresh Windows checkout; `bash` runs the script from `/mnt/c` without `$'\r'` errors |
| 1 | `scripts/linux_lane.sh` | §3.2: clone by SHA, the ext4 guard, `uv sync --frozen`, tiers `serve` and `regression`, the JUnit skip check, logs | On this branch's tip, from Windows: `--tier serve --tier regression` green, with only the allowlisted skips. Repeat with `--python 3.11`. Negative checks (local edits, not committed): `--dir /mnt/d/...` exits 2; a hand-added `skipif(True, reason="needs fcntl")` test makes the lane fail |
| 2 | `pyproject.toml`, `tests/test_packaging.py` | D4 console entry; a test that the entry's target imports to a callable and that `taskmaster-serve --help` never imports `taskmaster.backlog_server` (the same probe as `test_serve_help_never_imports_the_server`) | Windows: `test_packaging.py` passes, and `uv lock --check` is clean. Linux lane: after `uv sync`, `.venv/bin/taskmaster-serve --help` exits 0 (a lane assertion), and the `serve` tier is green |
| 3 | `taskmaster/serve/cli.py`, `deploy/systemd/taskmaster.service`, `deploy/caddy/Caddyfile`, `deploy/taskmaster.env.example`, `tests/test_deploy_files.py` | `build_parser()` factored out of `run_main` (no behaviour change); the shipped files (§5.2, §5.3); the parity tests (§5.4) | Windows and Linux: the new tests and `tests/test_serve_cli.py` pass. Mutation checks, local and never committed: `max_size 2MB`, an added `trusted_proxies`, `header_up -X-Forwarded-For`, `request_header Host 127.0.0.1`, `--insecure-bind` in `ExecStart`, a renamed flag, a domain changed in only one file, and a removed `disable_http_challenge` each fail a named test |
| 4 | `tests/test_serve_deploy_smoke.py`, `scripts/linux_lane.sh` (tier `deploy`) | §3.5: the pinned Caddy fetch with its committed SHA-512; the rendered Caddyfile with `tm.localhost`; the client on the non-loopback address; cases 1-12; `caddy validate` on the shipped file; `systemd-analyze verify` and `security --offline` | The lane's `--tier deploy` is green. Then each case's "fails under" mutation (§3.5) is rendered once, and the named case is shown to fail. These include `trusted_proxies static private_ranges` for case 5, the I1 check, and `read_body 3s` for case 6. If case 3 or 7b doesn't behave as expected, R1's response applies and the design is revised |
| 5 | `scripts/linux_rehearsal.sh` | §3.6: import the throwaway Ubuntu 24.04 distro, run as root, copy logs, unregister the distro | It runs green as root in the throwaway distro, and `wsl -l` no longer lists it afterwards. The exit-code cases behave as in §5.2's table. The `ss`, mode and ownership checks pass |
| 6 | `docs/runbooks/team-server-deploy.md` | The how-to: one "replace `tm.example.com` in both files" step; §5 install with D3's commands (checkout by SHA, `compileall`, `apt-mark hold caddy`); the unit; Caddy; firewall (§6.3, port 80 closed); keys (§6.4, `https://` only, rotate any key ever used over `http://`); backups and restore (013, uploads included); upgrade, rollback and `--force-upgrade` (§6.5); the marker and the rollout rule (§6.6); the viewer forward; the store (014); and the §7 checklist. Every command is either run by the rehearsal or marked *not rehearsed* | `test_deploy_files.py`'s how-to checks pass. The rehearsal script's command list and the how-to's agree (a test compares the commands the how-to fences as `rehearsed` with the script's) |
| 7 | `.github/workflows/linux-serve.yml`, `tests/test_deploy_files.py` | Q2: the workflow runs the lane's `serve`, `regression` and `deploy` tiers on `ubuntu-24.04`, with `permissions: contents: read`, actions pinned by full SHA, no secrets and no `pull_request_target` | A test in `test_deploy_files.py` parses the workflow and asserts each of those properties: every `uses:` has a 40-hex ref, the top-level `permissions` is exactly `contents: read`, and no `secrets.`, `pull_request_target` or `write` appears. `actionlint` also runs if available. It doesn't run on GitHub until pushed, and pushing is the user's call |
| 8 | (no files) | The lane's `full` tier once, on the final tip | A report to the lead: pass, fail and skip counts, and the failing node ids, for bug filing. Not a gate |

**Before the review gate:**

- the Windows serve set plus `test_packaging.py` and `test_deploy_files.py`;
- the lane's `serve`, `regression` and `deploy` tiers on the final tip, on 3.12 and 3.11;
- the rehearsal in the throwaway distro.

Logs go under `D:\scratchbook\taskmaster\ts016\`.
