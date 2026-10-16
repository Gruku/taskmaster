# Decision

A decision is a choice between two or more mutually exclusive paths that is waiting on the user. Recording it as a DEC entity means it survives context loss and shows on the dashboard; an option menu left in chat scrollback is gone once the context dies, and the user had to message themselves to remember open choices.

Ask a choice in chat as usual. Write a record when the choice is left open: the user defers it ("later", "let me think"), the work moves on without an answer, or the session is ending with it unanswered. A choice the user answers in the next message (brainstorming options, a structured question) doesn't become a record. After writing, say `DEC-NNN written: decide on the dashboard or reply with the option number`, and don't repeat the menu in chat. Go through this playbook rather than calling `backlog_decision_create` cold, so the authoring rules below get applied.

## Writing

`backlog_decision_create(title, options, recommendation, task_id, branch, related_issues, raised_in, body)`

- Title is 80 characters or fewer and action-shaped ("Land ue-plugin-086 fix").
- At least two options, mutually exclusive, one line each. "Do A" and "Do A then B" is a plan, not a decision. If you can't name two options, there is no decision; do the work.
- `recommendation` is a 1-based option index, or null when you have no preference.
- Link it: `task_id` is the in-progress task, `branch` the current git branch, `raised_in` the latest handover id when one was just written, `related_issues` any ISS-NNN it would settle. Body skeleton: `templates/decision-body.md`.
- If an open decision already covers the choice (`backlog_decision(action="list")`), update that one instead.

## Resolving, dropping, editing

- Resolve: `backlog_decision(action="resolve", decision_id, resolved_with=<1-based option>, rationale)`. Parse "option 2", "the local merge one" and similar phrasings. When a task is linked and `backlog_get_task` doesn't already show the decision, read the task's notes and write them back with one line appended, because `backlog_update_task(task_id, "notes", value)` replaces the notes whole: `YYYY-MM-DD · DEC-NNN resolved with option N: "<text>"`.
- Drop (circumstances changed): `backlog_decision(action="drop", decision_id, reason)`. The reason is required.
- Edit while open: `backlog_decision(action="update", decision_id, title, options, recommendation, body)`.
- List: `backlog_decision(action="list", status="open", task_id)`; `status="all"` shows every state.

`open` moves to `resolved` or `dropped`, and both are terminal. To reconsider, write a new decision and link the old one in its body. End-session sweeps open decisions linked to the task; its own playbook owns that flow. States and mutability: `references/lifecycle.md`.
