<!--
STANDARD TIER TEMPLATE — target ~60–130 lines.
Default for milestone-complete / context-handoff, or 30–100-turn sessions with an in-flight task.
Drop any section with no content. Never leave {placeholders}.
-->

## Resume prompt

> {Verbatim text the next session can paste. Include: cd command if a worktree, branch name, where to start (which file/section/task), and any hard rules ("don't push", "tests before commit").}

## Where execution stands

{Status snapshot: branch name, tip commit hash and message, what just landed, test counts (X/Y passing), server state if relevant. 4–10 lines.}

## What shipped this session

| # | What | Where |
|---|------|-------|
| 1 | {accomplishment} | {commit hash / file path / docs path} |
| 2 | {accomplishment} | {commit hash / file path / docs path} |

## What's next

1. {Numbered next-session action — verb-led, specific.}
2. {Numbered next-session action.}
3. {Numbered next-session action.}

## Files of interest

| Group | Path | What | Why next session needs it |
|---|---|---|---|
| Touched | {path} | {what changed this session} | {why next session reads this} |
| Read | {path} | (referenced for understanding) | {why} |
| Relevant | {path} | (not touched but next-session-relevant) | {why} |

## Important non-obvious things

1. {Numbered gotcha, hidden invariant, environment quirk the next session WILL hit.}
2. {Same.}
