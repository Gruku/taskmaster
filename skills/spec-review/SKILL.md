---
name: spec-review
description: "Adversarial design review of a task's spec before implementation begins. This skill should be used when the user says 'review this spec', 'challenge this design', 'is this the right approach?', 'spec review', or 'design review'. Records the spec-review gate (full lane) or design-review gate (standard lane). Reviews approach for assumptions, scope, edge cases, and blast radius. Does not review code — use taskmaster:review-gate for post-implementation review."
---

# Spec Review

Pre-implementation design gate. The task has a written spec/plan and the question is whether the *approach* is sound before any code gets written. This is adversarial — challenge the design, don't pat it on the back.

This skill does NOT review code. For post-implementation code review, use `taskmaster:review-gate`. For reviewing the implementation plan (full lane, after this gate), use `taskmaster:plan-review`.

## Arguments

- `task_id` (optional) — specific task ID; defaults to current task in scope.
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex adversarial pass. Default: `critical` priority = auto-suggest; `high` = offer; otherwise never. See `references/codex-integration.md`.

## Lane Context

This skill runs the **first verdict gate** of the task's lane. Read `task.lane` before anything else — it decides the gate name, the ceremony depth, and whether a gate is recorded at all:

| `task.lane` | Gate recorded | Ceremony |
|---|---|---|
| `full` | `spec-review` | All gates A–D; Codex per priority default |
| `standard` | `design-review` | Same gates, lighter depth; Gate C only on explicit `--codex` |
| `express` / no lane | none | Advisory only — present findings, record no gate |

Pipelines (pipeline position, not status — `pick-task` is the status transition):

- full: `spec → spec-review → pick-task → plan → plan-review → tests → impl → review-gate`
- standard: `spec → design-review → pick-task → tests → impl → review-gate`

Run after the spec is written, on demand for a second opinion, or re-run after a significant spec revision (re-running overwrites the prior record; `backlog_clear_gate(task_id, gate)` explicitly invalidates one).

## Steps

**1. Get task details.** `backlog_get_task(task_id)`. Read `task.lane` and resolve the gate name from the table above. Resolve spec/plan path: check `task.docs.spec`/`task.docs.plan`, then search `docs/specs/`, `docs/plans/`, `{sub_repo}/docs/`, and the task body's `## Spec / Plan` section. If no spec found: stop. Tell the user "No spec/plan found — write one first."

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

**Verdict rules — server truth: only `pass` (or an explicit skip) satisfies a gate; `warn` leaves it pending.**
- **pass** — no unresolved Critical findings; Important findings fixed in the spec or explicitly acknowledged by the user. Record honest `critical_count`/`important_count` either way — `pass` with `important_count=2` is the canonical shape for "acknowledged and proceeding."
- **warn** — Important findings the user has neither addressed nor acknowledged. The gate stays `<gate>:pending`: later verdict gates cannot be recorded and `done` is blocked until a re-review passes or the user explicitly skips (`backlog_skip_gate`, audited).
- **fail** — Critical design flaw; `gate_state` becomes `blocked@<gate>`. Revise the spec and re-run.

**7. Record the review** (full/standard lanes only — express records nothing). Call `backlog_record_gate(task_id, gate, verdict=<verdict>, spec_path=<path>, codex_used=<bool>, critical_count=<n>, important_count=<n>)` with the lane-resolved gate name. `backlog_set_spec_review` is an alias that hardcodes the `spec-review` gate — never use it for a standard-lane task. Full recording steps including v3 spec persistence into task body: `references/adversarial-steps.md`.

**8. Next step.**
- **pass**, full lane: "Ready to `pick-task {task_id}` — plan + `plan-review` come before tests."
- **pass**, standard lane: "Ready to `pick-task {task_id}`."
- **warn**: "Address or acknowledge the Important findings and re-run, or `backlog_skip_gate` with a reason to proceed anyway."
- **fail**: "Revise the spec and re-run spec-review."

## Additional Resources

- `references/adversarial-steps.md` — full Gate A-D prose + recording detail
- `references/codex-integration.md` — Codex dispatch pattern, Case A/B

## Related

- `taskmaster:plan-review` — adversarial review of the implementation plan (full lane, after this gate).
- `taskmaster:review-gate` — post-implementation code review.
- `taskmaster:pick-task` — start implementation after this gate passes.
