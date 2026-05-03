# Lesson Reinforce Flows

Three reinforcement modes; same backend (`backlog_lesson_reinforce`), different orchestration.

## 1. Reinforce-immediate

When Claude proactively cites a lesson in a response (e.g. *"Per L-007, you should read auth/session.ts before editing the login flow"*), it calls `backlog_lesson_reinforce` immediately after the citation:

```
backlog_lesson_reinforce(lesson_id="L-007")
```

The MCP tool's return includes the eligibility hint when `reinforce_count` crosses the promotion threshold:

```
Reinforced L-007 → x5
→ Eligible for promotion to core tier (auto-load at session start). Use `backlog_lesson_update L-007 tier=core` to promote.
```

Surface the hint inline. Do **not** auto-promote — wait for the end-session sweep (or an explicit user request) to trigger the user-confirmed promotion.

User push-back later (e.g. "actually, that wasn't relevant") does **not** decrement `reinforce_count` automatically. Over-counting is acceptable; the user can `backlog_lesson_update` to correct if they care.

## 2. Reinforce-sweep (end-session)

Run during end-session's v3-pre-2a step:

1. List lessons that were trigger-loaded at start-session (use the `backlog_lesson_list(tier="active")` output and intersect with whatever start-session injected) OR cited mid-session by Claude.

2. Present a multi-select to the user:

   ```
   AskUserQuestion({
     questions: [{
       question: "Which lessons actually applied this session?",
       header: "Reinforce",
       multiSelect: true,
       options: [
         { label: "L-007 [gotcha] Always read auth/session.ts before editing auth flow", description: "trigger-loaded; appeared in this session" },
         { label: "L-014 [pattern] Use AskUserQuestion for ambiguous intents", description: "cited at turn 23" },
         { label: "Skip — none of these applied", description: "" }
       ]
     }]
   })
   ```

3. For each pick, call:

   ```
   backlog_lesson_reinforce(lesson_id="L-NNN")
   ```

4. After all reinforcements, if any return surfaces "Eligible for promotion to core tier", ask:

   > "L-NNN is eligible for core tier (auto-loaded every session). Promote?"

   On confirm: `backlog_lesson_update(lesson_id="L-NNN", tier="core")`. See `promotion-decay.md` for the core-cap (5/5) handling.

## 3. Reinforce — user-initiated

The user can invoke reinforce-immediate explicitly any time:

> "Reinforce L-014 — that just bit me again."

This is a single-step path: call `backlog_lesson_reinforce(lesson_id="L-014")` and surface the response. No sweep, no multi-select. Same eligibility-hint surfacing as path 1.

## What reinforcement actually does

`backlog_lesson_reinforce` (backend: `reinforce_lesson` in `taskmaster_v3.py`) increments `reinforce_count` by 1, sets `last_reinforced` to today's ISO date, and rewrites the lesson file. Side effect: the lesson moves up in the digest sort order (`backlog_lesson_digest` sorts by reinforce_count desc).

Eligibility for core promotion: `tier == "active"` AND `kind in {gotcha, anti-pattern}` AND `reinforce_count >= 5`. Patterns never auto-promote — they're the cheap, common case where applying the rule shouldn't push it to core.
