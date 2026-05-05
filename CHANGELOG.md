# Changelog

All notable changes to the taskmaster plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html) — major bumps
indicate schema breaks or removed surfaces.

---

## 3.0.1 — patch (2026-05-06)

Two release-blocker fixes surfaced during 3.0.0 smoke testing.

### Fixed

- **Migrated tasks no longer show empty Description / Notes.** The v3 reader (`_load_task_full`) was only copying `("docs", "review_instructions", "patchnote", "release", "worktree", "spec_review", "locked_by")` from per-task `.md` frontmatter — silently dropping `description` and `notes` even though the migration writer puts them there. The reader now imports `HEAVY_FIELDS` from `taskmaster_v3` and concatenates with the other fm-only fields, so any future addition to `HEAVY_FIELDS` is automatically respected. Closes ISS-003.
- **Skill auto-offers fire on legacy v3 backlogs without the `schema_version` marker.** Two-pronged fix: (1) `_ensure_v3_marker` runs after each v3 entity create (handover/lesson/issue), promoting `meta.schema_version` to 3 in `backlog.yaml`. Marker-only — does not split tasks. Idempotent. (2) `_effective_schema_version` heuristic — `backlog_status` now emits `**Schema:** v<N>` as its first line, reporting v3 when entity content (`handovers`/`issues`/`lessons_meta`) exists even without the marker. The three retrofit skill gates (`start-session`, `pick-task`, `end-session`) read this line. Closes ISS-001.

### Test count

460+ Python tests (10 new ISS-001 regression tests, 2 new ISS-003 regression tests). E2E smoke and full suite green.

### Upgrade notes

- No action required. After `/plugin update gruku-tools/taskmaster` and an MCP reconnect, both fixes apply retroactively to existing migrated tasks and existing v3-content backlogs without re-migration.
- The `**Schema:** v<N>` line is now the canonical read site for skill schema gates; if you authored custom skills that grep for `schema_version` in `backlog_status` output, switch to the `Schema:` line.

---

## 3.0.0 — v3 release

Major version aligned with the schema name (`schema_version: 3`,
`taskmaster:migrate-v3`, the `/v3` viewer URL). Skips 2.x entirely; the
last shipped major was 1.11.x. Two themes:

1. **Narrative continuity.** Sessions, decisions, gotchas, and bugs are now first-class entities — handovers, lessons, and issues each have their own MCP surface, viewer screens, and skill layer. Recap tells you what changed in project state since the last snapshot. Auto-mode drives single tasks, whole epics, or entire phases through the full lifecycle as a state machine, with PreCompact-safe cursor persistence.
2. **Edit-in-UI.** Every entity (task, epic, phase, handover, lesson, issue) is editable from the viewer with inline-field autosave, ETag/If-Match concurrency, conflict banners, and server-side schema validation. The kanban dashboard, task detail, and entity modals all share one component system.

v2 backlogs keep working unchanged. The v3 schema is opt-in via the new `taskmaster:migrate-v3` skill, which wraps `backlog_migrate_v3` with a pre-flight gate, opt-in confirmation, and post-flight gitignore handling. The migration is idempotent and reversible (`git restore backlog.yaml` + remove `tasks/`).

### Breaking changes

- **Schema break (opt-in).** v3 backlogs move heavy task fields (`description`, `notes`, `docs`, `review_instructions`) out of `backlog.yaml` into per-task files at `.taskmaster/tasks/<task-id>.md`. The slim index in `backlog.yaml` keeps id/title/status/priority/etc. for fast dashboard renders. **In-memory shape is unchanged** — every existing tool/skill keeps working on both schemas. v2 backlogs are untouched until the user explicitly opts in.
- **Major version bump.** Plugins or scripts pinning `taskmaster@^1.0.0` will not auto-upgrade. The viewer URL paths are unchanged but the data shape returned by `/api/backlog` differs on v3 — third-party integrations against the v2 API need a switch on `schema_version`.
- **Some v1.x viewer assets removed.** The HTML-only viewer (`backlog-viewer.html`) is replaced by the v3 viewer (`viewer/`). The old file remains in the repo for v2 compatibility but the dashboard `backlog_open_viewer` tool now opens the v3 surface.

### Migration

```text
You: I want to upgrade to v3
Claude: [invokes taskmaster:migrate-v3]
        [shows pre-flight: 47 tasks, 12 with non-trivial bodies will move]
        [explains schema break + reversibility]
AskUserQuestion: Migrate / Show diff / Cancel
You: Migrate
        [calls backlog_migrate_v3 → idempotent migration runs]
        [post-flight: appends .taskmaster/snapshots/ + .taskmaster/auto/ to .gitignore]
        [tour: handover/lesson/issue skills now available]
```

The full migration takes seconds for typical backlogs. Roll-back: `git restore .claude/backlog.yaml` (or `.taskmaster/backlog.yaml`) and remove the new `tasks/` directory.

### New: handovers — session-to-session continuity

`taskmaster:handover` skill writes a Claude-drafted session continuity artifact under `.claude/handovers/`. Auto-extracts files of interest, what shipped, what's next; user reviews and approves. Three tiers (light/standard/full) selected automatically based on session signals. Supersession chaining for milestone-complete handovers.

Triggers: "write a handover", "save context", "wrap up", "for tomorrow", "next time", "remind future me", "i'm at 300k", "before compaction", "context handoff", "continue where we left off".

Auto-offered by `end-session` for heavy sessions (>60 turns, >200k tokens, in-flight task at end). The new `pick-task` "continue this task" trigger (also "continue where we left off") auto-resolves via the latest handover's first task id, jumping straight back to where you left off.

MCP surface: `backlog_handover_create`, `backlog_handover_get`, `backlog_handover_list` (with `task_id`/`session_kind`/`since` filters), `backlog_handover_latest`, `backlog_handover_resync`, `backlog_handover_supersede`. Index capped at 30 entries, overflow archived to `handovers/_archive/<year>/`.

### New: lessons — patterns that reinforce across sessions

`taskmaster:lesson` skill records project-scoped lessons (gotchas, anti-patterns, conventions) and tracks how often they actually fire. Five entry points: write-from-context, write-from-candidate, reinforce-immediate, reinforce-sweep, session-retro. Mid-session, Claude can emit `<lesson-candidate>` XML tags inline (no tool call) to flag knowledge to capture later — the candidate sweep at end-session reviews them.

Three tiers (active/promoted/core) with reinforce-driven promotion and idle-decay retirement. Core lessons (≤5) are loaded in full on every `start-session`; active lessons (≤30) are loaded as a slim digest and matched against the picked task's anchors via `backlog_lesson_match`.

Triggers: "remember this", "save as a lesson", "learn this lesson", "memorize this", "this keeps happening", "we always do X here", "we got burned by this last time", "promote candidate to lesson", "review lesson candidates", "flag this session for retro".

MCP surface: `backlog_lesson_create`, `backlog_lesson_get`, `backlog_lesson_list`, `backlog_lesson_update`, `backlog_lesson_match`, `backlog_lesson_digest`, `backlog_lesson_reinforce`, plus four candidate-management tools.

### New: issues — bug records separate from work tasks

`taskmaster:issue` skill captures bugs distinct from work tasks. Five entry points: log-issue, flag-from-conversation, update-status, close-on-task-complete, triage-review. Severity P0–P3 with concrete decision rules (data-loss/security → P0, blocks core flow → P1, has workaround → P2, cosmetic → P3). Lifecycle: `open → investigating → fixed/wontfix/duplicate` with required-field gates (`fixed` requires `fixed_in_task`; `duplicate` requires `duplicate_of`).

Triggers: "log a bug", "found an issue", "this is broken", "track this defect", "log this defect", "file a bug", "report a bug", "this is a bug", plus all the lifecycle phrases.

`end-session` now prompts to close any of the just-completed task's `related_issues` that are still open or investigating. `start-session` surfaces the top 10 open issues by severity.

MCP surface: `backlog_issue_create`, `backlog_issue_get`, `backlog_issue_list`, `backlog_issue_update`, `backlog_issue_resync`. Index unbounded (issues are bounded by reality, not policy).

### New: recap + snapshots — project-state delta across sessions

`backlog_recap` shows what changed in the project since the last snapshot — tasks added, status moves, fixed issues, phase advances. Distinct from `backlog_last_session` (your work log). Both render at session start on v3 backlogs.

The PreCompact hook ships with the plugin (no per-project setup) and writes `.taskmaster/snapshots/last.json` before context compaction so the next session's recap reflects pre-compaction state. Cost: ~100ms wall, zero in-context tokens. Test coverage in `tests/test_precompact_hook.py`.

### New: auto-mode — state-machine task driver

`taskmaster:auto-task` drives one task through PICK → SPEC_REVIEW → IMPLEMENT → TEST → REVIEW_GATE → HANDOVER_STUB → END_SESSION. Cursor persistence in `.taskmaster/auto/state.json` survives compaction (PreCompact hook flushes state). Failure taxonomy (tests-failed/spec-rejected/blocked/crashed/user-aborted) with recovery handovers.

`taskmaster:auto-epic` orchestrates one task per subagent for every todo task in an epic. Subagent isolation keeps orchestrator main-context cost roughly constant — a 10-task auto-epic accumulates ~2,250 tokens of orchestrator state, not 10× a full implementation context.

`taskmaster:auto-phase` orchestrates one auto-epic per epic for an entire phase. Three-level subagent isolation. Failure mode is configurable per run (gates / unattended / continue-on-fail).

MCP surface: `backlog_auto_start`, `backlog_auto_status`, `backlog_auto_advance`, `backlog_auto_complete_task`, `backlog_auto_finish`, `backlog_auto_abort`.

### New: viewer redesign + edit-in-UI

The dashboard, kanban, task detail, and entity modal screens are a full rewrite (`viewer/`). Inline-field editing on every editable surface with 600ms autosave debounce. ETag/If-Match concurrency prevents lost writes; conflict banner surfaces 409 with Keep-mine / Use-server choices. Server-side validation rejects unknown epics/phases, dependency cycles, and bad enums with structured 422 responses.

Field renderers: TextField, MdField, EnumSelect, NumberField, DateField, ChipInput, RelationPicker. All composable via the shared `h()` factory. Schema validation runs the same rules client and server. Entity modal shell drives create/edit for tasks today; lessons/handovers/issues follow.

The kanban supports drag-to-status with auto-stamping `started`/`completed` dates. Status transitions touch only the involved task; ETag rejects cross-session conflicts. Test coverage: ~107 JS unit tests across 14 files.

### New: skills layer for v3

The connective tissue between conversation and v3 entities. Authored against patterns mined from a 457-task v1 backlog (CodeMaestro). Twelve skill files shipped or retrofit:

- New entity skills: `taskmaster:handover`, `taskmaster:lesson`, `taskmaster:issue`, `taskmaster:migrate-v3`.
- Retrofit existing skills: `start-session` (recap diff, lesson digest, core lessons, latest handover, top open issues), `pick-task` (continue-this-task trigger, related handovers/issues/lessons), `end-session` (auto-handover offer, lesson candidate sweep, issue-close-on-complete, archive sweep), `init-taskmaster` (v3 schema choice, gitignore additions, capabilities tour), `spec-review` (persists approved spec into task body), `taskmaster` router (full v3 routing table + disambiguation block).
- Quality pass: `auto-task`, `auto-epic`, `auto-phase` wired to real handover/lesson/issue tools; doc-vs-runtime mismatches in archival and failure-aggregation policy resolved.

### MCP surface expansion

72 MCP tools total — 43 v3-specific (handover/lesson/issue/recap/snapshot/auto/migrate/recap-set/snapshot-diff). Regression guards in `tests/test_mcp_v3_exposure.py` lock the surface in: 32 parameterized cases verifying every v3 tool name is registered on the built server (catches the v3-release-001-class incident where `@mcp.tool()` was in source but the running 1.11.1 server didn't expose the tool).

### Other improvements

- ThreadingHTTPServer in dev mode (multi-tab kanban no longer blocks).
- Shared time-format helper unifies date renders across the viewer.
- Pluralization helper sweep.
- Empty-state copy convention with a shared component.
- Sanitized `${id}` in not-found innerHTML across detail screens (XSS hardening).
- Recap snapshot/diff plumbing reconciliation — receipts now match the narrative.
- v3 `tasks/<id>.md` body sections handle long unbroken strings without horizontal overflow.
- Task transitions auto-stamp `started`/`completed` timestamps; dashboard date columns stay accurate without manual entry.
- Issue index pre-sorted P0 → P3 so the dashboard top-10 stays meaningful.
- `backlog_handover_list` accepts `task_id`/`session_kind`/`since` filters (used by pick-task and start-session retrofits).

### Test count

408+ Python tests, ~107 JS unit tests across the v3 viewer. 70+ new tests added in the v3-skills + v3-release work (lint guards, MCP exposure, hook integration, server-level handover read coverage). Three known pre-existing failures in `test_server_task_detail.py` are tracked as `v3-polish-035` and unrelated to v3.

### Acknowledgements

Thanks to dogfooding across the v3 viewer + edit-in-UI batch (16 tasks) and the v3-skills layer (12 tasks) — every concrete bug found and fix made along the way is tracked in `v3-polish` epic.

---

## 1.11.x and earlier

See `git log --oneline plugins/taskmaster/.claude-plugin/plugin.json` for the
full version history. Highlights:

- 1.11.0 — Spec-review skill + viewer pass.
- 1.10.0 — Auto-task / auto-epic / auto-phase skill drafts (replaced in 2.0).
- 1.9.0 — Anchors, budget warning, staleness tracking, auto-summary.
- 1.8.0 — Worktree-submodule auto-init hook.
- 1.7.0 — Worktree-hang fix, priority rename, version badge.
- 1.5.0 — Tracked-mode `.taskmaster/` directory option.
- 1.4.0 — `init-taskmaster` skill.
- 1.0.0 — Initial release.
