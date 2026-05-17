---
name: taskmaster
description: "Universal taskmaster work router for any project with backlog.yaml. Invoke for implementing features, fixing bugs, writing tests, refactoring, planning epics, or any narrative-continuity operation (handovers, issues, lessons, auto-mode). The only exceptions are pure git commits and dedicated PR security reviews."
---

# Taskmaster Router

All work in a taskmaster-enabled project flows through the task system. This skill detects what the user wants and routes to the right sub-skill or MCP tool.

## Intent Detection

| Intent Signal | Route To |
|---|---|
| "Let's get started", "orient me", "show the backlog" | `taskmaster:start-session` |
| "Pick task X", names a task ID, "what should I work on" | `taskmaster:pick-task` |
| Implementation request, in-progress task exists | Work in current task's worktree |
| Implementation request, no in-progress task | `taskmaster:pick-task` first |
| "Review this spec", "challenge this design" | `taskmaster:spec-review` |
| "Is this ready?", "check my work", "review gate" | `taskmaster:review-gate` |
| "End session", "wrap up", "mark task done" | `taskmaster:end-session` |
| "Set up taskmaster", "initialize backlog" | `taskmaster:init-taskmaster` |
| "Write a handover", "context handoff", "for tomorrow" | `taskmaster:handover` |
| "Log a bug", "this is broken", "track this defect" | `taskmaster:issue` |
| "Remember this", "save as a lesson" | `taskmaster:lesson` |
| "Save this as an idea", "/add-idea" | `taskmaster:add-idea` |
| "Auto this task", "autopilot T-001" | `taskmaster:auto-task` |
| "Auto-epic <id>", "auto-phase <id>" | `taskmaster:auto-epic` / `taskmaster:auto-phase` |
| "Upgrade to v3", "migrate to v3" | `taskmaster:migrate-v3` |
| "Check TODOs", "todo audit" | `taskmaster:check-todos` |
| Status, search, phase, recap, snapshot | Direct `backlog_*` tool call |

For the full 35-row routing table, all v3 routes, and implementation-without-a-task guidance, read `references/routing-table.md`.

## Do NOT Route Through Taskmaster

- Pure git operations (commit, push, branch) — git directly
- PR security reviews — dedicated review tools

## When Multiple Intents Match

Handle sequentially — complete the first action before starting the second.

## When to Deepen

When routes are ambiguous (handover vs end-session, issue vs task, lesson vs note, auto-task vs pick-task), read `references/disambiguation.md`.

## Mid-session deepening

Skills stay in glance mode during a session. When the user asks to see more detail on a specific entity, Claude deepens that entity directly — no need to re-invoke a ceremony skill.

| User says | Claude does |
|---|---|
| "show me HND-012" / "open that handover" | `backlog_handover_get("HND-012")` (full body by default) |
| "read the plan for T-001" / "show me the spec" | `backlog_get_task("T-001", sections=["plan"])` or `sections=["spec"]` |
| "full task details" / "load everything for T-001" | `backlog_get_task("T-001", verbose=True)` |
| "show me lesson L-007" / "read that lesson" | `backlog_lesson_get("L-007")` (full body) |
| "details on ISS-014" | `backlog_issue_get("ISS-014", verbose=True)` |

The deepening is surgical — one entity, the section the user asked for. The ceremony (start-session, pick-task) is not re-run. This keeps the rest of the session context unaffected.
