# Spec Review

Pre-implementation design gate: the task has a written spec/plan; the question is whether the *approach* is sound before any code is written. Adversarial — challenge the design, don't rubber-stamp it. Does NOT review code (use `taskmaster:review-gate`) or the implementation plan (use `taskmaster:plan-review`, full lane, after this gate).

## Arguments

- `task_id` (optional) — defaults to the current task in scope.
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex pass. Default: `critical` = auto-suggest, `high` = offer, else never.

## Lane Context

The **first verdict gate** of the lane. Read `task.lane` first — it decides the gate name, ceremony depth, and whether a gate is recorded:

| `task.lane` | Gate recorded | Ceremony |
|---|---|---|
| `full` | `spec-review` | All gates A–D; Codex per priority default |
| `standard` | `design-review` | Same gates, lighter depth; Gate C only on explicit `--codex` |
| `express` / no lane | none | Advisory only — present findings, record no gate |

Pipeline position (not status — `pick-task` is the status transition):

- full: `spec → spec-review → pick-task → plan → plan-review → tests → impl → review-gate`
- standard: `spec → design-review → pick-task → tests → impl → review-gate`

Run after the spec is written, or re-run after a significant revision (re-running overwrites the prior record; `backlog_clear_gate(task_id, gate)` invalidates one).

## Steps

**1. Get task details.** `backlog_get_task(task_id)`. Read `task.lane`, resolve the gate name from the table. Resolve spec/plan path: check `task.docs.spec`/`task.docs.plan`, then `docs/specs/`, `docs/plans/`, `{sub_repo}/docs/`, and the task body's `## Spec / Plan` section. If none found, stop: "No spec/plan found — write one first."

**2. Gate A: Spec sanity.** Check the spec covers Goal, Approach, Scope, Risk/open questions, Target files. For each missing item: WARN, ask to fill or proceed. Full axis list: `references/adversarial-steps.md`.

**3. Gate B: Adversarial Design Review (Claude).** Challenge Assumptions, Scope creep/gaps, Alternative approaches, Failure modes, Reversibility, Dependencies & coupling. Cite files/lines. Group by Critical/Important/Minor. Details: `references/adversarial-steps.md`.

**4. Gate C: Codex Adversarial Pass (opt-in).** Skip if Codex not detected. Dispatch `codex:codex-rescue` on blind spots Gate B didn't flag. Tag findings `(codex)`. Pattern: `references/codex-integration.md`.

**5. Gate D: Blast Radius.** `backlog_blast_radius(task_id, mode="predictive")`. Surface overlapping in-progress tasks loudly; list 2-4 follow-ups. Advisory, never blocks.

**6. Present results — verdict first.**
```
Gate A — Spec Sanity:          PASS / WARN
Gate B — Adversarial (Claude): PASS / FAIL (N issues)
Gate C — Adversarial (Codex):  PASS / FAIL / SKIP
Gate D — Blast Radius:         PASS / WARN (advisory)
```

**Verdict rules — server truth: only `pass` (or an explicit skip) satisfies a gate; `warn` leaves it pending.**
- **pass** — no unresolved Critical findings; Important findings fixed or explicitly acknowledged. Record honest `critical_count`/`important_count` either way — `pass` with `important_count=2` is the canonical "acknowledged and proceeding" case.
- **warn** — Important findings neither addressed nor acknowledged. Gate stays `<gate>:pending`: later gates cannot record and `done` is blocked until a re-review passes or the user skips (`backlog_skip_gate`, audited).
- **fail** — Critical design flaw; `gate_state` becomes `blocked@<gate>`. Revise the spec and re-run.

**7. Record the review** (full/standard lanes only — express records nothing). Call `backlog_record_gate(task_id, gate, verdict=<verdict>, spec_path=<path>, codex_used=<bool>, critical_count=<n>, important_count=<n>)` with the lane-resolved gate name. `backlog_set_spec_review` is an alias hardcoding the `spec-review` gate — never use it on a standard-lane task. Full recording steps + v3 spec persistence: `references/adversarial-steps.md`.

**8. Next step.**
- **pass**, full lane: "Ready to `pick-task {task_id}` — plan + `plan-review` come before tests."
- **pass**, standard lane: "Ready to `pick-task {task_id}`."
- **warn**: "Address or acknowledge the Important findings and re-run, or `backlog_skip_gate` with a reason."
- **fail**: "Revise the spec and re-run spec-review."

## Resources & related

- `references/adversarial-steps.md` — full Gate A-D prose + recording detail
- `references/codex-integration.md` — Codex dispatch pattern, Case A/B
- `taskmaster:plan-review` — plan review (full lane, after this gate); `taskmaster:review-gate` — post-implementation code review; `taskmaster:pick-task` — start implementation once this gate passes.
