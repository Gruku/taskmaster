# Epic B — Areas + Finite Epics Implementation Plan (tm 4.1.0, part 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class **Area** entity (long-lived subsystem, no status lifecycle) and make **Epics strictly finite** — `done_when` required at creation, "closeable" surfaced everywhere once all tasks are done.

**Architecture:** Area is a sidecar-file entity mirroring the existing notes module (per-file markdown under `.taskmaster/areas/<slug>.md`, lazy dir, 4 MCP tools). `done_when` and `area` are slim scalar fields on epics (slim-index allowlist, NOT heavy fields); `area` is also a task field. "Closeable" is derived-on-read from the existing `_epic_stats` math (`done+archived == total`), never stored. Viewer gets Area as a filter/group axis (no new screen) and closeable badges on existing epic surfaces.

**Tech Stack:** Python (FastMCP server, pytest), vanilla-JS viewer (`node --test tests/unit/*.test.js`), markdown playbooks with lint/budget tests.

**Spec:** claude-tools `docs/superpowers/specs/2026-07-08-taskmaster-areas-releases-lessons-design.md`, sections 1 (Area/Epic) and 2.4-2.5 (validate rule, viewer axis). Inventory: `## Appendix — Inventory` below (referenced as INV-§N; line numbers from master @ e953ea5 — locate by symbol if drifted).

## Global Constraints

- Repo: `C:\Users\gruku\Files\Claude\taskmaster`. Branch `feature/areas-finite-epics` off `master` (e953ea5).
- Version at end: **4.1.0** in `pyproject.toml` AND `.claude-plugin/plugin.json` (additive surface → minor).
- **Locked design decisions** (do not relitigate in tasks):
  - Area IDs are kebab-case slugs (like epic ids: `desktop-app`), NOT numbered prefixes. No `ENTITY_KIND_BY_PREFIX` entry, no typed link domain rows, no CANONICAL_SECTIONS entry. Tasks/epics reference areas via a plain `area` scalar field.
  - Area entity fields: `id`, `name`, `description` (optional), `anchors` (optional list of path/glob strings), `created`. NO status field of any kind.
  - Area MCP surface: `backlog_area_create`, `backlog_area_get`, `backlog_area_list`, `backlog_area_update` — exactly 4, no archive (areas never finish; renames go through update).
  - `done_when` is REQUIRED on `backlog_add_epic` (reject empty/missing with exactly: `Epics are finite: 'done_when' is required. An epic that can't say when it's done is an area.`) and editable via `backlog_update_epic`. Legacy epics without it remain loadable; `backlog_validate` warns.
  - `done_when` and `area` are SLIM epic fields (add to slim allowlist `taskmaster_v3.py:144`), never heavy — `test_epic_phase_bodies.py:11` asserts `EPIC_HEAVY_FIELDS` unchanged and must stay passing.
  - "Closeable" = derived (`total>0 and done+archived == total` per `_epic_stats`), surfaced but never stored, never auto-archives. Archiving stays explicit via `backlog_archive_epic`.
  - Viewer: Area filter/group axis on kanban + table, closeable badge + `done_when` on existing epic surfaces. NO dedicated Areas screen, NO sidebar entry, NO store slice (YAGNI — revisit in Epic D if migration demands it).
  - Unknown-area references (task or epic `area` not matching an existing area file) are `backlog_validate` WARNINGS + rejected at write time by the area-aware tools (mirror the unknown-epic check `taskmaster_v3.py:4316-4317`).
- Suites: `python -m pytest tests/ -q` (baseline 1467 passed, 1 skipped) and `node --test tests/unit/*.test.js` from `viewer/` (baseline 315) — green after every task.
- Commit per task, explicit pathspecs only. Never touch `.taskmaster/` data in other projects.
- **~80 tests use the `tm_epic_phase` conftest fixture (`tests/conftest.py:104`) calling `backlog_add_epic` without `done_when` — Task 2 MUST update the fixture and every direct `backlog_add_epic` call in tests in the same commit as the gate, or the suite shatters.**

---

### Task 1: Area entity — v3 module + 4 MCP tools

**Files:**
- Modify: `taskmaster/taskmaster_v3.py` (new Areas section — mirror the notes block at `:3369-3560` per INV-§3a)
- Modify: `taskmaster/backlog_server.py` (4 tools mirroring note tools `:3712-3805` per INV-§3b; imports near `:118-124`)
- Modify: `taskmaster/backlog_server.py` `backlog_init` subdir tuple `:1771` (add `"areas"`), `taskmaster_v3.py` `_CANONICALIZE_ITEMS` `:1084-1095` (add `"areas"`)
- Test: create `tests/test_areas.py`; modify `tests/test_mcp_v3_exposure.py` (new `test_area_tools_exposed` mirroring the ideas block `:105-116`; raise the `:175` floor 19→23), `tests/test_dead_tool_cull.py` KEPT list

**Interfaces:**
- Produces (taskmaster_v3.py): `area_dir(bp)`, `area_path(bp, area_id)`, `list_area_ids(bp) -> list[str]`, `write_area(bp, fm: dict, body: str)`, `read_area(bp, area_id) -> tuple[dict, str]`, `update_area(bp, area_id, updates: dict)`, `_validate_area(fm)` (requires kebab-case `id`, nonempty `name`; rejects any `status` key).
- Produces (backlog_server.py): `backlog_area_create(area_id, name, description="", anchors=None)`, `backlog_area_get(area_id)`, `backlog_area_list()`, `backlog_area_update(area_id, field, value)` — registered MCP tools. Frontmatter shape: `{id, name, description, anchors: [], created}`.
- Consumes: nothing new.

- [ ] **Step 1: Write `tests/test_areas.py` (failing)** — create/get/list/update round-trip; kebab-case rejection (`"Desktop App"` → error); duplicate-id rejection; `status` field rejection on create AND update; anchors list round-trip; file lands at `.taskmaster/areas/<id>.md`; empty list on fresh backlog. Use the `tm_backlog` conftest pattern (`conftest.py:66-73`). Also add the exposure block + floor bump.
- [ ] **Step 2: Run** `python -m pytest tests/test_areas.py tests/test_mcp_v3_exposure.py -q` — expect FAIL (tools missing).
- [ ] **Step 3: Implement** the v3 module + tools, copying the notes module's structure symbol-for-symbol (INV-§3a table), then init/canonicalize additions.
- [ ] **Step 4: Full suite green** — `python -m pytest tests/ -q`.
- [ ] **Step 5: Commit** — `feat(4.1): Area entity — sidecar module + 4 MCP tools (epic B task 1)`.

### Task 2: `done_when` gate + `area` on epics

**Files:**
- Modify: `taskmaster/backlog_server.py` — `backlog_add_epic` `:5367-5403` (required `done_when` param, optional `area` param with existence check via `list_area_ids`), `ALLOWED_EPIC_FIELDS` `:5220` (+`done_when`, `area`), `backlog_update_epic` `:5253-5326` (`area` value validated against `list_area_ids`; empty `done_when` value rejected)
- Modify: `taskmaster/taskmaster_v3.py` — slim epic allowlist `:144` (+`done_when`, `area`)
- Modify: `taskmaster/backlog_server.py` — `backlog_validate` (`:1499+`): new warning "epic `<id>` has no done_when (pre-4.1 legacy) — set one via backlog_update_epic or convert to an area"
- Test: extend `tests/test_components.py`-style coverage in a new `tests/test_epic_done_when.py`; **fixture sweep**: `tests/conftest.py:104` (`tm_epic_phase` gains `done_when="all test tasks complete"`), plus every direct `backlog_add_epic(` call in tests — grep and update all (`test_e2e_v3_smoke.py:112,142`, `test_backlog_status_slim.py:53`, `test_complete_task_bug_gate.py:36`, `test_lifecycle_guards.py:14`, `test_epic_status.py`, `test_epic_detail_endpoint.py`, `test_design_lock.py`, `test_components.py`, and any others the grep finds)

**Interfaces:**
- Produces: `backlog_add_epic(epic_id, name, done_when, description="", status="planned", area="")` — new REQUIRED third param; rejection message exactly as the Global Constraints state. Epic dicts carry `done_when` (and `area` when set) in the slim index.
- Consumes: Task 1's `list_area_ids`.

- [ ] **Step 1: Write `tests/test_epic_done_when.py` (failing)** — create-without-done_when rejected with the exact message; create-with succeeds and round-trips through save_v3/load_v3 (slim survival — assert value present after reload); `backlog_update_epic(id, "done_when", "")` rejected; `area` set to unknown slug rejected, known slug accepted; `backlog_validate` warns on a legacy epic dict injected without done_when; `EPIC_HEAVY_FIELDS` untouched (import-assert).
- [ ] **Step 2:** `python -m pytest tests/test_epic_done_when.py -q` — FAIL.
- [ ] **Step 3: Implement** the gate + fields + validate rule. Then run the FULL suite and fix every fixture/direct-call failure by adding `done_when` (grep `backlog_add_epic(` across `tests/` — update ALL call sites in this same commit).
- [ ] **Step 4:** `python -m pytest tests/ -q` green; `python -m pytest tests/test_epic_phase_bodies.py -q` green specifically (heavy-fields assert).
- [ ] **Step 5: Commit** — `feat(4.1)!: done_when required on epics + epic.area field (epic B task 2)`.

### Task 3: `area` on tasks + write-time validation

**Files:**
- Modify: `taskmaster/backlog_server.py` — task `ALLOWED_FIELDS` `:4689` (+`area`), `backlog_add_task` (optional `area` param), `backlog_update_task`/`backlog_batch_update` (inherit via allowlist `:5897/:5929`)
- Modify: `taskmaster/taskmaster_v3.py` — `validate_task_write` (mirror unknown-epic block `:4316-4317` with unknown-area check via `list_area_ids`); task slim/index fields at `:123` region (+`area`)
- Modify: `taskmaster/backlog_server.py` — `backlog_list_tasks` (`:1021-1098`): add `area` filter param alongside the `epic` filter
- Test: create `tests/test_task_area.py`

**Interfaces:**
- Produces: tasks accept/round-trip `area`; unknown area rejected at write with message naming valid areas (mirror `_epic_names` style via a small `_area_names` helper); `backlog_list_tasks(area="desktop-app")` filters.
- Consumes: Task 1's `list_area_ids`; Task 2's patterns.

- [ ] **Step 1: Write `tests/test_task_area.py` (failing)** — add_task with valid/unknown/absent area; update_task area change; batch_update area; list_tasks area filter; slim round-trip through save/load.
- [ ] **Step 2:** FAIL run. **Step 3:** implement. **Step 4:** full suite green. **Step 5: Commit** — `feat(4.1): task.area field + area filter + write validation (epic B task 3)`.

### Task 4: Closeable derivation + server surfacing

**Files:**
- Modify: `taskmaster/backlog_server.py` — `_epic_stats` `:5721-5737` (add derived `closeable` bool to the returned stats), `backlog_epic_status` `:5741-5798` (closeable line + `done_when` display), `backlog_status` epic table `:881-893` (closeable marker on the row), `_load_epic_full` `:6507-6548` (add `closeable` + `done_when` + `area` to the payload)
- Test: extend `tests/test_epic_status.py` and `tests/test_epic_detail_endpoint.py`

**Interfaces:**
- Produces: `_epic_stats(...)["closeable"]` (True iff `total>0 and done+archived==total`); `GET /api/epic/<id>` payload carries `closeable`, `done_when`, `area`; `backlog_status` epic rows show `[closeable]` when true; `backlog_epic_status` prints `Done when: <text>` and `⚑ CLOSEABLE — all N tasks done; archive via backlog_archive_epic` when applicable.
- Consumes: Task 2's fields.

- [ ] **Step 1: Failing tests** — epic with all tasks done reports closeable in `backlog_epic_status` output and `_load_epic_full` payload; epic with open tasks doesn't; zero-task epic doesn't; archived-tasks-count-as-done case (mirror `test_epic_status.py:57` math).
- [ ] **Step 2:** FAIL run. **Step 3:** implement. **Step 4:** full suite green. **Step 5: Commit** — `feat(4.1): closeable epics derived + surfaced in status/epic-status/epic API (epic B task 4)`.

### Task 5: Viewer — area axis + closeable badges

**Files:**
- Modify: `viewer/js/screens/kanban.js` — `DEFAULT_FILTERS` `:21-28` (+`area`), group-by options `:111` (+`['area','Group: Area']`)
- Modify: `viewer/js/lib/filters.js` — `applyFilters` `:9-36` (area predicate beside epic `:21`), `groupTasks` `:76-112` (area branch beside epic `:98-110`)
- Modify: `viewer/js/screens/table.js` — `COLUMNS` `:14-33` (+area column beside epic `:25-26`), filter state/hydrate/predicate/chip-rail `:49/:90-94/:107-122/:138-142` (+area)
- Modify: `viewer/js/lib/epic-format.js` — new `closeableBadge(stats)` helper beside `progressPercent` `:22-27`
- Modify: `viewer/js/screens/epics.js` — closeable badge in the row `:49-54`, `done_when` as row tooltip/subline
- Modify: `viewer/js/components/epic-detail-document.js` — closeable badge at the status pill `:50`, `done_when` block in the header region `:45-56`, `area` shown when set
- Test: create `viewer/tests/unit/epic-closeable.test.js` (badge helper + epics-row rendering); extend existing filter/group unit coverage if `lib/filters.js` has a unit file (check `viewer/tests/unit/` — if none exists for filters, add area cases to the new test file by importing `applyFilters`/`groupTasks` directly)

**Interfaces:**
- Consumes: Task 4's `GET /api/epic/<id>` payload (`closeable`, `done_when`, `area`) and `GET /api/backlog` epic slims (`done_when`, `area` — verify the backlog route's heavy-strip passes slim fields through; they will, being slim).
- Produces: area group/filter on kanban + table; closeable badge (tinted background or full border per design rules — **NO colored left rails, NO hover motion, NO box-shadows**).

- [ ] **Step 1: Failing unit tests** (`node --test tests/unit/epic-closeable.test.js` from `viewer/`) — `closeableBadge` returns markup iff closeable; `applyFilters` area predicate; `groupTasks` area branch groups + labels ungrouped as "No area".
- [ ] **Step 2:** FAIL run. **Step 3:** implement (match each file's existing idiom; kanban chips/stepper untouched — area arrives as filter+group only). **Step 4:** `node --test tests/unit/*.test.js` all green (315 + new); `python -m pytest tests/ -q` untouched-green. **Step 5: Commit** — `feat(4.1): viewer area filter/group axis + closeable badges (epic B task 5)`.

### Task 6: Playbooks, docs, version 4.1.0

**Files:**
- Modify: `playbooks/init-taskmaster/playbook.md` (`:90-93` region: first-epic guidance teaches `done_when` + suggests defining 3-6 areas first; `references/analysis-mode.md:14,24,28` epic-creation mentions gain done_when)
- Modify: `playbooks/start-session/playbook.md` (`:54` dashboard line: mention closeable epics when present)
- Modify: `playbooks/taskmaster/references/routing-table.md` (`:18` "plan out this epic" row: note done_when requirement; add area-management row → `backlog_area_*`)
- Modify: `adapters/agents-md/AGENTS.md`, `adapters/codex/AGENTS.md` (mirror the routing additions), run `python scripts/check_adapter_coverage.py`
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json` → `4.1.0`; `CHANGELOG.md` new section:

```markdown
## 4.1.0
**Areas + finite epics.** New first-class Area entity (long-lived subsystem: `backlog_area_create/get/list/update`, sidecar files under `.taskmaster/areas/`, no status lifecycle). Epics are now finite: `backlog_add_epic` requires `done_when` (an epic that can't say when it's done is an area); epics whose tasks are all done surface as "closeable" in backlog_status, backlog_epic_status, and the viewer. Tasks and epics carry an optional `area` field (validated against existing areas); kanban and table gain an Area filter/group axis. `backlog_validate` warns on legacy epics without `done_when`. Existing backlogs load unchanged. Second of four 4.x epics (lessons removal → **areas** → release trains → migration tooling).
```

- Test: budget checks via `tests/skill_budget_helper.py` for every edited playbook (all must stay under caps); lint tests green
- Verification battery: full pytest; viewer units; adapter coverage; `python -c "from taskmaster import backlog_server"`; grep sanity `grep -rn "done_when" playbooks/ | wc -l` ≥ 2

- [ ] **Step 1:** playbook edits + budget verification (run the helper, don't guess). **Step 2:** version bump + CHANGELOG (verbatim above). **Step 3:** full battery green. **Step 4: Commit** — `chore(4.1): version 4.1.0 — areas + finite epics complete (epic B)`.

---

## Self-review notes

- Spec coverage: Area entity ✔ (T1), tasks+epics carry area ✔ (T2/T3), done_when required + exact rejection ✔ (T2), closeable surfaced in status calls/start-session/viewer ✔ (T4/T5/T6), validate warning ✔ (T2), viewer area axis ✔ (T5). Spec's "code-path anchors (ties into project.yaml repos)" shipped as free-form anchor strings on the Area entity (INV-§8: no manifest field to extend; repo-key validation deferred to Epic D where real data exists).
- The `done_when` requirement is technically breaking for MCP callers of `backlog_add_epic`, inside a minor bump — acceptable: 4.x majors are reserved for schema/data breaks per the epic ladder, and the CHANGELOG names it. (Flag to the final reviewer.)
- Epic A (release trains) follows; its plan gets written against post-B master.

---

## Appendix — Inventory (epic surface @ e953ea5)

*(Verbatim from the exploration report; §-references above point here.)*

# Epic B Inventory — Areas + finite Epics (tm 4.1 planning input)

Codebase root: `C:\Users\gruku\Files\Claude\taskmaster`
Package code: `C:\Users\gruku\Files\Claude\taskmaster\taskmaster\` (backlog_server.py, taskmaster_v3.py, project.py live directly here)
Branch: master, tm 4.0.0 (Lessons subsystem just removed)

This is an inventory, NOT a plan. All references are `file:line` as of the time of this scan.

**Two things Epic B ships:**
- **Area** — new first-class entity: id/name/description/optional code-path anchors, NO status lifecycle. Tasks carry `area`; epics carry `area`.
- **Finite epics** — `backlog_add_epic` REQUIRES `done_when` (reject without it: "an epic that can't say when it's done is an area"). Epics with all tasks done surface as "closeable" in status calls, start-session, viewer.

Confirmed greenfield: no existing `done_when`, `closeable`/`closable`, `finite`, or `area` references anywhere in the Python server, viewer JS, playbooks, or tests.

---

## 1. Epic data model

### On-disk shape (`backlog.yaml` → `epics:` list)
Epic dict created by `backlog_add_epic` — `backlog_server.py:5392-5399`:
```python
new_epic = {
    "id": epic_id, "name": name, "status": status,
    "description": description, "created": _now(), "tasks": [],
}
```
Additional keys set elsewhere: `archive_reason`/`archived` (archive, `:5351-5352`), `design_status` (`:5319`), `components` (`:5312`), `docs` (`:5300`), `max_tasks` (soft cap, read at `:3973`).

**Heavy-field split (v3):** `EPIC_HEAVY_FIELDS = ("description", "docs", "components")` — `taskmaster_v3.py:437`. Epics with heavy content spill to `epics/<id>.md`; the slim index in `backlog.yaml` keeps `id/name/status/design_status/created` + `tasks: []`. Slim epic key allowlist: `taskmaster_v3.py:144` `"epic": ("id","name","status","design_status","created")` — **`done_when` and `area` must be added here** to survive the slim round-trip, OR added to `EPIC_HEAVY_FIELDS` if they belong in the body file. (For a short scalar like `done_when`/`area`, add to the slim allowlist `:144`, NOT to heavy fields.)

### Create / update / archive / status tools (backlog_server.py)
- **`backlog_add_epic(epic_id, name, description="", status="planned")`** — `:5367-5403`. Validates `status ∈ VALID_EPIC_STATUSES`, kebab-case id, dedupe. **Epic B: add required `done_when` param; reject empty with the "…can't say when it's done is an area" message.** Also add optional `area`.
- **`backlog_update_epic(epic_id, field, value)`** — `:5253-5326`. Field allowlist `ALLOWED_EPIC_FIELDS = {"name","status","description","docs","components","design_status"}` (`:5220`). status/docs/components/design_status have special handlers (`:5273-5321`); everything else is a scalar set (`:5323-5324`). **Epic B: add `"done_when"` and `"area"` to `ALLOWED_EPIC_FIELDS`; validate `area` against known areas.**
- **`backlog_archive_epic(epic_id, reason="done")`** — `:5329-5364`. Cascades archive to all tasks. Unaffected by Epic B except a closeable epic is the natural archive trigger.
- **`backlog_epic_status(epic_id)`** — `:5741-5798`. Derived-on-read progress: design lock, status, progress bar, component rollup, attention list. **Progress numerator = `done + archived` over `total` (`:5762`) — this is the exact "all tasks done" signal for closeable.** Epic B: add a "closeable" line when `stats.total>0 and done==total`, and surface `done_when`.

### Constants / validation
- `VALID_EPIC_STATUSES = {"active","planned","done","archived"}` — `:5219`
- `ALLOWED_EPIC_FIELDS` — `:5220`
- `VALID_DESIGN_STATUSES` — `:5221`; `_validate_components` — `:5224-5250`

### Helpers (backlog_server.py)
- `_find_epic(data, epic_id)` — `:422-425`
- `_epic_names(data)` — `:532` (error-message list of valid epics)
- `_epic_status_label(status)` — `:549`
- `_component_rollup(data, epic_id)` — `:480-529`
- `_epic_stats(data, epic_id)` — `:5721-5737` (**keeps archived in `total`; `done+archived` numerator — closeable math source**)
- `_load_epic_full(epic_id)` — `:6507-6548` (viewer payload: merges heavy fields via `_load()`, adds `stats`, `component_rollup`, `attention`, slim `tasks`). **Epic B: add `closeable`/`done_when` to this payload (~`:6523-6528`).**
- `_task_context(data, task, epic)` — `:828-856` (pick-task epic context)

### Helpers (taskmaster_v3.py)
- `epic_file_path(backlog_path, epic_id)` → `epics/<id>.md` — `:858-860`
- Slim/merge: `_split_entity_for_v3`/`_merge_entity_from_v3` applied to epics in `save_v3` (`:3681-3711`) and `load_v3` (`:980-992`); write conditions mirror in `v3_files_for` (`:1042-1072`).
- Slim epic key allowlist — `:144` (see above; **add `done_when`/`area`**).
- Task→epic validation: `validate_task_write` builds `epic_ids` `:4298`, **unknown-epic check `:4316-4317`** — the template for "unknown area" validation.

---

## 2. Where epic data is READ

All read loops iterate `data["epics"][].tasks[]`. Key surfaces in `backlog_server.py`:
- **`backlog_status`** (dashboard) — `:865-900`; epic progress table rows `:881-893` (`_epic_status_label`, done/total, focus). **Closeable badge candidate in this table.**
- **Dashboard payload builder** (statusline/desk) — `:553-630`: `next_up` filtered to active epics (`:599-614`), `active_epic` = epic with most in-progress (`:617-630`).
- **`backlog_snapshot` / diff** — `:1947-1990`; per-task snapshot tracks `epic` (`taskmaster_v3.py:1305`, `:1397-1410`, tracked field `epic` at `:1410`).
- **`backlog_recap`** — iterates epics (`:662`, `:706-717`); recap table row per epic `:717`.
- **`backlog_phase_status`** — reads across epics (phase is orthogonal axis).
- **`backlog_next_available`** — `:1423-1495`; only todo tasks in `status=="active"` epics (`:1439-1440`, `:1487`).
- **`backlog_pick_task`** — `:4043-4165`; `_task_context` epic block.
- **`backlog_list_tasks`** — `:1021-1098`; `epic` filter param (`:1021`, `:1052`, `:1079-1080`).
- **`backlog_get_task`** — `:1135-1305`; epic context block (`:1214`, `:1264-1301`), epic docs surfaced.
- **`backlog_search`** — `:1313-1348`; matches epic name.
- **`backlog_dependencies`** — `:1370-1399`.
- **`backlog_release_notes`** — `:4457-4520`; `group_by="epic"` default.
- **`backlog_complete_task`** — `:4342-4451`; "next in epic" suggestion `:4445-4451`.

### HTTP routes serving epics (backlog_server.py `do_GET` ~`:7276`+)
- `GET /api/backlog` → full `{tasks, epics, phases, ...}` (heavy fields stripped) — the viewer's epic-list source.
- `GET /api/epic/<id>` → `_load_epic_full` (the only dedicated epic endpoint). **Epic B closeable/done_when rides here.**
- `GET /api/continuity` → decisions/handovers projection (not epics today).

---

## 3. New top-level entity wiring — the Area checklist (template: note/decision entities)

Decision + note entities are **self-contained sidecar modules** (per-file markdown, lazy-created dirs) — NOT registered in the central dispatch tables. Since Area has **no status lifecycle**, mirror **notes** (create/get/list/update/archive), not decisions (which add resolve/drop).

### 3a. v3 helpers (taskmaster_v3.py) — copy the note block `:3369-3560`
| Touchpoint | Note did it at | Decision did it at |
|---|---|---|
| `<kind>_dir()` | `:3369-3370` | `:2452-2454` |
| `<kind>_archive_dir()` | `:3373-3374` | — |
| `<kind>_path()` | `:3377-3379` | `:2480-2481` |
| `_resolve_<kind>_path()` | `:3382-3390` | — |
| `list_<kind>_ids()` | `:3393-3411` | `:2457-2468` |
| `next_<kind>_id()` (scans live+archived) | `:3414-3423` | `:2471-2477` |
| `write_/read_/update_/list_` | `:3433-3560` | `:2484+` |

**Central dispatch tables (decisions/notes SKIPPED these — decide whether Area needs them):**
- `ENTITY_KIND_BY_PREFIX` — `taskmaster_v3.py:572-579` + `_PREFIX_ORDER` `:579`; consumed by `entity_kind_of()` `:600-615`. DEC-/NOTE- absent. Add `"AREA":"area"` only if Area IDs must resolve through generic ID→kind dispatch or typed links.
- `LINK_TYPE_DOMAIN` — `:582-594`; `is_valid_link()` `:618-622`. Wildcard types (`relates_to`/`references`/`referenced_by`) already accept any endpoint *if* `entity_kind_of` recognizes the prefix. Add typed `(x,"area")` rows only for typed area links.
- No `CANONICAL_SECTIONS` (body-section map `:63-69`) entry needed unless Area bodies have named sections.

### 3b. MCP tools (backlog_server.py) — mirror note tools `:3712-3805`
`backlog_note_create :3712` · `backlog_note_list :3737` · `backlog_note_get :3760` · `backlog_note_update :3775` · `backlog_note_archive :3793`. Decision set (with lifecycle): `backlog_decision_create/list/get/resolve/drop/update` `:3302-3421`. Area → `backlog_area_create/get/list/update` (+ optional `archive`). Import helpers at top block `:118-124` or inline like notes.

### 3c. HTTP routes (backlog_server.py)
- `do_GET` dispatch chain `:7348-7572`; notes list `GET /api/notes` `:7562-7570`; decision get `GET /api/decisions/<id>` `:7544-7552`; terminal 404 `:7571-7572`.
- `do_POST` `:7666`+; notes create `POST /api/notes` `:7705-7731`; notes update/archive `:7733-7763`.
- Add `GET /api/areas` (near `:7562`), `GET /api/areas/<id>` (near `:7544`), `POST /api/areas` + `/api/areas/<id>/update` (near `:7705`/`:7733`).

### 3d. init / migrate / canonicalize
- `backlog_init` subdir tuple `("tasks","handovers","issues","snapshots","auto")` — `backlog_server.py:1771`.
- `_CANONICALIZE_ITEMS` — `taskmaster_v3.py:1084-1095`.
- Decision/note/idea dirs are created **lazily at first write**, never by init. For Area referenced by tasks/epics, pre-creating `areas/` at init `:1771` (+ canonicalizer `:1084`) is safer.

### 3e. conftest + exposure
- conftest subdir tuple `("tasks","handovers","issues","ideas")` — `tests\conftest.py:57`.
- Exposure test param blocks: handovers `:76-87`, issues `:91-102`, **ideas `:105-116` (closest full template)**, recap/snapshot `:120-128`, project structure `:131-139`, project manifest `:143-155` — `tests\test_mcp_v3_exposure.py`. Decisions/notes have NO exposure entry (covered by dedicated test modules). Add a `test_area_tools_exposed` block mirroring ideas `:105-116`.

### 3f. viewer client
- `viewer\js\api.js` — note methods `:154-159` (`notes`/`createNote`/`updateNote`/`archiveNote`); generic `get`/`post` `:93-94`. Add `areas`/`createArea`/`updateArea` near `:154`.
- `viewer\js\store.js` — only `ideas` is a first-class slice (`state.ideas :11`, `getIdeas :49`, `setIdeas :55`). Add an `areas` slice here only if Area needs reactive global state.

---

## 4. Viewer epic surface (`viewer\js\`)

### epics.js (list) — `screens\epics.js`
- Reads store, not HTTP: `store.getBacklog().epics` (`:21-23`), stats computed client-side `:38-44` (`tasks.filter(t=>t.epic===ep.id)`, total/done/archived, `progressPercent`).
- Renders `a.epic-row` `:45-55`: name `:51`, `design_status` badge `:52`, `done/total` `:53`, progress bar `:54`, color swatch `:48,50`, link `#/epic/<id>` `:47`.
- **Closeable badge:** inside `:49-54`; condition `stats.total>0 && stats.done+stats.archived===stats.total`; `done_when` already on `ep`.

### epic-detail.js — `screens\epic-detail.js` (thin) + `components\epic-detail-document.js`
- Fetches via HTTP: `getEpic(id)` → `GET /api/epic/:id` (`epic-detail.js:2,27`); mounts `epic-detail-document`.
- `epic-detail-document.js` fields: `design_status` badge `:41/:49`, `stats{done,total}` bar `:42/:53-56`, id `:48`, name `:52`, `status` pill `:50` (**closeable-badge anchor**), description+`_body` `:67-76`, `components`+`component_rollup` diagram `:79-102`, `attention[]` `:105-125`, `docs{}` `:128-144`.
- **`done_when` block + Closeable badge:** header `:45-56` (status pill `:50`); `stats.done/total` `:55` gives the signal (server-derived).

### kanban.js — `screens\kanban.js`
- Epic colors: `assignEpicColors` (`:12`, `:190`), passed to cards `:398,400`.
- Filter state `DEFAULT_FILTERS = {priorities,epics,phase,group_by,sort,search}` — `:21-28`; `applyFilters` `:207`. **Add `area` here.**
- Group-by dropdown options `status`/`phase`/`epic` — `:109-118` (`:111`). **Add `['area','Group: Area']` at `:111`.** Grouping executed `:311-312` (`groupTasks`).
- Epic chips row `:166-168`, `:280-308` (`renderEpicChips`); ranking `:270-278` (`countActiveTasksByEpic`, `rankEpics` from `lib\epic-ranking.js`). Phase-scoped epic pruning `:195-204`, `:259-265` (`epicsForPhase`).
- **Area filter axis plugs in:** `DEFAULT_FILTERS :21-28`, group option `:111` (and/or an Area chip row paralleling phase stepper `:162-168`), predicate+group branch in `lib\filters.js` (see below), tallies `:250-254`,`:316-317`.

### table.js — `screens\table.js`
- `COLUMNS` array `:14-33`; **epic already a column** `:25-26` (`{key:'epic',...,get:t=>t.epic,render:...}`). Add `area` column here.
- Filter state `{status,priority,epic}` `:49`, hydrate `:90-94`, predicate `applyFilters :107-122` (epic `:114`), chip-rail groups `:138-142` (epic group `:141`). **Add `area` to `:49`,`:90-94`,`:107-122`,`:138-142`** + hint/clear refs `:170`,`:186-202`,`:241`.

### api.js — `viewer\js\api.js`
- `getEpic(id)` → `GET /api/epic/:id` `:80-87`, re-exported `:100`. Epic list via `backlog()` → `GET /api/backlog` `:96`. Generic `get`/`post` `:93-94`. ETag handling task-scoped only `:11-30`.

### store.js — `viewer\js\store.js`
- State `{backlog,prefs,identity,issues,ideas,etags}` `:6-13`; epics NOT a slice — inside `state.backlog.epics`. `getBacklog` `:45`, `setBacklog` (jsonEqual guard) `:51`. No `getEpics()` selector; screens read inline. Populated by poll `main.js:99-101`.

### sidebar / router
- Sidebar nav `SECTIONS` array `components\sidebar.js:6-30`; Epics entry `:12` (`{key:'epics',icon:'⬡',label:'Epics',hash:'#/epics'}`). Active-sync via `meta.sidebarKey` `:96-108`. **Add "Areas" entry here.**
- Router `router.js` `registerScreen(prefix, loader)` `:15-17`, lazy import `:72`, `mount(el,{params,subpath,store,api,prefs})` `:82-86`.
- Screen route table `main.js:10-25`: epics `:14`, epic-detail `:15`. **Add `/areas` (+ optional `/area`).** CSS links `index.html:11-30` (epics.css `:16`, epic-detail.css `:17`) — add `areas.css`. Mount point `#screen-mount` `index.html:41`.

### Supporting libs
- `lib\filters.js` — `applyFilters :9-36` (epic `:21`), `groupTasks :76-112` (epic branch `:98-110`), `epicsForPhase :152-161`. **Area predicate + group branch here.**
- `lib\epic-format.js` — `progressPercent :22-27` (closeable math), `designBadge :12-14`, `DESIGN_STATUS :5-10`. Closeable badge helper fits here.
- `lib\epics.js` — palette + `assignEpicColors :16-30`, `epicCssVar :38-46`. Analogous `assignAreaColors`.
- `lib\epic-ranking.js` — `countActiveTasksByEpic`, `rankEpics`.
- Entity-edit infra `viewer\js\components\edit\` — `schema.js runValidation :6-19`, `edit\fields\`, `edit\forms\` (only `task-form.js`; **no `epic-form.js`**). A `done_when` epic field needs schema/form here. Epic edit path exists (`viewer\tests\epic-modal.spec.js`) — check `components\edit\entity-modal.js`.

---

## 5. Playbooks / skills mentioning epics (`playbooks\`, `skills\`)

- **start-session** `playbooks\start-session\playbook.md`: `:54` "**Dashboard:** epic progress summary" (**closeable-surfacing touch point**); `:68` empty-state guides to `backlog_add_epic`.
- **pick-task** `references\bundles.md:61` — bundle slug uses epic prefix.
- **taskmaster router**: `skills\taskmaster\SKILL.md:3` "planning epics"; `playbooks\taskmaster\references\routing-table.md:18` "plan out this epic → `backlog_add_epic`", `:59` "add a task under epic X"; `playbook.md:24` auto-epic redirect.
- **init-taskmaster** (creates epics): `playbook.md:5` (never write backlog.yaml directly; use `backlog_add_epic`), `:20`,`:45` clean-start, `:90-93` "create your first epic / 5-8 tasks per epic"; `references\analysis-mode.md:14,24,28`. **`backlog_add_epic` guidance here must teach `done_when`.**
- **check-todos** `references\scan-flow.md:12,81,87,97,99` — group TODOs into epics/areas (note: prose already uses "area" loosely).
- **bug** `entry-point-flows.md:11`; **issue** `entry-point-flows.md:19`, `auto-extraction.md:12` — infer `components` from epic/folder.
- **migrate-v3** `references\v2-vs-v3.md:13-16,27` — epic/task fields.
- **end-session**: ZERO epic mentions — touch only if closeable surfacing added to wrap-up.
- No playbook mentions `backlog_epic_status` by name; none mention `done_when`/`closeable`/`area` (all net-new).

---

## 6. Tests covering epics (`tests\`)

- **`test_epic_status.py`** — `backlog_epic_status`: counts+components `:27`, unknown `:35`, attention `:39-52`, **archived rollup `:57` (`2/2`, `Archived: 1`) = closeable-math analog**.
- **`test_epic_detail_endpoint.py`** — `_load_epic_full`: unknown→None `:23`, heavy-merge+rollup `:27`, attention `:51`.
- **`test_epic_phase_bodies.py`** — v3 split/merge; **`:11` asserts `EPIC_HEAVY_FIELDS == ("description","docs","components")`** (touch if `done_when` goes heavy — it should NOT); roundtrip `:23-43`, save/load `:68-83`, status persist `:104-108`, migrate counts `:145`, docs-clear `:209`.
- **`test_components.py`** — `backlog_add_epic`/`backlog_update_epic` design_status+components (field-validation pattern to mirror for `done_when`).
- **`test_design_lock.py`** — `backlog_update_epic` design_status gating `:7,:17,:41`.
- `backlog_add_epic` in many fixtures: `test_e2e_v3_smoke.py:112,142`, `test_backlog_status_slim.py:53`, `test_complete_task_bug_gate.py:36`, `test_lifecycle_guards.py:14`, etc.
- `backlog_archive_epic` — guarded in `test_dead_tool_cull.py` `KEPT` list `:26`, exposure `:38`.

### MCP exposure floor
`tests\test_mcp_v3_exposure.py::test_full_v3_surface_count` — **`:175` `assert len(v3_tools) >= 19`** (history 38→36→30→19 in comments `:169-174`). **Adding Area tools raises this floor.** Companion `test_dead_tool_cull.py` (`CULLED`/`KEPT` lists).

### conftest fixtures
`tests\conftest.py`:
- `tmp_taskmaster` `:30`; backlog dict `:66-73` (`epics: []`); subdir tuple `:57` `("tasks","handovers","issues","ideas")`.
- **`tm_epic_phase` `:100-106`**: `:104` `backlog_add_epic(epic_id="test-epic", name="Test Epic")` — **used by ~80 tests; a required `done_when` breaks all of them unless `backlog_add_epic` defaults it or the fixture passes it.**
- Inline epic dicts vary: link tests use `{"id","title","tasks"}` (note: `title` not `name`); others `{"id","name","tasks"}`. `test_epic_phase_bodies.py:24` uses fullest `name/status/description/docs/components/tasks`.

---

## 7. `.taskmaster/epics/` on disk

The taskmaster repo has **no `.taskmaster/` dir** (fresh split — no self-hosted backlog). Epic bodies DO live as files when heavy: `epic_file_path` → `epics/<id>.md` (`taskmaster_v3.py:858-860`), written by `save_v3` `:3681-3711`, merged by `load_v3` `:980-992`, only when the epic has `description`/`docs`/`components` or a body (write condition `:3687`/`:1059`). Epics without heavy content stay inline in `backlog.yaml` with `tasks: []`. The `epics/` subdir is created lazily by `write_task_file`'s `mkdir(parents=True)`, not by init.

---

## 8. Additional Epic-B touchpoints

- **Task carries `area`:** add `"area"` to `ALLOWED_FIELDS` — `backlog_server.py:4689` (also used by `backlog_batch_update` `:5897`/`:5929`, `backlog_add_task` `:3836+`, `backlog_update_task`). Adjacent existing fields: `anchors`, `component`.
- **Epic carries `area`:** add `"area"` to `ALLOWED_EPIC_FIELDS` `:5220`; add `area` param to `backlog_add_epic` `:5367`.
- **Unknown-area validation:** `validate_task_write` — `taskmaster_v3.py:4316-4317` (unknown-epic block is the exact template); load areas via a new `list_area_ids(bp)` rather than from `data` (areas live in files). Mirror for epic writes.
- **`backlog_validate`** (integrity checker) — `backlog_server.py:1499-1560+`: done-without-completed `:1518`, dangling deps `:1529`, docs-path-not-on-disk `:1533`, cycles `:1554+`. **No epic/area rule today. Epic B adds: "legacy epic without `done_when` → warn"** here (and optionally "task/epic references unknown area → warn").
- **`backlog_batch_update`** — `:5897`; task fields via `ALLOWED_FIELDS` `:5929`, epics via `update_epic` op. Accepts `area` once added to the allowlists.
- **project.py** — `ProjectManifest` `:181-192`. No existing Area/subsystem/component entity. Closest for code-path anchors: `Repo.name`/`Repo.path` `:75-76`, `KnowledgeLink.path` `:133`, `extensions` `:192`. If Area anchors reference real repos, validate anchor repo keys against `ProjectManifest.repos` (`repo(name)` `:198-199`). Area's anchors otherwise live in the Area entity's own frontmatter (repo name + path/glob list) — genuinely new, no project.yaml field to extend.
- **continuity projection** — `taskmaster_v3.py:continuity_items() :4526-4560+` (decisions `:4554-4560`), served at `GET /api/continuity`. Areas likely get their own board, not the continuity rail — but this is where a projection would plug in.

