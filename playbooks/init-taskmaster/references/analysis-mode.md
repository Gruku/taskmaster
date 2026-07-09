# Init Taskmaster -- Analysis Mode Detail

## Step 3b: Analyze Project (Full Flow)

If the user chose analyze:

1. Call backlog_init(project_name) first to create the files.
2. Scan for existing work items:
   - Search for TODO, FIXME, HACK, XXX comments across the codebase using Grep
   - Read README.md for any roadmap, planned features, or task lists
   - Check for existing issue trackers: TODO.md, ROADMAP.md, .github/ISSUES_TEMPLATE
   - Look at recent git log for active areas of work: git log --oneline -20
3. Synthesize findings into a proposed backlog:
   - Group related TODOs into candidate areas first (by directory/domain) — these become `backlog_area_create` calls for the long-lived subsystems, not epics
   - Within each area, propose finite epics that carry a `done_when` (a real completion condition, not "ongoing work on X")
   - Convert individual TODOs into tasks with priorities:
     - FIXME -> high (should fix)
     - HACK -> medium (tech debt)
     - TODO -> medium (planned work)
     - XXX -> high (needs attention)
   - Extract roadmap items from README as tasks
4. Present the proposed backlog to the user with text summary then ask for approval
   (use your structured-question tool if available; otherwise list the options).
   Options: Create it / Adjust first / Cancel.
   On Create it: use backlog_area_create, backlog_add_epic (with `done_when`), backlog_add_task, backlog_add_phase. Include source file:line in task notes.

## Edge Cases

- Huge codebase with hundreds of TODOs: group into areas by directory/domain, then create finite epics (with `done_when`) for the top areas' near-term goals.
- No TODOs found: proceed like a clean start.
- Monorepo: project root should be the top-level directory. Tasks can reference sub-repos via the sub_repo field.

## v3 Upgrade Later

If the user picked v2 now and wants to upgrade later, they can run taskmaster:migrate-v3 at any point.
The migration is idempotent and preserves all existing data.

## Post-v3 Setup Tour

If v3 was chosen, tell the user what they just unlocked:
- Handovers -- taskmaster:handover skill captures session continuity.
- Issues -- taskmaster:issue skill for bug tracking separate from work tasks.
- Recap -- backlog_recap shows what changed in the project since the last snapshot.

The PreCompact hook ships with this plugin and runs automatically before context compaction.
No per-project setup required.
