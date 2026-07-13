# Handover

Capture a session into `.taskmaster/handovers/{date}-{slug}.md` so the next Claude session can resume without re-exploration.

## Why this skill exists

A handover is the per-session full record — context-injection optimisation for the next AI session. The skill auto-extracts files of interest and writes through `backlog_handover_create`, which updates the index and (when `supersedes` is set) edits the prior handover in place. Calling `backlog_handover_create` directly skips auto-extraction and supersession chaining.

## When to invoke

1. **Explicit user invocation** — phrases listed in the description above.
2. **Auto-offer from end-session** — when v3-pre-2 heuristics fire.
3. **Auto-offer mid-session** — at token-watch thresholds (>=200k or >=270k).

## Steps

**1. Resolve `session_kind`.** Pick from `references/session-kinds.md`. Default: `continuity`. Override: "milestone done" / "chunk complete" -> `milestone`; "context handoff" / "300k" -> `deep-context`. Ask the user if unsure (use your structured-question tool if available).

**2. Auto-extract draft inputs.** Walk the six sources in `references/auto-extraction.md`. Output deduplicated paths under: Touched / Read / Relevant. Each path needs one-line `what changed` and `why next session needs it` — bare paths defeat the purpose.

**3. Resolve `task_ids`.** In-progress task id for milestone/deep-context/auto-stage. Last-touched task id for continuity. Leave empty `[]` for exploration sessions — do not invent a task id.

**3b. Resolve `thread`.** The stable resume token. If this session resumed from a thread (via `backlog_thread_resume` or a pasted name), reuse that name. Otherwise leave `thread` empty — the server derives it (bundle slug → epic → task id → tldr). Only set it explicitly when the user names the line of work.

**4. Determine `supersedes`.** Set to prior id when: `session_kind == "milestone"` AND prior latest handover for same `task_ids` exists AND prior is also `milestone`. Algorithm in `references/supersession.md`.

**5. Draft body from `templates/body.md`.** Fill every section with concrete content. Delete empty sections — never leave `{placeholder}`. If end-session's decision sweep produced open/resolved decisions, embed them in the body under "Open decisions" / "Resolved this session" sections using `[[DEC-NNN]]` references — the body is the durable carrier, not a separate kwarg.

**6. Generate `tldr` and `next_action`.** Each <=100 chars. `tldr` = past-tense what shipped. `next_action` = imperative what next session does first.

**7. Write directly — no draft-and-approve.** Write immediately via step 8. Exceptions: user asked "show draft first"; milestone supersession changes a prior milestone; auto-extraction returned zero files (ask scope).

**8. Write through `backlog_handover_create`** with its top-level fields: `tldr`, `next_action`, `body`, `task_ids`, `session_kind`, `supersedes`, `thread`, `flag_for_review`. Rarely-set fields go in the `options` dict: `branch`, `tip_commit`, `context_size_at_write`, `review_reason` (e.g. `options={"branch": ..., "tip_commit": ...}`). Decisions live inside `body` (step 5), not as separate kwargs. If a `pending_review_flag` was buffered upstream, forward `flag_for_review=true` + `options={"review_reason": "<reason>"}`.

**9. Confirm.** Echo the server's final line verbatim — `Resume: <thread> — <next_action>` — as the last line of your reply. That line is the durable resume token: pasting the thread name into any future session resumes this work via `backlog_thread_resume`. Surface any WARNING line from the response.

## Manual status entry points

- `taskmaster:handover close <id>` — `backlog_handover_update_status(<id>, "closed", reason)`.
- `taskmaster:handover reopen <id>` — same with `"open"`.
- Thread level: `backlog_thread_update(<name>, "parked" | "closed" | "open", reason)` — park a line of work without touching individual handovers.

## References

- `references/session-kinds.md` — the four kinds, resume-load behavior, archive policy
- `references/auto-extraction.md` — six sources, dedup grouping rules, regex specs
- `references/supersession.md` — chained-supersession algorithm
- `references/triage.md` — triage loop algorithm
- `templates/body.md` — body skeleton
