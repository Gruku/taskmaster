# Taskmaster Router

All work in a taskmaster-enabled project flows through the task system. This skill detects what the user wants and routes to the right sub-skill or MCP tool.

Every playbook path below also exists as a native skill on Claude Code/ZCode:
`taskmaster:<name>`, where `<name>` is the playbook directory name.

## Intent Detection

| Intent Signal | Route To |
|---|---|
| "Let's get started", "orient me", "show the backlog" | `../start-session/playbook.md` |
| "Pick task X", names a task ID, "what should I work on" | `../pick-task/playbook.md` |
| Implementation request, in-progress task exists | Work in current task's worktree |
| Implementation request, no in-progress task | `../pick-task/playbook.md` first |
| "Review this spec", "challenge this design" | `../spec-review/playbook.md` |
| "Is this ready?", "check my work", "review gate" | `../review-gate/playbook.md` |
| "End session", "wrap up", "mark task done" | `../end-session/playbook.md` |
| "Set up taskmaster", "initialize backlog" | `../init-taskmaster/playbook.md` |
| "Write a handover", "for tomorrow" | `../handover/playbook.md` |
| "Log an issue", "file an issue" | Word-agnostic intake — `../issue/playbook.md` if evidence cited, else `../bug/playbook.md` |
| "Log a bug", "track this defect" | `../bug/playbook.md` |
| "Save this as an idea", "/add-idea" | `../add-idea/playbook.md` |
| "auto this task", "autopilot", "auto-epic/phase X" | Redirect to ultracode (auto removed) |
| "Upgrade to v3", "migrate to v3" | `../migrate-v3/playbook.md` |
| "Check TODOs", "todo audit" | `../check-todos/playbook.md` |
| "Set up linear", "link to linear", "linear status" | `../linear/playbook.md` |
| Status, search, phase, recap, snapshot | Direct `backlog_*` tool call |

Full routing table + word-agnostic intake algorithm: read `references/routing-table.md` and `references/word-agnostic-intake.md`.

## Do NOT Route Through Taskmaster

- Pure git operations (commit, push, branch) — git directly
- PR security reviews — dedicated review tools

## When Multiple Intents Match

Handle sequentially — complete the first action before starting the second.

## When to Deepen

When routes are ambiguous (handover vs end-session, issue vs task), read `references/disambiguation.md`.

## Mid-session deepening

Skills stay in glance mode. Deepen specific entities directly — no skill re-invocation needed:

| User asks for | Call |
|---|---|
| "show me HND-012" | `backlog_handover_get("HND-012")` |
| "read the plan for T-001" | `backlog_get_task("T-001", sections=["plan"])` |
| "full task details" | `backlog_get_task("T-001", verbose=True)` |
| "details on ISS-014" | `backlog_issue_get("ISS-014", verbose=True)` |
