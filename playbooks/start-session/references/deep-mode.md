# start-session: deep briefing (`--deep`)

Runs when the user asks for `--deep`, "full briefing", "give me everything", or an equivalent explicit depth signal, after the glance in `../playbook.md`. It adds the full issue list, the last session record, open decisions and untracked work.

D1 to D3 don't depend on each other; call them together.

## Reads

- **D1. All open issues:** `backlog_issue_list(status="open")`, grouped P0 to P3, with P0 and P1 flagged.
- **D2. Last session:** `backlog_last_session` returns the most recent PROGRESS.md entry; show it as a `**Last session:**` block for continuity.
- **D3. Open decisions:** `backlog_decision(action="list", status="open")`; report the count and titles, for example "3 open decisions: DEC-001 (Land ue-plugin-086), DEC-003 (Voice billing semantics), DEC-005 (...)". When a decision blocks the task the user has in mind, say so in the recap of where they left off.
- **D4. Untracked work:** list commits on the main branch since the last session's date (the `### YYYY-MM-DD` heading in D2) that aren't on an in-progress task's `branch`, with `git log --oneline --since="<date>" --no-merges`. Show them neutrally: "Since last session: N commits outside tracked tasks", one line each. Skip the section when there are none.

## Order of the deep additions

All open issues, last session, open decisions, then the untracked-work notice.

## Spec-review hint

For a suggested critical or high task that has a spec or plan but no `spec_review` record, add: "run `taskmaster:spec-review {task_id}` before picking, because the design hasn't been vetted."

The glance anchors on `backlog_thread_list()`; use `backlog_handover_list` only for per-handover history.
