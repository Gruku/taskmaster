# Jira ↔ Taskmaster Integration — Design Spec

**Date:** 2026-05-07
**Status:** Draft (design only; implementation lives in the Taskmaster plugin repo)
**Implementation target:** `taskmaster@gruku-tools` plugin (separate repo, pushed from there)
**Backlog task:** TBD — opened against the Taskmaster plugin epic, not against CodeMaestro

---

## 1. Problem & motivation

CodeMaestro work is tracked in two systems with different granularities:

- **Jira** (CodeMaestro instance) — issues are roughly epic-sized: broad scope, multi-day, often span several files/areas. Jira is the source of truth for cross-team visibility, reporting, and worklog.
- **Taskmaster** — tasks are small, file-of-truth, AI-managed units (typically a single session). One Jira issue typically maps to multiple Taskmaster tasks.

Today there is no link between the two. Re-deriving "which Taskmaster work belongs to which Jira issue" relies on memory or naming conventions. Posting status updates to Jira is fully manual.

We also work against multiple Jira instances simultaneously (CodeMaestro Jira + others). Any integration must support per-project credentials without secrets entering git.

## 2. Goals / non-goals

### Goals

1. **Link** Taskmaster tasks to a parent Jira issue without duplicating Jira data.
2. **Pull** Jira issue metadata into a local read-only mirror so dashboards and AI flows can see it without re-querying Jira every command.
3. **Asymmetric write** — comments, status transitions, worklog, and (gated) description updates are pushed only on explicit user action.
4. **Multi-Jira** — multiple instances coexist; per-project config; per-project token via env var; no token in git.
5. **Clean separation** — Jira logic lives in its own router skill (`taskmaster:jira`) and its own MCP tools (`backlog_jira_*`); existing skills only get light, soft hooks.

### Non-goals (explicitly out of scope, V1)

- Bidirectional automatic sync (status/comments propagating without user action).
- Webhook listener (no requirement for real-time push).
- Cross-Jira linking (an issue in Jira A pointing to an issue in Jira B).
- Mirroring Jira sprints/boards as Taskmaster phases.
- Bulk decomposition (`jira decompose --all`).
- Pulling Jira projects/users/components into local catalogues.
- Token storage in keychain / OS credential store.

## 3. Concepts

### 3.1 Tracker

A new top-level entity in Taskmaster, alongside **phase** and **epic**. A tracker is a *cached, read-only mirror of a single external issue*. In V1, "external" always means Jira; the schema is designed so future systems (GitHub, Linear) reuse the same shape.

A tracker is **not** an epic. It does not carry epic semantics (priority rollup, status flow, completion). It is a reference + cache, nothing more.

A Taskmaster task may carry a `tracker_id` field linking it to its parent tracker. The reverse mapping (tracker → linked tasks) is **derived on demand** from the tasks index — never stored on the tracker.

### 3.2 Instance

A single Jira deployment + project + credential, declared in `.taskmaster/jira.yaml` and named by an alias. A project may declare multiple instances; each tracker remembers which instance it came from.

## 4. Data model

### 4.1 Tracker file: `.taskmaster/trackers/<id>.md`

```yaml
---
id: jira-codemaestro-cm-101    # local id; format: <system>-<instance_alias>-<key-lowercased>
external_system: jira
external_key: CM-101
instance_alias: codemaestro    # references .taskmaster/jira.yaml
title: "Welcome flow polish — magic-button styling"
status: In Progress             # cached, verbatim from Jira
assignee: Volodymyr Demchenko
url: https://codemaestro.atlassian.net/browse/CM-101
last_synced: 2026-05-07T14:22:00Z
synced_hash: 7f3c8b2a…          # sha256 of the canonicalised payload last seen
---

# CM-101 — Welcome flow polish — magic-button styling

(Cached Jira description, rendered from ADF to markdown. Read-only.
Acceptance criteria, attachments-as-links, etc.)
```

- `id` is deterministic from `external_system` + `external_key`. Re-pulling the same Jira key never creates a duplicate tracker.
- `synced_hash` lets `jira pull` detect "no change" cheaply — if the canonical payload hashes the same, we skip the file write.
- Body is the rendered Jira description + acceptance criteria. Markdown only; no embedded HTML.

### 4.2 Task linkage

Optional task frontmatter field:

```yaml
tracker_id: jira-codemaestro-cm-101
```

`backlog.yaml`'s task index gets the same field copied for fast querying:

```yaml
tasks:
  - id: cm-101-impl-magic-buttons
    title: ...
    epic: desktop-app
    tracker_id: jira-codemaestro-cm-101
```

A task has at most one tracker. Issues (`taskmaster:issue`) may also carry `tracker_id` — same shape, same rules.

### 4.3 No `linked_tasks` array on the tracker

The reverse map (tracker → tasks) is derived by scanning the tasks index. This eliminates a class of "the array got out of sync with reality" bugs.

## 5. Configuration

### 5.1 `.taskmaster/jira.yaml` (committed)

```yaml
instances:
  - alias: codemaestro
    base_url: https://codemaestro.atlassian.net
    project_key: CM
    user_email: gruku.v.d@gmail.com
    token_env: TASKMASTER_JIRA_TOKEN_CM
    default_jql: "assignee = currentUser() AND statusCategory != Done"

default_instance: codemaestro
```

- File is **committed**. It contains no secrets.
- `token_env` is the **name** of the environment variable that holds the API token. The value is never stored.
- Multiple instances are allowed; each must have a unique `alias` and a unique `token_env`.

### 5.2 Credentials

- Token is read **only** from `process.env[token_env]` at the moment of an API call.
- If the env var is missing, the command refuses with:
  ```
  $TASKMASTER_JIRA_TOKEN_CM is not set.
  Create a token at https://id.atlassian.com/manage-profile/security/api-tokens
  then export TASKMASTER_JIRA_TOKEN_CM=<token>.
  ```
- No keychain, no prompt-and-cache, no file fallback.
- Multi-Jira coexistence comes from each instance using a different env-var name. The user names them per project.

### 5.3 `.taskmaster/jira.local.yaml` (gitignored, optional)

For per-machine overrides if ever needed (e.g. a dev points at a sandbox Jira). Same shape as `jira.yaml` minus secrets — merged on top of it. **Tokens are still only read from env vars** (per §5.2); `jira.local.yaml` may not contain a `token` field of any kind, only instance config such as `base_url` or `default_jql`. Not required for V1; mentioned only because gitignoring it now is free and avoids a later migration.

## 6. Commands

All Jira commands route through the new **`taskmaster:jira` router skill**. The router exposes sub-commands; each sub-command maps to one MCP tool (`backlog_jira_*`).

### 6.1 Read commands (no write scope needed)

| Command | Description |
|---|---|
| `jira pull [--instance <alias>] [--jql '<q>']` | Run JQL (default: `default_jql`), upsert tracker files. Report counts: new / updated (hash changed) / unchanged / closed-in-Jira. Never touches tasks. |
| `jira list [--instance <a>] [--status <s>]` | Print trackers + linked-task counts (derived). |
| `jira show <key>` | Print one tracker file + the list of linked tasks/issues (derived). |
| `jira link <task-id> <key>` | Manually attach a tracker to an existing task (sets `tracker_id`). |
| `jira unlink <task-id>` | Clear `tracker_id` on a task. |

### 6.2 Write commands (require write-scoped token; each prompts to confirm)

| Command | Behaviour |
|---|---|
| `jira decompose <key>` | Sub-agent reads tracker body + relevant codebase context, proposes a Taskmaster task list. User reviews/edits/accepts. Creates tasks with `tracker_id` set. **No Jira write.** |
| `jira comment <key>` | Opens editor pre-filled with the latest session summary (or `--message`). User confirms → `POST /rest/api/3/issue/{key}/comment`. |
| `jira transition <key>` | Fetch transitions via `GET /rest/api/3/issue/{key}/transitions`, present picker, confirm → `POST .../transitions`. |
| `jira worklog <key>` | Prompt for time-spent + comment + start time, confirm → `POST /rest/api/3/issue/{key}/worklog`. |
| `jira update <key> [--fields <list>]` | Show current Jira fields vs. proposed diff, confirm → `PUT /rest/api/3/issue/{key}`. **Default scope: description only.** Other fields (labels, components, etc.) require explicit `--fields`. |

### 6.3 Confirmation gates

- Every write command shows a preview block: target instance, target key, the action, and the diff if applicable.
- `jira update` is the highest-risk command. It must:
  1. Re-fetch the issue immediately before showing the diff (avoid stomping a teammate edit made since the last pull).
  2. Present a side-by-side diff (current vs. proposed) for every changed field.
  3. Refuse to push if the issue's `updated` timestamp moved between the fetch-for-diff and the user's confirmation, instructing the user to re-run.

## 7. Lifecycle & soft hooks

### 7.1 Tracker lifecycle

- A tracker exists from the first `jira pull` that returns its key.
- Subsequent pulls refresh `title`, `status`, `assignee`, `url`, `last_synced`, `synced_hash`, and the body.
- A tracker is never "completed" locally. Its lifecycle reflects whatever Jira reports.
- `jira pull` reports trackers that *were* in the last pull but are no longer in the current JQL response as **closed** (status note in the report). They are **not** deleted — they remain on disk for history. A separate `jira archive <key>` command (V1.5) can move them to `.taskmaster/trackers/archive/` if the user wants to declutter.

### 7.2 Soft hooks in existing skills

`taskmaster:start-session`:
- If `.taskmaster/jira.yaml` exists, the dashboard adds a **Trackers** panel:
  - Open tracker count, last pull timestamp, age warning if older than 24 h.
  - **No automatic pull** — purely informational.

`taskmaster:end-session`:
- If the session's task has `tracker_id` and all sibling tasks under that tracker are now `done`:
  - Offer `jira comment` (pre-filled with the session summary).
  - Offer `jira transition` (with the configured transitions list).
- Both offers are skippable. The hook delegates to the `taskmaster:jira` skill; it never writes directly.

`taskmaster:pick-task`:
- If the task carries `tracker_id`, the loaded context block includes the tracker's title + status + URL.

These are the only existing-skill touchpoints. Everything else lives behind the `taskmaster:jira` router.

## 8. MCP tool surface

New tools, all prefixed `backlog_jira_`:

- `backlog_jira_pull(instance?, jql?)` → report
- `backlog_jira_list(instance?, status?)` → trackers
- `backlog_jira_show(key)` → tracker + derived linked tasks/issues
- `backlog_jira_link(task_id, key)` / `backlog_jira_unlink(task_id)`
- `backlog_jira_decompose(key)` → proposed tasks (does not commit)
- `backlog_jira_comment(key, body)` → comment id
- `backlog_jira_transitions(key)` → list available transitions
- `backlog_jira_transition(key, transition_id)` → result
- `backlog_jira_worklog(key, time_seconds, started_at, comment)` → result
- `backlog_jira_update(key, fields_diff)` → result

All write tools accept the **resolved diff** prepared by the skill, not raw user input — confirmation/diff rendering is the skill's job.

## 9. Multi-Jira behaviour

- Project A's `.taskmaster/jira.yaml` declares `token_env: TASKMASTER_JIRA_TOKEN_CM` (CodeMaestro Jira).
- Project B's `.taskmaster/jira.yaml` declares `token_env: TASKMASTER_JIRA_TOKEN_OTHER` (other Jira).
- Both projects can be open in different shells with both env vars set. No collision; each project resolves its own token by name.
- Within a single project, multiple instances are allowed. Each tracker carries `instance_alias`, so tools always know which instance to dispatch against.
- The `--instance` flag overrides `default_instance` for read commands. Write commands always require an unambiguous tracker target, so the instance is implicit.

## 10. Failure modes & rules

| Failure | Behaviour |
|---|---|
| `.taskmaster/jira.yaml` missing | All `jira *` commands print "no Jira config in this project" and exit 0. Soft hooks become no-ops. |
| Env var for `token_env` missing | Command refuses with the message in §5.2. Read commands and write commands behave the same. |
| Jira returns 401 | Print "token rejected by {base_url} — re-issue at id.atlassian.com/manage-profile/security/api-tokens", exit 2. Do not retry. |
| Jira returns 429 / 5xx | Read commands: retry with exponential backoff up to 3× then fail. Write commands: do **not** retry — surface the error and let the user re-issue. |
| `jira update` finds the issue's `updated` moved between fetch-for-diff and user confirm | Refuse, instruct re-run. Never overwrite. |
| Two instances with the same `alias` declared in one project | Refuse at config-load time. Aliases must be unique within a project. (Same key across *different* aliases is fine — the `id` format includes the alias and disambiguates.) |

## 11. Migration

No schema change is required for existing tasks. `tracker_id` is optional. Existing projects without `.taskmaster/jira.yaml` stay completely unaffected.

To opt a project in:

1. Create `.taskmaster/jira.yaml` from the template.
2. Set the env var.
3. `jira pull` — populates `.taskmaster/trackers/`.
4. Optionally, `jira link <task-id> <key>` for existing tasks that already correspond to known Jira issues.

## 12. Open questions

- **Comment format on push.** Should `jira comment` post raw markdown, or convert to ADF? Markdown is simpler; ADF gives richer rendering. V1: post as Atlassian Wiki Markup (Jira Cloud accepts a `body` plus `representation: "wiki"`), trivial to render from our local markdown without a full ADF builder. Revisit if the rendering looks off.
- **`jira decompose` model choice.** Sonnet by default; raise to Opus if the tracker body is long or the codebase context the agent pulls in is unusually large. Decision belongs in the skill, not the spec.
- **Custom field IDs.** Jira instances often use custom fields (e.g. acceptance criteria, story points). V1 reads only standard fields. If users need custom-field reads, add a `custom_fields:` map to the instance config in V1.5.

## 13. Definition of done (for the V1 implementation)

1. `.taskmaster/jira.yaml` schema accepted and validated by `backlog_validate`.
2. All read commands work against a real Jira instance with a real token, end-to-end.
3. All write commands work and gate on confirmation; `jira update` performs the re-fetch-and-compare guard.
4. Soft hooks fire only when configured; behave as no-ops otherwise.
5. Multi-Jira: two instances declared in two open projects exchange data without env-var collision.
6. No token ever appears in any committed file, log line, or error message.
7. Existing Taskmaster projects without Jira config see zero behavioural change.
