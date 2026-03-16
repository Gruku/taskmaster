---
name: review-gate
description: "Run quality checks on a task before marking it ready for user testing. Invoke when the user says 'is this ready?', 'run the review gate', 'check my work', 'I think this is done', or wants to verify implementation quality before merging. Checks spec/plan, runs code review, runs tests and build, then transitions task to in-review."
---

# Review Gate

Quality gate before marking a task ready for user testing. The user has just finished implementing something and wants to know if it's solid. Lead with the overall verdict — they want validation, not a data dump.

## Arguments

- `task_id` (optional) — specific task ID. If omitted, uses the current in-progress task (if only one). If multiple tasks are in-progress, ask the user which one.

## Steps

1. **Get task details** — call `backlog_get_task(task_id)` to get priority, epic, branch, docs, sub_repo, and review_instructions.

2. **Gate 1: Spec/Plan Check (P0/P1 tasks only)**

   High-priority tasks should have documentation. This gate checks existence, not correctness — verifying spec-to-implementation alignment is out of scope for automated checks.

   - For tasks with priority P0 or P1:
     - First check the task's `docs` field — if `docs.plan` or `docs.spec` exists, verify the files exist on disk.
     - If no docs field, search `docs/specs/` and `docs/plans/` (project root), and `{sub_repo}/docs/` if sub_repo is set.
     - If no spec or plan found: **WARN** — "No spec/plan found for {priority} task `{id}`." Continue anyway.
   - For P2/P3 tasks: skip this gate entirely.

3. **Gate 2: Code Review**
   - Determine the working directory: the task's worktree path if set, otherwise the project root.
   - Determine the working branch from the task's `branch` field. If `branch` is empty, check the current git branch in the working directory.
   - If no branch can be determined: **WARN** for P2/P3, **escalate to user** for P0/P1 — skipping code review on a high-priority task silently is unacceptable.
   - If the `superpowers:code-reviewer` subagent is available, dispatch it scoped to changed files between the branch and its base. If not available, perform an inline review of the diff.
   - Report findings grouped by severity:
     - **Critical** — must fix before merge
     - **Important** — should fix, may proceed with justification
     - **Minor** — optional improvements

4. **Gate 3: Verification (Tests + Build)**
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

5. **Present Results — lead with the verdict:**

   Start with the overall outcome: "All gates passed — ready for your testing" or "Review gate found issues — see details below."

   Then show the breakdown:
   ```
   Gate 1 — Spec/Plan:    PASS / WARN / SKIP
   Gate 2 — Code Review:  PASS / FAIL (N issues)
   Gate 3 — Tests:        PASS / FAIL / SKIP
   Gate 3 — Build:        PASS / FAIL / SKIP
   ```

   **Blocking rules:**
   - Critical code review findings block unconditionally.
   - Important findings require user acknowledgment before proceeding.
   - Minor findings and WARN/SKIP results never block.

   If gates failed, offer: "Stay in-progress and address issues" or "Move to in-review anyway (you'll need to justify the critical findings)."

6. **Add review instructions:**
   - If the task has no `review_instructions` (or they're empty):
     - Draft review instructions based on what was implemented: what to test, how to verify, specific steps.
     - Present: "**Proposed review instructions:**\n{drafted}\n\nWant to use these, edit them, or write your own?"
     - Save via `backlog_update_task(task_id, "review_instructions", "{final}")`
   - If existing: show and ask "Keep these or update?"

7. **Transition to `in-review`:**
   - If all gates passed: "Move `{task_id}` to `in-review`? This means it's ready for you to manually test."
   - If confirmed, call `backlog_update_task(task_id, "status", "in-review")`

## Why In-Review Exists

See `references/task-lifecycle.md` for the full lifecycle. The short version: automated tests can't catch everything. `in-review` means "Claude did its job; now the human verifies it actually works the way they want." Tasks should pass through this stage before being marked done.
