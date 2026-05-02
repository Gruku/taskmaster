<!--
FULL TIER TEMPLATE — target ~150–200 lines.
Used for pivot, audit, complex multi-pass, or sessions >100 turns / >200k tokens.
Drop any section with no content. Never leave {placeholders}.
-->

## Resume prompt

> {Verbatim text the next session can paste. cd, branch, where to start, hard rules. Be precise — this is the load-bearing field.}

## Where execution stands

{Branch, tip commit, last-landed commits, test counts, server state, environment state, blockers if any. 8–15 lines.}

## What shipped this session

| # | What | Where |
|---|------|-------|
| 1 | {accomplishment} | {commit hash / file / docs} |
| 2 | {accomplishment} | {commit hash / file / docs} |

## What's next

1. {Numbered next-session action.}
2. {Numbered next-session action.}
3. {Numbered next-session action.}

## Files of interest

| Group | Path | What | Why next session needs it |
|---|---|---|---|
| Touched | {path} | {what changed} | {why} |
| Read | {path} | (referenced) | {why} |
| Relevant | {path} | (not touched, relevant) | {why} |

## Important non-obvious things

1. {Numbered gotcha, hidden invariant, environment quirk.}
2. {Same.}

## Pending commits

{Bash commands ready to run, in code blocks. Auto-generated from `git status` if uncommitted state exists. Drop this section if working tree is clean.}

```bash
git add <files>
git commit -m "<message>"
```

## Per-task dispatch templates

{Verbatim subagent prompts the next session can use. Only emit when orchestration was the work (e.g., dispatching parallel subagents was the point of this session). Drop otherwise.}

### Dispatch template — {task_id}

```
{verbatim subagent prompt}
```
