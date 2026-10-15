# Initialize Taskmaster

Set Taskmaster up so the project ends with one store at its repository root and an empty or seeded backlog, without setup chores for the user. The SQLite store (`.taskmaster/local/store.db`) is the authority and the other files under `.taskmaster/` are its exported projection, so write only through the `backlog_*` tools: hand edits to `backlog.yaml` or task files are overwritten or quarantined.

## Check where the server points

The server takes its project root from the directory it was started in (or `TASKMASTER_ROOT`), and the first write creates the store there. Call `backlog_store_status` first; it is read-only and never creates anything. (`backlog_init` is not a probe: it creates the backlog.)

- `no backlog found at <dir>/backlog.yaml`: `<dir>` must be the repository's top level in the main checkout (the first entry of `git worktree list`), not a subdirectory or a linked worktree. If it isn't, stop and ask the user to restart the session from that root or set `TASKMASTER_ROOT` to it, because initializing here puts the backlog in the wrong place, and in a linked worktree the files land in the worktree while the store lands in the main checkout. Outside Git, `<dir>` must be the directory the user means as the project.
- A `Store:` / `Root:` report: Taskmaster is already set up. Show `backlog_status` and stop.
- A legacy backlog, which gets adopted rather than initialized: `Error: backlog lives at <dir>; ... Run backlog_canonicalize_layout first` (a pre-6.0 backlog in `.claude/` or the project root), or a report whose `Warning:` says `no store yet` (a `.taskmaster/backlog.yaml` from before the store). Hand off to `taskmaster:migrate-v3`, which shows the user what adoption rewrites and asks first. Don't call `backlog_init` (it only answers "Already initialized") or other tools first, since the first read already adopts the files.

## Ask once, then create

Ask before writing anything (use your structured-question tool if you have one):

- How to start: **Analyze project** (recommended; proposes a backlog from TODOs, README plans and recent history) or **Clean start** (empty backlog).
- Whether to scaffold `.taskmaster/project.yaml`, the manifest of repos, submodules, branch protection, stacks, deploy targets and error-trace ladder that `backlog_project_ship_order` and the merge-gate hook read (recommended), or skip it and add it later with `backlog_project_init`.

<!-- cc-only:start -->
```
AskUserQuestion({ questions: [
  { question: "How do you want to start?", header: "Init mode", multiSelect: false, options: [
    { label: "Analyze project (Recommended)", description: "Scan TODOs, README plans and recent history to propose a backlog" },
    { label: "Clean start", description: "Empty backlog; add epics and tasks as you go" } ] },
  { question: "Scaffold a project manifest now?", header: "Manifest", multiSelect: false, options: [
    { label: "Yes (Recommended)", description: "Minimal .taskmaster/project.yaml; powers ship order, error-trace ladder, repo/branch awareness" },
    { label: "Skip", description: "Add later with backlog_project_init" } ] } ] })
```
<!-- cc-only:end -->

Call `backlog_init(project_name)` with only the name; the defaults are the current layout. If the manifest was wanted, call `backlog_project_init(name)` (it refuses to overwrite); the user fills in repos and branches later.

- **Clean start:** suggest 3-6 areas for long-lived subsystems, `backlog_area_create(area_id, name)` (areas never close); then the first finite epic, `backlog_add_epic(epic_id, name, done_when, area=...)` (anything that never finishes is an area); then a phase, `backlog_add_phase(phase_id, name)` (the first one becomes active); then its tasks, `backlog_add_task(title, epic, phase, tldr=..., options={"area": ...})` with the finish condition in `tldr`. Create in that order, because each call names the ones before it. Aim for 5-8 tasks per epic; link a long plan with `options={"docs": "plan:<path>"}`.
- **Analyze:** follow `references/analysis-mode.md`. Propose the whole backlog and create it only after the user approves.

## Finish

Report in a few lines what was created and what it means for Git: `.taskmaster/` is meant to be committed (`backlog.yaml`, `taskmaster.json`, `project.yaml`, `areas/`, `tasks/`), while `.taskmaster/local/` (the store, `PROGRESS.md`, caches) is machine-local and carries its own `.gitignore`. Committing is the user's call, so leave it to them. Then point to `taskmaster:start-session` for the first session, `backlog_open_viewer` for the board, and `taskmaster:linear` if they track work in Linear.
