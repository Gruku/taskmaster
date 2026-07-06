# pick-task — Deep Ceremony (`--deep` mode)

Invoked when the user says `pick-task --deep`, "full task briefing", "load everything for this task", or equivalent explicit depth signal.

Run all steps below after the standard glance steps complete (backlog_get_task slim + deps + open handovers + lesson match IDs + issues filtered + linkage pills).

## Deep steps

### D1. Full task body + docs

Call `backlog_get_task(<id>, verbose=True)`. This returns the full frontmatter + all body sections + inlined docs (spec, plan, design, analysis, roadmap).

### D2. Full lesson bodies

For each lesson ID returned by `backlog_lesson_match` in the glance step, call `backlog_lesson_get <id>` to load the full body. Cap at 3 lessons. Keep bodies in working context for the duration of the task.

Surface to user: "Loaded N lessons: L-007 (gotcha) auth/session.ts read-before-edit …"

Call `backlog_lesson_reinforce <id>` only on confirmed successful application during work — not on load.

### D3. Blast radius

Call `backlog_blast_radius(<id>, mode="predictive")`. Display the full structured block for critical/high tasks; one-line for medium/low.

If spec-review was already done, reference the prior analysis: "Full predictive analysis in spec-review record — calling for latest."

If overlapping in-progress tasks found, highlight: "Heads up — `{task_id}` is in-flight in the same area."

### D4. Handover bodies (context-handoff kind)

From the glance step's `backlog_handover_list(task_id=<id>, status="open")` result, identify any handover where `session_kind` is `context-handoff`. Call `backlog_handover_get <id>` (no `verbose` needed — full body by default after Plan A) for each such handover. This surfaces the "where I left off" context written at compaction.

Cap: load at most 2 handover bodies. If more, surface the others by tldr and let the user request via `backlog_handover_get <id>`.

## Deep-mode token budget

| Source | Typical |
|---|---|
| Full task body (verbose=True) | ~800 tokens |
| 3 full lesson bodies | ~900 tokens |
| Blast radius (predictive) | ~500 tokens |
| Handover bodies (context-handoff, ≤2) | ~400 tokens |
| **Total additive above glance** | ~2,600 tokens |

Glance is ~700 tokens; deep total is ~3,300.

## Pruning order (if over budget)

1. Drop lowest `reinforce_count` lesson body.
2. Drop optional handover body fetches.
3. Never prune related issues — bug context is load-bearing.

## Token-budget reference for glance path

See `references/v3-context-loading.md` for the per-source breakdown of glance step tokens (steps 5a–5c in the old numbering).

## Notes on spec-review and blast radius (deep only)

- If `task.spec_review` exists, the predictive blast-radius analysis was already done during spec-review. Show the one-line summary and reference the prior review: "Full predictive analysis in spec-review record."
- For critical/high tasks without spec-review: warn and suggest `taskmaster:spec-review` before starting implementation. Don't block; let the user decide.
- For medium/low tasks: skip blast-radius silently.

## Anchors reminder (deep only)

If `task.anchors`: display prominently and remind:
"If you find yourself editing files outside these anchors, double-check you're on the right target."
