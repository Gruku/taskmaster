---
name: end-session
description: "Close out a work session: log what was done, move finished tasks to done or in-review with backlog_complete_task, and commit the tracking. Use for 'end session', 'I'm done for today', 'let's wrap up', 'mark this task done', 'save progress'. A mid-work handoff that closes nothing is taskmaster:handover."
---

# End Session

Follow the playbook at `../../playbooks/end-session/playbook.md` (relative to
this skill's base directory); it holds the tool calls, store rules and
user checkpoints for this workflow. `references/` and `templates/` paths
inside it resolve relative to the playbook's own directory.
