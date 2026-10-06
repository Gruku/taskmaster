<!-- User intent: re-skin the Kanban board, the task detail modal and topbar row 1 onto the RR shared components — cards that are real links, filters that stay on one line, a board that is one column at a time on a phone — and clear the board's audited debt (KB-01..KB-12) and the detail modal's carried defects, B-095 first among them. -->

# Viewer × Reality Reprojection — Plan 3a: Kanban, the detail modal, topbar row 1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Kanban screen (cards, phase strip, priority and epic filters, row-2 controls, columns, the phone column switcher), the task detail modal and topbar row 1 rebuilt on the plan 1/2a/2b foundation, matching spec §6 Kanban in dark and light at 1440 and 390, with KB-01..KB-12, the detail modal's carried defects and B-095 closed, and the epic palette moved onto `--cat-N` for the tracks that consume it.

**Architecture:** The card becomes a `linkRow()` laid out on a CSS grid, so the whole card is one link and its copy/doc controls are siblings above it. The phase strip, the priority row and the epic row are built once at mount and updated in place on every paint (`chipRow().update()`, a new `phaseStrip().update()`), so focus and open popovers survive the board's poll. Row 2 keeps search, density and the two labelled selects; the count and "Add task" move to row 1, which learns to fit at 390. At ≤768px the board shows one column at a time behind a `role="tablist"` switcher. The detail modal re-checks the edit lease after its fetch (B-095) and lifts the title's refusal out of the scrolling body.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §4 (row 1), §5.1, §5.3, §5.6, §5.8, §5.9, §5.10, §6 Kanban, §11 (amendments override). Audit `docs/specs/2026-10-01-viewer-audit.md` §3 (KB-01..KB-12), §6 (DM-01..DM-03), §18 (X-01, X-03, X-07, X-12, X-13). Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "3a" and its "Controller allocation" (overrides placement), backlog bug B-095 (claude-tools).

**Depends on:** Plan 3 index `docs/plans/2026-10-06-viewer-rr-3-screens.md` (binding) and plan 2b at integration HEAD 638acec: `linkRow`, `chipRow`/`filterChip`, `overflowRow`, `openPopover`/`openPopoverCount`, `truncate`, `claimTopbar`/`claimTopbarPrimary`/`setTopbarCount`/`tmSearch`/`tmSegmented`/`tmAction`, `marker`/`statusMarker`/`priorityMarker`/`TASK_STATUS`/`PRIORITY`, `stateBlock`, `icon()`, `rememberView`, `chipClickNext`/`CHIP_CLICK_HINT`.

## Global Constraints

The plan 3 index's Global Constraints, File ownership, Review Focus and commands apply unchanged; read them first. 3a adds:

- **Where:** `<wt>` = `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3a`, branch `rr3/a-kanban-detail-modal` from the integration HEAD. Mocked specs: `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 <spec>`. Captures: `node <wt>/viewer/tests/tools/capture-modals.mjs <dir> --port=8841`. Port 8831 and 8841 have no listener when a task finishes.
- **No new custom properties in CSS.** The style test refuses a `--x:` declared outside `tokens.css`, and 3a does not edit `tokens.css`. Category hues are classes (`.card-swatch--cat-N { background: var(--cat-N); }`); the only inline variable is `--epic`, written by `epicCssVar()` and already defaulted in `tokens.css`.
- **No Unicode glyphs as UI.** None of `⧉ ⎇ ⬢ ⛔ ↳ ▤ ▦ ‹ › ⌫ ⚲ ★ ☆ ↗ ▾ 📄` is emitted by any file 3a touches; glyphs come from `icon()`, states from `marker()`.
- **Built once, updated in place.** A Kanban control that a paint can touch (phase strip, priority row, epic row, Epic options button, column toggles, tabs) keeps its element across paints, or its focus is restored by the board's focus keeper (Task 8). `kanban.js`'s cleanup destroys every overflow row and closes every popover it opened.
- **Counts mean open tasks.** Every count on a Kanban filter (phase-scoped priority and epic chips, the Epic options list) is the number of tasks whose status is neither `done` nor `archived`, from one function (`countOpen`, Task 6). The group labels' `title` says so.
- **Cross-track wait:** Task 8's phone switcher consumes 3d's `columnTabs` (3d Task 6) instead of building a second tablist.
- **Shared files 3a edits** (append-only or a named line): `viewer/index.html` (append `css/components/card.css` and `css/components/detail-modal.css` at the end of the stylesheet links, after every existing line, so both win over `kanban.css`, `task-detail.css` and `modal.css`), `ENFORCED` (append `components/card.css`, `components/detail-modal.css`, `screens/kanban.css`), `viewer/tests/mock-fixtures.js` (append `longBoard()`), `viewer/tests/tools/capture-modals.mjs` (`openCreate` and new scenes), `viewer/tests/task-detail.mock.spec.js` (three named lines), `viewer/tests/shell.mock.spec.js` (the two "Add task" lookups), `viewer/css/components/toolbar.css` (the `.on` bridge, lines 83–86, which its own comment hands to 3a), `viewer/js/components/component-diagram.js` (one guard line, Task 3), spec §11 (append, Task 9).

## Review Focus

The index's five conditions as they meet the board and the modal, each pinned by a named test in the owning task.

1. **The board at 390 with real volume** (27 epics, 230 tasks, ids to `T-1234`, 120-character titles, 8 phases). One column at a time, no sideways page scroll, the page ends where that column ends, no id breaks, every cut text has its `title`, row 1 holds title, count, "Add task" and the theme toggle on one line. → Task 8 "at 390 with 230 tasks one column shows, nothing scrolls sideways, and the page ends with that column"; Task 3 "a 120-character title is clamped to three lines and the id never breaks"; Task 2 "at 390 row 1 keeps title, count, primary and theme toggle on one 56px line".
2. **Another writer changes the task or the board while it is open.** An inline editor opened while a soft re-read is in flight is not re-mounted over (B-095); a poll keeps focus on the same card and on a pressed chip. → Task 4 "a re-read that lands after an inline editor opened leaves the editor alone (B-095)"; Task 8 "a poll that redraws the board keeps focus on the same card"; Task 6 "a poll keeps focus on a pressed chip and keeps Epic options open".
3. **Keyboard only.** Tab reaches search, row-2 controls, phases, priority, epics, then cards and their controls in visual order; Enter opens a card; Escape closes the topmost thing and focus returns to the card. → Task 8 "keyboard walk: row 2, phases, priority, epics, then the cards in order; Enter opens, Escape returns".
4. **Leaving the board with something open** (Epic options, More, the archived-phases menu, Filters). No page error, no popover, no observer left behind; the next screen lays out from scratch. → Task 6 "leaving the board with Epic options open leaves nothing behind".
5. **Light theme.** Swatches, markers, chips and popovers pass AA; no accent text on `--col-bg` or an overlay surface. → Task 3 "axe (light|dark): cards have no contrast, nested-interactive or aria violation"; Task 6 "axe (light|dark): the filter bar and its popovers"; Task 9 axe on `#/kanban` and the detail modal in both themes.

## Order

One worktree, serial. The controller merges Task 1 as soon as it passes review: 3b's swatch task waits on it.

| Order | Task | Depends on | Why this place |
|---|---|---|---|
| 1 | Epic swatches on `--cat-N`; the long-data fixture | — | 3b waits on `lib/epics.js`; every later test uses `longBoard()` |
| 2 | Topbar row 1 at phone width | — | Every track appends its primary to row 1 |
| 3 | The card is a link | 1 | Tasks 4 and 8 open the detail from a card link |
| 4 | Detail modal: B-095, the title's refusal, the marker row | 3 | Highest-value defect; independent of the board layout |
| 5 | Phase strip on an overflow row | — | First of the filter-bar rewrites |
| 6 | Priority and epic filters, Epic options, Clear filters | 1, 5 | Shares the filter bar with Task 5 |
| 7 | Row 2 split; count and "Add task" in row 1 | 2, 6 | Priority has left row 2 in Task 6 |
| 8 | Columns, the phone column switcher, `kanban.css` enforced | 3, 5, 6, 7; 3d Task 6 (`columnTabs`) merged | Rewrites what is left of `kanban.css`; the switcher is 3d's shared component, not a second one |
| 9 | Verification | 1–8 | |

## Carry-in

Checked against HEAD 638acec; dropped or moved, one line each:

- Reviewer note collapsing on a soft reload (P2a-FR M-9) — fixed in 2b Task 1 (`rememberView`; test "another writer's change keeps the reviewer note open").
- Handover menu listeners left after a redraw (M-9) — fixed in 2b Task 2 (popover closes `'detached'`).
- Title-glyph slot at the row end (P2b:66) — kept by design: the reserved slot stops a save from reflowing the title (test "a title save that goes through moves nothing below the heading").
- A carried refusal goes stale after another writer changes the field (P2b:62) — lives in `rememberView` (`task-detail-document.js`, 3c) and affects both views: handed to 3c.
- No non-visual saving/saved cue (P2b:66) — lives in `edit/inline-field.js` (shared, unowned): moved to plan 4 "shared component hardening".
- Filters count with zero-width children, self-dependency, form shell hooks — fixed in 2b (Controller allocation).
- `kanban.js` `searchTimer` — gone.

---

### Task 1: Epic swatches on the categorical tokens, and the long-data fixture

**Depends on:** nothing. **Merge first** (3b waits).

**Files:**
- Modify: `viewer/js/lib/epics.js`, `viewer/tests/unit/epics.test.js`, `viewer/tests/mock-fixtures.js` (append)
- Create: `viewer/tests/unit/long-board.test.js`

**Interfaces:**
- Consumes: nothing new.
- Produces (`viewer/js/lib/epics.js`; `EPIC_PALETTE` and the `FALLBACK` hex are deleted):
  ```js
  export function epicSwatch(epicId, epics) → 1..6 | null
    // ownSwatch(epic) when it is not null, else today's rule: position among epics with an id, mod 6, plus 1
  function ownSwatch(epic) → 1..6 | null   // not exported: the ONE place an epic record's own `color` is read (Step 3b: swatch-only override)
  export function epicIndex(epics) → Map<string, { name: string, swatch: 1|2|3|4|5|6 }>
    // one entry per epic with an id, in order; name = epic.name when a non-empty string, else the id;
    // swatch = epicSwatch(id, epics).
  export function assignEpicColors(epics) → { [epicId]: 1..6 }       // kept for 3b/3c callers: the swatch numbers
  export function epicColor(epicId, map) → 1..6 | null               // map[epicId] ?? null
  export function epicCssVar(swatch) → string
    // 1..6 → '--epic: var(--cat-N)'; anything else → '' (the token default of --epic applies)
  ```
  `viewer/tests/mock-fixtures.js` gains:
  ```js
  export function longBoard() → board   // deterministic, no Date.now():
    // phases: P0 'Phase 0: The prototype that was dropped before anyone used it' status 'archived', archived_reason 'superseded';
    //   P1..P7 named `Phase ${n}: ${PHASE_WORDS[n-1]}` (PHASE_WORDS: 'Foundation and tokens', 'Shell and navigation',
    //   'Shared components and modals', 'Screens re-skinned onto the shared parts', 'Data fixes and the bug route',
    //   'Cleanup and the full re-audit', 'Release and the changelog'); status P1–P3 'done', P4 'active', P5–P7 'planned'; order = n.
    // epics: 27, id `epic-${nn}` (01..27), name `Epic ${nn}: ${EPIC_WORDS[(n-1) % 9]}` (EPIC_WORDS: 'Viewer re-skin onto RR',
    //   'Native store cutover', 'Linear sync retries', 'Handover quotes', 'Guard hooks', 'Status line', 'Feedback inbox',
    //   'Agent tool-use evals', 'Release 7.2 notes'); status 01–22 'active', 23–25 'done', 26–27 'archived'.
    // tasks: 230, i = 0..229: id `T-${1005 + i}`; title = `Task ${i + 1}: make every column, chip and row cope with a title
    //   that runs past one line`.padEnd(120, ' and more').slice(0, 120); status = STATUS_CYCLE[i % 10] with
    //   STATUS_CYCLE = ['todo','todo','in-progress','todo','done','in-review','todo','blocked','in-progress','done']
    //   (todo 92, in-progress 46, done 46, in-review 23, blocked 23); priority = ['critical','high','medium','low','medium'][i % 5];
    //   epic = `epic-${String((i % 27) + 1).padStart(2, '0')}`; phase = i % 23 === 0 ? undefined : `P${(i % 7) + 1}`;
    //   estimate = ['S','M','L','3',undefined][i % 5]; branch = i % 5 === 0 ? `feat/T-${1005 + i}-a-branch-name-long-enough-to-be-cut-on-a-card` : undefined;
    //   bundle = i >= 10 && i <= 13 ? 'long-bundle-slug-alpha' : undefined; depends_on: []; created '2026-09-01T09:00:00Z';
    //   started '2026-09-20T09:00:00Z' for every status but todo.
    // revision 'long-r1', cursor 'c1', meta { project: 'Long fixture' }.
  ```

- [ ] **Step 1: Tests.** `epics.test.js`: delete the `EPIC_PALETTE` and explicit-colour cases; add: `epicIndex([{id:'a',name:'Alpha'},{id:'b'},{name:'no id'},{id:'c',name:''}])` → entries `a {Alpha,1}`, `b {b,2}`, `c {c,3}` and size 3; the 7th epic's swatch is 1; `assignEpicColors` of the same list deep-equals `{ a: 1, b: 2, c: 3 }`; an epic's own `color` per Step 3b's chosen override: `color: 3` → 3, `'cat-3'` → 3, `'--cat-5'` → 5, `'#ff0000'` and `7` → its position's number; `epicColor('zz', {})` is `null`; `epicCssVar(4) === '--epic: var(--cat-4)'`; `epicCssVar(null) === ''` and `epicCssVar('#6ea8ff') === ''`; the source of `viewer/js/lib/epics.js` (read with `readFileSync`) matches no `/#[0-9a-f]{3,8}\b/i` and no `rgba?\(`. `long-board.test.js`: two calls deep-equal; 27 epics, 230 tasks, 8 phases; the last id is `T-1234`; every title is 120 characters; status counts as above; 10 tasks have no phase; exactly one archived phase.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/epics.test.js viewer/tests/unit/long-board.test.js` — Expected: FAIL (`epicIndex` not exported, `longBoard` not exported).
- [ ] **Step 3: Implement** both; `epicSwatch` (and through it `epicIndex` and `assignEpicColors`) asks `ownSwatch(epic)` first; update the header comment of `epics.js` to a `User intent:` line (epics are told apart by a categorical swatch from the theme's tokens, never by a hex the theme cannot adjust).
- [ ] **Step 3b: Swatch-only override (user decision, 2026-10-06).** An epic record's own `color` still picks its swatch, but only when it names one of the six swatches; implement the second bullet below. (The first bullet is kept for the record and is not implemented.)
  - **Drop (not chosen):** `function ownSwatch() { return null; }` with the comment "An epic's own `color` is not honoured (plan 3a, pending user decision): swatches follow position." The Step 1 test stands as written.
  - **Swatch-only override (chosen):** `ownSwatch(epic)` returns N when `epic.color` is the integer N or the string `'N'`, `'cat-N'` or `'--cat-N'` with N in 1–6, else `null` (a hex, a name or any other value is ignored — the theme cannot adjust it). The Step 1 case becomes: `color: 3` → 3, `'cat-3'` → 3, `'--cat-5'` → 5, `'#ff0000'` → its position's number, `7` → its position's number; and `epicIndex` / `assignEpicColors` report the same numbers.
  Nothing else in this plan changes with the choice: every swatch in 3a (cards, bundle frames, epic chips, Epic options) reads `epicIndex`/`epicSwatch`.
- [ ] **Step 4: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2` (full mocked suite: the epics screen, the epic detail and the task document call `assignEpicColors`/`epicCssVar` unchanged) — Expected: PASS.
- [ ] **Step 5: Commit** — `feat(viewer): epic swatches come from the categorical tokens, not a hex palette; a long-data board fixture for phone-width checks`

---

### Task 2: Topbar row 1 fits at phone width

**Depends on:** nothing.

**Files:**
- Modify: `viewer/js/lib/topbar.js`, `viewer/css/shell.css` (row 1 rules only: `.topbar-row1`, `#page-title`, `.topbar-count`, `.topbar-primary`, and the ≤768px block's row 1 lines), `viewer/tests/unit/topbar.test.js`
- Create: `viewer/tests/topbar-row1.mock.spec.js`

**Interfaces:**
- Consumes: `tmAction` (its `aria-label` is `title || label`).
- Produces (ruling on the carried "inconsistent" pair — row 1 has two slots and two calls, both cleared by `claimTopbar()` on every route):
  ```js
  setTopbarCount(text = '')    // sets #topbar-count text and title to the same string; '' empties both (title removed)
  claimTopbarPrimary() → HTMLElement | null   // empties #topbar-primary, then returns it (a claim clears, as claimTopbar does)
  claimTopbar()                // unchanged contract; now calls setTopbarCount('') instead of replaceChildren() on the count
  ```
- Behaviour (each line is a test):
  1. Row 1 never wraps and never scrolls sideways: `.topbar-row1 { min-width: 0; }`, `#page-title { flex: 0 1 auto; }`, `.topbar-count { flex: 0 1000 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }` — the count gives way first, then the title.
  2. At ≤768px a primary that holds an icon shows only the icon: `.topbar-primary .btn:has(> .icon) { min-width: 44px; min-height: 44px; padding: 0; justify-content: center; }` and `.topbar-primary .btn:has(> .icon) > span { display: none; }`. Its accessible name is unchanged (`aria-label`).
  3. At 390×844 on `#/settings` with count `230 tasks · 230 visible` and a primary `tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: 'Row one probe' })`: row 1 is 56px tall, `scrollWidth <= clientWidth`; hamburger, title, count, primary and theme toggle each lie inside row 1's box (top/bottom/right, ±0.5px); the primary is ≥44×44 and named "Row one probe"; the count is cut (`scrollWidth > clientWidth`) and its `title` is the full text.
  4. At 1440×900 the same setup shows the label "Task" and the count uncut.

- [ ] **Step 1: Tests.** Unit (`topbar.test.js`, jsdom with `#topbar-count`, `#topbar-primary`, `#topbar-actions`): `setTopbarCount('12 tasks')` → text and `title` `12 tasks`; `setTopbarCount('')` → no `title` attribute; two `claimTopbarPrimary()` calls each appending a button leave only the second; `claimTopbar()` after a count leaves `#topbar-count` empty with no `title`. Mocked (`topbar-row1.mock.spec.js`, header `User intent:` line, `mockApi` with `/api/viewer/prefs`, `afterEach` asserting `unmockedWrites` empty): lines 3 and 4 (set up with `page.evaluate(() => import('/js/lib/topbar.js').then(({ setTopbarCount, claimTopbarPrimary, tmAction }) => { … }))` after `#/settings` has mounted); **"at 390 row 1 keeps title, count, primary and theme toggle on one 56px line"** is line 3; "a new route clears the count, its title and the primary": after line 3's setup, `location.hash = '#/table'`, wait for `table.tbl`; `#topbar-count` has no `title` equal to the probe text and `#topbar-primary` holds no element named "Row one probe".
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/topbar.test.js` and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 topbar-row1.mock.spec.js` — Expected: FAIL (title not set, primary not emptied, row 1 overflows at 390).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 topbar-row1.mock.spec.js shell.mock.spec.js` — Expected: PASS; `shell.css` still 0 style violations.
- [ ] **Step 5: Commit** — `feat(viewer): topbar row 1 fits a phone — the count gives way first with its words in title, a primary shows its icon only — and claiming row 1's primary clears it`

---

### Task 3: The card is a link

**Depends on:** Task 1 (`epicIndex`).

KB-01, KB-02, KB-11 (card), X-01 (card status), X-03 (Kanban's mouse-only targets), spec §5.6 and §6 card lines, the §11 interim `role=article` replaced.

**Files:**
- Modify: `viewer/js/components/card.js` (rewrite; new `User intent:` header), `viewer/js/components/bundle-frame.js`, `viewer/js/lib/filters.js` (`STATUS_LABELS`), `viewer/js/screens/kanban.js` (pass `epicIndex`; drop `assignEpicColors`), `viewer/css/screens/kanban.css` (delete the card, bundle-frame, lane-badge, merge-dot, tracker, status-pill blocks: today's lines 59–318 and the ≤768px card rules 893–928), `viewer/js/components/component-diagram.js` (one guard line, see Step 3), `viewer/index.html` (append `css/components/card.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/card.css`), `viewer/tests/task-detail.mock.spec.js` (line 37 `card` helper; line 171 `data-before`), `viewer/tests/unit/card-human-action.test.js`, `viewer/tests/unit/card-bundle.test.js`, `viewer/tests/unit/bundle-frame.test.js`, `viewer/tests/unit/component-diagram.test.js`
- Create: `viewer/css/components/card.css`, `viewer/tests/unit/card.test.js` (replaces `card-name.test.js`, which is deleted with `git rm`), `viewer/tests/kanban.mock.spec.js`

**Interfaces:**
- Consumes: `linkRow` (`components/link-row.js`), `truncate` (`lib/text.js`), `priorityMarker`, `statusMarker`, `marker` (`components/status.js`), `icon()`, `bindCopy` (`lib/copy.js`), `epicIndex` (Task 1), `laneBadge` (`gate-pipeline.js`), `renderMergeLadderCompact` (`merge-status.js`), `formatTimeInStatus`, `classifyTimeInStatus`, `isoToMs`, `formatAbsolute` (`lib/time.js`).
- Produces:
  ```js
  // card.js
  export function renderCard({ task, density = 'full', epicIndex = new Map(), groupBy = 'status', now = Date.now(), hideBundleChip = false })
    → HTMLElement   // linkRow({ tag: 'div', className: `card-task ${density}`, href: `#/task/${encodeURIComponent(task.id)}`, … })
  export const renderMinimalCard, renderFullCard, parseTrackerId   // unchanged names
  // bundle-frame.js
  export function renderBundleFrame({ slug, tasks, total }, { density = 'full', epicIndex = new Map(), groupBy = 'status' } = {})
  // lib/filters.js
  export const STATUS_LABELS   // from TASK_STATUS: { blocked: 'Blocked', todo: 'Todo', 'in-progress': 'In progress', 'in-review': 'In review', done: 'Done' }
  ```
- Card DOM (each line is a test):
  1. Root `div.card-task.link-row.<density>[data-task-id]`, no `role`, no `tabindex`, no `style` attribute; `.recent` when started within 24h (KB-11: the border goes `--border-strong`, and line 1 carries the tag below; no glow).
  2. The link `a.link-row__link[href="#/task/<id>"]` holds `span.card-sr` (the id and a space, visually hidden) and `truncate(title || '(untitled)', { lines: 3, className: 'card-title' })`. Its accessible name is `<id> <title>`; its `title` is the full title.
  3. Content, in order: `span.card-new` "New" (only on a `.recent` card, both densities; a neutral Technical tag, no hue); `span.card-pri` holding `priorityMarker(priority)` (absent when the task has no priority); `span.card-age` (Technical; `title` "Since <formatAbsolute(anchor)>"; `.card-age--stale` when `classifyTimeInStatus` says stale); full density only: `div.card-tags` holding `span.card-tag.card-epic` (`span.card-swatch.card-swatch--cat-<n>` or `--none`, then the epic's name from `epicIndex`, else its id, cut with `truncate`), the estimate tag, the spec-review marker (pass `{ label: 'Spec passed', shape: '●', tone: 'success' }`, warn `{ 'Spec warning', '▲', 'warning' }`, fail `{ 'Spec failed', '◆', 'critical' }`), tracker, unmet-dependency (`<n> unmet`), sub-repo, bundle (`Bundle <slug>`, unless `hideBundleChip`), lane badge, gate state, merge dots, and `statusMarker('task', status)` when `groupBy !== 'status'`; `div.card-note` for an in-review `human_action` (a `marker({ label: 'Waiting on you', shape: '▲', tone: 'warning' })` then the text) or a blocked task's blockers (`marker({ label: 'Blocked by <n>', shape: '◆', tone: 'critical' })`). Minimal density: priority, age, and the status marker when `groupBy !== 'status'`.
  4. Controls (siblings of the link), each with a `data-focus` the board's focus keeper uses (Task 8); the link carries `data-focus="link"`: `button.card-id[type=button][data-focus="copy-id"][aria-label="Copy id <id>"]` holding `truncate(id)` and `icon('copy', { size: 12 })`, copying the id through `bindCopy(btn, id, { flashClass: 'is-copied' })`; full density with a branch: `button.card-branch[data-focus="copy-branch"][aria-label="Copy branch <branch>"]` holding `truncate(branch)` and the copy icon (no branch line without a branch); full density with docs: `button.btn.btn--ghost.btn--icon.btn--sm.card-docs[data-focus="docs"][aria-label="Open primary doc"]` holding `icon('document', { size: 14 })`, opening the first doc as today.
  5. No character of the 3a glyph list (Global Constraints) in `outerHTML`.
- `card.css` (tokens only; new `User intent:` header): the card is a grid; `.link-row__content` and `.link-row__controls` are `display: contents` inside it, and every control is lifted over the link's hit area:
  ```css
  .card-task { display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    grid-template-areas: "id new pri age" "title title title title" "tags tags tags tags" "branch branch branch docs" "note note note note";
    align-items: center; column-gap: var(--space-xs); padding: var(--space-sm) var(--space-md); margin-bottom: var(--space-xs);
    background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); color: var(--foreground-default);
    transition: background-color var(--dur-standard) var(--ease-hourglass), border-color var(--dur-standard) var(--ease-hourglass); }
  .card-task:hover, .card-task.recent { border-color: var(--border-strong); }
  .card-task > :is(.link-row__content, .link-row__controls) { display: contents; }
  .card-task .link-row__controls > * { position: relative; z-index: 1; }
  .card-task > .link-row__link { grid-area: title; margin-top: var(--space-xs); min-width: 0; color: var(--foreground-bold); text-decoration: none; }
  .card-title { font-family: var(--font-narrator); font-weight: var(--font-narrator-weight); font-size: var(--size-narrator-small); line-height: var(--leading-heading); overflow-wrap: anywhere; }
  .card-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  ```
  plus: `.card-new` (grid-area `new`; Technical small, `--foreground-default`, `1px solid var(--border-default)`, `--radius-sm`, `padding: 0 var(--space-micro)`; no background hue), `.card-id` (grid-area `id`, `justify-self: start`, `max-width: 100%`, Technical small, `--foreground-subtle`, transparent 1px border, hover `--foreground-bold` + `--border-default`, `cursor: copy`), `.card-pri` (`pri`), `.card-age` (`age`, Technical small, subtle; `--stale` bold), `.card-tags` (`tags`, flex-wrap, `margin-top: var(--space-xs)`), `.card-tag` (Technical small, `--foreground-default`, `min-width: 0`), `.card-swatch` (8×8, `--radius-xs`) with `--cat-1`…`--cat-6` and `--none` (`--foreground-subtle`), `.card-branch` (`branch`), `.card-docs` (`docs`), `.card-note` (`note`), `.is-copied` (`border-color: var(--tone-success)`), `.lane-badge` and `.ml-dot` on neutral tokens (`--border-default`, `--tone-success` fill for a filled rung), `.bundle-frame` (`1px solid var(--border-default)`, `--radius-lg`, `padding: var(--space-xs)`), `.bundle-frame.bh-N { border-color: var(--cat-N); }` for N 1–6, `.bundle-frame__swatch` (as `.card-swatch`), `.bundle-frame-head` (Technical small, `--foreground-default`; lane and count `--foreground-subtle`). No rule sets a hue on text.
- `bundle-frame.js`: the `⬢` span becomes `span.bundle-frame__swatch.card-swatch--cat-<slugHue>`; the lane word stays.
- `component-diagram.js` line 113: the block's `keydown` handler starts with `if (ev.target !== block) return;` — a card link inside a block now takes Enter itself (cards were not focusable before).

- [ ] **Step 1: Unit tests.** `card.test.js` (jsdom): lines 1–5, each with concrete values — `{ id: 'T-102', title: 'Re-skin the board', status: 'in-review', priority: 'critical', epic: 'viewer', branch: 'feat/x', docs: { spec: 'a.md' }, human_action: 'add the key' }` with `epicIndex: new Map([['viewer', { name: 'Viewer re-skin', swatch: 1 }]])`: link name `T-102 Re-skin the board`; `.card-pri .marker__word` `Critical`; `.card-epic` text `Viewer re-skin` and `.card-swatch--cat-1`; `button.card-id` labelled `Copy id T-102`; `button.card-branch` labelled `Copy branch feat/x`; `.card-docs svg.icon`; `.card-note` contains `Waiting on you` and `add the key`; `link.querySelector('a, button, input, [tabindex]')` is `null`; unknown epic `zz` → `.card-epic` text `zz` and `.card-swatch--none`; no priority → no `.card-pri`; `groupBy: 'phase'` → a `.marker__word` reading `In review`; no `branch` → no `.card-branch`; a title of 120 characters → `.card-title.truncate--3` with `title` equal to it; glyph list absent from `outerHTML`; no `style` attribute on the root; a task started 2 hours before `now` has `.recent` and `.card-new` reading `New` in both densities, one started 2 days before has neither (KB-11). `card-human-action.test.js`: the label test now asserts `STATUS_LABELS['in-review'] === 'In review'` and `STATUS_LABELS['in-progress'] === 'In progress'`. `card-bundle.test.js`: the bundle tag reads `Bundle asset-ux` (no `⬢`). `bundle-frame.test.js`: the header has `.bundle-frame__swatch` and no `⬢`; pass `epicIndex` instead of `epicColors`. `component-diagram.test.js`: "Enter on a card link inside a block does not navigate the block" (dispatch a bubbling `keydown` Enter on the card's link; `onComponentNav` not called).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/card.test.js viewer/tests/unit/card-human-action.test.js viewer/tests/unit/card-bundle.test.js viewer/tests/unit/bundle-frame.test.js viewer/tests/unit/component-diagram.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement** card, bundle frame, `STATUS_LABELS`, the guard, `card.css`; append the stylesheet link at the end of the stylesheet links in `index.html`; ENFORCED; delete the old blocks from `kanban.css`; in `kanban.js` build `const index = epicIndex(epicsArr)` once per paint and pass `epicIndex: index` to `renderCard` and `renderBundleFrame`. In `task-detail.mock.spec.js`: line 37 becomes ``const card = (page, id) => page.locator(`.card-task[data-task-id="${id}"] > .link-row__link`);`` and line 171 reads `await expect(page.locator('[data-before]')).toHaveCount(0);` (the marker now sits on the link, which the redraw replaces with the rest of the card).
- [ ] **Step 4: Mocked tests** — `kanban.mock.spec.js` (new; `User intent:` header; a `board(page, { theme, board = BOARD, viewport })` helper calling `mockApi` with prefs, `/api/board`, `/api/backlog`, `/api/bugs: []` and the T-102 detail; `afterEach` asserts `unmockedWrites` empty):
  - "a card is a link: Enter opens the task, Escape hands focus back, Ctrl+click is the browser's": focus T-102's link, Enter → `.modal--detail` visible; Escape → the link is focused; Ctrl+click on the card → `page.context().waitForEvent('page')` resolves and no `.modal`.
  - "the copy-id control copies and never opens the task": `addInitScript` replacing `navigator.clipboard` with `{ writeText: async (t) => { window.__copied = t; } }`; click `.card-id` of T-102 → `window.__copied === 'T-102'`, no `.modal`.
  - **"a 120-character title is clamped to three lines and the id never breaks"** (Review Focus 1): `longBoard()` at 390×844; for the first 10 cards: `.card-title` height ≤ 3 × its computed line-height + 1; `.card-id .truncate` has one client rect; the card's `scrollWidth <= clientWidth`.
  - "a recent card has a strong border and a New tag, and no glow" (KB-11): a board whose T-102 `started` is one hour ago; its `.card-new` reads `New` and is visible on line 1 (same `offsetTop` band as `.card-id`); the card's computed `border-color` equals `--border-strong`'s value and its `box-shadow` is `none`; T-101 (no `started`) has no `.card-new`.
  - **"axe (light|dark): cards have no contrast, nested-interactive or aria violation"** (Review Focus 5): axe on `.kanban-board` with `runOnly: ['color-contrast', 'nested-interactive', 'aria-allowed-attr', 'aria-valid-attr-value', 'link-name', 'button-name']` → zero, both themes.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js task-detail.mock.spec.js surfaces.mock.spec.js popover.mock.spec.js rows.mock.spec.js` — Expected: PASS; `components/card.css` enforced with 0 violations; the style report for `screens/kanban.css` is lower than before (state both numbers).
- [ ] **Step 6: Commit** — `feat(viewer): a Kanban card is one real link with its copy and doc controls beside it — id, priority marker and age on one line, the title in three, the epic by swatch and name, no glyphs`

---

### Task 4: Detail modal — B-095, the title's refusal stays in view, one marker size

**Depends on:** Task 3 (the card link is the opener).

**Files:**
- Modify: `viewer/js/components/detail-modal.js`, `viewer/tests/task-detail.mock.spec.js` (the assertion at today's lines 285–286), `viewer/index.html` (append `css/components/detail-modal.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/detail-modal.css`)
- Create: `viewer/css/components/detail-modal.css`, `viewer/tests/detail-modal.mock.spec.js`

**Interfaces:**
- Consumes: `store.isEditing(id)`, `getTaskDetailFull`, `rememberView`, `modal.header`, `modal.body`.
- Produces: no exported change. Behaviour (each line is a test):
  1. **B-095.** A soft re-read (`load(k, i, { soft: true })`) that resolves while `store.isEditing(i)` is true returns without disposing or re-mounting; the end of that edit (`store.endEdit` emits `task:<id>`) re-reads. Implement as `if (soft && store.isEditing(i)) return;` directly after the `closed || request !== generation` check that follows the fetch.
  2. **The title's refusal never scrolls away.** After each task mount, the document's `.td-body > .td-title-message` is moved to sit directly after `modal.header` (`modal.header.after(msg)`), and `dispose()` removes the moved node. `detail-modal.css`: `.modal--detail > .td-title-message { flex: 0 0 auto; padding: var(--space-sm) var(--space-lg) 0; }` (the header's own horizontal padding at every width). The dialog's accessible name stays the title.
  3. **One marker size.** `.td-markers`' computed `font-size` and the first marker's height are the same on first open, after another writer's change, and on the full page `#/task/T-102`. Step 3 says how the value is chosen.
  4. **No meta line in the modal** (spec §11 ruling): `.modal--detail [data-test="task-id"]` count 0, while `#/task/T-102` has one.

- [ ] **Step 1: Mocked tests** — `detail-modal.mock.spec.js` (`User intent:` header; helpers copied from `task-detail.mock.spec.js`: `board`, `card` (the link), `openCard`, `editing`, `renamedElsewhere`; `afterEach` `unmockedWrites` empty):
  - **"a re-read that lands after an inline editor opened leaves the editor alone (B-095)"** (Review Focus 2): open T-102; `let release; const held = new Promise((r) => { release = r; });` and `page.route('**/api/task/T-102/detail', async (route) => { await held; await route.fulfill({ json: taskDetail({ ...DETAIL_TASK, title: 'Renamed while editing' }, 't1:other', RICH_RELATED) }); })`; bump the board revision (`store.setBoard` with a new `revision`) and wait for the request; click `[data-field="status"] .ef-editable`; expect its `select` focused and `editing(page, 'T-102')` true; `release()`; wait for the response and one `requestAnimationFrame` in the page; expect the same `select` still focused, `editing` still true and the title not yet renamed; press Tab; expect the title `Renamed while editing`. On today's code the select is gone after `release()`.
  - "a refused title stays in view with the body scrolled to the end": open T-105 (`LONG_TASK`) with `PATCH /api/tasks/T-105` answered `{ status: 409, json: { ok: false, error: 'Titles are frozen during review' } }`; scroll `.modal-body` to its bottom; edit the title, press Enter; `.modal--detail > .td-title-message` is visible, its top ≥ the header's bottom − 1 and its bottom ≤ the body's top + 1; `getByRole('dialog', { name: LONG_TASK.title })` resolves.
  - "the marker row keeps one size through a redraw and matches the full page": read `getComputedStyle(.td-markers).fontSize` and the first `.td-marker-host .marker` height in the modal; `renamedElsewhere`; read again; then on `#/task/T-102` read the page's; all three pairs equal.
  - "the modal has no meta line; the page keeps it" (line 4).
  In `task-detail.mock.spec.js` the assertion at today's lines 285–286 becomes `expect(await message.evaluate((el) => el.parentElement.classList.contains('modal--detail') && el.previousElementSibling?.classList.contains('modal-header'))).toBe(true);` with its comment "Under the header, outside the scrolling body; nothing of it inside the heading that names the dialog."
- [ ] **Step 2: Run** `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 detail-modal.mock.spec.js` — Expected: B-095, the scrolled refusal and (if the ledger holds) the marker test FAIL; the meta-line test PASSES (a guard).
- [ ] **Step 3: Implement** lines 1 and 2. For line 3, first read the failing test's numbers: whichever of the two modal values equals the full page's value is the intended one; pin `.modal--detail .td-markers { font-size: <the token whose value that is> }` in `detail-modal.css` (`--size-narrator-default` is 16px, `--size-narrator-small` 14px). If the modal values already agree with the page, the carried item is stale: keep the test, add no rule, and say so in the report.
- [ ] **Step 4: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 detail-modal.mock.spec.js task-detail.mock.spec.js popover.mock.spec.js conflict-banner.mock.spec.js` — Expected: PASS; `components/detail-modal.css` 0 violations.
- [ ] **Step 5: Commit** — `fix(viewer): the detail modal never re-mounts over an editor opened during a re-read (B-095), keeps a refused title's reason in view, and draws its marker row at one size`

---

### Task 5: Phase strip on an overflow row

**Depends on:** nothing new (serial after Task 4).

KB-04, KB-10, the archived-phases menu on `openPopover`, and the stepper's transforms, shadows, hex and italic gone.

**Files:**
- Create: `viewer/js/components/phase-strip.js`, `viewer/tests/unit/phase-strip.test.js`
- Delete (`git rm`): `viewer/js/components/phase-stepper.js`, `viewer/js/components/archived-phases-dropdown.js`
- Modify: `viewer/js/screens/kanban.js` (strip built once at mount, `update` per paint; `stepperViewState` gone; cleanup calls `destroy()`), `viewer/css/screens/kanban.css` (delete the PHASE STEPPER block, the archived-dropdown block and their ≤768px rules; add the `.phase-strip` rules below), `viewer/tests/kanban.mock.spec.js`

**Interfaces:**
- Consumes: `overflowRow`, `openPopover`, `bucketPhases` (`lib/phase-buckets.js`), `icon()`, `h()`.
- Produces:
  ```js
  // components/phase-strip.js
  export function phaseStrip({ onSelect }) → { el, update({ phases, active }), destroy() }
    // phases: [{ id, name, status: 'done'|'active'|'planned'|'future'|'archived', done, total, archived_reason? }] in display order
    // active: '__all__' | '__orphans__' | a phase id. onSelect(value) — the caller decides toggling.
  ```
- Structure and behaviour (each line is a test):
  1. `div.phase-strip[role="group"][aria-labelledby]` → `span.phase-strip__label` "Phase" (Technical label voice) → `div.phase-strip__items` under `overflowRow(items, { moreLabel: 'More', popoverLabel: 'More phases', keep: (el) => el.matches('.phase-chip--all, .phase-chip--current, [aria-pressed="true"], .phase-archived') })`.
  2. Items in order: `button.chip.phase-chip.phase-chip--all[data-value="__all__"]` "All"; `button.btn.btn--ghost.btn--sm.phase-archived` "Archived" + `span.phase-archived__count` (present only when archived phases exist); one `button.chip.phase-chip[data-value=<id>]` per past, active and future phase (`bucketPhases` order) with `span.phase-chip__num` (the id's number, as today's `phaseNum`), `span.phase-chip__name` (full name; CSS cuts it) and `span.phase-chip__count` `done/total`; `title` = `<name> · <done>/<total> done`; class `phase-chip--done` (with `icon('check', { size: 12 })` before the name), `phase-chip--current` (with `span.phase-chip__bar > span` whose inline `width` is the done percentage), or `phase-chip--future`; last `button.chip.phase-chip.phase-chip--orphans[data-value="__orphans__"]` "No phase".
  3. `aria-pressed="true"` on the chip whose value is `active` (All for `'__all__'`), `"false"` on the rest. Click → `onSelect(value)`.
  4. `update()` reuses each button by value: focus and an open More survive; adds, removals and reorders apply as in `chipRow`.
  5. The archived button opens `openPopover({ anchor, content, role: 'menu', label: 'Archived phases', focus: 'checked' })` with one `button.popover-item[role="menuitemradio"][aria-checked]` per archived phase (name, then `done/total`, then the reason as plain text); choosing closes with `returnFocus: true` and calls `onSelect(id)`. The archived button reads pressed (`aria-pressed="true"`) while an archived phase is active.
  6. No `.disabled` class, no `transform` in any inline style, none of the 3a glyphs.
- CSS (in `kanban.css`, tokens only): `.phase-strip { display: flex; align-items: center; gap: var(--space-xs); min-width: 0; }`, label as `.chip-row__label`; `.phase-strip__items { display: flex; flex-wrap: nowrap; align-items: center; gap: var(--space-micro); flex: 1 1 auto; min-width: 0; overflow: hidden; }`; `.phase-chip__name { max-width: 16ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`, `.phase-chip--current .phase-chip__name { max-width: 32ch; }`; `.phase-chip__num, .phase-chip__count` Technical small `--foreground-subtle`; `.phase-chip--done` `color: var(--foreground-subtle)` (pressed: `--foreground-bold`); `.phase-chip__bar` 4px tall, `--bg-recessed` track, `--tone-success` fill, `--radius-full`; `.phase-chip--current` `flex-direction` stays row and its bar sits under the name (`display: grid` on the chip with the bar spanning). At ≤768px the chips are 44px (chips.css already) and `.phase-archived` is `min-height: 44px`.

- [ ] **Step 1: Unit tests** (`phase-strip.test.js`, jsdom — no `ResizeObserver`, so nothing parks): lines 1–6 with the `longBoard()` phases mapped as `kanban.js` maps them (done counts from its tasks); identity: the P2 button before and after `update({ active: 'P2' })` is the same element and is pressed, All is not; archived: the menu has one `menuitemradio`, `aria-checked="true"` when `active` is `P0`; clicking it calls `onSelect('P0')` and `openPopoverCount()` is 0 afterwards; with no archived phase there is no `.phase-archived`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/phase-strip.test.js` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement**; in `kanban.js` keep today's phase rows (sorted by `order`, status derived when missing) and pass them with `state.filters.phase`; `onSelect` keeps today's toggle (selecting the active value returns to `'__all__'`).
- [ ] **Step 4: Mocked tests** (`kanban.mock.spec.js`, `longBoard()`):
  - "every phase is named in full or in its title, and the current one is wider": at 1440×900, for each visible `.phase-chip[data-value^="P"]` the `.phase-chip__name` either is not cut (`scrollWidth <= clientWidth`) or the chip's `title` starts with the full name; the `.phase-chip--current` box is wider than any `.phase-chip--future` box.
  - "at 390 the phase row is one line; More lists the rest and picking one filters the board": every visible item shares one `offsetTop`; the row's More is visible; open it, click `P5`; then the number of `.card-task` elements equals `longBoard().tasks.filter((t) => t.phase === 'P5').length`.
  - "the archived menu picks an archived phase from the keyboard": focus `.phase-archived`, Enter (focus lands on the checked item or the first), Enter → the button reads pressed and no `.card-task` remains (no task is in P0).
  - axe on `.phase-strip` (and on the open More / menu) in both themes: zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js` — Expected: PASS; `screens/kanban.css` report count lower than after Task 3 (state both).
- [ ] **Step 6: Commit** — `feat(viewer): the phase filter is one line of named phase chips — the current one wider, the rest behind More, archived phases in a menu — with no carousel, transforms or shadows`

---

### Task 6: Priority and epic filters, Epic options, Clear filters

**Depends on:** Tasks 1 (`epicIndex`), 5 (the filter bar).

KB-05, KB-06, KB-07 (adoption), KB-08 (nested "open ↗"), KB-09, and the "clear all" span that was not a control.

**Files:**
- Modify: `viewer/js/components/priority-chips.js` (rewrite), `viewer/js/components/epic-chips.js` (rewrite), `viewer/js/components/epic-dropdown.js` (rewrite: Epic options), `viewer/js/lib/epic-ranking.js` (remove `countActiveTasksByEpic`, `ACTIVE_TASK_STATUSES`, `splitQuickAndDropdown`, `QUICK_CAP` users), `viewer/js/lib/filters.js` (`countOpen`, `OPEN_COUNT_HINT`), `viewer/js/screens/kanban.js`, `viewer/css/screens/kanban.css` (delete the FILTER BAR, PRIORITY CHIPS, EPIC CHIPS ROW, epic single-row, dropdown trigger, dropdown panel and `.kanban-epic-chip__open` blocks and their ≤768px lines; add the rules below), `viewer/tests/unit/epic-ranking.test.js`, `viewer/tests/unit/epic-phase-filter.test.js`, `viewer/tests/unit/filters.test.js`, `viewer/tests/kanban.mock.spec.js`
- Create: `viewer/tests/unit/kanban-filters.test.js`

**Interfaces:**
- Consumes: `chipRow`, `openPopover`, `openPopoverCount`, `chipClickNext`, `CHIP_CLICK_HINT`, `PRIORITY`, `epicIndex`, `rankEpics`, `sortEpicsForDropdown`, `epicsForPhase`, `icon()`, `truncate`.
- Produces:
  ```js
  // lib/filters.js
  export function countOpen(tasks, field) → Map<string, number>
    // tasks whose status is neither 'done' nor 'archived' (a missing status counts), keyed by String(task[field]);
    // tasks without the field are not counted
  export const OPEN_COUNT_HINT = `Counts are open tasks (not done, not archived) · ${CHIP_CLICK_HINT}`;
  // priority-chips.js
  export function priorityChips(active = [], counts = new Map())
    → [{ value, label, pressed, count }]          // critical, high, medium, low; label = PRIORITY[value].label
  // epic-chips.js
  export function epicChips({ epics, selectedIds = [], pinnedIds = [], counts = new Map(), sort = 'count', showArchived = false })
    → [{ value: '__all__', label: 'All', pressed: selectedIds.length === 0 },
       ...{ value: id, label: name, pressed, count: counts.get(id) ?? 0, swatch }]
    // epics: [{ id, name, status, last_referenced, swatch }] already phase-scoped by the caller.
    // Order: pinned epics in pin order, then the rest by rankEpics(rest, counts) for sort 'count', else
    // sortEpicsForDropdown(rest, sort, counts). Archived epics are left out unless showArchived, selected or pinned.
  // epic-dropdown.js
  export function openEpicOptions({ anchor, epics, counts, pinnedIds, sort, showArchived, onPinToggle, onSortChange, onShowArchived })
    → popover handle   // openPopover({ anchor, content, role: 'dialog', label: 'Epic options', focus: 'first', className: 'epic-options' })
  ```
- Kanban filter bar (each line is a test):
  1. `.kanban-filterbar` holds the phase strip (Task 5) and then `div.kanban-filters`: the Priority `chipRow({ label: 'Priority', hint: OPEN_COUNT_HINT })`, the Epic `chipRow({ label: 'Epic', hint: OPEN_COUNT_HINT })`, `button.btn.btn--ghost.btn--icon.btn--sm.epic-options-btn[aria-label="Epic options"]` (`icon('sliders')`), and `button.btn.btn--ghost.btn--sm.kanban-clear` (`icon('dismiss', { size: 14 })` + "Clear filters", `hidden` while no filter and no search is set). All are built once at mount; every paint calls `update()`.
  2. Counts: priority `countOpen(tasksInPhase, 'priority')`, epic `countOpen(tasksInPhase, 'epic')` (`tasksInPhase` is today's phase-scoped list in `kanban.js`), Epic options the same epic map. A zero-count chip is disabled unless pressed (chipRow).
  3. Clicks: a priority chip → `chipClickNext(e, state.filters.priorities, value)`; epic `__all__` → `[]`; any other epic → `chipClickNext(e, state.filters.epics, value)`. Clear filters keeps today's `clearAllFilters()` and returns focus to the search field.
  4. Epic options content: a labelled "Order" select (`span.ef-select > select.ef-enum-select` + chevron; options count "Open tasks", status "Status", recent "Recent activity", alpha "Name"), a checkbox "Show archived epics (<n>)", a search input labelled "Filter epics", and `ul.epic-options__list` of `li.epic-option`: `span.card-swatch.card-swatch--cat-<n>`, `a.epic-option__name[href="#/epic/<id>"]` (truncated name; replaces the chip's nested "open ↗"), `span.epic-option__count` (the same open count), the status word in `--foreground-subtle` when not `active`, and `button.btn.btn--ghost.btn--sm.epic-option__pin[aria-pressed]` "Pin" labelled `Pin <name>`. Pin, order and archived changes call back; the popover stays open (its anchor is never redrawn) and the pin button flips `aria-pressed` in place.
  5. `kanban.js` keeps `pinnedEpics` and `epicSort` in prefs as today; `state.showArchivedEpics` is local (default false). The phase-scope pruning of selected epics stays.
- CSS (in `kanban.css`): `.kanban-filterbar { display: flex; flex-direction: column; gap: var(--space-sm); padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); min-width: 0; }`; `.kanban-filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs) var(--space-lg); min-width: 0; }`; the Priority row `flex: 0 0 auto`; the Epic row `flex: 1 1 240px; min-width: 240px`; `.epic-options` list rows: flex, `min-height: 32px` (44px at ≤768px), hover `--overlay-surface-hover`, name `--foreground-bold`, count Technical small subtle. A group may move to the next line; chips inside a group never wrap.

- [ ] **Step 1: Unit tests.** `kanban-filters.test.js`: `countOpen` counts todo, in-progress, in-review, blocked and status-less tasks and skips done and archived; `priorityChips(['high'], new Map([['high', 3]]))` → four chips with words `Critical, High, Medium, Low`, only High pressed, High count 3, Critical count 0; `epicChips` — pinned `['c']` comes first after All; an archived epic is absent, present with `showArchived`, present when selected; All is pressed with no selection; labels are names; `sort: 'alpha'` orders the rest by name. `epic-ranking.test.js`: delete the `ACTIVE_TASK_STATUSES`, `countActiveTasksByEpic` and `splitQuickAndDropdown` cases; keep `rankEpics` and `sortEpicsForDropdown` (now fed by `countOpen(…, 'epic')`). `epic-phase-filter.test.js`: the `rankEpics` cases build counts with `countOpen`. `filters.test.js`: `OPEN_COUNT_HINT` contains `open tasks` and `shift-click`. jsdom for `openEpicOptions`: Order select is named "Order"; the list has one link per epic with `href="#/epic/<id>"`; pressing Pin calls `onPinToggle(id, true)` and the button reads `aria-pressed="true"`; typing `07` in the filter leaves only `epic-07`'s row.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/kanban-filters.test.js viewer/tests/unit/epic-ranking.test.js viewer/tests/unit/epic-phase-filter.test.js viewer/tests/unit/filters.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement**; delete the old chip, dropdown and priority code paths (`renderPriorityChips`, `updatePriorityChips`, `renderEpicChips`, `renderEpicDropdown`) and their document-level click listeners.
- [ ] **Step 4: Mocked tests** (`kanban.mock.spec.js`; `longBoard()` unless named):
  - "the epic row is one line with More at 1440 and at 390": visible Epic chips share one `offsetTop`; More is visible; the Priority row's chips share one `offsetTop`.
  - "an epic's count is the same on its chip and in Epic options, and the label says what it counts" (KB-06): `epic-03`'s `.chip__count` text equals its `.epic-option__count`; the Epic group label's `title` contains `open tasks`.
  - "priority chips are words and filter the board": names `Critical`, `High`, `Medium`, `Low`; click High → every card's `.card-pri .marker__word` is `High`; Shift+click Critical → only High and Critical.
  - "no control sits inside a chip" (KB-08): `.chip a, .chip button` count 0; axe `nested-interactive` zero on `.kanban-filterbar`.
  - "Clear filters appears with a filter and clears everything": hidden at load; click High and type `T-10` in search → visible; click it → no chip pressed except All, the search field is empty, the button is hidden and focus is in the search field.
  - "pinning an epic in Epic options puts it first and saves it": pin `epic-20`; the first Epic chip after All is `Epic 20…`; the last `PUT /api/viewer/prefs` body has `kanban.pinnedEpics` `['epic-20']`; Epic options is still open.
  - **"a poll keeps focus on a pressed chip and keeps Epic options open"** (Review Focus 2): click High, focus it; `store.setBoard` with a new revision → High is focused and pressed. Then open Epic options, bump the revision again → `.epic-options` still visible.
  - **"leaving the board with Epic options open leaves nothing behind"** (Review Focus 4): collect `pageerror`; open Epic options; `location.hash = '#/table'`; wait for `table.tbl`; `.popover` count 0; `page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()))` is 0; no page error; back to `#/kanban`: the Epic row's More count equals the number of parked chips.
  - **"axe (light|dark): the filter bar and its popovers"** (Review Focus 5): axe on `.kanban-filterbar`, then with Epic options open and with the Epic row's More open (scope `body`): zero `color-contrast`, `nested-interactive`, `aria-*`, `label`, `select-name`.
  - at 390×844 every chip, the options button and Clear filters are ≥44px tall.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js chips.mock.spec.js` — Expected: PASS; `screens/kanban.css` report count lower than after Task 5 (state both).
- [ ] **Step 6: Commit** — `feat(viewer): Kanban filters are chip rows with full words and one open-task count — every epic on one line with More, pins and order in an Epic options popover, a real Clear filters button`

---

### Task 7: Row 2 split; the count and "Add task" go to row 1

**Depends on:** Tasks 2, 6.

Carried: split `.kanban-head-right`, move the primary to row 1, density on `tmSegmented`, Group/Sort named (KB-08, KB-12).

**Files:**
- Modify: `viewer/js/screens/kanban.js`, `viewer/css/screens/kanban.css` (delete `.kanban-head-right`, `.kanban-group-btn`, `.kanban-sort-btn`, `.kanban-reset-link`, GROUP / SORT DROPDOWNS and their ≤768px lines; add `.kanban-field`), `viewer/css/components/toolbar.css` (lines 83–86: drop `.tm-segmented > button.on` and the sentence about plan 3a), `viewer/tests/shell.mock.spec.js` (the "Add task" lookups at today's lines 233 and 279 read `#topbar-primary [aria-label="Add task"]`; the test "parked controls are a column in the Filters popover and a parked chip row wraps", today's lines 554–583, made self-contained as Step 1 says), `viewer/tests/tools/capture-modals.mjs` (`openCreate` clicks `#topbar-primary [aria-label="Add task"]` after `settleRow`), `viewer/tests/kanban.mock.spec.js`

**Interfaces:**
- Consumes: `claimTopbar`, `claimTopbarPrimary`, `setTopbarCount` (Task 2), `tmSearch`, `tmSegmented`, `tmAction`, `icon()`.
- Produces (each line is a test):
  1. Row 1: `setTopbarCount(`${n} ${task|tasks} · ${visible} visible`)` on every paint; `claimTopbarPrimary().append(tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task', onClick }))` once at mount.
  2. Row 2, each a direct child of `#topbar-actions` so Filters parks them one by one: the search (unchanged), `tmSegmented([{ key: 'minimal', label: 'Minimal', title: 'Minimal cards' }, { key: 'full', label: 'Full', title: 'Full cards' }], { value, onChange })` with `role="group"` and `aria-label="Card density"`, then `label.kanban-field` "Group" and `label.kanban-field` "Sort", each `span.kanban-field__label` (Technical label voice) + `span.ef-select > select.ef-enum-select` + `icon('chevron', { size: 16 })`. Group options: Status, Phase, Epic, Area. Sort options: "Priority: high first" (`priority:desc`), "Priority: low first", "Size: largest first", "Size: smallest first", "Created: newest first", "Created: oldest first", "Started: newest first", "Started: oldest first", "Touched: newest first", "Touched: oldest first".
  3. No `tm-subcount`, `.kanban-head-right` or `.on` class is created by `kanban.js`.
  4. `.kanban-field { display: inline-flex; align-items: center; gap: var(--space-xs); }`; at ≤768px its select is `min-height: 44px`.

- [ ] **Step 1: Mocked tests** (`kanban.mock.spec.js`): at 1440×900 `#topbar-count` reads `7 tasks · 7 visible` (BOARD) and row 2 parks nothing (Filters hidden); `getByRole('combobox', { name: 'Group' })` and `{ name: 'Sort' }` resolve; choosing Group "Epic" gives one column per epic named by the epic's name (`Viewer re-skin`, `Native store`); the density segment "Minimal" has `aria-pressed="true"` after a click and cards lose `.card-tags`; at 390×844 "Add task" is in row 1, 44×44, opens the Create form; row 2 shows the search and Filters, and Group works from inside the Filters popover; the last prefs PUT after choosing Sort "Created: oldest first" has `kanban.filters.sort` `{ by: 'created', dir: 'asc' }`. Update the two `shell.mock.spec.js` lookups and `capture-modals.mjs` `openCreate` as listed. After this task Kanban's row 2 holds no `.tm-chip-row`, so the shell test "parked controls are a column in the Filters popover and a parked chip row wraps" keeps its intent with probe controls on `#/kanban` (still over the board's sticky column heads): after `mockApi(page, withContent())` and `goto('/#/kanban')`, wait for the search, then `page.evaluate` appends to `#topbar-actions` a `div.tm-chip-row#probe-chips` holding 10 `button.chip` labelled `Probe chip 1`…`10` and a `button.btn.btn--secondary#probe-wide` labelled `Probe control`; these lines go between the `goto` and today's `await expect(filters(page)).toBeVisible()` (the row's observer relayouts on added children, so Filters then appears), and the rest of the test runs unchanged with every assertion as it is (`direction` column, `stacked`, `chipWrap` non-empty and all `wrap`, `sideways` 0, `covered` empty).
- [ ] **Step 2: Run** `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the unit suite and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js shell.mock.spec.js task-form.mock.spec.js` — Expected: PASS; `components/toolbar.css` still 0 violations; `node <wt>/viewer/tests/tools/capture-modals.mjs <scratch> --only=create-untouched --port=8841` exits 0.
- [ ] **Step 5: Commit** — `feat(viewer): the board's count and Add task move to row 1; row 2 keeps search, a named density switch and labelled Group and Sort selects, each parked on its own behind Filters`

---

### Task 8: Columns, the phone column switcher, and `kanban.css` enforced

**Depends on:** Tasks 3, 5, 6, 7, and **3d Task 6** (`components/column-tabs.js`) merged into the integration branch — rebase `rr3a` onto it first; do not copy it.

KB-03, KB-11 (columns), X-12 (board empty states), and the board's focus across polls.

**Files:**
- Modify: `viewer/js/screens/kanban.js`, `viewer/css/screens/kanban.css` (rewrite what is left: page, board, columns, collapse, tabs, ≤768px; `User intent:` header), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/kanban.css`), `viewer/tests/kanban.mock.spec.js`

**Interfaces:**
- Consumes: `statusMarker`, `stateBlock`, `icon()`, `epicIndex`, `STATUS_LABELS`, `PRIORITY`; from 3d: `columnTabs({ label, columns: [{ key, label, count, panelId }], selected, onSelect }) → { el, update({ columns, selected }) }` (`div.column-tabs[role=tablist]`, `hidden` with fewer than two columns, display:none above 768px from its own CSS; `role=tab` buttons with id `${panelId}-tab`, `aria-selected` and `aria-controls=<panelId>`, reused by key on `update()` so a focused tab keeps focus; roving tabindex; `update()` scrolls the selected tab into view inside the list; Arrow/Home/End move and select, the list scrolls inside itself, hidden above 768px, tabs ≥44px). If its merged API differs, raise NEEDS_CONTEXT.
- Produces (each line is a test):
  1. **Column.** `section.kanban-col[data-key][id="kanban-col-<key>"][aria-labelledby=<title id>]` → `div.kanban-col-head` → `h2.kanban-col-title[id]` (status grouping: `statusMarker('task', key)`; epic: the epic's name; phase: the phase's name; area: the value) + `span.kanban-col-whisper` "waiting on you" (in-review only) + `span.kanban-col-count` (Technical) + `button.btn.btn--ghost.btn--icon.btn--sm.kanban-col-toggle[aria-expanded][aria-controls="kanban-col-body-<key>"][aria-label="Collapse <label>" | "Expand <label>"]` (`icon('chevron', { size: 16 })`) → `div.kanban-col-body[id="kanban-col-body-<key>"]`. A collapsed column still expands on a click anywhere on it.
  2. **Empty.** With filters set and no match, the first open column holds `stateBlock({ label: 'No match', headline: `0 of ${n} tasks match`, hint, action: { label: 'Clear filters', onClick } })` where `hint` names the filters in words (search text, priority words, epic names, phase name); every other empty column holds `p.kanban-col-empty` "No tasks".
  3. **Phone switcher.** `columnTabs({ label: 'Columns', columns: groups.map((g) => ({ key: g.key, label: <the column's label>, count: g.tasks.length, panelId: `kanban-col-${g.key}` })), selected, onSelect })`, built once at mount before the board and `update()`d on every paint. While `matchMedia('(max-width: 768px)')` matches and there are two or more columns (the tablist is not hidden): each column has `role="tabpanel"` and `aria-labelledby="kanban-col-<key>-tab"`, every column but the selected one is `hidden`, and the collapse toggles are hidden; otherwise columns carry no tab roles and none is hidden (the component hides itself above 768px). A media change repaints; `kanban.js`'s cleanup removes that listener. The selected key is local state; when it is not among the groups, the first group with tasks is selected, else the first. `kanban.js` writes no tab markup or key handling of its own.
  4. **Focus across polls.** Before a paint replaces the board, `kanban.js` records the focused element inside it as `{ taskId, focus }` (the card's `data-task-id`; the element's `data-focus` from Task 3: `link`, `copy-id`, `copy-branch`, `docs`) or `{ toggle: key }` / `{ tab: key }` (a tab is the same element across `update()`; it is found by id `kanban-col-<key>-tab`), and afterwards focuses the same thing in the new board with `{ preventScroll: true }` when it exists, else that column's tab (phone) or toggle (desktop).
  5. **Layout.** Desktop: the page never scrolls (`.kanban-page` fills the slot, column bodies scroll); columns are `--col-bg` channels with a 1px `--border-subtle` border and `--radius-lg`; JS column widths as today. ≤768px: natural flow, one column, the head sticky on `--col-bg` with no shadow. `kanban.css` has no `box-shadow`, `transform` on hover, italic, legacy alias, hex/rgba or raw `font-size`, and is in ENFORCED.

- [ ] **Step 1: Mocked tests** (`kanban.mock.spec.js`):
  - "a column head is a heading with a marker, a count and a named collapse button": In review's `h2` contains the marker word `In review` and the whisper `waiting on you`; the toggle is named `Collapse In review`, `aria-expanded="true"`; pressing it → `aria-expanded="false"`, name `Expand In review`, and the prefs PUT has `kanban.collapsed_columns` `['in-review']`.
  - **"at 390 with 230 tasks one column shows, nothing scrolls sideways, and the page ends with that column"** (Review Focus 1): `longBoard()`, 390×844; the tablist "Columns" has 5 tabs (`Blocked 23`, `Todo 92`, `In progress 46`, `In review 23`, `Done 46` by name and count) and Blocked is one of them; exactly one `.kanban-col` is visible; `document.documentElement.scrollWidth <= innerWidth`; `document.documentElement.scrollHeight <=` the visible column's bottom in page coordinates + 64; ArrowRight from the selected tab selects the next and shows its column; no collapse toggle is visible; no `.kanban-col-head` has a computed `box-shadow` other than `none`; at 390 every tab is ≥44px tall.
  - "at 1440 every column shows and the page does not scroll": `longBoard()`; no visible tablist; 5 visible columns; `scrollHeight <= innerHeight`; the Todo body's `scrollHeight > clientHeight`.
  - "a search that matches nothing says so once and offers Clear filters": type `zzzz`; one `.tm-empty` with headline `0 of 7 tasks match` and a Clear filters button; the other columns read "No tasks".
  - **"a poll that redraws the board keeps focus on the same card"** (Review Focus 2): focus T-102's link; `store.setBoard` with a new revision and T-104 renamed → T-104's title is the new one and T-102's link is focused; repeat with T-102's copy-id control focused.
  - **"keyboard walk: row 2, phases, priority, epics, then the cards in order; Enter opens, Escape returns"** (Review Focus 3): from the page's top, press Tab up to 120 times recording each `document.activeElement`'s first matching descriptor of `[data-global-search]`, `.tm-segmented button`, `.kanban-field select`, `.phase-chip--all`, `.chip[data-value="critical"]`, `.chip[data-value="__all__"]`, `.epic-options-btn`, `.kanban-col .link-row__link`, `.kanban-col .card-id`; the first index of each is strictly increasing in that order; on the first card link Enter opens `.modal--detail`, Escape closes it and that link is focused.
  - axe on `#screen-mount` at 1440 and 390, both themes: zero `color-contrast`, `nested-interactive`, `aria-*`, `scrollable-region-focusable`, `heading-order`.
- [ ] **Step 2: Run** `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2 kanban.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement**; add `screens/kanban.css` to ENFORCED.
- [ ] **Step 4: Run** the unit suite (style-rules: `screens/kanban.css` 0 violations) and `MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2` (full mocked suite) — Expected: PASS.
- [ ] **Step 5: Commit** — `feat(viewer): Kanban columns are named channels with real collapse buttons; on a phone a Columns tablist shows one at a time; a poll keeps focus where it was; kanban.css passes the style rules`

---

### Task 9: Verification

**Depends on:** Tasks 1–8.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs` (scenes, append only), `viewer/tests/mock-fixtures.js` (append `kanbanMocks`), `viewer/tests/kanban.mock.spec.js`, `viewer/tests/detail-modal.mock.spec.js`, `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11 bullets, appended)

- [ ] **Step 1: Scenes.** Add to `ALL_SCENES` (still mocked and static-served; a scene that needs `longBoard()` registers `page.route('**/api/board*', …)` and `'**/api/backlog*'` in its `routes`, which win over `mockApi`):
  - `kanban-board` — `#/kanban`, the capture board, scope `#screen-mount`;
  - `kanban-long` — `#/kanban` with `longBoard()`, scope `body` (row 1 and row 2 included);
  - `kanban-epic-more` — `longBoard()`, the Epic row's More open, scope `body`;
  - `kanban-epic-options` — `longBoard()`, Epic options open, scope `body`;
  - `kanban-archived-phases` — `longBoard()`, the archived-phases menu open, scope `body`;
  - `kanban-filters` — `longBoard()`, the topbar Filters popover open (at the desktop width it captures the row with Filters hidden), scope `body`;
  - `kanban-empty` — `#/kanban` with `zzzz` searched, scope `#screen-mount`;
  - `detail-refused-title` — T-105 in the modal, body scrolled to the end, title refused (409 with an `error`).
- [ ] **Step 2:** Run `node <wt>/viewer/tests/tools/capture-modals.mjs <wt-of-viewer-rr>/.superpowers/sdd/2026-10-06-viewer-rr-3a-kanban-detail-modal/shots-task-9 --port=8841` (all scenes, both themes, both widths). From `metrics.json`: every `kanban-*` and `detail-*` key has `overflowX` 0 and no `color-contrast`, `nested-interactive` or `aria-*` axe entry; `problems` is empty. LOOK at every new image and at `detail-rich`, `detail-long` and `create-untouched`; check them against spec §6 Kanban (card lines 1–3, columns as recessed channels, phase strip, one-line epic filter with More, labelled selects, the phone switcher) and the user's rules (no shadows, no hover motion, no coloured left rails). Fix what is wrong within 3a's files, with a test where one makes sense. In the report describe each new image plainly and list anything that belongs to another track or plan 4.
- [ ] **Step 3: Measurements** (state each number): at 390×844 with `longBoard()` — `document.documentElement.scrollHeight` on `#/kanban` (audit baseline 52,824px), `scrollWidth - innerWidth`, row 1 height; axe `color-contrast` node counts on `#/kanban` and in the detail modal, both themes (target 0).
- [ ] **Step 4: Full runs** — unit (`npm --prefix <wt>/viewer run test:unit`), mocked (`MOCK_PORT=8831 npm --prefix <wt>/viewer run test:mock -- --workers=2`), server (`env --chdir=<wt> timeout 900 C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider`). Report exact pass/fail/skip counts for each; port 8831 and 8841 have no listener afterwards.
- [ ] **Step 5:** Append to spec §11, one bullet each, prefixed "(plan 3a)":
  - §6 Kanban: the card's id is a copy control on line 1; the link carries the id (visually hidden) and the title, so the whole card is one link.
  - §6 Kanban: priority chips leave topbar row 2 for the board's filter bar beside the epic chips; row 2 keeps search, density, Group and Sort.
  - §6 Kanban: the epic row shows every in-scope epic (pinned first) on one line with More; pins, order and archived epics live in an "Epic options" popover; every filter count is open tasks.
  - §6 Kanban: the phase filter is a one-line strip on an overflow row (All, the current phase and a pressed phase never park), archived phases in a menu; the carousel is gone.
  - §6 Kanban: the phone Columns tablist scrolls sideways inside itself (a tab cannot be parked behind More).
  - §4 row 1: `claimTopbarPrimary()` clears the slot it hands over; `setTopbarCount()` puts the count's full text in its title; at ≤768px a primary with an icon shows its icon only.
  - §3.2 categorical palette (user decision): swatches come from `--cat-N` by position, unless an epic record's own `color` names a swatch 1–6 (`3`, `'cat-3'`, `'--cat-3'`); a hex or any other value is ignored.
- [ ] **Step 6: A mocked spec plan 4 can reuse** (index Global Constraints, "Every screen has a mocked spec plan 4 can reuse"). Append to `mock-fixtures.js`:
  ```js
  // The API table that puts the Kanban (and the detail modal opened from its T-102 card) on screen with content.
  export function kanbanMocks({ theme = 'dark', board = BOARD } = {}) {
    return {
      '/api/viewer/prefs': { theme, ui: {}, screens: {} },
      '/api/board': board, '/api/backlog': board, '/api/bugs': [],
      '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
    };
  }
  ```
  `kanban.mock.spec.js`'s `board()` helper and `detail-modal.mock.spec.js`'s helper call `mockApi(page, { ...kanbanMocks({ theme, board }), ...extra })` instead of their own tables, and both wait on the content selectors below; run both specs — PASS. Report, per route:
  - `#/kanban` — `kanbanMocks()` — content loaded: `.card-task[data-task-id] > .link-row__link` (a card's link; neither `stateBlock` nor the loading state draws one).
  - the detail modal (not a route; `#/kanban`, then click T-102's card link) — `kanbanMocks()` — content loaded: `.modal--detail .td-doc--embedded` (the task's document; the modal's loading and error states are `.tm-empty` blocks, never `.td-doc`).
- [ ] **Step 7: Commit** — `test(viewer): capture the Kanban and the detail modal in both themes and widths; kanbanMocks() is the Kanban's reusable mock table` and, separately, `docs(viewer): spec §11 records plan 3a's rulings`
