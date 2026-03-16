---
name: init-taskmaster
description: "Set up Taskmaster in the current project. Invoke when the user wants task/backlog tracking, says 'set up taskmaster', 'initialize backlog', 'I want to track my work here', or when backlog.yaml does not exist. Offers clean init or analysis of existing TODOs/structure to pre-populate the backlog."
---

# Initialize Taskmaster

Set up AI-powered task management in the current project. Offers two modes: clean start or analyze existing work.

## Step 1: Check if already initialized

Call `backlog_init` with no arguments — if it reports "already initialized", call `backlog_status` instead and show the user what's there. Skip the rest of these steps.

## Step 2: Choose storage location

Ask the user where they want the backlog stored:

> **Where should I store the backlog?**
>
> 1. **Hidden** (`.claude/` directory) — won't show up in your repo, stays out of the way. Good for personal tracking.
> 2. **Tracked** (project root) — visible files you can commit to git. Good for team visibility and shared progress tracking.
>
> Default: hidden

Map their choice to the `location` parameter: "hidden" or "tracked".

## Step 3: Choose init mode

Ask the user how they want to start:

> **How do you want to set up the backlog?**
>
> 1. **Clean start** — empty backlog, you'll add epics and tasks as you go
> 2. **Analyze project** — I'll scan the codebase for TODOs, FIXMEs, README plans, and existing structure to suggest an initial backlog

## Step 4a: Clean start

If the user chose clean start:

1. Call `backlog_init(project_name, location)` with their chosen location.
2. Guide them to create their first epic: "What are the main workstreams? For example: `auth-system`, `api`, `frontend`"
3. Help them add tasks under those epics.
4. Suggest creating a milestone to organize the first batch of work.

## Step 4b: Analyze project

If the user chose analyze:

1. Call `backlog_init(project_name, location)` first to create the files.
2. **Scan for existing work items:**
   - Search for `TODO`, `FIXME`, `HACK`, `XXX` comments across the codebase using Grep
   - Read `README.md` for any roadmap, planned features, or task lists
   - Check for existing issue trackers: `.github/ISSUES_TEMPLATE`, `TODO.md`, `ROADMAP.md`
   - Look at recent git log for active areas of work: `git log --oneline -20`
   - Check for any existing project management files (Jira exports, Linear exports, etc.)

3. **Synthesize findings into a proposed backlog:**
   - Group related TODOs into epics (by directory/domain)
   - Convert individual TODOs into tasks with appropriate priorities:
     - `FIXME` → P1 (should fix)
     - `HACK` → P2 (tech debt)
     - `TODO` → P2 (planned work)
     - `XXX` → P1 (needs attention)
   - Extract roadmap items from README as tasks
   - Identify the most active code areas from git log as likely epics

4. **Present the proposed backlog to the user:**

   > **Here's what I found:**
   >
   > **Proposed Epics:**
   > - `api` — 5 TODOs found in `src/api/`
   > - `auth` — 3 FIXMEs in `src/auth/`
   > - `frontend` — README mentions "planned dashboard feature"
   >
   > **Proposed Tasks:** (12 total)
   > - [list each with source: "TODO in src/api/routes.ts:45"]
   >
   > **Proposed Milestone:** "Cleanup & Foundation" — group the FIXMEs and HACKs
   >
   > Want me to create this backlog, or do you want to adjust it first?

5. **After user approval**, create the epics, tasks, and milestone using the MCP tools:
   - `backlog_add_epic` for each epic
   - `backlog_add_task` for each task (include the source file:line in notes)
   - `backlog_add_milestone` for the initial milestone
   - Assign tasks to the milestone

## Step 5: Confirm

Show the result: "Taskmaster is set up! Use `/start-session` to see your dashboard."

## Edge Cases

- **Monorepo** — The project root should be the top-level directory. Tasks can reference sub-repos via the `sub_repo` field.
- **Huge codebase with hundreds of TODOs** — Don't create a task for every TODO. Group them by area, create epics for the top areas, and add representative tasks. Mention "N more TODOs in this area" in the notes.
- **No TODOs found** — That's fine! Say "Clean codebase! Let's set up the backlog based on what you're planning to work on." and proceed like a clean start.
