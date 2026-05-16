---
name: handover
description: "Write a Claude-drafted session handover into .taskmaster/handovers/. Invoke when the user says 'write a handover', 'save context', 'wrap up', 'for tomorrow', 'next time', 'remind future me', 'i'm at 300k', 'before compaction', 'context handoff', or 'continue where we left off' (writing context). Auto-extracts files of interest, what shipped, what's next; chained supersession for milestone kind. This is the only correct way to write a handover — do not call backlog_handover_create directly."
---

# Handover

Capture a session into `.taskmaster/handovers/{date}-{slug}.md` so the next Claude session can resume without re-exploration.

## Why this skill exists

PROGRESS.md is the rolled-up project chronology. A handover is the **per-session full record** — context-injection optimisation for the next AI session. The skill drafts the body from a single template, auto-extracts files of interest, and writes through `backlog_handover_create`, which updates the index and (when `supersedes` is set) edits the prior handover in place. Calling `backlog_handover_create` directly skips auto-extraction and supersession chaining — always go through this skill.

## When to invoke

Three trigger contexts:

1. **Explicit user invocation** — phrases listed in the skill description above.
2. **Auto-offer from end-session** — end-session calls into this skill when its v3-pre-2 heuristics fire (see end-session SKILL.md).
3. **Auto-offer mid-session** — when the orchestrator detects token-watch thresholds (≥200k or ≥270k), it offers this skill: *"You're approaching compaction. Write a handover now?"*

In all three cases the user is asked first. Never write a handover silently.

## Steps

### 1. Resolve session_kind

Pick exactly one value from `references/session-kinds.md`. The default is `continuity`. Override based on cues:

- User said "milestone done", "chunk complete", "ready for next plan", "we changed direction", "pivoting", "new approach" → `milestone`
- User said "context handoff", "near compaction", "300k", "save before compact" → `deep-context`
- Auto-task or auto-epic invoked the skill mid-loop → `auto-stage`
- Otherwise → `continuity`

If unsure between two kinds, ask the user with `AskUserQuestion`.

### 2. Auto-extract draft inputs

Walk the six sources in `references/auto-extraction.md` in order. Output a deduplicated, grouped list of paths under three buckets: **Touched** (sources 1, 2, 3 ∩ written), **Read** (source 3 ∩ read-only), **Relevant** (sources 4, 5 minus the first two).

For each path, write a one-line `what changed` and a one-line `why next session needs it`. **Never skip annotations** — bare paths defeat the optimisation.

### 3. Resolve `task_ids`

- `auto-stage`, `milestone`, `deep-context`: prefer the in-progress task's id; fall back to the task ids from any commits in this session.
- `continuity`: the in-progress task id (or last-touched task id).
- No in-flight task (exploration/memory session): leave empty (`[]`). Do not invent a task id.

### 4. Determine `supersedes`

Set `supersedes = <prior_id>` when **all** of:

- `session_kind == "milestone"`
- The prior latest handover for the same `task_ids` exists
- That prior latest is also `milestone`

Look up the prior with `backlog_handover_list(limit=10)` and pick the newest entry whose `task_ids` overlap. See `references/supersession.md` for the exact algorithm.

### 5. Draft the body from `templates/body.md`

Open `templates/body.md` and fill it. Concrete content only — never leave a `{placeholder}`. If a section has no content (e.g., no pending commits, no dispatch templates), **delete the section** rather than leaving it empty.

If open or resolved decisions exist, pass them as `open_decisions=[...]` / `resolved_this_session=[...]` to `backlog_handover_create`; reference decisions inline with `[[DEC-NNN]]`.

### 6. Generate `tldr` and `next_action`

- `tldr`: one sentence, ≤ 100 chars, past-tense, what shipped.
- `next_action`: one sentence, ≤ 100 chars, imperative, what the next session should do first.

These two fields are the only thing the next session reads by default — they earn their tokens.

### 7. Write directly — no draft-and-approve

Move straight to step 8 and write the handover. Do **not** present a draft and ask "looks good?" — auto-extraction and supersession are deterministic on the inputs, and the user can edit the written file or say "tweak the handover" as a follow-up.

Exceptions where you DO present first:
- The user explicitly asked to review (e.g., "show me the draft first", "let me review before writing").
- `session_kind="milestone"` and the supersession would rewrite a prior milestone — surface the chain change before committing.
- Auto-extraction returned zero files-of-interest and body would be thin — ask the user to confirm scope.

In all other cases: write, then echo the result in step 9.

### 8. Write through `backlog_handover_create`

Call:

```
backlog_handover_create(
    tldr=...,
    next_action=...,
    body=<approved markdown body, no frontmatter>,
    task_ids=[...],
    session_kind="...",
    open_decisions=[...],
    resolved_this_session=[...],
    context_size_at_write="<optional, e.g. ~250k>",
    supersedes="<prior id or empty>",
    branch="<git branch from `git rev-parse --abbrev-ref HEAD`>",
    tip_commit="<git tip from `git rev-parse --short HEAD`>",
)
```

The server writes the file, syncs the index, and (if `supersedes` is set) edits the old handover in place.

If the lesson skill (via end-session's v3-pre-2a sweep) buffered a `pending_review_flag`, forward both `flag_for_review=true` and `review_reason=<buffered reason>` to `backlog_handover_create` unchanged. The flag lands in the new handover's frontmatter so future `session-retro` runs can find it.

### 9. Confirm

Echo back: *"Handover written: `<id>`. Next session can resume from this with `backlog_handover_latest`."*

If the response includes a `WARNING` line about `supersedes` not found, surface that to the user — do not hide it.

## Manual status entry points

The default skill flow is "write a handover." Four additional invocations let the user (or auto routes) move an existing handover through the lifecycle.

### `taskmaster:handover mark-done <id> [reason]`

Resolve `<id>` (accept partial slug — fuzzy-match against `backlog_handover_list`). Call:

```
backlog_handover_update_status(<id>, "done", reason)
```

Echo the result. The `status_user_set: true` lock is set automatically — subsequent supersession or task-complete signals will leave this handover alone.

### `taskmaster:handover mark-in-progress <id> [reason]`

Same shape as `mark-done` with `status="in-progress"`. Use when the user is actively using the handover as working context but wants to keep it out of the auto-resume churn.

### `taskmaster:handover mark-todo <id> [reason]`

Same shape with `status="todo"`. Use to undo an erroneous mark-done.

### `taskmaster:handover triage`

Walk every `todo` handover older than 14 days (default), one at a time. For each:

1. Display `id`, `tldr`, age, `next_action`.
2. Ask the user via `AskUserQuestion`: **mark done** / **supersede with new handover** / **skip / leave as todo** / **stop triage**.
3. On `mark done`: call `backlog_handover_update_status(id, "done", reason="triaged")`.
4. On `supersede`: invoke the existing write flow with `supersedes=<id>` and `session_kind="milestone"` (the `apply_supersession` auto-flip in the write flow will mark the old as done).
5. On `skip`: leave the handover untouched.
6. On `stop`: end the loop and report what was processed.

Pull the candidate set with `backlog_handover_list(status="todo")` then filter by date prefix older than 14 days from today. Cap the loop at 20 entries per invocation; print a "more available" notice if there are leftovers.

## Edge cases

- **Multiple in-progress tasks** — list them, ask the user which is the primary; use that as `task_ids[0]` and append the others.
- **No backlog** — `backlog_handover_create` returns `Error: no backlog found`. Tell the user to run `backlog_init` first.
- **Server sandbox at wrong cwd** — if the MCP tool error mentions a path mismatch, do not retry; tell the user to restart Claude Code from inside the project root and skip the write for this session (offer to print the draft instead so it isn't lost).
- **Auto-stage** — when invoked from `auto-task`, the orchestrator passes `session_kind="auto-stage"` and a stub-only body (frontmatter is the load-bearing part). Skip user review for this path.
- **`--light` override on a heavy session** — trim output to the light template, but still run auto-extraction; we don't want a `--light` flag to mask information the user wanted captured.

## References

- `references/session-kinds.md` — the four kinds, resume-load behavior, archive policy
- `references/auto-extraction.md` — six sources, dedup grouping rules, regex specs
- `references/supersession.md` — chained-supersession algorithm
- `templates/body.md` — body skeleton

## Spec

`docs/superpowers/specs/2026-05-02-handover-skill-design.md`
