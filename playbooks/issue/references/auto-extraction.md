# Issue: drafting fields from the conversation

Draft every field of `backlog_issue_create` from the conversation, then show the draft to the user before writing (`entry-point-flows.md`, log-issue).

## Arguments and their sources

| Argument | Source | If missing |
|---|---|---|
| `title` | First sentence describing the defect, up to 80 characters, active voice ("X fails when Y"). | Ask. |
| `severity` | From impact language: crash, data loss, outage → P0; blocks, regression, no workaround → P1; wrong result with a workaround → P2; cosmetic → P3. | Ask when there is no signal; a silent P0 guess does damage. |
| `evidence` | Required. The criterion (recurring, systemic, outstanding) plus its citation, in one sentence. | Without it the item is a Bug (`taskmaster:bug`, `../../bug/playbook.md`). |
| `impact` | What breaks, in the user's words. | Ask "What breaks when this happens?" |
| `components` | The epic or folder where the defect lives; infer from a cited file path. | The in-progress task's epic; empty if genuinely cross-cutting. |
| `location` | A `file:line` cited in the conversation; never invent one. | Empty list. |
| `discovered_by` | `Claude` when your analysis surfaced it (Issues often come from analysis); `user` when the user reported it, quoting their first sentence. | `Claude`. |
| `related_tasks` | The in-progress task id (from `backlog_status`) plus any task ids the user mentioned. | Empty list. |
| `body` | `## Repro` and `## Expected` from the conversation, using `../templates/issue-body.md`. | "Investigation pending". |
| `tldr` | Optional one-line essence; the store generates one from `impact` or the title when omitted. | Omit. |

`repro` and `symptom` are sections of `body`, not arguments.

## Dropping bad locations

Skip paths under `node_modules/`, `__pycache__/`, `.venv/`, `.git/`, `dist/` and `build/`, and paths that are only documentation examples (`example.com`, `foo/bar.ts`). Mention each dropped path in the draft: "(dropped `path`: not a real source file)".

## Focus hints

When the user says which aspect to focus on, weight `title`, `impact` and the `## Repro` section toward it. A hint steers the draft where signals are missing; it doesn't override explicit severity signals.
