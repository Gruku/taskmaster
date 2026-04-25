---
name: review-gate
description: "Run quality checks on a task's IMPLEMENTATION before marking it ready for user testing. Invoke when the user says 'is this ready?', 'run the review gate', 'check my work', 'I think this is done', or wants to verify implementation quality before merging. Reviews the diff for defects and spec adherence, runs tests and build, then transitions task to in-review. For pre-implementation design review, use taskmaster:spec-review instead."
---

# Review Gate

Post-implementation quality gate before marking a task ready for user testing. The user has just finished implementing something and wants to know if it's solid. Lead with the overall verdict — they want validation, not a data dump.

This skill reviews **code** (the diff). For pre-implementation design review of the spec/plan, use `taskmaster:spec-review`.

## Arguments

- `task_id` (optional) — specific task ID. If omitted, uses the current in-progress task (if only one). If multiple tasks are in-progress, ask the user which one.
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex precision pass.
  Default behavior:
    - `critical` priority: auto-suggest, ask user before running
    - `high` priority: offer if Codex is detected, default off
    - `medium` / `low`: never run
  Codex is detected if `~/.claude/plugins/cache/openai-codex/` exists or `codex` is on PATH.
  If `--codex` is passed but Codex isn't available, WARN and continue without it.

## Steps

1. **Get task details** — call `backlog_get_task(task_id)` to get priority, epic, branch, docs, sub_repo, review_instructions, and `spec_review` record (if any).

2. **Gate 1: Spec/Plan Check (critical/high tasks only)**

   High-priority tasks should have documentation. This gate checks existence, not correctness — verifying spec-to-implementation alignment is Gate 2c.

   - For tasks with priority critical or high:
     - First check the task's `docs` field — if `docs.plan` or `docs.spec` exists, verify the files exist on disk.
     - If no docs field, search `docs/specs/` and `docs/plans/` (project root), and `{sub_repo}/docs/` if sub_repo is set.
     - If no spec or plan found: **WARN** — "No spec/plan found for {priority} task `{id}`." Continue anyway.
     - If a spec exists but no `spec_review` record exists for this task: **WARN** — "Spec was never reviewed via `taskmaster:spec-review` — design issues may surface late." Advisory only, does not block.
   - For medium/low tasks: skip this gate entirely.

3. **Gate 2a: Code Review (Claude)**
   - Determine the working directory: the task's worktree path if set, otherwise the project root.
   - Determine the working branch from the task's `branch` field. If empty, check the current git branch in the working directory.
   - If no branch can be determined: **WARN** for medium/low, **escalate to user** for critical/high.
   - If the `superpowers:code-reviewer` subagent is available, dispatch it scoped to changed files between the branch and its base. If not available, perform an inline review of the diff.
   - Group findings by severity: Critical / Important / Minor.

4. **Gate 2b: Codex Precision Pass (opt-in, second opinion)**

   Skip silently if Codex isn't detected and not requested. Run **after** Gate 2a so it can be focused.

   Codex is the precision knife — best for catching defects Claude's review missed and verifying that bugs fixed in this task actually stay fixed across the codebase. Use **non-adversarial** framing here — design choice was already vetted (or should have been) at spec-review time.

   Build a focus arg from what Gate 2a *didn't* flag (avoids duplicate work). Two cases:

   **Case A — Standard precision review (default).**
   Use `/codex:review` against the branch diff:

   ```bash
   /codex:review --base <branch-base> --wait <focus text>
   ```

   Focus text should reflect what Gate 2a covered so Codex hunts for blind spots, e.g.:
   "Claude's review flagged X and Y. Look for: error path correctness, edge cases in <specific function>, and any sibling occurrences of the same bug pattern elsewhere in the codebase."

   **Case B — Repeated-bug verification (when Gate 2a found criticals already patched in-session).**
   Codex is unusually good at catching when "the bug fix only patched the one site that hit the test." Prefer `/codex:rescue` framed as a verification task:

   ```
   Agent({
     subagent_type: "codex:codex-rescue",
     description: "Codex verifies fix is complete",
     prompt: `REVIEW-ONLY. Do not modify code. The bug <describe> was fixed in
   commit <hash>. Verify the fix is complete: search the codebase for sibling
   occurrences of the same pattern that may also need patching. Cite file:line.`
   })
   ```

   Tag findings with `(codex)` when merging into the report.

5. **Gate 2c: Spec Adherence (critical/high tasks with a spec)**

   For critical/high tasks that have a spec/plan: skim the spec and the diff side-by-side. Answer in 2–3 sentences:
   - Does the diff implement what the spec describes?
   - Anything in the spec that's missing or deferred without a follow-up task?
   - Anything in the diff that's *not* in the spec (scope creep)?

   Mark mismatches as Important findings unless the user already acknowledged the deviation.

   Skip this gate if there is no spec.

6. **Gate 3: Verification (Tests + Build)**
   - Detect which directory to run in from the task's `sub_repo` field or worktree path.

   **Tests** — auto-detect and run:
   - Node.js: `package.json` with `test` script → `npm test`
   - Python: `pytest.ini` / `pyproject.toml` / `tests/` → `pytest`
   - .NET: `.csproj` / `.sln` → `dotnet test`
   - Rust: `Cargo.toml` → `cargo test`
   - Go: `go.mod` → `go test ./...`
   - Not found → "No test runner detected — skipping" (not a failure)

   **Build** — auto-detect and run:
   - Node.js: `package.json` with `build` → `npm run build`
   - .NET: `.csproj` → `dotnet build`
   - Rust: `Cargo.toml` → `cargo build`
   - Go: `go.mod` → `go build ./...`
   - Not found → "No build command detected — skipping"

   **Review instructions:** If the task has a `review_instructions` field, display it prominently: "**Manual test steps:** {review_instructions}"

7. **Present Results — lead with the verdict:**

   Start with the overall outcome: "All gates passed — ready for your testing" or "Review gate found issues — see details below."

   Then show the breakdown:
   ```
   Gate 1  — Spec/Plan:               PASS / WARN / SKIP
   Gate 2a — Code Review (Claude):    PASS / FAIL (N issues)
   Gate 2b — Codex Precision:         PASS / FAIL / SKIP (not installed | opt-out)
   Gate 2c — Spec Adherence:          PASS / WARN / SKIP
   Gate 3  — Tests:                   PASS / FAIL / SKIP
   Gate 3  — Build:                   PASS / FAIL / SKIP
   ```

   List issues grouped by severity, tagged with source (Claude / Codex) and gate.

   **Blocking rules:**
   - Critical findings (either source) block unconditionally.
   - Important findings require user acknowledgment before proceeding.
   - Minor findings and WARN/SKIP results never block.

   If gates failed, offer: "Stay in-progress and address issues" or "Move to in-review anyway (you'll need to justify the critical findings)."

   If Codex flagged criticals that Claude's review missed, call it out explicitly: "Codex caught issues Claude's review didn't — worth reading carefully."

8. **Add review instructions:**
   - If the task has no `review_instructions` (or they're empty):
     - Draft review instructions based on what was implemented: what to test, how to verify, specific steps.
     - Present: "**Proposed review instructions:**\n{drafted}\n\nWant to use these, edit them, or write your own?"
     - Save via `backlog_update_task(task_id, "review_instructions", "{final}")`
   - If existing: show and ask "Keep these or update?"

9. **Transition to `in-review`:**
   - If all blocking gates passed: "Move `{task_id}` to `in-review`? This means it's ready for you to manually test."
   - If confirmed, call `backlog_update_task(task_id, "status", "in-review")`

## Why In-Review Exists

See `references/task-lifecycle.md` for the full lifecycle. The short version: automated tests can't catch everything. `in-review` means "Claude did its job; now the human verifies it actually works the way they want." Tasks should pass through this stage before being marked done.

## Related Reviewers (NOT part of this gate)

- **`taskmaster:spec-review`** — pre-implementation adversarial review of the spec/plan. Run this *before* `pick-task` for critical/high tasks. Blast radius lives there now.
- **`/code-review`** (claude-plugins-official) — post-PR review that fans out 5 Sonnet agents and posts a `gh pr comment`. Use it after the PR is up — it's PR-scoped and writes to GitHub, intentionally outside this gate.
- **`/codex:adversarial-review`** — challenges design choices via diff. Mostly useful at spec time; spec-review uses a prose-friendly equivalent. Don't invoke it from review-gate.
