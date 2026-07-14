# Initialize Taskmaster

Set up AI-powered task management in the current project. Offers two modes: clean start or analyze existing work.

> **CRITICAL: Never write `backlog.yaml` directly.** All backlog mutations MUST go through `backlog_init`, `backlog_add_epic`, `backlog_add_task`, `backlog_add_phase`. The schema is owned by the server.

## Step 1: Check if already initialized

Call `backlog_init` with no arguments. If it reports "already initialized", call `backlog_status` and show the user what's there. Stop.

## Step 2: Ask how to start — MANDATORY, do NOT skip

New projects always use the current schema (v4 — sharded per-task storage). Do not present schema as a choice; `backlog_init` uses the server default. Ask only how to seed the backlog:

- "How do you want to start?" — options:
  - "Analyze project (Recommended)": Scan for TODOs, FIXMEs, README plans, and existing structure to suggest an initial backlog.
  - "Clean start": Empty backlog — you'll add epics and tasks as you go.

Do NOT call `backlog_init` until you have the answer.

<!-- cc-only:start -->
On Claude Code, use `AskUserQuestion`:

```
AskUserQuestion({
  questions: [
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
<!-- cc-only:end -->

## Step 2b: Offer project manifest

After `backlog_init` succeeds, ask whether to also scaffold the Project manifest at `.taskmaster/project.yaml` — the structured truth about repos, submodules, branch protocol, stacks, deploy targets, and error-trace ladder. Pairs with `backlog.yaml` (work in flight).

Ask the user (use your structured-question tool if available; otherwise present the options):

- "Scaffold a project manifest now?" — options:
  - "Yes (Recommended)": Creates minimal .taskmaster/project.yaml. Powers ship_order, error_trace_ladder, repo/branch awareness in skills.
  - "Skip": Can be added later with backlog_project_init.

<!-- cc-only:start -->
On Claude Code:

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
<!-- cc-only:end -->

On "Yes": call `backlog_project_init` (no args — it writes a minimal valid manifest, refuses to overwrite). Then point the user at it: "Edit `.taskmaster/project.yaml` to declare your repos, submodules, branch protocol, and error-trace ladder. Schema reference: `taskmaster/project.py` (plugin root)."

## Step 3a: Clean start

1. `backlog_init(project_name)`.
2. Suggest 3-6 areas first (`backlog_area_create`) for long-lived subsystems (e.g. "backend", "viewer") — areas never close.
3. Create their first epic ("What's the first finite goal?"). Epics require `done_when`; if it never finishes, it's an area.
4. Add tasks under those epics, tagging each with `area` where relevant.
5. Suggest creating a phase.
6. Budget guidance: "Aim for 5-8 tasks per epic. Link long plans with `docs.plan`."

## Step 3b: Analyze project

Full analysis-mode flow (scan TODOs/README/git log, synthesize into proposed backlog, present for approval, create via MCP tools) in `references/analysis-mode.md`.

## Step 4: Open the viewer and confirm

1. `backlog_open_viewer` to open the dashboard.
2. "Taskmaster is set up! Use `/start-session` to see your dashboard."
3. If MCP tools stop responding, run `/mcp` to reconnect.
