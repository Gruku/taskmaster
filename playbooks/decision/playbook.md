# Decision

A decision is a structured branching point: ≥2 mutually exclusive paths Claude proposes, awaiting user resolution. Decisions live in `.taskmaster/decisions/DEC-NNN.md` and survive context death.

## Why this skill exists

The user used to receive option menus inline in chat ("Options: 1. ... 2. ..."). Scrollback is not durable storage. After context dies, the user couldn't find unresolved choices and resorted to writing Telegram messages to themselves. Decisions move that menu into a first-class entity readable from the continuity dashboard.

The backend is `backlog_decision_create` (distinct tool) plus `backlog_decision(action=...)` for `list` / `get` / `resolve` / `drop` / `update`. This skill is the **authoring + lifecycle** layer. Calling the MCP tools directly skips trigger heuristics and the option-quality gate. Always go through this skill.

## When to invoke

Five entry points:

1. **`write-decision`** — Claude is about to write `Options:` followed by ≥2 mutually exclusive paths in chat. **Hard rule:** route through this skill instead. Echo a one-line "DEC-NNN written — decide on dashboard or via `/decide DEC-NNN`" rather than spelling the menu in chat.
2. **`resolve`** — user picks an option ("go with option 2", "let's do the local merge", "/decide DEC-001 2").
3. **`drop`** — circumstances changed; the decision no longer matters ("drop DEC-001", "this got resolved externally").
4. **`update`** — pre-resolution edits to title, options, or recommendation.
5. **`list`** — surface open decisions ("what decisions are open", "show me open decisions for ue-plugin-086").

## Authoring rules

- **Title ≤ 80 chars**, action-shaped ("Land ue-plugin-086 fix", not "the question of how to land").
- **At least 2 options.** If you can't articulate 2, there is no decision — just write the work.
- **Options are mutually exclusive.** "Do A" and "Do A then B" is *not* a decision; that's a plan.
- **One-line option text** — the decision body is for rationale, not for an essay per option.
- **Recommendation is optional.** Leave null if you genuinely don't have a preference; lying about indifference wastes the user's read.
- **Link to context** — set `task_id` from the current in-progress task; set `branch` from `git rev-parse --abbrev-ref HEAD`; set `related_issues` for any ISS-NNN this would resolve.

## Lifecycle

See [`references/lifecycle.md`](references/lifecycle.md).

`open` → `resolved` (option chosen) or `dropped` (no option, reason required). Terminal states cannot reopen — create a new decision instead.

## Auto-resolution

See [`references/auto-resolution.md`](references/auto-resolution.md).

- Commit message `Resolves: DEC-001 with option 2` flips status on MCP scan.
- `end-session` runs a per-decision sweep over open decisions linked to the in-progress task.

## Steps — write a decision

1. **Compose** the title, options, recommendation, and the context body.
2. **Resolve linking info** from current state:
   - `task_id` = current in-progress task id (from auto state or recent commits).
   - `branch` = `git rev-parse --abbrev-ref HEAD`.
   - `raised_in` = most recent handover id, if writing during a session that just produced one.
3. **Call**:
   ```
   backlog_decision_create(
     title=..., options=[...], recommendation=...,
     task_id=..., branch=..., related_issues=[...],
     raised_in=..., body=...,
   )
   ```
4. **Echo** `DEC-NNN written — decide on dashboard or via /decide DEC-NNN`. Do **not** restate the options in chat.

## Steps — resolve

1. Parse the option number from the user's phrasing ("option 2", "the local merge one", "2", `/decide DEC-001 2`).
2. Optionally capture a one-line rationale.
3. Call `backlog_decision(action="resolve", decision_id=..., resolved_with=..., rationale=...)`.
4. Echo the resolution and (if a linked task exists) append a one-line trace to the task body:
   `2026-MM-DD · DEC-NNN resolved with option N: "<option text>"`.

## Edge cases

- **No backlog** — return the standard `backlog_init` first error.
- **Re-open attempt** — user says "actually let's reconsider DEC-001 even though it's resolved." Do **not** re-open. Write a new decision and add a body link to the previous one.
- **Recommendation > N options** — validation rejects; recompose.
- **Decision overlaps with an open one** — list returns the existing; offer to update the existing instead of writing a new one.

## References

- `references/lifecycle.md` — state diagram, terminal-state rules.
- `references/auto-resolution.md` — commit-message + end-session sweep contract.
- `templates/decision-body.md` — body skeleton.

## Spec

`docs/superpowers/specs/2026-05-15-continuity-dashboard-design.md` §1.
