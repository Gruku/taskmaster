# Changelog

All notable changes to the taskmaster plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html) — major bumps
indicate schema breaks or removed surfaces.

---

## [Unreleased]

---

## 3.11.0 — Entity detail modals + settings (2026-05-29)

### Added

- **Entity detail modals** — task/epic detail opens in a modal overlay by
  default (settable to full-page via the new `#/settings` screen); delegated
  capture-phase link interception keeps real `href` attributes (refresh /
  new-tab still work); history-aware close via Back / Esc / scrim click;
  kanban epic ↗ entry point.

---

## 3.10.0 — Epic viewer surface (2026-05-29)

### Added

- **Epic detail screen** — `GET /api/epic/<id>` HTTP endpoint (load_v3-backed), `mountEpicDetail` viewer component (rollup + components + design-lock + narrative), `/epic/<id>` detail route, `/epics` list screen, and sidebar Epics entry. (Spec C1b)

---

## 3.9.1 — Git read-path no longer leaks index.lock (2026-05-28)

### Fixed

- **`_run_git` now passes `--no-optional-locks`** (B-059). The read-only feature-detection helper was running `git status`-class commands that take `.git/index.lock` to refresh the stat-cache. On a slow repo (e.g. Windows Defender scanning a large monorepo) the call could exceed the 10s timeout; Python killed the child git, which left a 0-byte `index.lock` orphan that silently blocked every future commit until cleared by hand. The flag stops git from taking the lock at all, killing the whole class of leak across all call sites.

---

## 3.9.0 — Epics/phases as doc-bearing entities + bughunt fixes (2026-05-28)

### Added

- **`backlog_epic_status` rollup tool** — per-epic status summary with per-component rollup and an attention surface that flags blocked tasks (and surfaces archived tasks in the rollup).
- **Epic `components` block + `design_status`** — epics declare named components; tasks bind to them via a `component` field validated against the parent epic's component keys (reserved/self-ref keys guarded).
- **`design_change` flag with locked-epic teeth** — gates mutations against epics whose design is locked.
- **Phase `docs` field** — brings phases to epic/task parity for attached documentation.
- **Two-tier storage for epics/phases** — heavy fields split into per-entity body files on save, merged back on load; backward-compatible with single-file entities and auto-migrates on first mutation.

### Fixed

- **Viewer:** closed XSS and crash bugs in the recap and bugs screens (B-052/053/054/056).
- **Lifecycle:** terminal-state transitions guarded across task/bug/decision (B-025/026/049/050).
- **Linear sync:** robust error classification, bounded retries, crash-safe drain (B-027..B-032).
- **Error boundaries:** structured-error handling for project.yaml, bug-scan, and slim sections (B-016/017/018/019/024/042).
- **Auto-mode:** unique session ids, single-outcome invariant, stopped-run filter (B-046/047/051).
- **Linking:** link auto-detection, query/list contracts, archive-reason key (B-033/035/036/039/040/048).
- **Frontmatter:** render/parse round-trip is now idempotent (B-007).

### Notes

- 3.8.3 was a version-only bump with no changelog entry; its delta is folded into this release.

---

## 3.8.2 — Skill-flow deprecation cleanup (2026-05-21)

### Changed

- Three skill files updated to call `backlog_handover_list(status="open", limit=1)` instead of the deprecated `backlog_handover_latest`:
  - `skills/handover/SKILL.md` step 9 (confirm message).
  - `skills/handover/references/supersession.md` step 1 of the interim supersession algorithm.
  - `skills/taskmaster/references/routing-table.md` "Show last handover" row.
- Tool itself (`backlog_handover_latest`) is unchanged — still emits its deprecation warning for backwards compatibility.

---

## 3.6.1 — Ceremony glance-first redesign (Plan D) (2026-05-18)

### Changed

- `start-session` default mode is now a ~800–1,000 token glance: slim `backlog_status` + top-5 open handovers + 1-line counts. Full ceremony (recap diff, lesson digest, core lessons, all issues, last session) is available via `--deep`.
- `pick-task` default mode is now a ~600–800 token glance: slim task + deps + open handovers for task + matched lesson IDs+tldrs (no full bodies) + filtered issues + linkage pills. Full ceremony (full task body, full lesson bodies, blast radius, handover context) is available via `--deep`.
- Deep ceremony content for both skills moved to `references/deep-mode.md` per skill — loaded only when `--deep` is invoked.
- Mid-session deepening documented in taskmaster router: "show me HND-012" → `backlog_handover_get`; "read the plan" → `backlog_get_task(sections=["plan"])` — no skill re-invocation.
- Lint tests added: `test_start_session_skill_lint.py`, `test_pick_task_skill_lint.py`.
- Smoke tests added: `test_start_session_smoke.py`, `test_pick_task_smoke.py`.
- `backlog_handover_latest` is deprecated in skill flows; replaced by `backlog_handover_list(status="open")` (requires Plan B).

---

## 3.6.0 — Skill content slimming (Plan E) (2026-05-17)

- Every taskmaster SKILL.md is now within its token budget (800–1,500 tokens per skill).
- Deep-walkthrough content extracted to `references/<topic>.md` per skill — loaded on demand, not eagerly.
- All skill `description` fields trimmed to ≤60 words (exception: `issue` at ≤70 words to preserve 14 required trigger phrases).
- Eager skill catalog reduced from ~4,000 to ≤2,500 tokens.
- New lint infrastructure: `skill_budget_helper.py` + parametrized body and description tests for all 16 skills.
- `start-session` and `pick-task` body budgets lint-checked but marked xfail pending Plan D merge.
- `pick-task` description trimmed to 54 words.

Spec: `docs/superpowers/specs/2026-05-15-taskmaster-progressive-disclosure-design.md` §5.
Plan: `docs/superpowers/plans/2026-05-16-taskmaster-progressive-disclosure-plan-e-skill-slimming.md`.

---

## 3.5.0 — Programmatic Linking (Plan C) (2026-05-17)

### Added

- **Typed unified `links: [{type, target}]` schema** on every entity (tasks, issues, lessons, handovers, ideas). Replaces the proliferation of `related_issues`, `related_lessons`, `depends_on`, `related_tasks`, `fixed_in_task`, `duplicate_of`, `supersedes`, `superseded_by` fields with one symmetric, server-managed array per entity.
- **13 canonical link types** with full reverse-pair table — see `plugins/taskmaster/taskmaster_v3.py::REVERSE_TYPE`. Types: `depends_on`/`blocks`, `fixes`/`fixed_in_task`, `informed_by`/`informs`, `supersedes`/`superseded_by`, `duplicate_of`/`duplicates`, `references`/`referenced_by`, and `relates_to` (its own inverse).
- **Server-managed symmetric sync**: writing a link on one side auto-writes the inverse on the peer via `sync_inverse(source, target, type, remove=False)`. Removal is symmetric too.
- **Auto-detection of inline ID mentions** (`T-001`, `[[T-001]]`, `@T-001`) on every entity save — materializes as `references` links with `referenced_by` inverse on the target. Opt out per entity via `auto_link: false` frontmatter. Existing explicit link types are never overwritten.
- **Cycle detection on `depends_on` chains** — `backlog_link_create` rejects writes that would form self-cycles, 2-node, or N-node cycles via DFS with grey/black coloring (see `find_cycle` / `would_create_cycle` in `taskmaster_v3.py`).
- **New MCP tools** (Plan C / spec §6):
  - `backlog_link_create(source, target, type, note="")` — validates type, domain, cycle, and writes both sides
  - `backlog_link_remove(source, target, type="")` — drops both sides; omit `type` to remove all links between the pair
  - `backlog_link_query(source="", target="", type="", depth=1)` — JSON edge list with optional transitive traversal
  - `backlog_link_validate()` — reports orphans, asymmetric pairs, depends_on cycles, and archived-target warnings
  - `backlog_link_reconcile()` — fills missing inverses; reports unfixable orphans
- **Slim `_get` views** (`backlog_get_task`, `backlog_handover_get`, `backlog_issue_get`, `backlog_lesson_get`, `backlog_idea_get`) emit one grouped `links:` block. `expand_links=true` swaps bare IDs for `{id, tldr}` pills.
- **Viewer**: shared `link-pills.js` renderer; new "Links" panel in the task-detail right rail; `issue-detail`, `lesson-detail`, `ideas` sidebars use unified link-pills instead of per-field rendering. Legacy-field fallback ensures unmigrated projects still render correctly. UI honors project conventions: no left-colored rails, no motion on hover, no box-shadow elevation.
- **`scripts/migrate_links.py`** — one-shot, idempotent migration: translates legacy fields to typed `links`, drops the old fields, runs `backlog_link_reconcile` to fill inverses. Reports JSON summary of `migrated.<kind>` counts plus `reconcile.fixed` / `reconcile.unfixable`.
- **Read-fallback shim** — `read_entity_anywhere(..., fallback=True)` (default) synthesizes a virtual `links` array from legacy fields when the field is absent. Used by the slim `_get` views and the viewer so unmigrated projects work seamlessly. Pass `fallback=False` to opt out (migration script uses this).

### Hooked into entity write paths

`auto_link_on_save` runs at the end of every entity-creating/updating MCP tool: `backlog_handover_create`, `backlog_issue_create`, `backlog_issue_update`, `backlog_idea_create`, `backlog_idea_update`, `backlog_lesson_create`, `backlog_lesson_update`, and `backlog_update_task` (on `notes`/`review_instructions` field changes).

### Migration

1. Run `python -m plugins.taskmaster.scripts.migrate_links --root <project>` (use `--keep-legacy` to keep old fields readable alongside the new `links` array).
2. Inspect the JSON summary — `reconcile.unfixable` lists orphan links pointing at deleted entities (manual cleanup).
3. Run `backlog_link_validate` after the migration; expect `orphans == []`, `asymmetric == []`, `cycles == []`.
4. Old `related_*` / `depends_on` / `fixed_in_task` / `duplicate_of` / `supersedes` / `superseded_by` fields are still read as a fallback when `links` is absent. They are dropped by `migrate_links` and will be removed entirely from read-fallback in a future release.

### Breaking changes

- After migration, entities no longer carry the legacy linkage fields. Callers that read `task["depends_on"]` directly must switch to `entity_links(task)` or filter `task["links"]`.
- `backlog_get_task` slim mode emits a grouped `links:` block in addition to (or, after migration, in place of) per-field linkages.

### Out of scope

- **Spec §6E (computed/derived linkages — commit → issue, files → lesson)** is explicitly deferred from Plan C; auto-detection here only materializes inline ID mentions the user wrote, not git/path/semantic inferences.
- Transitive graph algorithms beyond `backlog_link_query(depth=N)` (shortest-path, connected-components, etc.).

Spec: `docs/superpowers/specs/2026-05-15-taskmaster-progressive-disclosure-design.md` §6.
Plan: `docs/superpowers/plans/2026-05-16-taskmaster-progressive-disclosure-plan-c-programmatic-linking.md`.

---

## 3.4.0 — Parallel Handovers (Plan B) (2026-05-17)

### Breaking changes

- `HANDOVER_STATUSES` enum renamed: `"todo"` → `"open"`, `"in-progress"` → `"open"`, `"done"` → `"closed"` or `"superseded"` depending on context. Run `scripts/migrate_handover_statuses.py` against any existing project before upgrading.

### New features

- **Smart auto-close rule:** When a task transitions to `done` or `archived`, open handovers that reference it are auto-closed only when all three criteria are met: (1) all `task_ids` are done/archived, (2) `next_action` is empty or references only done/archived tasks, (3) `session_kind` is `"task-complete"` or absent. Otherwise the handover stays open and is flagged with a human-readable `flag_reason`.
- **`flag_reason` field:** Flagged-but-open handovers carry a `flag_reason` string in frontmatter, surfaced in `backlog_handover_list` output with a `▸ FLAGGED:` prefix so start-session glance can show them inline.
- **`smart_auto_close_handovers()`:** New data-layer function in `taskmaster_v3.py`. Called automatically by `backlog_complete_task` and `backlog_archive_task`.
- **`flag_open_reason()`:** New data-layer helper. Returns the `flag_reason` string for a flagged open handover, or `None` if absent or already closed.
- **`migrate_handover_statuses()`:** New data-layer migration function. One-shot, idempotent. CLI at `scripts/migrate_handover_statuses.py`.
- **`task-complete` session kind:** New canonical handover kind eligible for smart auto-close.

### Deprecations

- `backlog_handover_latest()` is deprecated. It now emits a deprecation notice in its output and delegates to `backlog_handover_list(status="open", limit=1)`. Use `backlog_handover_list(status="open")` for all in-flight tracks. Will be removed in the next major release.

### Internal

- `_HANDOVER_INDEX_FIELDS` now includes `"flag_reason"` so flagged entries propagate into `backlog.yaml` index.
- `apply_supersession()` sets `status = "superseded"` (was `"done"`).
- `backfill_handover_status()` stamps `status = "open"` (was `"done"`) on legacy handovers lacking the field.
- `_default_handover_status()` returns `"closed"` for `auto-stage` kind (was `"done"`), `"open"` for all others (was `"todo"`).
- `_handover_to_item()` action_class now uses `status == "open"` (was `"todo"`) for resume routing. **Merge note:** keeps master's looser rule (any-age open → resume) and the `RESUME_RECENT_DONE_CAP` promotion ladder from 3.3.0 polish; does not adopt the `age <= 7` constraint Plan B had on the feature branch.
- `mark_task_handovers_complete()` removed — replaced by `smart_auto_close_handovers()`.
- `mark_task_handovers_resumed()` removed — open handovers stay open under the new model; no transition needed on task pick.

---

## 3.3.0 — Continuity dashboard polish + Plan A reconcile (2026-05-17)

Reconciles `plugin.json` (was stale at 3.1.1) with the 3.2.0 work already
on master and adds the continuity polish below. One minor bump folds both.

### Added

- **Continuity dashboard — Resume sub-grouping.** Backend `_handover_to_item` now surfaces all open handovers (todo / in-progress) at any age and promotes the latest 5 done handovers via `RESUME_RECENT_DONE_CAP`; older done handovers stay in ambient. Frontend `resume-rail.js` splits the bucket into **Open** (full rows) and **Recent** (compact rows). Resolves the "only one handover ever shows up" miss in the post-merge dashboard.
- **XML tag rendering in the continuity dashboard.** New `viewer/js/lib/xml-render.js` recognises `<lesson-candidate>`, `<thinking>`, `<example>`, `<system-reminder>`, `<decision>`, `<issue>`. Inline chips replace tag text in item-row `next` / `where`. Click a handover or decision row → the body is fetched and rendered inline below the row with block-level tag panels.
- **`GET /api/handover/<id>`** in `backlog_server.py` to back the row-expand fetch. Path regex matches the decision endpoint pattern (`[A-Za-z0-9_-]+`).
- **`setActive()` imperative API on the view-switcher** so the Action / Time / Entity highlight follows clicks (was stuck on the initial selection because the topbar mount wasn't re-rendered between view changes).
- **`.co-dash` added to `shell.css` scroll-policy A**, restoring viewport-fit parity with `.dash` / `.issues` / `.lessons`. The body now scrolls within its slot instead of growing past or leaking into sibling screens.

### Notes

- `_handover_to_item` projection gains a `status` field for downstream consumers (used by the Resume rail's Open / Recent split).
- xml-render uses a stateless detector regex plus a fresh `/g` instance per call so `lastIndex` can't leak between nested or concurrent callers.

Task: `v3-polish-055`. Spec embedded in task notes.

---

## 3.2.0 — Progressive Disclosure Foundation (2026-05-16)

### Added

- Required `tldr` field on tasks, issues, lessons, ideas (handovers already had it). Auto-generated from body's first sentence when missing on create; flagged with `tldr_autogen: true`.
- `next_step` field on `backlog_add_task` and `backlog_update_task` — persisted and exposed in slim view.
- Optional `task_id` kwarg on `backlog_add_task` for caller-supplied IDs (e.g., in tests). Empty/unspecified preserves auto-gen (`{epic}-NNN`) behavior.
- Slim-by-default mode on every `_get` MCP tool: `backlog_get_task`, `backlog_handover_get`, `backlog_issue_get`, `backlog_lesson_get`, `backlog_idea_get`. Returns frontmatter + tldr + extras + bare-ID linkages (typically ~150 tokens; ≤200 tokens for tasks with multiple links and open handovers). Use `verbose=true` for full body, `sections=[...]` for surgical retrieval, `expand_links=true` for `{id, tldr}` pills.
- Slim-by-default on `_list` tools: `backlog_list_tasks`, `backlog_handover_list`, `backlog_issue_list`, `backlog_lesson_list`. Heavy fields excluded.
- `backlog_status` slim default (~1.8K chars) — omits archived, caps next-up at 5. Use `verbose=true` for full dashboard.
- `backlog_lesson_match` slim default returns `{id} — {tldr}` pills. Use `verbose=true` for full summary line (kind + reinforce_count + title).
- `backlog_validate` now warns on entities missing `tldr` (advisory, not blocking). Run `python -m plugins.taskmaster.scripts.backfill_tldr --root <path>` to fix.
- Internal helpers in `taskmaster_v3.py`: `extract_tldr`, `backfill_tldr`, `slim_entity`, `resolve_sections`, `expand_link_ids`, `build_tldr_index`, plus `CANONICAL_SECTIONS`, `SLIM_FIELDS`, `TASK_INLINE_SECTIONS`, `TASK_DOC_SECTIONS`, `TLDR_MAX_CHARS=200` constants.
- Shared `tmp_taskmaster` + `tm_epic_phase` test fixtures in `plugins/taskmaster/tests/conftest.py`.
- Migration script: `plugins/taskmaster/scripts/backfill_tldr.py`.

### Changed

- `backlog_update_task` now accepts `tldr=` and `next_step=` keyword arguments in addition to the existing `field`/`value` API. Mixed-style calls (both kwargs and field/value supplied) return an error to prevent silent data loss.

### Notes

- Foundation only: Plans B (parallel handovers), C (typed links), D (glance-first ceremonies), E (skill slimming) ship in subsequent PRs.
- The `tldr_autogen: true` flag is removed when a caller explicitly supplies a tldr via `backlog_update_task`. The corresponding `backlog_issue_update`, `backlog_lesson_update`, and `backlog_idea_update` tools do not yet accept a `tldr=` kwarg — manual frontmatter edits are needed to promote an autogen tldr to authored on those entities. To be addressed in a follow-up.
- `verbose=true` reproduces today's full-load semantics: full body, all frontmatter (now including `tldr` and `tldr_autogen` where applicable). Output formatting on `backlog_get_task` is preserved exactly; other `_get` tools include their new `tldr` field in the frontmatter block.

Spec: `docs/superpowers/specs/2026-05-15-taskmaster-progressive-disclosure-design.md`
Plan: `docs/superpowers/plans/2026-05-16-taskmaster-progressive-disclosure-plan-a-foundation.md`

---

## 3.1.0 — Ideas surface (2026-05-10)

### Added

- **New `Ideas` surface** — a lightweight per-project parking-lot for unvalidated thoughts, lighter than tasks. Per-idea YAML+markdown files at `<backlog parent>/ideas/IDEA-NNN.md` plus an append-only `IDEAS.md` chronological index. Three capture paths: explicit `/add-idea` slash skill (user-driven), inline `<idea-candidate>` XML tag for ambient capture (swept by end-session, committed with `status="candidate"`), and confidence-threshold auto-log when the user states a sharp idea (Claude calls `backlog_idea_create` directly and announces inline).
- **Three new MCP tools**: `backlog_idea_create`, `backlog_idea_list` (also serves as get when filtered by `idea_id`), `backlog_idea_update` (covers archive, promote, body edits, status changes). Deliberately minimal surface — no separate get/archive/promote/resync wrappers.
- **HTTP endpoints** `GET /api/ideas` and `POST /api/ideas` for the viewer. `GET` accepts `status`, `tag`, `archived`, `related_task`, `limit`, `summary` query params; `POST` validates `title` and accepts the same write-side fields as the MCP wrapper.
- **Viewer Ideas screen** (`viewer/js/screens/ideas.js`) — new top-level screen alongside Issues / Lessons. Status + tag chip filters (freeform values discovered from data, `chipClickNext` helper per L-001), archived toggle off by default, list/detail toggle, frontmatter sidebar with click-through links to related tasks/issues/lessons and `promoted_to`. Topbar carries a primary "+ New Idea" button that opens a modal posting to `/api/ideas`.
- **`taskmaster:add-idea` skill** for explicit user-driven capture. Slash form (`/add-idea …`) and natural-language form ("save this as an idea: …"). Optional flags `--tags`, `--status`, `--related-task`, `--related-issue`, `--related-lesson`.
- **End-session sweep** for `<idea-candidate>` tags. Scans the in-context transcript and commits each tag directly via `backlog_idea_create` with `status="candidate"` (no per-item draft-and-approve gate per the standing project rule). Reports counts in the wrap-up summary.

### Fixed

- **Lesson-candidate flow no longer fires silently.** Root cause: the emit guidance for `<lesson-candidate>` lived only in `lesson/SKILL.md`, which is loaded only when the lesson skill is explicitly invoked. During ordinary coding, Claude never saw the trigger heuristic. Fix: a new top-level `## Mid-session behavior` section in `start-session/SKILL.md` (loaded for every v3 session) documents both the lesson-emit heuristics (repeated correction / bug second-encounter / architectural ground rule) and the idea-emit heuristics (path A skip / path B fuzzy candidate / path C sharp auto-log) with a one-line decision tree. Same edit shipped both halves.
- **Race-safe `IDEA-NNN` allocation.** `write_idea` now uses `Path.touch(exist_ok=False)` bump-and-retry to atomically reserve the next id, eliminating the read-then-write race. Bounded at 64 attempts.
- **Viewer detail body / archived toggle.** Caught in post-merge cross-cutting review by Codex: `/api/ideas` GET returned summaries (no body) and defaulted `archived=false`, so the viewer's detail pane was always empty and the archived toggle filtered over data it didn't have. Fix: `list_ideas` gains a `summary: bool = True` kwarg (also closes a spec drift), HTTP GET defaults `summary=False` for the viewer, viewer fetches with `?archived=true&summary=false`. MCP `backlog_idea_list` keeps `summary=True` default to preserve its compact string output for scripted callers.
- **Viewer cache freshness + error UI.** Ideas screen now always refetches on `mount()` (cache is soft fallback only), and surfaces an inline error banner on initial-load failures instead of silently rendering an empty state.

### Tracked follow-ups

- `ISS-011` — `parse_frontmatter`/`render_frontmatter` body trailing-newline asymmetry. The local rstrip in `read_idea` is the workaround until the parse helper is normalized.

---

## Unreleased — `.claude/` → `.taskmaster/` consolidation

### Changed

- `backlog_init` now writes everything (config, backlog.yaml, PROGRESS.md, artifact subdirs) into `.taskmaster/` regardless of project. The `location` parameter is retained for backwards-compat but accepts only `"tracked"`; passing `"hidden"` returns an error pointing at `backlog_canonicalize_layout`.
- `CONFIG_PATH` moved from `.claude/taskmaster.json` to `.taskmaster/taskmaster.json`. Existing projects with config at the legacy path keep working — the resolver reads `.taskmaster/taskmaster.json` first, then falls back to `.claude/taskmaster.json` and emits a one-shot deprecation warning per process.
- The path resolvers in `backlog_server._resolve_paths`, `taskmaster_v3._resolve_artifact_root`, and `hooks/snapshot.py` now check `.taskmaster/` before `.claude/`. When the legacy layout is matched, a deprecation warning fires once per detail.
- `init-taskmaster` skill no longer asks where to store the backlog — `.taskmaster/` is the only target.
- Skill copy in `handover` and `end-session` updated to reference `.taskmaster/handovers/` (the actual writer location for canonical-layout v3 projects).

### Migration

Existing `.claude/`-layout projects (both v2 and v3) keep working under the deprecation shim. Run `backlog_canonicalize_layout` (already shipping) to consolidate. The shim will be removed in a future major release.

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
