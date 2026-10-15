# Initialize Taskmaster

Set Taskmaster up, with no chores for the user, so the project ends with one native store at its repository root and a seeded or empty backlog. The database (`.taskmaster/local/store.db`) is the authority; a coordinator the tools start on demand owns its writes and exports the other `.taskmaster/` files. Write only through the `backlog_*` tools: hand edits to `backlog.yaml` or task files are read only at `backlog_sync()`.

## Check where the server points

The server takes its project root from the directory it was started in (or `TASKMASTER_ROOT`), and the first write creates the store there. Call `backlog_store_status` first; it is read-only and never creates anything. (`backlog_init` is not a probe: it creates the backlog and the store.)

- `no backlog found at <dir>/backlog.yaml`: `<dir>` must be the repository's top level in the main checkout (the first entry of `git worktree list`), not a subdirectory or a linked worktree. If it isn't, stop and ask the user to restart the session from that root or set `TASKMASTER_ROOT` to it, because initializing here puts the backlog in the wrong place. Outside Git, `<dir>` must be the directory the user means as the project.
- A `Store:` / `Root:` report: Taskmaster is already set up. Show `backlog_status` and stop.
- A legacy backlog, which gets adopted rather than initialized: `Error: backlog lives at <dir>; ... Run backlog_canonicalize_layout first` (a pre-6.0 backlog in `.claude/` or the project root), or a report whose `Warning:` says `no store yet` (a `.taskmaster/backlog.yaml` from before the store). Hand off to `taskmaster:migrate-v3`, which shows the user what adoption rewrites and asks first. Call nothing else first, since the first read already adopts the files.

## Ask once, then create

Ask before writing anything (use your structured-question tool if you have one):

- How to start: **Analyze project** (recommended; proposes a backlog from TODOs, README plans and recent history) or **Clean start** (empty backlog).
- Whether to scaffold `.taskmaster/project.yaml` (recommended), the manifest of repos, branches, stacks, deploy targets and error-trace ladder that `backlog_project_ship_order` and the merge-gate hook read; `backlog_project_init` can add it later.

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

Call `backlog_init(project_name)` with only the name; it creates the files and a native store. A `Note:` in its reply says why it fell back to the legacy store; pass it on. Pass `legacy_store=true` only on the user's request, for a host that cannot run the coordinator. If the manifest was wanted, call `backlog_project_init(name)` (it refuses to overwrite); the user fills in repos and branches later.

- **Clean start:** suggest 3-6 areas for long-lived subsystems, `backlog_area_create(area_id, name)` (areas never close); then the first finite epic, `backlog_add_epic(epic_id, name, done_when, area=...)` (anything that never finishes is an area); then a phase, `backlog_add_phase(phase_id, name)` (the first one becomes active); then its tasks, `backlog_add_task(title, epic, phase, tldr=..., options={"area": ...})` with the finish condition in `tldr`. Create in that order, because each call names the ones before it. Aim for 5-8 tasks per epic; link a long plan with `options={"docs": "plan:<path>"}`.
- **Analyze:** follow `references/analysis-mode.md`. Propose the whole backlog and create it only after the user approves.

## Finish

Report in a few lines what was created and what it means for Git: `backlog.yaml`, `project.yaml`, `areas/` and `tasks/` are committed with the managed Git command, not `git add` (`../end-session/references/edge-cases.md`, *Native store*); `taskmaster.json`, which it leaves out, with plain Git. `.taskmaster/local/` (store, `PROGRESS.md`, caches, coordinator files) is machine-local with its own `.gitignore`. Committing is the user's call, so leave it to them. Then point to `taskmaster:start-session` for the first session, `backlog_open_viewer` for the board, and `taskmaster:linear` if they track work in Linear.
