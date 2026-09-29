<!-- User intent: the taskmaster test suite and its verification cycle take hours; cut that to minutes without losing the regression coverage that keeps catching real bugs. -->

# Test suite speed — design

**Status:** draft for review · **Date:** 2026-09-29 · **Branch base:** `feat/database-native-foundation` (`a12d3aa`, after the N16 merge; the profile below predates it at `402c17c`, and N16 added 269 tests)

## 1. Problem

Measured on 2026-09-29 at `402c17c`, profiled with `pytest tests -n 3 --durations=0 --junitxml`:

- **4,348 tests, 30 min wall at `-n 3`, 5,365 worker-seconds.** Workers are evenly loaded (wall ≈ worker-seconds ÷ 3), so the fix is less work, not better balancing. A concurrent N16 acceptance run shared the machine, so treat these as upper-bound numbers.
- **Growth:** 2,509 tests (2026-09-16) → 4,617 (2026-09-29). Every milestone and review finding adds tests; twins run each scenario on both backends.
- **The verification cycle is the real cost:** the full suite is re-run after every review round, plus `-m scale`, plus acceptance and CodeMaestro rehearsals. That adds up to hours per milestone.
- **Runs die:** Claude Code reaps background shells under memory pressure. This killed a probe run during this investigation, and every killed run is paid for twice.

Where the time goes:

| Cost | Evidence | Share |
|---|---|---|
| Fixture setup | 2,161 s of setup (40%); twin builds cost 2–3.6 s per case: `test_native_bypass_gate` 295 s, `test_native_routing_tasks` 171 s, `test_native_routing_epics_phases` 159 s | 40% |
| Heavy tail | 145 tests over 5 s = 1,642 s; 40 tests over 10 s = 926 s (`test_store_concurrency` mixed-process 194 s, `test_projection_conflict_property` 116 s, `test_native_routing_context_invariant` seeds ~25 s each, viewer oracles, cutover crash matrix 160 s) | 31% |
| Temp-dir churn | A full run leaves ~61k files, 39k dirs, 1.3 GB. pytest `rmtree`s the oldest retained run at session end: **229 s inside a one-test session** (cProfile: 100k `unlink`, 51k `rmdir`) | Whole minutes, often in the inner loop |
| Durability | Each write pays `synchronous=FULL` plus ≥5 fsyncs (22 fsync sites, 5 `synchronous=FULL` sites). No test-mode relaxation. One probe: −17% (baseline contaminated) | ~15% (to confirm) |
| By area | `test_native_*` 65.5%, `test_store_*` 10.9%, server/api/viewer 6.9%, skill lint ~0% | — |

## 2. Goals and non-goals

**Goals**

- **G1.** The merge suite (§4) runs in **≤ 10 min wall at `-n 3`** on this machine.
- **G2.** Verifying a change during implementation or a review round takes **< 2 min** in the typical case.
- **G3.** **One full run per merge**; heavy evidence **once per release**. This replaces a full run per review round.
- **G4.** **No silent coverage loss.** Every test that runs today still runs in some tier with the same assertions. The only change is where a test runs, listed explicitly in §4.2.
- **G5.** Runs don't die: one suite at a time, a RAM check before start, no reaped background shells.

**Non-goals**

- Deleting or consolidating tests. That is Step 3, deferred (§5).
- Moving runs to CI. That is optional and needs its own decision because it requires pushes (§6).
- Changing production durability. The durability switch in §3.2 is test-only by construction.

## 3. Step 1 — Remove waste (same tests, same coverage)

### 3.1 Temp-dir hygiene

**Today:** pytest keeps three numbered basetemps and deletes the oldest inline at session end. A full run's basetemp takes ~4 min to delete on NTFS, and whichever later session rotates it out pays that cost, even a one-test run. Setting `tmp_path_retention_policy = "failed"` naively makes this worse: with no `--basetemp`, pytest then deletes the session's *own* basetemp inline (`_pytest/tmpdir.py:325-333`).

**Design:**
- `tests/conftest.py` assigns every session a unique basetemp when none is given: `<system temp>/taskmaster-tests/run-<timestamp>-<pid>`. Only the xdist controller does this; workers inherit it.
- A basetemp passed explicitly gets no numbered-dir or inline cleanup.
- At session start, the controller launches one detached, windowless cleanup process. It deletes run dirs other than the newest two, and never the current one.
- No pytest session deletes old runs inline.
- The footprint is measured per test file once, in the baseline (§7). Any file writing more than 10× the median is recorded as a follow-up, not fixed here.

**Acceptance:** a one-test session run immediately after a full run finishes in < 10 s wall.

### 3.2 Test-mode durability

**Why it's safe:** fsync and `synchronous=FULL` only protect against OS crash or power loss, and no test simulates either. Crash tests use `os._exit` or a process kill, and those leave unsynced writes intact in the OS cache. SQLite documents that an application crash is safe at `synchronous=OFF`. Only two tests observe durability directly: `test_store_schema.py:228` asserts `PRAGMA synchronous == 2`, and `test_native_projection_drain.py:57` exercises mid-fsync behaviour.

**Design:**
- A new module, `taskmaster/durability.py`, owns `fsync(fd)` and the synchronous pragma value, with a process-global switch that is **on by default**.
- All 22 `os.fsync` call sites and all 5 `PRAGMA synchronous=FULL` sites route through it. These are in `store.py`, `native/commands.py`, `native/cutover.py`, `native/projection.py`, `native_routing/progress.py`, `coordinator/{checkouts,git,ownership}.py`.
- **The only way to turn it off is an in-process call.** There is no environment variable and no CLI flag, so production cannot be misconfigured.
- An autouse fixture in `tests/conftest.py` turns it off for every test except those marked `durability`. The two tests above get that marker.
- Subprocesses spawned by tests (hooks, the coordinator service, crash children) keep full durability, which is unchanged from today.
- A test starts a fresh interpreter and asserts that the switch defaults to on.

### 3.3 Seeded-project templates

**Today:** every twin or seeded-store test builds its project from scratch. Examples: `make_twins` (`tests/native_twins.py:327-342`), `rigged` (`test_native_bypass_gate.py:302-312`), `root` (`test_native_service.py:17-21`), `build_project` (`test_native_cutover.py:24-43`). Measured: fresh `make_twins` with the `rigged` seed has a median of **1.40 s** (0.88–2.71 s). A copy of both trees (~38 files, 0.68 MB) has a median of **0.042 s**.

**Feasibility (2026-09-29 study, throwaway experiment):**
- 8 exercises (status, pick, handover create/resync, resolve_conflict, changes_since, context) passed 30/30 on copies.
- After masking values that already differ between two fresh builds (UUIDs, session IDs, clocks, cursors), answers, projected files and every native-DB table matched.
- The only copy-specific difference is `sessions.cwd` (`store.py:2176-2186`). It is harmless: live-session filters never show those rows, and a fresh build already carries the legacy path into the native DB.

**Design:**
- **A per-worker registry, built on first use.** It is a module-level dict under `tmp_path_factory.getbasetemp()`, so templates never outlive the session.
- **No cache on disk across runs.** Stored rows carry the builder's pid, which later runs would see as dead or reused, and that changes claim and session liveness (`claims.py:183-193`). Stored wall-clock values would also age.
- **Opt-in by explicit key.** Call sites pass `template_key=`. Seeds that capture arguments (`_depends(value)`, `request.param`, …) include those values in the key. Anything else falls back to a fresh build. Never key on `seed.__code__` alone. `TASKMASTER_TWINS_VERIFY` is part of every key.
- **Build steps:**
  1. Build under a private `pytest.MonkeyPatch` and undo it afterwards (the precedent is `test_agent_journeys.py:85-107`).
  2. Close everything (`store.reset_for_tests()`, `close_owned()`) and assert no non-empty `-wal` file remains.
  3. Record the build's `CLOCK` (`native_twins.py:35,50-54`) and `bs._session_task` (`backlog_server.py:7919-7935`).
- **Copy steps:**
  1. `copytree` with the default copy2, which keeps mtimes. The legacy projection checks at `store.py:3503,4571` depend on that.
  2. Skip `*-shm`, `*.tmp.*` and `local/coordinator/`.
  3. Restore `CLOCK` and `_session_task`.
  4. Call `point_server_at` on the copied legacy root, which is the state `make_twins` ends in.
  5. Never hand a test the template itself.
- **Check mode.** With `TASKMASTER_TEMPLATE_VERIFY=1`, each templated fixture also builds fresh and diffs the two, with the same noise masking. The `release` run sets it, so templating is re-proven every release.
- **Candidates:**
  - `rigged`
  - the `twins` fixtures in `test_native_routing_tasks` and `test_native_routing_epics_phases`
  - `root` in `test_native_service` (and its 12 importing files)
  - `build_project` in `test_native_cutover` (the state before cutover)
  - `tm_epic_phase` (small gain)
  - the remaining `make_twins` call sites, one file at a time
- **Must stay fresh:**
  - fixtures with git or worktrees, because linked checkouts store an absolute `git_dir` and inode identity (`coordinator/checkouts.py:128-145`)
  - sync-fingerprint and first-sync cache tests (fingerprints are keyed by path and inode, `sync_files.py:201-206,375-380`)
  - crash, WAL and durability tests that depend on the build's own files or handles
  - `real_service_process` tests
  - seeds that use `tmp_path` or wall-clock values
  - tests that patch something *before* building

**Expected:** setup drops from 1–3.6 s to ~0.05 s per templated test, plus ~2.5 s once per recipe per worker.

### 3.4 Coordinator and HTTP server shutdown latency — measure, then decide

`Coordinator.close()` waits on `serve_forever`'s 0.5 s poll plus 0.1/0.25 s thread polls. That applies at 177 `with Coordinator(` sites and in about 150 server/viewer tests. The one probe was inconclusive because of machine noise.

**Decision rule:** the baseline (§7) measures total teardown time.
- If it is **≥ 5%** of worker-seconds, add a poll-interval constructor parameter that tests set to 0.02 s. The production default stays unchanged.
- Otherwise, drop this item.

## 4. Step 2 — Change when things run

### 4.1 Three commands

A single runner, `scripts/run_tests.py`, absorbs `scripts/run_detached_check.py`.

| Command | When | Selection | Target |
|---|---|---|---|
| `changed` | During implementation and every review round | Rules below, excluding `release` | < 2 min typical |
| `merge` | Once, on the final candidate, before merging into the working branch | Everything except `release` (the same set as bare `pytest`) | ≤ 10 min |
| `release` | Once per release | Everything, including `release`/`scale`, plus the acceptance and rehearsal scripts named in `docs/runbooks/` | Unbounded |

**How `changed` selects tests.** "Changed" means changed since the merge-base with `feat/database-native-foundation` (or `--base`), including uncommitted work. The rules are applied in this order:
1. Changed test files.
2. Test files that directly import a changed Python module under `taskmaster/`, `backlog_server.py` or `tests/*helpers*` / `tests/native_*.py`.
3. Test files whose source mentions a changed non-Python file by its path or file name (skills, playbooks, hooks, viewer, adapters).
4. A change to `tests/conftest.py` or `pyproject.toml` selects everything, the same set as `merge`.

**What the runner does on every command:**
- **Single-flight lock.** It refuses to start while another suite run holds the lock.
- **RAM preflight.** It refuses below 2 GB free for `-n 3`, based on the measured worker baseline of ~90–100 MB and the 0.65 GB dip seen at `-n 3` after the RAM fix.
- **Unique basetemp,** per §3.1.
- **Output** to `test-results/<command>-<timestamp>/`: junit plus durations. It prints the ten slowest tests and warns (does not fail) on any test outside `release` that takes > 10 s.
- **`--detach`** runs it windowless in its own process group, as `run_detached_check.py` does today. This keeps it out of reach of the shell reaper.
- **`release` also sets `TASKMASTER_TEMPLATE_VERIFY=1`** (§3.3).

**Why the `changed` selection is safe:** it is deliberately cheap and imprecise. It uses direct imports only, because transitive imports reach almost everything through `backlog_server`. The single `merge` run before every merge catches whatever `changed` misses. That trade is what makes G2 and G3 hold together.

### 4.2 The `release` marker — what moves out of `merge`

- `release` replaces and absorbs `scale`. Both stay deselected unless the marker expression names them, and `-m scale` keeps working.
- Moves to `release`:
  - **Multi-seed property and invariant tests.** The first seed or case stays in `merge`; the others become `pytest.param(..., marks=pytest.mark.release)`. Files: `test_native_routing_context_invariant`, `test_viewer_board_oracles` (seeded sequences), `test_native_routing_context_paging`, `test_store_related_incremental` (seeded edit sequences).
  - **Whole-test stress and property runs:** `test_store_concurrency::test_mixed_public_tool_operations_across_processes_never_lose_a_write` (194 s) and `test_projection_conflict_property::test_generated_histories_lose_nothing_and_never_touch_protected_files` (116 s). Each gets a reduced-size `merge` variant, e.g. fewer processes, operations or histories, so the invariant is still checked every merge.
  - **The cutover crash matrix** (`test_native_cutover_crash`, 66 cases, 160 s). One representative crash point per mode stays in `merge`. Cutover runs once per project, so the full matrix is release evidence.
- The final list is derived from the baseline durations (§7), using the rule "> 5 s and a repeat of a scenario that still has a representative in `merge`". It is written into `tests/README.md`. A test is never moved out of `merge` without a representative staying behind.

### 4.3 Where the policy lives

- **`tests/README.md`:** the three commands, when to use each, the markers, and the rule in §4.2.
- **A new project `CLAUDE.md`** with a short Testing section pointing at `tests/README.md`. The repo has none today, and approving this spec authorizes creating it. This is what makes agents run `changed` in review rounds instead of the full suite.
- **README "Development" section:** replace `uv run --with pytest python -m pytest -q` (`README.md:276`) with the runner commands.
- **Milestone handoffs and ledger rows:** from now on they cite the `merge` run at the merge commit, not a run per round. Existing documents are not rewritten.

## 5. Step 3 — Prune (deferred, separate spec)

Not part of this work. The trigger is the legacy-backend decision (after N17).
- **Largest candidate:** the legacy half of the twins. Options are a parity switch (`full | sample | off`) or recorded legacy goldens replacing live legacy runs.
- **Other candidates:** consolidating per-finding pins from shipped milestones (`test_native_n13_*`, `*_review*`), and overlap among the 11 `test_handover_status_*` files.
- **Precondition for any deletion:** a coverage diff showing the lines and branches still covered.

## 6. Optional — CI offload (separate decision)

Running `merge` on GitHub Actions, sharded, would take suite runs off this machine entirely. It requires pushing branches, which is gated per push, and it needs Windows runners because of the Job Object and ctypes paths. Not in scope; revisit after Step 2 lands.

## 7. Verification of this work

- **Baseline first.** Re-run the profile on a quiet machine (no acceptance or rehearsal runs in parallel) and record the numbers in `docs/reports/`. Every later number is compared against this, not the noisy 2026-09-29 profile.
- **After each Step 1 item:**
  - `pytest --collect-only -q` yields the same test IDs.
  - The pass/skip counts are identical.
  - Worker-seconds are re-measured.
- **After Step 2:** `merge` ∪ `release` yields the same collected IDs as today's full suite. Parametrized seeds move between tiers, but none disappear.
- **Acceptance:** G1 and G2 measured and reported. G2 is measured on three representative changes: a native routing module, `store.py`, and a skill markdown file.

## 8. Risks

| Risk | Mitigation |
|---|---|
| A templated copy silently differs from a fresh build | §3.3: explicit keys (no code-object keys), fresh-only exclusions (git, fingerprints, crash, pre-build patches), process state restored after copy, fresh-vs-copy diff on every `release` run |
| The durability switch reaches production | No env/CLI path; fresh-interpreter default test; subprocesses unaffected |
| `changed` misses an indirect break | One `merge` run before every merge |
| A regression hides in `release`-only cases until release | Every moved scenario keeps a representative in `merge`; `release` runs before every release |
| Estimates miss G1 | Baseline plus per-item measurement show it early; the fallbacks are §3.4 and a larger `release` move, with the user's approval |

## 9. Execution

- Separate branch `feat/test-speed` off `feat/database-native-foundation`, in its own worktree, merged back when G1–G5 are met.
- Order: baseline → §3.1, §3.2 and §3.3 (independent; can run in parallel) → §3.4 decision → Step 2.
- **Expected effect:**
  - After Step 1, worker-seconds should drop from ~5,400 to ~3,000: templating saves ~1,500 s and durability ~800 s.
  - After Step 2 moves ~1,300–1,600 s of heavy tail to `release`, the total should be ~1,500–1,700, which is ~9–10 min at `-n 3`.
  - These are estimates, to be replaced by measured numbers.
