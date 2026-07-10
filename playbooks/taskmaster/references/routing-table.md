# Taskmaster Router — Full Routing Table

This file contains the complete intent→skill routing table for `taskmaster:taskmaster`.
The SKILL.md body carries only the ~15 highest-frequency rows.

## All Routes

| Intent Signal | Route To |
|---|---|
| Starting a new conversation, "what's going on", "orient me", "show the backlog", "let's get started" | `taskmaster:start-session` |
| "Pick task X", "let's work on X", "what should I tackle", names a task ID, "start task" | `taskmaster:pick-task` |
| Any implementation request when a task is in-progress | Work in current task's worktree — no routing needed |
| Any implementation request when NO task is in-progress | `taskmaster:pick-task` first |
| "Review this spec", "challenge this design", "is this the right approach?", "spec review" | `taskmaster:spec-review` |
| "Is this ready?", "run the review", "check my work", "I think this is done", "quality check" | `taskmaster:review-gate` |
| "End session", "I'm done", "wrap up", "log this", "mark task done", "save progress" | `taskmaster:end-session` |
| "Set up taskmaster", "initialize", "create backlog", first time in project without backlog.yaml | `taskmaster:init-taskmaster` |
| "Add a task", "create a task for X", "plan out this epic" | `backlog_add_task` / `backlog_add_epic` (requires `done_when` — a workstream that never finishes is an area, not an epic) |
| "Create an area", "define a subsystem", "what areas exist", "rename this area" | `backlog_area_create` / `backlog_area_list` / `backlog_area_get` / `backlog_area_update` |
| "Show task X", "task details", "what's the status of X" | `backlog_get_task` / `backlog_status` |
| "Search for X", "find tasks about X" | `backlog_search` |
| "Create a phase", "plan the next phase", "set up phases" | `backlog_add_phase` |
| "Show phase progress", "where are we in the phase?" | `backlog_phase_status` |
| "Advance to next phase", "this phase is done" | `backlog_advance_phase` |
| "Check TODOs", "scan for TODOs", "are my TODOs tracked", "todo audit" | `taskmaster:check-todos` |
| (v3) "Write a handover", "wrap up for tomorrow", "context handoff", "save where I left off", "for tomorrow", "remind future me", "before compaction" | `taskmaster:handover` |
| (v3) "Show last handover", "where did I leave off" | `backlog_handover_list(status="open", limit=1)` |
| (v3) "List handovers", "recent handovers" | `backlog_handover_list` |
| (v3) "Show this handover in full", "read handover 2026-XX-XX" | `backlog_handover_get <id>` |
| (v3) "Supersede this handover", "the new handover replaces the old one" | `backlog_handover_supersede(old_id, new_id)` |
| (v3) "Mark handover done", "mark handover todo", "I read that handover", "triage old handovers" | `taskmaster:handover` (manual status entry points: mark-done / mark-in-progress / mark-todo / triage) |
| (v3) "Choose between", "pick an option", "decide on X", "open question", "branching path", "list open decisions", "resolve DEC-X", "drop DEC-X" | `taskmaster:decision` |
| (v3) "Set up linear sync", "connect to linear", "link to linear ENG-42", "unlink from linear", "linear status", "retry linear pushes", "list linear trackers" | `taskmaster:linear` |
| (v3) "Init project manifest", "scaffold project.yaml", "show project", "what repos are in this project" | `backlog_project_init` / `backlog_project_get` |
| (v3) "Ship order", "which repo first", "dependency order" | `backlog_project_ship_order` |
| (v3) "Where do errors land", "error-trace ladder", "how do I trace this exception" | `backlog_project_error_trace_ladder` |
| (v3) "Log an issue", "this is an issue", "file an issue" | Word-agnostic intake — `taskmaster:issue` if evidence cited, else `taskmaster:bug` |
| (v3) "Log a bug", "this is a bug", "track this defect", "I found a bug" | `taskmaster:bug` |
| (v3) "Promote B-XX to an issue" | `taskmaster:bug` (promote subflow) → `taskmaster:issue` (creation) |
| (v3) "Shelve this", "park this for later" | `taskmaster:bug` (disposition: shelved) |
| (v3) "List issues", "open bugs", "what's broken" | `backlog_issue_list(status=open)` |
| (v3) "Mark issue fixed", "close ISS-XX" | `taskmaster:issue` (entry point `update-status`) |
| (v3) "Start investigating ISS-XX", "this is a duplicate of ISS-YY", "won't fix ISS-XX" | `taskmaster:issue` (entry point `update-status`) |
| (v3) "Triage open bugs", "review issues by severity" | `taskmaster:issue` (entry point `triage-review`) |
| (v3) "Save this as an idea", "remember this idea", "/add-idea ..." | `taskmaster:add-idea` |
| (v3) "List ideas", "show parking lot" | `backlog_idea_list` |
| (v3) "Archive that idea", "promote IDEA-NNN to a task" | `backlog_idea_update` |
| (v3) "auto this task", "autopilot", "auto-epic X", "auto T-001" | Redirect: auto mode removed — suggest **ultracode** (Workflow orchestration) |
| (v3) "Upgrade to v3", "migrate to v3", "switch to v3", "enable handovers" | `taskmaster:migrate-v3` |
| (v3) "Migrate lessons", "convert lessons to memory", "what happened to lessons" | `taskmaster:migrate-lessons` |

## Implementation Work Without a Task

If the user asks to implement something and there's no task for it yet:

1. Call `backlog_status` to check what's in-progress.
2. If the work fits an existing task, pick that task.
3. If not, suggest creating a new task: "This looks like new work. Want me to add a task for it under epic X?"
4. Once a task is picked, work proceeds in its worktree.

This ensures nothing falls through the cracks.
