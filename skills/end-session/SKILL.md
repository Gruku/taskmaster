---
name: end-session
description: "Close out a work session by logging what was accomplished. Invoke when the user says 'end session', 'I'm done for today', 'let's wrap up', 'log this work', 'mark this task done', or 'save progress'. Auto-generates Done/Decisions/Issues summary, transitions task status, commits tracking files. This is the ONLY correct way to mark tasks done or in-review with a session record."
---

# End Session

Log the current work session, transition tasks, and commit tracking files.

## Why This Skill Exists

`backlog_complete_task` atomically writes the changelog entry AND transitions the task status. Calling `backlog_update_task` directly to set status to "done" leaves the PROGRESS.md changelog silent — the next `/start-session` will have no "last session" context, and the project loses its work history. This skill ensures every status transition comes with a session record.

**This is the ONLY way to mark tasks as done or in-review with proper session logging.**

## Steps

### v3 pre-steps (skip on v2 backlogs)

Check the first line of `backlog_status` output (`**Schema:** v<N>`). If `v3` or higher, run these BEFORE the existing flow. The Schema line is the *effective* version — a backlog with v3 entity content reports v3 even when the `schema_version` marker is missing.

**v3-pre-1: Snapshot.** Call `backlog_snapshot(quiet=true)` to capture pre-end-of-session state. This makes the next session's `backlog_recap` show what changed across the session boundary, not just from now to the next snapshot. Cheap (~50ms), no token cost.

**v3-pre-2a: Lesson candidate sweep.** Decide whether to invoke `taskmaster:lesson` for an end-session sweep. Auto-offer when ANY of:
   - Any `<lesson-candidate>` tag visible in the current conversation context.
   - Any entries in `.taskmaster/lessons/_candidates.md` (check via `backlog_lesson_candidates_list`).
   - Any feedback-memory cluster with 2+ entries scoped to this project (auto-suggestion source — separate from candidate scans).

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
   | Defer | `backlog_lesson_candidate_defer(...)` — the candidate stays in `_candidates.md` for next session. |
   | Discard | Drop without persisting (no tool call). |

   For any promoted candidate with `scope="session"`: the lesson skill **buffers** a `flag_for_review` for the upcoming handover write (next sub-step). Do not modify any existing handover here.

   Then: list lessons that were trigger-loaded at start-session OR cited mid-session by Claude. Multi-select prompt: "which actually applied this session?" For each pick:

   ```
   backlog_lesson_reinforce(lesson_id="L-NNN")
   ```

   After all reinforcements: if any return surfaces "Eligible for promotion to core tier", ask the user once:

   > *"L-NNN is eligible for core tier (auto-loaded every session). Promote?"* (yes → `backlog_lesson_update(lesson_id, tier="core")`; respect the core cap from references/promotion-decay.md)

   Finally: if any lessons auto-retired this session (server-side), emit one info line:

   > *"Retired N stale lessons (review with `backlog_lesson_list --tier retired`)."*

   No prompt, signal only.

   If none of the auto-offer conditions apply, skip this whole sub-step silently — no prompt.

**v3-pre-2: Handover auto-write.** Write a session handover automatically (no prompt) when ANY of:
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

**Skip the auto-write only if:** none of the four trigger conditions above are true (lightweight one-touch session — no handover needed) OR the user explicitly said "no handover" / "skip handover" this session.
If v3-pre-2a buffered a `pending_review_flag` (any `scope="session"` candidate was promoted in this session), pass `flag_for_review=true` and `review_reason=<buffered reason>` through to the handover skill's call. The handover skill forwards both kwargs to `backlog_handover_create`. If the user skipped the handover write, the flag is dropped silently.

**v3-pre-2b: Handover archive sweep.** Call `backlog_handover_resync()` quietly to enforce the 30-entry index cap and move any overflow into `handovers/_archive/<year>/`. `backlog_handover_create` already runs the sync, so this sub-step only matters when (a) no handover was written this session but the user manually edited the `handovers/` directory between sessions, or (b) the cap was lowered. Cheap (~30ms), no token cost. Skip silently on v2.

**v3-pre-2c: Idea-candidate sweep.**

After completing the work-summary phase, scan the in-context transcript for `<idea-candidate>` XML tags emitted earlier in the session. For each tag found:

1. Parse the `title` attribute (required), `tags`, `status`, `related-task`, `related-issue`, `related-lesson` (all optional).
2. Use the tag's text content as the body. If empty, use the title as the body.
3. Call:
   ```
   backlog_idea_create(
       title=<title>,
       body=<tag-body-or-title>,
       tags=<parsed tags or []>,
       status=<parsed status or "candidate">,   # default to "candidate" so they're filterable in the viewer
       related_tasks=<[task] if related-task else []>,
       related_issues=<[issue] if related-issue else []>,
       related_lessons=<[lesson] if related-lesson else []>,
       created_by="Claude",
   )
   ```
4. Collect the returned IDEA-NNN ids.

**Commit directly — do NOT prompt the user per item.** The user's standing rule is no draft-and-approve gates in end-session.

After all candidates are committed, also tally the IDEA-NNNs that were auto-logged this session via path C (look at the IDEAS.md index for entries with `created` timestamps within the session window — alternatively, just call `backlog_idea_list(limit=10)` and filter to entries newer than the session start).

Report the result inline in the wrap-up summary as a separate bullet:

> **Ideas captured this session:** N total
> - From `<idea-candidate>` tags (committed as `status: "candidate"`): IDEA-009, IDEA-010
> - Auto-logged sharp ideas: IDEA-011

If both counts are zero, omit the bullet entirely.

The user can then review/edit/archive captured ideas at their leisure via the Ideas viewer screen.

### Existing flow

0. **Determine summary mode.** Check the session weight:
   - Count commits this session and files changed
   - If the session was **light** (1-2 commits, single-topic work, or user says "quick wrap"):
     - Use **auto-summary mode**: skip the structured Done/Decisions/Issues template
     - Generate a one-line summary from git: `git diff --stat HEAD~N` and commit messages
     - Call `backlog_complete_task` with `auto_summary=true` and pass the git stats as the `done` field
     - Format: "Files changed: N | +X -Y\nCommits: \"msg1\", \"msg2\""
   - If the session was **substantial** (3+ commits, multiple topics, design decisions made):
     - Use **structured mode** (the existing flow below)
   - The user can always override: "give me the full summary" forces structured mode

1. **Auto-generate session summary** by reviewing the current conversation:
   - **Done:** List of accomplishments (what was built, fixed, configured)
   - **Decisions:** Architectural or design choices made during the session
   - **Issues:** Problems encountered, unresolved items. If none, write "None"
   - **Tasks touched:** IDs of any tasks whose status changed this session

2. **Draft a user-facing patchnote (optional).** If the task has meaningful user impact (new feature, visible UX change, fixed bug a user would notice), draft a 1-2 sentence patchnote in the user's voice — not the internal title. Skip for internal/infra/cleanup/refactor tasks (leave blank). Examples:
   - ✅ "Interactive clarification overlay — multi-question queue with option chips and a single SUBMIT ALL action."
   - ✅ "Release-notes pipeline now aggregates patchnotes per release bucket."
   - ❌ (skip) Refactor of `_load()` helper, CI config tweak, dependency bump.

   Also pick a **release bucket** (`pre-alpha`, `alpha-1.0`, …) if the project uses them — ask the user if unclear. Patchnotes without a release tag are still stored but won't surface in `backlog_release_notes` unless `include_unreleased=true`.

3. **Generate session title:** `{Topic}: {Brief Description}` (don't include the date — the server auto-prefixes with today's date)

4. **Determine target status (no prompt by default).** Default silently to `in-review` — the user tests and marks `done` later. Override only when conversation context clearly indicates one of:

   - User already confirmed manual testing during the session ("I tested it", "works on my end") → `done`
   - Task has no testable surface (pure infra/cleanup/docs/config bump) → `done`
   - User explicitly says "mark done" / "this is done" → `done`

   Do not ask "does this need manual testing?" — that's a confirmation gate on a decision the default already handles correctly. The user can transition `in-review → done` later with a one-liner.

5. **Skip the review gate.** Move straight to step 6 and call `backlog_complete_task`. Do not present the auto-generated summary, patchnote, or target status as a "looks good?" draft — these are derived from conversation context and git state and the user can correct anything as a follow-up. Only ask the user when there is a genuine ambiguity Claude cannot resolve from context (e.g., two equally plausible target statuses, a patchnote that could belong to either of two release buckets).

6. **Call `backlog_complete_task` with all session fields:**

   ```
   backlog_complete_task(
       task_id="...",
       session_title="Topic: Brief Description",
       done="item 1\nitem 2\nitem 3",
       decisions="decision 1\ndecision 2",
       issues="None",
       tasks_touched="task-001, task-002",
       target_status="in-review",  # or "done"
       patchnote="Interactive clarification overlay — ...",  # omit or "" for internal tasks
       release="pre-alpha",  # omit or "" if project doesn't use release buckets
   )
   ```

   For OTHER tasks that also need transitions (not the primary task):
   - Use `backlog_update_task` for individual status changes — these won't get changelog entries, which is fine for secondary tasks.

**v3-post-complete-1: Issue-close-on-task-complete hook.** (v3 backlogs only — Schema: v3+)

After `backlog_complete_task` succeeds, check whether the just-completed task has any `related_issues`. For each issue whose current `status` is `open` or `investigating`, prompt:

> *"ISS-XXX is still open — close it as fixed in `<task_id>`, or leave for follow-up?"*

| Choice | Action |
|---|---|
| Close as fixed | `backlog_issue_update(issue_id="ISS-XXX", status="fixed", fixed_in_task="<task_id>")` |
| Leave for follow-up | No tool call — issue remains open |

If the task has no `related_issues`, or all related issues are already `fixed` / `closed` / `wont-fix`, skip this sub-step silently — no prompt.

If multiple issues are open, prompt for each in sequence (or offer a multi-select if the skill implementation supports it).

Reference: `taskmaster:issue` skill, entry point `close-on-task-complete`.

7. **Worktree cleanup (done tasks only):**
   - If the task was marked **done** and has a worktree, offer cleanup:
     "Clean up the worktree for `{task_id}`? This removes the isolated working directory."
   - If confirmed: `git worktree remove .worktrees/{task-id}`, then `backlog_update_task(task_id, "worktree", "")`
   - If declined: leave it — the user may want to reference it.
   - **Skip for `in-review` tasks** — the user still needs the worktree for testing.

8. **Commit tracking files:**
   ```bash
   git add backlog.yaml PROGRESS.md
   git commit -m "chore: log session — {brief topic}

   {1-line summary}"
   ```

   **(v3) Also stage these directories if they have changes:**
   - `.taskmaster/handovers/` (handovers written this session)
   - `.taskmaster/issues/` (issues created or updated)
   - `.taskmaster/lessons/` (lessons reinforced — last_reinforced field updates)
   - `.taskmaster/tasks/` (per-task body updates from spec-review or notes)

   Do NOT stage `.taskmaster/snapshots/` or `.taskmaster/auto/` — both are gitignored. If git complains they're tracked, that's a misconfiguration the user should fix; do not work around it.

9. **Confirm:** "Session logged and committed. Task is now `{target_status}`."

## Task Lifecycle

`in-review` means "Claude is done, user tests now." `done` means "user confirmed it works." Full flow: `todo → in-progress → in-review → done → archived`.

## Additional Resources

- **`references/edge-cases.md`** — no in-progress task, not in a git repo, multiple tasks changed.
- **`references/auto-mode.md`** — how to behave when `backlog_auto_status` reports an active run.
