# Plan Review

Full-lane gate between the implementation plan and writing tests: challenge the plan before any test or production code exists. The spec was already reviewed by `taskmaster:spec-review` (`../spec-review/playbook.md`, which states the gate order per lane); code is reviewed later by `taskmaster:review-gate`.

Arguments: `task_id` (defaults to the task in scope); `--codex` / `--no-codex` to force the Codex pass on or off (default: critical = suggest, high = offer, else off).

## Review

`backlog_get_task(task_id)`. This gate applies only to `lane: full`. On any other lane, say the gate doesn't apply (a standard-lane task has `design-review`, run by `taskmaster:spec-review`, `../spec-review/playbook.md`) and stop.

Find the plan in `task.docs.plan`, then `docs/plans/`, then the task's `## Spec / Plan` section. None found: say a plan has to be written first and stop.

Report each check as a gate row and keep going; questions go in the closing message.
- **Gate A, plan sanity:** goal recap, step-by-step approach, files touched, risks, rollback. Each missing item is a WARN row.
- **Gate B, adversarial review (you):** assumptions that must hold for the plan to work; steps that will be discovered mid-implementation; ordering (does test-first expose a circular dependency?); what fails silently if a step is wrong; unexpected blast radius onto other tasks or components; steps that can't be undone without data loss. Cite plan sections or files; group Critical / Important / Minor.
- **Gate C, Codex pass (opt-in):** same defaults and dispatch as `../spec-review/references/codex-integration.md`, aimed at what Gate B didn't flag. Tag findings `(codex)`.

## Verdict, record, next step

Show the verdict first, then the rows: A PASS/WARN, B PASS/FAIL (N issues), C PASS/FAIL/SKIP.

Verdicts follow `../spec-review/playbook.md` ("Report, record, next step"): `pass` (no open Critical, Importants fixed or acknowledged), `warn` (Importants unaddressed; gate stays pending), `fail` (a Critical flaw in the plan).

Record with `backlog_record_gate(task_id, "plan-review", verdict, critical_count=<n>, important_count=<n>, codex_used=<bool>)`.

Next: pass means write the tests. warn means address or acknowledge the findings and re-run, or skip with `backlog_skip_gate(task_id, "plan-review", reason)`. fail means revise the plan and re-run.
