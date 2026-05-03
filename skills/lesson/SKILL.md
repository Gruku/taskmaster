---
name: lesson
description: "Write/reinforce/promote a project-scoped lesson. Invoke when the user says 'remember this', 'save as a lesson', 'learn this lesson', 'memorize this', 'this keeps happening', 'we always do X here', 'we got burned by this last time', 'promote candidate to lesson', 'review lesson candidates', or 'flag this session for retro'. Auto-offered by end-session when <lesson-candidate> tags or deferred candidates exist. Mid-session, emits <lesson-candidate> XML tags inline (no tool call) to flag knowledge to capture later. This is the only correct way to write or reinforce a project lesson — do not call backlog_lesson_create or backlog_lesson_reinforce directly."
---

# Lesson

Project-scoped, structured knowledge that compounds across sessions. Where auto-memory captures *user* preferences globally, lessons capture *project* truths locally. The longer you use a project, the better this system becomes at it.

## Why this skill exists

The backend (`backlog_lesson_create`, `_reinforce`, `_update`, `_match`, `_digest`) is the storage layer; this skill is the **authoring + lifecycle** layer. Calling `backlog_lesson_create` directly skips auto-extraction (kind / triggers / why / what-to-do / examples), the user-review gate, and the candidate buffer that ties the write to mid-session signals. Always go through this skill.

## When to invoke

Five entry points, listed in `references/reinforce-flows.md` and dispatched in step 1 below:

1. **`write-from-context`** — explicit user invocation (`save as lesson`, `remember this`, …).
2. **`write-from-candidate`** — end-session sweep "Promote" action against a deferred or in-context candidate.
3. **`reinforce-immediate`** — Claude cited an `L-NNN` in its own response.
4. **`reinforce-sweep`** — end-session "which lessons applied this session?" sub-step.
5. **`session-retro`** — user later asks "scan handover X for retro lessons" against a `flag_for_review:true` handover.

In all five cases the user reviews and approves before any file is written.

## Mid-session: the `<lesson-candidate>` XML marker

While a session is running, this skill is **not** invoked for every candidate moment. Instead, Claude emits an inline tag (no tool call):

```xml
<lesson-candidate kind="gotcha" topic="multi-tab fanout" scope="point">
useEffect reading useLocation().state without active-tab guard.
Recurred on cb6927c0; fix is chatId === activeTabId early-return.
</lesson-candidate>
```

Heuristic for emit (any one is enough): user correction repeats; bug second-encounter (same root cause shape); architectural ground rule emerges ("we always X here", "never Y in this codebase"). Silence is the default — do not flag unless one of those fires.

Format details and grep convention live in `references/marker-format.md`. Do not abbreviate the opening anchor — it must always be the literal `<lesson-candidate ` (with one trailing space) so a single regex can recover it from disk transcripts.

## Steps

### 1. Identify the entry point

Pick exactly one — they each pass a different `intake` into the shared write subflow (step 3 onward) or skip the subflow entirely.

| Trigger signal | Entry | What runs |
|---|---|---|
| User said "save as lesson" / "remember this" / "we always do X here" | `write-from-context` | Write subflow with intake = current session context |
| End-session sweep promoted a candidate | `write-from-candidate` | Write subflow with intake = candidate body + session context |
| Claude just wrote "Per L-NNN, ..." | `reinforce-immediate` | Step 6 only (immediate reinforce) |
| End-session sub-step "which lessons applied?" | `reinforce-sweep` | Step 7 only (sweep reinforce) |
| User said "scan handover X for retro lessons" | `session-retro` | Step 8 (batch propose, then write subflow per accepted candidate) |

### 2. (Reinforce-immediate path) Call `backlog_lesson_reinforce`

Only for `reinforce-immediate`:

```
backlog_lesson_reinforce(lesson_id="L-NNN")
```

If the response includes "Eligible for promotion to core tier", surface it inline to the user — do not auto-promote. Stop here.

### 3. Write subflow — determine intake

Pull the intake from the entry point:

- `write-from-context`: candidate-shaped record built from current session prose (Claude's own context).
- `write-from-candidate`: deferred-file entry (read from `backlog_lesson_candidates_list`) or in-context `<lesson-candidate>` body.
- `session-retro`: per-candidate intake produced by step 8.

### 4. Auto-extract every field

Walk `references/auto-extraction.md` for the per-field source rules:

- **`title`** — first line of the candidate body or user request, ≤80 chars, imperative tense.
- **`kind`** — candidate `kind` attr if set, else inferred (corrections → `anti-pattern`; "we always" / "always do" → `pattern`; "watch out" / "got burned" → `gotcha`).
- **`triggers.files`** — `git diff --name-only HEAD~5` collapsed to globs (e.g. `src/auth/login.ts` + `src/auth/session.ts` → `src/auth/**`).
- **`triggers.task_titles_match`** — keyword extraction (3–5 nouns/verbs) from current and recent task titles.
- **`triggers.task_kinds`** — currently-in-progress task's `kind` field if set; else `[]`.
- **Body `## Why`** — drafted from candidate body + bug/correction context.
- **Body `## What to do`** — numbered steps drafted from the resolution path.
- **Body `## Examples`** — task ids of session-touched tasks + commit SHAs from `git log --oneline HEAD~5`.
- **`related_tasks`** — task_ids in flight this session (from current `backlog_status`).

If the invocation included a focus hint (e.g. "focus on multi-tab"), weight extraction toward that topic.

### 5. Present the full draft for review

Show the user the assembled draft as one document with section labels (frontmatter preview + body). Then ask:

> "Looks good? I can change kind, edit triggers, rewrite Why / What to do / Examples, or drop sections."

Iterate until the user approves. Do **not** write the file before approval.

### 6. On approve, write through `backlog_lesson_create`

Call:

```
backlog_lesson_create(
    title=...,
    kind="gotcha"|"anti-pattern"|"pattern",
    body=<approved markdown body, with ## Why / ## What to do / ## Examples>,
    files=[...],
    task_titles_match=[...],
    task_kinds=[...],
    related_tasks=[...],
    related_issues=[...],
    tier="active",
)
```

Echo back the new id: *"Lesson written: `L-NNN`. It will trigger-load on tasks matching its triggers."*

### 6a. If the candidate had `scope="session"`

Do **NOT** modify any existing handover. Instead, **buffer** the flag for the next handover write this session:

- Set an internal note `pending_review_flag = {reason: <topic or first line of candidate body>}`.
- When end-session invokes `taskmaster:handover` (its v3-pre-2 step), pass `flag_for_review=true` + `review_reason=<reason>` to `backlog_handover_create`.
- If end-session is skipped (user wraps without writing a handover), the flag is **dropped silently**. `scope="session"` semantics require an associated handover artifact to flag — if there is no handover, there is nothing to flag.

If the candidate came from `_candidates.md`, also call `backlog_lesson_candidate_drop(index=<idx>)` to remove it from the deferred list.

### 7. Reinforce-sweep path (end-session sub-step)

Only for `reinforce-sweep`:

1. List lessons that were trigger-loaded at start-session OR cited mid-session by Claude. Use `backlog_lesson_list(tier="active")` then filter to ones that appear in your context.
2. Multi-select: which actually applied?
3. For each picked id:

   ```
   backlog_lesson_reinforce(lesson_id="L-NNN")
   ```

4. After all reinforcements, if any return surfaces "Eligible for promotion to core tier", ask the user:

   > "L-NNN is eligible for core tier (auto-loaded every session). Promote?"

   On confirm:

   ```
   backlog_lesson_update(lesson_id="L-NNN", tier="core")
   ```

   See `references/promotion-decay.md` for the core-cap (≤5 entries) handling.

### 8. Session-retro path

Only for `session-retro`. The user invokes with a handover id; that handover should have `flag_for_review: true` in its frontmatter (set when a `scope="session"` candidate was promoted in a prior session).

1. Read the handover via `backlog_handover_get(handover_id=...)`. Note its `task_ids` and `review_reason`.
2. Walk session signals:
   - `git log --oneline {handover.tip_commit}~..{handover.tip_commit}` for commits.
   - `backlog_get_task` for each id in `task_ids` — read their `notes` and recent updates.
   - If a transcript jsonl is reachable, run `backlog_lesson_candidates_scan(days=30)` and filter to that session window.
3. Propose a batch of candidates (typically 2–5). Each one has the same shape as a `<lesson-candidate>`.
4. For each candidate the user accepts: run the write subflow (steps 3–6) with that candidate's body as intake.

`references/session-retro.md` walks the full algorithm.

## Edge cases

- **No backlog** — `backlog_lesson_create` returns `Error: no backlog found ...`. Tell the user to run `backlog_init` first.
- **Compaction recovery** — if mid-session compaction wiped earlier `<lesson-candidate>` tags, offer `backlog_lesson_candidates_scan(days=7)` to recover them from the on-disk transcript. This is the only recovery path until the PreCompact hook (v3-skills-006) ships.
- **Server sandbox at wrong cwd** — if `backlog_lesson_candidates_scan` returns "No transcripts directory" but you know the project has sessions, the MCP server is rooted at the wrong cwd. Tell the user to restart Claude Code from the project root and skip the recovery scan; the in-context tags Claude can still see are unaffected.
- **Auto-suggestion source vs. candidate scan** — auto-memory `feedback/*.md` clusters with 2+ entries this project are *promotion suggestions*, not pre-flagged `<lesson-candidate>` tags. End-session merges both inputs (see end-session SKILL.md v3-pre-2a) but they have different shapes — handle them via the regular write subflow (intake = the cluster's representative entry).
- **Duplicate detection** — when the proposed `title` looks like an existing lesson's title, ask the user "this looks like L-NNN — reinforce that instead?" and route to `reinforce-immediate` if they confirm. Out of scope to do similarity scoring automatically; the user's eye is the check.

## References

- `references/marker-format.md` — XML schema, attrs, emit heuristics, compaction defenses
- `references/auto-extraction.md` — per-field extraction sources and fallbacks
- `references/reinforce-flows.md` — immediate + sweep + user-initiated reinforcement
- `references/promotion-decay.md` — core-tier promotion thresholds, decay UX, core cap handling
- `references/session-retro.md` — `scope="session"` flow + flagged-handover analysis
- `templates/lesson-body.md` — Why / What to do / Examples skeleton

## Spec

`docs/superpowers/specs/2026-05-03-lesson-skill-design.md`
