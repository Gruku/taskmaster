---
name: handover
description: "Write a Claude-drafted session handover into .taskmaster/handovers/. Invoke when the user says 'write a handover', 'save context', 'wrap up', 'for tomorrow', 'next time', 'remind future me', 'i'm at 300k', 'before compaction', 'context handoff', or 'continue where we left off' (writing context). Auto-extracts files of interest, what shipped, what's next; user reviews and approves; chained supersession for milestone-complete. This is the only correct way to write a handover — do not call backlog_handover_create directly."
---

# Handover

Capture a session into `.taskmaster/handovers/{date}-{slug}.md` so the next Claude session can resume without re-exploration.

## Why this skill exists

PROGRESS.md is the rolled-up project chronology. A handover is the **per-session full record** — context-injection optimisation for the next AI session. The skill drafts a tier-appropriate body (light / standard / full), auto-extracts files of interest, and writes through `backlog_handover_create`, which updates the index and (when `supersedes` is set) edits the prior handover in place. Calling `backlog_handover_create` directly skips tier selection, auto-extraction, and supersession chaining — always go through this skill.

## When to invoke

(Filled in Task 6.)

## Steps

(Filled in Task 6.)

## References

(Filled in Task 6.)
