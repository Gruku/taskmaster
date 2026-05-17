---
name: lesson
description: "Write or reinforce a project lesson. Invoke when the user says 'remember this', 'save as a lesson', 'learn this lesson', 'memorize this', 'this keeps happening', 'we always do x here', 'we got burned by this last time', 'promote candidate to lesson', 'review lesson candidates', or 'flag this session for retro'. Do not call backlog_lesson_create or backlog_lesson_reinforce directly."
---

# Lesson

Project-scoped, structured knowledge that compounds across sessions. Where auto-memory captures *user* preferences globally, lessons capture *project* truths locally.

## Why this skill exists

The backend (`backlog_lesson_create`, `_reinforce`, `_update`, `_match`, `_digest`) is the storage layer; this skill is the **authoring + lifecycle** layer. Calling `backlog_lesson_create` directly skips auto-extraction, the user-review gate, and the candidate buffer that ties the write to mid-session signals. Always go through this skill.

## When to invoke

Five entry points:

1. **`write-from-context`** — explicit user invocation (`save as lesson`, `remember this`, ...).
2. **`write-from-candidate`** — end-session sweep "Promote" action against a deferred or in-context candidate.
3. **`reinforce-immediate`** — Claude cited an `L-NNN` in its own response.
4. **`reinforce-sweep`** — end-session "which lessons applied this session?" sub-step.
5. **`session-retro`** — user asks "scan handover X for retro lessons" against a `flag_for_review:true` handover.

In all five cases the user reviews and approves before any file is written.

## Decision tree

| Trigger signal | Entry | What runs |
|---|---|---|
| User said "save as lesson" / "remember this" | `write-from-context` | Write subflow with intake = current session context |
| End-session sweep promoted a candidate | `write-from-candidate` | Write subflow with intake = candidate body + session context |
| Claude just wrote "Per L-NNN, ..." | `reinforce-immediate` | Call `backlog_lesson_reinforce` then stop |
| End-session sub-step "which lessons applied?" | `reinforce-sweep` | Multi-select reinforce loop — see `references/reinforce-flows.md` |
| User said "scan handover X for retro lessons" | `session-retro` | Batch propose + write subflow per accepted candidate — see `references/session-retro.md` |

## Write subflow (write-from-context and write-from-candidate)

1. **Determine intake** from the entry point (session context or candidate body).
2. **Auto-extract all fields** — walk `references/auto-extraction.md` for per-field source rules (title, kind, triggers.files, triggers.task_titles_match, body ## Why / ## What to do / ## Examples, related_tasks).
3. **Present the full draft for review.** Ask: "Looks good? I can change kind, edit triggers, rewrite Why / What to do / Examples, or drop sections." Iterate until approved. Do NOT write before approval.
4. **On approve**, call `backlog_lesson_create(title, kind, body, files, task_titles_match, task_kinds, related_tasks, related_issues, tier="active")`. Echo: "Lesson written: `L-NNN`."
5. **If candidate had `scope="session"`**: buffer `pending_review_flag` for end-session handover write. If candidate came from `_candidates.md`, call `backlog_lesson_candidate_drop(index=<idx>)`.

## Mid-session: the `<lesson-candidate>` XML marker

While a session is running, emit an inline tag (no tool call) when: user correction repeats; bug second-encounter; architectural ground rule emerges.

```xml
<lesson-candidate kind="gotcha" topic="multi-tab fanout" scope="point">
useEffect reading useLocation().state without active-tab guard.
</lesson-candidate>
```

Format details and grep convention: `references/marker-format.md`. Silence is the default — only emit when one of those heuristics fires.

## Key rules

- Never call `backlog_lesson_create` or `backlog_lesson_reinforce` directly without going through this skill.
- User always reviews before any file is written (except reinforce which is non-destructive).

## References

- `references/marker-format.md` — XML schema, attrs, emit heuristics, compaction defenses
- `references/auto-extraction.md` — per-field extraction sources and fallbacks
- `references/reinforce-flows.md` — immediate + sweep + user-initiated reinforcement
- `references/promotion-decay.md` — core-tier promotion thresholds, decay UX, core cap handling
- `references/session-retro.md` — scope="session" flow + flagged-handover analysis
- `templates/lesson-body.md` — Why / What to do / Examples skeleton
