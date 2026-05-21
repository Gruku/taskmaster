# start-session — Deep Ceremony (`--deep` mode)

Invoked when the user says `start-session --deep`, "full briefing", "give me everything", or equivalent explicit depth signal.

Run all steps below in addition to the standard glance steps (backlog_status + open handovers). This reproduces today's full-load behavior.

## Deep steps (run in order after glance steps 1–3)

### D1. Recap diff

Call `backlog_recap` to see what changed in the project since the last snapshot — tasks added, status transitions, issues fixed, phase advances.

Surface as a `**Since last snapshot:**` block. Compact format — do not expand into prose. If nothing changed, skip the block.

### D2. Lesson digest

Call `backlog_lesson_digest` to load the slim digest of active project lessons (id + kind + title, ≤30 entries).

These are project-specific behavioral rules Claude should keep in mind during the session. Loading the digest at session start is *priming* — it does not mean lessons have been applied. Do not call `backlog_lesson_reinforce` on session start.

### D3. Core lessons (full body)

Look at the digest output. For any lesson in the `core` tier (denoted `[core/...]`), fetch it in full via `backlog_lesson_get <id>`. Keep the body in working context for the whole session. Cap: 5 core lessons.

### D4. Full issue list

Call `backlog_issue_list(status="open")` (no limit cap). Surface all open issues grouped P0 → P3. P0/P1 entries get a visual flag.

### D5. Last session entry

Call `backlog_last_session` to get the most recent PROGRESS.md entry. Surface as `**Last session:**` block for continuity.

### D6. Open decisions

Call `backlog_decision_list(status="open")`. Report the count and titles.

> "3 open decisions: DEC-001 (Land ue-plugin-086), DEC-003 (Voice billing semantics), DEC-005 (…)"

If a decision blocks the user's top-of-mind task, mention it inline in the "where you left off" recap.

### D7. Untracked work

After showing the dashboard, check for commits since the last session that aren't associated with any tracked task branch:

1. Get the last session date from `backlog_last_session` output (the `### YYYY-MM-DD` heading).
2. Run `git log --oneline --since="{last_session_date}" --no-merges` on the main branch.
3. Get the list of tracked task branches from in-progress tasks (their `branch` field).
4. Any commits on the main branch that aren't in a task branch are "untracked work".
5. If found, show informatively (not judgmentally):
   ```
   Since last session: N commits outside tracked tasks
     - fix typo in README
     - bump dependencies
   ```
6. If none found, skip this section silently.

## Deep-mode briefing structure

Present in this order after the glance briefing:

1. `**Since last snapshot:**` (recap diff, D1)
2. `**Lesson digest (${N} active):**` (D2 list, compact)
3. `**Core lessons loaded:**` (D3 — title only in the briefing, bodies in context)
4. `**All open issues:**` (D4)
5. `**Last session:**` (D5 — PROGRESS.md entry)
6. `**Open decisions:**` (D6 — count + titles)
7. Untracked work notice if any (D7)

## Spec-review hints (deep only)

For each suggested critical/high task that has a spec/plan but no `spec_review` record, append:
> "↳ run `taskmaster:spec-review {task_id}` before picking — design hasn't been vetted."

## Notes

- Deep mode total token budget: ~3,000–4,000 tokens (glance ~800 + deep additions ~2,200).
- If lesson digest + core bodies push past 5,000 tokens, prune lowest `reinforce_count` lessons first.
- `--deep` is user-explicit. Never auto-trigger deep mode based on signals (days since last session, etc.).
- `backlog_handover_latest` is deprecated (kept as a backward-compat alias only). Use `backlog_handover_list(status="open", limit=5)` — already wired into the glance path.
- Lesson digest does NOT mean apply lessons — loading the digest is *priming* only. Reinforce on confirmed application during work, not on session start.
