---
name: review-gate
description: "Run quality checks on a task's implementation before marking it ready for user testing. Invoke when the user says 'is this ready?', 'run the review gate', 'check my work', or 'I think this is done'. Reviews code for defects and spec adherence, runs tests and build, transitions task to in-review. For pre-implementation design review, use taskmaster:spec-review instead."
---

# Review Gate

Post-implementation quality gate. The user has finished implementing something and wants to know if it's solid. Lead with the overall verdict — they want validation, not a data dump.

This skill reviews **code** (the diff). For pre-implementation design review of the spec/plan, use `taskmaster:spec-review`.

## Arguments

- `task_id` (optional) — specific task ID; defaults to current in-progress task.
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex precision pass. Default: critical = auto-suggest; high = offer; medium/low = never. See `references/codex-integration.md`.

## Steps

| Step | What |
|---|---|
| 1 | `backlog_get_task` — priority, branch, docs, review_instructions, spec_review record |
| 2 | Gate 1: Spec/Plan Check (critical/high only — checks existence, not correctness) |
| 3 | Gate 2a: Claude code review of the diff (see `references/gate-details.md`) |
| 4 | Gate 2b: Codex pass (opt-in) — see `references/codex-integration.md` |
| 5 | Gate 2c: Spec adherence for critical/high with a spec (see `references/gate-details.md`) |
| 6 | Gate 3: Tests + Build (auto-detect runner — see `references/gate-details.md`) |
| 7 | Present results — lead with verdict, gate matrix (see `references/gate-details.md`) |
| 8 | Add review instructions if absent (see `references/gate-details.md`) |
| 8b | Bug close-gate — query open bugs before transitioning (see below) |
| 9 | Record the gate + transition to `in-review` if all blocking gates passed |

**Blocking rules:** Critical findings block unconditionally. Important require user acknowledgment. Minor and WARN/SKIP never block.

**Gate recording.** After reaching a verdict, call:
```
backlog_record_gate(task_id, "review-gate", verdict="pass"|"warn"|"fail",
                    commit_sha=<current sha>, critical_count=<n>)
```
A task cannot move to `done` until review-gate is `pass` (or explicitly skipped) — the data layer enforces this. If completion is blocked, surface `backlog_task_pipeline(task_id)` to show the outstanding gates.

Test runner detection, build detection, Codex Case A/B framing, gate matrix format, and review instructions handling in `references/gate-details.md`.

### Bug close-gate

Before transitioning the task (step 9), query open Bugs linked via `found_in`:

```
backlog_bug_list(status="open", found_in="<active-task-id>")
```

If the result is non-empty:

> "Task **T-XXX** has **N** open bug(s) linked via `found_in`:
> - B-NNN — title
> - B-MMM — title
>
> Resolve each before close. For each open bug, choose disposition: fix-now / spawn-task / shelve / promote."

Walk the disposition entry point in `taskmaster:bug` for each open bug. Only proceed to task transition when all linked bugs are non-`open`.

Note: `backlog_complete_task` enforces this server-side too — the skill just gives the user the chance to resolve interactively before hitting the server gate.

## Related Reviewers (NOT part of this gate)

- **`taskmaster:spec-review`** — pre-implementation adversarial review of the spec/plan.
- **`/code-review`** (claude-plugins-official) — post-PR review, fans out agents, posts to GitHub.
- **`/codex:adversarial-review`** — challenges design choices; spec-review uses a prose-friendly equivalent.
