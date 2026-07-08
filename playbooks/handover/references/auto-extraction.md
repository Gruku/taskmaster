# Handover Auto-Extraction Sources

Six input sources, walked in order, deduplicated, grouped into three buckets.

## The six sources

1. **`git status` + `git diff --stat`** — uncommitted state, line-count deltas. Drives the "Pending commits" section in `full` tier.

2. **`git log {merge_base}..HEAD --name-only`** — files committed during this session, commit messages, SHAs. Use the branch's merge-base with the default branch (or `HEAD~N` for last N commits if no clear branch boundary). Drives the "What shipped this session" table in `standard` and `full` tiers.

3. **Conversation tool-call history** — Read / Edit / Write paths from this session. **The orchestrator passes this in** when invoking the skill — the skill itself does not scrape its own context. If the orchestrator can't pass it, sources 1, 2, 4, 5 still work; source 3 falls back to empty.

4. **Task body anchors** — `task.anchors`, `task.docs.spec`, `task.docs.plan`, `task.related_handovers`, `task.related_issues`. Pulled via `backlog_get_task(task_id)` for each task in `task_ids`. Drives the **Relevant** group.

5. **Conversation text regex** — paths matching the pattern `[a-zA-Z_][a-zA-Z0-9_/.-]*\.(md|py|js|css|html|yaml|json|ts|tsx|jsx)` mentioned anywhere in the conversation. Includes paths in code blocks and inline `code spans`.

6. **Conversation numbered-step regex** — lines matching `^\d+\.\s+` that read like next-session actions (verb-led: "Run X", "Read Y", "Continue with Z"). Drives the "What's next" section in `standard` and `full` tiers.

## Grouping into three buckets

After all six sources are collected, deduplicate paths and assign each one to exactly one of:

- **Touched** = (sources 1, 2, 3) ∩ written/edited
- **Read** = source 3 ∩ read-only (i.e., paths Claude `Read` but never `Edit`/`Write`)
- **Relevant** = (sources 4, 5) − (Touched ∪ Read)

A path that appears in source 5 but is also in source 3-edited goes to **Touched**, not **Relevant**. The grouping is mutually exclusive — every path lives in exactly one bucket.

## Annotation requirement

For every path written into the handover, Claude writes:

- A one-line **what changed** (Touched), **why we read it** (Read), or **what it is** (Relevant).
- A one-line **why next session needs it**.

A path with no annotation is worse than not including the path at all — it forces the next session to re-explore. **Never skip annotations.**

## What gets dropped

- Paths under `node_modules/`, `__pycache__/`, `.venv/`, `.git/`, `dist/`, `build/`, `.snapshots/`, `.taskmaster/auto/`.
- Paths the regex caught from prose that are obviously not real (e.g., `example.com`, `foo.bar.baz`).
- Paths the user said to ignore in this session.

When in doubt, keep the path and annotate why it might matter — over-inclusion is cheap, under-inclusion makes the next session re-explore.
