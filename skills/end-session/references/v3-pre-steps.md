# End-Session — v3 Pre-Steps Detail

Read this file when any v3 pre-step condition fires. The SKILL.md body carries
only the headline and condition; the full flow is here.

## v3-pre-2a: Lesson Candidate Sweep

Decide whether to invoke `taskmaster:lesson` for an end-session sweep. Auto-offer when ANY of:
- Any `<lesson-candidate>` tag visible in the current conversation context.
- Any entries in `.taskmaster/lessons/_candidates.md` (check via `backlog_lesson_candidates_list`).
- Any feedback-memory cluster with 2+ entries scoped to this project.

Inputs (gathered in this order, then merged for review):

1. **Candidate-discovery scans** (routine):
   - In-context scan: grep Claude's own conversation memory for `<lesson-candidate `.
   - Deferred-file read: `backlog_lesson_candidates_list`.
2. **Auto-suggestion source** (routine, separate input — not a candidate scan):
   - Scan auto-memory `feedback/*.md` for 2+ similar entries this project. These are *promotion suggestions*, not pre-flagged candidates.
3. **Disk-transcript scan** (on-demand only): if this session had a `/compact` event, offer `backlog_lesson_candidates_scan(days=7)` as a recovery option. Skip otherwise.

If any candidates or suggestions exist, ask:

> *"Found N lesson candidates from this session. Review now?"* (user-confirmed; default skip)

If the user accepts, invoke `taskmaster:lesson` with each candidate. Per-candidate options:

| Action | What runs |
|---|---|
| Promote | Lesson skill's write subflow (auto-extract + user review + `backlog_lesson_create`). |
| Defer | `backlog_lesson_candidate_defer(...)` — stays in `_candidates.md` for next session. |
| Discard | Drop without persisting (no tool call). |

For any promoted candidate with `scope="session"`: the lesson skill **buffers** a `flag_for_review` for the upcoming handover write (next sub-step). Do not modify any existing handover here.

Then: list lessons that were trigger-loaded at start-session OR cited mid-session by Claude. Multi-select prompt: "which actually applied this session?" For each pick:

```
backlog_lesson_reinforce(lesson_id="L-NNN")
```

After all reinforcements: if any return surfaces "Eligible for promotion to core tier", ask the user once:

> *"L-NNN is eligible for core tier (auto-loaded every session). Promote?"* (yes → `backlog_lesson_update(lesson_id, tier="core")`; respect the core cap from `references/promotion-decay.md`)

Finally: if any lessons auto-retired this session (server-side), emit one info line:

> *"Retired N stale lessons (review with `backlog_lesson_list --tier retired`)."*

No prompt, signal only.

If none of the auto-offer conditions apply, skip this whole sub-step silently — no prompt.

## v3-pre-2: Handover Auto-Write

Write a session handover automatically (no prompt) when ANY of:
- Session length > 60 turns of conversation.
- Conversation context estimate > 200k tokens.
- A task is still in flight (status `in-progress` or `auto/state.json` cursor non-null).
- User said anything like "for tomorrow", "remind me next time", "context handoff", "pick this up later".

Infer `session_kind` from conversation cues:

| Cue | session_kind |
|---|---|
| "context handoff", "near compaction", "300k", "save before compact" | `context-handoff` |
| "milestone done", "chunk complete", "ready for next plan" | `milestone-complete` |
| "we changed direction", "pivoting", "new approach" | `pivot` |
| No in-flight task, no commits to a feature | `exploration` |
| Otherwise | `end-of-day` |

Then **invoke the `taskmaster:handover` skill** with the inferred `session_kind`. The handover skill writes directly (no draft-and-approve gate). End-session does NOT draft the body itself and does NOT ask the user whether to write — the heuristics already fired. The user can say "skip handover" upfront to opt out, or edit/replace the file after it's written.

End-session continues regardless of the handover skill's outcome.

**Skip the auto-write only if:** none of the four trigger conditions above are true (lightweight one-touch session) OR the user explicitly said "no handover" / "skip handover" this session.

If v3-pre-2a buffered a `pending_review_flag`, pass `flag_for_review=true` and `review_reason=<buffered reason>` through to the handover skill. If the user skipped the handover write, the flag is dropped silently.

## Decision Sweep (before handover write)

Before invoking `taskmaster:handover`, sweep open decisions linked to the in-progress task:

1. Call `backlog_decision_list(status="open", task_id=<current>)`.
2. If non-empty, for **each** decision ask via `AskUserQuestion`:
   - **Carry forward** — leave the decision open; record it under the handover body's "Open decisions" section as `[[DEC-NNN]] — <one-line summary>`.
   - **Resolve now** — present options; on pick, call `backlog_decision_resolve(id, resolved_with=N, rationale="<short>")`, then record it under "Resolved this session".
   - **Drop** — capture one-line reason; call `backlog_decision_drop(id, reason=...)`, then record it under "Resolved this session" with `(dropped)` suffix.
3. Auto-resolved decisions (via commit message `Resolves: DEC-NNN with option N`) need no prompting — query `backlog_decision_list(status="resolved", resolved_in=session_window)` and append them to "Resolved this session" the same way.
4. The two collected lists are inserted into the handover **body markdown** by `taskmaster:handover` (step 5 of that skill). `backlog_handover_create` has no separate `open_decisions` / `resolved_this_session` parameters — the body is the durable carrier and the `[[DEC-NNN]]` link syntax is what powers cross-entity navigation in the viewer.

## v3-pre-2b: Handover Archive Sweep

Call `backlog_handover_resync()` quietly to enforce the 30-entry index cap and move any overflow into `handovers/_archive/<year>/`. Only matters when (a) no handover was written this session but the user manually edited `handovers/`, or (b) the cap was lowered. Skip silently on v2.

## v3-pre-2c: Idea-Candidate Sweep

After completing the work-summary phase, scan the in-context transcript for `<idea-candidate>` XML tags. For each tag found:

1. Parse the `title` attribute (required), `tags`, `status`, `related-task`, `related-issue`, `related-lesson` (all optional).
2. Use the tag's text content as the body. If empty, use the title as the body.
3. Call:
   ```
   backlog_idea_create(
       title=<title>,
       body=<tag-body-or-title>,
       tags=<parsed tags or []>,
       status=<parsed status or "candidate">,
       related_tasks=<[task] if related-task else []>,
       related_issues=<[issue] if related-issue else []>,
       related_lessons=<[lesson] if related-lesson else []>,
       created_by="Claude",
   )
   ```
4. Collect the returned IDEA-NNN ids.

**Commit directly — do NOT prompt the user per item.** The user's standing rule is no draft-and-approve gates in end-session.

After all candidates are committed, also tally any IDEA-NNNs that were auto-logged via path C (call `backlog_idea_list(limit=10)` and filter to entries newer than the session start).

Report inline in the wrap-up summary as a separate bullet:

> **Ideas captured this session:** N total
> - From `<idea-candidate>` tags: IDEA-009, IDEA-010
> - Auto-logged: IDEA-011

If both counts are zero, omit the bullet entirely.
