# Taskmaster Router

All work in a taskmaster-enabled project flows through the task system. This skill detects what the user wants and routes to the right sub-skill or MCP tool.

Each converted target's "Route To" cell gives the playbook path first, with
the native invocation as a dual-path hint (CONVENTIONS.md rule 4): e.g.
`../start-session/playbook.md`; on Claude Code/ZCode: `taskmaster:start-session`.

## Intent Detection

| Intent Signal | Route To |
|---|---|
| "Let's get started", "orient me", "show the backlog" | `../start-session/playbook.md`; on Claude Code/ZCode: `taskmaster:start-session` |
| "Pick task X", names a task ID, "what should I work on" | `../pick-task/playbook.md`; on Claude Code/ZCode: `taskmaster:pick-task` |
| Implementation request, in-progress task exists | Work in current task's worktree |
| Implementation request, no in-progress task | `../pick-task/playbook.md`; on Claude Code/ZCode: `taskmaster:pick-task` first |
| "Review this spec", "challenge this design" | `../spec-review/playbook.md`; on Claude Code/ZCode: `taskmaster:spec-review` |
| "Is this ready?", "check my work", "review gate" | `../review-gate/playbook.md`; on Claude Code/ZCode: `taskmaster:review-gate` |
| "End session", "wrap up", "mark task done" | `../end-session/playbook.md`; on Claude Code/ZCode: `taskmaster:end-session` |
| "Set up taskmaster", "initialize backlog" | `../init-taskmaster/playbook.md`; on Claude Code/ZCode: `taskmaster:init-taskmaster` |
| "Write a handover", "for tomorrow" | `../handover/playbook.md`; on Claude Code/ZCode: `taskmaster:handover` |
| "Log an issue", "file an issue" | Word-agnostic intake — `../issue/playbook.md` (`taskmaster:issue`) if evidence cited, else `../bug/playbook.md` (`taskmaster:bug`) |
| "Log a bug", "track this defect" | `../bug/playbook.md`; on Claude Code/ZCode: `taskmaster:bug` |
| "Remember this", "save as a lesson" | `../lesson/playbook.md`; on Claude Code/ZCode: `taskmaster:lesson` |
| "Save this as an idea", "/add-idea" | `../add-idea/playbook.md`; on Claude Code/ZCode: `taskmaster:add-idea` |
| "auto this task", "autopilot", "auto-epic/phase X" | Redirect to ultracode (auto removed) |
| "Upgrade to v3", "migrate to v3" | `../migrate-v3/playbook.md`; on Claude Code/ZCode: `taskmaster:migrate-v3` |
| "Check TODOs", "todo audit" | `../check-todos/playbook.md`; on Claude Code/ZCode: `taskmaster:check-todos` |
| "Set up linear", "link to linear", "linear status" | `../linear/playbook.md`; on Claude Code/ZCode: `taskmaster:linear` |
| Status, search, phase, recap, snapshot | Direct `backlog_*` tool call |

Full routing table + word-agnostic intake algorithm: read `references/routing-table.md` and `references/word-agnostic-intake.md`.

## Do NOT Route Through Taskmaster

- Pure git operations (commit, push, branch) — git directly
- PR security reviews — dedicated review tools

## When Multiple Intents Match

Handle sequentially — complete the first action before starting the second.

## When to Deepen

When routes are ambiguous (handover vs end-session, issue vs task, lesson vs note), read `references/disambiguation.md`.

## Mid-session deepening

Skills stay in glance mode. Deepen specific entities directly — no skill re-invocation needed:

| User asks for | Call |
|---|---|
| "show me HND-012" | `backlog_handover_get("HND-012")` |
| "read the plan for T-001" | `backlog_get_task("T-001", sections=["plan"])` |
| "full task details" | `backlog_get_task("T-001", verbose=True)` |
| "show me lesson L-007" | `backlog_lesson_get("L-007")` |
| "details on ISS-014" | `backlog_issue_get("ISS-014", verbose=True)` |
