<!-- User intent: let a cold session take release candidate 7.0.0-rc.1 through the authorized release
     steps (merge, push, claude-tools, install, live CodeMaestro cutover, final 7.0.0) without
     re-deriving N17's state, and clean up the N16/N17 scratch safely. -->

# Handoff: N17 complete locally, release candidate 7.0.0-rc.1 ready (2026-09-30)

## State

- **Branch `feat/native-n17`** in `.worktrees/n17`, HEAD `8309258` plus the commit carrying this
  handoff, the N17 report and the ledger row. Release commit `6186ffe` (`release: 7.0.0-rc.1`);
  every in-repo version string reads `7.0.0-rc.1` (`python scripts/bump_version.py --check`).
- **Nothing is pushed, published or installed.** No live project was cut over.
- Status: **complete locally; release candidate 7.0.0-rc.1 prepared; publishing and live activation
  not done (separate authorized steps).** Evidence:
  [`docs/reports/2026-09-30-native-n17.md`](../reports/2026-09-30-native-n17.md) and
  [`2026-09-29-native-n17-packaging.md`](../reports/2026-09-29-native-n17-packaging.md). Ledger row:
  `docs/plans/2026-09-09-database-native.md`.
- Tests: full suite at `6186ffe` 4,744 passed / 1 failed (test bug, fixed `b892954`) / 2 skipped.
  After the demonstration fixes (`b7e2827`, `8309258`) only `test_packaging` (34 passed),
  `test_native_service_build` and `test_native_bypass_gate` (169 passed) ran. **The full suite has not
  run on the final code.**
- Branch topology: `master` (`94c6e27`, 6.0.3, = `origin/master`) is an ancestor of
  `feat/native-n17`. `feat/database-native-foundation` (`a75622d`) has 2 commits not in N17 (the
  test-suite speed design and plan, docs only) and is 92 commits ahead of its origin.

## Decisions (user, this milestone)

- **Version 7.0.0, release candidate 7.0.0-rc.1.** The final bump retitles the CHANGELOG entry
  `## 7.0.0-rc.1` to `## 7.0.0`.
- **Fix the build handshake** (done: a newer client retires an idle older coordinator; an older one
  refuses).
- **`backlog_sync` is tool-only.** Nothing imports hand edits automatically.

## Release steps (each one only when the user authorizes it)

### 1. Merge to master (local)

1. Run the full suite once on the final N17 code, in the foreground and logged to a file
   (`pytest tests -n 4 > test-results/n17-full-<sha>.out.log`), and record the result in the report.
2. In the main checkout (`feat/database-native-foundation`): `git merge --no-ff feat/native-n17`.
3. Merge `feat/database-native-foundation` into `master` (`--no-ff`), in the `master` worktree
   (`.worktrees/master-release`). `master` is an ancestor, so this cannot conflict. Do **not** merge
   `master` back into a native branch with a different base: see the memory note on
   cherry-equivalent bases (`fa6935b`), which resurrected deleted store code once.

### 2. Push (gated)

`git push origin master` and `git push origin feat/database-native-foundation`. The claude-tools
submodule's URL is `https://github.com/Gruku/taskmaster.git`, so the release commit must be on
GitHub before step 3.

### 3. claude-tools (`C:\Users\gruku\Files\Claude\claude-tools`)

Follow [`docs/runbooks/release-packaging.md`](../runbooks/release-packaging.md), *Release steps*
4–8. claude-tools' working tree carries unrelated untracked files; stage only the paths named here.

1. **Apply the two script changes first** (they are not committed there):
   - `scripts/sync_taskmaster_codex_distribution.py`: add `"taskmaster_cli.py",` to `FILES`
     (currently `backlog_server.py` and `viewer/index.html` only). Without it the Codex snapshot ships
     without the CLI front door.
   - `scripts/check_plugin_version_bump.py`, `_changelog_has`: make the heading match exact, so
     `## 7.0.0-rc.1` cannot satisfy `7.0.0`:
     `re.search(rf"^##\s+{re.escape(version)}(?=\s|$)", text, re.MULTILINE)` in place of the `\b`
     form.
2. **Advance the submodule:** check out the release commit (the merge on `master`) in
   `plugins/taskmaster`. **This is also the install for this machine:** the live Claude sessions run
   `claude-tools/plugins/taskmaster/backlog_server.py` straight from the submodule (the demonstration's
   process scan showed them), so a new session loads whatever is checked out there.
3. Set the taskmaster `version` in `.claude-plugin/marketplace.json` to `7.0.0-rc.1`.
4. Run `python scripts/sync_taskmaster_codex_distribution.py` and confirm
   `codex-plugins/taskmaster/taskmaster_cli.py` exists.
5. Commit the gitlink, `codex-plugins/`, `marketplace.json` and the two script changes together.
6. `python scripts/check_plugin_version_bump.py --base origin/master` must exit 0 (it fails before
   the step-5 commit; that is expected, see the runbook's HEAD-gitlink rule).
7. Push claude-tools (gated).

### 4. Install

- Restart every Claude Code session and every Codex host connection so they load 7.0.0-rc.1. The first
  server start builds a new uv environment (about 15 s, network needed).
- Hooks of an older version refuse a native store and fail open; restarted sessions pick up the new
  ones.
- Check one session: MCP `serverInfo.version` is `7.0.0-rc.1`.
- Not yet verified: a real Codex host with the manifest's `cwd: "."` and no `TASKMASTER_ROOT` (the
  server refuses to use its plugin directory as a project root). Check it on the first Codex session.

### 5. Live CodeMaestro cutover (`C:\Users\gruku\Files\Work\CodeMaestro`)

Follow [`docs/runbooks/native-cutover.md`](../runbooks/native-cutover.md). `<cli>` below is
`uv run C:/Users/gruku/Files/Claude/claude-tools/plugins/taskmaster/taskmaster_cli.py`.

1. **Stop all sessions.** Close every Claude Code and Codex session on the machine, not only
   CodeMaestro's: Windows cannot see a process's cwd, so the process scan lists every taskmaster
   server. No Taskmaster 6.0.2 or older may run against the project, ever again. Just before closing
   the last CodeMaestro session, run one tool call (`backlog_handover_list`) so its scan adopts any
   recent file change. From then on, no pulls, checkouts or edits in the project until the cutover
   ends.
2. **Repair the 3 archived handovers, keeping LF.** Under `.taskmaster/handovers/_archive/2026/`:
   - `2026-06-12-taskmaster-notes-grounded-handover.md`
   - `2026-06-14-mock-grounded-playable-chain-plans-ready.md`
   - `2026-06-15-build-glass-shipped-slide-up-orchestrator-driven.md`

   Check each with `git ls-files --eol <path>` (on the copy all three were `w/lf`). Add the runbook's
   minimal frontmatter (`id` = file name, `date`, `tldr`, `next_action`, `task_ids`, `session_kind`,
   `status: closed`, `archived: true`) above the unchanged body, in LF. The exact values used on the
   copy are in `%TEMP%\tmn17\demo\repair.py` (and `%TEMP%\tmn16\cm2\repair.py`); copy them out before
   deleting the scratch. Check the body is unchanged (the script recorded each body's sha1).
3. **Touch B-339** without changing its bytes:
   `python -c "import os, sys; os.utime(sys.argv[1])" .taskmaster/bugs/B-339.md`. This build parses it;
   only the old quarantine stamp holds it.
4. Start one session, run `backlog_handover_list` and then `backlog_store_status` until it shows
   **no `Quarantined:` and no `Flagged:`** files (on the copy: 1 left after the first re-adopt, 0
   after the touch). Close the session again.
5. **Dry run:** `<cli> cutover --root C:\Users\gruku\Files\Work\CodeMaestro --dry-run`. Expect
   `cutover dry-run: OK` with 0 quarantined. If the only refusal is the process list, check that every
   listed pid belongs to another project or is already gone, then add `--confirm-stopped`.
6. **Cutover:** `<cli> cutover --root C:\Users\gruku\Files\Work\CodeMaestro` (plus
   `--confirm-stopped` if needed). Expect all 7 stages `done`; on the copy it took 13.5 s by the
   journal, repaired 4,610 historical link rows and seeded 3,718 merge bases. On a refusal or failure,
   follow the report's `next`/`hint` and the runbook (`--resume` or `--rollback`); activation cannot be
   rolled back after it commits.
7. **Verify:** start a session; `backlog_store_status` (native, nothing quarantined);
   `backlog_sync()` (on the copy 2.1 s, one call); one write. Only after the first native write does
   `git status` show `M .taskmaster/backlog.yaml`, together with the 3 repaired handovers. Commit them
   with the managed command, from the project: `<cli> git commit -m "taskmaster: native cutover"`.
   Expect 4 orphan tasks (no epic or status) that every tool answers "not found" for; that is a known
   limit, not a cutover defect.
8. Keep the newest `pre-native-*` backup set (with its `.db-shm`/`.db-wal`) until the native store
   is verified; then prune as the runbook says.

To stop the coordinator at any point: `<cli> coordinator status --root <project>` /
`<cli> coordinator stop --root <project>`.

### 6. Final 7.0.0

After the live cutover has been verified:

1. In taskmaster, retitle `## 7.0.0-rc.1` to `## 7.0.0` in `CHANGELOG.md` (update any figures the
   live cutover changed).
2. `python scripts/bump_version.py 7.0.0` (exits 0 only when all five strings and the heading agree).
3. Run the tests, commit (`release: 7.0.0`), merge to `master`, push (gated).
4. Repeat the claude-tools steps (3.2–3.7) with `7.0.0`, then restart sessions.

## Open follow-ups

- The full suite on the final N17 code (step 1.1).
- A real Claude Code and Codex host session, including the Codex project root.
- POSIX: nothing ran there; containment and the process scan's POSIX paths are unit-tested only.
- Script environments are not locked (`uv run` in script mode ignores `uv.lock`); an install resolves
  the newest version inside each range. `uv lock --script` would pin them, but the locks would have
  to ship in the Codex snapshot too.
- The demonstration's first native write answered in 0.51 s including the coordinator start, against
  N16's ~6 s cold start; not investigated.
- From the guide's *Known limitations*: write p95 under 8–12-client contention; `generation()` ~7 s
  per managed Git operation; CodeMaestro full viewer read 164 ms p95; cold first sync/commit on a
  fresh copy 40–88 s (hypothesis: cold fingerprint cache), and a cold 37,010-file sync takes several
  rounds (total unmeasured); racing `backlog_link` create reply; repeat `sync.apply` events for
  conflicted files; linked-worktree sync not batched; legacy adoption roughly quadratic (~87 min at
  10×); 10× acceptance not run; 4 orphan tasks; legacy `backlog_link create` (N14 D1) and the
  `target_kind` fallback; 6.0.2 and older fail uncleanly on a native store; handover/issue resyncs and
  the Python client's sync can outlast Codex's 30 s tool timeout; merge stamps wait for the next write
  when no coordinator runs.

## Scratch to clean up (disposable; remove safely)

Use the `guard-hooks:safe-worktree-removal` procedure: `git worktree remove <path>` **without
`--force`**, never `rm -rf` on a worktree; on "Filename too long" stop and prune from a shell with
`core.longpaths`.

- **Before deleting anything:** copy out `%TEMP%\tmn17\demo\repair.py` (step 5.2), and the
  git-ignored `test-results/` in `.worktrees/n16` and `.worktrees/n17` if the logs cited by the
  reports are wanted. Check that no coordinator from the scratch copies is still running
  (`<cli> coordinator status --root %TEMP%\tmn17\demo\cm`; the rc.2 one from step 6 exits after
  300 s idle).
- **`%TEMP%\tmn16\`:** N16 rehearsal copies, acceptance runs and the A/B. First remove its two
  registered worktrees, `%TEMP%\tmn16\ab\old` (`4867c39`) and `%TEMP%\tmn16\ab\new` (`3f646c2`).
- **`%TEMP%\tmn17\`:** `pkg\`, `rev-hs\`, `rev-pkg\`, `rev-sync\` and `demo\` (the CodeMaestro copy
  `cm`, the claude-tools clone, the `rc2` copy and its `.venv`, a private `uv-cache`). None is a
  registered worktree of this repository.
- **N16 worktrees:** `.worktrees/n16`, `n16-batchsync`, `n16-harness`, `n16-instr`, `n16-linkplan`,
  `n16-perf`, `n16-sync`, `n16-syncjudge`, `n16-wording`, and
  `.claude/worktrees/agent-a52791b1f87169618` (`feat/native-n16-preflight`). All are merged; delete
  their branches with `git branch -d` afterwards. Branch `rr/n16-trial-merge` is not an ancestor, so
  `-d` refuses: ask the user before `-D`.
- **N17 worktrees:** `.worktrees/n17-docs`, `n17-handshake`, `n17-pkg`, `n17-synctool` (all merged
  into `feat/native-n17`), and `.worktrees/n17` itself once step 1 has merged it; then
  `git branch -d` each branch.

## Conventions (keep)

- One worktree per track, test-first, a fresh adversarial reviewer each round.
- Heavy runs one at a time, in the foreground or logged to a file; do not commit into a worktree
  while its suite runs (the `604cc08` run was contaminated that way).
- Copy-only on CodeMaestro until step 5 is authorized; record live before/after (HEAD and the store
  files' sizes and times) around any copy.
