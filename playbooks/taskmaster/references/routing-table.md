# Taskmaster Router — Rarer Routes

The playbook carries the common routes. This file holds the rest, for when the playbook table doesn't settle where a request goes. Skills are `taskmaster:<name>`. On a v2 backlog, handover, issue, bug, idea and decision routes need `taskmaster:migrate-v3` first.

## Tasks, areas, phases

| Intent Signal | Route To |
|---|---|
| "Add a task", "create a task for X", "plan out this epic" | `backlog_add_task` / `backlog_add_epic` (an epic needs `done_when`; a workstream that never finishes is an area, not an epic) |
| "Create an area", "define a subsystem", "what areas exist", "rename this area" | `backlog_area_create` / `backlog_area_list` / `backlog_area_get` / `backlog_area_update` |
| "Show task X", "task details", "what's the status of X" | `backlog_get_task` / `backlog_status` |
| "Search for X", "find tasks about X" | `backlog_search` |
| "Create a phase", "plan the next phase", "set up phases" | `backlog_add_phase` |
| "Show phase progress", "where are we in the phase?" | `backlog_phase_status` |
| "Advance to next phase", "this phase is done" | `backlog_advance_phase` (refuses unfinished tasks or deliverables; `force=True` only when the user says so) |
| "Check TODOs", "scan for TODOs", "todo audit" | `taskmaster:check-todos` |

## Handovers and threads

| Intent Signal | Route To |
|---|---|
| "Show last handover", "where did I leave off", "resume my work" | `backlog_thread_list()` then `backlog_thread_resume(<name>)` |
| "List handovers", "recent handovers" | `backlog_handover_list` |
| "Show this handover in full", "read handover 2026-XX-XX" | `backlog_handover_get(<id>)` |
| "Supersede this handover", "the new handover replaces the old one" | `backlog_handover_supersede(old_id, new_id)` |
| "Close this handover", "reopen this handover", "I read that handover", "park this thread", "triage old handovers" | `taskmaster:handover` (entry points: close / reopen / triage / thread-update) |

## Decisions, Linear, project

| Intent Signal | Route To |
|---|---|
| "Choose between", "decide on X", "open question", "list open decisions", "resolve DEC-X", "drop DEC-X" | `taskmaster:decision` |
| "Set up linear sync", "connect to linear", "link to linear ENG-42", "unlink from linear", "linear status", "retry linear pushes" | `taskmaster:linear` |
| "Init project manifest", "show project", "what repos are in this project" | `backlog_project_init` / `backlog_project_get` |
| "Ship order", "which repo first", "dependency order" | `backlog_project_ship_order` |
| "Where do errors land", "error-trace ladder" | `backlog_project_error_trace_ladder` |

## Bugs, issues, ideas

| Intent Signal | Route To |
|---|---|
| "Log an issue", "this is an issue", "file an issue" | `taskmaster:issue` if evidence is cited, else `taskmaster:bug` (`../bug/references/bug-vs-issue.md`) |
| "Promote B-XX to an issue" | `taskmaster:bug` (promote flow), then `taskmaster:issue` |
| "Shelve this", "park this for later" | `taskmaster:bug` (disposition: shelved) |
| "List issues", "open bugs", "what's broken" | `backlog_issue_list(status="open")` |
| "Mark issue fixed", "close ISS-XX", "start investigating ISS-XX", "duplicate of ISS-YY", "won't fix ISS-XX" | `taskmaster:issue` (entry point `update-status`) |
| "Triage open bugs", "review issues by severity" | `taskmaster:issue` (entry point `triage-review`) |
| "List ideas", "show parking lot" | `backlog_idea_list` |
| "Archive that idea", "promote IDEA-NNN to a task" | `backlog_idea_update` (field/value) |

## Other

| Intent Signal | Route To |
|---|---|
| "auto this task", "autopilot", "auto-epic X" | Auto mode is gone; suggest ultracode (Workflow orchestration) |
