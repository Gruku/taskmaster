# Handover Auto-Extraction

Gather the files of interest from what you already know and from git, then run the index reverse lookup (source 7).

## Sources 1-6

Use `git status` and `git diff --stat` for uncommitted work, and `git log <merge-base>..HEAD --name-only` for this session's commits (the merge-base with the default branch, or `HEAD~N` when there is no clear boundary). Add the files you edited and read in this conversation, and each task's `anchors`, `docs.spec`, `docs.plan`, related handovers and issues (`backlog_get_task`).

Put each path in one group:

- **Touched:** edited or committed this session.
- **Read:** read but not changed.
- **Relevant:** pointed to by the task or the conversation, not opened.

Skip build and vendor paths (`node_modules/`, `__pycache__/`, `.venv/`, `.git/`, `dist/`, `build/`, `.snapshots/`, `.taskmaster/auto/`), strings that only look like paths (`example.com`, `foo.bar.baz`), and paths the user said to ignore.

## Annotations

Give every path a line on what changed (Touched), why you read it (Read), or what it is (Relevant), and a line on why the next session needs it. A bare path forces the next session to re-explore. When unsure, keep the path with a note on why it might matter: an extra row is cheap, a missing one sends the next session exploring.

## Source 7

7. **Index reverse lookup** — for every Touched path, `backlog_query("SELECT DISTINCT e.id,e.kind,e.status,e.title FROM entity_paths p JOIN entities e ON e.id=p.entity_id WHERE p.source IN ('anchors','location') AND (p.path='<rel>' OR (p.match_kind='glob' AND '<rel>' GLOB p.path))")`. Paths are project-root relative with `/`. Before splicing `<rel>` into the query, escape single quotes by doubling them (`'` → `''`), and skip the path entirely if it holds any character outside `[A-Za-z0-9_./' -]`. Open results feed `task_ids` (tasks) and the fix question (bugs and issues). Skip silently when `backlog_query` reports a missing index.
