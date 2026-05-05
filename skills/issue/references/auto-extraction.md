# Issue Auto-Extraction

For each issue the skill creates, every field is auto-drafted from a specific source. The user reviews and approves before the file is written.

## Per-field sources

| Field | Extraction source | Fallback if missing |
|---|---|---|
| `title` | First user sentence describing the bug, summarized to ≤80 chars. Active voice: "X fails when Y", "Z crashes on startup". | Ask the user. |
| `severity` | Infer from impact language: "crash" / "data loss" / "outage" → P0; "blocks" / "regression" / "no workaround" → P1; "wrong result" / "workaround exists" → P2; "cosmetic" / "minor" → P3. | Always ask if no signal — never guess P0 silently. |
| `impact` | User's description of what breaks. Extracted from "what breaks?" follow-up if blank in initial message. | Ask: "What breaks when this happens?" |
| `components` | Epic name or folder where the bug lives. Inferred from the file path if the user or conversation cited one (e.g. `src/auth/login.ts` → `auth`). | Infer from the in-progress task's epic slug; leave empty if genuinely cross-cutting. |
| `location` | The `file:line` reference cited in conversation (regex: `\b[\w/.-]+\.(ts|py|tsx|js|go|rs):\d+\b`). | Leave empty (`[]`) for non-localized bugs. Do not fabricate a path. |
| `discovered_by` | `"Claude"` if the defect was surfaced mid-session by Claude's analysis. `"user"` if the user reported it. Quote the first sentence the user used to describe it when `discovered_by="user"`. | `"Claude"` — the default when source is ambiguous. |
| `related_tasks` | Currently in-progress task ID (from `backlog_status`) + any task IDs the user explicitly mentioned in the bug report. | Empty list (`[]`). |
| `body` / Repro | Numbered repro steps extracted from conversation prose. Formatted as `## Repro` body section using the template in `templates/issue-body.md`. If no steps are known, use the placeholder "Investigation pending". | "Investigation pending" placeholder — do not leave the body blank. |

## What `repro` and `symptom` are NOT

`repro` and `symptom` are **body sections** (markdown under `## Repro` and `## Expected`), not frontmatter fields. The `write_issue` signature does not accept `repro=` or `symptom=` as keyword arguments — these live only in the `body=` string.

The `_ISSUE_INDEX_FIELDS` kept in `backlog.yaml` are: `id`, `title`, `status`, `severity`, `components`, `related_tasks`. Everything else — `impact`, `location`, `discovered_by`, `resolved`, `fixed_in_task`, `duplicate_of` — is in the individual issue file only.

The `write_issue` signature is:
```python
write_issue(
    backlog_path, *,
    title, severity, impact="", components=None,
    location=None, related_tasks=None,
    discovered="", discovered_by="", body="",
    issue_id=None, status="open",
)
```

Pass repro steps and symptom descriptions as formatted markdown in `body=`. Do not pass them as frontmatter keys.

## Extraction drops

- File paths under `node_modules/`, `__pycache__/`, `.venv/`, `.git/`, `dist/`, `build/` — these are never valid `location` entries.
- Paths that appear to be documentation examples or prose metaphors (e.g. `example.com`, `foo/bar.ts`) — drop and note in the draft preview.

When a path is dropped, note it in the draft: "(dropped `path`: not a real source file)".

## Focus-hint weighting

When the user provides extra context on what aspect of the bug to focus on, weight `title`, `impact`, and the `## Repro` body toward that framing. The hint does not override explicit severity signals — it only steers the auto-fill where signals are absent.
