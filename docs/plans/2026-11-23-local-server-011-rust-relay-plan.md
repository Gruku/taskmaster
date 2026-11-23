<!-- User intent: the build plan for local-server-011 (the Rust tm relay), split into a few Sonnet lanes against one fixed contract, with the gates that must pass before .mcp.json points at bin/tm-relay and 7.7.0 ships. -->
# local-server-011: the Rust tm relay, implementation plan

Spec: docs/specs/2026-11-23-local-server-011-rust-relay.md (rev 3, 8cfcb36c; design review 1 and re-reviews 1-2 resolved, `D:\scratchbook\taskmaster\relay\spec-review.md`). Rulings: the spec's R1-R10 (R1-R6 from docs/handoffs/2026-11-22-local-server-lanes/rulings.md). Parent spec and plan: docs/specs/2026-11-22-local-server-design.md, docs/plans/2026-11-22-local-server-plan.md (its §0 contract is unchanged by this work). Branch `feat/rust-relay` (taskmaster repo, private: never pushed), worktree `D:\scratchbook\taskmaster\wt-relay`. Lane worktrees `D:\scratchbook\taskmaster\wt-relay-<lane>` on branches `relay/<lane>` off feat/rust-relay. Resume files `docs/handoffs/2026-11-22-local-server-lanes/relay-<lane>.md`, reviews `.../reviews/local-server-011-<lane>-review.md`. The orchestrator merges lanes one at a time (`git merge --no-ff`) into feat/rust-relay after each lane's review gate, and feat/rust-relay into feat/local-server after the switch (gate G5).

Scratch: `D:\scratchbook\taskmaster\relay` (pytest basetemps `relay\basetemp-<lane>`, logs, probes); `CARGO_TARGET_DIR=D:\scratchbook\taskmaster\relay\target` for every cargo build in every lane (cargo serializes on its own lock); `TASKMASTER_RELAY_VERIFY_DIR=D:\scratchbook\taskmaster\relay\verify` (spec §6 says `relay-verify`; kept under the one scratch folder instead). A lane deletes its basetemps when it finishes.

## 0. The contract (fixed here so L1, L2 and L3 can build in parallel)

**Step 0 (orchestrator, inline, before any lane forks):**

1. Merge `feat/local-server` (3dfd9fd6, the 7.6.0 release content) into feat/rust-relay. Checked: `tm_bridge.py`, `.mcp.json`, `hooks/`, `tests/test_tm_bridge*.py`, the runbook and `scripts/` are unchanged between 846b1687 and 3dfd9fd6, so the spec's line references hold; the merge brings the regenerated handshake, the 7.6.0 versions and the README local-server section.
2. `.gitattributes`: `bin/tm-relay text eol=lf`, `bin/tm-relay-* binary`, `bin/*.exe binary`, `bin/relay-manifest.json text eol=lf` (this checkout has core.autocrlf=true, so the rules land before any file under bin/ exists).
3. (moved to L1 as its first commit, which L1 reports at once; L2 merges that commit before its cargo tests read the file) `tests/fixtures/relay_vectors.json` (schema below), with `constants` and `cwd` filled from tm_bridge.py at that commit (a throwaway script in scratch, not committed), `sse` cases produced by `Bridge.events`, and hand-written `chunked` cases.

Commit step 0 as one commit; every lane forks from it.

**Ownership.** Each file has one owning lane; no other lane edits it. A lane that needs a change in another lane's file says so in its resume file and to the orchestrator.

| Lane | Owns |
|---|---|
| L1 | `tm_bridge.py`, `tests/test_relay_blackbox.py`, `tests/fake_local_server.py`, `tests/test_relay_helper.py`, `tests/test_relay_vectors.py`, `tests/test_relay_parity.py`, `tests/fixtures/relay_parity.json`, the Python side of `relay_vectors.json` (may append cases, never change one) |
| L2 | `relay/**` (crate, `Cargo.lock`, `rust-toolchain.toml`), `scripts/build_relay.py`, `bin/tm-relay.exe`, `bin/tm-relay-linux-*`, `bin/relay-manifest.json`, `tests/test_relay_packaging.py` |
| L3 | `bin/tm-relay` (the sh entry), `hooks/session-start.sh`, `tests/test_relay_entry.py`, `tests/test_relay_self_heal.py`, `tests/fixtures/relay_standin/**`, `docs/runbooks/release-packaging.md` |
| S (orchestrator) | `.mcp.json`, `tests/test_tm_bridge.py::test_mcp_json_launches_the_bridge_through_uv_without_a_project` (replaced), the runbook's entry-point table row, README's local-server section |

**Helper protocol v1** is spec §3 and §3.1 verbatim: `tm_bridge.py --relay-helper` (line 1 `session`, then for `local` line 2 `endpoint` or `unavailable`, exit 0), `--relay-helper --ensure-only` (no line 1; one `{"op":"ensure"}` line in, one `endpoint`/`unavailable` line out, exit on stdin EOF), `"v": 1` on every line, `TASKMASTER_BRIDGE_DISCRIMINATOR` from the relay used as the retire requester, stderr inherited (notes keep the `tm-bridge: ` prefix), the token only on the helper's stdout pipe. The relay closes the helper's stdin after the last line it needs and kills a helper still alive 5 s later (`tm-bridge: relay helper did not exit; stopped`). A helper that exits before the awaited line or prints `v` ≠ 1 counts as ServerUnavailable (`tm-bridge: relay helper exited (code N) before answering`).

**Test-mode environment.** Honoured only when `TASKMASTER_BRIDGE_TESTING=1`, by both relays and the helper; with any other value every variable below is ignored (a black-box test asserts it for both relays, because in production an environment variable must not redirect what the relay spawns).

| Variable | Value | Who reads it |
|---|---|---|
| `TASKMASTER_RELAY_HELPER` | JSON argv replacing `uv run --no-project --python ">=3.11" python <plugin>/tm_bridge.py`; the relay appends `--relay-helper` [`--ensure-only`] | Rust (spec §3) |
| `TASKMASTER_BRIDGE_TEST_SERVER_ARGV` | JSON argv passed to `launch_server(server_argv=...)` (the fake launcher) | helper and Python bridge (spec §7.1) |
| `TASKMASTER_BRIDGE_TEST_BOUNDS` | JSON object, constant name → seconds (a list for `GET_BACKOFF`). Allowed names: `SHORT_TIMEOUT`, `LIST_HOLD`, `HOLD_BOUND`, `RECONNECT_BUDGET`, `FALLBACK_WINDOW`, `GET_BACKOFF`, `LAUNCH_BOUND`, `DRAIN_WAIT`, `SUCCESSOR_WAIT`, `INIT_WAIT`. Each implementation uses the names it has and ignores the rest of the list; a name outside the list exits at startup with `tm-bridge: unknown test bound <name>` | all (spec §7.1) |
| `TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV` | JSON argv replacing `uv run <plugin>/backlog_server.py` (plan addition, so most fallback tests use a fast stdio stand-in; one test keeps the real uv path, skipped without uv) | Rust and Python bridge |
| `TASKMASTER_BRIDGE_TEST_DELEGATE_ARGV` | JSON argv replacing `uv run --no-project --python ">=3.11" python <plugin>/tm_bridge.py` for the team and `TASKMASTER_RELAY=python` delegate (plan addition) | Rust |

**Relay surface** (Rust, pinned for L1's tests and L3's entry):

- Plugin dir = the parent of the directory that holds the running executable (`current_exe`, canonicalized). It finds `tm_bridge.py`, `tm_bridge_handshake.json` and `backlog_server.py` there. No override.
- `tm-relay --version` prints one line `tm-relay <crate version> <source_digest or "unknown">` to stdout, exits 0, never reads stdin and never spawns anything.
- `TASKMASTER_RELAY=python`: no helper; the relay starts the delegate argv at once and pumps both ways for the whole session (nothing replayed: initialize has not arrived yet).
- Env names and texts as spec §2: `TASKMASTER_BRIDGE_LOG`, the `tm-bridge: ` prefix, `X-Taskmaster-*` headers, the error texts pinned by `mods/taskmaster-mods/tests/fixtures/bridge-text.ts`.

**`relay_vectors.json` schema:** `{"format": 1, "constants": {NAME: number | [number] | [string]}, "init_wait": 2.0, "cwd": [[path, encoded]], "sse": [{"chunks": [str], "events": [object]}], "chunked": [{"raw": str, "body": str} | {"raw": str, "error": str}]}`. `constants` holds every name in spec §7.2 (CONNECT_TIMEOUT … GET non-200 cap); INIT_WAIT is relay-only and sits on its own key.

## Lanes

Every lane: Sonnet builder, briefed from this plan alone plus the spec sections it names; Python from the main checkout's venv with the worktree first on the path (`PYTHONPATH=<worktree> C:\Users\gruku\Files\Claude\taskmaster\.venv\Scripts\python.exe -m pytest ...`, because the editable install otherwise imports the main checkout's code); tests in the foreground with `--basetemp D:\scratchbook\taskmaster\relay\basetemp-<lane>`; subprocesses in tests through `taskmaster.bounded_run.run_bounded` (a plain `subprocess.run(timeout=)` can hang on Windows); every relay, helper, fake server and stand-in a test starts is stopped by PID in teardown (a running `tm-relay.exe` also blocks the next rebuild); any `claude -p` run is `env -u ANTHROPIC_API_KEY claude -p --model haiku`. Each new file gets a 1-3 line user-intent header. A lane ends with its work committed on its branch, the resume file written, and a one-line report.

### L1 - Python helper mode, black-box suite, vectors and parity (Sonnet; the test-heavy lane first)

Goal: the Python half of the contract and the one suite both relays must pass. Spec §2 (rows 1-15, 29, 34, 35 stay here), §3, §3.1, §7.1, §7.2, §7.5.

1. `tm_bridge.py`: the `--relay-helper` and `--relay-helper --ensure-only` modes over the existing functions (`_start_team_bridge` detection without starting it, `fallback_reason`, `ensure_server`, the build identity); `TASKMASTER_BRIDGE_DISCRIMINATOR`; the test-mode variables above for the Python bridge and helper. Bound: about 150 added lines, no change to the full-mode bridge's behaviour (every existing `tests/test_tm_bridge*.py` passes unchanged).
2. `tests/fake_local_server.py`: standalone fake launcher/server (claims the lease, serves the FakeServer contract, writes the result file and discovery, behaviour switches from a control file), spec §7.1.
3. `tests/test_relay_blackbox.py`: parametrized over `relay` ∈ {python, rust}; rust resolves `bin/tm-relay.exe` or `bin/tm-relay-<os>-<arch>` and skips with a reason when absent, fails when `TASKMASTER_REQUIRE_RELAY=1`. Every case of spec §7.1, both relays unless marked Rust-only (helper lifetime M2, reconnect episode M3, team-clone forwarding and the late-line-1 note, `TASKMASTER_RELAY=python` delegation), plus the test-mode-ignored-without-TESTING case. The Rust-only cases are written now and skip until L2's binary exists. Wording assertions read `bridge-text.ts`. "uv missing" runs with a PATH that has no uv and no helper override.
4. `tests/test_relay_helper.py`: helper lines for each mode and reason (local, team, each fallback reason, exception → fallback with `local server unavailable: <repr>`), `--ensure-only` with two ops, exit after the last line.
5. `tests/test_relay_vectors.py`: `constants` equal tm_bridge's module constants; `cwd` vs `encode_cwd`; `sse` vs `Bridge.events`; `chunked` against `http.client` on a raw socket.
6. `tests/fixtures/relay_parity.json` + `tests/test_relay_parity.py` per spec §7.5 (rows parsed from the spec's §2 table, every entry names a test parametrized over both relays, ast-located sha256 pin over the Python Bridge's duplicated functions, failure text says to port the fix to `relay/` and re-pin).
Done when: the new tests pass with rust skipped; `tests/test_tm_bridge*.py`, `tests/test_packaging.py` and the import-set pin pass; a targeted run of the bridge-adjacent tiers passes. Touches nothing in `relay/`, `bin/`, `hooks/`, `.mcp.json`, docs.

### L2 - Rust relay crate, build script, packaging checks (Sonnet; phase A parallel, phase B after L1 merges)

Goal: `bin/tm-relay.exe` and `bin/tm-relay-linux-*` implementing spec §2 rows 5, 16-28, 30-33 (Rust column), §3 (relay side), §3.1, §4.2 (GUI subsystem, static CRT), §4.3, §6. Port `tm_bridge.py`'s `Bridge` (lines 542-1238) as the source of truth for behaviour and wording.

Phase A (parallel with L1 and L3; cargo and targeted pytest only):
1. `relay/`: `Cargo.toml` (dependency `serde_json` only; release profile per §6), `Cargo.lock`, `rust-toolchain.toml` (1.93.1, minimal, the three targets), modules `main`, `relay`, `http` (HTTP/1.1, Content-Length and chunked, every resolved address in order, `[::1]` URLs), `sse`, `helper`, `delegate`, `spawn` (the one hidden-spawn function: CREATE_NO_WINDOW, `uv.exe` PATH search per §3 M5), `log`; `HANDLE_FLAG_INHERIT` cleared on the std handles at startup (§4.3, M4); `TM_RELAY_SOURCE_DIGEST` read with `option_env!`.
2. cargo tests: vectors (`include_str!` of `tests/fixtures/relay_vectors.json`: constants, cwd, sse, chunked), the spawn function sets CREATE_NO_WINDOW, a spawned child holds no handle to the relay's stdin pipe, helper-line parsing (v ≠ 1, missing line), retry classification.
3. `scripts/build_relay.py` (stdlib): build, `--check`, `--verify` exactly as §6 (remaps, `/Brepro`, `--locked`, crt-static, manifest with source digest, rustc, RUSTFLAGS per target, sha256 and size; `--verify` from a `git archive` of `relay/` at HEAD into `TASKMASTER_RELAY_VERIFY_DIR`). Targets: `x86_64-pc-windows-msvc`, `x86_64-unknown-linux-musl`, and `aarch64-unknown-linux-musl` after `rustup target add aarch64-unknown-linux-musl` (a toolchain download; if it is refused, ship x86_64 only and record it: aarch64 Linux then takes the sh entry's Python path).
4. `tests/test_relay_packaging.py`: manifest sha256s equal the committed files; `build_relay.py --check` passes; `--version` prints the manifest's digest; no absolute path outside the remapped prefixes (§6 pattern, URL look-behind); PE subsystem 2 and no VCRUNTIME import; binaries git mode 100755.
Phase B (after L1 merges; L2 merges feat/rust-relay into its branch and is then the test-heavy lane): `pytest tests/test_relay_blackbox.py` green for both relays with `TASKMASTER_REQUIRE_RELAY=1`; L1's assertions are the contract, so a test L2 thinks is wrong goes to the orchestrator with the reason, and is not edited quietly.
Bounds: each binary ≤ 1 MB (spec estimate 400-700 KB); idle private bytes ≤ 5 MB measured once by the lane with the spike's `D:\scratchbook\taskmaster\relay\scripts\measure.py` (the acceptance number is G3's). Done when phase B is green, cargo tests pass, `build_relay.py --verify` passes from a clean commit, and the binaries plus manifest are committed by `build_relay.py` (never hand-copied).

### L3 - sh entry, SessionStart self-heal, runbook (Sonnet; parallel with L1 and L2, light tests)

Goal: spec §4.1 (the entry as written there), §6 (D5 self-heal, rev 3 rules), §7.3 (entry and self-heal tests), the runbook steps.
1. `bin/tm-relay`: the §4.1 script, LF, `#!/bin/sh`, git mode 100755 (`git update-index --chmod=+x`).
2. `hooks/session-start.sh` (bash, `set -euo pipefail`: the new block must not abort the hook on a failing probe): **dormant unless `$CLAUDE_PLUGIN_ROOT/.mcp.json` points the tm entry at `bin/tm-relay`** (a plain grep; before S merges, and if G4 fails and S never merges, the block returns at once and never runs the exe, so no SessionStart pays an exe start, an antivirus scan or an unchecked cursor for a relay nothing uses; a test pins both states); then a Windows-only (`uname` MINGW/MSYS) check of `$CLAUDE_PLUGIN_ROOT/bin/tm-relay.exe`; rename to `tm-relay.exe.blocked` with the §6 notice only on a definite start failure (sh exit 126, or error text with permission denied / not executable / exec format / bad image); everything else leaves the exe and appends one line to `.taskmaster/local/serve/relay-heal.log` (only when that directory exists; the hook never creates a store layout); bound via `/usr/bin/timeout` by absolute path, else the check runs in the background. Decide from the damaged-file test whether Git sh's exit 127 on a non-PE `.exe` (spec §4.2 side note) also counts as a definite failure, and record the decision. Bound: ≤ 40 added lines; the healthy path adds ≤ 100 ms to SessionStart (measure 10 runs, record in the resume file).
3. `tests/fixtures/relay_standin/`: a std-only Rust stand-in executable whose behaviour an env var picks (print a version line, sleep N s, print odd output, exit code N), built by the tests once into `CARGO_TARGET_DIR` (skipped with a reason when cargo is absent). Stand-in cases also use a text file named `tm-relay.exe` (damaged) and a copy with a deny-execute ACE (`icacls`, removed in teardown).
4. `tests/test_relay_entry.py` and `tests/test_relay_self_heal.py`: every §7.3 case for the entry (Git sh on Windows, sh in the Linux lane) and the self-heal (renames: denied, damaged; leaves: runnable, slow past the bound, odd output, non-zero exit after start, a stand-in `timeout` on PATH ahead of `/usr/bin/timeout`).
5. `docs/runbooks/release-packaging.md`: step 2 appended per §6 (`build_relay.py --check`; on failure build, `--verify`, the Defender scan with its exit codes, commit `relay/`, `bin/`, the manifest with the bump); a "Relay binaries" subsection (provenance, `--verify`, the self-heal, the `TASKMASTER_RELAY=python` lever, the one-line rollback, macOS on the Python path). The entry-point table row is S's, not L3's.
Done when the new tests pass on Windows and `tests/test_hooks_json.py`, `tests/test_session_start_hook.py` and `tests/test_local_hooks.py` pass. Touches nothing in `tm_bridge.py`, `relay/`, the binaries or `.mcp.json`.

### S - the switch (orchestrator, its own commit and branch)

On `relay/switch` off feat/rust-relay once L1-L3 are merged and G2 has passed: `.mcp.json` tm entry becomes `{"command": "${CLAUDE_PLUGIN_ROOT}/bin/tm-relay"}` with no args; `test_mcp_json_launches_the_bridge_through_uv_without_a_project` is replaced by the §7.3 assertion; the runbook's "MCP server (Claude)" row and README's local-server section describe the relay. One commit. It is the base of the G3 and G4 runs and is merged into feat/rust-relay only at G5.

## Order and concurrency

```
step 0 ──┬── L1 (test-heavy) ── G1(L1) ── merge ──┐
         ├── L2 phase A (cargo) ──────────────────┴── L2 phase B (test-heavy) ── G1(L2) ── merge ──┐
         └── L3 (light) ── G1(L3) ── merge (any time, one merge at a time) ─────────────────────────┴── G2 ── S ──┬── G3 (Windows) ──┬── G5 ── G6
                                                                                                                 └── G4 (WSL) ───────┘
```

- L1, L2 phase A and L3 run in parallel: their files are disjoint (ownership table) and only L1 runs broad test sets. L2 phase A runs cargo and `tests/test_relay_packaging.py` only; L3 runs its two test files and the hook tests only.
- L2 phase B waits for L1's merge because it needs the suite; L1 is finished by then, so L2 becomes the one test-heavy lane.
- Merges into feat/rust-relay happen one at a time, each after its own G1.
- G3 and G4 both use the S branch and may overlap: G4 is light (one WSL install, a few `claude` calls), G3 is the heavy one.

## Gates, in order

- **G0 Plan review** (fresh Opus, `taskmaster:plan-review`) before step 0. Only Critical/Important block.
- **G1 Per-lane review gate** (fresh Opus each, `taskmaster:review-gate`, Opus because the lanes touch process spawning, the token path and a file-renaming hook): against the spec's R1-R10 and this plan's contract; two fix rounds, then the user. Minors batched once for the branch.
- **G2 Integration, both relays green** (Sonnet lane `relay-verify` on merged feat/rust-relay): `tests/test_relay_*.py` with `TASKMASTER_REQUIRE_RELAY=1`; cargo test; `build_relay.py --check` and `--verify`; the full Windows suite (`pytest tests -n 4`, only test-heavy lane at that time); the mods tests (bun) for the wording fixture; **Linux**: from a `git archive` export on WSL's ext4 (`bash -lc`, WSL has python 3.12, uv, node), the black-box suite against `tm-relay-linux-x86_64` and the entry tests with sh. Fails go back to the owning lane via SendMessage.
- **G3 Windows live gate** (Sonnet lane `relay-live`, worktree on `relay/switch`, `--plugin-dir` at it, scratch project under `D:\scratchbook\taskmaster\relay`): spec §7.4 items 1-10, scripted where scripting reaches: (1) process tree, (2) EnumWindows/conhost probe for every PID in the chain, (3) server outlives the first session, `/mcp` reconnect, Claude Code killed leaves no relay or helper, (4) session header in serve.log, (5) the mods' read batch and one mod write through the relay, "connecting" on a server restart, (6) acceptance probe with 8 sessions: relay ≤ 5 MB private each, total vs 7.6.0's 58 MB per session, helper peak during the 8-session boot, zero windows, mods reads p95 < 3 s, (7) HAY team clone: initialize carries the team server's instructions and one read answers (reads only; a write on the live team server is the user's call), (8) retire with 8 sessions open: helper count and peak RAM, (9) blocked exe → self-heal → `/mcp` or the next session connects, recording the failed-connect memory, (10) plugin update with 2 sessions open (scratch `CLAUDE_CONFIG_DIR` if it can log in there without touching the user's config; else a user step). Results in `docs/handoffs/2026-11-22-local-server-lanes/relay-live-gate.md`.
  **User steps (the orchestrator hands the user a short checklist):** by eye, no console flash and no app-starting cursor when a session starts and on `/mcp` reconnect, once in the terminal and once in the desktop app (item 2); the taskmaster-mods panes populate in an interactive session (item 5); item 10 if it could not be scripted. Fail branches: a cursor → the M8 contingency (L2 adds `PeekMessageW` if ≤ +1 MB, else the console subsystem), rebuild, re-run items 1-2 and 6; a window or a relay over 5 MB → fix in L2 before G5; a mods break → stop and report.
- **G4 G-exec on WSL** (spec §7.4, I3). **Runner:** a Sonnet lane `relay-gexec`, started after the user approves the install below. **User step: approval to install the current Claude Code (the version on Windows at the time, 2.1.330 today) into a scratch prefix on WSL's ext4 (`~/scratchbook/taskmaster/gexec/`, not `/mnt/*`) with an isolated `CLAUDE_CONFIG_DIR` and `HOME`-scoped npm prefix**, plus a login in that config if `claude mcp list` needs one. Install: `npm install -g --prefix <scratch> @anthropic-ai/claude-code@<version>`; if npm has no such version, the newest npm version with the same minor, recorded; if none, stop and report (no other install route without asking).
  **Source on ext4 only** (a directory marketplace read from `/mnt/d` (drvfs) shows every file as 0777, so the gate would pass falsely): `git clone` the `relay/switch` branch from the Windows repo into `~/scratchbook/taskmaster/gexec/taskmaster`, then (a) a **production-shaped git marketplace**: a scratch repo `gexec/market` whose `.claude-plugin/marketplace.json` lists `./plugins/taskmaster`, with `plugins/taskmaster` a **git submodule** pointing at that clone, added as a `file://` git marketplace; and (b) a **directory marketplace** at a plain copy on ext4. Into the clone, before either install, the lane adds one tracked control file `bin/mode-control` at git mode 100644.
  **Pass rule, per marketplace kind:** PASS only when, in the installed plugin cache, `bin/tm-relay` and `bin/tm-relay-linux-x86_64` are executable for the owner, `bin/mode-control` is still not executable, and tm connects (`claude mcp list` shows plugin:taskmaster:tm connected, then one `-p` call to backlog_status answers). A control that came out executable means the check could not tell modes apart: INCONCLUSIVE, not pass. FAIL when the bin files are not executable or tm does not connect. G4 passes when the git kind passes (production is a git marketplace with a submodule); the directory kind is recorded and, if it fails, the runbook says so. Results in `relay-live-gate.md`; the lane stops every process it starts and leaves the scratch prefix for the user to delete. Pre-existing and out of scope: `hooks/*.sh` are 100644 yet run directly (B-115; report only, the control rule above also answers it).
- **G5 Merge the switch.** Only when G3 and G4 pass: merge `relay/switch` into feat/rust-relay, then feat/rust-relay into feat/local-server (both local, no-ff, routine). **If G4 fails**, S is not merged and `.mcp.json` keeps the uv entry **on every platform, Windows included**: one static `.mcp.json` serves every OS, so there is no Windows-only switch. The relay, the helper mode, the sh entry, the self-heal and the binaries still merge; they are dormant (nothing launches `bin/`; the self-heal stays dormant because `.mcp.json` does not point at `bin/tm-relay`) and the Windows RAM win waits. The user then decides between shipping 7.7.0 without the switch (CHANGELOG says the relay is built but not enabled) and holding the release, and a follow-up task looks for a mode-independent entry (candidates: `"command": "sh", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/tm-relay"]`, which needs no exec bit on POSIX but whose resolution of bare `sh` on Windows is unmeasured and would keep sh resident (+6 MB); per-OS plugin variants in the marketplace).
- **G6 Release 7.7.0** (orchestrator, on feat/local-server after G5; runbook docs/runbooks/release-packaging.md): CHANGELOG `## 7.7.0`; `python scripts/bump_version.py 7.7.0`; regenerate the shipped handshake (`python tests/surface_inventory.py --write-handshake`; `serverInfo.version` changed); `python scripts/build_relay.py --check`, and if `relay/` changed since the committed binaries, build, `--verify` and commit; `--verify` in any case when `bin/` changed since 7.6.0 (it has); the Defender scan of `bin/tm-relay.exe` (`MpCmdRun.exe -Scan -ScanType 3 -File <repo>\bin\tm-relay.exe -DisableRemediation`, exit 0 required, 2 blocks the release); the full suite with `TASKMASTER_REQUIRE_RELAY=1`; claude-tools steps 4-8 (the Codex snapshot copies an allowlist without `bin/`, so it is unaffected; no mods version bump, the mods do not change). Squash to master without the lanes folder and every push wait for the user's explicit go-ahead. **macOS stays on the Python path** (no darwin binary: the sh entry execs `uv run ... tm_bridge.py`, 7.5.0/7.6.0 behaviour) until a GitHub Actions job on `macos-14` (spec §4.5) builds and tests the darwin binaries; creating and running that job needs the user's go-ahead and is its own task.

## Risks (plan level; the design risks are spec §8)

- **Two relays drift while lanes run in parallel.** L1's suite and parity pin are the contract; L2 ports from `Bridge` and cannot change L1's assertions without the orchestrator; G2 runs the suite on both.
- **L2 phase B finds suite bugs.** Expected; they go to the orchestrator, who sends them to L1's builder (continued via SendMessage, not respawned) or rules on them.
- **A running `tm-relay.exe` blocks rebuilds and file replacement on Windows** (spec risk 6b). Tests stop everything by PID; `build_relay.py` reports a locked output as such; G3's sessions are closed before any rebuild.
- **Test load on this machine.** One test-heavy lane at a time (L1, then L2 phase B, then G2); G3's 8 sessions run when no lane runs tests.
- **The self-heal misfires.** Rev 3 renames only on a definite start failure; G3 item 9 checks the real blocked case and the healthy-path timing.
- **G4 needs the user.** Without the approval G5 cannot happen; everything before it can, and the dormant merge in G5's fail branch is the fallback.

## Rollback

One line, at any point after G5: set the tm entry back to `"command": "uv", "args": ["run", "--no-project", "--python", ">=3.11", "python", "${CLAUDE_PLUGIN_ROOT}/tm_bridge.py"]` (revert S). tm_bridge.py stays fully working in full mode throughout, so the revert restores 7.6.0's behaviour exactly. Without a file edit: `TASKMASTER_RELAY=python`. A blocked exe heals itself (D5). No store, schema or server change is involved, so nothing in a project needs undoing.

## Acceptance (recorded by G3 in relay-live-gate.md)

- Per session on Windows: relay ≤ 5 MB private, no uv and no conhost resident after boot; 8 sessions' total and the 7.6.0 comparison (58 MB per session) recorded.
- The helper is gone within 3 s of its last line (black-box) and its boot and retire peaks with 8 sessions are recorded.
- Zero visible windows in the whole chain; the user saw no flash or app-starting cursor in the terminal or the desktop app.
- The mods work through the relay (panes, writes, "connecting" wording); the mods' reads p95 < 3 s with 8 sessions.
- The black-box suite is green for both relays on Windows and Linux; `build_relay.py --verify` passes; the Defender scan is clean.
- G-exec passed (or the G5 fail branch was taken and the user ruled on the release).


## Plan review 1 resolutions

Review: `D:\scratchbook\taskmaster\relay\plan-review.md` (0 Critical, 2 Important, 12 Minor).

| Finding | Resolution |
|---|---|
| **I1** G4 had no runner, source location or pass rule; drvfs shows 0777 | G4 rewritten: runner `relay-gexec` (Sonnet), install route and its fallback, source cloned onto ext4, a production-shaped git marketplace with `plugins/taskmaster` as a submodule plus a directory marketplace, a 100644 control file, PASS/FAIL/INCONCLUSIVE per kind, the git kind decides |
| **I2** the self-heal ran at every Windows SessionStart while the relay was unused | The self-heal block is dormant unless `.mcp.json` points tm at `bin/tm-relay` (L3 step 2, tested both ways); it first runs for real on `relay/switch` in G3, whose items 2 and 9 must pass before G5 merges S |
| M: reconnect backoff and the GET non-200 cap are inline literals (tm_bridge.py ~903/921/1169) | L1 hoists and names them as module constants before the vectors read them; behaviour unchanged |
| M: the Python relay has no "uv not found" text | Black-box tests assert the `taskmaster bridge error: ` prefix and the 2 s bound there, the full text only for Rust |
| M: team-initialize equality only on Rust | Runs on both relays |
| M: `current_exe` canonicalize returns a `\\?\` path on Windows | L2 strips the verbatim prefix before building child paths and argv (unit test) |
| M: aarch64 in rust-toolchain.toml makes rustup fetch it on every cargo run | `targets` lists only x86_64-pc-windows-msvc and x86_64-unknown-linux-musl; aarch64 stays out until a build exists |
| Other Minors | Left to the lanes' judgment; listed in the review file |
