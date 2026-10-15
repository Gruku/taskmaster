# End-Session: pre-close sweeps

Read this when the playbook's "Before closing" steps need detail: the handover cue table, the decision sweep, and the idea sweep.

## Handover

Write a session handover without asking when the task is still in flight (`in-progress`), the session was long, or the user said anything like "for tomorrow", "remind me next time", "context handoff", "pick this up later". Skip it for a small one-touch session, or when the user said "no handover" or "skip handover".

Infer `session_kind` from the conversation:

| Cue | session_kind |
|---|---|
| "context handoff", "near compaction", "save before compact" | `deep-context` |
| "milestone done", "chunk complete", "ready for next plan", direction changed | `milestone` |
| Anything else | `continuity` |

Then invoke the `taskmaster:handover` skill with that `session_kind`. It writes directly, so end-session does not draft the body itself or ask whether to write. The user can edit or replace the file afterwards. End-session continues whatever the handover's outcome.

If a `pending_review_flag` was buffered upstream, pass `flag_for_review=true` and `review_reason=<buffered reason>` to the handover skill. If the handover is skipped, the flag is dropped.

## Decision sweep (before the handover write)

Sweep the open decisions linked to the in-progress task, so the handover can carry them:

1. `backlog_decision(action="list", status="open", task_id=<current>)`.
2. For each decision, ask the user (use your structured-question tool if available):
   - **Carry forward**: leave it open; record it under the handover body's "Open decisions" section as `[[DEC-NNN]] — <one-line summary>`.
   - **Resolve now**: present the options; on a pick, `backlog_decision(action="resolve", decision_id=id, resolved_with=N, rationale="<short>")`, then record it under "Resolved this session".
   - **Drop**: take a one-line reason, `backlog_decision(action="drop", decision_id=id, reason=...)`, then record it under "Resolved this session" with a `(dropped)` suffix.
3. `taskmaster:handover` inserts the two lists into the handover body markdown. `backlog_handover_create` has no separate `open_decisions` / `resolved_this_session` parameters: the body is the durable carrier, and the `[[DEC-NNN]]` links are what the viewer uses for cross-entity navigation.

## Handover index resync

`backlog_handover_resync()` re-applies the 30-entry index cap and moves overflow into `handovers/_archive/<year>/`. Run it only after hand edits to `handovers/` or after the cap was lowered; a normal session needs no call.

## Idea sweep

Scan the conversation for `<idea-candidate>` tags. For each one:

1. Read the `title` attribute (required) and `tags`, `status`, `related-task`, `related-issue` (optional). The tag's text is the body; with no text, use the title.
2. Call:
   ```
   backlog_idea_create(
       title=<title>,
       body=<tag-body-or-title>,
       tags=<parsed tags or []>,
       status=<parsed status or "candidate">,
       related_tasks=<[task] if related-task else []>,
       related_issues=<[issue] if related-issue else []>,
   )
   ```
3. Collect the returned IDEA-NNN ids.

Create them without asking per item: the tag was already the capture step, and a per-item prompt at close time only delays the wrap-up.

Report the ids as one bullet in the wrap-up summary ("Ideas captured this session: IDEA-009, IDEA-010"). With no ids, leave the bullet out.
