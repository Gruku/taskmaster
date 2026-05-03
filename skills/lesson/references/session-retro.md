# Session-Retro Flow (`scope="session"`)

A `<lesson-candidate scope="session">` tag does **not** propose a single lesson immediately. Instead, it flags the *whole session* for later retro-extraction. This handles the "this whole session was a learning experience and I'll come back to it" case without forcing the user to summarize 100+ turns into one body at end-of-session.

## End-session: stamping the flag

When end-session's sweep encounters a `scope="session"` candidate (either in-context or in `_candidates.md`):

1. **Do NOT** modify any existing handover.
2. Buffer a `pending_review_flag` in the lesson skill's working memory:
   ```
   pending_review_flag = {
     reason: <topic attr OR first line of candidate body>,
   }
   ```
3. When end-session invokes `taskmaster:handover` (its v3-pre-2 step), the lesson skill passes the buffered flag through:
   ```
   backlog_handover_create(
       tldr=...,
       next_action=...,
       body=...,
       session_kind="end-of-day",  # or whatever the user picked
       flag_for_review=True,
       review_reason="<topic or summary>",
   )
   ```
4. The new handover lands with `flag_for_review: true` + `review_reason: <text>` in its frontmatter.
5. If end-session is skipped (user wraps without writing a handover), the flag is **dropped silently**. There is no orphan handover to attach a flag to.

If the user writes the handover *first* and then promotes a `scope="session"` candidate later in the same session, the candidate's promotion calls `apply_handover_review_flag(handover_id=<just-written-id>, review_reason=...)` directly — same effect, different ordering.

## Session-retro entry point — invocation

Days or weeks later, the user invokes:

> "scan handover 2026-05-03-autonomous-3hour for retro lessons"

The `taskmaster:lesson` skill's `session-retro` entry point fires.

## Session-retro algorithm

1. **Load the handover.** Call `backlog_handover_get(handover_id=...)`. Read its `task_ids`, `tip_commit`, `branch`, `review_reason`.

2. **Mine commits in the session window.** Run `git log --oneline {tip_commit}~10..{tip_commit}` (or `{tip_commit}~{n}..{tip_commit}` heuristically based on commit timestamps; if the previous milestone-complete handover exists, use its `tip_commit` as the lower bound). Capture commit SHAs and messages.

3. **Mine task notes.** For each id in `handover.task_ids`, call `backlog_get_task` and read `notes`. Recent notes added during the flagged session are likely lesson-shaped.

4. **Mine the transcript (if available).** Call `backlog_lesson_candidates_scan(days=<window>, kind="")` where `<window>` is the number of days since the handover's `date`. Filter results to entries whose `source_file` mtime is within ±1 day of the handover's date.

5. **Synthesize candidate proposals.** Cluster the signals (commits + notes + scanned tags) into 2–5 distinct lesson candidates. Each candidate has the same shape as a `<lesson-candidate>`: `kind`, `topic`, body summary.

6. **Present the batch.** Show the user the list, with provenance per candidate (which commit / note / scanned tag drove it). Multi-select: which to promote?

7. **Per accepted candidate, run the write subflow** (SKILL.md steps 3–6) using that candidate's body as intake.

## What `flag_for_review` does NOT do

- It does **not** auto-trigger session-retro at start-session. The user has to invoke it explicitly.
- It does **not** prevent the handover from being superseded — `apply_supersession` rewrites the body callout but leaves the flag intact in frontmatter.
- It does **not** mean the handover is broken or special — it's just a lookup hint for future retro work.

## Future enrichment (out of scope)

Per spec §11: a richer session-retro product could add productivity metrics, task ordering analysis, tool-usage breakdown, and a dedicated review skill. Tracked but not built — this skill ships with the simple "scan + propose" version.
