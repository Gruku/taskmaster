# Taskmaster Router — Disambiguation Guide

When two routes could both fire, pick correctly. This file covers the most common
ambiguous pairs. The test for "which one fires" is the user's stated intent, not the
technical trigger words — multiple trigger phrases can overlap.
Use intent to resolve, not the surface phrase.

## handover vs end-session

- **end-session** (`../../end-session/playbook.md`) is a task transition — status → done/in-review with changelog. Route there when user says "wrap up" (end-session auto-writes a handover).
- **handover** is a narrative continuity artifact — can be written without ending a task. Route directly to `taskmaster:handover` when user says "context handoff" or "for tomorrow".

## bug/issue record vs task

- **bug or issue** = a defect record (`taskmaster:bug`, `taskmaster:issue`). "Track this bug."
- **task** = a unit of work (`backlog_add_task`). "Add a task to fix this bug."
- Both can coexist for the same defect.

## bug vs issue

- **bug** — a one-off defect (`taskmaster:bug`, `../../bug/playbook.md`). "I found a bug", "file a bug", "track this defect".
- **issue** — the elevated tier for defects that are recurring (≥2 occurrences), systemic (≥2 components), or P0/P1 and outstanding (`taskmaster:issue`, `../../issue/playbook.md`). "promote B-XX to an issue", "this keeps happening".
- Colloquial "issue" with no recurring/systemic/outstanding evidence routes to **bug**, not issue.

## idea vs note

- **idea** — a parking-lot entry worth tracking on its own record. "Save this as an idea" → `taskmaster:add-idea`.
- **desk note** — an ephemeral situational scratchpad entry, not a standalone entity. "Note this for later" with no clear future-work shape → `backlog_note(action="create")`.
- If in doubt, prefer the idea route — it is the durable, searchable one.

## When Overlap Is Fine

Some phrases belong to multiple skills by design. "wrap up" triggers end-session (which auto-writes a handover). "context handoff" triggers handover directly (which doesn't transition task status). These are intentional; the user's phrasing determines which dimension they care about more.
When in doubt: pick the skill that satisfies the most urgent user need.
