# Migrate Lessons

One-time conversion of a project's legacy `.taskmaster/lessons/L-*.md` files (taskmaster ≤3.x) into the two-tier memory model. Run once per project after upgrading to taskmaster 4.x.

## Steps

1. Read every `.taskmaster/lessons/L-*.md` in the current project. If the directory is missing or empty, report "no lessons to migrate" and stop.
2. For each lesson, propose exactly one destination:
   - **instruction-file** — knowledge that must bind all assistants (conventions, invariants, "we got burned by X"). Draft a 1-3 line entry for the repo's CLAUDE.md / AGENTS.md.
   - **assistant-memory** — session-craft insight useful to you specifically. Draft a memory entry in your own memory system's format.
   - **drop** — stale, superseded, or already encoded elsewhere (check instruction files before proposing).
3. Present the full proposal as one table (lesson id, title, destination, draft text) and apply on user approval — instruction-file entries via Edit, assistant-memory entries via your memory mechanism.
4. Leave `.taskmaster/lessons/` untouched on disk. Do not delete lesson files; they are the historical record.
5. Report: N migrated to instructions, M to memory, K dropped.
