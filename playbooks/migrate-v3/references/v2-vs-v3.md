# v2 vs v3 Schema Reference

Reference for understanding what the v2 → v3 migration changes on disk. See `../SKILL.md` for the migration flow.

---

## Heavy fields that move

These fields are stripped from the task entries in `backlog.yaml` and written into per-task files at `tasks/<task-id>.md` (under `.taskmaster/`, or `.claude/` for legacy-layout projects). Any task that has one or more of these fields populated gets a file written; tasks with none of these fields populated are unaffected.

| Field | v2 location | v3 location |
|---|---|---|
| `description` | `backlog.yaml` → epic → task | `tasks/<id>.md` (frontmatter) |
| `notes` | `backlog.yaml` → epic → task | `tasks/<id>.md` (frontmatter) |
| `docs` | `backlog.yaml` → epic → task | `tasks/<id>.md` (frontmatter) |
| `review_instructions` | `backlog.yaml` → epic → task | `tasks/<id>.md` (frontmatter) |

The task body (free-form markdown below frontmatter) also lives in `tasks/<id>.md`. On v2 there is no task body — v3 adds it.

---

## What stays in backlog.yaml

The slim index — the fields that drive the kanban, dependencies, and status queries:

- `id`, `title`, `status`, `priority`
- `epic`, `phase`
- `depends_on`, `blocked_by`
- `started`, `completed`
- `estimate`, `stage`
- `sub_repo`
- `related_issues` (task-level link to ISS-NNN)

`id` and `title` are also mirrored into the per-task file's frontmatter for human readability, but `backlog.yaml` is the authoritative source for those fields.

---

## New top-level lists in backlog.yaml on v3

Two new top-level sections appear in `backlog.yaml` after migration. Each is a slim index; full content lives in the corresponding subdirectory.

| Section | What it holds |
|---|---|
| `handovers` | Last 30 handover entries: `id`, `date`, `tldr`, `next_action`, `task_ids` |
| `issues` | All issues: `id`, `title`, `severity`, `status`, `components`, `related_tasks` |

---

## New on-disk subdirectories

All paths relative to the backlog directory (`.taskmaster/`; `.claude/` for legacy-layout projects):

| Directory | Contents | Gitignored? |
|---|---|---|
| `tasks/` | `<id>.md` per task — heavy fields + body | No |
| `handovers/` | `YYYY-MM-DD-<slug>.md` per session handover | No |
| `issues/` | `ISS-<NNN>.md` per issue | No |

---

## What stays the same

The in-memory shape of a task is identical on v2 and v3 — heavy fields are merged back in when loaded. All existing MCP tools and skills work without modification. The `schema_version` field in `backlog.yaml` is the only indicator of which layout is in use.

---

## Roll-back

The migration is idempotent — running `backlog_migrate_v3` again on an already-v3 backlog is a no-op.

To roll back to v2:

1. `git restore .taskmaster/backlog.yaml` (or `.claude/backlog.yaml` for legacy-layout projects) — restores the v2 single-file backup
2. Delete the new `tasks/` directory: `rm -rf .taskmaster/tasks/` (or `.claude/tasks/` for legacy)

Heavy-field content written to per-task files during migration is not present in the v2 `backlog.yaml`; the git-restore step recovers it from the pre-migration commit. If no git history exists, the v2 backup is not available and roll-back is not possible — this is the main reason to commit before migrating.
