# Legacy lessons: convert to memory and instruction files

<!-- User intent: keep the one-time conversion of pre-4.x `.taskmaster/lessons/` files available after the migrate-lessons skill is retired; start-session links here when it finds them. -->

A project's legacy `.taskmaster/lessons/L-*.md` files (taskmaster 3.x and earlier) become entries in the two-tier memory model. Run it once per project, when the user wants the conversion.

1. Read every `.taskmaster/lessons/L-*.md` in the current project. When the directory is missing or empty, report "no lessons to migrate" and stop.
2. For each lesson, propose one destination:
   - **instruction-file**: knowledge that has to bind every assistant (conventions, invariants, "we got burned by X"). Draft a 1-3 line entry for the repo's CLAUDE.md / AGENTS.md.
   - **assistant-memory**: session-craft insight useful to you specifically. Draft a memory entry in your own memory system's format.
   - **drop**: stale, superseded, or already encoded elsewhere (check the instruction files before proposing it).
3. Present the full proposal as one table (lesson id, title, destination, draft text) and apply it on the user's approval: instruction-file entries with Edit, assistant-memory entries through your memory mechanism.
4. Leave `.taskmaster/lessons/` untouched on disk; the lesson files are the historical record, so don't delete them.
5. Report: N migrated to instructions, M to memory, K dropped.
