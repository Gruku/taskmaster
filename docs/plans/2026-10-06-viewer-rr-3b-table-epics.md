<!-- User intent: re-skin the Table, Epics and Epic detail screens onto the RR components — a Table that never cuts an ID, keeps ID and title in view and becomes cards on a phone; an Epics list and Epic detail that say each epic's real status and progress from one function, list the epic's tasks, and pass contrast in both themes. -->

# Viewer × Reality Reprojection — Plan 3b: Table, Epics, Epic detail

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read the plan 3 index (`docs/plans/2026-10-06-viewer-rr-3-screens.md`) first: its Global Constraints, File ownership and Review Focus bind every task here.

**Goal:** The Table, Epics and Epic detail screens match spec §6 in dark and light at 1440px and 390px, with real data volume: the Table in fixed columns with ID and title sticky, an edge fade as its scroll cue, status/priority markers in its cells and stacked cards on a phone; Epics as aligned link rows with the lifecycle marker and an always-drawn progress bar; Epic detail with a status breakdown and its tasks grouped by status — all epic figures from one function in `lib/epic-format.js`.

**Architecture:** `lib/epic-format.js` becomes the single source of every epic figure (`epicStats` counts a task list, `epicProgress`/`epicBreakdown`/`isCloseable` read those counts), and it holds the epic lifecycle map drawn through `marker()`. The Table stays a real `<table>` (sortable `<th>` with `aria-sort`), measured once per paint so the ID column fits its longest ID and the title column sticks beside it; each row carries one real link (the title) that a click anywhere on the row is forwarded to. Epics and Epic-detail task rows are `linkRow()`. Two tasks consume another track's work and wait for it to merge: the epic swatches (3a's `lib/epics.js` `--cat-N` swap) and the row-1 primary action and counts (3a's row-1 task). Everything else is independent of them.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §6 Table, Epics, Epic detail (and the detail template's header), §5.1, §5.3, §5.6, §5.9, §5.10, §7 (epic progress single source, ED-01), §11. Audit: TB-01…TB-06, EP-01…EP-05, ED-01…ED-04, X-01, X-02, X-03, X-07. Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "3b" and "Controller allocation".

**Depends on:** Plan 2b (HEAD 638acec) — `chipRow`, `linkRow`, `sortHeader`, `truncate`, `stateBlock`, `marker`/`statusMarker`/`priorityMarker`, `claimTopbar`/`claimTopbarPrimary`/`setTopbarCount`/`tmSearch`/`tmAction`, `epicSwatch`, the detail interceptor (`lib/open-detail.js`) and the detail modal's in-dialog link peek. Cross-track: Task 6 waits for 3a Task 1 (`lib/epics.js` swatches, `epicIndex`); Task 7 waits for 3a Task 2 (topbar row 1); see Order.

## Carry-in

From `plan-3-4-carries.md` "3b", checked against the code at 638acec:

- Table legacy hex pastels, light-theme pill contrast, trailing "···" in status cells, `outline: none` on `.tbl-row:focus-visible`, italic `.tbl-empty`, the 24 style-rule violations → Task 2. Rows losing focus on a redraw → Task 2. Table layout (fixed, sticky, scroll cue) → Task 2; stacked cards at 390 → Task 3.
- "Swap the rail ResizeObserver for a filling overflow row" → **kept as is**: `table.js:97-106` follows `overflowRow`'s documented contract (a row sized by its chips does not grow back by itself); 2b accepted it. No task.
- Epics "Exploring" on every row, bar only on some rows, 390 layout, `var(--epic, rgba(…))` hover fallback → Task 4.
- Epic detail task list, "Exploring" chip beside "active", italic 12px `.cd-block__empty` → Task 5.
- ED-01 count/percent disagreement → **already fixed** (9070dff: `epicProgress` returns `closed` and `pct` from one formula). What is still open is that the Epics list counts statuses itself (`epics.js:39-43`) while Epic detail reads the server's `stats`: Task 1 adds `epicStats()` and Tasks 4 and 5 both count through it.
- Graph items (11px `.node-id`/`.node-meta`, graph node drop shadow) → **3c**: they live in `task-detail-graph.js` and `task-detail.css`, which the index gives to 3c. Not in this plan.
- Primary action to row 1 (Table "+ Task") and the counts → Task 7.
- Pointer-only targets on the Table (rows were `tr[tabindex]` with click) → Task 2 (rows carry a real link).

## Global Constraints

The plan 3 index's Global Constraints apply unchanged (no `box-shadow`, no hover motion, no coloured left border, no `outline: none`, no italic, no text under 11px, tokens only, `--text-accent` never on `--col-bg` or an overlay surface, markers for status/priority, `ENFORCED`, no native dialogs, `User intent:` headers, mocked specs only, git rules, commands). 3b specifics:

- **Worktree and port.** `<wt>` = `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3b`, branch `rr3/b-table-epics`. Mocked specs: `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 <files>`. Nothing may listen on 8832 when a task ends.
- **Read-only to 3b:** `viewer/js/lib/epics.js`, `viewer/js/lib/topbar.js`, `viewer/js/components/status.js`, `card.js`, `detail-modal.js`, `link-row.js`, `chips.js`, `overflow-row.js`, `sort-header.js`, `empty-state.js`, `viewer/css/tokens.css`, `viewer/css/components/*.css`. A missing value or hook there → NEEDS_CONTEXT, not an edit.
- **3b owns:** `viewer/js/screens/{table,epics,epic-detail}.js`, `viewer/css/screens/{table,epics,epic-detail}.css`, `viewer/js/lib/epic-format.js`, `viewer/js/components/epic-detail-document.js`, `viewer/js/components/component-diagram.js` (imported only by the epic detail document; 3a Task 3 also adds one guard line to its click handler — 3b's Task 5 edits only `rollupSummary` and the empty-block text, so the two merge as a union), their unit tests, `viewer/tests/{table,epics,epic-detail}.mock.spec.js`. Shared append-only: `viewer/tests/mock-fixtures.js`, `viewer/tests/tools/capture-modals.mjs`, `ENFORCED`. Named edits outside that list: `viewer/tests/shell.mock.spec.js` (Task 7, only the Table tests named there).
- **One source for epic figures.** No screen counts task statuses for an epic itself: counts come from `epicStats()`, and percent, label, breakdown and "closeable" from `epicProgress()`, `epicBreakdown()`, `isCloseable()` over those counts.
- **Screens build inside their own element.** A screen never sets `class` or `style` on the mount root (`#screen-mount`): Epics builds into `section.epics-screen`, Epic detail into `div.ed-page`. (Today both write to the root and Epic detail can paint over the next screen.)
- **Inline style only for measured geometry:** the Table's ID column width, its title cells' `left`, its `min-width`; progress fill and breakdown segment widths. No inline colour, no custom property set from JS (they would need a default in `tokens.css`, which 3b does not own).
- **The Table is a `<table>`.** `sortHeader()` gives `th` + `aria-sort`, which need table semantics, and a `<tr>` cannot hold an `<a>` as a direct child, so Table rows are not `linkRow`: each row's title cell holds one real link `a.tbl-link[href="#/task/<id>"]`, and a plain primary click anywhere else on the row (not on a control, not ending a text selection) calls that link's `click()`. Modified and middle clicks on the link are the browser's. Every other list row in 3b is `linkRow()`.
- **A detail link follows the user's detail-view setting.** Table rows, Epics rows and Epic-detail task rows are plain `#/task/…` / `#/epic/…` links: the page interceptor opens the detail modal (default) or the full page, and inside the detail modal the modal peeks them in place. No 3b code sets `location.hash` for a detail.

## Review Focus

The index's five, as they touch 3b, each pinned by a named test in the owning task:

1. **The Table at 390px with real volume** (`LONG_IDS_BOARD`: 27 epics, 230 tasks, IDs up to 27 characters, 120-character titles, archived tasks). Nothing scrolls sideways at page level, no ID is cut, every cut title keeps its words in `title`, and the page stays about one screen tall (the cards scroll inside the table frame). → Task 3, test "at 390 with 230 long rows nothing scrolls sideways, no ID is cut and the page stays one screen tall"; at 1440 the same fixture → Task 2, test "at 1440 with 230 long rows the ID column fits its longest ID and ID and title stay put while the rest scrolls".
2. **Light theme on the Table's cells** (the 2b pills measured 1.0–1.5:1). Every cell — status, priority, epic, area, phase, branch, dates — passes contrast. → Task 2, test "axe (light, 1440): every table cell passes contrast"; Task 3 the same at 390; Task 8 on every route, both themes, both widths.
3. **Keyboard only.** Table: Tab reaches the search, the chips, every header button and then each row's link; Enter on a row link opens that task's detail, Escape closes it and focus is back on the same link. Epics: Tab to an epic, Enter opens it, Escape returns. Epic detail: Tab reaches each task row and the group toggles. → Task 2 "a keyboard user opens a row and comes back to it", Task 4 "a keyboard user opens an epic and comes back to it", Task 5 "every task row and group toggle is reached by Tab".
4. **Another writer changes the data while the screen is open** (the poll redraws). On the Table the keyboard stays on the same task's link and the frame keeps its scroll; if that task left the filtered set, focus goes to the row now at its place, never to `<body>`. On Epics focus stays on the same epic. → Task 2, test "a redraw keeps the keyboard on the same task and the frame where it was"; Task 4, test "a redraw keeps focus on the same epic".
5. **Leaving a screen with something in flight.** Leaving Epic detail while the epic is still loading leaves nothing behind: the next screen is not painted over, `#screen-mount` keeps no `ed-root` class or `style`, the architecture map's observer is not left running, no page error. → Task 5, test "leaving while the epic is still loading leaves nothing behind". (The Table's "leave with More open" test from 2b stays green and also counts the frame's observer: Task 2.)

## Order and parallelism

| Wave | Tasks | Waits for |
|---|---|---|
| 1 | 1 | — (schedule first: 3e's Dashboard may consume `epic-format.js`) |
| 2 | 2, 4, 5 | 1 (`LONG_IDS_BOARD`, `epicPayload`, `epicStats`, `EPIC_STATUS`) |
| 3 | 3 | 2 |
| 4 | 6 | 2, 4, 5 **and 3a Task 1 (epic swatches; `epicIndex`) merged into the integration branch** |
| 4 | 7 | 2, 4 **and 3a Task 2 (topbar row 1 at phone width) merged into the integration branch** |
| 5 | 8 | all |

In one worktree the serial order is 1, 2, 3, 4, 5, then 6 and 7 once their 3a task has merged (rebase `rr3/b-table-epics` on the integration branch first), then 8. Tasks 2, 4 and 5 touch disjoint files except `ENFORCED` (append-only union). If 3a's tasks are late, 3b stops after 5 and reports; it never copies 3a's change.

## Out of scope

- `lib/epics.js` itself, the Kanban card (`renderMinimalCard`, drawn inside the architecture map), the detail modal's chrome → 3a. The task graph → 3c. `status.js` maps → 3d (3b's epic lifecycle map lives in `epic-format.js`, see Task 1).
- Removing the `onNavigate` parameter of `mountEpicDetail` (3a's `detail-modal.js` still passes it; it is accepted and unused after Task 5) → plan 4.
- The live specs `viewer/tests/epics.spec.js` and `epic-detail.spec.js` stay as they are (not run); Tasks 4 and 5 write mocked specs that cover them. Deleting them → plan 4.
- Prefs/router robustness, shared component hardening → plan 4 (controller allocation).

---

### Task 1: Epic figures from one source, and the long-data fixture

**Depends on:** nothing. **Wave 1** — schedule first.

**Files:**
- Modify: `viewer/js/lib/epic-format.js`, `viewer/tests/unit/epic-format.test.js`, `viewer/tests/mock-fixtures.js` (append only)

**Interfaces:**
- Consumes: nothing new.
- Produces (`viewer/js/lib/epic-format.js` — replace its first three comment lines with a `User intent:` header: *"every epic figure the viewer shows — counts, percent, label, breakdown, closeable, lifecycle word — comes from this one file, so the Epics list, Epic detail and the Dashboard can never disagree"*; keep it DOM-free):
  ```js
  export const STATUS_GROUPS;   // Object.freeze(['in-progress', 'in-review', 'blocked', 'todo', 'done', 'archived'])
                                // the order Epic detail lists its task groups and draws its breakdown
  export function epicStats(tasks) → {
    total, todo, 'in-progress', 'in-review', blocked, done, archived, other,
  }
    // Counts every element of `tasks` that is a non-null object (a non-array argument counts as []).
    // A missing status counts as 'todo' (the server's default, backlog_server.py _epic_stats); a status that is
    // not one of the six counts in `total` and in `other`. total === sum of the seven buckets, always.
  export function epicProgress(stats)            // unchanged: { total, done, archived, closed, pct, label }
  export function isCloseable(stats) → boolean   // epicProgress(stats): total > 0 && closed === total
  export function epicBreakdown(stats) → Array<{ status, count, pct }>
    // STATUS_GROUPS order, then 'other'; only buckets with count > 0; [] when total is 0.
    // pct are integers summing to exactly 100 (largest remainder; a tie goes to the earlier bucket).
    // Reads the seven bucket keys of `stats`; missing keys are 0. total is the sum of the buckets read.
  export const EPIC_STATUS;   // frozen like status.js tables: { label, shape, tone }
                              // active ['Active','◐','accent'], planned ['Planned','○','neutral'],
                              // done ['Done','●','success'], archived ['Archived','✕','neutral']   (spec §5.1 by meaning)
  export function epicStatusMeta(value) → { label, shape, tone }
    // a fresh object; missing/empty → Active (the server's and today's screen default); an unknown string → { label:
    // that string, shape '○', tone 'neutral' }; a non-string → { label: '—', shape: '○', tone: 'neutral' }
  ```
  `designBadge`, `componentGlyph`, `progressPercent`, `tasksForComponent` and `closeableBadge` stay unchanged in this task (`closeableBadge` is deleted in Task 6, once Tasks 4 and 5 no longer call it).
- Produces (`viewer/tests/mock-fixtures.js`, appended at the end):
  ```js
  export const LONG_IDS_BOARD;   // board-shaped: { revision: 'r-long', cursor: 'c-long', meta, phases, epics[27], tasks[230] }
  export function epicPayload(board, id, extra = {}) → object   // GET /api/epic/<id>, built the way the server builds it
  ```
  3a's Task 1 appends `longBoard()` (the index's general long-data board) to the same file. 3b needs its own board and does not wait for 3a's: the Table's review focus is IDs of very different lengths (TB-02 cut "v3-polish…"), so `LONG_IDS_BOARD` mixes `T-1xxx` ids with slug ids up to 27 characters; Epics and Epic detail need archived tasks (closed = done + archived) and planned epics, which `longBoard()` does not have. The names differ, so both land as a union.

- [ ] **Step 1: Write the failing unit tests** — append to `viewer/tests/unit/epic-format.test.js` (extend its import with `epicStats, isCloseable, epicBreakdown, EPIC_STATUS, epicStatusMeta, STATUS_GROUPS`, and add `import { LONG_IDS_BOARD, epicPayload } from '../mock-fixtures.js';`):

```js
test('epicStats — counts each status, missing as todo, unknown as other; total is the sum', () => {
  const s = epicStats([
    { status: 'done' }, { status: 'done' }, { status: 'archived' }, { status: 'in-review' },
    { status: 'blocked' }, { status: 'in-progress' }, {}, { status: 'paused' }, null, 'x',
  ]);
  assert.deepEqual(s, { total: 8, todo: 1, 'in-progress': 1, 'in-review': 1, blocked: 1, done: 2, archived: 1, other: 1 });
  assert.deepEqual(epicStats(undefined), { total: 0, todo: 0, 'in-progress': 0, 'in-review': 0, blocked: 0, done: 0, archived: 0, other: 0 });
});

test('epicStats feeds epicProgress — the spec example reads "35/55 closed · 25 done · 10 archived"', () => {
  const tasks = [
    ...Array(25).fill({ status: 'done' }), ...Array(10).fill({ status: 'archived' }), ...Array(20).fill({ status: 'todo' }),
  ];
  const p = epicProgress(epicStats(tasks));
  assert.deepEqual({ closed: p.closed, done: p.done, archived: p.archived, total: p.total, pct: p.pct, label: p.label },
    { closed: 35, done: 25, archived: 10, total: 55, pct: 64, label: '35/55 closed · 25 done · 10 archived' });
});

test('isCloseable — every task done or archived, and at least one task', () => {
  assert.equal(isCloseable(epicStats([{ status: 'done' }, { status: 'archived' }])), true);
  assert.equal(isCloseable(epicStats([{ status: 'done' }, { status: 'todo' }])), false);
  assert.equal(isCloseable(epicStats([])), false);
  assert.equal(isCloseable(undefined), false);
});

test('epicBreakdown — group order, non-zero only, percents sum to exactly 100', () => {
  const b = epicBreakdown(epicStats([{ status: 'todo' }, { status: 'done' }, { status: 'in-progress' }]));
  assert.deepEqual(b, [
    { status: 'in-progress', count: 1, pct: 34 }, { status: 'todo', count: 1, pct: 33 }, { status: 'done', count: 1, pct: 33 },
  ]);
  assert.deepEqual(epicBreakdown(epicStats([])), []);
  const odd = epicBreakdown(epicStats([...Array(7).fill({ status: 'done' }), { status: 'paused' }, { status: 'blocked' }]));
  assert.deepEqual(odd.map((x) => x.status), ['blocked', 'done', 'other']);
  assert.equal(odd.reduce((n, x) => n + x.pct, 0), 100);
  assert.deepEqual(STATUS_GROUPS, ['in-progress', 'in-review', 'blocked', 'todo', 'done', 'archived']);
});

test('EPIC_STATUS — lifecycle by meaning; missing is Active, unknown is shown as it is', () => {
  assert.deepEqual(Object.keys(EPIC_STATUS), ['active', 'planned', 'done', 'archived']);
  assert.deepEqual(epicStatusMeta('done'), { label: 'Done', shape: '●', tone: 'success' });
  assert.deepEqual(epicStatusMeta(undefined), { label: 'Active', shape: '◐', tone: 'accent' });
  assert.deepEqual(epicStatusMeta(''), { label: 'Active', shape: '◐', tone: 'accent' });
  assert.deepEqual(epicStatusMeta('paused'), { label: 'paused', shape: '○', tone: 'neutral' });
  assert.deepEqual(epicStatusMeta(7), { label: '—', shape: '○', tone: 'neutral' });
  epicStatusMeta('done').label = 'x';
  assert.equal(EPIC_STATUS.done.label, 'Done');
});

test('LONG_IDS_BOARD — the volume the index review focus names', () => {
  assert.equal(LONG_IDS_BOARD.epics.length, 27);
  assert.equal(LONG_IDS_BOARD.tasks.length, 230);
  assert.equal(new Set(LONG_IDS_BOARD.tasks.map((t) => t.id)).size, 230);
  assert.equal(Math.max(...LONG_IDS_BOARD.tasks.map((t) => t.id.length)), 27);
  assert.ok(LONG_IDS_BOARD.tasks.filter((t) => t.title.length === 120).length >= 70);
  assert.ok(LONG_IDS_BOARD.tasks.some((t) => t.status === 'archived'));
  // every epic has tasks, and the per-epic counts add up to the board
  const ids = LONG_IDS_BOARD.epics.map((e) => e.id);
  const sum = ids.reduce((n, id) => n + epicStats(LONG_IDS_BOARD.tasks.filter((t) => t.epic === id)).total, 0);
  assert.equal(sum, 230);
  assert.ok(ids.every((id) => LONG_IDS_BOARD.tasks.some((t) => t.epic === id)));
});

test('epicPayload — the server shape: stats from the epic\'s tasks, closeable, task rows', () => {
  const p = epicPayload(LONG_IDS_BOARD, 'epic-01', { done_when: 'x' });
  const mine = LONG_IDS_BOARD.tasks.filter((t) => t.epic === 'epic-01');
  assert.equal(p.id, 'epic-01');
  assert.equal(p.stats.total, mine.length);
  assert.equal(p.stats.done, mine.filter((t) => t.status === 'done').length);
  assert.equal(p.closeable, p.stats.closeable);
  assert.deepEqual(p.tasks.map((t) => t.id), mine.map((t) => t.id));
  assert.deepEqual(Object.keys(p.tasks[0]).sort(), ['component', 'design_change', 'id', 'phase', 'priority', 'status', 'title']);
  assert.equal(p.done_when, 'x');
  assert.equal(p.design_status, 'exploring');
});
```

- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/epic-format.test.js` — Expected: FAIL (`epicStats` is not exported; `LONG_IDS_BOARD` is not exported).

- [ ] **Step 3: Implement `epic-format.js`.**

```js
export const STATUS_GROUPS = Object.freeze(['in-progress', 'in-review', 'blocked', 'todo', 'done', 'archived']);
const BUCKETS = [...STATUS_GROUPS, 'other'];

export function epicStats(tasks) {
  const out = { total: 0, todo: 0, 'in-progress': 0, 'in-review': 0, blocked: 0, done: 0, archived: 0, other: 0 };
  for (const t of Array.isArray(tasks) ? tasks : []) {
    if (!t || typeof t !== 'object') continue;
    const s = t.status == null || t.status === '' ? 'todo' : t.status;
    out[STATUS_GROUPS.includes(s) ? s : 'other'] += 1;
    out.total += 1;
  }
  return out;
}

export function isCloseable(stats) {
  const p = epicProgress(stats);
  return p.total > 0 && p.closed === p.total;
}

export function epicBreakdown(stats) {
  const rows = BUCKETS.map((status) => ({ status, count: Math.max(0, Number(stats?.[status]) || 0) })).filter((r) => r.count > 0);
  const total = rows.reduce((n, r) => n + r.count, 0);
  if (!total) return [];
  const exact = rows.map((r) => (r.count * 100) / total);
  const pct = exact.map(Math.floor);
  let left = 100 - pct.reduce((n, x) => n + x, 0);
  const order = exact.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of order) { if (!left) break; pct[i] += 1; left -= 1; }
  return rows.map((r, i) => ({ ...r, pct: pct[i] }));
}

const freeze = (table) => Object.freeze(Object.fromEntries(
  Object.entries(table).map(([value, [label, shape, tone]]) => [value, Object.freeze({ label, shape, tone })])));

// An epic's lifecycle by meaning, as the spec's status table has it: active work is in motion, a planned epic has not
// started, a done one is complete, an archived one is dropped. Drawn through status.js marker(), like every status.
export const EPIC_STATUS = freeze({
  active: ['Active', '◐', 'accent'],
  planned: ['Planned', '○', 'neutral'],
  done: ['Done', '●', 'success'],
  archived: ['Archived', '✕', 'neutral'],
});

export function epicStatusMeta(value) {
  if (value == null || value === '') return { ...EPIC_STATUS.active };
  if (typeof value !== 'string') return { label: '—', shape: '○', tone: 'neutral' };
  return Object.hasOwn(EPIC_STATUS, value) ? { ...EPIC_STATUS[value] } : { label: value, shape: '○', tone: 'neutral' };
}
```

- [ ] **Step 4: Implement the fixture** — append to `viewer/tests/mock-fixtures.js`:

```js
// Plan 3's review focus 1: a real backlog's volume — 27 epics, 230 tasks, ids up to 27 characters (slug ids every
// tenth task, one very long one), every third title 120 characters, one task in ten archived, long branches.
const LONG_WORDS = ['store', 'viewer', 'handover', 'linear', 'gate', 'index', 'board', 'theme', 'sync', 'hooks'];
const LONG_SENTENCE = 'keep the whole sentence readable when the table has to cut it short ';
export const LONG_IDS_BOARD = (() => {
  const epics = Array.from({ length: 27 }, (_, i) => (i === 26
    ? { id: 'database-native-tracking', name: 'Database-native tracking: the SQLite store becomes the one authority', status: 'active', phase: 'P1' }
    : { id: `epic-${String(i + 1).padStart(2, '0')}`, name: `${LONG_WORDS[i % 10]} work ${i + 1}`, status: ['active', 'active', 'planned', 'done', 'archived'][i % 5], phase: 'P1' }));
  const statuses = ['todo', 'in-progress', 'in-review', 'blocked', 'done', 'done', 'todo', 'done', 'todo', 'archived'];
  const tasks = Array.from({ length: 230 }, (_, i) => {
    const id = i === 229 ? 'database-native-n17-cutover' : i % 10 === 9 ? `v3-polish-${String(i).padStart(3, '0')}` : `T-${1000 + i}`;
    const base = `${LONG_WORDS[i % 10]} task ${i + 1}`;
    return {
      id, title: i % 3 === 0 ? `${base} — ${LONG_SENTENCE.repeat(3)}`.slice(0, 120) : base,
      status: statuses[i % 10], priority: ['critical', 'high', 'medium', 'low'][i % 4], epic: epics[i % 27].id, phase: 'P1',
      area: ['viewer-ui', 'store', 'docs', 'hooks'][i % 4], estimate: ['S', 'M', 'L', '3d'][i % 4], depends_on: [],
      ...(i % 5 === 0 ? { branch: `feat/${id}-${'long-branch-name-'.repeat(3)}end` } : {}),
      ...(i % 4 === 0 ? { started: `2026-09-2${i % 9}T09:00:00Z` } : {}),
    };
  });
  return { revision: 'r-long', cursor: 'c-long', meta: { project: 'Long fixture' },
    phases: [{ id: 'P1', name: 'Foundation', status: 'active' }], epics, tasks };
})();

// GET /api/epic/<id> for an epic of `board`, built as backlog_server.py _epic_full_from builds it.
export function epicPayload(board, id, extra = {}) {
  const epic = board.epics.find((e) => e.id === id) ?? { id };
  const tasks = board.tasks.filter((t) => t.epic === id);
  const count = (s) => tasks.filter((t) => (t.status || 'todo') === s).length;
  const stats = { total: tasks.length, done: count('done'), 'in-progress': count('in-progress'), 'in-review': count('in-review'),
    todo: count('todo'), blocked: count('blocked'), archived: count('archived') };
  stats.closeable = stats.total > 0 && stats.done + stats.archived === stats.total;
  return {
    description: '', docs: {}, components: {}, design_status: 'exploring', done_when: '', area: null, ...epic,
    stats, closeable: stats.closeable, component_rollup: {},
    attention: tasks.filter((t) => t.status === 'blocked').map((t) => ({ id: t.id, title: t.title, blocked: true, why: t.blockers || '' })),
    tasks: tasks.map((t) => ({ id: t.id, title: t.title, status: t.status || 'todo', component: t.component ?? null,
      priority: t.priority, phase: t.phase, design_change: t.design_change ?? null })),
    ...extra,
  };
}
```

- [ ] **Step 5: Run** `env --chdir=<wt> node --test viewer/tests/unit/epic-format.test.js viewer/tests/unit/epic-closeable.test.js` — Expected: PASS. Then `npm --prefix <wt>/viewer run test:unit` — Expected: PASS (state the count).
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/js/lib/epic-format.js viewer/tests/unit/epic-format.test.js viewer/tests/mock-fixtures.js` then `git -C <wt> commit -m "feat(viewer): every epic figure from one file — task counts, breakdown, closeable and the lifecycle word — and a long-data fixture with a real backlog's volume"`

---

### Task 2: The Table — fixed columns, sticky ID and title, a scroll cue, markers in the cells, rows that are links

**Depends on:** Task 1 (`LONG_IDS_BOARD`). **Wave 2**, beside Tasks 4 and 5.

**Files:**
- Modify: `viewer/js/screens/table.js`, `viewer/tests/table.mock.spec.js`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `'screens/table.css'`)
- Rewrite: `viewer/css/screens/table.css`

**Interfaces:**
- Consumes: `statusMarker('task', v)`, `priorityMarker(v)` (status.js); `truncate()` (lib/text.js); `stateBlock()` (empty-state.js); `sortHeader()`; `chipRow()`, `epicSwatch` (unchanged chip rail); `formatAbsolute`.
- Produces: no exports. DOM contract later tasks and specs rely on:
  ```
  section.tbl-screen
    div.tbl-chips                          (unchanged from 2b)
    div.tbl-frame[data-more-end?][data-scrolled?]
      div.tbl-host                         the scroller, both axes
        table.tbl                          style.minWidth = calc(<id px>px + <sum of fixed rem + 20>rem)
          colgroup > col.tbl-col--<key>    id: style.width = '<px>px'; fixed columns: style.width = '<n>rem'; title: none
          thead > tr > th.sort-th.tbl-th[data-key]
          tbody > tr.tbl-row[data-task-id] > td.tbl-cell.tbl-cell--<key>
                   title cell: a.tbl-link[href="#/task/<encoded id>"] > span.truncate (the title, or the id when empty)
      div.tbl-fade[aria-hidden="true"]
  ```
- Columns (in order; `width` in rem, fixed): `id` (measured), `title` (rest, at least 20rem), `status` 9, `priority` 7.5, `phase` 7, `epic` 12.5, `area` 9, `estimate` (label "Size") 4.5, `branch` 14 (not sortable), `started` 8. Cells: id → `span.t-id`; title → the link; status → `statusMarker('task', t.status)`; priority → `priorityMarker(lowercased)` or `span.t-none` "—"; phase/area → `truncate(v, { className: 't-tech' })` or "—"; epic → `truncate(<epic name, else id>)` or "—"; estimate → `span.t-tech`; branch → `truncate(v, { tag: 'code', className: 't-tech' })` or "—"; started → `span.t-tech` with `formatAbsolute(t.started, { time: false, year: true }) || t.started`, or "—". `STATUS_ORDER` gains `archived: 5`.
- Behaviour (each line is a test):
  1. **ID never cut.** After each paint, a hidden probe `span.t-id.tbl-probe` holding the longest `id` (by length) is measured inside `.tbl-host` and removed; the ID `col` width = `ceil(probe width) + the ID th's padding-left + padding-right + 1` px; every title `td`/`th` gets `style.left` = the same px. Re-measured only when the longest id changes, and once after `document.fonts.ready` (guarded so it does nothing after unmount).
  2. **Sticky.** Header cells stick to the top; ID and title cells stick to the left (ID at 0, title at the ID width); the two header corners stack above both. Sticky cells are opaque (`--card-bg`; hovered row `--card-bg-hover`).
  3. **Scroll cue.** `.tbl-frame` has `data-more-end` while `scrollLeft + clientWidth < scrollWidth - 1` and `data-scrolled` while `scrollLeft > 0`, updated on the host's `scroll` (passive), by a `ResizeObserver` on the host, and after every paint. The fade shows only with `data-more-end`; the title column's right border is `--border-strong` with `data-scrolled`. The observer is disconnected on unmount.
  4. **Rows are links.** No `tr` has `tabindex`. A plain primary click (`button === 0`, no Ctrl/⌘/Shift/Alt) on a row outside any `a[href]`, `button`, `input`, `select`, `textarea`, with no text selected, calls that row's `.tbl-link` `click()`; anything else does nothing. The interceptor then opens the detail modal (or the full page under `detail_view_mode: 'full'`).
  5. **A redraw keeps place.** Before rebuilding, the screen records the host's `scrollTop`/`scrollLeft`, the focused header's key, or the focused row link's task id and row index. After rebuilding it restores the scroll and focuses (with `preventScroll`) the same header button; else the link of the same task; else the link at the same index clamped to the last row; else, with no rows, the ID header's button. Nothing is focused when nothing in the table was.
  6. **Empty states are state blocks.** No tasks: `stateBlock({ label: 'Table', headline: 'No tasks yet.', hint: 'Tasks added to the backlog show up here.' })`. Filters match nothing: `stateBlock({ label: 'No match', headline: '0 of <n> tasks match.', hint: <buildFilterHint>, action: { label: 'Clear filters', onClick: clearFilters } })`. `emptyState` is no longer imported.
  7. `table.css` passes the style rules and is in `ENFORCED`; no legacy alias, no hex/rgba, no `font-size` literal, no italic, no `outline: none`.
  8. **A link can name the status** (controller ruling; 3e's Dashboard links "In progress" to `#/table?status=in-progress` and "Waiting on you" to `#/table?status=in-review`). At mount, `params.status` (the router's query parameters, `router.js` `parseHash`) is split on `,`, trimmed and deduplicated, and only values that are Status chip options (`TASK_STATUS` keys other than `archived`) are kept. If any remain, they replace `state.filters.status` for this visit; the other saved filters and the search stay as saved. The seed is not persisted at mount (no prefs write until the user changes something), and pressing chips afterwards does not rewrite the URL. An absent, empty or all-unknown value changes nothing.

- [ ] **Step 1: Update the existing tests for markers and links.** In `viewer/tests/table.mock.spec.js`: `statusCells` becomes `page.locator('.tbl-row .tbl-cell--status .marker__word').allTextContents()`; in the 2b axe test remove the `exclude: [['#screen-mount .tbl-row']]` part (contrast is now checked on every cell); in "leaving the Table…" add `host: on('tbl-host')` to `watching()` and expect `{ rail: 1, rows: 8, host: 1 }` before leaving and `{ rail: 0, rows: 0, host: 0 }` after. Change the fixture import to `import { BOARD, LONG_IDS_BOARD, DETAIL_TASK, taskDetail } from './mock-fixtures.js';`, extend `boot()` with a `board = TABLE_BOARD` option used for `/api/board` and `/api/backlog`, make its wait for `table.tbl` apply to any route starting with `#/table` (`route.startsWith('#/table')`), and add `'/api/task/T-102/detail': taskDetail(DETAIL_TASK)` to its mock table (the dialog is then named `DETAIL_TASK.title`, "Re-skin the Kanban cards and columns").

- [ ] **Step 2: Write the failing tests** in `viewer/tests/table.mock.spec.js`:

```js
const host = (page) => page.locator('.tbl-host');
// Another writer's change, as the poll would deliver it: the store gets a new board revision and every screen redraws.
const otherWriter = (page, id, patch) => page.evaluate(([id, patch]) => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  Object.assign(next.tasks.find((t) => t.id === id), patch);
  next.revision = `r-${Date.now()}`;
  store.setBoard(next);
}), [id, patch]);

test('cells are markers and plain words — no pills, no "···"', async ({ page }) => {
  await boot(page);
  const row = page.locator('.tbl-row[data-task-id="T-102"]');
  await expect(row.locator('.tbl-cell--status .marker__word')).toHaveText('In progress');
  await expect(row.locator('.tbl-cell--priority .marker__word')).toHaveText('Critical');
  await expect(row.locator('.tbl-cell--epic')).toHaveText('Viewer re-skin');
  await expect(page.locator('.t-status, .t-pri, .t-epic, .t-area')).toHaveCount(0);
  const cut = await page.locator('.tbl-cell--status, .tbl-cell--priority').evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth).length);
  expect(cut).toBe(0);
  await expect(page.locator('.tbl-row[tabindex]')).toHaveCount(0);
});

test('at 1440 with 230 long rows the ID column fits its longest ID and ID and title stay put while the rest scrolls', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-row')).toHaveCount(230);
  const ids = await page.locator('.tbl-cell--id').evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent));
  expect(ids).toEqual([]);
  expect(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-more-end', '');
  await expect(page.locator('.tbl-fade')).toHaveCSS('opacity', '1');
  const titles = await page.locator('.tbl-cell--title .truncate').evaluateAll((els) => els.filter((el) => el.title !== el.textContent).length);
  expect(titles).toBe(0);
  await host(page).evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-scrolled', '');
  await expect(page.locator('.tbl-frame')).not.toHaveAttribute('data-more-end', '');
  const at = await page.evaluate(() => {
    const h = document.querySelector('.tbl-host').getBoundingClientRect().left;
    const id = document.querySelector('.tbl-row .tbl-cell--id').getBoundingClientRect();
    const title = document.querySelector('.tbl-row .tbl-cell--title').getBoundingClientRect().left;
    return { id: Math.round(id.left - h), title: Math.round(title - id.right) };
  });
  expect(Math.abs(at.id)).toBeLessThanOrEqual(1);
  expect(Math.abs(at.title)).toBeLessThanOrEqual(1);
});

test('a click on a row opens its task; Ctrl+click on its link is the browser\'s', async ({ page }) => {
  await boot(page);
  await page.locator('.tbl-row[data-task-id="T-102"] .tbl-cell--status').click();
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  const [popup] = await Promise.all([
    page.context().waitForEvent('page'),
    page.locator('.tbl-row[data-task-id="T-102"] .tbl-link').click({ modifiers: ['Control'] }),
  ]);
  await popup.close();
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('a keyboard user opens a row and comes back to it', async ({ page }) => {
  await boot(page);
  // After the last header button, Tab goes to the first row's link: rows are not tab stops of their own.
  await page.locator('th[data-key="started"] button.sort-header').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('.tbl-row').first().locator('.tbl-link')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('.tbl-row').nth(1).locator('.tbl-link')).toBeFocused();
  const link = page.locator('.tbl-row[data-task-id="T-102"] .tbl-link');
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(link).toBeFocused();
});

test('a redraw keeps the keyboard on the same task and the frame where it was', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await host(page).evaluate((el) => { el.scrollTop = 900; el.scrollLeft = 200; });
  const link = page.locator('.tbl-row[data-task-id="T-1040"] .tbl-link');
  await link.evaluate((a) => a.focus({ preventScroll: true }));
  const where = await host(page).evaluate((el) => [el.scrollTop, el.scrollLeft]);
  expect(where[0]).toBeGreaterThan(0);
  await otherWriter(page, 'T-1040', { title: 'Renamed by another writer' });
  await expect(link).toHaveText('Renamed by another writer');
  await expect(link).toBeFocused();
  expect(await host(page).evaluate((el) => [el.scrollTop, el.scrollLeft])).toEqual(where);
  // The focused task leaves the filtered set: the row now at its place takes the keyboard, never <body>.
  await chip(page, 'Status', 'todo').click();
  const ids = await page.locator('.tbl-row').evaluateAll((rows) => rows.map((r) => r.dataset.taskId));
  const at = 3;
  await page.locator(`.tbl-row[data-task-id="${ids[at]}"] .tbl-link`).focus();
  await otherWriter(page, ids[at], { status: 'done' });
  await expect(page.locator('.tbl-row')).toHaveCount(ids.length - 1);
  const now = page.locator('.tbl-row .tbl-link:focus');
  await expect(now).toHaveCount(1);
  expect(await now.evaluate((a) => a.closest('tr').dataset.taskId)).toBe(ids[at + 1]);
});

test('a link with ?status= opens the Table with those chips pressed, without saving them', async ({ page }) => {
  const puts = await boot(page, { route: '#/table?status=in-progress', table: { filters: { epic: [] } } });
  await expect(chip(page, 'Status', 'in-progress')).toHaveAttribute('aria-pressed', 'true');
  const ip = TABLE_BOARD.tasks.filter((t) => t.status === 'in-progress').length;
  await expect.poll(() => statusCells(page)).toEqual(Array(ip).fill('In progress'));
  await expect(page.locator('.sidebar-link[data-key="table"]')).toHaveClass(/active/);
  expect(puts.filter((b) => b.table)).toEqual([]);
});

test('?status= takes a comma list, ignores unknown values, and an all-unknown value changes nothing', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await boot(page, { route: '#/table?status=in-review,blocked,bogus,in-review' });
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(2);
  await expect.poll(async () => new Set(await statusCells(page))).toEqual(new Set(['In review', 'Blocked']));
  await page.evaluate(() => { location.hash = '#/table?status=bogus'; });
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('no tasks is a state block', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const empty = { ...BOARD, tasks: [] };
  await mockApi(page, { '/api/board': empty, '/api/backlog': empty, '/api/bugs': [] });
  await page.goto('/#/table');
  await expect(page.locator('.tbl-empty .tm-empty__headline')).toHaveText('No tasks yet.');
  await expect(page.locator('.tbl-empty .tm-empty__label')).toHaveText('Table');
});

test('no match is a state block whose one action clears the filters', async ({ page }) => {
  await boot(page);
  await page.getByPlaceholder('Filter… (prefix ! to exclude)').fill('nothing-matches-this');
  const block = page.locator('.tbl-empty .tm-empty');
  await expect(block.locator('.tm-empty__label')).toHaveText('No match');
  await expect(block.locator('.tm-empty__headline')).toHaveText(`0 of ${TABLE_BOARD.tasks.length} tasks match.`);
  await block.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}, 1440): every table cell passes contrast`, async ({ page }) => {
    await boot(page, { theme, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => (await window.axe.run(document.getElementById('screen-mount'),
      { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive'] }, resultTypes: ['violations'] })).violations);
    expect(v.map((x) => `${x.id}: ${x.nodes.length} — ${x.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
```

- [ ] **Step 3: Run** `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js` — Expected: the new tests FAIL (no `.marker__word` in cells, no `.tbl-frame`, rows have `tabindex`, light contrast violations).

- [ ] **Step 4: Implement `table.js`.** Keep the chip rail, filters, sort, search, persistence and Clear button exactly as they are. Replace `COLUMNS` with the list in Interfaces (each with `key`, `label`, `sortable`, `width` (rem or absent), `get`, `cell(t, ctx)` returning a Node; `ctx` = `{ epicName }`), drop `esc()` and every `innerHTML`, and build the frame once at mount:

```js
const frame = h('div', { class: 'tbl-frame' });
const tableHost = h('div', { class: 'tbl-host' });
const fade = h('div', { class: 'tbl-fade', 'aria-hidden': 'true' });
frame.append(tableHost, fade);
screen.appendChild(frame);
const cue = () => {
  frame.toggleAttribute('data-more-end', tableHost.scrollLeft + tableHost.clientWidth < tableHost.scrollWidth - 1);
  frame.toggleAttribute('data-scrolled', tableHost.scrollLeft > 0);
};
tableHost.addEventListener('scroll', cue, { passive: true });
const hostObserver = window.ResizeObserver ? new ResizeObserver(cue) : null;
hostObserver?.observe(tableHost);
```

  `mount(root, { store, api, prefs, params })`: right after `state` is built from prefs, apply line 8 (`const seed = [...new Set(String(params?.status ?? '').split(',').map((v) => v.trim()))].filter((v) => v !== 'archived' && Object.hasOwn(TASK_STATUS, v)); if (seed.length) state.filters.status = seed;`), before the first `paint()`; mount never calls `persist()` itself.

  `renderTable(tasks, total, ctx)`: take the snapshot (line 5), build `table.tbl` with a `colgroup` (`col.style.width = col.width + 'rem'` for fixed columns), the `sortHeader` row (`th.dataset.key`, class `tbl-th`), and the body (one `tbody` click listener per build implementing line 4: `if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; if (e.target.closest('a[href], button, input, select, textarea')) return; if (String(getSelection?.() ?? '')) return; e.target.closest('tr.tbl-row')?.querySelector('a.tbl-link')?.click();`), replace the host's children, then `sizeColumns(tbl, tasks)` (line 1), restore (line 5) and `cue()`.

```js
let measured = { longest: null, px: 0 };
function sizeColumns(tbl, tasks) {
  const longest = tasks.reduce((a, t) => (String(t.id ?? '').length > a.length ? String(t.id) : a), 'ID');
  if (longest !== measured.longest) {
    const probe = h('span', { class: 't-id tbl-probe' }, longest);
    tableHost.append(probe);
    const th = tbl.querySelector('th[data-key="id"]');
    const cs = getComputedStyle(th);
    measured = { longest, px: Math.ceil(probe.getBoundingClientRect().width) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 1 };
    probe.remove();
  }
  tbl.querySelector('col.tbl-col--id').style.width = `${measured.px}px`;
  tbl.style.minWidth = `calc(${measured.px}px + ${FIXED_REM + TITLE_MIN_REM}rem)`;
  for (const el of tbl.querySelectorAll('th[data-key="title"], td.tbl-cell--title')) el.style.left = `${measured.px}px`;
}
```

  where `TITLE_MIN_REM = 20` and `FIXED_REM` = the sum of the fixed widths, computed from `COLUMNS` (71.5). After mount: `document.fonts?.ready.then(() => { if (!alive) return; measured.longest = null; paint(); });` with `let alive = true` set false in the cleanup, which also removes the scroll listener and disconnects `hostObserver`. Rows: `h('tr', { class: 'tbl-row', 'data-task-id': t.id })`, cells `h('td', { class: 'tbl-cell tbl-cell--' + key }, col.cell(t, ctx))`; the title link `h('a', { class: 'tbl-link', href: '#/task/' + encodeURIComponent(t.id) }, truncate(t.title || t.id))`. Empty states per line 6 (`td.tbl-empty[colspan]`). Use `h()` from `../util/h.js`.

- [ ] **Step 5: Rewrite `viewer/css/screens/table.css`** (tokens only). Keep the chip-rail block (lines 9–28 today) unchanged; replace everything else with:

```css
/* User intent: the Table shows every task in fixed columns that never cut an ID, keeps ID and title in view while the
   rest scrolls sideways inside its frame (a fade says there is more), and reads in both themes. */
.tbl-screen { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; gap: var(--page-gap); }

.tbl-frame {
  position: relative; display: flex; flex: 1 1 auto; min-height: 0; min-width: 0; overflow: hidden;
  border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); background: var(--card-bg);
}
.tbl-host { flex: 1 1 auto; min-height: 0; min-width: 0; overflow: auto; }
.tbl-fade {
  position: absolute; top: 0; right: 0; bottom: 0; width: var(--space-2xl); pointer-events: none;
  background: linear-gradient(to left, var(--card-bg), transparent); opacity: 0;
  transition: opacity var(--dur-standard) var(--ease-hourglass);
}
.tbl-frame[data-more-end] .tbl-fade { opacity: 1; }
.tbl-probe { position: absolute; visibility: hidden; white-space: nowrap; }

table.tbl {
  table-layout: fixed; width: 100%; border-collapse: separate; border-spacing: 0;
  color: var(--foreground-default); font-family: var(--font-narrator); font-weight: var(--font-narrator-weight);
  font-size: var(--size-narrator-small);
}
.tbl-th {
  position: sticky; top: 0; z-index: 2; padding: var(--space-xs) var(--space-sm);
  background: var(--card-bg); border-bottom: 1px solid var(--border-default); color: var(--foreground-subtle);
  font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-label);
  letter-spacing: var(--tracking-wide); text-transform: uppercase; text-align: left; white-space: nowrap; user-select: none;
}
.tbl-th[aria-sort] { color: var(--foreground-bold); }
.tbl-cell {
  padding: var(--space-xs) var(--space-sm); border-bottom: 1px solid var(--border-subtle); background: var(--card-bg);
  white-space: nowrap; overflow: hidden; vertical-align: middle;
}
.tbl-row { cursor: pointer; }
.tbl-row:hover > .tbl-cell { background: var(--card-bg-hover); }

/* ID and title stay in view; the title's left offset is the measured ID width, set from table.js. */
.tbl-th[data-key="id"], .tbl-cell--id { position: sticky; left: 0; z-index: 1; }
.tbl-th[data-key="title"], .tbl-cell--title { position: sticky; z-index: 1; border-right: 1px solid var(--border-subtle); }
.tbl-th:is([data-key="id"], [data-key="title"]) { z-index: 3; }
.tbl-frame[data-scrolled] :is(.tbl-th[data-key="title"], .tbl-cell--title) { border-right-color: var(--border-strong); }

.tbl-link { display: block; min-width: 0; color: var(--foreground-bold); font-weight: var(--font-narrator-weight-semi); text-decoration: none; }
.t-id, .t-tech {
  font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small);
  color: var(--foreground-default); white-space: nowrap;
}
code.t-tech { background: transparent; padding: 0; }
.t-none { color: var(--foreground-subtle); }
.tbl-empty { padding: var(--space-2xl) var(--space-lg); white-space: normal; }
```

  Add `'screens/table.css'` to `ENFORCED`. (Check `--font-narrator-weight-semi` exists in `tokens.css` before using it; it does at 638acec.)

- [ ] **Step 6: Run** `env --chdir=<wt> node --test viewer/tests/unit/style-rules.test.js`, the unit suite, and `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js shell.mock.spec.js rows.mock.spec.js` — Expected: PASS; `screens/table.css` enforced with 0 violations.
- [ ] **Step 7: Commit** — `git -C <wt> add viewer/js/screens/table.js viewer/css/screens/table.css viewer/tests/table.mock.spec.js viewer/tests/unit/style-rules.test.js` then commit `feat(viewer): the Table in fixed columns that never cut an ID, ID and title sticky with an edge fade, markers in the cells, rows that open as links and keep their place through a redraw, and a ?status= link that opens it filtered`

---

### Task 3: The Table on a phone — stacked cards and a Sort select

**Depends on:** Task 2. **Wave 3.**

**Files:**
- Modify: `viewer/js/screens/table.js`, `viewer/css/screens/table.css`, `viewer/tests/table.mock.spec.js`

**Interfaces:**
- Consumes: Task 2's DOM contract; the shared select markup (`span.ef-select > select.ef-enum-select` + `icon('chevron', { size: 16 })`, as `enum-select.js:57` builds it; `edit-fields.css` styles it).
- Produces: `div.tbl-sortbar` between `.tbl-chips` and `.tbl-frame`: `label.tbl-sortbar__label[for="tbl-sort"]` "Sort" + `span.ef-select > select#tbl-sort.ef-enum-select`. One option per sortable column and direction, value `<key>:<asc|desc>`, text `<Label> — ascending` / `<Label> — descending`, in column order. Its value follows `state.sort` on every paint; a change sets `state.sort = { by, dir }`, paints and persists.
- Behaviour (each line is a test):
  1. At ≤768px: the sort bar shows and the header row, the column widths and the fade do not; at >768px the sort bar is `display: none`.
  2. At ≤768px each row is a card: line 1 ID (left) · priority marker (right); line 2 the title, up to 2 lines; line 3 status marker · size; line 4 epic name. Phase, area, branch and started are not shown on the card. The ID is never cut.
  3. The whole card is its link's hit area (`.tbl-link::after` covers the `position: relative` row); the link keeps the focus ring; a card is at least 44px tall.
  4. The table's inline `min-width` and the title cells' inline `left` have no effect at ≤768px.

- [ ] **Step 1: Write the failing tests** (`table.mock.spec.js`):

```js
test('at 390 with 230 long rows nothing scrolls sideways, no ID is cut and the page stays one screen tall', async ({ page }) => {
  await boot(page, { width: 390, height: 844, board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-row')).toHaveCount(230);
  const look = await page.evaluate(() => {
    const host = document.querySelector('.tbl-host');
    return {
      pageX: document.scrollingElement.scrollWidth - innerWidth,
      pageH: document.scrollingElement.scrollHeight / innerHeight,
      hostX: host.scrollWidth - host.clientWidth,
      hostScrolls: host.scrollHeight > host.clientHeight,
      cutIds: [...document.querySelectorAll('.tbl-cell--id .t-id')].filter((el) => el.getBoundingClientRect().right > el.closest('.tbl-row').getBoundingClientRect().right + 0.5 || el.closest('td').scrollWidth > el.closest('td').clientWidth).length,
      lostWords: [...document.querySelectorAll('.tbl-cell--title .truncate')].filter((el) => el.title !== el.textContent).length,
      head: getComputedStyle(document.querySelector('.tbl thead')).display,
      short: [...document.querySelectorAll('.tbl-row')].filter((r) => r.getBoundingClientRect().height < 44).length,
    };
  });
  const { pageH, ...rest } = look;
  expect(rest).toEqual({ pageX: 0, hostX: 0, hostScrolls: true, cutIds: 0, lostWords: 0, head: 'none', short: 0 });
  expect(pageH).toBeLessThan(2);
});

test('at 390 a card reads ID · priority, title, status · size, epic — and the whole card opens its task', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  const card = page.locator('.tbl-row[data-task-id="T-102"]');
  const box = async (sel) => card.locator(sel).boundingBox();
  const id = await box('.tbl-cell--id'); const pri = await box('.tbl-cell--priority');
  const title = await box('.tbl-cell--title'); const status = await box('.tbl-cell--status'); const epic = await box('.tbl-cell--epic');
  expect(Math.abs(id.y - pri.y)).toBeLessThanOrEqual(2);
  expect(title.y).toBeGreaterThan(id.y);
  expect(status.y).toBeGreaterThan(title.y);
  expect(epic.y).toBeGreaterThan(status.y);
  for (const hidden of ['phase', 'area', 'branch', 'started']) await expect(card.locator(`.tbl-cell--${hidden}`)).toBeHidden();
  await card.click({ position: { x: 4, y: 4 } });
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
});

test('at 390 the Sort select sorts, says the current sort and is saved; at 1440 it is not shown', async ({ page }) => {
  const puts = await boot(page, { width: 390, height: 844 });
  const sort = page.getByLabel('Sort');
  await expect(sort).toHaveValue('priority:asc');
  await sort.selectOption('title:desc');
  const titles = TABLE_BOARD.tasks.map((t) => t.title.toLowerCase()).sort();
  await expect(page.locator('.tbl-row .tbl-cell--title').first()).toHaveText(new RegExp(`^${titles.at(-1)}$`, 'i'));
  await expect.poll(() => puts.filter((b) => b.table?.sort).at(-1)?.table.sort).toEqual({ by: 'title', dir: 'desc' });
  expect(await sort.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.tbl-sortbar')).toBeHidden();
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}, 390): every card passes contrast and nothing is nested`, async ({ page }) => {
    await boot(page, { theme, width: 390, height: 844, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => (await window.axe.run(document.getElementById('screen-mount'),
      { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'label', 'select-name'] }, resultTypes: ['violations'] })).violations);
    expect(v.map((x) => `${x.id}: ${x.nodes.length}`)).toEqual([]);
  });
}
```

- [ ] **Step 2: Run** `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js` — Expected: the four new tests FAIL.
- [ ] **Step 3: Implement** the sort bar in `table.js` (built once at mount with `h()` and `icon()`; `change` → `const [by, dir] = sel.value.split(':'); state.sort = { by, dir }; paint(); persist();`; `paint()` sets `sel.value = \`${state.sort.by}:${state.sort.dir}\``) and append to `table.css`:

```css
.tbl-sortbar { display: none; }
@media (max-width: 768px) {
  .tbl-sortbar { display: flex; align-items: center; gap: var(--space-xs); flex: 0 0 auto; }
  .tbl-sortbar__label {
    font-family: var(--font-technical); font-weight: 800; font-size: var(--size-technical-label);
    letter-spacing: var(--tracking-ultra); text-transform: uppercase; color: var(--foreground-subtle);
  }
  .tbl-sortbar .ef-enum-select { min-height: 44px; }
  .tbl-fade { display: none; }
  /* The measured desktop geometry (inline min-width, title left) does not apply to cards. */
  table.tbl { display: block; min-width: 0 !important; }
  .tbl colgroup, .tbl thead { display: none; }
  .tbl tbody { display: block; }
  .tbl-row {
    position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas: "id priority" "title title" "status size" "epic epic";
    gap: var(--space-micro) var(--space-sm); min-height: 44px; padding: var(--space-sm) var(--space-md);
    border-bottom: 1px solid var(--border-subtle); background: var(--card-bg);
  }
  .tbl-row:hover { background: var(--card-bg-hover); }
  .tbl-row > .tbl-cell, .tbl-row:hover > .tbl-cell { position: static; display: block; min-width: 0; padding: 0; border: 0; background: transparent; }
  .tbl-cell--id { grid-area: id; }
  .tbl-cell--priority { grid-area: priority; justify-self: end; }
  .tbl-cell--title { grid-area: title; white-space: normal; }
  .tbl-cell--title .truncate { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; white-space: normal; }
  .tbl-cell--status { grid-area: status; }
  .tbl-cell--estimate { grid-area: size; justify-self: end; }
  .tbl-cell--epic { grid-area: epic; }
  .tbl-row > :is(.tbl-cell--phase, .tbl-cell--area, .tbl-cell--branch, .tbl-cell--started) { display: none; }
  .tbl-link::after { content: ''; position: absolute; inset: 0; }
  .tbl-empty { display: block; }
}
```

- [ ] **Step 4: Run** style rules, unit suite, `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js shell.mock.spec.js` — Expected: PASS; `screens/table.css` 0 violations.
- [ ] **Step 5: Commit** — stage `viewer/js/screens/table.js viewer/css/screens/table.css viewer/tests/table.mock.spec.js`; message `feat(viewer): on a phone the Table is a list of cards, each one link, with a Sort select in place of the header row`

---

### Task 4: Epics — aligned link rows with the lifecycle marker and an always-drawn progress bar

**Depends on:** Task 1. **Wave 2**, beside Tasks 2 and 5.

**Files:**
- Rewrite: `viewer/js/screens/epics.js`, `viewer/css/screens/epics.css`
- Modify: `viewer/tests/unit/style-rules.test.js` (ENFORCED += `'screens/epics.css'`)
- Create: `viewer/tests/epics.mock.spec.js`

**Interfaces:**
- Consumes: `epicStats`, `epicProgress`, `isCloseable`, `epicStatusMeta` (Task 1); `marker()`; `linkRow()`; `truncate()`; `stateBlock()`; `claimTopbar()`, `tmSearch()`.
- Produces (DOM): `section.epics-screen > ul.epics-list > li.link-row.epic-row[data-epic-id]` built by `linkRow({ tag: 'li', className: 'epic-row', href: '#/epic/<encoded id>', name, content, title })`:
  - `name` = `span.epic-row__name` holding `truncate(ep.name || ep.id, { lines: 2 })` (Task 6 prepends the swatch here);
  - `content` = `span.epic-row__status` (`marker(epicStatusMeta(ep.status))`), `span.epic-row__bar[aria-hidden="true"] > span.epic-row__fill` (`style.width = pct + '%'`), `span.epic-row__count` (`${closed}/${total}`, `title` = `label`), `span.epic-row__tags` (holding `span.epic-tag` "Closeable" when `isCloseable(stats)` and the epic's status is missing, `active` or `planned`; else empty);
  - `title` = the name, plus `\nDone when: <done_when>` when set.
- Behaviour (each line is a test):
  1. The screen never touches the root's `class` or `style`; `#screen-mount` has neither `epics` nor a `style` attribute after mount or after leaving. Rows carry no inline `style` (only the fill does, width only).
  2. Every row shows the lifecycle word ("Active", "Planned", "Done", "Archived"; an unknown status as written; none → "Active"), never the design status.
  3. Every row draws the bar, 0% included; the count is `closed/total` from `epicStats` of the store's tasks with that epic.
  4. The four content columns line up: `.epic-row__status`, `.epic-row__bar` and `.epic-row__count` have the same left edge on every row at 1440 (±0.5px).
  5. Row 2 holds `tmSearch({ placeholder: 'Filter epics…' })`: a case-insensitive substring of name or id keeps that epic. No match → `stateBlock({ label: 'No match', headline: 'No epic matches “<q>”.', action: { label: 'Clear search', onClick } })`; the button clears the search, shows every epic and focuses the search input. No epics at all → `stateBlock({ label: 'Epics', headline: 'No epics yet.', hint: 'An epic groups tasks toward one outcome.' })`.
  6. A store redraw keeps focus on the same epic's link (by `data-epic-id`), else on the row at the same index, never `<body>`.
  7. At ≤768px a row stacks: the name (up to 2 lines) on its own line, then status and count, then the bar across the row; nothing scrolls sideways and the bar ends inside the row.

- [ ] **Step 1: Write the failing spec** `viewer/tests/epics.mock.spec.js`:

```js
// User intent: the Epics list says each epic's real lifecycle and progress — counted from its tasks — in columns that
// line up, opens an epic by mouse or keyboard, survives another writer's change, and reads at phone width in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, LONG_IDS_BOARD, epicPayload } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
// BOARD plus an epic with no tasks, one whose tasks are all closed, one planned and one in a status the map does not know.
const EPICS_BOARD = {
  ...BOARD,
  epics: [...BOARD.epics,
    { id: 'empty', name: 'No tasks yet', status: 'active' },
    { id: 'closed', name: 'All closed', status: 'active', done_when: 'Both tasks are done.' },
    { id: 'later', name: 'Planned work', status: 'planned' },
    { id: 'odd', name: 'Odd status', status: 'paused' },
    { id: 'bare' }],
  tasks: [...BOARD.tasks,
    { id: 'T-301', title: 'Closed one', status: 'done', priority: 'low', epic: 'closed', phase: 'P1', depends_on: [] },
    { id: 'T-302', title: 'Closed two', status: 'archived', priority: 'low', epic: 'closed', phase: 'P1', depends_on: [] }],
};

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function boot(page, { theme = 'dark', width = 1440, height = 900, board = EPICS_BOARD } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': board, '/api/backlog': board, '/api/bugs': [],
    '/api/epic/viewer': epicPayload(board, 'viewer'),
  });
  await page.goto('/#/epics');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('.epic-row').first()).toBeVisible();
}
const row = (page, id) => page.locator(`.epic-row[data-epic-id="${id}"]`);

test('each epic is one link row with its lifecycle word, a bar and closed/total', async ({ page }) => {
  await boot(page);
  await expect(page.locator('.epic-row')).toHaveCount(EPICS_BOARD.epics.length);
  await expect(row(page, 'viewer').locator('a.link-row__link')).toHaveAttribute('href', '#/epic/viewer');
  await expect(row(page, 'viewer').getByRole('link')).toHaveAccessibleName('Viewer re-skin');
  await expect(row(page, 'bare').getByRole('link')).toHaveAccessibleName('bare');
  const word = (id) => row(page, id).locator('.epic-row__status .marker__word');
  await expect(word('viewer')).toHaveText('Active');
  await expect(word('later')).toHaveText('Planned');
  await expect(word('odd')).toHaveText('paused');
  await expect(word('bare')).toHaveText('Active');
  await expect(page.getByText('Exploring')).toHaveCount(0);
  await expect(row(page, 'viewer').locator('.epic-row__count')).toHaveText('1/4');
  await expect(row(page, 'viewer').locator('.epic-row__count')).toHaveAttribute('title', '1/4 closed · 1 done');
  await expect(row(page, 'empty').locator('.epic-row__count')).toHaveText('0/0');
  await expect(row(page, 'empty').locator('.epic-row__bar')).toBeVisible();
  await expect(row(page, 'empty').locator('.epic-row__fill')).toHaveAttribute('style', 'width: 0%;');
  await expect(row(page, 'closed').locator('.epic-tag')).toHaveText('Closeable');
  await expect(row(page, 'empty').locator('.epic-tag')).toHaveCount(0);
  await expect(row(page, 'closed').getByRole('link')).toHaveAttribute('title', 'All closed\nDone when: Both tasks are done.');
});

test('the root keeps no class or style, and rows carry no colour', async ({ page }) => {
  await boot(page);
  expect(await page.locator('#screen-mount').evaluate((el) => [el.className, el.getAttribute('style')])).toEqual(['', null]);
  await expect(page.locator('.epic-row[style], .epic-row [style]:not(.epic-row__fill)')).toHaveCount(0);
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  expect(await page.locator('#screen-mount').evaluate((el) => [el.className, el.getAttribute('style')])).toEqual(['', null]);
});

test('the columns line up on every row', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  for (const cls of ['epic-row__status', 'epic-row__bar', 'epic-row__count']) {
    const lefts = await page.locator(`.${cls}`).evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().left)));
    expect(new Set(lefts).size, cls).toBe(1);
  }
});

test('search filters by name or id; no match says so and Clear search brings everything back', async ({ page }) => {
  await boot(page);
  const search = page.getByPlaceholder('Filter epics…');
  await search.fill('STORE');
  await expect(page.locator('.epic-row')).toHaveCount(1);
  await search.fill('zzz');
  await expect(page.locator('.tm-empty__headline')).toHaveText('No epic matches “zzz”.');
  await page.getByRole('button', { name: 'Clear search' }).click();
  await expect(page.locator('.epic-row')).toHaveCount(EPICS_BOARD.epics.length);
  await expect(search).toBeFocused();
});

test('no epics is a state block', async ({ page }) => {
  const none = { ...BOARD, epics: [], tasks: [] };
  await mockApi(page, { '/api/board': none, '/api/backlog': none, '/api/bugs': [] });
  await page.goto('/#/epics');
  await expect(page.locator('.tm-empty__headline')).toHaveText('No epics yet.');
});

test('a keyboard user opens an epic and comes back to it', async ({ page }) => {
  await boot(page);
  const first = row(page, 'viewer').getByRole('link');
  await first.focus();
  // Rows are one tab stop each: the next Tab is the next epic.
  await page.keyboard.press('Tab');
  await expect(row(page, 'store').getByRole('link')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(first).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Viewer re-skin' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(first).toBeFocused();
});

test('a redraw keeps focus on the same epic', async ({ page }) => {
  await boot(page);
  const link = row(page, 'store').getByRole('link');
  await link.focus();
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.epics.find((e) => e.id === 'store').name = 'Native store, renamed';
    next.revision = 'r-2';
    store.setBoard(next);
  }));
  await expect(link).toHaveAccessibleName('Native store, renamed');
  await expect(link).toBeFocused();
});

test('at 390 a row stacks and nothing scrolls sideways', async ({ page }) => {
  await boot(page, { width: 390, height: 844, board: LONG_IDS_BOARD });
  const look = await page.evaluate(() => ({
    pageX: document.scrollingElement.scrollWidth - innerWidth,
    spill: [...document.querySelectorAll('.epic-row')].filter((r) => {
      const b = r.querySelector('.epic-row__bar').getBoundingClientRect();
      return b.right > r.getBoundingClientRect().right + 0.5 || b.width < 40;
    }).length,
    lostWords: [...document.querySelectorAll('.epic-row__name .truncate')].filter((el) => el.title !== el.textContent).length,
  }));
  expect(look).toEqual({ pageX: 0, spill: 0, lostWords: 0 });
  const r = row(page, 'database-native-tracking');
  const name = await r.locator('.epic-row__name').boundingBox();
  const status = await r.locator('.epic-row__status').boundingBox();
  const bar = await r.locator('.epic-row__bar').boundingBox();
  expect(status.y).toBeGreaterThanOrEqual(name.y + name.height - 1);
  expect(bar.y).toBeGreaterThan(status.y);
});

for (const theme of ['dark', 'light']) for (const [w, h] of [[1440, 900], [390, 844]]) {
  test(`axe (${theme}, ${w}): Epics passes contrast, nesting and aria`, async ({ page }) => {
    await boot(page, { theme, width: w, height: h, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      return (await window.axe.run(document.getElementById('screen-mount'),
        { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'list', 'listitem', ...aria] }, resultTypes: ['violations'] })).violations;
    });
    expect(v.map((x) => `${x.id}: ${x.nodes.length}`)).toEqual([]);
  });
}
```

  In "no epics is a state block", `boot` waits for `.epic-row`; call `page.goto` directly instead of `boot` there (mock as in `boot`, then `await expect(page.locator('.tm-empty__headline')).toHaveText('No epics yet.')`) — do not swallow errors with `.catch`.

- [ ] **Step 2: Run** `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 epics.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement `epics.js`** (header: `// User intent: the Epics list — every epic as one link row with its real lifecycle and its progress counted from its tasks, in columns that line up, filterable from the topbar.`). `mount(root, { store })`: `const screen = h('section', { class: 'epics-screen' }); root.replaceChildren(screen);` `const row2 = claimTopbar(); const search = tmSearch({ placeholder: 'Filter epics…', onInput: (v) => { q = v; render(); } }); row2?.append(search.el);` `render()` snapshots focus (`document.activeElement.closest?.('.epic-row')` → `data-epic-id` and index), builds the list or the state block per line 5, replaces `screen`'s children, restores focus per line 6. Count each epic with `epicStats(tasks.filter((t) => t.epic === ep.id))`. Cleanup: unsubscribe and `screen.remove()`. No `innerHTML`, no `assignEpicColors`/`epicCssVar`/`closeableBadge` import.
- [ ] **Step 4: Rewrite `epics.css`** (tokens only):

```css
/* User intent: epic rows on the shared card surface, their status, bar and count in columns that line up, stacked on a phone. */
.epics-screen { display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; }
.epics-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-xs); max-width: 1100px; }
.epic-row {
  display: grid; align-items: center; column-gap: var(--space-md);
  grid-template-columns: minmax(0, 1fr) 9rem minmax(6rem, 14rem) 4.5rem 6.5rem;
  grid-template-areas: "name status bar count tags";
  padding: var(--space-sm) var(--space-md);
  background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg);
}
.epic-row:hover { border-color: var(--border-strong); }
.epic-row > .link-row__content { display: contents; }
.epic-row > .link-row__link {
  grid-area: name; min-width: 0; color: var(--foreground-bold); text-decoration: none;
  font-family: var(--font-narrator); font-weight: var(--font-narrator-weight-semi); font-size: var(--size-narrator-small);
}
.epic-row__name { display: flex; align-items: center; gap: var(--space-xs); min-width: 0; }
.epic-row__status { grid-area: status; }
.epic-row__bar { grid-area: bar; display: block; height: var(--space-micro); border-radius: var(--radius-full); background: var(--bg-recessed); border: 1px solid var(--border-subtle); overflow: hidden; }
.epic-row__fill { display: block; height: 100%; background: var(--signature-fill); }
.epic-row__count { grid-area: count; text-align: right; font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); color: var(--foreground-default); }
.epic-row__tags { grid-area: tags; display: flex; justify-content: flex-end; }
.epic-tag {
  padding: 0 var(--space-xs); border: 1px solid var(--border-default); border-radius: var(--radius-sm);
  font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); color: var(--foreground-default); white-space: nowrap;
}
@media (max-width: 768px) {
  .epic-row {
    grid-template-columns: minmax(0, 1fr) auto; row-gap: var(--space-xs);
    grid-template-areas: "name name" "status count" "bar bar" "tags tags";
  }
  .epic-row__tags { justify-content: flex-start; }
  .epic-row__tags:empty { display: none; }
}
```

  Add `'screens/epics.css'` to `ENFORCED`.
- [ ] **Step 5: Run** style rules, unit suite, `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 epics.mock.spec.js shell.mock.spec.js` — Expected: PASS.
- [ ] **Step 6: Commit** — stage `viewer/js/screens/epics.js viewer/css/screens/epics.css viewer/tests/epics.mock.spec.js viewer/tests/unit/style-rules.test.js`; message `feat(viewer): Epics as aligned link rows — the real lifecycle word, a bar on every row, progress counted from the epic's tasks, a search, and a stacked row on a phone`

---

### Task 5: Epic detail — the detail header, a status breakdown, the epic's tasks grouped by status

**Depends on:** Task 1. **Wave 2**, beside Tasks 2 and 4.

**Files:**
- Modify: `viewer/js/components/epic-detail-document.js`, `viewer/js/screens/epic-detail.js`, `viewer/js/components/component-diagram.js` (block summary words, empty text), `viewer/tests/unit/component-diagram.test.js`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `'screens/epic-detail.css'`)
- Rewrite: `viewer/css/screens/epic-detail.css`
- Create: `viewer/tests/epic-detail.mock.spec.js`

**Interfaces:**
- Consumes: `epicStats`, `epicProgress`, `epicBreakdown`, `isCloseable`, `epicStatusMeta`, `STATUS_GROUPS`, `designBadge` (Task 1 / existing); `marker`, `statusMarker`, `statusMeta`, `priorityMarker`; `linkRow`; `truncate`; `stateBlock`; `mountMarkdown`; `mountComponentDiagram`.
- Produces: `mountEpicDetail(container, { epic, store, onNavigate, onComponentNav, chrome = 'page' }) → dispose` keeps its signature. `onNavigate` is accepted and no longer called (every task link is a real `href`; the page interceptor and the modal's own link handler route them). It never writes `style` to `container`; it adds `ed-root` and removes it on dispose (as today). DOM:
  ```
  header.ed-head
    div.ed-meta            (page only) span.ed-id <id> · a[href="#/epics"] "Epics" · span <phase> (when set)
    h1.ed-title            (page only)
    div.ed-markers         marker(epicStatusMeta(status)) · span.ed-tag "Design · <designBadge(design_status).label>"
                           · span.ed-tag "Closeable" (isCloseable, status missing/active/planned) · span.ed-tag <area> (when set)
    p.ed-done-when         span.ed-label "Done when" + text (when set)
  div.ed-grid > div.ed-main + aside.ed-side
    ed-main: section.ed-narrative "Design" (when text) → section.ed-progress "Progress" → section.ed-tasks "Tasks"
             → section.ed-diagram "Architecture" (when components)
      ed-progress: p.ed-progress__label (epicProgress label)
                   div.ed-breakdown[aria-hidden="true"] > span.ed-seg.ed-seg--<status>[title="<Label>: <n>"] (style.width pct%)
                   ul.ed-legend > li > marker (statusMeta('task', s), 'other' → { label: 'Other', shape: '○', tone: 'neutral' }) + span.ed-legend__n <n>
                   — total 0: stateBlock({ label: 'Tasks', headline: 'No tasks in this epic yet.' }) instead of label, bar and legend
      ed-tasks:    one details.ed-group[data-status] per non-empty group in STATUS_GROUPS order (then 'other'),
                   `open` except done and archived;
                   summary.ed-group__head = marker(...) + span.ed-group__n <n>
                   ul.ed-task-list > li.link-row.ed-task  linkRow({ tag: 'li', className: 'ed-task', href: '#/task/<id>',
                       name: span.ed-task__name > span.t-id <id> + ' ' + truncate(title || id, { lines: 2 }),
                       content: [priorityMarker(p)] when priority })
    ed-side: section "Attention" (each: marker {Blocked ◆ critical | Has blockers ▲ warning} + a[href="#/task/<id>"] + truncate(why))
             section "Docs" (a[href="/file/<path>"][target=_blank][rel=noopener] <key> + span.t-tech <path>)
  ```
  Figures come from `epicStats(epic.tasks)` — the list the page draws — so the label, the breakdown and the list always agree.
- `epic-detail.js`: builds `div.ed-page` inside `root` (never decorates `root`); shows `stateBlock({ state: 'loading', headline: 'Loading…', busy: true })` while `getEpic(id)` runs; afterwards, **if `page` is no longer connected it returns a no-op cleanup and paints nothing** (the router replaced the mount); a response without an `id`, or an error with `code === 404` → `stateBlock({ state: 'missing', label: 'Not found', headline: \`There is no epic called ${id}.\`, action: { label: 'All epics', href: '#/epics' } })`; any other error → `stateBlock({ state: 'error', label: 'Error', headline: 'This epic could not be loaded.', hint: 'The server did not answer. Try again in a moment.', action: { label: 'Try again', onClick: load } })`; no id → `stateBlock({ label: 'Epics', headline: 'No epic selected.', action: { label: 'All epics', href: '#/epics' } })`. Cleanup disposes the document and removes `page`.
- `component-diagram.js`: block summary reads `statusMeta('task', status).label` (+ ` · <n> blocked`), e.g. "In progress · 2 blocked", "Todo"; the empty block reads "No tasks yet".
- Behaviour: each bullet under Step 1 is a test.

- [ ] **Step 1: Write the failing tests.** Unit (`component-diagram.test.js`): change `/no tasks yet/i` to `/^No tasks yet$/` on `.cd-block__empty` text and `/in-progress · 2 blocked/` to `/^In progress · 2 blocked$/`. Mocked (`viewer/tests/epic-detail.mock.spec.js`, header *"User intent: Epic detail reads like every detail page — its real status, progress from one function, its tasks grouped as links — by mouse, keyboard and phone, in both themes, and leaving it early leaves nothing behind."*; copy `ARCH_EPIC_FIXTURE` verbatim from `viewer/tests/epic-detail.spec.js:6-33`; boot as Task 4's with `'/api/epic/viewer': epicPayload(BOARD, 'viewer', { design_status: 'locked', description: 'Every screen takes the **Reality Reprojection** system.', done_when: 'All screens pass the audit.', docs: { spec: 'docs/specs/viewer.md' }, attention: [{ id: 'T-102', title: 'Re-skin the Kanban cards and columns', blocked: false, why: 'critical and in progress' }] })`, `'/api/task/T-102/detail': taskDetail(DETAIL_TASK)`, `'/api/epic/arch-test': ARCH_EPIC_FIXTURE`, `'/api/epic/nope': { status: 404, json: { ok: false, error: 'epic not found' } }`, `'/api/epic/broken': { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } }`, `'/api/epic/big': epicPayload({ ...LONG_IDS_BOARD, epics: [{ id: 'big', name: 'Big epic', status: 'active' }], tasks: LONG_IDS_BOARD.tasks.slice(0, 60).map((t) => ({ ...t, epic: 'big' })) }, 'big')`):
  - "the header reads id · Epics, the name, the lifecycle marker and the design tag — no back crumb, no Exploring beside active": on `#/epic/viewer`, `.ed-meta` text matches `/^viewer\s*·?\s*Epics/`, its link `href` `#/epics`; `h1.ed-title` "Viewer re-skin"; `.ed-markers .marker__word` first is "Active"; an `.ed-tag` reads "Design · Locked"; no `.ed-back`, no text "‹", no "🔒", and `getByText('Exploring')` count 0; `#screen-mount` has no `style` attribute and no class.
  - "progress says closed/total and the breakdown and legend agree with the task list": `.ed-progress__label` "1/4 closed · 1 done"; `.ed-seg` count 3 (in-progress, todo, done) with widths summing to the bar's width (±2px); legend words "In progress", "Todo", "Done" with counts 2, 1, 1; `.ed-group` statuses in order `['in-progress', 'todo', 'done']`; `.ed-group[data-status="done"]` has no `open`; the in-progress group lists T-102 and T-103 as links named `T-102 Re-skin the Kanban cards and columns` etc.
  - "a task row opens its task in the modal on the page, and peeks it inside the epic modal": on the page, click T-102's row (on its priority marker) → dialog named "Re-skin the Kanban cards and columns"; Escape; focus is on T-102's row link. Then from `#/epics` (Task 4 may not be merged: navigate with `page.evaluate(() => import('/js/lib/open-detail.js').then((m) => m.openDetail('epic', 'viewer')))`), in the epic dialog click T-102's row → the same dialog now named "Re-skin the Kanban cards and columns"; `.modal` count 1.
  - "every task row and group toggle is reached by Tab": focus the "Epics" link in `.ed-meta`, then press Tab repeatedly (bounded at 40) collecting `document.activeElement` descriptors; the sequence includes each open group's summary followed by its task links in order, and the closed Done summary; pressing Enter on the Done summary opens it and its task links become reachable.
  - "an epic with no tasks says so instead of an empty bar": `'/api/epic/empty'` = `epicPayload(BOARD, 'empty', { id: 'empty', name: 'Empty' })`; `.ed-progress .tm-empty__headline` "No tasks in this epic yet."; no `.ed-breakdown`, no `.ed-group`.
  - "not found, a failure and Try again are state blocks with one action": `#/epic/nope` → `.tm-empty[data-state="missing"]` exists, its `.tm-empty__label` "Not found", headline "There is no epic called nope.", link "All epics" to `#/epics`, `#page-title` "Epic"; `#/epic/broken` → `.tm-empty[data-state="error"]`, headline "This epic could not be loaded.", page text contains neither `sqlite3` nor `500`; re-route `/api/epic/broken` to `epicPayload(BOARD, 'viewer', { id: 'broken', name: 'Recovered' })` and click "Try again" → `h1.ed-title` "Recovered".
  - **"leaving while the epic is still loading leaves nothing behind"** (Review Focus 5): record page errors; count live `ResizeObserver`s as `table.mock.spec.js` "leaving the Table…" does (same `addInitScript`); route `**/api/epic/arch-test` to fulfil `ARCH_EPIC_FIXTURE` after a 600 ms delay; go to `#/epic/arch-test`, expect the loading block, then `location.hash = '#/table'`; wait for `table.tbl`; wait 1000 ms; expect no `.ed-root`, no `.ed-page`, no `.cd-map` in the document; `#screen-mount` className `''` and no `style`; no live observer whose targets include `.ed-diagram__canvas` or `.cd-map`; no page error.
  - The five architecture-map checks of `epic-detail.spec.js:84-115`, ported unchanged against `#/epic/arch-test` (svg present, 4 blocks, 2 edge paths, unassigned block, diagram before the side column).
  - "Attention and Docs are real links in words, not hue": `.ed-side` has a link to `#/task/T-102` and a `Docs` link to `/file/docs/specs/viewer.md` with `target="_blank"`; computed `color` of those links equals computed `color` of `.ed-side` body text's `--foreground-default` (read via a probe span with `color: var(--foreground-default)`).
  - "at 390 the page is one column and nothing scrolls sideways": `#/epic/big` at 390×844 — `document.scrollingElement.scrollWidth <= innerWidth`; `.ed-side` top ≥ `.ed-main` bottom − 1; every `.ed-task .truncate` keeps its words (`title === textContent`).
  - axe, both themes × (1440, 390), on the page (`#screen-mount`) and in the epic modal (`[role="dialog"]`, opened with `openDetail('epic', 'viewer')`): zero `color-contrast`, `nested-interactive`, `list`, `listitem`, `aria-*`; computed `box-shadow` of `.cd-block` is `none`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/component-diagram.test.js` and `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 epic-detail.mock.spec.js` — Expected: FAIL.
- [ ] **Step 3: Implement** `epic-detail-document.js` per the DOM contract with `h()` (no `innerHTML`; the design text still goes through `mountMarkdown`), `epic-detail.js` per its contract (the `page.isConnected` check right after `await getEpic(id)` and after a "Try again" load), and the two strings in `component-diagram.js`. Remove the `assignEpicColors`/`epicCssVar`/`closeableBadge` imports and the `⏸ `/`⚠ ` text glyphs.
- [ ] **Step 4: Rewrite `epic-detail.css`** (tokens only; `.cd-*` rules restyled, not dropped):

```css
/* User intent: Epic detail as a detail page — quiet Technical meta, a Narrator title, markers, a breakdown bar, task
   groups as link rows and the architecture map on bordered card surfaces — in both themes and on a phone. */
.ed-page { min-width: 0; }
.ed-root { max-width: 1100px; min-width: 0; }
.ed-head { display: flex; flex-direction: column; gap: var(--space-xs); padding-bottom: var(--space-md); margin-bottom: var(--space-lg); border-bottom: 1px solid var(--border-default); }
.ed-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-micro) var(--space-xs); color: var(--foreground-subtle);
  font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); }
.ed-meta a { color: var(--foreground-default); text-decoration: underline; text-decoration-color: var(--border-strong); text-underline-offset: 3px; }
.ed-meta a:hover { color: var(--foreground-bold); text-decoration-color: currentColor; }
.ed-id { color: var(--foreground-default); }
.ed-title { margin: 0; max-width: 80ch; color: var(--foreground-bold); font-family: var(--font-narrator); font-weight: 700;
  font-size: var(--size-narrator-large); line-height: var(--leading-heading); overflow-wrap: anywhere; }
.ed-markers { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); }
.ed-tag { padding: 0 var(--space-xs); border: 1px solid var(--border-default); border-radius: var(--radius-sm); color: var(--foreground-default);
  font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); white-space: nowrap; }
.ed-done-when { margin: 0; max-width: 80ch; color: var(--foreground-default); font-family: var(--font-narrator); font-size: var(--size-narrator-small); }
.ed-label, .ed-h {
  font-family: var(--font-technical); font-weight: 800; font-size: var(--size-technical-label);
  letter-spacing: var(--tracking-ultra); text-transform: uppercase; color: var(--foreground-subtle);
}
.ed-label { margin-right: var(--space-xs); }
.ed-h { margin: 0 0 var(--space-xs); }
.ed-grid { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: var(--space-lg); }
.ed-main { min-width: 0; display: flex; flex-direction: column; gap: var(--space-lg); }
.ed-md { color: var(--foreground-default); }

.ed-progress__label { margin: 0 0 var(--space-xs); color: var(--foreground-default); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); }
.ed-breakdown { display: flex; height: var(--space-xs); max-width: 480px; border-radius: var(--radius-full); overflow: hidden; background: var(--bg-recessed); border: 1px solid var(--border-subtle); }
.ed-seg { display: block; height: 100%; background: var(--tone-neutral); }
.ed-seg--in-progress { background: var(--tone-accent); }
.ed-seg--in-review { background: var(--tone-warning); }
.ed-seg--blocked { background: var(--tone-critical); }
.ed-seg--done { background: var(--tone-success); }
.ed-legend { list-style: none; margin: var(--space-xs) 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-micro) var(--space-md); }
.ed-legend li { display: inline-flex; align-items: center; gap: var(--space-micro); }
.ed-legend__n, .ed-group__n { color: var(--foreground-subtle); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); }

.ed-group + .ed-group { margin-top: var(--space-sm); }
.ed-group__head { display: flex; align-items: center; gap: var(--space-xs); min-height: 32px; cursor: pointer; list-style: none; }
.ed-group__head::-webkit-details-marker { display: none; }
.ed-task-list { list-style: none; margin: var(--space-micro) 0 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-micro); }
.ed-task { display: flex; align-items: center; gap: var(--space-sm); padding: var(--space-xs) var(--space-sm);
  background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); }
.ed-task:hover { border-color: var(--border-strong); }
.ed-task > .link-row__link { flex: 1 1 auto; min-width: 0; color: var(--foreground-bold); text-decoration: none;
  font-family: var(--font-narrator); font-weight: var(--font-narrator-weight-semi); font-size: var(--size-narrator-small); }
.ed-task__name { display: flex; align-items: baseline; gap: var(--space-xs); min-width: 0; }
.ed-task .t-id { flex: 0 0 auto; font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); color: var(--foreground-default); }
.ed-task > .link-row__content { flex: 0 0 auto; }

.ed-side { display: flex; flex-direction: column; gap: var(--space-lg); min-width: 0; }
.ed-attn, .ed-docs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-xs);
  color: var(--foreground-default); font-family: var(--font-narrator); font-size: var(--size-narrator-small); }
.ed-attn a, .ed-docs a { color: var(--foreground-default); text-decoration: underline; text-decoration-color: var(--border-strong); text-underline-offset: 3px; }
.ed-attn a:hover, .ed-docs a:hover { color: var(--foreground-bold); text-decoration-color: currentColor; }

/* Architecture map */
.ed-diagram__canvas { position: relative; width: 100%; overflow-x: auto; box-sizing: border-box; padding: var(--space-sm);
  background: var(--bg-recessed); border: 1px solid var(--border-default); border-radius: var(--radius-lg); }
.cd-map { position: relative; display: flex; align-items: flex-start; gap: var(--space-2xl); min-height: 40px; }
.cd-connectors { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; z-index: 0; }
.cd-edge { fill: none; stroke: var(--border-strong); stroke-width: 1.2; }
.cd-rank { position: relative; z-index: 1; display: flex; flex-direction: column; gap: var(--space-lg); min-width: 200px; }
.cd-block { padding: var(--space-sm); background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); }
.cd-block[role="button"] { cursor: pointer; }
.cd-block[role="button"]:hover { border-color: var(--border-strong); }
.cd-block__head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-xs); margin-bottom: var(--space-xs); }
.cd-block__title { color: var(--foreground-bold); font-family: var(--font-narrator); font-weight: var(--font-narrator-weight-semi); font-size: var(--size-narrator-small); }
.cd-block__summary { color: var(--foreground-subtle); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); white-space: nowrap; }
.cd-block__cards { display: flex; flex-direction: column; gap: var(--space-micro); }
.cd-block__empty { color: var(--foreground-subtle); font-family: var(--font-narrator); font-size: var(--size-narrator-small); padding: var(--space-micro) 0; }
.cd-block--done { border-color: var(--tone-success); }
.cd-block--progress { border-color: var(--tone-accent); }
.cd-block--attention { border-color: var(--tone-critical); border-top-width: 2px; }
.cd-block--unassigned { border-style: dashed; }

@media (max-width: 768px) {
  .ed-grid { grid-template-columns: minmax(0, 1fr); }
  .ed-group__head { min-height: 44px; }
  .ed-task { min-height: 44px; }
}
```

  Add `'screens/epic-detail.css'` to `ENFORCED`.
- [ ] **Step 5: Run** style rules, the unit suite, and `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 epic-detail.mock.spec.js task-detail.mock.spec.js` — Expected: PASS.
- [ ] **Step 6: Commit** — stage the files named above; message `feat(viewer): Epic detail reads like a detail page — its lifecycle marker, progress and a status breakdown from one function, its tasks grouped as link rows — and leaving it early paints nothing over the next screen`

---

### Task 6: Epic swatches on the categorical palette

**Depends on:** Tasks 2, 4, 5, and **3a Task 1 ("Epic swatches on the categorical tokens, and the long-data fixture") merged into the integration branch** (rebase `rr3/b-table-epics` onto it first). **Wave 4.**

**Files:**
- Modify: `viewer/js/screens/epics.js`, `viewer/js/components/epic-detail-document.js`, `viewer/js/screens/table.js`, `viewer/css/screens/epics.css`, `viewer/css/screens/epic-detail.css`, `viewer/css/screens/table.css`, `viewer/js/lib/epic-format.js` (delete `closeableBadge`), `viewer/tests/unit/epic-closeable.test.js`, `viewer/tests/epics.mock.spec.js`, `viewer/tests/epic-detail.mock.spec.js`, `viewer/tests/table.mock.spec.js`

**Interfaces:**
- Consumes (from 3a Task 1, `viewer/js/lib/epics.js`): `epicIndex(epics) → Map<string, { name: string, swatch: 1|2|3|4|5|6 }>` (one entry per epic with an id, in order; `name` = the epic's name when a non-empty string, else its id; an epic's own `color` field is ignored) and `epicSwatch(epicId, epics) → 1…6 | null` (unchanged); the `--cat-1…6` tokens. 3b does not use 3a's `assignEpicColors`/`epicColor`/`epicCssVar` (they now carry swatch numbers and an inline `--epic`); 3b draws the swatch with a class.
- Produces: one helper per screen file that needs it (no new shared file):
  ```js
  const swatchEl = (n) => (n ? h('span', { class: `epic-swatch epic-swatch--cat-${n}`, 'aria-hidden': 'true' }) : null);
  ```
  placed before the epic's name in: each Epics row's `.epic-row__name` (`epicIndex(epics).get(ep.id).swatch`); Epic detail's `.ed-markers` as its first child (`epicSwatch(epic.id, store.getBacklog()?.epics ?? [])`, no swatch when the store has no board); each Table epic cell, where the cell becomes `span.t-epic-cell` holding the swatch and `truncate(name)` and the name now comes from `epicIndex` (replacing Task 2's own name lookup). The same rules go in each of `epics.css`, `epic-detail.css` and `table.css`:
  ```css
  .epic-swatch { flex: 0 0 auto; display: inline-block; width: 8px; height: 8px; border-radius: var(--radius-xs); }
  .epic-swatch--cat-1 { background: var(--cat-1); }
  .epic-swatch--cat-2 { background: var(--cat-2); }
  .epic-swatch--cat-3 { background: var(--cat-3); }
  .epic-swatch--cat-4 { background: var(--cat-4); }
  .epic-swatch--cat-5 { background: var(--cat-5); }
  .epic-swatch--cat-6 { background: var(--cat-6); }
  ```
  and in `table.css`: `.t-epic-cell { display: flex; align-items: center; gap: var(--space-xs); min-width: 0; }`. The swatch is never the only carrier of the epic: the name is always beside it.
- Behaviour (each line is a test):
  1. An Epics row, the Epic detail marker row and a Table epic cell each hold exactly one swatch for a known epic, whose computed `background-color` equals that of a probe with `background: var(--cat-<epicSwatch>)`; the 7th epic of `LONG_IDS_BOARD` takes the same swatch as the 1st; an epic id the board does not list gets no swatch and still shows its id.
  2. `grep -n "assignEpicColors\|epicCssVar\|epicColor\|closeableBadge" viewer/js/screens/epics.js viewer/js/screens/table.js viewer/js/components/epic-detail-document.js viewer/js/lib/epic-format.js` finds nothing; `epic-closeable.test.js` tests `isCloseable` instead of `closeableBadge` (same three cases).
  3. axe in light at 1440 on `#/epics`, `#/epic/viewer` and `#/table` with `LONG_IDS_BOARD`: zero `color-contrast` (the swatch carries no text).

- [ ] **Step 1: Write the failing tests** — line 1 in each of the three mocked specs (one test each, named "the epic swatch is its categorical colour"), line 3 added to each spec's existing light axe test by booting with `LONG_IDS_BOARD`; rewrite `epic-closeable.test.js` per line 2.
- [ ] **Step 2: Run** the three specs and `node --test viewer/tests/unit/epic-closeable.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement**; delete `closeableBadge` from `epic-format.js`.
- [ ] **Step 4: Run** the grep from line 2 (no match), style rules, unit suite, `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 epics.mock.spec.js epic-detail.mock.spec.js table.mock.spec.js` — Expected: PASS.
- [ ] **Step 5: Commit** — stage the files named above; message `feat(viewer): epics carry their categorical swatch beside the name on the Epics list, Epic detail and the Table`

---

### Task 7: The Table's primary action and the Table and Epics counts in topbar row 1

**Depends on:** Tasks 2 and 4, and **3a Task 2 ("Topbar row 1 fits at phone width") merged into the integration branch** (rebase first). **Wave 4.**

**Files:**
- Modify: `viewer/js/screens/table.js`, `viewer/js/screens/epics.js`, `viewer/tests/table.mock.spec.js`, `viewer/tests/epics.mock.spec.js`, `viewer/tests/shell.mock.spec.js` (only the tests named in Step 1)

**Interfaces:**
- Consumes (from 3a Task 2, `viewer/js/lib/topbar.js`): `setTopbarCount(text = '')` — sets `#topbar-count`'s text and `title` to the same string ('' empties both); `claimTopbarPrimary() → HTMLElement | null` — empties `#topbar-primary` and returns it; `claimTopbar()` clears both on every route. At ≤768px 3a's row-1 rules show a primary's icon only, 44×44, with its `aria-label` as its name.
- Produces: Table — at mount `claimTopbarPrimary()?.append(tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task', onClick }))`; on every paint `setTopbarCount(\`${n} ${pluralize(n, 'task', 'tasks')} · ${shown} visible\`)` (the format 3a's Kanban uses); `tmSubcount` is no longer used; row 2 holds the search only. Epics — on every render `setTopbarCount(\`${n} ${pluralize(n, 'epic', 'epics')} · ${shown} visible\`)`.
- Behaviour (each line is a test):
  1. On `#/table` at 1440 and at 390: "Add task" is inside `#topbar-primary`, visible, `btn btn--primary`, opens the "Create task" dialog (at 390 it is at least 44×44); `#topbar-count` reads "18 tasks · 18 visible" for `TABLE_BOARD`, and "18 tasks · 3 visible" after pressing the Status chip "Done" (`TABLE_BOARD` has three done tasks: T-101, T-202, T-207); `#topbar-actions` holds `.tm-search` and the Filters button only; at 390 `document.scrollingElement.scrollWidth <= innerWidth`.
  2. On `#/epics` with `EPICS_BOARD`: `#topbar-count` reads "7 epics · 7 visible", then "7 epics · 1 visible" after typing "store"; `#topbar-primary` is empty.
  3. Leaving `#/table` for `#/epics` leaves no "Add task" in `#topbar-primary` and replaces the count.

- [ ] **Step 1: Update `shell.mock.spec.js`** — only these tests, which assumed the Table kept its primary and count in row 2:
  - "on the Table the search and Filters stay; the rest is in the Filters popover and works there" → rename "on the Table at 390 the primary is in row 1 and row 2 is the search alone"; assert `#topbar-primary [aria-label="Add task"]` visible and opens "Create task"; `#topbar-actions > .tm-search` visible; Filters hidden; `rowFits` `{ overflow: 0, cut: [] }`.
  - "leaving a screen with its controls parked behind Filters leaves nothing behind" (2b Review Focus 5) → keep its name and purpose but make it independent of any screen's own controls: on `#/table` at 390, `page.evaluate` appends three `button.probe-park` (`style="flex-shrink: 0; width: 160px"`, text "Probe 1…3") to `#topbar-actions`; expect Filters visible; open it; `location.hash = '#/kanban'`; expect no `.probe-park` and no `.popover` in the document, no element with placeholder "Filter… (prefix ! to exclude)", the Kanban search visible in row 2, `rowFits` fits, no page error. Drop its last two assertions about Kanban's parked "Add task" and `.tm-subcount` (Kanban's row 1/row 2 split is 3a's).
  - "at desktop width the Table parks nothing and Filters is hidden" → `#topbar-primary [aria-label="Add task"]` visible, `#topbar-count` not empty, Filters hidden with count "0", `rowFits` fits.
  - "a topbar control that grows in place is laid out again" → on `#/table` at 1440, append `span.tm-subcount` "x" to `#topbar-actions` via `page.evaluate`, wait for Filters hidden, then grow that span's text as today.
  Then add Task 7's line 1–3 tests to `table.mock.spec.js` and `epics.mock.spec.js`.
- [ ] **Step 2: Run** `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js epics.mock.spec.js shell.mock.spec.js` — Expected: the new and rewritten tests FAIL.
- [ ] **Step 3: Implement** in `table.js` and `epics.js`.
- [ ] **Step 4: Run** the full mocked suite `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2` — Expected: PASS (every screen builds its topbar; a failure in another track's spec that existed before this task is reported, not fixed).
- [ ] **Step 5: Commit** — stage the five files; message `feat(viewer): the Table's Add task and the Table and Epics counts live in topbar row 1; row 2 is the search`

---

### Task 8: Verification

**Depends on:** Tasks 1–7.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs` (append-only: scenes and fixtures; still mocked, static-served, never a live server), `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11, append-only), `viewer/tests/mock-fixtures.js` (append-only), `viewer/tests/{table,epics,epic-detail}.mock.spec.js` (their `boot()` mock tables)

- [ ] **Step 1: Scenes.** The fixtures are already imported as `F`. Add to `TABLE`: `'/api/epic/viewer': F.epicPayload(BOARD, 'viewer', { design_status: 'locked', done_when: 'All screens pass the audit in both themes.', description: F.EPIC.description })` and `'/api/epic/epic-01': F.epicPayload(F.LONG_IDS_BOARD, 'epic-01')`; add `const longIdsBoard = (p) => Promise.all(['**/api/board*', '**/api/backlog*'].map((g) => p.route(g, (r) => r.fulfill({ json: F.LONG_IDS_BOARD }))));` and append to `ALL_SCENES`:
  - `table-long` — `#/table` with `routes: longIdsBoard`, `scope: '#screen-mount'`;
  - `table-long-scrolled` — the same, `drive` sets `.tbl-host` `scrollLeft` to its maximum and `scrollTop` to 600;
  - `epics` — `#/epics`, `scope: '#screen-mount'`;
  - `epics-long` — `#/epics` with `routes: longIdsBoard`, `fullPage: true`, `scope: '#screen-mount'`;
  - `epic-detail` — `#/epic/viewer`, `fullPage: true`, `scope: '#screen-mount'`;
  - `epic-detail-long` — `#/epic/epic-01` with `routes: longIdsBoard`, `fullPage: true`, `scope: '#screen-mount'`;
  - `epic-modal` — `#/epics`, click the "Viewer re-skin" row (topmost dialog is the scope);
  - `epic-missing` — `#/epic/nope` with `'/api/epic/nope'` answered `{ status: 404, json: { ok: false, error: 'epic not found' } }` via `routes`, `scope: '#screen-mount'`.
  Each `open` waits for its screen (`table.tbl .tbl-row`, `.epic-row`, `.ed-head` or `.tm-empty`) as `openTable` does.
- [ ] **Step 2: Capture.** `node <wt>/viewer/tests/tools/capture-modals.mjs <wt-of-viewer-rr>/.superpowers/sdd/2026-10-06-viewer-rr-3b-table-epics/shots-task-8 --port=8832` (all scenes, both themes, both widths). `metrics.json` must list for every 3b scene `overflowX: 0` and no `color-contrast` in `axe`, and `problems` empty. LOOK at every 3b image and at `table-chips`/`table-sorted` from 2b. Describe each new image plainly in the report (what is where, what is cut and whether its `title` carries it, how the light theme reads). Fix anything wrong within 3b's files, with a test where one makes sense; list anything that belongs to another track.
- [ ] **Step 3: Route checks** (one mocked spec run, no new file): for `#/table`, `#/epics`, `#/epic/viewer` with `LONG_IDS_BOARD` (and `epicPayload(LONG_IDS_BOARD, 'epic-01')` for the detail), in both themes at 1440×900 and 390×844, the tests already written in Tasks 2–5 cover axe (zero `color-contrast`), `scrollWidth <= innerWidth` and page height; run `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2 table.mock.spec.js epics.mock.spec.js epic-detail.mock.spec.js` and report each count.
- [ ] **Step 3b: Route tables plan 4 can reuse** (index Global Constraints, "Every screen has a mocked spec plan 4 can reuse"). Move each 3b spec's mock table into a named function appended to `viewer/tests/mock-fixtures.js` and have the spec's `boot()` call it: `tableMocks({ theme = 'dark', board = TABLE_BOARD, table } = {})` (move `TABLE_BOARD` there as `export const TABLE_BOARD`), `epicsMocks({ theme = 'dark', board = EPICS_BOARD } = {})` (move `EPICS_BOARD` as an export), and `epicDetailMocks({ theme = 'dark' } = {})` (move `ARCH_EPIC_FIXTURE` as an export). Each returns the object passed to `mockApi`, with `'/api/viewer/prefs'` set from its own `theme` argument (`{ theme, ui: {}, screens: {} }`, plus `table` for `tableMocks`), so plan 4's gate can call it once per theme; nothing in them hard-codes a theme. Their "content is loaded" selectors are `table.tbl .tbl-row` (`#/table`), `.epic-row .link-row__link` (`#/epics`) and `h1.ed-title` (`#/epic/viewer`): never `.tm-empty`, which an error would also show. Re-run the three specs (PASS), stage `viewer/tests/mock-fixtures.js` and the three specs in the Step 6 commit, and list the three functions and selectors in the report.
- [ ] **Step 4: Full runs** — `npm --prefix <wt>/viewer run test:unit`; `MOCK_PORT=8832 npm --prefix <wt>/viewer run test:mock -- --workers=2`; `timeout 900 env --chdir=<wt> C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider`. Report exact passed/failed/skipped counts for each; the style-rules report line for `screens/table.css`, `screens/epics.css`, `screens/epic-detail.css` (all 0, all in `ENFORCED`). Confirm nothing listens on 8832 afterwards (`netstat -ano | grep 8832` → no LISTENING line).
- [ ] **Step 5: Spec §11.** Append (append-only; 3a appends its own) one bullet per ruling this plan made against earlier spec text:
  - §5.6 / §6 Table: Table rows stay `<tr>` (sortable `th` with `aria-sort` need table semantics, and a `<tr>` cannot hold a link as a direct child); each row's title is its one link and a click elsewhere on the row is forwarded to it. Rows open per the detail-view setting, like Kanban cards.
  - §6 Table at ≤768px: a card shows ID, priority, title (2 lines), status, size and epic; phase, area, branch and started are left to the detail. The header row gives way to a labelled Sort select.
  - §5.1 epics: the lifecycle map (`active` ◐, `planned` ○, `done` ●, `archived` ✕) lives in `lib/epic-format.js` beside the epic figures; a missing status reads "Active". The design status is a tag "Design · <word>" on Epic detail only.
  - §6 Epic detail / §7: every figure is counted from the task list the page draws (`epicStats(epic.tasks)`), not the server's `stats`; Done and Archived groups start collapsed.
- [ ] **Step 6: Commit** — `git -C <wt> add viewer/tests/tools/capture-modals.mjs viewer/tests/mock-fixtures.js viewer/tests/table.mock.spec.js viewer/tests/epics.mock.spec.js viewer/tests/epic-detail.mock.spec.js`; message `test(viewer): capture the Table, Epics and Epic detail with a real backlog's volume in both themes and widths`; then, separately, `git -C <wt> add docs/specs/2026-10-01-viewer-reality-reprojection-design.md` with `docs(viewer): spec §11 records plan 3b's rulings`. Never commit images.
