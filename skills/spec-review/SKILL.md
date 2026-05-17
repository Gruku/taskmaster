---
name: spec-review
description: "Adversarial design review of a task's spec before implementation begins. Invoke when the user says 'review this spec', 'challenge this design', 'is this the right approach?', or 'spec review'. Reviews approach for assumptions, scope, edge cases, and blast radius. Does not review code — use taskmaster:review-gate for post-implementation review."
---

# Spec Review

Pre-implementation design gate. The user has a task with a written spec/plan and wants to know whether the *approach* is sound before any code gets written. This is adversarial — challenge the design, not pat it on the back.

This skill does NOT review code. For post-implementation code review, use `taskmaster:review-gate`.

## Arguments

- `task_id` (optional) — specific task ID; defaults to current task in scope.
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex adversarial pass. Default: critical = auto-suggest; high = offer; medium/low = never. See `references/codex-integration.md`.

## When This Should Run

Lifecycle: `todo (with spec)` -> **spec-review** -> `pick-task` -> `in-progress` -> ... -> `review-gate` -> `in-review`.

Run: after writing a spec for a critical/high task (before `pick-task`); on demand for a second opinion; re-run if spec was significantly revised. Skip: no spec/plan exists, or medium/low tasks.

## Steps

**1. Get task details.** `backlog_get_task(task_id)`. Resolve spec/plan path: check `task.docs.spec`/`task.docs.plan`, then search `docs/specs/`, `docs/plans/`, `{sub_repo}/docs/`. If no spec found: stop. Tell the user "No spec/plan found — write one first."

**2. Gate A: Spec sanity.** Check spec covers: Goal, Approach, Scope, Risk/open questions, Target files. For each missing item: WARN, ask if user wants to fill or proceed. Full axis list: `references/adversarial-steps.md`.

**3. Gate B: Adversarial Design Review (Claude).** Challenge across: Assumptions, Scope creep/gaps, Alternative approaches, Failure modes, Reversibility, Dependencies & coupling. Cite files/lines. Group by Critical/Important/Minor. Full axis details: `references/adversarial-steps.md`.

**4. Gate C: Codex Adversarial Pass (opt-in).** Skip if Codex not detected. Dispatch `codex:codex-rescue` subagent focused on blind spots Gate B didn't flag. Tag findings `(codex)`. Full dispatch pattern: `references/codex-integration.md`.

**5. Gate D: Blast Radius.** `backlog_blast_radius(task_id, mode="predictive")`. Surface overlapping in-progress tasks loudly; list 2-4 implied follow-ups. Advisory only — never blocks.

**6. Present results — lead with verdict.**
```
Gate A — Spec Sanity:           PASS / WARN
Gate B — Adversarial (Claude):  PASS / FAIL (N issues)
Gate C — Adversarial (Codex):   PASS / FAIL / SKIP
Gate D — Blast Radius:          PASS / WARN (advisory)
```
Blocking: Critical blocks; Important requires acknowledgment; Minor/WARN/Skip never block.

**7. Record the review.** `backlog_set_spec_review(task_id, verdict, spec_path, codex_used, critical_count, important_count)`. Full recording steps including v3 spec persistence into task body: `references/adversarial-steps.md`.

**8. Next step.** PASS/WARN: "Ready to `pick-task {task_id}`." FAIL: "Revise the spec and re-run spec-review."

## Additional Resources

- `references/adversarial-steps.md` — full Gate A-D prose + step 7/7a detail
- `references/codex-integration.md` — Codex dispatch pattern, Case A/B

## Related

- `taskmaster:review-gate` — post-implementation code review.
- `taskmaster:pick-task` — start implementation after spec-review passes.
