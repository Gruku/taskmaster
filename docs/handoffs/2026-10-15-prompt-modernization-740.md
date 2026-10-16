<!-- User intent: resume file for the 7.4.0 release (prompts rewritten to the Claude 5.5 guides +
     native-by-default init) so a cold session can run the suite and ship it without this chat. -->

# 2026-10-15 — taskmaster 7.4.0 ready locally, not pushed

## State

- Repo `C:\Users\gruku\Files\Claude\taskmaster`, branch `feat/database-native-foundation`.
  Release commit **`8e8b5c7`** ("release: taskmaster 7.4.0 …"), this handoff on top. Nothing pushed.
- Version strings aligned at 7.4.0 (`scripts/bump_version.py --check`); taskmaster-mods 0.2.3; rr-tui unchanged.
- CHANGELOG `## 7.4.0` written. `.claude-plugin/plugin.json` description refreshed.
- `master` is checked out in `.worktrees/master-release` at `b415334` (7.3.2), clean.
- claude-tools (`C:\Users\gruku\Files\Claude\claude-tools`, master `933fb0f`): submodule still 7.3.2; untracked junk there is not ours.

## What 7.4.0 contains

1. **Prompt modernization** — plan `docs/plans/2026-10-15-prompt-modernization.md` (0ec7953), units U1-U11 merged, review fixes F1-F5 merged. User rulings: start-session only on orient asks; tracked work with no task = find-or-create, announce, pick, no stops; "log a bug" writes first; DEC only for open/deferred choices; "continue" with several threads auto-picks the one whose newest handover's first task is in progress.
2. **Native-by-default init** — `feat/native-default-init` (1ec4b06) merged; `taskmaster/native/fresh.py`, `backlog_init(legacy_store=True)` opt-out, intent recorded in taskmaster.json, resume of interrupted init, worktree/submodule refusal, coordinator warm-up, hook exempts taskmaster.json.

## Verification so far

- Every unit's targeted tests passed after its merge; `check_adapter_coverage.py --strict` OK.
- Eval `evals/agent_tool_use` (19-scenario subset on the new tool text): 18/19; s20 fails only because the session's auto-mode classifier blocked `backlog_skip_gate` (right tool chosen). Report: session scratchpad `eval-pm1-report.txt` (temp, may be gone).
- Reviews: R1/R2/R3 (prompts) PASS after two rounds; native-init independent review "merge" after two rounds.
- Full suite at `140d90b` (before the native merge): 5039 passed / 4 failed. Three passed in isolation (load flakes: n16 acceptance fault, dependency-graph deadline, service-process restart). `test_resolver_guard::test_resolve_paths_negative_control_non_plugin_dir` fails only because of a stray git-ignored `.taskmaster/` (dated 2026-10-11) in this repo root — passes with `TASKMASTER_ROOT` set to a clean dir. Folder left in place (user's call).
- Full suite on `8e8b5c7`: **not finished** — stopped at ~37% (2 early failures, unidentified) when the session wrapped up.

## Next steps (user gave go-ahead for all of these on 2026-10-15)

1. Full suite on the release tip, detached (RAM is tight; ~75 min at `-n 2`, ~11 min at `-n 4` with ≥10 GB free):
   `uv run python scripts/run_detached_check.py --name rel740-suite2 --cwd <repo> <repo>/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -n 2 --basetemp=<scratch> tests/`
   Rerun any failure in isolation; the four above are known.
2. Then `claude plugin test mods/taskmaster-mods` (`env -u ANTHROPIC_API_KEY`); never in parallel with the suite. It had ~20-45 flaky 5 s timeouts before and after the mods change.
3. In `.worktrees/master-release`: `git merge --no-ff feat/database-native-foundation`, then push foundation and master (guard hook asks Approve/Deny per push).
4. claude-tools per `docs/runbooks/release-packaging.md` steps 4-8: advance `plugins/taskmaster` to the master merge, marketplace version 7.4.0 (refresh its description: native by default, prompts), `python scripts/sync_taskmaster_codex_distribution.py`, commit gitlink + codex-plugins + marketplace together, `python scripts/check_plugin_version_bump.py --base origin/master`, push.
5. After install: confirm `/` still lists the 7 skills whose command files were deleted; restart the MCP server.
6. Cleanup: remove this session's worktrees with plain `git worktree remove` (no `--force`): `pm-U1`..`pm-U11`, `pm-F1`..`pm-F5`, `pm-init`, `native-init`; then `git branch -d` their branches.

## Follow-ups (not in 7.4.0)

- Plan §6 list (server records branch/worktree on pick; status counts; enums; retire alias tools; orientation map; routing eval).
- `backlog_query` with `json_each(doc,'$.related_tasks')` hung >100 s on the eval seed — file a bug.
- `backlog_init` in a subdirectory still creates a nested store; `backlog_status` raises a raw FileNotFoundError on a fresh project.
- Coordinator 15 s launch cap in `taskmaster/coordinator/client.py`; submodule store resolution.
- Test-suite speed plan (spec 7546e0a, plan a75622d) not yet executed.
