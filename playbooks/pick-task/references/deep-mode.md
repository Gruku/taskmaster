# pick-task: deep briefing (`--deep`)

Runs when the user asks for `pick-task --deep`, "full task briefing", "load everything for this task", or an equivalent explicit depth signal, after the glance in `../playbook.md` (claim status, context answer, linkage).

## Reads

- **D1. Full task body and docs:** `backlog_get_task(<id>, verbose=True)` returns the frontmatter, every body section and the inlined docs (spec, plan, design, analysis, roadmap).
- **D2. Blast radius:** `backlog_blast_radius(<id>, mode="predictive")`. Show the full structured block for critical and high tasks, one line for medium and low. When a spec-review already ran, its record holds the earlier analysis; mention it and call for the latest anyway. When an overlapping task is in progress in the same area, say so: "Heads up, `{task_id}` is in flight in the same area."
- **D3. Handover bodies:** from `selected.handovers` in the context answer, take any handover whose `session_kind` is `deep-context` (a pre-compaction capture) and read it with `backlog_handover_get <id>`. Load at most 2 bodies and list the rest by tldr for the user to request.

Related issues stay in the briefing at every depth, because they are what stops a fixed defect from coming back.

## Notes

- A critical or high task without a spec-review: suggest `taskmaster:spec-review` before implementation; the user decides.
- `anchors`: show them prominently and add "if you find yourself editing files outside these anchors, double-check you're on the right target."
