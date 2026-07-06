# Lesson Auto-Extraction

For each lesson the skill writes, every frontmatter and body field is auto-drafted from a specific source. The user reviews and edits before the file is written.

## Per-field sources

| Field | Source | Fallback |
|-------|--------|----------|
| `title` | First sentence of the candidate body OR user's request phrase. ≤80 chars. Imperative tense ("Always X before Y"). | Ask the user. |
| `kind` | Candidate `kind` attr if set; else infer from session tone: corrections → `anti-pattern`; "we always" / "always do" → `pattern`; "watch out" / "got burned" / "this keeps biting" → `gotcha`. | Ask the user. |
| `triggers.files` | `git diff --name-only HEAD~5` from this session, collapsed to globs (e.g. `src/auth/login.ts` + `src/auth/session.ts` → `src/auth/**`). | `[]` (lesson loads only via `task_titles_match`). |
| `triggers.task_titles_match` | 3–5 keyword nouns/verbs from current task title + last 3 task titles in `backlog_status`. | `[]`. |
| `triggers.task_kinds` | Currently-in-progress task's `kind` field if it has one; else `[]`. | `[]`. |
| Body `## Why` | Candidate body + correction/bug context Claude has. 2–4 sentences. | Ask the user. |
| Body `## What to do` | Numbered list. Steps drawn from the resolution path Claude observed. ≥2 steps. | Ask the user. |
| Body `## Examples` | Bullet list of `T-NNN`/`feature-NNN` task ids touched this session + 1–3 short commit SHAs from `git log --oneline -5`. | `(none)` — drop the section if empty. |
| `related_tasks` | Task ids in `backlog_status` with status `in-progress` or transitioned this session. | `[]`. |
| `related_issues` | `ISS-NNN` ids referenced in this session's prose (regex `\bISS-\d+\b` over conversation). | `[]`. |

## Glob collapsing rule

When two or more file paths share a common ancestor of depth ≥2, collapse to `<ancestor>/**`. When they don't, list each path explicitly. Examples:

- `src/auth/login.ts` + `src/auth/session.ts` → `src/auth/**`
- `src/auth/login.ts` + `tests/auth/test_login.py` → `["src/auth/**", "tests/auth/**"]` (separate glob each).
- A single file → keep the literal path; don't collapse to `**`.

## Focus-hint weighting

When the user invokes the skill with a hint (e.g. "save a lesson — focus on multi-tab fanout"), weight extraction toward that topic:

- `title` must include the hint word(s).
- `triggers.task_titles_match` must include the hint word as one of the first 3 keywords.
- Body `## Why` first sentence must explain why the hint topic causes the issue.

The hint never overrides explicit candidate attrs (`kind=`, `topic=`); it only steers the auto-fill where the candidate is silent.

## What gets dropped from extraction

- Paths under `node_modules/`, `__pycache__/`, `.venv/`, `.git/`, `dist/`, `build/`, `.snapshots/`, `.taskmaster/auto/`. Same exclusion list as the handover skill.
- Paths the regex caught from prose that are obviously not real (e.g. `example.com`, `foo.bar.baz`).
- Tasks the user said to ignore in this session.

When a path is dropped, do **not** silently swallow it — note in the draft preview "(dropped <path>: <reason>)" so the user can override.
