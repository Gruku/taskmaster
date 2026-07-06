# Lesson Promotion + Decay

Lessons compound through the `active → core` promotion (high-value, always loaded) and decay through `active → retired` (silent auto-retire when stale). Both flows are server-side primitives; this doc explains the surface UX.

## Promotion to core

**Eligibility (server-side, in `lesson_eligible_for_promotion`):**

```python
fm["tier"] == "active"
and fm["kind"] in ("gotcha", "anti-pattern")
and fm["reinforce_count"] >= 5    # LESSON_PROMOTE_REINFORCE
```

Patterns never auto-promote — they're cheap to re-derive. Only gotchas and anti-patterns earn the always-loaded slot.

**Surface:** never silent. Two surfaces:

1. **Inline reinforce response** — `backlog_lesson_reinforce` already includes "Eligible for promotion to core tier" in its return when threshold crosses. The skill surfaces this inline; it does **not** auto-promote.
2. **End-session sweep step** — after batch-reinforcing, the skill iterates eligible lessons and prompts:

   > "L-007 is eligible for core tier (auto-loaded every session). Promote?"

User-confirmed always. On yes:

```
backlog_lesson_update(lesson_id="L-007", tier="core")
```

## Core cap (5/5)

Hard cap: ≤5 lessons in `core` tier (constant `LESSON_CORE_CAP`). When promotion would exceed the cap, offer a swap:

> "Core tier is full (5/5). The lowest-count core lesson is L-002 (count 3). Demote L-002 back to active to make room?"

User-confirmed. On yes: `backlog_lesson_update(lesson_id="L-002", tier="active")` then `backlog_lesson_update(lesson_id="L-007", tier="core")`. On no: leave L-007 active and **suppress the eligibility prompt for the rest of the session** (don't pester the user every reinforcement).

## Decay (auto-retire)

**Eligibility (server-side, in `lesson_eligible_for_decay`):**

```python
fm["tier"] == "active"
and (today - last_reinforced).days >= 180   # LESSON_DECAY_DAYS
and fm["reinforce_count"] < 2               # LESSON_DECAY_REINFORCE
```

Lessons never reinforced fall back to `created` date as the proxy.

**Surface:** **silent**. The decay sweep happens server-side without user interaction. End-session may emit a single info line if any lessons retired this session:

> *"Retired N stale lessons (review with `backlog_lesson_list --tier retired`)."*

No prompt, no action — signal only. The user can list retired lessons and `backlog_lesson_update(lesson_id, tier="active")` to revive any false-positive retirements.

## Manual demotion

Either tier flip is just `backlog_lesson_update(lesson_id, tier=...)`. Use `backlog_lesson_update(lesson_id, tier="retired")` to permanently retire a lesson the user no longer wants surfaced; the file stays on disk for history.

## Where this surfaces

- Start-session: loads all `core` tier lessons in full + the digest of active tier (capped at 30 per `LESSON_DIGEST_CAP`).
- Pick-task: `match_lessons_for_task` injects up to 3 (`LESSON_TRIGGER_MATCH_CAP`) trigger-matched active+core lessons. Retired never matches.
- End-session: this skill's sweep step is where reinforcement and promotion prompts surface.
