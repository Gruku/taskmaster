# Initialize Taskmaster

Set up task tracking in this project. Write the backlog only through the `backlog_*` tools (`backlog_init`, `backlog_area_create`, `backlog_add_epic`, `backlog_add_task`, `backlog_add_phase`) and never write `backlog.yaml` directly, because the store owns those files and hand edits are overwritten or quarantined.

Check first with `backlog_status`. If a backlog already exists, show its summary and stop. (Don't probe with `backlog_init`: it creates the backlog, named after the directory.)

Ask once, before writing anything (use your structured-question tool if you have one):

- How to start: **Analyze project** (recommended; proposes a backlog from TODOs, README plans and recent history) or **Clean start** (empty backlog).
- Whether to scaffold `.taskmaster/project.yaml`, the manifest of repos, submodules, branch protocol, stacks, deploy targets and error-trace ladder that powers ship_order and repo-aware skills (recommended), or skip it and add it later with `backlog_project_init`.

<!-- cc-only:start -->
```
AskUserQuestion({ questions: [
  { question: "How do you want to start?", header: "Init mode", multiSelect: false, options: [
    { label: "Analyze project (Recommended)", description: "Scan TODOs, README plans and recent history to propose a backlog" },
    { label: "Clean start", description: "Empty backlog; add epics and tasks as you go" } ] },
  { question: "Scaffold a project manifest now?", header: "Manifest", multiSelect: false, options: [
    { label: "Yes (Recommended)", description: "Minimal .taskmaster/project.yaml; powers ship_order, error_trace_ladder, repo/branch awareness" },
    { label: "Skip", description: "Add later with backlog_project_init" } ] } ] })
```
<!-- cc-only:end -->

Then call `backlog_init(project_name)`; the schema is the server default, so don't pass one. If the manifest was wanted, call `backlog_project_init(name)` with the project name (it refuses to overwrite) and point the user at `.taskmaster/project.yaml` (schema: `taskmaster/project.py` in the plugin root).

- **Clean start:** suggest 3-6 areas for long-lived subsystems (`backlog_area_create`; areas never close), then the first finite epic (`backlog_add_epic` needs `done_when`; anything that never finishes is an area), then a phase (`backlog_add_phase`), then its tasks (`backlog_add_task` needs `title`, `epic` and `phase`; put the finish condition in `tldr` and the area in `options={"area": ...}`). Aim for 5-8 tasks per epic; link long plans with `docs.plan`.
- **Analyze:** follow `references/analysis-mode.md`. Propose the whole backlog and create it only after the user approves.

Finish with `backlog_open_viewer` and point the user to `taskmaster:start-session`.
