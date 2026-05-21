---
name: init-taskmaster
description: "Set up Taskmaster in the current project. Invoke when the user wants task/backlog tracking, says 'set up taskmaster', 'initialize backlog', 'I want to track my work here', or when backlog.yaml does not exist. Offers clean init or analysis of existing TODOs/structure to pre-populate the backlog. Also offers to scaffold the project manifest (.taskmaster/project.yaml) on v3 setups."
---

# Initialize Taskmaster

Set up AI-powered task management in the current project. Offers two modes: clean start or analyze existing work.

> **CRITICAL: Never write `backlog.yaml` directly.** All backlog mutations MUST go through `backlog_init`, `backlog_add_epic`, `backlog_add_task`, `backlog_add_phase`. The schema is owned by the server.

## Step 1: Check if already initialized

Call `backlog_init` with no arguments. If it reports "already initialized", call `backlog_status` and show the user what's there. Stop.

## Step 2: Ask setup questions — MANDATORY, do NOT skip

Use `AskUserQuestion` with both questions in a single call. Do NOT call `backlog_init` until you have the answers.

```
AskUserQuestion({
  questions: [
    {
      question: "Which schema version?",
      header: "Schema",
      multiSelect: false,
      options: [
        { label: "v2 (Default — stable)", description: "Single backlog.yaml file. Simple, proven, all existing tools work." },
        { label: "v3 (Narrative continuity — opt-in)", description: "Slim index + per-task files. Adds handovers, lessons, issues, recap, auto-mode." }
      ]
    },
    {
      question: "How do you want to start?",
      header: "Init mode",
      multiSelect: false,
      options: [
        { label: "Analyze project (Recommended)", description: "Scan for TODOs, FIXMEs, README plans, and existing structure to suggest an initial backlog" },
        { label: "Clean start", description: "Empty backlog — you'll add epics and tasks as you go" }
      ]
    }
  ]
})
```

Map: v3 -> after `backlog_init`, call `backlog_migrate_v3`. If v3, gitignore `.taskmaster/snapshots/` and `.taskmaster/auto/`.

## Step 2b: Offer project manifest (v3 only)

After `backlog_init` succeeds on a v3 setup, ask whether to also scaffold the Project manifest at `.taskmaster/project.yaml` — the structured truth about repos, submodules, branch protocol, stacks, deploy targets, and error-trace ladder. Pairs with `backlog.yaml` (work in flight).

```
AskUserQuestion({
  questions: [{
    question: "Scaffold a project manifest now?",
    header: "Project manifest",
    multiSelect: false,
    options: [
      { label: "Yes (Recommended)", description: "Creates minimal .taskmaster/project.yaml. Powers ship_order, error_trace_ladder, repo/branch awareness in skills." },
      { label: "Skip", description: "Can be added later with backlog_project_init." }
    ]
  }]
})
```

On "Yes": call `backlog_project_init` (no args — it writes a minimal valid manifest, refuses to overwrite). Then point the user at it: "Edit `.taskmaster/project.yaml` to declare your repos, submodules, branch protocol, and error-trace ladder. Schema reference: `plugins/taskmaster/project.py`."

Skip this step entirely on v2 — project.yaml is a v3 surface.

## Step 3a: Clean start

1. `backlog_init(project_name)`.
2. Guide them to create their first epic ("What are the main workstreams?").
3. Help add tasks under those epics.
4. Suggest creating a phase.
5. Budget guidance: "Aim for 5-8 tasks per epic. If a task has many steps, create a plan doc and link it with `docs.plan`."

## Step 3b: Analyze project

Full analysis-mode flow (scan TODOs/README/git log, synthesize into proposed backlog, present with AskUserQuestion for approval, create via MCP tools) in `references/analysis-mode.md`.

## Step 4: Open the viewer and confirm

1. `backlog_open_viewer` to open the dashboard.
2. "Taskmaster is set up! Use `/start-session` to see your dashboard."
3. If MCP tools stop responding, run `/mcp` to reconnect.
