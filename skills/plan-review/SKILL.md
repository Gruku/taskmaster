---
name: plan-review
description: "Adversarial review of a task's implementation plan before writing tests. This skill should be used when the user says 'review this plan', 'plan review', 'challenge my plan', or 'is this plan solid'. The full-lane gate between PLAN and WRITE_TESTS. Does not review code — use taskmaster:review-gate for post-implementation review."
---

# Plan Review

Pre-test adversarial gate for the `full` lane. The task has a written implementation plan and the goal is to challenge it before a single line of test or production code is written.

This skill does NOT review code. For post-implementation code review, use `taskmaster:review-gate`. For spec/approach review before the plan exists, use `taskmaster:spec-review`.

## Lane context

This gate sits between **PLAN** and **WRITE_TESTS** in the `full` lane:

```
spec → spec-review → plan → plan-review → tests → impl → review-gate
```

`standard`-lane tasks skip this gate (they use `design-review` instead). Only proceed with plan-review when `task.lane == "full"`.

## Arguments

- `task_id` (optional) — specific task ID; defaults to current task in scope.
- `--codex` / `--no-codex` (optional) — force enable/skip Codex adversarial pass.

## Steps

**1. Get task details.** `backlog_get_task(task_id)`. Resolve plan path: check `task.docs.plan`, then search `docs/plans/`, `tasks/<id>.md` `## Spec / Plan` section. If no plan found: stop. Tell the user "No implementation plan found — write one first."

**2. Gate A: Plan sanity.** Verify the plan covers: Goal recap, Implementation approach (step-by-step), Files/modules touched, Risk / open questions, Rollback / reversibility. WARN on each missing element. Do not block on warnings — ask if user wants to fill or proceed.

**3. Gate B: Adversarial Plan Review (Claude).** Challenge across:
- **Assumptions** — what must be true for this plan to work?
- **Scope gaps** — steps missing that will be discovered mid-impl?
- **Ordering** — will the test-first order expose circular dependencies?
- **Failure modes** — what breaks silently if a step is wrong?
- **Blast radius** — which other tasks/components does this plan touch unexpectedly?
- **Reversibility** — can each step be undone without data loss?

Group findings: Critical / Important / Minor. Cite specific plan sections or file names.

**4. Gate C: Codex Adversarial Pass (opt-in).** Skip if Codex not detected. Dispatch focused on blind spots Gate B didn't flag. Tag findings `(codex)`.

**5. Present results — lead with verdict.**
```
Gate A — Plan Sanity:           PASS / WARN
Gate B — Adversarial (Claude):  PASS / FAIL (N issues)
Gate C — Adversarial (Codex):   PASS / FAIL / SKIP
```
Blocking: Critical blocks; Important requires acknowledgment; Minor/WARN/Skip never block.

**6. Record the review.** Call `backlog_record_gate(task_id, "plan-review", verdict=<verdict>, critical_count=<n>, important_count=<n>)`. Pass `codex_used=True` if Gate C ran.

**7. Next step.** PASS/WARN: "Ready to `write-tests` for `{task_id}`." FAIL: "Revise the plan and re-run plan-review."

## Related

- `taskmaster:spec-review` — adversarial review of the spec/approach (runs before plan-review in full lane).
- `taskmaster:review-gate` — post-implementation code review.
