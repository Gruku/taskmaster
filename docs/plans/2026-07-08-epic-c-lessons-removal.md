# Epic C — Lessons Removal Implementation Plan (tm 4.0.0, part 1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the entire Lessons subsystem (11 MCP tools, helpers, candidates/marker machinery, viewer screens, skill/playbook, `related_lessons` link fields, `lessons_fired` recap stat) and replace it with the two-tier memory rule: assistant's own memory for session insights, repo instruction files (CLAUDE.md/AGENTS.md) for cross-assistant knowledge.

**Architecture:** Pure removal + two small additions (a `migrate-lessons` playbook and an "instruction-file candidate" check in end-session). Removal proceeds outside-in so the suite stays green after every task: MCP tools first, then helpers/schema, then viewer, then playbooks/adapters, then new content, then docs+version. `.taskmaster/lessons/` files on user disks are never touched.

**Tech Stack:** Python (FastMCP server, pytest), vanilla-JS viewer (`node --test` units + Playwright e2e), markdown playbooks with lint tests.

**Spec:** claude-tools `docs/superpowers/specs/2026-07-08-taskmaster-areas-releases-lessons-design.md`, section 3.

## Global Constraints

- Repo: `C:\Users\gruku\Files\Claude\taskmaster`. Work on branch `feature/lessons-removal` off `master`.
- Version at end of epic: **4.0.0** in `pyproject.toml` AND `.claude-plugin/plugin.json` (removed surface → major). No marketplace.json in this repo.
- **Never delete or modify `.taskmaster/lessons/` data directories in any project** — only code, tests, docs inside this repo.
- The `<lesson-candidate>` XML annotation system is REMOVED along with the entity (spec: start-session lesson-marker logic goes; end-session gains the instruction-file check).
- `related_lessons` / `related_lesson` link fields and the `informed_by`/`informs` lesson link-domain entries are REMOVED (schema break, covered by the major bump).
- `lessons_fired` recap/snapshot stat is REMOVED (recap loses one card).
- Historical records stay: CHANGELOG history, `docs/design-v3-*.md`, `docs/v1/`, dated specs/plans. Living docs that would mislead get updated.
- Python suite: `python -m pytest tests/ -x -q` from repo root. Viewer units: `node --test tests/unit/` from `viewer/`. Full green after every task.
- Commit after every task; explicit pathspecs only (`git add <paths>`), never `git add -A`. Stash any stray edits first.

## Removal Inventory (authoritative)

Every task below references this inventory by section number. Line numbers are from `master` @ 2c1af96 — re-locate by symbol name if drifted; **grep is the gate**: after each task, the listed grep must return zero hits in the task's file set.

### INV-1 `taskmaster/backlog_server.py`
- **Tool block 3862–4365** (delete contiguously, ends before `backlog_add_task` at 4368): `backlog_lesson_create` (3863), `_list` (3965), `_get` (4009), `_update` (4078), `_reinforce` (4138), plain helper `lesson_reinforce` (4159), `_digest` (4182), `_candidate_defer` (4212), `_candidates_list` (4251), `_candidate_drop` (4273), `_candidates_scan` (4289), `_match` (4328).
- Standalone `lesson_list_extended()` 9066–9085.
- Imports 114–130: remove `LESSON_KINDS`, `LESSON_TIERS`, `write_lesson`, `read_lesson`, `update_lesson`, `reinforce_lesson`, `list_lesson_ids`, `match_lessons_for_task`, `lesson_digest`, `core_lessons`, `sync_lesson_index`, `lesson_eligible_for_promotion`, `LESSON_CANDIDATE_KINDS`, `LESSON_CANDIDATE_SCOPES`, `lesson_candidates_defer`, `lesson_candidates_read`, `lesson_candidates_clear`.
- Non-lesson callers to edit: `_has_v3_content` 379 (`lessons_meta` check); `backlog_get_task` expand_links 1160/1162/1176/1213 (`related_lessons`); subdir loop 1675; `backlog_init` 1705–1791 (`lessons_meta=[]` at 1789, subdir tuple 1791, docstrings 1716–18); `backlog_migrate_v3` 1812–1897 (docstrings + subdir list 1897); idea-create docstring 2047; link-reconcile loops 2495/2566 (`("lessons","L")`); `backlog_idea_create`/`backlog_note_create`/`backlog_idea_update` 3497–3537/3567/3597/3632–3715 (`related_lessons` params, expand entry 3666, `updates[...]` 3714–15); recap event-kind docstring 6923 (`lesson_promoted`); `_task_related` 7086–7193 (lessons block 7133–40 + return key 7193); HTTP `GET /api/lessons` 8012–8029 (delete branch); POST handler 8251 (import) + 8280 (`related_lessons` payload); HTTP `POST /api/lessons/<id>/reinforce` 8349–8364 (delete branch).

### INV-2 `taskmaster/taskmaster_v3.py`
- Lessons section 3121–3478 (constants `LESSON_*`, `_LESSON_INDEX_FIELDS`, all `*lesson*` helpers listed in the inventory agent report — everything between `resolve_linear_token` end and line 3478 that mentions lesson).
- Lesson-candidates section 3929–4064 + marker regex/scanner 4048–4132 (`_LESSON_CANDIDATE_RE`, `_ATTR_RE`, transcript scanner).
- `list_lesson_ids_cwd` 4707–4709; `compute_lesson_shelf` 4799–~4830.
- Non-lesson callers to edit: `CANONICAL_SECTIONS` 67; slim/index fields 123 (`related_lessons`); entity field maps 133/148; `_build_tldr_index` 260/270; docstring 310; `ENTITY_KIND_BY_PREFIX` 584 (`"L"`); `LINK_TYPE_DOMAIN` 597–598 (`informed_by`/`informs`); legacy link map 800; link rules 807; legacy-fields maps 847/849; list literal 1108; `apply_pending_review` docstring 1732–34 (keep function, reword); idea write/update helpers 3593/3636/3690/3728; `VIEWER_PREFS_DEFAULTS` 4163/4184; snapshot-diff 4493/4525 (`lessons_fired`); entity dispatch 5267/5278/5304.

### INV-3 Viewer
- DELETE: `js/screens/lessons.js`, `js/screens/lesson-detail.js`, `js/components/lesson-card.js`, `js/util/lesson-shelf.js`, `css/screens/lessons.css`. Check-then-delete orphans (delete only if no remaining importer after lesson-card goes): `js/components/anchor-pills.js`, `js/components/sparkline.js`, `js/components/dot-meter.js`.
- EDIT: `js/main.js` 17–18 (routes); `js/components/sidebar.js` 19 (nav); `index.html` 20 (css link); `js/store.js` 10/49/56; `js/api.js` 130–136, 160, 218–233; `js/components/right-rail.js` 3/15/81–94; `js/components/task-detail-graph.js` 185/189–195; `js/components/link-pills.js` 68/75–78; `js/components/recap-receipts-grid.js` 21/81–91; `js/components/snapshot-diff.js` 30; `js/screens/recap.js` 171/180–181/227/230/257; `js/screens/issue-detail.js` 244; `js/screens/ideas.js` 683; `js/lib/xml-render.js` 11 (`<lesson-candidate>` chip tag — remove); CSS: `css/shell.css` 317–354 lesson selectors/comments, `css/components.css` 190/265/327, `css/screens/task-detail.css` 254/307, `css/screens/ideas.css` 304–305.

### INV-4 Skills / playbooks / adapters
- DELETE: `skills/lesson/SKILL.md`; `playbooks/lesson/` (7 files); `adapters/codex/prompts/lesson.md`.
- EDIT: `skills/taskmaster/SKILL.md:3`; `skills/migrate-v3/SKILL.md:3`; router `playbooks/taskmaster/playbook.md` 23/44/55 + `references/routing-table.md` 44/45/52 + `references/disambiguation.md` 19/22; `playbooks/start-session/playbook.md` 22/25/56/72/74 + `references/deep-mode.md` (delete D2/D3 lesson subsections 15–23/62–63/77/80); `playbooks/end-session/playbook.md` 17/77/87 + `references/v3-pre-steps.md` (delete `v3-pre-2a` section 6–48; edit 100/111); `playbooks/pick-task/playbook.md` 3/50–52 + `references/v3-context-loading.md` 7/14/20 + `references/deep-mode.md` D2 (5/13–19/40/49); `playbooks/handover/playbook.md:31` + `references/auto-extraction.md:13`; `playbooks/migrate-v3/playbook.md:41` + `references/migration-steps.md:50` + `references/v2-vs-v3.md` 46/59; `playbooks/init-taskmaster/playbook.md` 17/36 + `references/analysis-mode.md:3`; `playbooks/add-idea/playbook.md` 19/38 + `references/slash-form.md` 18/27/46; example rewording in `playbooks/issue/references/entry-point-flows.md:100`, `issue-bar.md` 12/22, `playbooks/bug/references/bug-vs-issue.md:33`; `adapters/agents-md/AGENTS.md` 12/37; `adapters/codex/AGENTS.md` 12/32.
- Coverage gate `scripts/check_adapter_coverage.py` is dynamic — no script change; must pass after deletions.

### INV-5 Tests
- DELETE Python: `tests/test_v3_lesson_reinforce.py`, `test_v3_lesson_candidates.py`, `test_lesson_skill_lint.py`, `test_slim_lesson_get.py`, `test_server_lessons.py`. DELETE viewer: `viewer/tests/lessons.spec.js`, `viewer/tests/unit/lesson-shelf-placement.test.js`.
- EDIT Python (full detail in inventory report; key items): `conftest.py` 42/58 subdir tuple; `skill_budget_helper.py:18`; `test_dead_tool_cull.py` (entries + one test fn); `test_e2e_v3_smoke.py` (86 + `test_v3_lesson_create_match_reinforce`); `test_mcp_v3_exposure.py` (`test_lesson_tools_exposed`, keyword 188, floor 194 — lower floor by 11); `test_slim_list_tools.py` (3 lesson tests + imports); `test_tldr_required_on_create.py` 170–205; `test_tldr_backfill.py` (37–73, 104–115, 130–137); `test_v3_layout.py` (`class TestLessons` 850–1001 + lifecycle/assert/prefs edits 1005/1036–66/1103/1137); `test_link_migration.py` (28/34/50–58/108/182); `test_link_types.py` (48/56/76–79/86–87); `test_migrate_v3_skill_lint.py:52`; `test_pick_task_skill_lint.py` (123 + 143–153); `test_pick_task_smoke.py` (20/31/67/90 — recompute token budgets, don't just delete lines); `test_start_session_skill_lint.py` 133/137; `test_start_session_smoke.py` 21/29 (recompute budgets); `test_canonical_sections.py` (12 + 80–90); `test_foundation_smoke.py` (4 lesson blocks); `test_server_task_detail.py` (16/21 + edit and rename `test_get_task_related_returns_lessons_handovers_issues_and_deps`); `test_v3_snapshot_diff.py` (`lessons_fired` at 8/22/31/41/50/52); `test_v3_ideas.py` 90/98; `test_iss_001_regression.py:42`; `test_inline_ref_extraction.py` 8–10; `test_link_inverse_sync.py` (4/33 + `_seed_lesson` 62–71); `test_links_smoke.py` (33/138 + 47–50/63); tuple-only drops: `test_backlog_link_create.py`, `test_backlog_link_query.py`, `test_backlog_link_reconcile.py`, `test_backlog_link_validate.py`, `test_backlog_link_remove.py`, `test_auto_link_on_save.py`, `test_expand_links.py` 36/41/51, `test_tmp_taskmaster_fixture.py:11`, `test_server_ideas.py:96`, `test_server_api.py:15`.
- EDIT viewer tests: `viewer/tests/lessons-issues-routing.spec.js` → rename `issues-routing.spec.js`, keep issues leg only; `recap.spec.js:50`; `smoke.spec.js:7`; `v3-polish-045-layout.spec.js` 94/97; `unit/right-rail.test.js` 33/36/47 (panel count −1); `unit/task-detail-document.test.js` 223/232 (panel count 7→6); `unit/recap-receipts-grid.test.js` 5/11/18/29 (four→three cards); `unit/snapshot-diff.test.js` 25–29; `unit/view-mode.test.js:30`; `unit/xml-render.test.js` 37/41/68/74 (`<lesson-candidate>` chip tests deleted).

### INV-6 Docs / version
- DELETE: `docs/v3-polish-013/lessons.md`, `docs/v3-polish-013/lesson-detail.md`.
- EDIT: `docs/SMOKE_TEST_3.0.0.md` 44–51 (drop §4 lesson steps); `docs/v3-polish-013/issue-detail.md` (2 dangling refs); `docs/v3-polish-013/{README,recap,synthesis,dashboard}.md` lesson findings rows; `.claude-plugin/plugin.json` 3 (description) + 4 (version); `pyproject.toml:7`; `CHANGELOG.md` new `## 4.0.0`; `scripts/backfill_tldr.py` 1/46; `scripts/migrate_links.py` 4/51/65.

---

### Task 1: Branch + MCP tool surface removal (`taskmaster/backlog_server.py`)

**Files:**
- Modify: `taskmaster/backlog_server.py` (all INV-1 items)
- Delete: the 3 lesson-tool test files that exercise this surface: `tests/test_v3_lesson_reinforce.py`, `tests/test_slim_lesson_get.py`, `tests/test_server_lessons.py`
- Modify: `tests/test_dead_tool_cull.py`, `tests/test_e2e_v3_smoke.py`, `tests/test_mcp_v3_exposure.py`, `tests/test_slim_list_tools.py`, `tests/test_server_task_detail.py`, `tests/test_tldr_required_on_create.py` (per INV-5)

**Interfaces:**
- Produces: `backlog_server.py` with zero `lesson` references except imports it no longer makes; `_task_related` returns `{"handovers":…, "issues":…, "deps":…}` (no `"lessons"` key); `backlog_init`/`backlog_migrate_v3` create subdirs `("issues","handovers","ideas")` only; MCP tool count drops by 11 (exposure-test floor lowered accordingly).
- Consumes: nothing from other tasks (first task).

- [ ] **Step 1: Create branch**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster checkout -b feature/lessons-removal
```

- [ ] **Step 2: Delete/edit tests first (red)** — delete the 3 files, apply the INV-5 edits to the 6 mixed files listed above (delete lesson test functions, lower `test_mcp_v3_exposure.py` floor at line 194 by 11, remove `"lesson"` keyword at 188, rename `test_get_task_related_returns_lessons_handovers_issues_and_deps` → `..._returns_handovers_issues_and_deps` and drop its lesson writes/asserts).

- [ ] **Step 3: Run suite to see the red** — `python -m pytest tests/test_mcp_v3_exposure.py tests/test_server_task_detail.py -q`. Expected: FAIL (exposure floor now excludes lesson tools that still exist; `_task_related` still returns lessons key). This proves the tests now specify the post-removal contract.

- [ ] **Step 4: Apply INV-1 removals** to `taskmaster/backlog_server.py` — delete the tool block, `lesson_list_extended`, both HTTP branches, the imports, and edit every non-lesson caller exactly as INV-1 lists. Keep `_has_v3_content` working via its remaining checks.

- [ ] **Step 5: Grep gate + suite green**

```bash
grep -in "lesson" taskmaster/backlog_server.py   # expected: no output
python -m pytest tests/ -x -q                      # expected: all pass
```

- [ ] **Step 6: Commit** — `git add taskmaster/backlog_server.py tests/…` (explicit paths), message `feat(4.0)!: remove lesson MCP tools + HTTP routes (epic C task 1)`.

### Task 2: Helper/schema removal (`taskmaster/taskmaster_v3.py`)

**Files:**
- Modify: `taskmaster/taskmaster_v3.py` (all INV-2 items)
- Delete: `tests/test_v3_lesson_candidates.py`
- Modify (per INV-5): `tests/conftest.py`, `tests/skill_budget_helper.py`, `tests/test_v3_layout.py`, `tests/test_link_migration.py`, `tests/test_link_types.py`, `tests/test_canonical_sections.py`, `tests/test_foundation_smoke.py`, `tests/test_v3_snapshot_diff.py`, `tests/test_v3_ideas.py`, `tests/test_iss_001_regression.py`, `tests/test_inline_ref_extraction.py`, `tests/test_link_inverse_sync.py`, `tests/test_links_smoke.py`, `tests/test_tldr_backfill.py`, the 7 tuple-only files, `scripts/backfill_tldr.py`, `scripts/migrate_links.py`

**Interfaces:**
- Consumes: Task 1's `backlog_server.py` (no longer imports any lesson symbol).
- Produces: `taskmaster_v3.py` with no lesson code; `CANONICAL_SECTIONS`, `ENTITY_KIND_BY_PREFIX`, `LINK_TYPE_DOMAIN`, legacy-link maps, `VIEWER_PREFS_DEFAULTS`, snapshot-diff, and entity dispatch tables all lesson-free; save/load ignores `lessons_meta` (key simply no longer written; stale keys in existing backlog.yaml files are inert and left in place).

- [ ] **Step 1: Edit tests to the post-removal contract** (INV-5 list above; recompute — don't blind-delete — any aggregate counts, e.g. `test_v3_layout.py:1103` assertion and viewer-prefs keys at 1137).
- [ ] **Step 2: Run the edited tests, observe red** — `python -m pytest tests/test_v3_layout.py tests/test_link_types.py -q`. Expected: FAIL (link domain still contains `informed_by`).
- [ ] **Step 3: Apply INV-2 removals/edits.**
- [ ] **Step 4: Grep gate + full suite**

```bash
grep -in "lesson" taskmaster/taskmaster_v3.py scripts/backfill_tldr.py scripts/migrate_links.py  # no output
python -m pytest tests/ -x -q                                                                     # all pass
```

- [ ] **Step 5: Commit** — `feat(4.0)!: remove lesson helpers, candidates, related_lessons link schema (epic C task 2)`.

### Task 3: Viewer removal

**Files:** all INV-3 deletions/edits; viewer test changes per INV-5 (delete 2 files, rename `lessons-issues-routing.spec.js` → `issues-routing.spec.js` keeping the issues leg, edit the 8 mixed unit/e2e files).

**Interfaces:**
- Consumes: Task 1 (no `/api/lessons` endpoints — viewer must not call them).
- Produces: viewer with no lessons route/nav/panel; right-rail panel count −1; recap receipts grid has 3 cards; `xml-render.js` no longer registers `<lesson-candidate>`.

- [ ] **Step 1: Edit viewer unit tests to post-removal contract** (panel counts, card counts, xml-render chips), delete `unit/lesson-shelf-placement.test.js`.
- [ ] **Step 2: `cd viewer && node --test tests/unit/`** — expected: FAIL (panels still render lessons).
- [ ] **Step 3: Apply INV-3.** For the 3 possible orphans run `grep -rn "anchor-pills\|sparkline\|dot-meter" viewer/js --include="*.js"` first; delete only genuinely unimported ones.
- [ ] **Step 4: Gates**

```bash
grep -rin "lesson" viewer/js viewer/index.html viewer/css   # no output
cd viewer && node --test tests/unit/                         # all pass
```

E2e specs: run only the route-mocked ones touched (`npx playwright test tests/issues-routing.spec.js tests/recap.spec.js tests/smoke.spec.js` with the suite's standard local server); note ISS-025 — the broader e2e suite is red on master and is not this epic's gate.

- [ ] **Step 5: Commit** — `feat(4.0)!: remove lessons viewer surface (epic C task 3)`.

### Task 4: Playbooks, skills, adapters

**Files:** all INV-4 deletions/edits; `tests/test_lesson_skill_lint.py` deleted; lint/smoke test edits per INV-5 (`test_migrate_v3_skill_lint.py`, `test_pick_task_skill_lint.py`, `test_pick_task_smoke.py`, `test_start_session_skill_lint.py`, `test_start_session_smoke.py` — recompute token budgets by re-running the budget helper against the edited playbooks, not by guessing).

**Interfaces:**
- Produces: router/playbooks with no lesson routes or steps; start-session no longer emits `<lesson-candidate>` or matched-lesson counts; end-session pre-steps no longer include the candidate sweep (replacement lands in Task 5); `scripts/check_adapter_coverage.py` passes.

- [ ] **Step 1: Apply INV-4** (delete 9 files, edit the cross-references — remove routing rows, delete D2/D3 and v3-pre-2a sections, reword illustrative examples to use ideas/issues instead of lessons).
- [ ] **Step 2: Update lint/smoke tests + budgets** per INV-5.
- [ ] **Step 3: Gates**

```bash
grep -rin "lesson" skills/ playbooks/ adapters/ commands/ hooks/   # no output
python scripts/check_adapter_coverage.py                            # pass
python -m pytest tests/ -x -q                                        # all pass
```

- [ ] **Step 4: Commit** — `feat(4.0)!: remove lesson skill/playbook/adapters, reroute references (epic C task 4)`.

### Task 5: Replacement — two-tier memory rule + `migrate-lessons` playbook

**Files:**
- Modify: `playbooks/end-session/playbook.md` (add the check where the sweep was), `playbooks/end-session/references/v3-pre-steps.md`
- Create: `playbooks/migrate-lessons/playbook.md`, `skills/migrate-lessons/SKILL.md`
- Modify: `adapters/agents-md/AGENTS.md`, `adapters/codex/AGENTS.md` (add migrate-lessons row), `playbooks/taskmaster/references/routing-table.md` (route "migrate lessons", "convert lessons to memory")
- Test: extend `tests/test_start_session_skill_lint.py`-style lint: create `tests/test_migrate_lessons_skill_lint.py` (frontmatter present, description contains trigger phrases, playbook file exists, budget within `skill_budget_helper` cap — add `"migrate-lessons": 1_000` to `tests/skill_budget_helper.py:18` region)

**Interfaces:**
- Consumes: Task 4's cleaned end-session playbook.
- Produces: `taskmaster:migrate-lessons` skill; end-session "instruction-file candidate" step.

- [ ] **Step 1: Write `tests/test_migrate_lessons_skill_lint.py`** (mirror the structure of an existing `*_skill_lint.py`; assert SKILL.md frontmatter has `name: migrate-lessons` and a `description` mentioning "lessons" and "memory"; assert `playbooks/migrate-lessons/playbook.md` exists; assert budget cap). Run: expected FAIL (files missing).
- [ ] **Step 2: Add end-session step.** In `playbooks/end-session/playbook.md`, where the lesson-candidate sweep step was, add:

```markdown
### Instruction-file candidate check
Before writing the session record, ask yourself once: did this session surface knowledge that must bind ALL assistants working in this repo ("we always do X here", "Y breaks if you do Z")? If yes, propose a 1-3 line addition to the repo's instruction file (CLAUDE.md / AGENTS.md) and apply it on user approval. Session-level insights that only concern you go to your own memory system instead — do not write them here. Most sessions produce neither; skip silently.
```

- [ ] **Step 3: Write `playbooks/migrate-lessons/playbook.md`:**

```markdown
# Migrate Lessons

One-time conversion of a project's legacy `.taskmaster/lessons/L-*.md` files (taskmaster ≤3.x) into the two-tier memory model. Run once per project after upgrading to taskmaster 4.x.

## Steps

1. Read every `.taskmaster/lessons/L-*.md` in the current project. If the directory is missing or empty, report "no lessons to migrate" and stop.
2. For each lesson, propose exactly one destination:
   - **instruction-file** — knowledge that must bind all assistants (conventions, invariants, "we got burned by X"). Draft a 1-3 line entry for the repo's CLAUDE.md / AGENTS.md.
   - **assistant-memory** — session-craft insight useful to you specifically. Draft a memory entry in your own memory system's format.
   - **drop** — stale, superseded, or already encoded elsewhere (check instruction files before proposing).
3. Present the full proposal as one table (lesson id, title, destination, draft text) and apply on user approval — instruction-file entries via Edit, assistant-memory entries via your memory mechanism.
4. Leave `.taskmaster/lessons/` untouched on disk. Do not delete lesson files; they are the historical record.
5. Report: N migrated to instructions, M to memory, K dropped.
```

- [ ] **Step 4: Write `skills/migrate-lessons/SKILL.md`** (thin wrapper, matching the repo's existing wrapper pattern — frontmatter `name: migrate-lessons`, description: "One-time migration of legacy .taskmaster/lessons/ files into assistant memory and repo instruction files after upgrading to taskmaster 4.x. Invoke when the user says 'migrate lessons', 'convert lessons to memory', 'what happened to lessons', or when start-session detects L-*.md files under .taskmaster/lessons/ in a 4.x project." — body points at the playbook per the established wrapper form).
- [ ] **Step 5: Add routing-table row + adapter AGENTS.md rows; run gates** — lint test passes, `python scripts/check_adapter_coverage.py` passes, full suite green.
- [ ] **Step 6: Commit** — `feat(4.0): two-tier memory replacement — end-session instruction check + migrate-lessons playbook (epic C task 5)`.

### Task 6: Docs sweep, version bump, changelog, final verification

**Files:** all INV-6 items.

- [ ] **Step 1: Apply INV-6** — delete the 2 audit docs, update living docs, bump `pyproject.toml` and `.claude-plugin/plugin.json` to `4.0.0`, drop "lessons" from the plugin description, add CHANGELOG:

```markdown
## 4.0.0
**BREAKING — Lessons subsystem removed.** The 11 `backlog_lesson_*` MCP tools, lesson candidates/markers (`<lesson-candidate>`), `related_lessons`/`informed_by` link fields, `lessons_fired` recap stat, viewer Lessons screens, and the `taskmaster:lesson` skill are gone. Durable knowledge now lives in each assistant's own memory system (session insights) and repo instruction files like CLAUDE.md/AGENTS.md (cross-assistant knowledge). Existing `.taskmaster/lessons/` files are left untouched on disk; run the new `taskmaster:migrate-lessons` skill once per project to convert them. `lessons_meta` keys in existing backlog.yaml files are ignored. First of four 4.0 epics (lessons removal → areas → release trains → migration tooling).
```

- [ ] **Step 2: Full-repo verification**

```bash
grep -rin "lesson" taskmaster/ skills/ playbooks/ adapters/ commands/ hooks/ viewer/js viewer/css viewer/index.html scripts/ --include="*"   # no output
python -m pytest tests/ -q                                   # all pass, no -x: see full count
cd viewer && node --test tests/unit/                         # all pass
python scripts/check_adapter_coverage.py                     # pass
python -c "from taskmaster import backlog_server"            # imports clean
```

- [ ] **Step 3: Commit** — `chore(4.0)!: version 4.0.0 — lessons removal complete (epic C)`. Merge to local `master` via `git merge --no-ff feature/lessons-removal` after review-gate. **Do not push** — user gates all pushes.

---

## Self-review notes

- Spec §3 coverage: tools ✔ (T1), skill/playbook ✔ (T4), viewer screen ✔ (T3), start-session marker logic ✔ (T4), `lessons_meta` ✔ (T1/T2), files-stay-on-disk ✔ (constraint + T5 playbook step 4), two-tier rule ✔ (T5 step 2), migrate-lessons playbook ✔ (T5), 4.0.0 major ✔ (T6).
- Line numbers are advisory (master @ 2c1af96); symbol names + grep gates are the real contract.
- Epics B (areas/done_when), A (release trains), D (CodeMaestro migration) get their own plans after C ships — B's plan must be written against post-C master.
