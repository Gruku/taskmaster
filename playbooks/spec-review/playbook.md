# Spec Review

Pre-implementation design gate: the task has a written spec, and the question is whether the approach is sound before any code exists. Challenge the design rather than rubber-stamping it. This reviews the spec; code is reviewed by `taskmaster:review-gate` and the implementation plan by `taskmaster:plan-review`.

Arguments: `task_id` (defaults to the task in scope); `--codex` / `--no-codex` to force the Codex pass on or off (default: critical = suggest, high = offer, else off).

## Lane and gate order

Read `task.lane` first; it decides which gate this records.

| `task.lane` | Gate recorded | Depth |
|---|---|---|
| `full` | `spec-review` | Gates A-D; Codex per priority default |
| `standard` | `design-review` | Same gates, lighter; Gate C only on explicit `--codex` |
| `express` / no lane | none | Advisory: present findings, record nothing |

Gate order per lane (the server enforces it): full = spec, spec-review, plan, plan-review, tests, impl, review-gate. standard = spec, design-review, tests, impl, review-gate. express = impl, review-gate. `pick-task` moves status and is not a gate. Re-run this review after a significant spec revision; re-running overwrites the prior record, and `backlog_clear_gate(task_id, gate)` invalidates one.

## Review

1. `backlog_get_task(task_id)`. Find the spec in `task.docs.spec` / `task.docs.plan`, then `docs/specs/`, `docs/plans/`, `{sub_repo}/docs/`, or the task's `## Spec / Plan` section. None found: say a spec has to be written first and stop.
2. **Gate A, spec sanity:** does it cover Goal, Approach, Scope, Risk/open questions, Target files? Each missing item is a WARN row; keep reviewing.
3. **Gate B, adversarial design review (you):** assumptions, scope creep or gaps, alternative approaches, failure modes, reversibility, dependencies and coupling. Cite files and lines; group Critical / Important / Minor. Axis detail: `references/adversarial-steps.md`.
4. **Gate C, Codex pass (opt-in):** aimed at what Gate B didn't flag; tag findings `(codex)`. Pattern: `references/codex-integration.md`.
5. **Gate D, blast radius:** `backlog_blast_radius(task_id, mode="predictive")`. Put overlapping in-progress tasks first; list 2-4 follow-ups the spec implies. Advisory, never blocks.

## Report, record, next step

Verdict first, then the gate rows (A: PASS/WARN, B: PASS/FAIL with issue count, C: PASS/FAIL/SKIP, D: PASS/WARN) and the findings.

The server satisfies a gate only on `pass` or an audited skip:
- `pass`: no open Critical; Important findings fixed or acknowledged. Record honest counts either way; `pass` with `important_count=2` is the acknowledged-and-proceeding case.
- `warn`: Important findings neither fixed nor acknowledged. The gate stays pending, later gates can't record past it, and `done` stays blocked until a re-review passes or the user skips with `backlog_skip_gate(task_id, gate, reason)`.
- `fail`: a Critical design flaw; the pipeline becomes `blocked@<gate>`.

On full and standard lanes, record with `backlog_record_gate(task_id, gate, verdict, spec_path=<path>, codex_used=<bool>, critical_count=<n>, important_count=<n>)`, using the lane-resolved gate name. Use this rather than `backlog_set_spec_review`, which always writes the `spec-review` gate and so records the wrong one on a standard-lane task. Then append the short summary (`references/adversarial-steps.md`, "Task summary").

Close with the next step, and put any questions (fill the missing sections? acknowledge the Importants?) in this one closing message:
- pass: ready for `taskmaster:pick-task` (full lane: plan, then `taskmaster:plan-review`, comes before tests).
- warn: address or acknowledge the Important findings and re-run, or skip with a reason.
- fail: revise the spec and re-run.

Resources: `references/adversarial-steps.md`, `references/codex-integration.md`.
