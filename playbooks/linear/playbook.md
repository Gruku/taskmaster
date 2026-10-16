# Linear

Taskmaster's Linear sync is a shared mirror: taskmaster is the source of truth, every mutation pushes outbound after it commits, and teammates running their own taskmaster see each other through Linear. `backlog_linear` is the single tool (actions `probe`, `bootstrap_apply`, `link`, `unlink`, `list`, `show`, `status`, `retry`). Bootstrap is a conversation (probe, propose a mapping, apply), so run it through this playbook; a cold `bootstrap_apply` skips the discovery and the mapping the user should confirm.

## Entry points

| Trigger | Action |
|---|---|
| `set up linear`, `connect linear`, `bootstrap linear` | Bootstrap (below) |
| `link <task> to linear <KEY>` | `backlog_linear(action="link", task_id=<id>, external_key=<KEY>)`. Creates the tracker and stamps `tracker_id` on the task; its next mutation pushes. Pass `workspace_alias` when `linear.yaml` holds several. |
| `unlink <task> from linear` | `backlog_linear(action="unlink", task_id=<id>)`. The tracker file stays as a record; the task no longer references it, so later mutations don't push. |
| `show linear status`, `what's failing in linear` | `backlog_linear(action="status")`. Report queue depth, the oldest item's age, the permanent-failure count with the latest reason, and recent transient retries. |
| `retry linear pushes` | `backlog_linear(action="retry", target_id="")` for all, or `target_id=<task id>` for one. Report the counts. A permanent failure re-fails with the same reason, which is useful for diagnosis. |
| `list linear trackers` | `backlog_linear(action="list")`. Render as a table. |
| `show tracker <id>` | `backlog_linear(action="show", tracker_id=<id>)`. Render plainly. |

## Bootstrap

1. **Find the token variable.** The token lives in an env var named `TASKMASTER_LINEAR_TOKEN_<ALIAS>`. List such variables by name only; the value is a secret and must not reach the transcript. Bash: `env | cut -d= -f1 | grep '^TASKMASTER_LINEAR_TOKEN_'`. PowerShell: `Get-ChildItem env:TASKMASTER_LINEAR_TOKEN_* | Select-Object Name`. One match: use it. Several: ask which. None: send the user to https://linear.app/settings/api to create a key and set the variable, then wait; the token has to come from them.
2. **Probe.** `backlog_linear(action="probe", token_env="TASKMASTER_LINEAR_TOKEN_<ALIAS>")` returns the workspace's teams, their statuses and users as JSON. Summarise it ("N teams: ENG (7 statuses, 12 users), ...") and ask which team this project syncs to. A project syncs to one team.
3. **Propose the mapping** for the chosen team and show it as a table.
   - `workspace_alias`: default is the lowercase team key (`ENG` becomes `eng`).
   - `status_mapping`: taskmaster statuses are `todo, in-progress, in-review, done, blocked`; match each to a Linear state by name. If the team has no "In Review" state, stop and tell the user to add it in Linear. Without it the first push fails permanently, because the worker treats `status_mapping` errors as permanent.
   - `priority_mapping`: `critical:1, high:2, medium:3, low:4` by default. Linear's 0 is "No priority" and stays unused.
4. **Apply after the user confirms the table.** `backlog_linear(action="bootstrap_apply", workspace_alias=..., team_id=..., token_env=..., status_mapping="todo:<state-uuid>,...", priority_mapping="critical:1,...", default_workspace=True)`. On success, offer the next move: link an existing task, or rely on auto-push for new mutations.

## Notes

- Don't use `mcp__linear-server__*` tools for bootstrap or sync. The GraphQL pipeline behind `backlog_linear` exists to avoid their token cost; they are for ad-hoc debugging only.
- Sync is push-only and taskmaster is authoritative: a teammate's edit to a Linear issue is overwritten on the next push. Say so if the user asks.
- A sync failure never blocks a local write. If `status` shows a backlog, it is a sync problem, not a local one.
