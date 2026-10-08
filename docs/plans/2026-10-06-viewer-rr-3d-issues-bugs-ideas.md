<!-- User intent: rebuild the Issues, Bugs and Ideas list screens on the shared RR parts — real link rows and cards, filter chips that never wrap, one status language, a phone layout that shows one column at a time — so the three screens the audit found collapsed, mouse-only and unreadable in light become usable by keyboard, at 390px and in both themes. -->

# Viewer × Reality Reprojection — Plan 3d: Issues, Bugs, Ideas

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Issues board, the Bugs list and the Ideas list rebuilt on the plan 1/2a/2b foundation per spec §6 — link rows and cards, chip rows with "More", markers from `status.js`, state blocks, a phone column switcher for Issues, a searchable "Tags" popover for Ideas — in dark and light at 1440px and 390px, with audit findings IS-01…IS-08, BG-01…BG-04, IE-01…IE-05 and the 3d carries closed.

**Architecture:** Each screen keeps topbar row 2 for its search and one view control (Issues "View", Bugs "Sort"); its filter chips sit in an in-page filter rail (`filterRail()`, the Table's 2b precedent) built once and updated in place, so the topbar keeps one height and chip groups park behind their own "More". Rows and cards are `linkRow()`s whose own controls (evidence toggle, task links, found-in link) are siblings. Screens build their UI synchronously, start their load without awaiting it, re-render on store notifications, and put focus back where it was through one `keepFocus()` helper. Three small new components — `column-tabs.js`, `tag-filter.js`, `stale-tag.js` — carry the Issues phone switcher, the Ideas tag filter and the stale tag; `status.js` gains the issue and severity maps other tracks also consume.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §6 Issues / Bugs / Ideas, §5.1 (status table), §5.3, §5.6, §5.9, §5.10, §11 (bug statuses, priority in light, popover, chips, topbar row 2). Audit `docs/specs/2026-10-01-viewer-audit.md` §11 (IS-01…IS-08), §13 (BG-01…BG-04), §15 (IE-01…IE-05), §18 (X-01, X-02, X-03, X-07, X-12). Index (binding): `docs/plans/2026-10-06-viewer-rr-3-screens.md`. Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "3d" and "Controller allocation".

**Depends on:** Plan 2b (integration HEAD 638acec): `chipRow`/`filterChip`, `overflowRow`, `openPopover`, `linkRow`, `truncate`, `stateBlock`, `marker()`/`statusMarker()`, `claimTopbar`/`claimTopbarPrimary`/`setTopbarCount`/`tmSearch`/`tmSegmented`/`tmAction`, `renderMarkdown`/`mountMarkdown`, `formatStamp`, `linkPillsEl`/`linkRoute`, `icon()`, the Ideas entity form (`openIdeaCreateModal`).

## Global Constraints

The plan 3 index's Global Constraints, File ownership, Review Focus and commands apply to every task and are not restated. 3d adds:

- **Worktree and port.** `<wt>` = `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3d`, branch `rr3/d-issues-bugs-ideas`, branched from the integration HEAD. Mocked specs: `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2 <spec>`. The capture tool runs with `--port=8834` and only while no mocked run is using that port. Shots go to `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-3d-issues-bugs-ideas/shots-<task>/`; never commit images.
- **Where controls go.** Row 2 holds the search field and at most one view control (Issues: the "View" switcher; Bugs: the "Sort" select). Filter chips and filter toggles go in the screen's filter rail (`filterRail()`, Task 3), the first child of the screen. The Ideas "New idea" button goes in row 1 through `claimTopbarPrimary()`. Counts go in row 1 through `setTopbarCount()`; no screen builds a `tmSubcount` any more.
- **No await in mount.** A 3d screen's `mount()` builds its UI, starts its load without awaiting it, and returns its cleanup. The cleanup clears an `alive` flag that every later response checks, unsubscribes from the store, and destroys every `chipRow`. (The router drops the cleanup of a mount that resolves after a newer navigation, so an awaited load would leak its subscriptions.)
- **Read-only to 3d** (shared with 3c or other tracks): `api.js`, `store.js`, `lib/topbar.js`, `components/aging-bar.js`, `components/severity-glyph.js`, `util/severity-label.js`, `components/link-pills.js`, `components/chips.js`, `popover.js`, `overflow-row.js`, `link-row.js`, `lib/text.js`, `empty-state.js`, `edit/*`. 3d stops importing `severity-glyph.js` and `severity-label.js`; it does not delete them (3c's issue detail uses them).
- **Owned by 3d and consumed by others:** `components/status.js` and `components/stale-tag.js` (Task 1 — schedule and merge first), `css/screens/detail-pages.css` until Task 2 merges (then 3c's).
- **Fixtures** are appended to `viewer/tests/mock-fixtures.js` under one `// ── Plan 3d: list screens ──` heading (Task 4 opens it; Tasks 7 and 9 append below it). Never edit a line above it.
- **Severity words.** Every severity on a 3d screen is read through `severityKey()` (Task 1): `P0…P3` and the words map to `critical/high/medium/low`; anything else is "not set" and shows no marker.

## Review Focus

1. **The Issues board at 390px with real volume** (24 issues, ids like `ISS-1234`, 120-character titles, Status view's five columns). One column shows at a time behind tabs that carry their counts; the page never scrolls sideways; tabs and chips are ≥44px; the page height stays bounded. → Task 7, test "at 390 the board is one column behind tabs with counts, and nothing scrolls sideways" (index Review Focus 1 and 3).
2. **Long evidence read from the keyboard while another writer changes the board.** The clamped evidence's "Show all" is reachable by Tab, opens with Enter, says `aria-expanded`, and stays open and focused when the board poll redraws the cards. → Task 5, unit "an expanded card keeps its evidence unclamped and says so"; Task 7, test "evidence expands from the keyboard and stays expanded and focused through the board poll's redraw" (index Review Focus 3 and 4).
3. **The Ideas "Tags" popover with 40 tags** (and "UX"/"ux" spelled both ways). Searchable, scrolls inside itself, keyboard from the search box to the choices and back, Escape returns to "Tags", one entry per tag whatever its case, a list redraw while it is open keeps it open with its choices and focus. → Task 8, test "forty tags: searchable, scrolls inside, keyboard from search to choices, Escape back to Tags"; Task 9, test "a list redraw while Tags is open keeps it open, its choices and its focus".
4. **Light theme on all three screens** — markers, the stale tag, tags, chips, the found-in and task links on cards. → Tasks 4, 7, 9 each run axe on their route in dark and light with zero `color-contrast`; Task 10 runs axe on every 3d scene in both themes and widths.
5. **Leaving a screen mid-flight** — the issues request still loading, the Tags popover open, a chip group's "More" open. No page error, no popover left, no late response drawing into the next screen. → Task 7, test "leaving Issues while its issues are still loading leaves nothing behind"; Task 9, test "leaving Ideas with Tags open leaves nothing behind"; Task 4, test "leaving Bugs with More open leaves nothing behind" (index Review Focus 5).

## Carry-in

Checked against the code at 638acec:

- Issues collapse, STALE bar, "COMPONENTS:" label, serif heads, 390 page height, light chips (IS-01…IS-06) → Tasks 5, 6, 7. Severity/promoted chips 27px at 390 (IS-07) → Task 7 (44px test).
- Issue card shows symptom and evidence both → Task 5 (one `issueEvidence()` paragraph). Issues search haystack untested → Task 7 (`issueMatchesSearch` unit tests).
- Bugs: no severity, one-line titles, "Archive" reads as text, status chip (BG-01…BG-04) → Task 4. Also found: fixed/adopted/promoted bugs never shown (the screen filters on `filters[status]` with only open/shelved keys) → Task 4.
- Bugs `updateBug`/`createBug` raw errors (`api.js:213-231`): not a 3d path — `screens/bug-detail.js` (3c) is the only caller and `api.js` is shared. Handed to 3c / plan 4's entity-form hardening; flagged to the controller.
- Ideas raw-colour pills, `IDEA_STATUS` unused, "–" circles → Task 9. Screen-local raw `fetch` → Task 9 routes it through `api.get()` (no `api.js` edit). "UX"/"ux" → Task 8. "Show archived" span → Task 9. Italic/serif → Task 9 (rewrite).
- Ideas toolbar chips clipped at row-2 ends → moot once chips leave row 2 (Task 9); Task 10 confirms in the shots.
- Ideas form `err.message` fallback: **dropped** — `entity-modal.js` has no `err.message` path at 638acec; `idea-actions.js` words refusals with `describeWriteError`.
- `.id-body--italic` → **dropped** in Task 2 (dead: no JS uses it). Issue-detail italic serif → 3c.
- Found while reading: issue `duplicate` status (server `ISSUE_STATUSES`) never shown → Tasks 1, 7. `issue-card.js:114` puts `issue.impact` into `innerHTML` → gone in Task 5 (impact leaves the card). Ideas detail parses markdown into `innerHTML` unsanitised (`ideas.js:396-397`) → Task 9 (`mountMarkdown`). Ideas link pills rewritten to `#/issues/…`, `#/kanban/…` (dead routes) → Task 9 (`linkPillsEl`). Issues reads its list from the store cache and never refetches on return → Task 7.

## Order

One worktree. Run order: **1, 2, 6, 3, 4, 5, 7, 8, 9, 10** — Tasks 1, 2 and 6 go first because other tracks consume them (3c: 1 and 2; 3a's Kanban switcher: 6), and the controller merges each as soon as it passes review.

| # | Task | Depends on | Consumed by |
|---|---|---|---|
| 1 | Issue status and severity maps, stale tag | — | 3c (issue/bug detail), Tasks 4, 5, 7 |
| 2 | Detail CSS out of the list screens' files | — | 3c |
| 3 | List toolbar pieces and `keepFocus` | — | Tasks 4, 7, 9 |
| 4 | Bugs | 1, 3; 3a Task 2 | — |
| 5 | Issue card, resolved row | 1 | Task 7 |
| 6 | Column tabs | — | Task 7, 3a Task 8 (Kanban switcher) |
| 7 | Issues | 1, 3, 5, 6; 3a Task 2 | — |
| 8 | Tag filter popover | — | Task 9 |
| 9 | Ideas | 1, 3, 8; 3a Task 2 | — |
| 10 | Verification | 1–9 | controller |

Cross-track waits (as in the index's table): 3d Tasks 4, 7 and 9 wait for 3a Task 2 (row 1 at 390); 3c Tasks 7 and 8 wait for 3d Tasks 1 and 2; 3a Task 8 waits for 3d Task 6. No epic swatches on these screens.

## Spec readings (assumptions; Task 10 appends them to spec §11)

- §6 Ideas "Toolbar: search · status chips · Tags popover · Show archived" and §4 "row 2 = search · view switcher · filters" are read together with the Table's 2b precedent: the filters are a rail at the top of the screen, row 2 keeps the search and the one view control.
- §6 Issues card names ID, severity, title and evidence; the card keeps those plus status (in Severity and List views), "blocks n", the stale tag, location and its task/bug links. Impact and repro steps stay on the issue's own page.
- Issue status `duplicate` exists server-side; it is shown as "Duplicate", moved on (→), in the resolved shelf and as a Status-view column when any exists.
- Bugs: all `BUG_STATUS` values are filter chips; archived bugs (flag or status) come in through a "Show archived" toggle, as on Ideas. The default is Open + Shelved pressed (the old default). A bug with no severity shows no severity marker.
- The stale tag shows only for open and investigating issues whose aging tier is Stale, as a warning marker "stale 45d".
- Ideas rows are real links to `#/ideas/<id>` (the route `linkRoute()` already gives ideas); a plain click selects in place and rewrites the hash with `history.replaceState`, so a new tab or a pasted link opens the same idea.
- The Ideas tag filter keeps today's AND semantics, compared case-insensitively.
- Issues column taglines ("— actively under triage") are dropped; column heads are Technical labels with counts.

## Out of scope

- Issue and bug detail pages, their CSS once Task 2 moves it, and the bug actions' error wording → 3c. `api.js` `updateBug`/`createBug` messages → 3c / plan 4.
- The live specs `issues.spec.js`, `issues-routing.spec.js` stay as they are (plan 4 retires stale live specs); `issues.mock.spec.js` (Task 7) replaces their coverage.
- Legacy `.tm-chip-row` rules in `shell.css` → 3a / plan 4. Kanban's column switcher consumes `column-tabs.js` (3a Task 8, after 3d Task 6 merges); 3d does not touch Kanban.
- Editing an idea (no edit form exists today) — not added.

---

### Task 1: Issue status and severity maps, and the stale tag

**Depends on:** nothing. **Merge first** (3c's issue and bug pages consume all of it).

**Files:**
- Create: `viewer/js/components/stale-tag.js`, `viewer/tests/unit/stale-tag.test.js`
- Modify: `viewer/js/components/status.js`
- Test: `viewer/tests/unit/status.test.js`

**Interfaces:**
- Consumes: the file's own `freeze`, `lookup`, `marker`; `computeAgingTier` (`aging-bar.js`, read-only), `issueDiscovered` (`util/issue-fields.js`).
- Produces:
  ```js
  // By meaning, as the spec's status table has it (§5.1).
  export const ISSUE_STATUS;   // open ['Open','○','neutral'], investigating ['Investigating','◐','accent'],
                               // fixed ['Fixed','●','success'], wontfix ["Won't fix",'✕','neutral'],
                               // duplicate ['Duplicate','→','neutral']          (order = the server's ISSUE_STATUSES)
  export const SEVERITY;       // critical ['Critical','◆','critical'], high ['High','▲','orange'],
                               // medium ['Medium','●','warning'], low ['Low','○','neutral']   (same as PRIORITY)
  export function severityKey(value) → 'critical' | 'high' | 'medium' | 'low' | null
    // a string, trimmed and lower-cased: 'p0'…'p3' → critical/high/medium/low; a SEVERITY key → itself; else null.
    // null, undefined, '', numbers, objects → null.
  export function severityMeta(value) → { label, shape, tone } | null
    // known → a copy of SEVERITY[key]; a string that is not empty once trimmed and that it does not know →
    // { label: value.trim(), shape: '○', tone: 'neutral' }; anything else, whitespace-only strings included → null (no severity set)
  export function severityMarker(value) → HTMLSpanElement | null   // marker(severityMeta(value)) or null
  // STATUS_KINDS gains `issue: ISSUE_STATUS`, so statusMeta('issue', v) / statusMarker('issue', v) work.
  // components/stale-tag.js
  export function staleDays(issue, now = Date.now()) → number | null   // whole days since issueDiscovered(issue); null when none or unparsable
  export function staleTag(issue, agingCfg = {}, now = Date.now()) → HTMLSpanElement | null
    // null unless issue.status is 'open' or 'investigating' and the tier is 'Stale': issue.aging.tier when it is a string
    // (the server's), else computeAgingTier({ ...issue, severity_label: SEVERITY[key]?.label ?? 'Medium' }, agingCfg, new Date(now)).tier.
    // The tag: marker({ label: `stale ${days}d`, shape: '▲', tone: 'warning' }) with class `stale-tag` added and
    // title `Open ${days} days — past the aging window for its severity`.
  ```

- [ ] **Step 1: Write the failing tests** in `status.test.js` (extend the import line with `ISSUE_STATUS, SEVERITY, severityKey, severityMeta, severityMarker`):
  - `ISSUE_STATUS` deep-equals the table above, keys in that order; `statusMeta('issue', 'wontfix').label === "Won't fix"`; `statusMeta('issue', 'duplicate').shape === '→'`.
  - no two issue statuses share a shape and tone (same check as the existing task-status test).
  - `SEVERITY` deep-equals `PRIORITY` (value for value).
  - `severityKey` table: `'P0'→'critical'`, `'p3'→'low'`, `'High'→'high'`, `' medium '→'medium'`, `'critical'→'critical'`, `''→null`, `null→null`, `undefined→null`, `'P5'→null`, `7→null`, `{}→null`.
  - `severityMeta('P1')` deep-equals `{ label: 'High', shape: '▲', tone: 'orange' }` and is a copy (mutating it leaves `SEVERITY.high` intact); `severityMeta('P5')` → `{ label: 'P5', shape: '○', tone: 'neutral' }`; `severityMeta('')`, `severityMeta('   ')` and `severityMeta(null)` → `null`; `severityMarker('  ')` → `null`.
  - `severityMarker('P0')` → `span.marker.marker--critical` with `.marker__shape[data-shape="diamond"][aria-hidden="true"]` and word "Critical"; `severityMarker(null) === null`.
  - a severity `'<img src=x onerror=alert(1)>'` → `severityMarker` word is that text and the marker holds no `img`.
  - `stale-tag.test.js`: `now = Date.parse('2026-10-06T00:00:00Z')`; an open P1 issue discovered `2026-08-22` with `aging: { tier: 'Stale' }` → tag text "stale 45d", `.marker--warning`, `title` contains "45 days"; same with `tier: 'Fresh'` → null; `status: 'fixed'` → null; no `aging` and cfg `{ High: 30 }` → computed Stale → tag; no discovered date → null; `staleDays` with `'2026-10-01'` → 5. The stale tag needs no CSS of its own (it is a marker).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/status.test.js viewer/tests/unit/stale-tag.test.js` — Expected: FAIL (`ISSUE_STATUS` undefined, `stale-tag.js` missing).
- [ ] **Step 3: Implement** in `status.js`: the two tables with a one-line comment each (by meaning, as `BUG_STATUS` has), `SEVERITY_CODES = { p0: 'critical', p1: 'high', p2: 'medium', p3: 'low' }`, the three functions, `issue` in `STATUS_KINDS`; `stale-tag.js` with its `User intent:` header.
- [ ] **Step 4: Run** the unit file, then `npm --prefix <wt>/viewer run test:unit` — Expected: PASS (the stylesheet test "every shape in the tables has a drawn form" passes unchanged: no new shape).
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/js/components/status.js viewer/js/components/stale-tag.js viewer/tests/unit/status.test.js viewer/tests/unit/stale-tag.test.js` then `git -C <wt> commit -m "feat(viewer): status maps for issues and severity — one shape and word for every issue status and severity, whatever form it arrives in — and a stale tag only on issues past their aging window"`

---

### Task 2: Detail CSS out of the list screens' files

**Depends on:** nothing. **Merge early** (3c owns the new file afterwards). Agreed with plan-3c by message; if 3c's plan names other target files, use those names and keep everything else.

`issues.css` and `bugs.css` hold the issue- and bug-detail rules (and the aging bar only issue detail draws), so neither list file could be enforced without rewriting 3c's screens. They move, unchanged, to a file of their own.

**Files:**
- Create: `viewer/css/screens/detail-pages.css`, `viewer/tests/unit/detail-pages-css.test.js`
- Modify: `viewer/css/screens/issues.css` (remove the `/* aging bar */` block, today lines 255–267, and everything from `/* ─── Issue detail (full-screen) ─── */` to the end, today 334–512), `viewer/css/screens/bugs.css` (remove everything from `/* ─── Bug detail screen ─── */` to the end, today 139–227, including its `@media (max-width: 720px)` block, whose rules are all detail rules), `viewer/index.html` (link `css/screens/detail-pages.css` on the line after `css/screens/ideas.css`)

**Interfaces:**
- Produces: `detail-pages.css` = the header comment `/* User intent: the issue and bug detail pages' rules, moved verbatim out of the list screens' stylesheets (plan 3d Task 2); plan 3c re-skins them here. */`, then the moved issue-detail block, the aging-bar block and the bug-detail block, byte-for-byte as they were, minus the `.id-body--italic { … }` rule (dead: no JS uses the class).

- [ ] **Step 1: Write the failing test** `detail-pages-css.test.js` (reads files with `readFileSync`; comments stripped as in `style-rules.test.js`): for each selector in `['.issue-detail', '.id-crumb', '.id-back', '.id-empty', '.id-head', '.id-meta', '.id-sev', '.id-status', '.id-title', '.id-location', '.id-grid', '.id-main', '.id-side', '.id-h', '.id-body', '.id-repro-list', '.id-side-block', '.id-aging', '.id-dl', '.id-rel-pill', '.aging-bar', '.aging-bar__fill', '.bug-detail', '.bug-detail__sev', '.bug-detail__actions', '.bug-detail__action-btn']`: it starts a selector in `detail-pages.css` (`new RegExp('(^|[},\\s])' + escaped + '(?![\\w-])')`) and appears in neither `issues.css` nor `bugs.css`; no CSS file under `viewer/css` contains `id-body--italic`; `index.html`'s stylesheet links list `css/screens/detail-pages.css` immediately after `css/screens/ideas.css`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/detail-pages-css.test.js` — Expected: FAIL (file missing).
- [ ] **Step 3: Move** the blocks (cut and paste; no rule edited), delete `.id-body--italic`, add the link. Check with `git -C <wt> diff --stat` that the lines removed from the two files equal the lines added to the new one plus the header, minus the six lines of the dead rule.
- [ ] **Step 4: Run** the unit suite — Expected: PASS; style-rules report shows `screens/detail-pages.css` with the violations the two old files had in those blocks (state the three numbers before/after in the report).
- [ ] **Step 5: Commit** — `git -C <wt> add viewer/css/screens/detail-pages.css viewer/css/screens/issues.css viewer/css/screens/bugs.css viewer/index.html viewer/tests/unit/detail-pages-css.test.js` then commit `refactor(viewer): the issue and bug detail rules move verbatim out of the list screens' stylesheets into their own file`

---

### Task 3: List toolbar pieces and `keepFocus`

**Depends on:** nothing.

**Files:**
- Create: `viewer/js/components/list-toolbar.js`, `viewer/js/lib/keep-focus.js`, `viewer/css/components/list-toolbar.css`, `viewer/tests/unit/list-toolbar.test.js`, `viewer/tests/unit/keep-focus.test.js`
- Modify: `viewer/index.html` (link `css/components/list-toolbar.css` after `css/components/toolbar.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/list-toolbar.css`)

**Interfaces:**
- Consumes: `h()`, `icon()`.
- Produces:
  ```js
  // components/list-toolbar.js
  export function filterRail({ label = 'Filters', onClear }) → { el, add(...nodes), setClearable(on) }
    // el: div.list-filters[role="group"][aria-label=label]; holds, last, button.btn.btn--ghost.btn--sm.list-filters__clear
    //     (icon('dismiss', { size: 14 }) + "Clear filters"), hidden until setClearable(true). Click → onClear().
    // add(...nodes) inserts each node before the clear button, in order.
  export function labelled({ label, control }) → HTMLDivElement
    // control is or holds a <select>/<input>: div.list-labelled > label.list-labelled__label[for=<that control's id>] + control
    //   (an id `list-ctl-<n>` is given to the control when it has none).
    // otherwise (a segmented control): div.list-labelled[role="group"][aria-labelledby=<label id>] >
    //   span.list-labelled__label[id] + control.
  // lib/keep-focus.js
  export function keepFocus(scope) → () => boolean
    // Called before a redraw. Focus inside `scope` (not scope itself): remembers the focused element's `data-focus`,
    // else its `href` attribute. The returned function, called after the redraw, focuses the first element in `scope`
    // with the same `data-focus` (else the same `href`) with { preventScroll: true }, and returns whether focus is now
    // on it. Focus outside scope, or nothing found → returns false and moves nothing.
  ```
- `list-toolbar.css` (tokens only): `.list-filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs) var(--space-lg); min-width: 0; }` `.list-filters > .chip-row { flex: 0 0 auto; max-width: 100%; }` `.list-filters > .chip-row[data-grow] { flex: 1 1 240px; min-width: 240px; }` (the Table's two group rules) `.list-filters > .chip { flex: 0 0 auto; }` `.list-filters__clear { flex: 0 0 auto; margin-left: auto; }` `.list-filters__clear[hidden] { display: none; }` `.list-labelled { display: inline-flex; align-items: center; gap: var(--space-xs); flex: 0 0 auto; }` `.list-labelled__label` in the Technical label voice (`--font-technical`, 800, `--size-technical-label`, uppercase, `--tracking-ultra`, `--foreground-subtle`); `.list-tag { display: inline-block; max-width: 16ch; padding: 0 var(--space-micro); border: 1px solid var(--border-default); border-radius: var(--radius-sm); color: var(--foreground-default); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); line-height: var(--leading-heading); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; vertical-align: middle; }` (a non-interactive tag; ideas and archived rows use it); `@media (max-width: 768px) { .list-filters > .chip, .list-filters__clear { min-height: 44px; } }`.

- [ ] **Step 1: Write the failing tests.** `list-toolbar.test.js` (jsdom, set up like `chips.test.js`): `filterRail({ onClear })` → `role="group"`, `aria-label="Filters"`, clear button last and `hidden`; `add(a, b)` puts `a`, `b` before it in order; `setClearable(true)` unhides; a click calls `onClear` once; `labelled({ label: 'Sort', control: <span class="ef-select"><select></select></span> })` → a `label` whose `for` equals the select's generated id and whose text is "Sort", no `role`; `labelled({ label: 'View', control: <div class="tm-segmented"> })` → `role="group"` whose `aria-labelledby` resolves to "View". `keep-focus.test.js`: a scope holding `<button data-focus="evidence:ISS-1">` focused → after `scope.replaceChildren(<fresh copy>)` the restorer focuses the fresh button and returns true; same by `href` for an `<a href="#/bug/B-1">` without `data-focus`; focus outside the scope → returns false and `document.activeElement` is unchanged; nothing matching → false.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/list-toolbar.test.js viewer/tests/unit/keep-focus.test.js` — Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** both modules (each with a `User intent:` header), the CSS, the link, the ENFORCED entry.
- [ ] **Step 4: Run** the unit suite — Expected: PASS; `components/list-toolbar.css` enforced, 0 violations.
- [ ] **Step 5: Commit** — stage the seven files; `feat(viewer): a filter rail, labelled toolbar controls and a focus keeper for the list screens`

---

### Task 4: Bugs

**Depends on:** Tasks 1, 3; **3a Task 2 merged** (row 1 at 390: the count shrinks first; this task's row-1 count assertions at 390 hold only after it).

Spec §6 Bugs; BG-01…BG-04, X-03 (23 mouse-only cards), X-07.

**Files:**
- Create: `viewer/js/util/bugs-filter.js`, `viewer/tests/unit/bugs-filter.test.js`, `viewer/tests/unit/bug-row.test.js`, `viewer/tests/bugs.mock.spec.js`
- Rewrite: `viewer/js/components/bug-card.js` (exports `bugRow`; `bugCard` is deleted — `bugs.js` is its only importer), `viewer/js/screens/bugs.js`, `viewer/css/screens/bugs.css` (what Task 2 left: the list rules)
- Modify: `viewer/tests/mock-fixtures.js` (append), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/bugs.css`)

**Interfaces:**
- Consumes: `BUG_STATUS`, `statusMarker`, `statusMeta`, `severityKey`, `severityMarker`, `SEVERITY` (Task 1); `filterRail`, `labelled`, `keepFocus` (Task 3); `chipRow`, `filterChip`, `chipClickNext`, `CHIP_CLICK_HINT`, `linkRow`, `truncate`, `stateBlock`, `formatStamp`, `icon`, `claimTopbar`, `setTopbarCount`, `tmSearch`, `api.listBugs`.
- Produces:
  ```js
  // util/bugs-filter.js
  export const BUG_SORTS = [{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' },
                            { value: 'severity', label: 'Severity' }, { value: 'id', label: 'ID' }];
  export function isArchivedBug(bug) → boolean          // bug.archived === true || bug.status === 'archived'
  export function bugPrefs(saved) → { statuses: string[], archived: boolean, sort: string }
    // saved = prefs.screens.bugs. `statuses` an array of strings → kept; else legacy `filters` {open, shelved, archive}:
    // truthy open/shelved → statuses, archive → archived. Nothing saved → { statuses: ['open', 'shelved'], archived: false,
    // sort: 'newest' }. A sort not in BUG_SORTS → 'newest'.
  export function filterBugs(bugs, { statuses = [], archived = false, search = '' } = {}) → bug[]
    // archived bugs only when `archived`; empty statuses = every status; search (trimmed, case-insensitive) over
    // id, title and components joined by spaces
  export function sortBugs(bugs, sort) → bug[]   // a new array
    // newest: discovered descending, undated last, then id descending; oldest: discovered ascending, undated last;
    // severity: critical, high, medium, low, unset, each then newest; id: localeCompare(…, { numeric: true }) (B-9 < B-10)
  export function bugStatusChips(bugs, pressed) → [{ value, label, count, pressed }]
    // the statuses present among `bugs` (already narrowed by the archived toggle) in BUG_STATUS order, then unknown ones
    // alphabetically (label = statusMeta('bug', v).label); a pressed status that is absent is listed too, in its table
    // place, with count 0, so it can be released.
    // 'archived' is never a chip (the toggle owns it).
  // components/bug-card.js
  export function bugRow(bug, { now = Date.now() } = {}) → HTMLLIElement
  ```
- `bugRow` structure (each line is a unit test):
  1. `linkRow({ tag: 'li', className: 'bug-row' + (archived ? ' bug-row--archived' : ''), href: '#/bug/<encoded id>', name, content, controls })`.
  2. `name` = `span.bug-row__name` → `span.bug-row__id` (id), `span.bug-row__severity` (holding `severityMarker(bug.severity)`, or empty and `aria-hidden="true"` when unset), `truncate(title || 'Untitled', { lines: 2, className: 'bug-row__title' })`.
  3. `content` = `statusMarker('bug', bug.status || 'open')`, `span.list-tag` "Archived" when archived, `span.bug-row__components` (components joined ", ", via `truncate`) when any, `time.bug-row__age[datetime=<discovered>][title=<formatStamp title>]` with `formatStamp(discovered).text` when discovered.
  4. `controls` = `a.bug-row__found-in[href="#/task/<id>"]` "found in T-102" when `found_in` is set (the detail interceptor opens the task's modal from it); none otherwise.
- Screen behaviour (each line is a test):
  1. Row 2: `tmSearch({ placeholder: 'Search bugs…' })` and `labelled({ label: 'Sort', control: span.ef-select > select.ef-enum-select#bugs-sort (BUG_SORTS) + icon('chevron', { size: 16 }) })`. Row 1 count (the index's wording rule): `setTopbarCount` "<all> bugs" while nothing narrows the list, "<all> bugs · <shown> visible" while a status, the search or the archived toggle is set ("1 bug" in the singular); `<all>` counts the bugs the archived toggle admits.
  2. Rail: `chipRow({ label: 'Status', chips: bugStatusChips(…), hint: CHIP_CLICK_HINT })` (clicks through `chipClickNext`), then `filterChip({ label: 'Show archived', value: 'archived', pressed, count: <archived bugs> })`, then Clear. Clear resets statuses to `[]`, archived to false and empties the search (`input.value = ''` + an `input` event).
  3. One request: `api.listBugs({ include_archive: true })`. Prefs saved through `prefs.patch({ screens: { bugs: { statuses, archived, sort } } })` on every change (the legacy `filters` key is no longer written).
  4. List: `ul.bugs__list[aria-label="Bugs"]` of `bugRow`s in `sortBugs(filterBugs(…), sort)` order; redraws go through `keepFocus(list)`.
  5. States (`stateBlock`): loading `{ state: 'loading', busy: true, headline: 'Loading bugs…' }`; load failed `{ state: 'error', label: 'Bugs', headline: 'Could not load bugs.', action: { label: 'Try again', onClick: load } }`; none at all `{ label: 'Bugs', headline: 'No bugs recorded yet.' }`; none match `{ label: 'No matches', headline: 'No bugs match these filters.', action: { label: 'Clear filters', onClick: clear } }`. No status code, method, path or JSON reaches the page.
  6. Cleanup destroys the chip row and ignores a late response (Global Constraints "No await in mount").
- `bugs.css` (tokens only, rewritten): `.bugs { display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; }` `.bugs__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-xs); }` `.bug-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: var(--space-xs) var(--space-md); padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); }` (hover is `rows.css`'s `--card-bg-hover`) `.bug-row > .link-row__link { display: block; min-width: 0; color: var(--foreground-bold); text-decoration: none; }` `.bug-row__name { display: grid; grid-template-columns: minmax(8ch, max-content) 7rem minmax(0, 1fr); align-items: baseline; column-gap: var(--space-sm); }` `.bug-row__id` Technical small, `white-space: nowrap`, `--foreground-default`; `.bug-row__title` Narrator small 600, `overflow-wrap: anywhere`; `.bug-row > .link-row__content { display: flex; align-items: center; gap: var(--space-sm); min-width: 0; white-space: nowrap; }` `.bug-row__components { max-width: 18ch; }` `.bug-row__age` Technical small `--foreground-subtle`; `.bug-row__found-in` Technical small, `color: var(--text-accent)`, no underline until hover/focus; at ≤768px `.bug-row { grid-template-columns: minmax(0, 1fr); }` `.bug-row__name { grid-template-columns: max-content max-content; }` with the title on its own row (`grid-column: 1 / -1`), `.bug-row > .link-row__link, .bug-row__found-in { min-height: 44px; }` `.bug-row__found-in { display: inline-flex; align-items: center; }`. No `opacity`, no hue on text other than `--text-accent`.
- Fixtures appended to `mock-fixtures.js` (opens the 3d heading; Tasks 7 and 9 reuse `longText` and `daysAgo`):
  ```js
  // ── Plan 3d: list screens ──
  const daysAgo = (d) => new Date(Date.now() - d * 86_400_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  // 120 characters with spaces in them, so a title wraps; `unbroken` gives one long word that must not push the page wide.
  const longText = (lead, { unbroken = false } = {}) =>
    (unbroken ? lead + '-' + 'x'.repeat(120) : `${lead} — the words keep going past the edge of a phone screen and on again`.repeat(2)).slice(0, 120);
  // GET /api/bugs?include_archive=1: every bug, the archived ones flagged.
  export const LIST_BUGS = [
    { id: 'B-031', title: 'Card edge vanishes on the light ground', status: 'open', severity: 'P1', found_in: 'T-102', components: ['viewer'], discovered: daysAgo(2) },
    { id: 'B-030', title: 'Phase strip clips the current phase name', status: 'open', found_in: 'T-102', discovered: daysAgo(3) },
    { id: 'B-029', title: 'Store write hangs for six seconds on a large backlog', status: 'fixed', severity: 'P0', components: ['store'], discovered: daysAgo(16) },
    { id: 'B-028', title: 'Handover quote loses its heading', status: 'shelved', severity: 'P3', discovered: daysAgo(18) },
    { id: 'B-027', title: 'Inbox message archived twice', status: 'adopted', severity: 'P2', adopted_into: 'T-118', discovered: daysAgo(21) },
    { id: 'B-026', title: 'Legacy mirror written after cutover', status: 'fixed', archived: true, discovered: daysAgo(35) },
  ];
  const BUG_STATUSES = ['open', 'open', 'shelved', 'fixed', 'adopted', 'promoted'];
  export const LONG_BUGS = Array.from({ length: 23 }, (_, i) => ({
    id: `B-${1201 + i}`, title: longText(`Bug ${1201 + i}`, { unbroken: i === 4 }), status: BUG_STATUSES[i % 6],
    severity: i % 5 === 4 ? undefined : `P${i % 4}`, found_in: `T-${1234 + i}`, components: ['viewer', 'store'], discovered: daysAgo(i + 1),
  }));
  // The table plan 4's a11y gate reuses for #/bugs; loaded when `.bugs__list .bug-row` is visible.
  export const bugsMocks = ({ theme = 'dark' } = {}) => ({ '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/bugs': LIST_BUGS, '/api/board': BOARD, '/api/backlog': BOARD,
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1', RICH_RELATED) });
  ```

- [ ] **Step 1: Write the failing unit tests.** `bugs-filter.test.js`: `bugPrefs(undefined)` default; `bugPrefs({ filters: { open: true, shelved: false, archive: true } })` → `{ statuses: ['open'], archived: true, sort: 'newest' }`; `bugPrefs({ statuses: ['fixed'], sort: 'bogus' })` → sort `'newest'`; `filterBugs(LIST_BUGS)` excludes B-026 and includes the fixed B-029; `{ archived: true }` includes B-026; `{ statuses: ['open'] }` → B-031, B-030; `{ search: 'STORE' }` → B-029 (component match); `sortBugs(…, 'severity')` ids `['B-029', 'B-031', 'B-027', 'B-028', 'B-030']` on the non-archived five; `'id'` orders `B-9` before `B-10`; `bugStatusChips(nonArchived, ['promoted'])` → values `['open', 'fixed', 'adopted', 'promoted', 'shelved']` (table order), promoted with count 0 and pressed. `bug-row.test.js` (jsdom): lines 1–4 above; B-030 (no severity) has an empty `aria-hidden` severity cell and exactly one `.marker` (its status); a title `<img src=x onerror=alert(1)>` creates no `img`; the link's `href` for id `B 1` is `#/bug/B%201`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/bugs-filter.test.js viewer/tests/unit/bug-row.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement** `bugs-filter.js`, `bugRow`, the screen, the CSS, the fixtures, the ENFORCED entry.
- [ ] **Step 4: Write the mocked tests** `bugs.mock.spec.js` (`mockApi(page, { ...bugsMocks(), <test overrides> })`; every test waits for `.bugs__list .bug-row` (or its own state) before acting; record `PUT /api/viewer/prefs` bodies; `afterEach` asserts `unmockedWrites` empty and no `pageerror`):
  - "rows are links and the default shows open and shelved bugs": rows B-031, B-030, B-028 in that order; `getByRole('link', { name: /B-031/ })` has `href` `#/bug/B-031`; `#topbar-count` reads "5 bugs · 3 visible"; Enter on that link → `location.hash` `#/bug/B-031`.
  - "every status is a chip and a fixed bug can be found": click "Fixed" → only B-029; the last prefs PUT carries `screens.bugs.statuses` `['fixed']`; Shift+click "Adopted" → B-029 and B-027.
  - "Show archived brings archived bugs in, marked": press it → B-026 listed with an "Archived" tag; the button `aria-pressed="true"`.
  - "a severity is a marker and an unset one is nothing": B-031's row holds the word "High"; B-030's row has one `.marker`.
  - "Sort is a labelled select": `getByLabel('Sort')` is the select; choose "Severity" → the default view's rows read `B-031, B-028, B-030` (High, Low, unset); the prefs PUT carries `sort: 'severity'`.
  - "found in opens the task, never the bug": click "found in T-102" on B-031 → the detail modal for T-102 opens and `location.hash` is not `#/bug/B-031`.
  - keyboard walk: focus the empty search field (its clear button is hidden), Tab repeatedly and record each focused element's accessible name: Sort → the Status chips in order → "Show archived" → B-031's link → "found in T-102" → B-030's link.
  - "a failed load is said in words and can be tried again": first `/api/bugs` answer `{ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } }`, the second LIST_BUGS (counted route); the page shows "Could not load bugs." and no `500`, `/api`, `sqlite3` or `{`; "Try again" → rows appear.
  - "no match offers to clear": search `zzz` → "No bugs match these filters." with "Clear filters"; pressing it shows all five non-archived bugs and empties the search.
  - "leaving Bugs with More open leaves nothing behind": at 390×844 with `LONG_BUGS` the Status row's More is visible (five statuses do not fit); open it, `location.hash = '#/kanban'`; no `.popover`, no `.bugs`, no page error.
  - at 390×844 with `LONG_BUGS`: `document.documentElement.scrollWidth <= innerWidth`, `#screen-mount` `scrollWidth <= clientWidth`; every `.chip`, `.overflow-more`, `.list-filters__clear:not([hidden])`, `#bugs-sort`, row link and found-in link ≥44px tall; no id wraps (`.bug-row__id` height equals one line: ≤ its line-height + 1px).
  - axe on `#screen-mount` and `#topbar` in dark and light: zero `color-contrast`, `nested-interactive`, `label`, `select-name`, `aria-*`.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2 bugs.mock.spec.js shell.mock.spec.js` — Expected: PASS; `screens/bugs.css` enforced, 0 violations.
- [ ] **Step 6: Commit** — stage the files listed; `feat(viewer): the Bugs list is link rows with severity and status markers, every status a chip, archived bugs behind a toggle, a labelled Sort, and its states in words`

---

### Task 5: Issue card and resolved row

**Depends on:** Task 1.

Spec §6 Issues (card); IS-01, IS-04, IS-05, IS-08, X-03.

**Files:**
- Create: `viewer/css/components/issue-card.css`, `viewer/tests/unit/issue-card.test.js`
- Rewrite: `viewer/js/components/issue-card.js`
- Modify: `viewer/tests/unit/issue-card-evidence.test.js` (same three behaviours on the new card), `viewer/index.html` (link `css/components/issue-card.css` after `css/components/list-toolbar.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/issue-card.css`)

**Interfaces:**
- Consumes: `severityKey`, `severityMarker`, `statusMarker`, `staleTag` (Task 1); `computeBlocksCount` (`util/issue-blocks.js`); `issueEvidence`, `issueDiscovered` (`util/issue-fields.js`); `linkRow`, `truncate`, `formatStamp`.
- Produces:
  ```js
  // components/issue-card.js
  export function issueCard(issue, { tasksIndex = {}, agingCfg = {}, expanded = false, onToggleEvidence, showStatus = false,
                                     now = Date.now() } = {}) → HTMLElement
  export function issueRow(issue, { now = Date.now() } = {}) → HTMLElement
  ```
- `issueCard` structure (each line is a unit test):
  1. `linkRow({ tag: 'article', className: 'issue-card', href: '#/issue/<encoded id>', name, content, controls })`; the article carries `data-issue-id` and `data-status`.
  2. `name` = `span.issue-card__name` → `span.issue-card__line` (`span.issue-card__id` + `severityMarker(issue.severity_label ?? issue.severity)` when set) and `span.issue-card__title` (full title, not cut).
  3. `content`, in order and each only when it has something: `div.issue-card__meta` holding `statusMarker('issue', status)` (only with `showStatus`), `span.issue-card__blocks` "Blocks <n> task(s)" (from `computeBlocksCount`), `staleTag(…)`, and `truncate(location.join(', '), { className: 'issue-card__location' })`; then `truncate(issueEvidence(issue), { lines: 3, tag: 'p', className: 'issue-card__evidence' })` with `id="issue-evidence-<id>"` — without `truncate--3` when `expanded`. Evidence appears once (an issue with both `symptom` and `evidence` shows the evidence only).
  4. `controls`: when there is evidence, `button.btn.btn--ghost.btn--sm.issue-card__more[type=button][aria-controls=<evidence id>][aria-expanded][data-focus="evidence:<id>"]` reading "Show all" / "Show less"; it is `hidden` unless `expanded`, and on the next animation frame after the card is connected it is unhidden when the evidence overflows (`scrollHeight > clientHeight + 1`); a click calls `onToggleEvidence(issue.id)` (the screen owns the expanded set and redraws). Then `div.issue-card__refs` holding one `a.issue-card__ref[href="#/task/<id>"]` per `related_tasks` entry and one `a.issue-card__ref[href="#/bug/<id>"]` per `promoted_from` entry, each with `data-focus="ref:<issue>:<target>"` and the target id as text, preceded by a `span.issue-card__refs-label` "Tasks" / "From bugs" (Technical label). No `details`, no impact, no repro, no `innerHTML`.
- `issueRow` (resolved issues): `linkRow({ tag: 'div', className: 'issue-row', href, name: [span.issue-row__id, severity marker when set, truncate(title, { className: 'issue-row__title' })], content: [statusMarker('issue', status), time.issue-row__when (formatStamp(resolved ?? updated) text and title) when either is set] })`.
- `issue-card.css` (tokens only): `.issue-card { display: flex; flex-direction: column; gap: var(--space-xs); padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); min-width: 0; }` `.issue-card > .link-row__link { color: var(--foreground-bold); text-decoration: none; }` `.issue-card__name { display: flex; flex-direction: column; gap: var(--space-micro); }` `.issue-card__line { display: flex; align-items: center; gap: var(--space-sm); }` `.issue-card__id` Technical small, `white-space: nowrap`, `--foreground-default`; `.issue-card__title` Narrator small 600, `overflow-wrap: break-word`; `.issue-card__meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-micro) var(--space-sm); min-width: 0; }` `.issue-card__blocks`, `.issue-card__location` Technical small `--foreground-subtle`; `.issue-card__evidence { margin: 0; color: var(--foreground-default); font-size: var(--size-narrator-small); line-height: var(--leading-body); overflow-wrap: anywhere; }` `.issue-card > .link-row__controls { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs) var(--space-sm); }` `.issue-card__more[hidden] { display: none; }` `.issue-card__refs { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-micro) var(--space-xs); }` `.issue-card__ref` Technical small `color: var(--text-accent)`; `.issue-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--space-sm); padding: var(--space-xs) var(--space-sm); background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); }` `.issue-row > .link-row__link { display: grid; grid-template-columns: minmax(8ch, max-content) 6.5rem minmax(0, 1fr); align-items: baseline; column-gap: var(--space-sm); min-width: 0; color: var(--foreground-default); text-decoration: none; }` `.issue-row > .link-row__content { display: flex; align-items: center; gap: var(--space-sm); white-space: nowrap; }` `.issue-row__when` Technical small `--foreground-subtle`; at ≤768px `.issue-card__more, .issue-card__ref, .issue-card > .link-row__link, .issue-row > .link-row__link { min-height: 44px; }` `.issue-card__more, .issue-card__ref { display: inline-flex; align-items: center; }` and `.issue-row > .link-row__link { grid-template-columns: max-content max-content; }` with the title on its own line.

- [ ] **Step 1: Write the failing tests.** `issue-card.test.js` (jsdom): lines 1–4 for a fixture with `related_tasks: ['T-102']`, `promoted_from: ['B-031']`, `location: ['viewer/js/x.js:12']`, `severity_label: 'High'`; `isInteractive(name)` false and every `a`/`button` sits under `.link-row__controls`; **"an expanded card keeps its evidence unclamped and says so"** (Review Focus 2): `expanded: true` → evidence lacks `truncate--3`, the toggle is not hidden, `aria-expanded="true"`, text "Show less"; `expanded: false` → `truncate--3` present, toggle hidden, `aria-expanded="false"`; a click calls `onToggleEvidence('ISS-001')`; no evidence → no toggle; `showStatus: true` → a status marker with "Investigating"; an `impact` of `` `<img src=x onerror=alert(1)>` `` creates no `img` anywhere in the card; `issueRow` for a `wontfix` issue → status word "Won't fix", `href` `#/issue/ISS-002`. Adapt `issue-card-evidence.test.js` to the new card (evidence-only, legacy symptom, exactly once).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/issue-card.test.js viewer/tests/unit/issue-card-evidence.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement** the card and row, the CSS, the link, ENFORCED.
- [ ] **Step 4: Run** the unit suite — Expected: PASS; `components/issue-card.css` 0 violations. Until Task 7 the old `issues.js` draws the new cards (its `onTaskClick` and `suppressSeverityChip` options are ignored) with its old layout; no mocked spec covers `#/issues` yet.
- [ ] **Step 5: Commit** — `feat(viewer): issue cards are link rows — severity marker, evidence clamped to three lines with a real Show all, the stale tag, task and bug links beside the link — and resolved issues are link rows too`

---

### Task 6: Column tabs

**Depends on:** nothing. **Merge early:** 3a's Kanban switcher (3a Task 8) consumes it, looking tabs up by `[role="tab"][aria-controls="<panelId>"]`, reading their ids, and refocusing a tab after `update()` — the stable id and the reuse of buttons by key below are contract, not detail.

Spec §6 Issues ("column switcher at ≤768px"); the Kanban's KB-03 pattern (tabs with counts, `role="tablist"`).

**Files:**
- Create: `viewer/js/components/column-tabs.js`, `viewer/css/components/column-tabs.css`, `viewer/tests/unit/column-tabs.test.js`
- Modify: `viewer/index.html` (link after `css/components/issue-card.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/column-tabs.css`)

**Interfaces:**
- Consumes: `h()`.
- Produces:
  ```js
  export function columnTabs({ label, columns, selected, onSelect }) → { el, update({ columns, selected }) }
    // columns: [{ key, label, count, panelId }]. el: div.column-tabs[role="tablist"][aria-label=label], `hidden` when
    // fewer than two columns. Each tab: button.column-tabs__tab[type=button][role="tab"][id=`${panelId}-tab`]
    // [aria-controls=panelId][aria-selected="true|false"][tabindex="0" on the selected, "-1" on the rest][data-key]
    // holding span.column-tabs__label and span.column-tabs__count.
    // Click → onSelect(key). ArrowRight/ArrowLeft (wrapping), Home, End: focus that tab and onSelect(its key),
    // with preventDefault. A tab's id is always `${panelId}-tab` (stable across updates). update() repaints the tabs of
    // keys already shown in place (the same button elements, so focus stays),
    // adds and removes the rest, and scrolls the selected tab into view inside the list (block 'nearest', inline 'nearest').
    // Tabs are built even while `el` is hidden.
  ```
- **Panel contract** (every consumer — Issues here, Kanban in 3a Task 8 — follows it; the component does not touch panels): each panel has `id = panelId`. While `matchMedia('(max-width: 768px)')` matches and `el` is not hidden (two or more columns), every panel has `role="tabpanel"` and `aria-labelledby="${panelId}-tab"`, and every panel but the selected one is `hidden`. Otherwise panels carry no tab role, keep their own label, and none is hidden. A change of the media query repaints; the consumer removes its media listener on cleanup.
- `column-tabs.css` (tokens only): `.column-tabs { display: none; }` and at ≤768px `.column-tabs:not([hidden]) { display: flex; gap: var(--space-micro); overflow-x: auto; overscroll-behavior-x: contain; border-bottom: 1px solid var(--border-subtle); }` `.column-tabs__tab { flex: 0 0 auto; display: inline-flex; align-items: center; gap: var(--space-xs); min-height: 44px; padding: 0 var(--space-sm); border: 1px solid transparent; border-bottom: 0; border-radius: var(--radius-md) var(--radius-md) 0 0; background: transparent; color: var(--foreground-default); font-family: var(--font-narrator); font-weight: var(--font-narrator-weight); font-size: var(--size-narrator-small); white-space: nowrap; cursor: pointer; }` hover `background: var(--ground-15); color: var(--foreground-bold)`; `[aria-selected="true"] { background: var(--signature-glow-strong); border-color: var(--signature-dim); color: var(--foreground-bold); }` `.column-tabs__count` Technical small `--foreground-subtle` (selected: `--foreground-default`).

- [ ] **Step 1: Write the failing tests** (`column-tabs.test.js`, jsdom): three columns → three tabs with the attributes above (each `id` equals `${panelId}-tab`) and counts as text; the selected tab has `tabindex="0"` and the others `-1`; one column → `el.hidden`; click calls `onSelect('b')`; ArrowRight from the last tab focuses the first and calls `onSelect` with its key, `defaultPrevented` true; Home/End; `update()` with a changed count keeps the same button element for that key and its focus; a label `<img src=x onerror=alert(1)>` is text.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/column-tabs.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement**, link, ENFORCED.
- [ ] **Step 4: Run** the unit suite — Expected: PASS; `components/column-tabs.css` 0 violations. (Browser checks are Task 7's.)
- [ ] **Step 5: Commit** — `feat(viewer): column tabs — a phone switcher with counts that keys like a tablist and scrolls inside itself`

---

### Task 7: Issues

**Depends on:** Tasks 1, 3, 5, 6; **3a Task 2 merged** (row-1 count at 390).

Spec §6 Issues; IS-01…IS-08, X-03, X-12; index Review Focus 1, 3, 4, 5.

**Files:**
- Create: `viewer/js/util/issues-filter.js`, `viewer/tests/unit/issues-filter.test.js`, `viewer/tests/issues.mock.spec.js`
- Rewrite: `viewer/js/screens/issues.js`, `viewer/css/screens/issues.css` (what Task 2 left)
- Modify: `viewer/js/util/issues-grouping.js`, `viewer/tests/unit/issues-grouping.test.js`, `viewer/tests/mock-fixtures.js` (append), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/issues.css`)

**Interfaces:**
- Consumes: `issueCard`, `issueRow` (Task 5); `columnTabs` (Task 6); `filterRail`, `labelled`, `keepFocus` (Task 3); `SEVERITY`, `ISSUE_STATUS`, `severityKey` (Task 1); `chipRow`, `filterChip`, `chipClickNext`, `CHIP_CLICK_HINT`, `stateBlock`, `icon`, `claimTopbar`, `setTopbarCount`, `tmSearch`, `tmSegmented`, `api.getIssues`, `store.getIssues/setIssues/getBacklog/getPrefs/subscribe`, `issueEvidence`, `legacyLinksToTyped`.
- Produces:
  ```js
  // util/issues-filter.js
  export function issueSeverity(issue) → 'critical' | 'high' | 'medium' | 'low' | null   // severityKey(issue.severity_label ?? issue.severity)
  export function isResolvedIssue(issue) → boolean   // status fixed, wontfix or duplicate
  export function issueMatchesSearch(issue, term) → boolean
    // term trimmed and lower-cased; '' → true. Haystack: id, title, issueEvidence(issue), component, every location
    // entry, every link target (issue.links when non-empty, else legacyLinksToTyped(issue, 'issue')).
  export function filterIssues(issues, { search = '', severities = [], components = [], promotedOnly = false } = {}) → issue[]
  // util/issues-grouping.js (contract changes)
  export function groupByStatus(issues) → { open, investigating, fixed, wontfix, duplicate }   // arrays; unknown statuses dropped
  export function groupBySeverity(issues) → { critical, high, medium, low }   // keyed by issueSeverity(); unset dropped
  ```
- Screen behaviour (each line is a test):
  1. **Topbar.** Row 2: `tmSearch({ placeholder: 'Search issues…' })` and `labelled({ label: 'View', control: tmSegmented([{ key: 'A', label: 'Hybrid' }, { key: 'B', label: 'Status' }, { key: 'D', label: 'Severity' }, { key: 'C', label: 'List' }], …) })` — the view keys and `prefs.screens.issues.view` are unchanged. Row 1 count via `setTopbarCount`: "<all> issues", or "<all> issues · <shown> visible" while a filter or search is set ("1 issue" in the singular).
  2. **Rail.** `filterRail()` holding: `chipRow({ label: 'Severity', … })` (Critical, High, Medium, Low; count = issues with that severity in the whole list; no "All" chip — an empty selection is all); `chipRow({ label: 'Component', … })` with `data-grow` on its `el`, only while any issue has a component (values sorted, count per component); `filterChip({ label: 'From a bug', value: 'promoted', pressed, count: <issues with promoted_from> })` persisted as `prefs.screens.issues.promotedFromBug` (unchanged key); Clear (resets severities, components, the toggle and the search). Severity and Component clicks go through `chipClickNext`; both rows are built once and `update()`d on every paint.
  3. **Board.** `div.issues-board` → `columnTabs({ label: 'Issue columns', … })` then `div.issues-board__cols` (`overflow-x: auto` inside itself) of `section.issues-col[id="issues-col-<key>"]` (labelled per item 4) → `h2.issues-col__head` (`span.issues-col__name` Technical label + `span.issues-col__count`) + `div.issues-col__list`. Columns per view: Hybrid `investigating`, `open` (cards); Status `open`, `investigating` (cards), `fixed`, `wontfix` (rows), `duplicate` (rows, only when non-empty); Severity `critical`, `high`, `medium`, `low` (active issues, cards with `showStatus: true`); List one column `active` "Open and investigating" (investigating first, then open; `showStatus: true`). Column names come from `ISSUE_STATUS` / `SEVERITY` labels. An empty column shows `p.issues-col__empty` "None".
  4. **Phone.** The columns follow `columnTabs`' panel contract (Task 6) with `panelId` `issues-col-<key>`: at ≤768px with two or more columns each is `role="tabpanel"` labelled by its tab and only the selected one is not `hidden`; otherwise each is labelled by its own head (`aria-labelledby=<h2 id>`), has no tab role and shows. The selected key defaults to the view's first column and is kept per view while the screen is mounted; a tab's count is its column's count. At wider widths the tabs are hidden and all columns show, each at least 280px wide. The `matchMedia` listener is removed in the cleanup.
  5. **Resolved shelf** (Hybrid, Severity, List): `section.issues-shelf` → `h2.issues-shelf__head` holding `button.issues-shelf__toggle[type=button][aria-expanded][aria-controls="issues-shelf-list"]` (`icon('chevron', { size: 14 })` + "Resolved · <n> issue(s)") and `div#issues-shelf-list[hidden]` of `issueRow`s (fixed, wontfix, duplicate). Status view hides the shelf.
  6. **Cards.** `issueCard(i, { tasksIndex, agingCfg: store.getPrefs()?.issues?.aging ?? {}, expanded: expandedIds.has(i.id), onToggleEvidence, showStatus })`; `onToggleEvidence` flips the id in `expandedIds` and repaints; a redraw keeps the expanded set.
  7. **Data.** Always fetch on mount (`api.getIssues({ includeResolved: true })` → `store.setIssues(data.issues ?? [])`), drawing the store's cached list meanwhile when there is one. The screen subscribes to `issues` and `backlog` (blocks counts read the board) and repaints on either through `keepFocus(screen)`. States: loading with no cache → `stateBlock({ state: 'loading', busy: true, headline: 'Loading issues…' })`; failed with no cache → `stateBlock({ state: 'error', label: 'Issues', headline: 'Could not load issues.', action: { label: 'Try again', onClick: load } })`; failed with a cache → the list stays and `div.issues__notice[role="status"]` says "Could not refresh issues — showing the list loaded earlier." with a "Try again" button; none at all → `stateBlock({ label: 'Issues', headline: 'No issues recorded yet.' })`; none match → `stateBlock({ label: 'No matches', headline: 'No issues match these filters.', action: { label: 'Clear filters', onClick: clear } })` in place of the board.
  8. **Cleanup** per Global Constraints (both subscriptions, both chip rows, the `alive` flag).
- `issues.css` (tokens only, rewritten): `.issues { display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; }` `.issues-board__cols { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(280px, 1fr); gap: var(--space-md); overflow-x: auto; min-width: 0; padding-bottom: var(--space-xs); }` `.issues-col { display: flex; flex-direction: column; gap: var(--space-xs); min-width: 0; }` `.issues-col__head { display: flex; align-items: baseline; gap: var(--space-xs); margin: 0; padding-bottom: var(--space-micro); border-bottom: 1px solid var(--border-subtle); }` `.issues-col__name` Technical label voice in `--foreground-default`; `.issues-col__count` Technical small `--foreground-subtle`; `.issues-col__list { display: flex; flex-direction: column; gap: var(--space-xs); }` `.issues-col__empty` Narrator whisper `--foreground-subtle`, `margin: 0`; `.issues-shelf__head { margin: 0; }` `.issues-shelf__toggle` a ghost-style text button in the Technical label voice (`appearance: none; border: 0; background: transparent; color: var(--foreground-default); …`), its icon `transform: rotate(90deg)` when `[aria-expanded="true"]` (static, not hover); `#issues-shelf-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: var(--space-xs); }` `#issues-shelf-list[hidden] { display: none; }` `.issues__notice { display: flex; align-items: center; gap: var(--space-sm); color: var(--foreground-default); font-size: var(--size-narrator-small); }`; at ≤768px `.issues-board__cols { grid-auto-flow: row; grid-auto-columns: auto; overflow-x: visible; }` `.issues-col[hidden] { display: none; }` `.issues-shelf__toggle { min-height: 44px; }`. Nothing else from the old file survives (no `.issues__sev-chip`, `.issues__comp-chip`, `.issue-row__mark`, serif, italic, `--issues-*` properties).
- Fixtures appended below Task 4's block:
  ```js
  // GET /api/issues: the server adds severity_label and aging.
  const issue = (id, title, status, severity, extra = {}) => ({ id, title, status, severity,
    severity_label: { P0: 'Critical', P1: 'High', P2: 'Medium', P3: 'Low' }[severity], aging: { percent: 10, tier: 'Fresh' }, ...extra });
  export const LIST_ISSUES = [
    issue('ISS-001', 'Board poll redraws every card on a quiet tick', 'investigating', 'P1', { component: 'viewer',
      location: ['viewer/js/main.js:121'], related_tasks: ['T-102'], discovered: daysAgo(50), aging: { percent: 90, tier: 'Stale' },
      evidence: Array.from({ length: 8 }, (_, i) => `Line ${i + 1}: the poll answered 200 with an unchanged revision and the board still repainted.`).join(' ') }),
    issue('ISS-002', 'Writer mutex waits without a bound', 'open', 'P0', { component: 'store', related_tasks: ['T-105', 'T-106'],
      promoted_from: ['B-029'], evidence: 'Six seconds per write on a large backlog.', discovered: daysAgo(10) }),
    issue('ISS-003', 'Light theme pills fail contrast', 'open', 'P2', { component: 'viewer', evidence: 'axe: 33 nodes.', discovered: daysAgo(4) }),
    issue('ISS-004', 'Inbox triage skips archived items', 'open', 'P3', { discovered: daysAgo(3) }),
    issue('ISS-005', 'Legacy mirror written after cutover', 'fixed', 'P1', { resolved: daysAgo(6) }),
    issue('ISS-006', 'Search hint shows the Mac glyph on Windows', 'wontfix', 'P3', { resolved: daysAgo(12) }),
    issue('ISS-007', 'Duplicate of the poll repaint', 'duplicate', 'P2', { resolved: daysAgo(1) }),
  ];
  const ISSUE_STATUSES = ['investigating', 'open', 'open', 'open', 'fixed', 'wontfix', 'duplicate', 'investigating'];
  export const LONG_ISSUES = Array.from({ length: 24 }, (_, i) => issue(`ISS-${1201 + i}`, longText(`Issue ${1201 + i}`, { unbroken: i === 3 }),
    ISSUE_STATUSES[i % 8], `P${i % 4}`, { component: ['viewer', 'store', 'sync'][i % 3], related_tasks: [`T-${1234 + i}`],
      location: [`viewer/js/screens/a-rather-long-module-name-${i}.js:${100 + i}`], evidence: longText(`Evidence ${i}`).repeat(3),
      discovered: daysAgo(5 + i), resolved: ISSUE_STATUSES[i % 8] === 'open' || ISSUE_STATUSES[i % 8] === 'investigating' ? undefined : daysAgo(i) }));
  // The table plan 4's a11y gate reuses for #/issues; loaded when `.issues-col .issue-card` is visible.
  export const issuesMocks = ({ theme = 'dark' } = {}) => ({ '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/issues': { issues: LIST_ISSUES }, '/api/board': BOARD, '/api/backlog': BOARD,
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1', RICH_RELATED) });
  ```

- [ ] **Step 1: Write the failing unit tests.** `issues-filter.test.js`: `issueSeverity({ severity_label: 'High' })` → `'high'`, `({ severity: 'P0' })` → `'critical'`, `({})` → null; `issueMatchesSearch` finds by id, title, evidence, legacy `symptom`, component, a location entry, a typed link target and a legacy link target, case-insensitively; `''` matches everything; `filterIssues` with severities `['high']`, components `['store']`, `promotedOnly` (only ISS-002), and combined (AND across groups, OR within a group); `isResolvedIssue` for each status. `issues-grouping.test.js`: rewrite to the new keys (`duplicate` included; severity keys lower-case; an issue with only `severity: 'P2'` lands in `medium`).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/issues-filter.test.js viewer/tests/unit/issues-grouping.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement** the util changes, the screen, the CSS, the fixtures, ENFORCED.
- [ ] **Step 4: Write the mocked tests** `issues.mock.spec.js` (`mockApi(page, { ...issuesMocks(), <test overrides> })`; tests wait for `.issues-col .issue-card` unless they test a state; record prefs PUTs; `afterEach`: `unmockedWrites` empty, no `pageerror`). These replace the coverage of the live `issues.spec.js`:
  - "the Hybrid board shows Investigating and Open as columns, resolved issues on a shelf": two `section.issues-col` with heads "Investigating" and "Open" and counts 1 and 3; the shelf toggle reads "Resolved · 3 issues", `aria-expanded="false"`; Enter on it → `true` and three rows with "Fixed", "Won't fix", "Duplicate".
  - "a severity is a word and a shape": every `.issue-card__line .marker__word` is one of Critical, High, Medium, Low; no `P0`…`P3` text on the page.
  - "a card opens its issue and its task link opens the task": click ISS-003's evidence text → `location.hash` `#/issue/ISS-003`; back on `#/issues`, click ISS-001's "T-102" → the detail modal for T-102 opens and the hash is not `#/issue/ISS-001`.
  - "search and the chips filter, and say how many": search `mutex` → only ISS-002, `#topbar-count` "7 issues · 1 visible"; clear; click "store" in Component → ISS-002 only; press "From a bug" → ISS-002 only and the prefs PUT carries `promotedFromBug: true`; "Clear filters" → all again and the button is hidden.
  - "a severity at zero is disabled but a pressed one can be released": with `LIST_ISSUES` minus ISS-002 (no Critical), the Critical chip is disabled; press "High", then make the store lose every High issue (`store.setIssues` via `page.evaluate`) → the High chip is still enabled and pressed; click it → released.
  - "the View switcher is labelled and remembered": `getByRole('group', { name: 'View' })` holds four buttons; click "Severity" → columns Critical, High, Medium, Low and the prefs PUT `screens.issues.view` `'D'`; Status view shows a fifth column "Duplicate" only while a duplicate exists.
  - **"evidence expands from the keyboard and stays expanded and focused through the board poll's redraw"** (Review Focus 2): focus ISS-001's link, press Tab → focus on its "Show all" (`aria-expanded="false"`, `aria-controls` names an element whose text starts "Line 1:"); Enter → `aria-expanded="true"`, text "Show less", the evidence box is taller than before and lacks `truncate--3`; run the board-poll step (`page.evaluate(() => import('/js/store.js').then(({ store }) => { const next = structuredClone(store.getBacklog()); next.revision = 'r-poll'; store.setBoard(next); }))`) → the toggle is still `aria-expanded="true"` and is `document.activeElement`; Space → collapsed. ISS-003's short evidence shows no toggle.
  - **"at 390 the board is one column behind tabs with counts, and nothing scrolls sideways"** (Review Focus 1): 390×844, `'/api/issues': { issues: LONG_ISSUES }`: `getByRole('tablist', { name: 'Issue columns' })` holds "Investigating 6" and "Open 9" (`.column-tabs__label` + `.column-tabs__count`); exactly one `.issues-col` is visible, it is the selected tab's `aria-controls`, it has `role="tabpanel"` and its `aria-labelledby` is that tab's id; ArrowRight → the Open tab is selected and focused and the Open column is the visible one; Tab from the tab lands on that column's first card link; every tab, chip, `.overflow-more`, `.issues-shelf__toggle`, card link and `.issue-card__more:not([hidden])` is ≥44px tall; switch to Status (open Filters if the View group is parked, then click "Status") → five tabs and `getByRole('tablist').evaluate(el => el.scrollWidth > el.clientWidth || true)` (it may scroll inside itself) while `document.documentElement.scrollWidth <= innerWidth`; page height `document.scrollingElement.scrollHeight` ≤ 8000 on every view (report the four numbers).
  - "at 1440 every column shows, each at least 280px wide": Status view with `LONG_ISSUES` → no visible tablist, no `[role="tabpanel"]` and no hidden column, five columns each `getBoundingClientRect().width >= 280`, `document.documentElement.scrollWidth <= innerWidth` (the columns scroll inside `.issues-board__cols`).
  - keyboard walk: from the search field, Tab order is search → the View group's four buttons → the Severity chips → Component chips (or its More) → "From a bug" → the first column's first card link → its "Show all" (ISS-001) → "T-102" → the next card link. Escape inside the More popover closes only it and puts focus on More.
  - **"leaving Issues while its issues are still loading leaves nothing behind"** (Review Focus 5): hold `/api/issues` (`page.route` that stores the route and does not answer), go to `#/issues` (loading state visible), `location.hash = '#/kanban'`, then fulfil the held route with `LIST_ISSUES`; no `.issue-card` and no `.issues` anywhere, no page error; then `store.setIssues(LIST_ISSUES)` via `page.evaluate` → still no `.issue-card`.
  - "returning to Issues reads them again": visit `#/issues`, go to `#/kanban`, change the route's answer to add ISS-008, come back → ISS-008 shows (count the GETs: 2).
  - "a failed load is said in words": `/api/issues` → 500 with a `sqlite3` error → "Could not load issues." and none of `500`, `/api`, `sqlite3`, `{`; "Try again" with a good second answer → the board.
  - axe on `#screen-mount` and `#topbar` in dark and light, Hybrid view with the shelf open: zero `color-contrast`, `nested-interactive`, `scrollable-region-focusable`, `aria-*`, `heading-order`; computed `box-shadow` of `.issue-card` and `.issues-col` is `none`.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2 issues.mock.spec.js shell.mock.spec.js router.mock.spec.js` — Expected: PASS; `screens/issues.css` enforced, 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): the Issues board — columns that never collapse and scroll inside themselves, one column at a time behind tabs on a phone, severity and component chips that never wrap, a labelled View switcher, a real resolved shelf, and a list that reloads and keeps focus when the board changes`

---

### Task 8: Tag filter popover

**Depends on:** nothing.

Spec §6 Ideas ("Tags" popover, searchable multi-select), §5.8; IE-01, IE-03; the "UX"/"ux" carry.

**Files:**
- Create: `viewer/js/components/tag-filter.js`, `viewer/css/components/tag-filter.css`, `viewer/tests/unit/tag-filter.test.js`, `viewer/tests/tag-filter.mock.spec.js`
- Modify: `viewer/index.html` (link after `css/components/column-tabs.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/tag-filter.css`)

**Interfaces:**
- Consumes: `openPopover` (dismissal, placement, arrow keys over `[data-popover-item]`), `h()`.
- Produces:
  ```js
  export function tagKey(tag) → string   // String(tag ?? '').trim().toLowerCase()
  export function collectTags(items) → [{ key, label, count }]
    // every item's `tags`, grouped by tagKey (empty keys dropped); label = the spelling used most (ties: first seen);
    // count = items carrying it; sorted by count descending, then label (localeCompare, sensitivity 'base').
  export function tagFilter({ label = 'Tags', getTags, selected = [], onChange }) → {
    el,            // button.btn.btn--secondary.btn--sm.tag-filter[type=button]: span label + span.tag-filter__on "· n" while
                   // n > 0; aria-label "Tags" or "Tags, n selected"
    update(),      // repaint from getTags(): the button, and the open list in place (search text, choices, focus by key kept)
    selected(),    // → string[] of keys, in the order chosen
    clear(),       // empties the selection, repaints, calls onChange([])
  }
  ```
- Behaviour (each line is a test):
  1. A click on `el` opens `openPopover({ anchor: el, content, role: 'dialog', label: 'Filter by tag', focus: 'first', className: 'tag-filter__popover' })`; a second click closes it. The content: `input.tag-filter__search[type=search][aria-label="Find a tag"][placeholder="Find a tag…"]`, `div.tag-filter__list[role="group"][aria-label="Tags"]` of `label.tag-filter__option` → `input[type=checkbox][value=<key>][data-popover-item]` + `span.tag-filter__name` (label) + `span.tag-filter__count`, then `p.tag-filter__none[role="status"]` (empty unless no choice matches), then `button.btn.btn--ghost.btn--sm.tag-filter__clear` "Clear tags" (hidden while nothing is selected).
  2. Focus lands in the search box. Typing filters the choices (case-insensitive `includes` on the label): a choice that does not match is `hidden` and its checkbox `disabled` (so the popover's arrow keys skip it); none matching → `p.tag-filter__none` reads `No tag matches “<q>”.`.
  3. ArrowDown in the search box focuses the first enabled checkbox (`preventDefault`); arrows among the checkboxes are the popover's; Space toggles; every change calls `onChange(selected())` and repaints the button; focus stays on the checkbox.
  4. Escape (in the box or on a checkbox) closes the popover and returns focus to `el` (the popover's own rule).
  5. `update()` while open rebuilds the choices from `getTags()`, keeps the search text and its filtering, keeps the checked keys (a selected key no longer in the data stays listed, count 0, so it can be released), and puts focus back on the checkbox of the same key when focus was on one.
- `tag-filter.css` (tokens only): `.tag-filter__on { color: var(--foreground-default); }` `.tag-filter__popover { width: min(320px, calc(100vw - 8px)); gap: var(--space-xs); }` `.tag-filter__search` takes the field look (`box-sizing: border-box; width: 100%; min-height: 32px; padding: var(--space-xs) var(--space-sm); border: 1px solid var(--field-border); border-radius: var(--radius-md); background: var(--bg-recessed); color: var(--foreground-bold); font-family: var(--font-narrator); font-size: var(--size-narrator-small);`) `.tag-filter__list { display: flex; flex-direction: column; max-height: min(280px, 45vh); overflow-y: auto; }` `.tag-filter__option { display: flex; align-items: center; gap: var(--space-xs); min-height: 32px; padding: 0 var(--space-xs); border-radius: var(--radius-sm); color: var(--foreground-default); cursor: pointer; }` `.tag-filter__option:hover { background: var(--overlay-surface-hover); color: var(--foreground-bold); }` `.tag-filter__option[hidden] { display: none; }` `.tag-filter__name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-technical); font-size: var(--size-technical-small); }` `.tag-filter__count` Technical small `--foreground-subtle`; `.tag-filter__none:empty { display: none; }` `.tag-filter__clear[hidden] { display: none; }`; at ≤768px `.tag-filter__option, .tag-filter__search, .tag-filter__clear, .tag-filter { min-height: 44px; }`. The checkbox uses the browser's own box with `accent-color: var(--signature-fill)`.

- [ ] **Step 1: Write the failing unit tests** (`tag-filter.test.js`, jsdom): `tagKey(' UX ') === 'ux'`; `collectTags([{ tags: ['UX', 'perf'] }, { tags: ['ux'] }, { tags: ['ux', ''] }])` → `[{ key: 'ux', label: 'ux', count: 3 }, { key: 'perf', label: 'perf', count: 1 }]`; the button's `aria-label` with two selected is "Tags, 2 selected" and shows "· 2"; opening (jsdom `click`) lists one checkbox per tag; typing `pe` (set `value` + `input` event) hides `ux` and disables its checkbox; checking `perf` calls `onChange(['perf'])`; `update()` with new data keeps `perf` checked and the search text; a selected key missing from new data stays with count 0; a tag `<img src=x onerror=alert(1)>` is text.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/tag-filter.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement**, link, ENFORCED.
- [ ] **Step 4: Write the mocked tests** `tag-filter.mock.spec.js` (harness like `chips.mock.spec.js`: boot `#/settings`, import `/js/components/tag-filter.js`, mount the button into a `div` in `#screen-mount`; `getTags` returns `collectTags` of 42 items carrying tags `tag-01`…`tag-40` plus `UX` on one and `ux` on another; `onChange` records into `window.__tags`):
  - **"forty tags: searchable, scrolls inside, keyboard from search to choices, Escape back to Tags"** (Review Focus 3): click Tags → `getByRole('dialog', { name: 'Filter by tag' })` visible, the search box focused; 41 checkboxes (one `ux`); the list's `scrollHeight > clientHeight` and the popover's box is inside the viewport at 1440×900 and at 390×844; type `3` → visible choices `tag-03`, `tag-13`, `tag-23`, `tag-30`…`tag-39` (13); ArrowDown → `tag-03`'s checkbox focused; Space → `window.__tags` `['tag-03']` and the button reads "· 1"; ArrowDown → `tag-13` (hidden ones skipped); Escape → the dialog is gone and focus is on the Tags button; type `zzz` after reopening → "No tag matches “zzz”."
  - at 390×844 every visible option, the search box and the Tags button are ≥44px tall.
  - axe on the open popover (`scope: body`) in dark and light: zero violations; computed `box-shadow` of `.popover` is `none`.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2 tag-filter.mock.spec.js popover.mock.spec.js` — Expected: PASS; `components/tag-filter.css` 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): a Tags filter on the shared popover — searchable, one entry per tag whatever its case, keyboard from the search box to the choices`

---

### Task 9: Ideas

**Depends on:** Tasks 1, 3, 8; **3a Task 2 merged** (row-1 count and the "New idea" primary at 390 — icon only at ≤768px).

Spec §6 Ideas; IE-01…IE-05, X-03, X-07, X-12; the 3d Ideas carries.

**Files:**
- Create: `viewer/js/util/ideas-filter.js`, `viewer/tests/unit/ideas-filter.test.js`, `viewer/tests/ideas.mock.spec.js`
- Rewrite: `viewer/js/screens/ideas.js`, `viewer/css/screens/ideas.css`
- Modify: `viewer/tests/mock-fixtures.js` (append), `viewer/tests/shell.mock.spec.js` (the test "Filters counts the controls it holds; an empty chip group is neither parked nor counted", today ~line 605, which reads `#/ideas`' old row-2 chip groups), `viewer/tests/ideas-form.mock.spec.js` (`openCreate`: "New idea" is in row 1, so the Filters step goes), `viewer/tests/tools/capture-modals.mjs` (`openIdeas`: the button is looked up as `page.locator('#topbar-primary [aria-label="Create a new idea"]')` after `settleRow(page)` — the only line changed), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/ideas.css`)

**Interfaces:**
- Consumes: `IDEA_STATUS`, `statusMarker`, `statusMeta` (Task 1); `filterRail`, `keepFocus` (Task 3); `tagFilter`, `tagKey`, `collectTags` (Task 8); `chipRow`, `filterChip`, `chipClickNext`, `CHIP_CLICK_HINT`, `linkRow`, `truncate`, `stateBlock`, `formatStamp`, `mountMarkdown`, `linkPillsEl`, `linkRoute`, `legacyLinksToTyped`, `icon`, `claimTopbar`, `claimTopbarPrimary`, `setTopbarCount`, `tmSearch`, `tmAction`, `api.get`, `openIdeaCreateModal`.
- Produces:
  ```js
  // util/ideas-filter.js — ideas.js re-exports applyIdeasFilters (ideas-screen.test.js imports it from there)
  export function ideaMatchesSearch(idea, term) → boolean   // id, title, body, status, tags; case-insensitive; '' → true
  export function applyIdeasFilters(ideas, { statuses = [], tags = [], includeArchived = false, search = '' } = {}) → idea[]
    // as today (statuses OR, tags AND, newest created first) except tags compare by tagKey and `search` is applied
  export function ideaStatusChips(ideas, pressed) → [{ value, label, count, pressed }]
    // statuses present (IDEA_STATUS order, then unknown alphabetically with statusMeta('idea', v).label), plus pressed ones at 0
  ```
- Screen behaviour (each line is a test):
  1. **Topbar.** Row 1: `claimTopbarPrimary().append(tmAction({ icon: 'plus', label: 'New idea', variant: 'primary', title: 'Create a new idea', onClick }))`; count via `setTopbarCount` ("<all> ideas", or "<all> ideas · <shown> visible" while a filter or search is set; "1 idea" in the singular). Row 2: `tmSearch({ placeholder: 'Search ideas…' })` only.
  2. **Rail.** `chipRow({ label: 'Status', chips: ideaStatusChips(…) })` (counts over the ideas the archived toggle admits), `tagFilter({ getTags: () => collectTags(<ideas the archived toggle admits>), onChange })`, `filterChip({ label: 'Show archived', value: 'archived', pressed, count: <archived ideas> })`, Clear (statuses, tags, archived, search).
  3. **Rows.** `ul.ideas__list[aria-label="Ideas"]` of `linkRow({ tag: 'li', className: 'idea-row' + (archived ? ' idea-row--archived' : ''), href: linkRoute(id) /* #/ideas/<id> */, name: [span.idea-row__id, truncate(title || 'Untitled', { lines: 2, className: 'idea-row__title' })], content: [statusMarker('idea', status) only when status is set, span.idea-row__tags (the first three tags as `span.list-tag` + `span.idea-row__more` "+n" whose title lists the rest), span.list-tag "Archived" when archived, time.idea-row__age (formatStamp(created))] })`.
  4. **Selecting.** A plain left click on a row's link (no modifier, `button === 0`) is `preventDefault()`ed, the hash becomes `#/ideas/<id>` through `history.replaceState(history.state, '', …)`, the row's link gets `aria-current="true"` (the previous one loses it) and the detail pane shows that idea. Modified clicks are the browser's. Mounted at `#/ideas/<id>` (`subpath[0]`), the screen selects that idea once the list has loaded; an id not in the list shows in the pane `stateBlock({ state: 'missing', label: 'Not found', headline: '<id> is not in this project.' })`.
  5. **Phone.** At ≤768px (`matchMedia('(max-width: 768px)')`) the pane replaces the list while an idea is selected; selecting moves focus to the pane's `h2` (`tabindex="-1"`); its `button.btn.btn--ghost.btn--sm.ideas-detail__back` (`icon('chevron', { size: 14 })` turned to point left by CSS, "Back to ideas") deselects, sets the hash to `#/ideas` by `replaceState`, and focuses that idea's row link. At wider widths focus stays on the row.
  6. **Detail pane** `section.ideas-detail[aria-labelledby=<h2 id>]`: a Technical line (`span.ideas-detail__id`, the status marker when set, `span.list-tag` "Archived"), `h2.ideas-detail__title`, the body through `mountMarkdown(div.ideas-detail__body.md, idea.body)` or `p.ideas-detail__empty` "No description." (`--foreground-subtle`, upright), and a side column: `dl.ideas-detail__dl` with Created and Updated (`formatStamp` text, absolute date in `title`), Status (marker) and Tags (`span.list-tag`s); "Links" with `linkPillsEl(idea.links?.length ? idea.links : legacyLinksToTyped(idea, 'idea'))`; "Promoted to" as `a.link-pill[href=linkRoute(promoted_to)]`. Side headings are `h3` in the Technical label voice. No `innerHTML` other than through `mountMarkdown`.
  7. **Data.** `loadIdeas()` = `api.get('/api/ideas?archived=true&summary=false')` → `store.setIdeas(data?.ideas ?? (Array.isArray(data) ? data : []))`; an error with `code === 404` counts as no ideas. Started on mount (not awaited) and after a create (`onCreated: loadIdeas`). The screen subscribes to `ideas` and repaints through `keepFocus(screen)`, then `tags.update()`. States: loading with no cache, failed with no cache (`'Could not load ideas.'` + "Try again"), failed with a cache (`div.ideas__notice[role="status"]` "Could not refresh ideas — showing the list loaded earlier." + "Try again"), none at all (`stateBlock({ label: 'Ideas', headline: 'No ideas yet.', hint: 'Use “New idea” to capture one.' })`), none match (`'No ideas match these filters.'` + "Clear filters").
  8. **Cleanup** per Global Constraints; it also closes an open Tags popover (`popover` close happens with its anchor's removal; assert it).
- `ideas.css` (tokens only, rewritten): `.ideas { display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; }` `.ideas__content { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-lg); min-width: 0; }` `.ideas__content--detail-open { grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); }` `.ideas__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-xs); min-width: 0; }` `.idea-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--space-xs) var(--space-md); padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); }` `.idea-row:has(> .link-row__link[aria-current="true"]) { background: var(--card-bg-hover); border-color: var(--border-strong); }` `.idea-row > .link-row__link { display: grid; grid-template-columns: minmax(9ch, max-content) minmax(0, 1fr); align-items: baseline; column-gap: var(--space-sm); min-width: 0; color: var(--foreground-bold); text-decoration: none; }` `.idea-row__id` Technical small nowrap `--foreground-default`; `.idea-row__title` Narrator small 600 `overflow-wrap: anywhere`; `.idea-row > .link-row__content { display: flex; align-items: center; gap: var(--space-xs); min-width: 0; white-space: nowrap; }` `.idea-row__tags { display: inline-flex; gap: var(--space-micro); min-width: 0; }` `.idea-row__more, .idea-row__age` Technical small `--foreground-subtle`; `.ideas-detail` on `--card-bg` with a `--border-subtle` border and `--radius-lg`, `padding: var(--space-md) var(--space-lg)`; `.ideas-detail__title` Narrator header (`--size-narrator-large`, 700, `--foreground-bold`, upright, `overflow-wrap: anywhere`); `.ideas-detail__grid { display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: var(--space-lg); }` `.ideas-detail__back { display: none; }` `.ideas-detail__back .icon { transform: rotate(180deg); }` (static); `.ideas__notice` as Issues'; at ≤768px `.ideas__content--detail-open { grid-template-columns: minmax(0, 1fr); }` `.ideas__content--detail-open .ideas__list { display: none; }` `.ideas-detail__back { display: inline-flex; min-height: 44px; }` `.ideas-detail__grid { grid-template-columns: minmax(0, 1fr); }` `.idea-row { grid-template-columns: minmax(0, 1fr); }` `.idea-row > .link-row__link { min-height: 44px; }` `.idea-row__age { display: none; }`. Nothing from the old file survives (no `rgba`, `--ink*`, serif, italic, `.ideas__status-chip`, `.ideas__tag-chip`, `.ideas__archived-chip`, `.idea-row__status-pill`).
- Fixtures appended below Task 7's block:
  ```js
  export const LIST_IDEAS = [
    { id: 'IDEA-1', title: 'Board swimlanes by epic', status: 'exploring', tags: ['UX', 'board'], created: daysAgo(5),
      body: 'Group the **board** by epic.\n\n- fold a lane\n- keep the counts', links: [{ type: 'relates_to', target: 'ISS-001' }] },
    { id: 'IDEA-2', title: 'Faster store writes', status: 'candidate', tags: ['perf'], created: daysAgo(4) },
    { id: 'IDEA-3', title: 'Phone layout for the table', status: 'parking-lot', tags: ['ux', 'mobile', 'table', 'layout', 'phone'], created: daysAgo(3) },
    { id: 'IDEA-4', title: 'A note with no status yet', tags: [], created: daysAgo(2) },
    { id: 'IDEA-5', title: 'Retire the JSON mirror', status: 'promoted', promoted_to: 'T-111', tags: ['store'], created: daysAgo(30), archived: true },
  ];
  export const LONG_IDEAS = Array.from({ length: 31 }, (_, i) => ({
    id: `IDEA-${1201 + i}`, title: longText(`Idea ${1201 + i}`, { unbroken: i === 2 }),
    status: [undefined, 'exploring', 'candidate', 'parking-lot', 'promoted', 'dropped'][i % 6],
    tags: Array.from({ length: (i % 5) + 1 }, (_, k) => `tag-${String(((i + k) % 40) + 1).padStart(2, '0')}`), created: daysAgo(i + 1),
  }));
  // The table plan 4's a11y gate reuses for #/ideas; loaded when `.ideas__list .idea-row` is visible.
  export const ideasMocks = ({ theme = 'dark' } = {}) => ({ '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/ideas': { ideas: LIST_IDEAS }, '/api/board': BOARD, '/api/backlog': BOARD,
    '/api/task/T-111/detail': taskDetail({ ...DONE_TASK, id: 'T-111' }) });
  ```

- [ ] **Step 1: Write the failing unit tests** (`ideas-filter.test.js`): `applyIdeasFilters(LIST_IDEAS, { tags: ['ux'] })` → IDEA-3, IDEA-1 (both spellings; newest first); `{ tags: ['ux', 'board'] }` → IDEA-1; `{ search: 'SWIMLANES' }` → IDEA-1; archived IDEA-5 only with `includeArchived`; `ideaStatusChips(nonArchived, [])` values `['exploring', 'candidate', 'parking-lot']` (IDEA-4 has none and adds no chip); with pressed `['dropped']` it is listed at 0. `ideas-screen.test.js` passes unchanged.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/ideas-filter.test.js viewer/tests/unit/ideas-screen.test.js` — Expected: the new file FAILS.
- [ ] **Step 3: Implement** the util, the screen, the CSS, the fixtures, the two test-harness edits, ENFORCED.
- [ ] **Step 4: Write the mocked tests** `ideas.mock.spec.js` (`mockApi(page, { ...ideasMocks(), <test overrides> })`; tests wait for `.ideas__list .idea-row` unless they test a state; record prefs PUTs and `/api/ideas` GETs; `afterEach`: `unmockedWrites` empty, no `pageerror`, no native dialog):
  - "rows are links with markers, tags and age; an idea with no status has no marker": IDEA-1's row link `href` `#/ideas/IDEA-1`, its status word "Exploring"; IDEA-4's row has no `.marker`; IDEA-3 shows three tags and "+2" whose `title` lists "layout" and "phone".
  - "a click selects in place and the address follows": click IDEA-2 → the pane's heading "Faster store writes", `location.hash` `#/ideas/IDEA-2`, the GET count unchanged (no remount), IDEA-2's link `aria-current="true"`; Ctrl+click IDEA-3 opens a new page (`context.waitForEvent('page')`) and leaves the pane on IDEA-2.
  - "a pasted address opens that idea; an unknown one says so": `goto('/#/ideas/IDEA-1')` → pane shows IDEA-1 with the body rendered (a `strong` "board" and a two-item list) and a link pill to `#/issue/ISS-001`; `/#/ideas/IDEA-99` → "IDEA-99 is not in this project."
  - "status chips, Tags and Show archived filter together": click "Candidate" → IDEA-2 only; Clear; Tags → check the `ux` choice (labelled "UX", the first spelling seen) → IDEA-3 and IDEA-1; "Show archived" → IDEA-5 appears with an "Archived" tag; select it and its "Promoted to" link opens T-111's detail modal (mock `'/api/task/T-111/detail': taskDetail({ ...DONE_TASK, id: 'T-111' })`).
  - **"a list redraw while Tags is open keeps it open, its choices and its focus"** (Review Focus 3, index 4): open Tags, check `ux`, focus `perf`'s checkbox; `page.evaluate` `store.setIdeas([...LIST_IDEAS, { id: 'IDEA-6', title: 'Board density', status: 'exploring', tags: ['board'], created: <now> }])` → the dialog "Filter by tag" is still open, `ux` still checked, `perf`'s checkbox focused, `board`'s count reads 2, and the list shows IDEA-1 and IDEA-3 only.
  - **"leaving Ideas with Tags open leaves nothing behind"** (Review Focus 5): open Tags, `location.hash = '#/kanban'` → no `.popover`, no `.tag-filter__popover`, no `.ideas`, no page error, and the Kanban search field visible.
  - "New idea is in row 1 and a created idea appears": `#topbar-primary` holds `getByRole('button', { name: 'Create a new idea' })`; create "Faster board" (route answering `{ ok: true, id: 'IDEA-9' }` and a second GET including it) → the row appears and `#topbar-count` goes from "4 ideas" to "5 ideas".
  - keyboard walk: from the search field, Tab order is search → the Status chips → Tags → "Show archived" → IDEA-4's row link (newest) → IDEA-3's → IDEA-2's; Enter on IDEA-2's link selects it and focus stays on it at 1440.
  - phone: at 390×844 Enter on IDEA-1's link → the list is hidden, focus is on the pane's heading; "Back to ideas" → the list is back, the hash is `#/ideas`, focus on IDEA-1's link.
  - "a failed load is said in words": `/api/ideas` 500 → "Could not load ideas." and none of `500`, `/api`, `sqlite3`, `{`; a 404 → "No ideas yet."
  - at 390×844 with `LONG_IDEAS`: no sideways scroll (document and `#screen-mount`), page height ≤ 8000 (report it), every chip, More, Tags, Show archived, Clear, row link and "New idea" ≥44px tall.
  - axe on `#screen-mount` and `#topbar` with IDEA-1 selected, dark and light: zero `color-contrast`, `nested-interactive`, `aria-*`, `heading-order`, `list`, `listitem`.
- [ ] **Step 4b: Rewrite the shell test that read Ideas' old row 2.** In `shell.mock.spec.js`, "Filters counts the controls it holds; an empty chip group is neither parked nor counted" asserted `#topbar-actions > .ideas__status-chips:empty`; after this task Ideas' row 2 holds only the search, so Filters never shows there. Keep its intent with probe controls (still inside the 390×844 describe): `mockApi(page, withContent())`, `goto('/#/settings')`, wait for the screen, then `page.evaluate` appends to `#topbar-actions`, before its Filters button, a `div.tm-chip-row.probe-empty` (no children) and six `button.btn.btn--secondary.probe` labelled "Probe control 1"…"Probe control 6" (`style="width: 120px"`); wait for `rowSettled(page)`. Assert: Filters is visible; the empty group stays a child of `#topbar-actions` (`#topbar-actions > .probe-empty` count 1, not parked); open Filters; every item in `.overflow-list` has a width > 0, none is `.probe-empty`, and `.overflow-more__count` equals the number of items. Leave with `location.hash = '#/kanban'` so the probes go with the screen. The test keeps its name.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2 ideas.mock.spec.js ideas-form.mock.spec.js shell.mock.spec.js` — Expected: PASS; `grep -n "window.marked\|innerHTML\|fetch(" <wt>/viewer/js/screens/ideas.js` → no match; `screens/ideas.css` enforced, 0 violations; `node <wt>/viewer/tests/tools/capture-modals.mjs <scratch> --only=ideas-create --port=8834` exits 0.
- [ ] **Step 6: Commit** — `feat(viewer): the Ideas list — link rows that select in place and deep-link, status markers only when set, status chips, a Tags filter, a real Show archived toggle, New idea in row 1, the detail pane in markdown and markers, and its states in words`

---

### Task 10: Verification

**Depends on:** Tasks 1–9.

**Files:**
- Modify: `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11, appended), `viewer/tests/tools/capture-modals.mjs` (new scenes appended to `ALL_SCENES`; new keys appended to `TABLE`; still mocked and static-served), `viewer/tests/mock-fixtures.js` (only if Step 6 finds a gap in the three `*Mocks()` functions Tasks 4, 7 and 9 added)

- [ ] **Step 1: Scenes.** Import `LIST_ISSUES`, `LONG_ISSUES`, `LIST_BUGS`, `LONG_BUGS`, `LIST_IDEAS`, `LONG_IDEAS` from the fixtures (through the existing `F` namespace). Append `'/api/issues': { issues: F.LIST_ISSUES }` to `TABLE`. Each bugs and ideas scene passes `routes` that answer `**/api/bugs*` / `**/api/ideas*` GETs with its list (registered after `bugsByTask`, so it wins). Append:
  - `issues-board` — `#/issues`, Hybrid, shelf open, `scope: '#screen-mount'`;
  - `issues-status` — Status view (five columns at 1440, tabs at 390), `scope: '#screen-mount'`;
  - `issues-evidence` — ISS-001's evidence expanded, `scope: '#screen-mount'`;
  - `issues-long` — `LONG_ISSUES`, `fullPage: true`, `scope: '#screen-mount'`;
  - `bugs-list` — `#/bugs` with `LIST_BUGS`, "Show archived" pressed, `scope: '#screen-mount'`;
  - `bugs-long` — `LONG_BUGS`, `fullPage: true`;
  - `ideas-list` — `#/ideas/IDEA-1` with `LIST_IDEAS` (pane open at 1440; pane alone at 390), `scope: '#screen-mount'`;
  - `ideas-tags` — Tags open with `ux` checked, `scope: 'body'`;
  - `ideas-long` — `LONG_IDEAS`, `fullPage: true`.
- [ ] **Step 2: Capture.** `node <wt>/viewer/tests/tools/capture-modals.mjs C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-3d-issues-bugs-ideas/shots-10 --port=8834` (all scenes, both themes, both widths; no mocked run on 8834 meanwhile). LOOK at every new image and at `ideas-create` and `ideas-create-error` (Task 9 moved their button). In the report, describe each new image plainly (what is where, what is cut, what reads in light), and fix what is wrong inside 3d's files with a test where one makes sense.
- [ ] **Step 3: Measure.** From `metrics.json`: `overflowX` is 0 on every 3d scene; axe lists no `color-contrast`, `nested-interactive`, `scrollable-region-focusable`, `label`, `select-name` or `aria-*` violation on any 3d scene in either theme. In a scratch Playwright script (not committed) or the mocked specs already written, record the page heights at 390 of `issues-long` (each view), `bugs-long`, `ideas-long` — each ≤ 8000px — and report the numbers.
- [ ] **Step 4: Keyboard pass.** On each of `#/issues`, `#/bugs`, `#/ideas` at 1440, Tab from the search field to the last row with no pointer: every stop is visible (ring on the control or its row), nothing is skipped that a pointer can click, Escape closes only the topmost thing. Report any stop the mocked keyboard walks do not already pin.
- [ ] **Step 5: Full runs.** `npm --prefix <wt>/viewer run test:unit`; `MOCK_PORT=8834 npm --prefix <wt>/viewer run test:mock -- --workers=2` (whole mocked suite); `timeout 900 env --chdir=<wt> C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider`. Report exact pass/fail/skip counts for each (count the pytest dots and failures if the process hangs after 100%). The style-rules report lists `screens/issues.css`, `screens/bugs.css`, `screens/ideas.css` as enforced and none of 3d's files in the backlog. Port 8834 has no listener at the end (`netstat -ano | grep 8834` empty).
- [ ] **Step 6: Mocks plan 4 reuses** (index Global Constraint "Every screen has a mocked spec plan 4 can reuse"). Confirm each route ends plan 3 with a `*.mock.spec.js` whose base `mockApi` table is a named export of `mock-fixtures.js`, and a "content is loaded" selector that matches real content and never a `stateBlock` (`.tm-empty`): `#/bugs` → `bugsMocks()`, `.bugs__list .bug-row` (`bugs.mock.spec.js`); `#/issues` → `issuesMocks()`, `.issues-col .issue-card` (`issues.mock.spec.js`); `#/ideas` → `ideasMocks()`, `.ideas__list .idea-row` (`ideas.mock.spec.js`). Each takes `{ theme = 'dark' } = {}` and sets the prefs theme from it. Check by a scratch Playwright run per route and theme: `mockApi(page, xMocks({ theme }))`, the `html` element's `data-theme` equals `theme`, `goto`, the selector visible within 5 s, zero `.tm-empty`. Fix any gap in `mock-fixtures.js`. List function + selector per route in the report.
- [ ] **Step 7: Spec §11.** Append to the end of `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §11 (append-only; never edit another track's bullet) one bullet per "Spec reading" of this plan as it shipped, each starting `- **§6 Issues (plan 3d).**`, `- **§6 Bugs (plan 3d).**`, `- **§6 Ideas (plan 3d).**` or `- **§5.1 (plan 3d).**` as fits, plus any ruling made during execution.
- [ ] **Step 8: Commit** — `git -C <wt> add viewer/tests/tools/capture-modals.mjs viewer/tests/mock-fixtures.js` then `test(viewer): capture Issues, Bugs and Ideas in both themes and widths, with long data`; separately `git -C <wt> add docs/specs/2026-10-01-viewer-reality-reprojection-design.md` then `docs(viewer): spec §11 records plan 3d's rulings`
