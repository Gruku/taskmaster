---
name: linear
description: "Manage Taskmaster's Linear sync. Invoke for: 'set up linear sync', 'connect to linear', 'link task to linear', 'unlink from linear', 'show linear status', 'retry linear pushes', 'what's failing in linear', 'list linear trackers', 'show tracker linear-cm-eng-42'. Only correct way to drive backlog_linear_* — direct calls skip the bootstrap dialogue."
---

# Linear

Driver skill for Taskmaster's Linear sync. Linear is a **shared mirror** — TM is the source of truth, every TM mutation pushes outbound on the post-mutation hook, teammates running their own Taskmaster see each other through Linear.

## Why this skill exists

The `backlog_linear_*` MCP tools (`probe`, `bootstrap_apply`, `link`, `unlink`, `list`, `show`, `status`, `retry`) are the storage / transport layer. This skill is the dialogue layer — bootstrap requires a multi-step probe → propose-mapping → apply flow that has to happen in conversation, not in a single tool call.

Calling `backlog_linear_bootstrap_apply` directly skips the discovery + mapping proposal. Don't do that.

## Entry points

| Trigger | Entry |
|---|---|
| `set up linear`, `connect linear`, `bootstrap linear` | `bootstrap` |
| `link <task> to linear <KEY>`, `link this to linear` | `link` |
| `unlink <task> from linear` | `unlink` |
| `show linear status`, `what's failing in linear` | `status` |
| `retry linear`, `retry linear pushes`, `drain linear queue` | `retry` |
| `list linear trackers`, `show all linear issues` | `list` |
| `show tracker <id>`, `show linear-cm-eng-42` | `show` |

## bootstrap — the dialogue

Bootstrap is a 4-step flow. The user types "set up linear" and you walk them through it.

### Step 1 — confirm the env var

Pick a name following the convention `TASKMASTER_LINEAR_TOKEN_<ALIAS_UPPER>`. Ask: "What alias do you want for this workspace? Defaults to the lowercase team key after we probe."

Confirm the env var is set:
- Linux/Mac: `echo $TASKMASTER_LINEAR_TOKEN_CM`
- Windows: `echo $env:TASKMASTER_LINEAR_TOKEN_CM`

If the user hasn't created an API key: point them to `https://linear.app/settings/api`. **Wait for them to confirm before continuing** — never invent a token.

### Step 2 — probe

Call `backlog_linear_probe(token_env="TASKMASTER_LINEAR_TOKEN_<ALIAS>")`. Returns JSON: teams + their statuses + users.

Present a summary:
```
Linear workspace has N teams: <team_a> (KEY=ENG, 7 statuses, 12 users), <team_b> (...)
```

Ask which team this Taskmaster project syncs to. **One team per project** in v1 (multi-team is v3).

### Step 3 — propose mapping

For the chosen team, propose:

- **`workspace_alias`** — default to lowercase team key (e.g. `ENG` → `eng`). Confirm.
- **`status_mapping`** — TM statuses are: `todo, in-progress, in-review, done, blocked`. Match to the team's Linear states by name similarity. If "In Review" isn't a state, **stop and tell the user** — they need to add it in Linear, or you fail-loud on the first push (the worker treats `status_mapping` errors as permanent failures).
- **`priority_mapping`** — TM priorities `critical/high/medium/low` to Linear `0-4`. Default: `critical:1, high:2, medium:3, low:4` (Linear's priority 0 is "No priority" — reserved).

Show the proposed mapping as a table and confirm with the user before writing.

### Step 4 — apply

Call `backlog_linear_bootstrap_apply(workspace_alias=..., team_id=..., token_env=..., status_mapping="todo:state-uuid,...", priority_mapping="critical:1,...", default_workspace=True)`.

Read the response. On success: confirm with the user and offer the next move — "Want to link an existing task now (`/linear link <task_id> <KEY>`), or rely on auto-push for new mutations?"

## link

User: `link auth-003 to linear ENG-42`.

Call `backlog_linear_link(task_id="auth-003", external_key="ENG-42")`. The tool creates the Tracker file and stamps `tracker_id` on the task. Confirm with the user; the next mutation on that task will push to Linear.

If `workspace_alias` is ambiguous (multiple in `linear.yaml`), pass it explicitly.

## unlink

User: `unlink auth-003 from linear`.

Call `backlog_linear_unlink(task_id="auth-003")`. The tracker file stays on disk as a historical record — the task just no longer references it. Subsequent mutations on that task are no-ops for Linear. Tell the user.

## status

User: `show linear status`.

Call `backlog_linear_status()`. Read the JSON. Surface:
- Queue depth and the oldest enqueued item's age
- Count of permanent failures (and the most recent reason)
- Recent transient retries

If the queue has stuck permanent failures: surface the reason and offer `taskmaster:linear retry` (they'll be re-attempted; permanent ones will re-fail with the same reason, useful for diagnosing).

## retry

User: `retry linear pushes` or `retry linear for auth-003`.

Call `backlog_linear_retry(target_id="")` for all, or `backlog_linear_retry(target_id="auth-003")` for one. Returns counts. Surface them.

## list

User: `list linear trackers`.

Call `backlog_linear_list()`. Returns JSON of all linear trackers + their last-pushed state. Render as a table.

## show

User: `show tracker linear-cm-eng-42`.

Call `backlog_linear_show("linear-cm-eng-42")`. Returns the full frontmatter + body. Render plainly.

## Notes

- **Never** invoke `mcp__linear-server__*` tools as part of bootstrap or runtime. The whole point of the GraphQL pipeline is to avoid the token cost. `mcp__linear-server__*` is for ad-hoc dev/debug only.
- Sync direction is push-only in v1 — local TM is authoritative. If a teammate edits the Linear issue, their change is overwritten on the next TM mutation. Tell the user this if they ask.
- Sync failures never block local mutations — the local write always succeeds, the Linear push is best-effort. If `status` shows a backlog, it's a sync problem, not a local problem.
