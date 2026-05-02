# Handover Session Kinds

Six values, picked exactly once per handover. The kind drives both **what gets loaded on resume** and **how aggressively the file is archived**.

## The six kinds

| Kind | When to pick | Resume-load behaviour | Archive policy |
|---|---|---|---|
| `end-of-day` | Default. Wrap-up at the end of a working day. No special urgency. | Frontmatter only (`tldr`, `next_action`, `task_ids`). | FIFO past cap-30. |
| `context-handoff` | Pre-compaction safety capture (>200k tokens, or user said "near compaction"). | **Body loaded** — next session needs the full state because the current one is about to die. | FIFO past cap-30. |
| `milestone-complete` | A chunk shipped, a next chunk is ready (e.g., m1 → m2, plan3 → plan4). | **Body loaded** — dispatch templates and resume prompts are load-bearing. | Chained: a newer `milestone-complete` for the same `task_ids` supersedes the older one. |
| `pivot` | Direction change mid-flight (detour, plan switch). | **Body loaded** — captures *why* the change. | Chained: a newer `pivot` or `milestone-complete` for the same `task_ids` supersedes. |
| `exploration` | Investigation / infra / memory session, no task in flight (`task_ids: []`). | Frontmatter only. | FIFO past cap-30. |
| `auto-stage` | Written by `auto-task` during the loop (per-stage stub). | Frontmatter only — orchestrator already has state in `auto/state.json`. | Bulk-archived to `_archive/auto/` on epic/phase completion, replaced by an epic-level handover. |

## Choosing the right kind

The flowchart Claude follows:

1. Was the skill invoked by `auto-task`? → `auto-stage` and stop.
2. Did the user say "near compaction" / "300k" / "save before compact"? → `context-handoff` and stop.
3. Did the user say "milestone done" / "chunk complete" / "ready for next plan"? → `milestone-complete` and stop.
4. Did the user say "we changed direction" / "pivot" / "new approach"? → `pivot` and stop.
5. Was a task in flight this session (touched files, made commits)? → `end-of-day`.
6. No task in flight, no commits, just exploration / setup / memory work? → `exploration`.

If the user's words match more than one cue, ask via `AskUserQuestion` with the matching pair as options.

## Why this matters for the next session

Start-session and pick-task look at `session_kind` to decide whether to load only the frontmatter (cheap, ~200 tokens) or the full body (~2000 tokens). Picking the wrong kind costs tokens — `end-of-day` on a context-handoff hides critical context; `context-handoff` on a small wrap-up wastes load budget every session start.
