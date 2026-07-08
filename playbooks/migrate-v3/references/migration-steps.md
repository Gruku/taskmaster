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

## Step 5: Verify Viewer Flipped to v3

1. Call viewer_prefs_get and parse the JSON response.
2. If use_v3 is true: tell user to hard-refresh any open viewer tab (Ctrl+Shift+R / Cmd+Shift+R).
3. If use_v3 is false or missing: the inner flip silently failed. Call viewer_prefs_set({use_v3: true}) explicitly. If it returns error, tell user the schema migrated cleanly but the viewer toggle needs manual fixing.

## Step 6: Canonicalize Layout (Legacy .claude/ Projects Only)

If .claude/backlog.yaml exists and .taskmaster/backlog.yaml does not, offer:

Options: Yes, canonicalize / Show plan first / Skip.
- Yes: call backlog_canonicalize_layout(). Surface response verbatim.
- Show plan first: call backlog_canonicalize_layout(dry_run=true). Re-ask whether to proceed.
- Skip: acknowledge and move on.

If .taskmaster/backlog.yaml already exists, skip this step entirely.

## Step 7: Seed the Recap Baseline

Call backlog_snapshot(). Surface its one-line response. No confirmation needed -- this is harmless and unblocks recap immediately.

## Step 8: Post-Flight Gitignore

Check whether .gitignore contains .taskmaster/snapshots/. If missing, ask the user (use your structured-question tool if available; otherwise present the options): Yes, add it / Skip.

If Yes: append this block to .gitignore (do not overwrite; check first that the line is not already present):



## Step 9: Tour the v3 Surfaces

Tell the user what they just unlocked:
- Handovers -- taskmaster:handover skill captures session continuity.
- Issues -- taskmaster:issue skill for bug tracking separate from work tasks.
- Recap -- backlog_recap shows what changed in the project since the last snapshot.

The PreCompact hook ships with this plugin and runs automatically before context compaction. No per-project setup required.

## When NOT to invoke

- Already on v3: backlog_status will report schema_version: 3. Step 1 detects this and stops.
- No backlog yet: redirect to taskmaster:init-taskmaster.
- User on v2 and content with current capabilities: do not push v3 unprompted.
