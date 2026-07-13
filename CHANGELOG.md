# Changelog

All notable changes to the taskmaster plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/spec/v2.0.0.html) — major bumps
indicate schema breaks or removed surfaces.

---
## 5.0.0

**Team relayout — sharded per-task storage (`schema_version: 4`).** Every task field now lives in `tasks/<id>.md`; the slim `backlog.yaml` contains only project metadata, phases, and epic definitions. Task order is fractional and epic membership is declared by each task's `epic:` field.

Saves are dirty-scoped and merge-aware: only changed task files are written, while concurrent disk changes are preserved through a per-field three-way merge. IDs allocate by directory scan, and typed-link reads and writes now dispatch through the active schema.

Machine-local state (`viewer.json`, `auto/`, `PROGRESS.md`, and derived `meta.updated`) lives under `.taskmaster/local/`. Existing v3 projects remain readable and writable and receive a migration prompt; migrate idempotently with `backlog_migrate_v4`. New projects use v4 by default. An MCP server restart is required after upgrading.

This is epic 1 of the team-collaboration design; identity, synchronization, team IDs, and viewer collaboration land in later releases.

## 4.4.1

**Codex marketplace bundle fix.** Documents and versions the standalone payload consumed by `claude-tools`' generated Codex distribution. Codex marketplace repository installs do not materialize Git submodules, so pointing the catalog directly at the Taskmaster submodule produced an empty installed plugin with no skills or MCP servers. The parent marketplace now packages a generated, regular-file distribution sourced from this repository.

No Taskmaster runtime or workflow behavior changes.


## 4.4.0

**Native Codex marketplace packaging.** Adds a `.codex-plugin/plugin.json` manifest that exposes Taskmaster's already-verified harness-agnostic MCP core and all 17 assistant-neutral workflow playbooks directly through the Codex plugin marketplace. The Codex package uses a plugin-relative MCP working directory, so marketplace installs no longer need the older adapter's manual `{{TASKMASTER_HOME}}` substitution. Claude-only enforcement hooks remain intentionally outside the Codex capability tier; workflow gates remain advisory there, as documented in the capability matrix.

The existing `adapters/codex/` files remain useful for manual/source-checkout installs. Marketplace installation is now the preferred Codex path.

## 4.3.0
**MCP write-surface consolidation (tm-audit-020).** Merges skill-mediated write families behind action-dispatched tools and collapses heavy update signatures, cutting the MCP surface from 95 to 76 tools and trimming per-tool schema tokens loaded into every session. All consolidated families are skill-mediated (the taskmaster:linear / :decision / :handover skills and the note/link routing tell the model exactly what to call), so this ships as a minor by explicit user convention — no alias shims; skills update in the same release.
- **Merged (per-verb tools removed, one action-dispatched tool added):**
  - `backlog_linear` replaces `backlog_linear_probe`, `backlog_linear_bootstrap_apply`, `backlog_linear_link`, `backlog_linear_unlink`, `backlog_linear_list`, `backlog_linear_show`, `backlog_linear_status`, `backlog_linear_retry` (action ∈ probe/bootstrap_apply/link/unlink/list/show/status/retry).
  - `backlog_note` replaces `backlog_note_create`, `backlog_note_list`, `backlog_note_get`, `backlog_note_update`, `backlog_note_archive` (action ∈ create/list/get/update/archive).
  - `backlog_link` replaces `backlog_link_create`, `backlog_link_remove`, `backlog_link_query`, `backlog_link_validate`, `backlog_link_reconcile` (action ∈ create/remove/query/validate/reconcile).
  - `backlog_decision` replaces `backlog_decision_list`, `backlog_decision_get`, `backlog_decision_resolve`, `backlog_decision_drop`, `backlog_decision_update` (action ∈ list/get/resolve/drop/update). `backlog_decision_create` stays a distinct tool (heavy, distinct schema).
- **Param collapse (tool kept, signature changed):**
  - `backlog_issue_update`, `backlog_bug_update`, `backlog_idea_update` now take `(id, field, value)` like `backlog_update_task`. One field per call; list fields (e.g. `components`) take a comma-separated value; `archived` takes `"true"`/`"false"`. Lifecycle-paired updates (e.g. `status=fixed` needs `fixed_in_task`) are two sequential calls — set the companion field first; validation reads the merged on-disk state.
  - `backlog_add_task` keeps the common path top-level (title, epic, phase, priority, tldr, notes, next_step, depends_on, bundle) and moves rarely-set params into an `options` dict: `docs`, `sub_repo`, `stage`, `estimate`, `anchors`, `task_id`, `area`.
  - `backlog_handover_create` moves `branch`, `tip_commit`, `context_size_at_write`, `review_reason` into an `options` dict.

Direct MCP callers of the removed per-verb tools must switch to the merged tool with an `action` argument; direct callers of the collapsed update tools must switch to `field`/`value`. Skill-mediated callers are updated in this release.

**MCP server renamed `taskmaster` → `tm` (tm-audit-023).** The `.mcp.json` server key is now `tm`, so tool names become `mcp__plugin_taskmaster_tm__backlog_*` (Claude Code namespaces plugin servers as `plugin:<plugin>:<key>`; the doubled name is gone). Permission allowlists or configs referencing `plugin_taskmaster_taskmaster` must be updated.

**Direct-change lane (tm-audit-024).** Two-tier routing replaces "invoke taskmaster:taskmaster for ANY task-related request": small direct changes (user asked outright, one sitting, no open design decisions, ~≤3 files) skip skills and backlog writes entirely — just make the change and commit; everything else routes through the tracked lane, and a direct change that grows escalates with a one-line note. Encoded in the SessionStart notice (548 chars, net smaller), the router playbook, and the taskmaster skill description.

**Unified MCP read conventions (tm-audit-007).** One slim toggle (`verbose` — `backlog_idea_list`'s `summary` renamed), `limit` on every list surface (default 50, `limit=0` = all, standard "…N more" overflow footer), one error shape (`Error:` prefix — lowercase `error:` sites swept), `note` added to SLIM_FIELDS, and `expand_links` honored uniformly in slim + verbose reads.

**Skill descriptions re-trimmed −25% (tm-audit-026).** All 17 skill frontmatter descriptions tightened (6,016 → 4,504 chars) with every routing-critical clause and trigger phrase preserved.

---

## 4.2.0
**Project-structure scan and recap removed.** Two non-working features culled (2026-07-10). Note: breaks direct MCP callers of the four removed tools; shipped as a minor by explicit user decision.
- **Removed:** `backlog_project_structure` MCP tool, the `/api/project-structure` HTTP endpoint, and the viewer Structure > Project screen. The git-scanning subsystem behind them could hang on large or slow repos; the feature is culled. The `backlog_project_*` manifest tools (`project.yaml`) are unaffected.
- **Removed:** the recap + snapshot subsystem — `backlog_recap`, `backlog_snapshot`, and `recap_list` MCP tools, the recap/snapshot-diff core, the PreCompact snapshot hook, the `/api/recap/*` and `/api/snapshots/diff` HTTP endpoints, and the viewer Recap screen plus its sessions-timeline integration. Recap never produced a recap in practice and the snapshot machinery existed only to feed it. Sessions and handovers are unaffected. Existing `.taskmaster/recaps/` and `.taskmaster/snapshots/` directories on user disks are left untouched and simply ignored.

Direct MCP callers of the four removed tools must drop those calls; there are no replacements.

---

## 4.1.0
**Areas + finite epics.** New first-class Area entity (long-lived subsystem: `backlog_area_create/get/list/update`, sidecar files under `.taskmaster/areas/`, no status lifecycle). Epics are now finite: `backlog_add_epic` requires `done_when` (an epic that can't say when it's done is an area); epics whose tasks are all done surface as "closeable" in backlog_status, backlog_epic_status, and the viewer. **Breaking for direct MCP callers:** `backlog_add_epic` now takes `done_when` as a required argument — existing scripted callers must pass it. Tasks and epics carry an optional `area` field (validated against existing areas); kanban and table gain an Area filter/group axis. `backlog_validate` warns on legacy epics without `done_when`. Existing backlogs load unchanged. Second of four 4.x epics (lessons removal → **areas** → release trains → migration tooling).

---

## 4.0.0
**BREAKING — Lessons subsystem removed.** The 11 `backlog_lesson_*` MCP tools, lesson candidates/markers (`<lesson-candidate>`), `related_lessons`/`informed_by` link fields, `lessons_fired` recap stat, viewer Lessons screens, and the `taskmaster:lesson` skill are gone. Durable knowledge now lives in each assistant's own memory system (session insights) and repo instruction files like CLAUDE.md/AGENTS.md (cross-assistant knowledge). Existing `.taskmaster/lessons/` files are left untouched on disk; run the new `taskmaster:migrate-lessons` skill once per project to convert them. `lessons_meta` keys in existing backlog.yaml files are ignored. First of four 4.0 epics (lessons removal → areas → release trains → migration tooling).

---

## 3.24.1 — Phase 4 editor verification (docs) (2026-07-08)

### Changed

- Capability matrix: **opencode 1.17.14 verified** (MCP via `opencode.json`
  + native AGENTS.md routing to playbooks); Cursor configured, GUI verify
  pending; Zed untested (not installed locally).
- agents-md adapter README: opencode/Cursor install gotchas (absolute uv
  path, `external_directory` permission for non-interactive runs).

---

## 3.24.0 — codex + agents-md adapters (Phase 3) (2026-07-08)

### Added

- **`adapters/codex/`** — Codex CLI adapter: `config.toml` MCP-registration
  snippet, AGENTS.md routing fragment, and 17 slash prompts (one per
  playbook; thin pointers, no duplicated workflow content).
- **`adapters/agents-md/`** — generic AGENTS.md rules fragment for
  Cursor / Zed / opencode (any AGENTS.md-reading, MCP-speaking tool).
- `check_adapter_coverage.py` now enforces adapter coverage 1:1 (codex
  prompt + agents-md reference per playbook) and scans adapters for banned
  assistant-specific tokens.

Install-location independence via the `{{TASKMASTER_HOME}}` placeholder —
see each adapter's README.

---

## 3.23.0 — standalone repo extraction (Phase 2a) (2026-07-08)

Taskmaster now lives in its own canonical repo (extracted from
`claude-tools/plugins/taskmaster` via `git filter-repo`, full history
preserved). **No MCP surface changes** — `backlog_*` tool names and the
`.taskmaster/` per-project format are unchanged.

### Changed

- **Python core moved into the `taskmaster/` package** (`taskmaster_v3`,
  `backlog_server`, `project`, `blast_radius`, `integrations/`) with absolute
  imports; the repo is now pip-installable (`pyproject.toml`).
- **Root `backlog_server.py` is an entry shim** — `.mcp.json` registration
  mechanics (`uv run ${CLAUDE_PLUGIN_ROOT}/backlog_server.py`, PEP 723 script
  mode) are unchanged.
- **`merge_gate_decide.py` / `merge_recorder_stamp.py` moved to `hooks/`**
  (tier-3 subprocess scripts owned by the hooks).
- Plugin-root resolution (`SCRIPT_DIR`, `_PLUGIN_DIR`, viewer static roots)
  now points at the repo root, one level above the package.

### Added

- `pyproject.toml` (pip consumers: Hermes, The Fold, Codex-side tooling).
- `docs/capability-matrix.md` — per-assistant tier coverage (Phase 3/4 fill it in).
- `docs/specs/` + `docs/plans/` — design spec and phase plans travel with the repo.

---

## 3.22.0

Multi-assistant generalization, Phase 1 (spec: 2026-07-06-taskmaster-multi-assistant-design.md).

- All 17 skills' workflow content extracted to assistant-neutral `playbooks/<name>/playbook.md` (+ references/templates); SKILL.md files are now thin trigger wrappers — behavior on Claude Code unchanged.
- New `playbooks/CONVENTIONS.md` (authoring/neutrality rules) and `scripts/check_adapter_coverage.py` (1:1 wrapper↔playbook mapping + banned-CC-token scan; `--strict` gate).
- Skill lint tests re-pointed at playbooks; body token budgets now cover wrapper+playbook combined.

---

## 3.21.1 — spec-review skill trimmed to budget (B-071) (2026-07-05)

### Fixed

- **`spec-review` skill body + description back within their lint budgets.**
  Since 3.20.1 (`ea9f375`) the rewritten skill exceeded its own token/word
  budgets, leaving master red on 4 lint tests (`test_skill_body_budgets`,
  `test_skill_description_budgets`, `test_spec_review_skill_lint`). Trimmed
  prose (body 1449→1294 tokens, description 66→55 words) with no loss of
  routing detail — all gate/verdict/lane semantics preserved, deep prose
  still lives in `references/`. Suite green.

---

## 3.21.0 — artifact-root hijack guard (tm-audit-001) (2026-07-04)

### Fixed

- **CRITICAL: resolvers no longer misdirect writes into the plugin source
  tree.** When the MCP server (or any caller of its path resolvers) ran with
  cwd = `plugins/taskmaster/` and no `TASKMASTER_ROOT` set, `_resolve_paths()`
  (`backlog_server.py`) and `_resolve_artifact_root()` (`taskmaster_v3.py`)
  fell through to the root-layout fallback, found the plugin's own demo
  `backlog.yaml` fixture, and treated the plugin directory as a project root
  — every lesson/note/handover/idea/issue/recap/`viewer.json` write then
  landed in plugin source instead of a project's `.taskmaster/`. Both
  resolvers now raise `RuntimeError` unconditionally (checked before any
  fallback branch, so the guard can't go dead once a `.taskmaster/` subdir
  exists next to the plugin) whenever `ROOT`/cwd resolves to the plugin's own
  source directory. Fails loud — surfaces as an MCP tool error — instead of
  silently misdirecting writes.

### Changed

- Relocated the demo/screenshot fixture `plugins/taskmaster/backlog.yaml` to
  `plugins/taskmaster/tests/fixtures/visual-polish-test/backlog.yaml`, out of
  the resolver's blast radius. No code imported the old path.
- `.gitignore` backstop: `plugins/taskmaster/{lessons,notes,handovers,ideas,
  issues,recaps}/` and `plugins/taskmaster/viewer.json` can no longer be
  accidentally committed if a dev/dogfood run regenerates them before the
  guard is deployed everywhere.

---

## 3.20.2 — merge-gate reads v3 heavy gates; get_task is a pure read (2026-07-04)

Two bug fixes from the tm-audit epic, merged together.

**tm-audit-002:** `merge_gate_decide.py` read `gates` straight off the
slim `backlog.yaml` task dict, but `gates` is a HEAVY field that lives only
in `tasks/<id>.md` frontmatter on schema v3 — the merge-gate hook could
never see a verdict `backlog_record_gate` actually wrote, and always fell
through to "no review-gate has been run" regardless of reality.

**tm-audit-003:** `backlog_get_task` mutated and full-saved `backlog.yaml`
on every read (`last_referenced` bump), racing concurrent writers across
the N MCP processes that share one backlog file (ISS-027).

### Fixed

- `merge_gate_decide.py` now hydrates `gates` from the per-task file when
  the backlog is schema v3, before reading the review-gate verdict. A
  missing or corrupt task file falls through to the existing fail-open
  `except Exception: return "ALLOW"` — same posture as every other
  "can't read the data" case in this module.
- Corrected `test_merge_gate_hook.py::_seed` and
  `test_merge_ladder_integration.py::_seed_backlog`, which wrote `gates`
  inline on the slim task dict under `schema_version: 3` — a shape real v3
  backlogs never produce — masking the bug in every existing test.
- **`backlog_get_task` is now a pure read** — removed the `last_referenced`
  bump + `_save` that ran on every call. `last_referenced` is maintained
  solely by genuine mutations (pick/update/complete, task creation).
- **Behavior change:** reads no longer refresh a task's staleness timestamp.
  A todo task nobody has edited or picked in 14+ days now shows as stale
  even if it's been viewed repeatedly — this is the intended, sharper
  signal, not a regression. Archive stale tasks or mutate them
  (`backlog_update_task`, `backlog_pick_task`) to refresh.

---

## 3.20.1 — spec-review skill aligned with the enforced gates model (2026-07-02)

Documentation-only fix: the spec-review skill predated the Spec A lanes/gates
enforcement and gave guidance the server contradicts.

### Fixed

- **WARN no longer presented as proceedable** — `gate_satisfied` only accepts
  pass/done/skipped, so a `warn` verdict leaves the gate pending, blocks later
  verdict gates, and blocks `done`. The skill now encodes the real verdict
  semantics: acknowledged Important findings → record `pass` with honest
  `important_count`; `warn` = revise-and-re-run or explicit `backlog_skip_gate`.
- **"Skip medium/low tasks" contradiction removed** — medium/low tasks default
  to the `standard` lane, whose required `design-review` gate this skill runs.
  Applicability is now lane-driven (full → `spec-review`, standard →
  `design-review`, express/laneless → advisory only, no gate recorded).
- **Lane context promoted** to a leading section (gate name + ceremony table +
  per-lane pipelines) instead of a parenthetical inside the recording step.
- **`backlog_set_spec_review` alias trap flagged** — it hardcodes the
  `spec-review` gate; skill and reference now lead with `backlog_record_gate`
  and warn against the alias for standard-lane tasks; `backlog_clear_gate`
  replaces the spec-review-only clear alias as the primary invalidation call.
- **plan-review cross-reference** — now states that the standard lane's
  `design-review` gate is run and recorded by `taskmaster:spec-review`.

---

## 3.20.0 — Harden backlog_project_structure against hangs (2026-06-19)

Follow-up to the 3.16.1 `.worktrees/`/`node_modules` exclusion: that fix was
too narrow and the hang still reproduced on other monorepo layouts. Adds a
request-wide deadline and several cost reductions so the tool degrades to
partial results instead of stalling.

### Added

- **`warning` field on the response payload** — non-null with a deadline notice
  when collection is truncated; null on a complete walk. Additive, so existing
  consumers (superset shape) are unaffected.

### Changed

- **25-second overall wall-clock deadline** threaded through the filesystem walk
  and every git subprocess. When it fires, partial results are returned with the
  `warning` set, and the truncated response is **not cached** (`cache_clear`) so
  the next call retries.
- **Per-git-call timeouts are bounded by the remaining request budget**, so many
  slow sub-repos can no longer stack 10s timeouts past the deadline.
- **Fast path (`refresh_git=False`)** now caps each git call at 3s and makes two
  local calls per sub-repo instead of three: `branch -a` is replaced by local
  `for-each-ref refs/heads refs/remotes` (no remote-tracking scan), and the
  redundant per-repo `rev-parse` is dropped — `current_branch` is read from the
  `worktree list` output.
- **`_SKIP_SCAN_DIRS` expanded** with more dependency/build/cache/data dir names
  (`.tox`, `.nox`, `out`, `bin`, `obj`, `.gradle`, `.terraform`, `coverage`,
  `data`, `datasets`, `Pods`, `DerivedData`, etc.) and additional VCS dirs.

---

## 3.19.0 — Bundle framing in the kanban viewer (2026-06-18)

Additive viewer surface: the kanban board now visually groups a bundle's
members. Within each column, members sharing a `bundle` slug are pulled
contiguous and wrapped in a tinted, color-coded frame with a `⬢ <slug>`
header (member count + strictest execution lane). Framing is per-column — a
bundle spanning statuses shows an independent frame in each column it touches.
No new data; reads the existing `bundle` field. New `clusterBundles` layout
helper + `renderBundleFrame` component; the per-card bundle chip is suppressed
inside a frame to avoid redundancy. Bundle palette added to design tokens.

---

## 3.18.0 — Task Bundles (2026-06-18)

Additive surface: groups of related tasks can be bound together under a shared
`bundle` slug, worked in a single worktree/branch, and completed as a unit
through review-gate and end-session.

### Added

- **`bundle` slug field on tasks** — `backlog_add_task` and `backlog_update_task`
  accept a `bundle` string. Validated by `_valid_bundle_slug` (lowercase
  alphanumeric + hyphens, max 48 chars). Stored in `SLIM_FIELDS`; included in
  `ALLOWED_FIELDS` for write access.
- **`_find_tasks_by_bundle(bundle_slug)`** — internal helper that returns all
  tasks carrying the given slug, cross-checked against birth-time `sub_repo` to
  catch accidental collisions across projects.
- **Bundle-aware `backlog_pick_task`** — when picking a task whose bundle
  already has an in-progress sibling, the tool reuses that sibling's worktree
  and branch. Lane for the new member is the strictest lane among all bundle
  members (`_strictest_lane`). Active bundle is stored in `_session_bundle`
  for the duration of the session.
- **`structured` flag on `backlog_blast_radius`** — pass `structured=True` to
  receive a machine-readable dict (affected epics, tasks, files, skills) in
  addition to the prose summary. Used as a detection fallback in bundle-aware
  pick to characterise scope before committing to a shared worktree.
- **Viewer bundle badge** — the kanban card renders a small pill showing the
  bundle slug when `task.bundle` is set, using the existing slim-field pathway.
- **Pick-task skill bundle pickup** — `taskmaster:pick-task` reads
  `_session_bundle` at session start and resumes the active bundle if one is
  found, falling back to bundle detection via `backlog_blast_radius(structured=True)`.
- **Review-gate per-member verdict + descope** — `taskmaster:review-gate` loops
  over all bundle members, emits a per-task pass/fail verdict, and supports
  descoping individual members back to `todo` (legal `in-progress → todo`
  transition, recorded in the task's log).
- **End-session per-member completion + merge fan-out + cleanup timing** —
  `taskmaster:end-session` drives completion for each bundle member in turn,
  triggers a merge fan-out (one merge record per member per merge target), and
  gates worktree cleanup until all members are merged.

---

## 3.17.0 — Remove auto mode (2026-06-14)

Auto mode was an internal orchestration subsystem that never shipped as a
supported user-facing surface. It is replaced by Codex/ultracode for
autonomous task execution.

**SemVer note:** removing an unshipped surface → minor bump rather than
major. The decision is explicit: auto-task / auto-epic / auto-phase were
effectively internal/unshipped (zero documented consumer references, no
public guarantees), so this is treated as an additive cleanup rather than a
breaking change.

### Removed

- **6 `backlog_auto_*` MCP tools**: `backlog_auto_start`, `backlog_auto_advance`,
  `backlog_auto_abort`, `backlog_auto_finish`, `backlog_auto_status`,
  `backlog_auto_complete_task`. MCP surface is gone; no replacement tool is
  registered.
- **Auto HTTP endpoints** from `backlog_server.py`: the 7 `/api/auto/*` routes —
  5 GET (`/api/auto/sessions`, `/api/auto/sessions/<sid>`, `/api/auto/state`,
  `/api/auto/events`, `/api/auto/budget/<sid>`) and 2 POST (`/api/auto/pause`,
  `/api/auto/stop`). Viewer and external callers can no longer reach these routes.
- **3 driver skills**: `taskmaster:auto-task`, `taskmaster:auto-epic`,
  `taskmaster:auto-phase`. The SKILL.md files are removed; invoking these
  slash commands will no longer work.
- **Viewer auto-mode screen** (`js/screens/auto-mode.js` and its nav entry).
  The auto-mode tab has been removed from the kanban viewer; reusable
  presentational components were archived to `js/_dormant/` (imported by
  nothing) for a possible future goals dashboard.
- **`auto/state.json`** is no longer created or read by any MCP tool or
  HTTP handler. Existing state files in `.taskmaster/auto/` are inert.

**Redirect:** use Codex / ultracode (`codex:rescue`, `codex:codex-cli-runtime`)
for autonomous multi-step task execution going forward.

---

## 3.16.1 — Fix backlog_project_structure hang on worktree-pool monorepos (2026-06-11)

### Fixed

- `backlog_project_structure` no longer hangs on monorepos with a `.worktrees/`
  pool. The sub-repo discovery scan descended into `.worktrees/` and registered
  each worktree clone (a `.git` *file*) as a bogus embedded sub-repo, then fired
  3 git subprocesses per fake repo (`branch -a` / `rev-parse` / `worktree list`)
  even on the cheap `refresh_git=False` path — stalling for minutes on a repo
  with 30+ worktrees. The scan now skips `.worktrees`, `node_modules`, and other
  dependency/build dirs (`_SKIP_SCAN_DIRS`) at both depth levels, eliminating the
  false-sub-repo explosion and the `node_modules` enumeration cost.
  (inbox 2026-06-08)

---

## 3.16.0 — Token diet: dead-tool cull, bounded list_tasks, slimmer prompts (2026-06-10)

Fixed-context cost drops ~2,500 tokens per session (2026-06-10 token audit;
tm-audit-006/021/022).

### Removed

- **13 dead MCP tool registrations** (125 → 112 tools, ~1,400 tok/session).
  Zero agent-surface references, verified by corpus grep: `backlog_release_notes`,
  `backlog_handover_latest` (self-deprecated alias), `recap_get`, `recap_set`,
  `snapshot_diff`, `lesson_list_extended`, `issue_list_extended`, `auto_state_get`,
  `auto_pause`, `auto_stop`, `auto_history`, `auto_event_log`, and the orphan
  `lesson_reinforce` duplicate. The underlying functions remain — the viewer's
  HTTP routes and tests use them; only the MCP registrations are gone.
  SemVer note: technically removed surface (→ major), deliberately shipped as
  minor because every removed registration had zero references and zero
  behavior loss. Kept registered after reference-check: `backlog_archive_epic`
  (backlog_update_epic redirects to it), `recap_list` (reflect-auto-improve
  retro), `viewer_prefs_get/set` (migrate-v3 migration steps).

### Added

- **`backlog_list_tasks` is bounded**: default `limit=50` with an overflow
  footer ("…N more tasks — pass status/epic/phase filters or limit=0 for all")
  and `limit=0` escape hatch. Rows now sort by status activity
  (in-progress/in-review first, done last) then priority, so the truncated
  view leads with active work. Unfiltered calls previously dumped the entire
  backlog (~3.3k tokens at 157 tasks).
- **`backlog_lesson_reinforce` writes the reinforce_events audit trail.**
  Historically only the orphan `lesson_reinforce` (now culled) appended audit
  events, so MCP-driven reinforcement never populated the trail.

### Changed

- **6 heaviest skill descriptions tightened** (issue, decision, bug, linear,
  lesson, init-taskmaster; ~975 chars / ~244 tok of always-loaded frontmatter).
  All trigger phrases and the issue↔bug routing disambiguation preserved.
- **SessionStart injection halved** (837 → 555 chars): routing table reduced
  to the 3 highest-ambiguity routes; the `taskmaster:taskmaster` router skill
  carries the full table.

---

## 3.15.1 — Resilient hook launcher + legacy shims (2026-06-10)

### Fixed

- **Hooks route through `run_hook.sh`, a fail-open launcher.** Calling
  `python script.py` directly had two bad failure modes: a missing script
  makes Python exit 2, which PreToolUse interprets as a hard DENY (a
  half-updated plugin blocked every Bash call in a live session), and bare
  `python` doesn't exist on machines that only have `python3` / the `py`
  launcher / the Windows Store stub. The launcher resolves a Python ≥ 3.9
  (`$CLAUDE_HOOKS_PYTHON` → `python3` → `python` → `py -3`, probed once and
  cached), and exits 0 with a loud stderr warning when the script or
  interpreter is missing — a dead hook degrades to "no hook", never to
  "deny everything". Intentional exit-2 blocks pass through unchanged.
  Registrations SOURCE the launcher into the wrapper shell
  (`CLAUDE_HOOK_SCRIPT=x.py . ".../run_hook.sh"`) and it `exec`s Python,
  so the hot path spawns zero extra processes — MSYS bash process creation
  costs seconds under load, the very overhead the 3.13.1 port eliminated.
- **Legacy `.sh` shims restored** (`merge-gate.sh`, `merge-recorder.sh`,
  `worktree-submodule-init.sh`, `taskmaster-merge-approve.sh`). Hook
  registrations are snapshotted at SessionStart, so sessions started before
  the 3.13.1 Python port still invoke the deleted `.sh` paths and erred on
  every Bash call. The shims delegate to the Python ports via the launcher.

---

## 3.15.0 — The Desk: sticky notes dashboard (2026-06-10)

Dashboard rebuilt as **The Desk** — sticky notes first.

### Added

- **Sticky notes entity**: `.taskmaster/notes/NOTE-NNN.md` — freeform, situational notes-to-self. New MCP tools: `backlog_note_create`, `backlog_note_list`, `backlog_note_get`, `backlog_note_update`, `backlog_note_archive`. New HTTP API: `/api/notes` (+ update/archive). Viewer-created notes are author `user`; MCP-created are `claude` — rendered as visually distinct paper (yellow vs blue).
- **Desk dashboard** (`#/dashboard`): paper-note board (quick-add composer, pin, inline edit, archive) above a pruned continuity band (4 rails, max 5 fresh cards each, >30d items collapse into "+N older" links). Time/Entity views and the stale full-width hero are retired.
- **start-session** surfaces the desk ("Your desk" step); **end-session** may leave at most one consolidated claude note.

### Fixed

- **marked.js vendored locally** — CDN SRI hash mismatch silently blocked all markdown rendering.
- **Sidebar version chip** stuck at "v?" — identity replay now supplies version correctly.
- **Clipped "Taskmaster" brand** in sidebar.
- **Favicon 404** resolved.
- **Orphaned bento dashboard code removed** (`board-surface.js`, `dashboard.css`).
- **Desk registered in shell scroll policy.**

---

## 3.14.0 — C2: Epic Architecture Map (2026-06-06)

The epic detail screen now renders a live **Architecture Map** generated from the
epic's `components` block — the C2 piece of the Task & Epic Protocol. Replaces the
flat component list as the primary architecture surface.

### Added

- **Epic Architecture Map** on the epic detail document (page and modal chrome):
  HTML blocks per component with embedded task cards, connected by an SVG edge
  layer (blocks = components, connectors = `after` edges). Extends the bespoke
  viewer graph idiom — no Mermaid, no new library.
- **Pure component-DAG layout engine** (`component-graph-layout.js`) — rank
  assignment from `after` edges with cycle guard; unassigned tasks collect into a
  trailing dashed bucket.
- **Rollup-driven block coloring** with a pinned, tested node-state → visual-state
  mapping: `done` (green tint) / `in-progress` (amber tint) / `blocked` →
  attention (critical tint + top border) / `todo` (neutral) / unassigned (dashed).
  Degrades gracefully on coarse C1-only rollup data (empty `component_rollup`
  renders all-neutral, never throws); richer gate-rollup coloring is an additive
  seam.
- **E2E + unit coverage** — Playwright architecture-map specs (route-mocked
  fixture epic) and exhaustive visual-state mapping tests.

### Design rules

Tinted fills and full/top borders only — no colored left rails, no box-shadows,
no hover motion, per the viewer's hard visual rules.

---

## 3.13.1 — Python hook port (2026-06-05)

Performance fix, no surface change. The four bash Bash/AskUserQuestion hooks
are ported 1:1 to Python: on Windows each bash hook forked many subprocesses
(jq/grep/sed/git) and MSYS2 fork costs seconds — `merge-gate.sh` measured 32s
per Bash tool call. Python spawns in ~100ms with zero forks for regex work.

### Changed

- **`merge-gate.sh` → `merge_gate.py`**, **`merge-recorder.sh` →
  `merge_recorder.py`**, **`worktree-submodule-init.sh` →
  `worktree_submodule_init.py`**, **`taskmaster-merge-approve.sh` →
  `taskmaster_merge_approve.py`** — behavior-preserving ports (stdlib only,
  Python 3.9+). Decision tables, fail-open semantics, the 60s never-consumed
  approval window, the approval-file path
  (`$HOME/.claude/taskmaster-merge-approve-$SESSION_ID`), and all
  Claude-facing stderr/stdout messages are unchanged character-for-character.
  Zero subprocess spawns on the hot path — non-matching commands exit 0
  having spawned nothing; `git` / the decision and stamp modules are only
  spawned once a triggering pattern matched, same as before.
- **`hooks/hooks.json`** — the four entries now invoke
  `python "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py"` with `timeout: 10`.
  `session-start.sh` and `snapshot.py` registrations are unchanged.

### Removed

- The four ported `.sh` hook scripts (`session-start.sh` stays).

---

## 3.13.0 — Spec B: merge ladder (2026-06-02)

Builds on Spec A's review gates. Adds a per-task **promotion ladder** — an ordered
set of merge rungs (`develop → stage → master`) — that records where each task's
branch has landed, and an **opt-in, fail-OPEN** policy that a task branch can't be
merged into a rung until its `review-gate` is a recorded (fresh) pass.

### Added

- **`merge_targets` manifest field** — ordered merge ladder at
  `conventions.policies.merge_targets` (each rung = `{label, branches}`), plus the
  opt-in `review_gate_required_for_merge` flag (**off by default**). Loader bakes a
  default `develop / stage / master` ladder, so standard-branch repos need zero
  config. (Not to be confused with `ship_order` — that's the monorepo repo
  topo-sort.)
- **Per-task merge state** — `merge_status` (heavy, per-rung
  `{merged_at, merge_commit}`) plus slim `skip_merge_gate` /
  `merge_gate_freshness` / `merge_gate_state`, mirroring how Spec A split
  `gates` (heavy) vs `gate_state` (slim).
- **`backlog_record_merge(task_id, rung, sha)`** — manual MCP fallback that stamps
  a reached rung (mirrors `backlog_record_gate`; idempotent per rung; records an
  out-of-ladder `branch:<name>` label to preserve the audit trail).
- **`merge-gate.sh`** (PreToolUse) — blocks a `git merge` of a task branch into a
  rung only when **certain**: policy on AND a task matches the source branch AND
  `skip_merge_gate` is false AND `gates["review-gate"]` is not a fresh pass.
  Decision logic lives in `merge_gate_decide.py`, wrapped to print `ALLOW` on any
  error. One-shot **Approve / Deny** via taskmaster's OWN session-scoped approval
  file (`$HOME/.claude/taskmaster-merge-approve-$SESSION_ID`) — **expiry-based on a
  60s freshness window, never consumed**, so an Approve survives an unrelated merge
  retry. Guard-hooks' `consume-approval.sh` (which only burns `guard-approve-*`)
  never interferes.
- **`merge-recorder.sh`** (PostToolUse) — stamps the reached rung from a successful
  `git merge` via `backlog_record_merge` (v3-correct heavy write + `merge_gate_state`
  recompute); **never blocks**.
- **`taskmaster-merge-approve.sh`** (PostToolUse/AskUserQuestion) — taskmaster's own
  approval writer that touches the approval file on an "Approve" answer; no
  dependency on guard-hooks being installed.
- **Viewer** — merge-rung ladder dots on the task detail surface and compact dots on
  kanban cards (a separate `merge-status.js` component; the review-gate
  `gate-pipeline.js` is unchanged).

### Notes

- **Enforcement philosophy is deliberately split:** Spec A gates are **fail-CLOSED**
  (the MCP layer rejects illegal moves); Spec B's merge hook is **fail-OPEN** (any
  uncertainty — no manifest, policy off, unparseable branch, corrupt yaml, untracked
  task, hook error — resolves to *allow*; the worst case is a missing audit stamp,
  never a wedged `git merge`).
- Merge enforcement is **opt-in** and inert until `review_gate_required_for_merge`
  is set true in the project manifest.

---

## 3.12.0 — Spec A: lanes & enforced gates foundation (2026-05-29)

### Added

- **Task lanes** — explicit `lane` field on tasks (`full` / `standard` / `express`);
  each lane maps to a fixed set of required gate stages. Laneless tasks remain
  fully exempt from gate enforcement.
- **Per-task gate pipeline** — `gate_state` + `gates` fields track gate outcomes
  (passed / skipped / pending) per stage. New tools: `backlog_record_gate`,
  `backlog_skip_gate`, `backlog_clear_gate`, `backlog_task_pipeline`,
  `backlog_backfill_lanes`.
- **`plan-review` skill** — adversarial design review of a task's plan before
  implementation; sits between spec-review and review-gate in the lifecycle.
- **Viewer gate-pipeline tracker** — per-task gate checklist in the detail modal;
  lane badge on kanban cards.

### Changed

- **`complete_task` is now fail-closed** on outstanding review gates
  (spec-review / plan-review / design-review / review-gate) for lane-bearing
  tasks. Status gates (spec / plan / tests / impl) are non-blocking progress
  markers and never block completion (laneless tasks are exempt). `batch_update`
  `complete` / `status done` ops enforce the same gate.
- **`update_task` status follows a forward-transition table** for lane-bearing
  tasks; backward transitions are rejected unless explicitly forced (laneless
  tasks exempt).
- **Auto-mode is lane-aware** — an auto run walks the task's lane-specific stage
  sequence (`auto_stages_for_lane`); no-arg `backlog_auto_advance()` steps to the
  next planned stage and auto-records its gate, so a standard run records
  `design-review` and a full run records `plan-review`.
- **`set_spec_review` / `clear_spec_review`** are now thin aliases over
  `backlog_record_gate` / `backlog_clear_gate`; behaviour is unchanged.

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
