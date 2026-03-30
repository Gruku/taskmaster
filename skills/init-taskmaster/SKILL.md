---
name: init-taskmaster
description: "Set up Taskmaster in the current project. Invoke when the user wants task/backlog tracking, says 'set up taskmaster', 'initialize backlog', 'I want to track my work here', or when backlog.yaml does not exist. Offers clean init or analysis of existing TODOs/structure to pre-populate the backlog."
---

# Initialize Taskmaster

Set up AI-powered task management in the current project. Offers two modes: clean start or analyze existing work.

> **CRITICAL: Never write `backlog.yaml` directly.** All backlog mutations MUST go through the `backlog_*` MCP tools (`backlog_init`, `backlog_add_epic`, `backlog_add_task`, `backlog_add_phase`). The schema is owned by the server — writing the file manually will produce an incompatible format that breaks the viewer and all other tools.

## Step 1: Check if already initialized

Call `backlog_init` with no arguments — if it reports "already initialized", call `backlog_status` instead and show the user what's there. Skip the rest of these steps.

## Step 2: Ask setup questions — MANDATORY, do NOT skip

Use the `AskUserQuestion` tool with both questions in a single call. Do NOT call `backlog_init` or create any files until you have the answers.

```
AskUserQuestion({
  questions: [
    {
      question: "Where should I store the backlog?",
      header: "Location",
      multiSelect: false,
      options: [
        { label: "Hidden (Recommended)", description: ".claude/ directory — stays out of your repo, good for personal tracking" },
        { label: "Tracked", description: ".taskmaster/ directory — visible files you can commit to git, good for team visibility" }
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

Map the answers:
- Location: "Hidden" → `location="hidden"`, "Tracked" → `location="tracked"`
- Init mode: "Analyze project" → Step 3b, "Clean start" → Step 3a

## Step 3a: Clean start

If the user chose clean start:

1. Call `backlog_init(project_name, location)` with their chosen location.
2. Guide them to create their first epic: "What are the main workstreams? For example: `auth-system`, `api`, `frontend`"
3. Help them add tasks under those epics.
4. Suggest creating a phase to organize the first batch of work.
5. **Budget guidance:** Remind the user: "Aim for 5-8 tasks per epic. Tasks should be things you'd pick up in different sessions. If a task has many steps, create a plan document and link it with `docs.plan` instead of splitting into micro-tasks."

## Step 3b: Analyze project

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

   First, output your findings as text:

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
   > **Proposed Phase:** "Cleanup & Foundation" — group the FIXMEs and HACKs

   Then use `AskUserQuestion` to get explicit approval:

   ```
   AskUserQuestion({
     questions: [
       {
         question: "Should I create this backlog?",
         header: "Approval",
         multiSelect: false,
         options: [
           { label: "Create it", description: "Add the proposed epics, tasks, and phase to the backlog" },
           { label: "Adjust first", description: "I want to change some things before you create it" },
           { label: "Cancel", description: "Don't create anything, I'll set it up manually" }
         ]
       }
     ]
   })
   ```

   - "Create it" → proceed to step 5
   - "Adjust first" → ask what they want to change, update the proposal, then re-ask
   - "Cancel" → stop, tell them they can run `/init-taskmaster` again later

5. **After user approval**, create the epics, tasks, and phase using the MCP tools (do NOT write the YAML file directly — the server owns the schema):
   - `backlog_add_epic` for each epic
   - `backlog_add_task` for each task (include the source file:line in notes)
   - `backlog_add_phase` for the initial phase
   - Assign tasks to the phase

## Step 4: Open the viewer and confirm

1. Call `backlog_open_viewer` to open the backlog dashboard in the browser.
2. Show the result: "Taskmaster is set up! Use `/start-session` to see your dashboard."
3. **Tell the user:** if MCP tools stop responding or return connection errors in a future session, run `/mcp` to reconnect to the Taskmaster server.

## Edge Cases

- **Monorepo** — The project root should be the top-level directory. Tasks can reference sub-repos via the `sub_repo` field.
- **Huge codebase with hundreds of TODOs** — Don't create a task for every TODO. Group them by area, create epics for the top areas, and add representative tasks. Mention "N more TODOs in this area" in the notes.
- **No TODOs found** — That's fine! Say "Clean codebase! Let's set up the backlog based on what you're planning to work on." and proceed like a clean start.
