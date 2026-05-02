---
name: handover
description: "Write a Claude-drafted session handover into .taskmaster/handovers/. Invoke when the user says 'write a handover', 'save context', 'wrap up', 'for tomorrow', 'next time', 'remind future me', 'i'm at 300k', 'before compaction', 'context handoff', or 'continue where we left off' (writing context). Auto-extracts files of interest, what shipped, what's next; user reviews and approves; chained supersession for milestone-complete. This is the only correct way to write a handover — do not call backlog_handover_create directly."
---

# Handover

Capture a session into `.taskmaster/handovers/{date}-{slug}.md` so the next Claude session can resume without re-exploration.

## Why this skill exists

PROGRESS.md is the rolled-up project chronology. A handover is the **per-session full record** — context-injection optimisation for the next AI session. The skill drafts a tier-appropriate body (light / standard / full), auto-extracts files of interest, and writes through `backlog_handover_create`, which updates the index and (when `supersedes` is set) edits the prior handover in place. Calling `backlog_handover_create` directly skips tier selection, auto-extraction, and supersession chaining — always go through this skill.

## When to invoke

Three trigger contexts:

1. **Explicit user invocation** — phrases listed in the skill description above.
2. **Auto-offer from end-session** — end-session calls into this skill when its v3-pre-2 heuristics fire (see end-session SKILL.md).
3. **Auto-offer mid-session** — when the orchestrator detects token-watch thresholds (≥200k or ≥270k), it offers this skill: *"You're approaching compaction. Write a handover now?"*

In all three cases the user is asked first. Never write a handover silently.

## Steps

### 1. Resolve session_kind

Pick exactly one value from `references/session-kinds.md`. The default is `end-of-day`. Override based on cues:

- User said "milestone done", "chunk complete", "ready for next plan" → `milestone-complete`
- User said "context handoff", "near compaction", "300k", "save before compact" → `context-handoff`
- User said "we changed direction", "pivoting", "new approach" → `pivot`
- Session had no in-flight task (no `in-progress` task touched, no commits to a feature) → `exploration`
- Auto-task or auto-epic invoked the skill mid-loop → `auto-stage`
- Otherwise → `end-of-day`

If unsure between two kinds, ask the user with `AskUserQuestion`.

### 2. Select a tier

Apply the heuristic table in `references/tier-selection.md`. The user can force a tier with `--light`, `--standard`, or `--full`; respect the override.

### 3. Auto-extract draft inputs

Walk the six sources in `references/auto-extraction.md` in order. Output a deduplicated, grouped list of paths under three buckets: **Touched** (sources 1, 2, 3 ∩ written), **Read** (source 3 ∩ read-only), **Relevant** (sources 4, 5 minus the first two).

For each path, write a one-line `what changed` and a one-line `why next session needs it`. **Never skip annotations** — bare paths defeat the optimisation.

### 4. Resolve `task_ids`

- `auto-stage`, `milestone-complete`, `pivot`, `context-handoff`: prefer the in-progress task's id; fall back to the task ids from any commits in this session.
- `end-of-day`: the in-progress task id (or last-touched task id).
- `exploration`: leave empty (`[]`). Do not invent a task id.

### 5. Determine `supersedes`

Set `supersedes = <prior_id>` when **all** of:

- `session_kind in {"milestone-complete", "pivot"}`
- The prior latest handover for the same `task_ids` exists
- That prior latest is also `milestone-complete` or `pivot`

Look up the prior with `backlog_handover_list(limit=10)` and pick the newest entry whose `task_ids` overlap. See `references/supersession.md` for the exact algorithm.

### 6. Draft the body from the tier template

Open the matching template under `templates/`:

- `templates/light.md` — light tier, ~10–30 lines
- `templates/standard.md` — standard tier, ~60–130 lines
- `templates/full.md` — full tier, ~150–200 lines

Fill it. Concrete content only — never leave a `{placeholder}`. If a section has no content (e.g., no pending commits, no dispatch templates), **delete the section** rather than leaving it empty.

### 7. Generate `tldr` and `next_action`

- `tldr`: one sentence, ≤ 100 chars, past-tense, what shipped.
- `next_action`: one sentence, ≤ 100 chars, imperative, what the next session should do first.

These two fields are the only thing the next session reads by default — they earn their tokens.

### 8. Present draft for user review

Show the user the assembled draft as one document with section labels. Then ask:

> "Looks good? I can drop sections, add files of interest, or rewrite the next-action."

Iterate until the user says it's good. Do **not** write the file before approval.

### 9. Write through `backlog_handover_create`

Call:

```
backlog_handover_create(
    tldr=...,
    next_action=...,
    body=<approved markdown body, no frontmatter>,
    task_ids=[...],
    session_kind="...",
    context_size_at_write="<optional, e.g. ~250k>",
    supersedes="<prior id or empty>",
    branch="<git branch from `git rev-parse --abbrev-ref HEAD`>",
    tip_commit="<git tip from `git rev-parse --short HEAD`>",
)
```

The server writes the file, syncs the index, and (if `supersedes` is set) edits the old handover in place.

### 10. Confirm

Echo back: *"Handover written: `<id>`. Next session can resume from this with `backlog_handover_latest`."*

If the response includes a `WARNING` line about `supersedes` not found, surface that to the user — do not hide it.

## Edge cases

- **Multiple in-progress tasks** — list them, ask the user which is the primary; use that as `task_ids[0]` and append the others.
- **No backlog** — `backlog_handover_create` returns `Error: no backlog found`. Tell the user to run `backlog_init` first.
- **Server sandbox at wrong cwd** — if the MCP tool error mentions a path mismatch, do not retry; tell the user to restart Claude Code from inside the project root and skip the write for this session (offer to print the draft instead so it isn't lost).
- **Auto-stage** — when invoked from `auto-task`, the orchestrator passes `session_kind="auto-stage"` and a stub-only body (frontmatter is the load-bearing part). Skip user review for this path.
- **`--light` override on a heavy session** — trim output to the light template, but still run auto-extraction; we don't want a `--light` flag to mask information the user wanted captured.

## References

- `references/session-kinds.md` — the six kinds, resume-load behavior, archive policy
- `references/tier-selection.md` — heuristic table, override flags
- `references/auto-extraction.md` — six sources, dedup grouping rules, regex specs
- `references/supersession.md` — chained-supersession algorithm
- `templates/light.md`, `templates/standard.md`, `templates/full.md` — body skeletons

## Spec

`docs/superpowers/specs/2026-05-02-handover-skill-design.md`
