---
name: taskmaster
description: "Universal work router — invoke for ANY work the user wants to do in a project with backlog.yaml. This includes implementing features, fixing bugs, writing tests, refactoring code, setting up CI/CD, creating components, explaining code, planning epics, and all other development tasks. Taskmaster ensures all work flows through the task system for tracking, session logging, and worktree isolation. The only exceptions are pure git operations (commit/push) and dedicated PR security reviews. When in doubt, invoke this skill — it's better to route through taskmaster and discover the work isn't tracked than to skip it and lose session history."
---

# Taskmaster Router

All work in a taskmaster-enabled project should flow through the task system. This skill detects what the user wants to do and routes to the right sub-skill or MCP tool.

The reason everything routes through here: when work happens outside the task system, session history is lost, worktree isolation is skipped, and the backlog drifts out of sync with reality. By routing all work through taskmaster, we ensure every piece of work is tracked, logged, and isolated.

## Intent Detection

Read the user's message and match it to one of these intents:

| Intent Signal | Route To |
|--------------|----------|
| Starting a new conversation, "what's going on", "orient me", "show the backlog", "let's get started" | `taskmaster:start-session` |
| "Pick task X", "let's work on X", "what should I tackle", names a task ID, "start task" | `taskmaster:pick-task` |
| Any implementation request (build feature, fix bug, write tests, refactor, create component, set up CI/CD) when a task is in-progress | Work in the current task's worktree — no routing needed, just confirm you're in the right worktree |
| Any implementation request when NO task is in-progress | `taskmaster:pick-task` first — find or create a task, then work in its worktree |
| "Review this spec", "challenge this design", "is this the right approach?", "spec review" (before implementation) | `taskmaster:spec-review` |
| "Is this ready?", "run the review", "check my work", "I think this is done", "quality check" (after implementation) | `taskmaster:review-gate` |
| "End session", "I'm done", "wrap up", "log this", "mark task done", "save progress" | `taskmaster:end-session` |
| "Set up taskmaster", "initialize", "create backlog", first time in a project without backlog.yaml | `taskmaster:init-taskmaster` |
| "Add a task", "create a task for X", "plan out this epic" | Direct tool call — use `backlog_add_task` or `backlog_add_epic` with appropriate fields |
| "Show task X", "task details", "what's the status of X" | Direct tool call — use `backlog_get_task` or `backlog_status` |
| "Search for X", "find tasks about X" | Direct tool call — use `backlog_search` |
| "Create a phase", "plan the next phase", "set up phases" | Direct tool call — use `backlog_add_phase` |
| "Show phase progress", "where are we in the phase?" | Direct tool call — use `backlog_phase_status` |
| "Advance to next phase", "this phase is done" | Direct tool call — use `backlog_advance_phase` |
| "Check TODOs", "scan for TODOs", "are my TODOs tracked", "todo audit" | `taskmaster:check-todos` |

## Do NOT Route Through Taskmaster

- Pure git operations: "commit and push", "create a branch" — these are handled by git directly
- PR security reviews: "review this PR for vulnerabilities" — these use dedicated review tools

## When Multiple Intents Match

If the user's message contains multiple intents (e.g., "let's finish this task and start a new one"), handle them sequentially — complete the first action before starting the second.

## Implementation Work Without a Task

If the user asks to implement something and there's no task for it yet, don't just start coding. Instead:
1. Call `backlog_status` to check what's in-progress
2. If the work fits an existing task, pick that task
3. If not, suggest creating a new task: "This looks like new work. Want me to add a task for it under epic X?"
4. Once a task is picked, work proceeds in its worktree

This ensures nothing falls through the cracks.
