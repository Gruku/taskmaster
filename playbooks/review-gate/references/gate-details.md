# Review Gate — Gate Details

This file contains detailed runner detection, gate formats, and step prose moved from SKILL.md.

## Gate 3: Test Runner Auto-Detection

Auto-detect which directory to run in from the task's `sub_repo` field or worktree path.

**Tests:**
- Node.js: `package.json` with `test` script -> `npm test`
- Python: `pytest.ini` / `pyproject.toml` / `tests/` -> `pytest`
- .NET: `.csproj` / `.sln` -> `dotnet test`
- Rust: `Cargo.toml` -> `cargo test`
- Go: `go.mod` -> `go test ./...`
- Not found -> "No test runner detected — skipping" (not a failure)

**Build:**
- Node.js: `package.json` with `build` -> `npm run build`
- .NET: `.csproj` -> `dotnet build`
- Rust: `Cargo.toml` -> `cargo build`
- Go: `go.mod` -> `go build ./...`
- Not found -> "No build command detected — skipping"

**Review instructions:** If the task has a `review_instructions` field, display it prominently: "**Manual test steps:** {review_instructions}"

## Gate 2a: Code Review Dispatch Detail

- Determine the working directory: the task's worktree path if set, otherwise the project root.
- Determine the working branch from the task's `branch` field. If empty, check the current git branch.
- If no branch: WARN for medium/low, **escalate to user** for critical/high.
- Delegate the review to a sub-agent if your tool supports it (on Claude Code: the superpowers code-reviewer), scoped to changed files between the branch and its base. If not available, perform an inline review of the diff.
- Group findings by severity: Critical / Important / Minor.

## Gate 2c: Spec Adherence Check

For critical/high tasks with a spec/plan: skim the spec and the diff side-by-side. Answer in 2-3 sentences:
- Does the diff implement what the spec describes?
- Anything in the spec that's missing or deferred without a follow-up task?
- Anything in the diff that's *not* in the spec (scope creep)?

Mark mismatches as Important findings unless the user already acknowledged the deviation.

Skip this gate if there is no spec.

## Step 7: Gate Matrix Display Format

Lead with the overall verdict. Then show the breakdown:
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

If gates failed, offer: "Stay in-progress and address issues" or "Record a pass anyway (you'll need to justify the critical findings) — end-session will close it to done."

If Codex flagged criticals that Claude missed: "Codex caught issues Claude's review didn't — worth reading carefully."

## Step 8: Review Instructions

If the task has no `review_instructions` (or they're empty):
- Draft review instructions based on what was implemented: what to test, how to verify, specific steps.
- Present: "**Proposed review instructions:**\n{drafted}\n\nWant to use these, edit them, or write your own?"
- Save via `backlog_update_task(task_id, "review_instructions", "{final}")`

If existing: show and ask "Keep these or update?"
