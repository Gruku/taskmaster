# Review Gate: Gate Details

Detail for the review-gate playbook: the code-review brief, spec adherence, and the report format.

## Gate 2a: code review

Work in the task's worktree when set, otherwise the project root. The base is the branch the task was cut from; the head is the task's `branch` field (the current branch when empty). Group findings Critical / Important / Minor, each with file:line and a one-line reason.

Review inline when your tool can't dispatch sub-agents. On Claude Code, a sub-agent keeps the diff out of your context:

<!-- cc-only:start -->
```
Agent({
  subagent_type: "general-purpose",   // or the superpowers code-reviewer if installed
  model: "sonnet",                    // "opus" when the diff touches auth, data deletion, migrations or other destructive paths
  description: "Review-gate code review",
  prompt: `Review the changes for a task so the orchestrator can decide whether it may close.
<task>
<id>…</id>
<title>…</title>
<acceptance_criteria>…</acceptance_criteria>
<spec>path, or "none"</spec>
<diff>git diff <base>...<branch>, run in <worktree></diff>
</task>
Look for correctness bugs, missed edge cases, unhandled error paths, tests that don't exercise the change, and sibling occurrences of any bug you find.
Run whatever you need (tests, the changed command), but don't edit files; changes are the user's call.
Reply with <findings>: one entry per finding, as severity (Critical|Important|Minor), file:line, and a one-line reason. End with one sentence naming the checks you ran. When the review is done, stop and report; don't start extra review rounds or launch further sub-agents.`
})
```
<!-- cc-only:end -->

## Gate 2c: spec adherence

For critical/high tasks with a spec or plan, read the spec and the diff side by side and answer in two or three sentences: does the diff implement what the spec describes, is anything missing or deferred without a follow-up task, and is anything in the diff that the spec doesn't cover? Mismatches are Important findings unless the user already accepted the deviation. No spec: SKIP.

## Gate 3: tests and build

Use the project's own commands (`package.json` scripts, `pytest`, `dotnet test`, `cargo test`, `go test ./...`, and their build equivalents), run from the task's `sub_repo` or worktree. No runner or build command found is SKIP.

## Report format

Lead with the overall verdict, then the matrix:

```
Gate 1  — Spec/Plan:               PASS / WARN / SKIP
Gate 2a — Code Review (Claude):    PASS / FAIL (N issues)
Gate 2b — Codex Precision:         PASS / FAIL / SKIP (not installed | opt-out)
Gate 2c — Spec Adherence:          PASS / WARN / SKIP
Gate 3  — Tests:                   PASS / FAIL / SKIP
Gate 3  — Build:                   PASS / FAIL / SKIP
```

List issues grouped by severity, tagged with source (Claude / Codex) and gate. If Codex flagged Criticals that Gate 2a missed, say so, since the user will want to read those carefully.

When gates failed, offer two paths: stay `in-progress` and address the issues, or proceed over them with `backlog_skip_gate` and the user's reason. Recording a `pass` over open Criticals would put a false verdict on the record.

## Review instructions

When the task has `review_instructions`, show them as "Manual test steps". When it has none, draft them from what was implemented (what to test, how to verify, specific steps), save them with `backlog_update_task(task_id, "review_instructions", <text>)`, and show them with a note that the user can edit them.
