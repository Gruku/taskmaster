# Taskmaster — Codex CLI adapter

Append this fragment to your project's `AGENTS.md` (or `~/.codex/AGENTS.md`
for all projects), replacing `{{TASKMASTER_HOME}}` with the absolute path of
your taskmaster checkout.

---

## Taskmaster

This project tracks work in a `.taskmaster/` backlog (tasks, epics, bugs,
issues, handovers, ideas) served by the `taskmaster` MCP server
(`backlog_*` tools). Workflow discipline lives in assistant-neutral
playbooks — when a request matches an intent below, read the playbook in
full and execute it exactly. `references/` and `templates/` paths inside a
playbook resolve relative to the playbook's own directory.

Playbook root: `{{TASKMASTER_HOME}}/playbooks/`

| Intent | Playbook |
|---|---|
| Any task-related request you can't route precisely | `playbooks/taskmaster/playbook.md` (router) |
| New conversation, "orient me", "show the backlog" | `playbooks/start-session/playbook.md` |
| "Pick task X", names a task ID, "what should I work on", "continue where we left off" | `playbooks/pick-task/playbook.md` |
| "Is this ready?", "check my work", "review gate" | `playbooks/review-gate/playbook.md` |
| "Review this spec", "challenge this design" | `playbooks/spec-review/playbook.md` |
| "Review this plan", "is this plan solid" | `playbooks/plan-review/playbook.md` |
| "End session", "wrap up", "mark task done" | `playbooks/end-session/playbook.md` |
| "Write a handover", "for tomorrow", context handoff | `playbooks/handover/playbook.md` |
| "Log a bug", "this is a bug", one-off defect | `playbooks/bug/playbook.md` |
| "File an issue" with recurring/systemic evidence | `playbooks/issue/playbook.md` |
| "Save this as an idea" | `playbooks/add-idea/playbook.md` |
| About to present ≥2 mutually exclusive options | `playbooks/decision/playbook.md` |
| "Set up taskmaster", "initialize backlog" | `playbooks/init-taskmaster/playbook.md` |
| "Upgrade to v3", "migrate to v3" | `playbooks/migrate-v3/playbook.md` |
| "Check TODOs", "todo audit" | `playbooks/check-todos/playbook.md` |
| "Set up linear", "link to linear" | `playbooks/linear/playbook.md` |
| Status, search, recap, snapshot | direct `backlog_*` tool call |

Each playbook is also installable as a slash prompt (`/start-session`, …) —
see the adapter README.

**Gate discipline (advisory).** Codex has no enforcement hooks (that layer is
Claude-Code-class only). Follow the discipline the playbooks describe anyway:
pick a task before implementing, run the review-gate before declaring work
done, record gates and merges via the `backlog_*` tools, and never mark a
task done without a session record (end-session playbook).
