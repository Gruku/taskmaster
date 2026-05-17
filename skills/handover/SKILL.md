---
name: handover
description: "Write a session handover into .taskmaster/handovers/. Invoke when the user says 'write a handover', 'wrap up', 'for tomorrow', 'before compaction', 'context handoff', or 'continue where we left off'. This is the only correct way to write a handover — do not call backlog_handover_create directly."
---

# Handover

Capture a session into `.taskmaster/handovers/{date}-{slug}.md` so the next Claude session can resume without re-exploration.

## Why this skill exists

A handover is the per-session full record — context-injection optimisation for the next AI session. The skill auto-extracts files of interest and writes through `backlog_handover_create`, which updates the index and (when `supersedes` is set) edits the prior handover in place. Calling `backlog_handover_create` directly skips auto-extraction and supersession chaining.

## When to invoke

1. **Explicit user invocation** — phrases listed in the description above.
2. **Auto-offer from end-session** — when v3-pre-2 heuristics fire.
3. **Auto-offer mid-session** — at token-watch thresholds (>=200k or >=270k).

## Steps

**1. Resolve `session_kind`.** Pick from `references/session-kinds.md`. Default: `continuity`. Override: "milestone done" / "chunk complete" -> `milestone`; "context handoff" / "300k" -> `deep-context`; from auto-task loop -> `auto-stage`. Ask with `AskUserQuestion` if unsure.

**2. Auto-extract draft inputs.** Walk the six sources in `references/auto-extraction.md`. Output deduplicated paths under: Touched / Read / Relevant. Each path needs one-line `what changed` and `why next session needs it` — bare paths defeat the purpose.

**3. Resolve `task_ids`.** In-progress task id for milestone/deep-context/auto-stage. Last-touched task id for continuity. Leave empty `[]` for exploration sessions — do not invent a task id.

**4. Determine `supersedes`.** Set to prior id when: `session_kind == "milestone"` AND prior latest handover for same `task_ids` exists AND prior is also `milestone`. Algorithm in `references/supersession.md`.

**5. Draft body from `templates/body.md`.** Fill every section with concrete content. Delete empty sections — never leave `{placeholder}`. Pass open/resolved decisions as `open_decisions=[...]` / `resolved_this_session=[...]` to `backlog_handover_create`; reference inline with `[[DEC-NNN]]`.

**6. Generate `tldr` and `next_action`.** Each <=100 chars. `tldr` = past-tense what shipped. `next_action` = imperative what next session does first.

**7. Write directly — no draft-and-approve.** Write immediately via step 8. Exceptions: user asked "show draft first"; milestone supersession changes a prior milestone; auto-extraction returned zero files (ask scope).

**8. Write through `backlog_handover_create`** with all fields (tldr, next_action, body, task_ids, session_kind, open_decisions, resolved_this_session, context_size_at_write, supersedes, branch, tip_commit). If `pending_review_flag` was buffered by lesson skill, forward `flag_for_review=true` + `review_reason=<reason>`.

**9. Confirm.** "Handover written: `<id>`. Next session can resume from this with `backlog_handover_latest`." Surface any WARNING line from the response.

## Manual status entry points

- `taskmaster:handover mark-done <id>` — `backlog_handover_update_status(<id>, "done", reason)`.
- `taskmaster:handover mark-in-progress <id>` — same with `"in-progress"`.
- `taskmaster:handover mark-todo <id>` — same with `"todo"`. Undoes erroneous mark-done.
- `taskmaster:handover triage` — walk todo handovers older than 14 days. Full algorithm: `references/triage.md`.

## References

- `references/session-kinds.md` — the four kinds, resume-load behavior, archive policy
- `references/auto-extraction.md` — six sources, dedup grouping rules, regex specs
- `references/supersession.md` — chained-supersession algorithm
- `references/triage.md` — triage loop algorithm
- `templates/body.md` — body skeleton
