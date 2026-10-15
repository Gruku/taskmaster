# Review Gate

Post-implementation check of a task's code: is it solid enough to close? Lead the report with the verdict; the user wants a judgement, not a data dump. This reviews the diff. Design review of the spec is `taskmaster:spec-review`; plan review is `taskmaster:plan-review`.

Arguments: `task_id` (defaults to the in-progress task); `--codex` / `--no-codex` to force the Codex pass on or off.

## What to check

Start with `backlog_get_task(task_id)` (priority, branch, worktree, spec and plan docs, review_instructions, bundle) and `backlog_context(focus=<task_id>, scope="task", include=[])`, which lists every unsatisfied gate and every open bug filed against the task.

- **Gate 1, spec/plan exists** (critical/high only): existence, not correctness.
- **Gate 2a, code review** of the diff between the task branch and its base. No branch: WARN on medium/low; on critical/high ask the user which diff to review. Delegating to a sub-agent works well; the brief is in `references/gate-details.md`.
- **Gate 2b, Codex pass** (opt-in; critical = suggest, high = offer, else off): `references/codex-integration.md`.
- **Gate 2c, spec adherence** (critical/high with a spec): `references/gate-details.md`.
- **Gate 3, tests and build**, using the project's own commands in the task's worktree or sub_repo. None found is SKIP, not a failure.

Severity decides what blocks: Critical blocks. Important needs the user's acknowledgment. Minor, WARN and SKIP never block.

## Report and record

Show the verdict first, then the gate matrix and the findings grouped by severity, tagged by source (Claude or Codex). Matrix format: `references/gate-details.md`. If the task has `review_instructions`, show them as manual test steps. If it has none, draft them from what was built, save them with `backlog_update_task(task_id, "review_instructions", <text>)`, and tell the user they can edit them.

Record the honest verdict, because the server lets a task reach `done` only on a recorded `pass` or an audited skip:
`backlog_record_gate(task_id, "review-gate", verdict="pass"|"warn"|"fail", commit_sha=<HEAD>, critical_count=<n>)`.
When the user wants to proceed over findings, use `backlog_skip_gate(task_id, "review-gate", reason)` with their reason; don't record a `pass` over open Criticals. The task stays `in-progress`; end-session closes it.

If `backlog_context` listed open bugs, walk each through `taskmaster:bug` (fix now / spawn task / shelve / promote). `backlog_complete_task` refuses while any stay open, so resolving them here saves a failed close later.

## Bundles

A task whose `bundle` field is set (or whose pick said `Picking bundle <slug>`) shares one diff with its bundle mates. List the members with `backlog_claim(action="status", task_id=<id>)` (`members`), review the shared diff once, and give one verdict per member against that member's acceptance criteria, recording a gate per member. A failing member is either fixed in the same worktree and re-gated alone, or descoped explicitly. Details: `references/bundle-gate.md`.

## Verifying writes

A mutating result ending in `[seq N]` is committed. `(export pending: …)` means the write committed and only the file export is pending. `No change … already …` without `[seq]` means the value was already stored. `backlog_store_status` shows dirty and quarantined files and live sessions.
