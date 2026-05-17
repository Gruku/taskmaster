# Spec Review — Adversarial Steps Detail

This file contains the full step prose for Gates A-D and post-gate recording steps.

## Gate A: Spec Sanity (always runs)

Read the spec/plan. Check it covers, at minimum:
- **Goal** — what problem this solves and why now.
- **Approach** — the chosen design, not just "we'll add X."
- **Scope** — what's in and what's explicitly out.
- **Risk / open questions** — at least one.
- **Target files or modules** — even a rough list helps blast radius.

For each missing item: WARN, list what's missing, ask if the user wants to fill it in or proceed anyway.

## Gate B: Adversarial Design Review (Claude)

Read the spec and enough of the codebase to ground the critique. Challenge the design across these axes — be specific, cite files/lines:

- **Assumptions** — what does this spec assume that may not hold? (existing data shape, API contracts, user behavior, performance budget)
- **Scope creep / scope gaps** — does the spec do too much? Too little? Are there obvious sibling cases excluded?
- **Alternative approaches** — is there a simpler / smaller / safer way? Name one or two and say why the spec's approach is or isn't preferable.
- **Failure modes** — where will this break first under load, partial failure, concurrent writes, malformed input?
- **Reversibility** — if this ships and is wrong, how hard is it to undo?
- **Dependencies & coupling** — does this entangle modules that should stay separate?

Group findings by severity:
- **Critical** — design has a fundamental flaw; should not proceed without rework.
- **Important** — should be addressed in spec before implementation, may proceed with explicit acknowledgment.
- **Minor** — worth considering, not blocking.

## Gate D: Predicted Blast Radius

Call `backlog_blast_radius(task_id, mode="predictive")`. Interpret the result:
- Modules and subsystems likely affected.
- Overlapping in-progress tasks — call out conflicts loudly: "task X is in-progress in the same area."
- Existing features that may need updating.
- 2-4 specific suggested follow-ups or additional tasks the spec implies but doesn't cover.

Verdict (advisory only — never blocks):
- **PASS** — low fan-out, no overlaps, well-contained.
- **WARN** — moderate fan-out, or shared modules.
- **WARN (loud)** — overlapping in-progress task on the same files.

## Step 7: Record the Review

Save a record on the task so it's visible to `pick-task`, `review-gate`, and the dashboard:

```
backlog_set_spec_review(
  task_id,
  verdict="pass" | "warn" | "fail",
  spec_path="<path that was reviewed>",
  codex_used=true | false,
  critical_count=N,
  important_count=N,
)
```

If the spec is revised later, re-running spec-review overwrites the record automatically. To explicitly invalidate a prior review, call `backlog_clear_spec_review(task_id)`.

## Step 7a: Persist Spec into Task Body (v3 only)

If `backlog_status` shows `schema_version >= 3`:

- Read the spec content (the file at `spec_path`, or inline plan if no external file).
- Read the current task body via the per-task file (`tasks/<task_id>.md`).
- Update the body's `## Spec / Plan` section with the spec content, then append a `### Spec Review` block:
  ```
  ### Spec Review
  - Verdict: pass | warn | fail
  - Date: YYYY-MM-DD
  - Source: spec at <path>
  - Findings: critical=N, important=N (codex: yes|no)
  ```
- Write the updated body back via `backlog_update_task(task_id, body=<new body>)`.

This makes the spec discoverable from the task body in `pick-task` — no need to chase a separate `docs.spec` file. On v2 backlogs: skip this step entirely.
