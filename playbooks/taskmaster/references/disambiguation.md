# Taskmaster Router — Disambiguation Guide

When two routes could both fire, pick correctly. This file covers the four most common
ambiguous pairs. The test for "which one fires" is the user's stated intent, not the
technical trigger words — multiple trigger phrases can overlap.
Use intent to resolve, not the surface phrase.

## handover vs end-session

- **end-session** is a task transition — status → done/in-review with changelog. Route there when user says "wrap up" (end-session itself offers handover write).
- **handover** is a narrative continuity artifact — can be written without ending a task. Route directly to `taskmaster:handover` when user says "context handoff" or "for tomorrow".

## issue vs task

- **issue** = a bug record (`taskmaster:issue`). "Track this bug."
- **task** = a unit of work (`backlog_add_task`). "Add a task to fix this bug."
- Both can coexist for the same defect.

## lesson vs note

- **Task notes** — scratch space for one task. "Note this for the task."
- **Lesson** — project-wide guidance. "Remember this for next time you touch auth." → `taskmaster:lesson`.

## recap vs last_session

- **last_session** — what *you* did.
- **recap** — what changed in the *project state* (any source). At session start, both render — they're complementary.

## When Overlap Is Fine

Some phrases belong to multiple skills by design. "wrap up" triggers end-session (which auto-writes a handover). "context handoff" triggers handover directly (which does NOT transition task status). These are intentional; the user's phrasing determines which dimension they care about more.
When in doubt: pick the skill that satisfies the most urgent user need.
