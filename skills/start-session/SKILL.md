---
name: start-session
description: "Start a work session and orient for a new conversation. Invoke when the user says 'let's get started', 'what should I work on', 'show me the backlog', 'orient me', or begins a new conversation in a project that has backlog.yaml. Shows dashboard, last session summary, and suggests next tasks."
---

# Start Session

Load project context and orient for a new work session.

The user is arriving at the start of a conversation — they've lost context since last time and want to feel grounded quickly. Your job is to deliver a concise briefing, not a data dump.

## Steps

1. **Call `backlog_status` tool** to get the current dashboard. The first line of output is `**Schema:** v<N>` — note it. Steps 2a–2d below activate only when `Schema: v3` (or higher). On v2 backlogs, skip them and behave as before. The schema line reflects the *effective* version: a backlog with v3 entity content (handovers/lessons/issues) reports v3 even if the `schema_version` marker was never written, so auto-offers fire on legacy projects too.

2. **Call `backlog_last_session` tool** to get the most recent changelog entry for continuity.

### v3 additions (skip on v2 backlogs)

2a. **Recap diff.** Call `backlog_recap` to see what changed in the project since last snapshot — tasks added, status transitions, fixed issues, phase advances. This is *project-state delta*, distinct from "what I did last session." Both go in the briefing as separate sections.

2b. **Lesson digest.** Call `backlog_lesson_digest` to load the slim digest of active project lessons (id + kind + title only, ≤30 entries). These are project-specific behavioral guidance Claude should keep in mind during this session.

2c. **Core lessons.** Look at the digest output. For any lesson in the `core` tier (denoted by `[core/...]`), fetch it in full via `backlog_lesson_get <id>` and keep its body in working context for the whole session. Cap: 5 core lessons.

2d. **Latest handover.** Call `backlog_handover_latest` to get the previous session's handover frontmatter (tldr + next_action + linked task ids). If it exists, surface it prominently in the briefing — it's the "where I left off" anchor. If the frontmatter shows `session_kind: context-handoff`, this was a deliberate handoff written near compaction; offer to fetch the full body via `backlog_handover_get <id>` if the user wants the long version.

2d-bis. **Stale-todo handover counter.** Call `backlog_handover_list(status="todo", limit=30)`. If the count is **≥ 2**, surface a single line below the latest-handover line in the briefing:

> **N todo handovers** — oldest from `<YYYY-MM-DD from earliest id>`. Run `taskmaster:handover triage` to clear.

If the count is 0 or 1, skip silently — the latest-handover line already covers the single-todo case. The threshold avoids noise when only the active handover is `todo`.

The list returns slim entries (no bodies); the date prefix on the oldest id is the user-visible "how stale" anchor.

### Surface open decisions

After loading the latest handover, also call:

```
backlog_decision_list(status="open")
```

Report the count to the user as part of the dashboard summary:

> "3 open decisions: DEC-001 (Land ue-plugin-086), DEC-003 (Voice billing semantics), DEC-005 (…)"

If a decision blocks the user's top-of-mind task, mention it inline in the "where you left off" recap. The continuity dashboard surfaces them visually; this is the chat-side echo.

2e. **Open issues by severity.** Call `backlog_issue_list(status="open", limit=10)`. The list is already pre-sorted P0 → P3 by the index sync. Keep all returned entries in working context — they're the "what's broken right now" anchor. P0/P1 entries get a visual flag in the briefing (step 3). If the list is empty ("No issues yet."), skip silently and don't render the section.

3. **Present a structured briefing to the user:**

   Lead with the most actionable information first:

   - **(v3) Latest handover** — if step 2d returned a handover, lead with it: "**Where you left off:** {tldr}. Next: {next_action}." This is the most concrete starting anchor and should come first on v3 backlogs.
   - **If there are in-progress items:** "**Resuming:** You left these in progress:" — these are the most important because work is already started.
   - **If there are in-review items:** "**Needs your testing:** These are implemented but waiting for you to confirm they work:" — in-review tasks are equally important as in-progress. They represent finished work the user hasn't verified yet. Don't let them be forgotten between sessions.
   - **Last session summary** — what was accomplished last time, for continuity.
   - **(v3) Recap diff** — if step 2a returned changes, show them as a "**Since last snapshot:**" block. This is project-state delta (tasks added by you or others, status moves, issues fixed, phase advances). Compact format — do not expand it into prose.
   - **(v3) Open issues** — if step 2e returned any open issues, render them as "**Open issues (top by severity):**" with id + severity + title, in the order returned (P0 first). P0/P1 entries should be visually flagged. Cap visible at 10; mention `backlog_issue_list status=open` for the full list if there are more.
   - **Phase progress** — if an active phase exists, show it prominently: "**Phase: {name}** — {done}/{total} tasks done". This gives the user a sense of where they are in the project's arc.
   - **Stale tasks** — if the `backlog_status` output includes stale tasks (tasks not referenced in 14+ days), show them:
     ```
     Stale tasks (not referenced in 14+ days):
       auth-007  Add SAML support        — stale 21d
       api-012   GraphQL migration        — stale 18d
     Still relevant? Say "archive auth-007" or "keep it".
     ```
   - **Dashboard** — epic progress, stats.
   - **If there are next-up items:** "**Suggested next:** {first item} ({priority})" — these are filtered to the active phase when one exists, so the user only sees what's relevant right now.
     - For each suggested critical/high task that has a spec/plan but **no `spec_review` record**, append a hint: "↳ run `taskmaster:spec-review {task_id}` before picking — design hasn't been vetted." This makes spec-review feel like a natural pre-step rather than a hidden gate.
   - **Untracked work** — After showing the dashboard, check for commits since the last session that aren't associated with any tracked task branch:
     1. Get the last session date from `backlog_last_session` output (the `### YYYY-MM-DD` heading)
     2. Run `git log --oneline --since="{last_session_date}" --no-merges` on the main branch
     3. Get the list of tracked task branches from in-progress tasks (their `branch` field)
     4. Any commits on the main branch that aren't in a task branch are "untracked work"
     5. If found, show informatively (not judgmentally):
        ```
        Since last session: N commits outside tracked tasks
          - fix typo in README
          - bump dependencies
        ```
     6. If none found, skip this section silently

4. **Prompt:** "What would you like to work on? Pick a task with `/pick-task` or tell me to add new work."

## Empty State

If there are no epics and no tasks (fresh project):
- Say: "The backlog is empty — let's set it up! What are the main workstreams for this project?"
- Guide them to create their first epic with `backlog_add_epic`, then add tasks.
- Don't show an empty dashboard table — it's confusing.

## Error Handling

If the `backlog_status` tool fails (MCP server not running, backlog.yaml missing):
- Check if `backlog.yaml` exists. If not, suggest running `/init` first.
- If it exists but the tool fails, the MCP server may not be registered. Guide the user to check their `.mcp.json`.

## Notes

- This skill is read-only — it does not modify any files.
- The `backlog_status` tool handles all YAML parsing and stat computation.
- The `backlog_last_session` tool extracts the last changelog entry from PROGRESS.md.

### v3 token budget

When v3 features activate (steps 2a–2e), the briefing carries:
- Latest handover frontmatter: ~250 tokens
- Recap diff: ~250 tokens (often less)
- Lesson digest: ~300 tokens (≤30 × 10)
- Core lessons in full: ~500 tokens (≤5 × 100)
- Top issues: ~200 tokens (≤10 × 20)

Plus the dashboard + last-session log (~1,000 tokens combined). Soft target for the whole start-session injection: ~3,000 tokens. If the project regularly blows past 5,000, trim digest count or core cap in `.taskmaster/config.yaml` (future feature). For now, surface a brief warning to the user: "Session-start context is large — consider retiring stale lessons via `backlog_lesson_list tier=active` and reviewing low-reinforce entries."

### Lesson digest does NOT mean apply lessons

Loading the digest at session start is *priming* — keep these in mind. The actual *application* of a lesson happens during work (most often surfaced via `pick-task` trigger matching). When you successfully apply a lesson, call `backlog_lesson_reinforce <id>` to bump its count. Don't reinforce on session start (that would inflate counts without actual application).

## Mid-session behavior

While working in a v3 project, watch for two kinds of moments and respond inline — no separate tool call required for the candidate paths.

### Lesson-candidate emission

Emit a `<lesson-candidate>` XML tag inline in your reply (not a tool call) when ANY of these fire:

- **Repeated correction** — the user has corrected you on the same thing twice or more in this session, OR you notice you've corrected yourself on the same pattern multiple times.
- **Bug second-encounter** — you (or the user) hit a bug, gotcha, or surprising behavior that you've already encountered before in this codebase.
- **Architectural ground rule** — the user states a non-obvious "we always do X here" / "we never do Y here" / "in this codebase we…" rule.

Schema: see `plugins/taskmaster/skills/lesson/references/marker-format.md` for the `<lesson-candidate>` tag.

End-session sweeps these tags and either offers them for triage (default behavior) or commits them per the user's preferred flow.

### Idea-candidate emission and idea auto-log

When you detect an *idea* in the user's message — a thought, parking-lot item, future-work observation, or "we could try X" — pick ONE of three paths:

**Path A — Skip entirely.** When:
- User is thinking out loud about the immediate task (not a separable idea).
- Idea is already covered by an existing task or idea (when in doubt, call `backlog_idea_list(limit=20)` first).
- User explicitly said not to capture.

**Path B — Emit `<idea-candidate>` tag (fuzzy capture).** When:
- Hedged / ambient framing: "hmm could be cool…", "maybe at some point", "we could think about", "interesting thought".
- Tangential to the current task — user is mid-flow on something else.
- Concept is fuzzy or speculative.

Don't make a tool call. Inline-emit:
```xml
<idea-candidate title="Auto-tag from git diff" tags="automation,viewer">
User mentioned in passing that linking ideas to recent files would be useful — flagging for end-session.
</idea-candidate>
```

End-session will commit these with `status="candidate"` for later triage in the viewer.

**Path C — Auto-log via `backlog_idea_create` (sharp capture).** When ANY fire:
- Explicit framing: "idea:", "future work:", "for later:", "I want to try", "we should explore", "let's eventually".
- Direct request: "remember this idea", "save this idea", "that's a good idea, log it".
- Concrete-and-named: a specific feature/change with a clear noun and verb that's separable from the current task.

Call `backlog_idea_create(title=..., body=..., created_by="Claude")` and announce inline (single line, no summary):

> _Logged as IDEA-NNN — "<title>"_

Schema for the `<idea-candidate>` tag: see `plugins/taskmaster/skills/lesson/references/marker-format.md`.

### One-line decision tree

```
User said something that might be an idea/lesson?
├─ Lesson trigger fires (correction / bug-twice / ground rule)? → emit <lesson-candidate>
├─ Sharp idea? (explicit framing OR direct request OR concrete-and-named) → call backlog_idea_create + announce
├─ Fuzzy/ambient idea? → emit <idea-candidate>
└─ Otherwise → skip
```
