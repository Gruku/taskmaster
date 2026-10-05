<!-- User intent: re-skin every viewer screen onto the shared RR components in five parallel tracks, so the whole viewer reads as one system in both themes, by keyboard and at phone width — and every defect earlier plans deferred to "the screens" lands somewhere instead of getting lost. -->

# Viewer × Reality Reprojection — Plan 3: Screens (index)

> **For agentic workers:** this file is the index for five track plans. Each track plan is executed with superpowers:subagent-driven-development (recommended) or superpowers:executing-plans, task by task. This index's Global Constraints, File Ownership and Review Focus apply to every track. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every screen of the viewer rebuilt on the plan 1/2a/2b foundation (tokens, shell, modal shell, markers, fields, popover, chips, overflow row, link rows, sortable headers, `truncate()`, state blocks), matching spec §6 in dark and light at 1440px and 390px, with the audit findings for those screens closed and the carried defects fixed.

**Architecture:** Five independent tracks, one per group of screens, each in its own worktree and branch, merged locally `--no-ff` into `feat/viewer-reality-reprojection` by the controller one at a time. Screens consume shared components; they never restyle them. A change a track needs in a shared component is made only by that component's owner (see File Ownership); other tracks consume it after it merges.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core; the server side is Python (`taskmaster/backlog_server.py`, pytest).

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §4 (row 1), §5 (consumption), §6 (all screens), §7, §8, §9 stage 4, §11 amendments (they override earlier sections). Audit: `docs/specs/2026-10-01-viewer-audit.md` (finding IDs). Carry-in: `.superpowers/sdd/plan-3-4-carries.md` (every item plans 1, 2a and 2b deferred to plan 3, grouped by track, with sources).

**Depends on:** Plan 2b (HEAD 638acec): `openPopover`, `overflowRow`, `chipRow`/`filterChip`, `linkRow`, `sortHeader`, `truncate`, the conflict banner, the topbar controls (`claimTopbar`, `claimTopbarPrimary`, `setTopbarCount`, `tmSearch`, `tmSegmented`, `tmAction`) with row 2's "Filters" overflow, `describeWriteError`, the modal shell and the entity form, `marker()` and the status maps, `stateBlock`, `renderMarkdown`, `formatStamp`, `icon()`.

## Track plans

| Track | Plan | Screens |
|---|---|---|
| 3a | `2026-10-06-viewer-rr-3a-kanban-detail-modal.md` | Kanban, the detail modal, topbar row 1 at phone width |
| 3b | `2026-10-06-viewer-rr-3b-table-epics.md` | Table, Epics, Epic detail |
| 3c | `2026-10-06-viewer-rr-3c-detail-pages.md` | Task detail, Issue detail, Bug detail (full pages) + the bug route (§7) |
| 3d | `2026-10-06-viewer-rr-3d-issues-bugs-ideas.md` | Issues, Bugs, Ideas |
| 3e | `2026-10-06-viewer-rr-3e-sessions-archived-dashboard-settings.md` | Sessions, Archived, Dashboard, Settings, the generic right rail |

## Global Constraints

Plan 2b's constraints carry over unchanged (they are restated here so a track implementer needs only this file and their track plan):

- No `box-shadow`. No `transform`/`translate`/`scale`/`rotate` in any `:hover` rule. No colored left border (only `var(--border-*)` colours on `border-left`). No `outline: none|0`. No italic. No text below 11px (11px only for uppercase labels). No named colours, hex or rgb literals outside `tokens.css`.
- Focus ring is the global one: `outline: 2px solid var(--border-focus); outline-offset: 2px`. A component may move the ring to a wrapper (precedent `.tm-search`); it never restyles it.
- Signature-hued text uses `--text-accent` only. Text on a signature-tinted fill is `--foreground-bold`. Text on a solid `--signature-fill` is `--on-accent-fill`. Known misses in light: never put `--text-accent` on `--col-bg` or `surface-overlay`.
- Cards and rows use `--card-bg` / `--card-bg-hover`; modals, popovers and the banner use the `--overlay-surface*` roles. Never `--surface-raised` directly, never a legacy alias (`--bg-*`, `--ink*`, `--accent*`, `--amber`, `--green`, `--red`, `--sp-*`, `--r-*`, `--text-*` other than `--text-accent`, `--t-*`, `--border`, `--border-soft`).
- Status, severity and priority are a shape plus a word via `marker()` / the status maps; the shape carries the hue, the word is a `foreground` colour. No screen keeps its own status colours or abbreviations ("Cr/Hi", "In Progress").
- Every CSS file a task creates or rewrites is added to `ENFORCED` in `viewer/tests/unit/style-rules.test.js` and passes. A track that rewrites a screen's CSS file adds it. Read that test before writing CSS.
- No `window.confirm`, `window.alert` or `window.prompt`. No `innerHTML` with task or user data except through `renderMarkdown()`.
- Every animation has a `prefers-reduced-motion` fallback (the global rule in `tokens.css`; do not defeat it).
- Every new file starts with a 1–3 line `User intent:` header.
- **One popover, one overflow row.** Anything that floats opens through `openPopover()`; any row that must stay on one line uses `overflowRow()` / `chipRow()`. Stacking ladder: page popover 70, mobile drawer 80, modal overlay 85, conflict banner 90.
- **Real controls only.** Cards and list rows are `linkRow()` (`<a href>` with sibling controls) or `<button>` when not navigation; nothing interactive inside anything interactive (axe `nested-interactive` zero). Sortable headers are `sortHeader()`.
- **Cut text keeps its words** (`truncate()` or `title`).
- **Touch targets** at ≤768px: every chip, row control, tab, popover item and button is at least 44px tall.
- **Refused writes are words** through `describeWriteError()`.
- **Empty, not-found and error states** are `stateBlock()`: Technical label → one Narrator sentence → at most one action. No raw API strings.
- **Primary action in row 1.** A screen's one primary action is appended to `claimTopbarPrimary()` (row 1), never to row 2. `claimTopbar()` already clears row 1's primary and count slots on every route. Row 2 holds search, view switcher and filters only.
- **Tests never touch a live backlog server.** Mocked specs call `mockApi` before `page.goto` and assert `unmockedWrites(page)` is empty in `afterEach`. Live-server specs (`smoke.spec.js`, specs needing `TM_LIVE_SPECS_OK=1`) are not run. A track that replaces a live spec's coverage writes a `*.mock.spec.js` for it.
- **One vocabulary across screens.** A not-found state is `stateBlock` with `data-state="missing"` (never `not-found`). A screen's count in row 1 reads "n <noun> · m visible" (e.g. "24 issues · 12 visible"), never "m of n". Each track appends its own spec §11 bullets in its verification task (append-only); the controller records none on a track's behalf.
- **Mock builders take the theme.** Every `<screen>Mocks()` builder in `mock-fixtures.js` takes `{ theme = 'dark' } = {}` and sets the prefs theme from it, merging any other prefs it needs into the same object.
- **Every screen has a mocked spec plan 4 can reuse.** Plan 4's accessibility gate builds its route table from the screens' mocked specs: each screen a track owns ends plan 3 with a `*.mock.spec.js` whose `mockApi` table is a named export (or built by a named function in `mock-fixtures.js`) and whose "content is loaded" selector names real content (a card, a row, the record's heading), never something an error block would also match. Each track's verification task lists them in its report.
- **Git:** each track works in its own worktree `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3<x>` on branch `rr3/<x>-…` branched from the integration HEAD; no pushes, no amends, never `git add -A`; stage only the files the task names; never merge. Never chain `cd`; use `env --chdir=<wt>`, `npm --prefix <wt>/viewer`, `git -C <wt>`.

Commands (cwd never changed; `<wt>` = the track's worktree):

- One unit file: `env --chdir=<wt> node --test viewer/tests/unit/<file>`
- Unit suite: `npm --prefix <wt>/viewer run test:unit`
- Mocked specs: `MOCK_PORT=<track port> npm --prefix <wt>/viewer run test:mock -- --workers=2 <spec file or -g "title">` — ports: 3a 8831, 3b 8832, 3c 8833, 3d 8834, 3e 8835. The port must have no listener when a task finishes.
- Server: `env --chdir=<wt> C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider` (bound it with `timeout 900`; on Windows the process can hang after printing 100% — count the dots and failures in the output)
- Screenshots: `node <wt>/viewer/tests/tools/capture-modals.mjs <dir>` (mocked, static-served); output under `.superpowers/sdd/<plan dir>/shots-<task>/` in the `viewer-rr` worktree; never commit images.

## File ownership

Parallel tracks touch disjoint files. A file not listed here belongs to the track whose screen imports it alone; a shared file not listed is read-only to every track (raise NEEDS_CONTEXT instead of editing it).

| File(s) | Owner | Others |
|---|---|---|
| `viewer/js/screens/<screen>.js`, `viewer/css/screens/<screen>.css` | the track that owns the screen | — |
| `viewer/js/lib/topbar.js`, `viewer/css/shell.css` (row 1 rules) | 3a (row 1 at phone width) | consume only |
| `viewer/js/lib/epics.js` (`EPIC_PALETTE` → `--cat-N` swatches) | 3a | 3b consumes after 3a's task merges |
| `viewer/js/components/card.js`, `filters.js`, `epic-chips.js`, `epic-dropdown.js`, `priority-chips.js`, `detail-modal.js`, `bundle-frame.js` | 3a | — |
| `viewer/js/lib/epic-format.js` (new, one progress function, ED-01) | 3b | 3e's Dashboard consumes after it merges |
| `viewer/js/components/task-detail-document.js`, `task-detail-graph.js`, issue/bug detail templates, `taskmaster/backlog_server.py` bug route, `tests/` pytest for it | 3c | 3a's detail modal consumes the document (no edits) |
| `viewer/js/components/right-rail.js`, `viewer/css/components.css` `.right-rail` block | 3e | 3c consumes |
| `viewer/js/components/status.js` (maps) | 3d (idea/bug/issue rows need their maps complete) | others consume; a missing value elsewhere → NEEDS_CONTEXT |
| `viewer/js/components/epic-detail-document.js`, `component-diagram.js` | 3b | 3a adds one guard line to `component-diagram.js` (`if (ev.target !== block) return`) in its card task; 3a's detail modal consumes the epic document unchanged |
| `viewer/css/screens/detail-pages.css` (new) | created by 3d Task 2 (rules moved verbatim from `issues.css`/`bugs.css`), then 3c | 3c deletes the moved rules once its pages stop using them; 3c never edits `issues.css`/`bugs.css` |
| `viewer/js/components/stale-tag.js`, `column-tabs.js` (new), status maps `ISSUE_STATUS`/`SEVERITY`/`severityKey`/`severityMeta`/`severityMarker` | 3d | 3c consumes the maps and `staleTag(issue, agingCfg)`; 3a consumes `columnTabs` (tab id `${panelId}-tab`; `update()` reuses each key's button) |
| `viewer/css/components/handover-status.css`, new `components/right-rail.css` | 3e | 3c consumes `railPanels`/`statusPill` unchanged |
| `gate-pipeline.js` (`renderGatePipeline`), `conflict-banner.js`, `inline-field.js`, `task-actions.js`, `entity-modal.js` (additive `title`/`eyebrow`/`saveLabel`), `api.js` `updateBug`/`promoteBugs` | 3c | — |
| `viewer/tests/task-detail.mock.spec.js` | 3a and 3c (named lines) | controller resolves at the second merge |
| `viewer/tests/shell.mock.spec.js` | 3a (Kanban "Add task" lookups; the parked-chip-row test), 3b (four Table tests; the parked-Filters leave test made self-contained), 3d (the Ideas "Filters counts the controls it holds" test) | controller resolves at each later merge |
| `viewer/tests/unit/detail-pages-css.test.js` (new) | created by 3d Task 2, then 3c (Tasks 7/8 update it as they delete the moved rules) | — |
| `viewer/tests/unit/component-diagram.test.js` | 3b (3a Task 3 adds its guard's test) | — |
| `viewer/tests/unit/topbar.test.js`, `viewer/css/components/toolbar.css` (the `.on` bridge) | 3a | 3c Task 5 may add a test line to `topbar.test.js` after 3a Task 2 merges |
| `viewer/tests/threads-board.spec.js` (selectors) | 3e | — |
| `viewer/index.html` stylesheet links, `ENFORCED` in `style-rules.test.js`, `viewer/tests/mock-fixtures.js`, `viewer/tests/tools/capture-modals.mjs`, spec §11 | shared, append-only | merged as the union of both sides; never reorder or delete another track's lines — inserting a stylesheet link where the cascade needs it is allowed (3d changes one existing `openIdeas` line in `capture-modals.mjs` because "New idea" moves to row 1) |
| Legacy rules in `components.css` (`.tm-card`, `.tm-empty`, `.cmp-*`, old `.ho-status-pill`), migration aliases in `tokens.css` | plan 4 | no track deletes them, even when its last consumer goes |

## Review Focus (all tracks)

The five conditions most likely to bite a person using the re-skinned viewer that no single track's tests would otherwise catch. Each track plan pins the ones that touch its screens with a named test.

1. **A screen at 390px with real data volume** (27 epics, 230 tasks, 24 issues, long IDs like `T-1234`, 120-character titles). Nothing scrolls sideways at page level, no text breaks mid-ID, every cut text has its `title`, and the page height stays bounded (the audit measured a 52,824px Kanban). → every track's verification task measures `scrollWidth <= innerWidth` and page height on its screens at 390 with the long-data fixture.
2. **Light theme** on every screen. Accent text never sits on `--col-bg` or an overlay surface; pills, swatches and markers pass AA. → every track's verification task runs axe on each of its routes in both themes with zero `color-contrast` violations (the Table pills that failed in 2b are 3b's).
3. **Keyboard-only use** — Tab reaches every control in visual order, Enter/Space activate cards, rows, chips and headers, Escape closes the topmost thing only, and focus returns to the opener. → each track pins one keyboard walk per screen.
4. **Another writer changes the data while the screen is open** (the poll or a store notification redraws). Open popovers close with their anchor, focus is not dropped to `<body>`, an open inline editor is not re-mounted over (B-095 for the detail modal). → 3a (detail modal, Kanban poll), 3c (detail pages), 3d (lists).
5. **Leaving a screen** with a popover open, filters parked behind "Filters", or a request in flight. No page error, no listener or observer left behind, the next screen lays out from scratch. → each track's screens get a "leave with X open" test.

## Order and parallelism

All five tracks start together from the integration HEAD (wave 1). Inside a track, tasks run in that track's own order. Cross-track waits:

| Waiting task | Waits for (merged) | Merge-first in the owning track |
|---|---|---|
| 3b Task 6 (epic swatches) | 3a Task 1 (`lib/epics.js` on `--cat-N`, `epicIndex()`, `longBoard()`) | 3a Task 1 |
| 3b Task 7 (row 1 count and Add task); 3c Tasks 3, 9 (Edit / Mark fixed as row-1 primary); 3d Tasks 4, 7, 9 (row-1 counts, New idea); 3e Tasks 2, 3 (row-1 counts) | 3a Task 2 (row 1 at 390: the count shrinks first, a primary shows its icon only at ≤768px) | 3a runs Tasks 1, 2 first |
| 3a Task 8 (Kanban columns, phone tabs) | 3d Task 6 (`columnTabs`) | 3d runs 1, 2, 6 first |
| 3c Tasks 7 and 8 (issue and bug pages) | 3d Tasks 1 and 2 (status maps + `stale-tag.js`; `detail-pages.css`) | 3d Tasks 1, 2 |
| 3c Task 10 (verification) | 3e Task 1 (right rail, failed-status message) | 3e Task 1 |

3e's Dashboard summary strip shows no epic progress, so it does not wait on 3b's `epic-format.js`. Its "In progress" and "Waiting on you" links go to `#/table?status=in-progress` / `in-review`, which the Table reads to seed its status filter (3b).

The controller merges tasks as they pass review, one at a time, and runs the unit + mocked suites on the integration branch after each merge. A track whose task depends on another track's unmerged task waits; it does not copy the change.

Final step (controller, after all tracks): full unit, mocked and server runs on the integration branch, reported as exact pass/fail counts (pytest: count dots, `F` and `E` if the process hangs at exit); `capture-modals.mjs` for every scene in both themes and widths; a fresh-context reviewer compares the screenshots with spec §6. Then plan 4.
