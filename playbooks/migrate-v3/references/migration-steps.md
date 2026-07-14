# Migrate V3 -- Full Migration Steps Detail

This file contains the full step-by-step migration prose. The SKILL.md carries only
the essential decision points and the ONLY sentence.

## Step 2: Pre-Flight Summary

Call backlog_list_tasks and backlog_status to gather counts. Present:

- Total tasks: N
- Active tasks (in-progress or in-review): N -- mid-flight; migration is safe, user should know
- Heavy fields moving out of backlog.yaml: description, notes, docs, review_instructions -- content moves to per-task files at .taskmaster/tasks/<task-id>.md
- If legacy .claude/-layout project, the migrator writes back into .claude/; consider running backlog_canonicalize_layout afterwards
- No data is lost. The in-memory shape is identical on v2 and v3
- Reversibility: the migration is idempotent -- if you want to roll back, git restore backlog.yaml and delete the tasks/ directory

## Step 5: Refresh the Viewer

The viewer serves the same UI at `/` for every schema — migration no longer touches viewer prefs. If a viewer tab is open, tell the user to hard-refresh it (Ctrl+Shift+R / Cmd+Shift+R) so the newly unlocked v3 surfaces (Issues, Sessions) appear.

## Step 6: Canonicalize Layout (Legacy .claude/ Projects Only)

If .claude/backlog.yaml exists and .taskmaster/backlog.yaml does not, offer:

Options: Yes, canonicalize / Show plan first / Skip.
- Yes: call backlog_canonicalize_layout(). Surface response verbatim.
- Show plan first: call backlog_canonicalize_layout(dry_run=true). Re-ask whether to proceed.
- Skip: acknowledge and move on.

If .taskmaster/backlog.yaml already exists, skip this step entirely.

## Step 7: Tour the v3 Surfaces

Tell the user what they just unlocked:
- Handovers -- taskmaster:handover skill captures session continuity.
- Issues -- taskmaster:issue skill for bug tracking separate from work tasks.

## When NOT to invoke

- Already on v3: backlog_status will report schema_version: 3. Step 1 detects this and stops.
- No backlog yet: redirect to taskmaster:init-taskmaster.
- User on v2 and content with current capabilities: do not push v3 unprompted.
