<!-- User intent: finish the shared components every screen will stand on — one popover, filter chips that never wrap, real links for rows and cards, sortable headers, the Ideas form on the shared form, and a conflict banner that reads and works like the rest — so plan 3 rebuilds screens from parts instead of one-offs. -->

# Viewer × Reality Reprojection — Plan 2b: Shared Components

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The shared components plan 2a left open — popover/menu, filter chips with a "More" overflow, interactive rows and sortable headers, the text cut helper, the topbar controls and the conflict banner — built, tested and styled to RR in both themes, with the Ideas create form moved onto the shared entity form and plan 2a's carried defects fixed.

**Architecture:** Two new primitives carry most of the work: `popover.js` (one way to open, place, dismiss and key through anything that floats) and `overflow-row.js` (one way for a row to stay on one line and park what does not fit behind a button). Chips, the handover status menu, the field suggestion lists and the topbar's "Filters" all stand on those two. Rows and cards get `linkRow()` (a real `<a href>` with its controls as siblings) and tables get `sortHeader()`. The Table screen adopts chips and headers here as the one proving consumer; every other screen adopts in plan 3. The modal shell gains the hooks the forms need so no form reaches into its DOM.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §5 items 2 (topbar buttons), 3, 4 (segmented control), 6, 7 (Ideas form), 8, 9 (`truncate`), 11; §4 (topbar row 2 "Filters"); §11 amendments. Audit findings: KB-07 (primitive), TB-02, TB-03, TB-04, EM-02, X-03 (primitives), X-07, X-08, SE-01 (chip off state), IS-07 (zero-count rule). Carry-in: plan 2a ledger (`.superpowers/sdd/2026-10-01-viewer-rr-2a-task-modals/progress.md`) lines marked 2b, its final review's triage rows and M-9, the re-review's out-of-scope observations, and plan 1's carry list (`task-9-report.md` §7, "Plan 2").

**Depends on:** Plan 2a (HEAD a3f8e01): `openModal`/`confirmDialog`/`topModal`, `.btn`, markers and `marker()`, field renderers with `id`/`describedBy`/`.control`, `describeWriteError`/`lostRace`, `sameValue`, `renderMarkdown`, `formatStamp`, overlay surface roles, tone roles, `icon()`.

## Global Constraints

Plan 2a's constraints carry over unchanged:

- No `box-shadow`. No `transform`/`translate`/`scale`/`rotate` in any `:hover` rule. No colored left border. No `outline: none|0`. No italic. No text below 11px (11px only for uppercase labels). No named colours, hex or rgb literals outside `tokens.css`.
- Focus ring is the global one: `outline: 2px solid var(--border-focus); outline-offset: 2px`. A component may move the ring to a wrapper (the precedent is `.tm-search` in `shell.css:243-244`: same ring on the wrapper, `outline-color: transparent` on the inner control); it never restyles it.
- Signature-hued text uses `--text-accent` only. Text on a signature-tinted fill is `--foreground-bold`. Text on a solid `--signature-fill` is `--on-accent-fill`.
- Cards and rows use `--card-bg` / `--card-bg-hover`; modals, popovers and the banner use `--overlay-surface`, `--overlay-surface-sunken`, `--overlay-surface-hover`, `--overlay-surface-active`. Never `--surface-raised` directly, never a legacy alias (`--bg-*`, `--ink*`, `--accent*`, `--amber`, `--green`, `--red`, `--sp-*`, `--r-*`, `--text-*`, `--t-*`, `--border`, `--border-soft`).
- Status, severity and priority are a shape plus a word; the shape carries the hue, the word is a `foreground` colour.
- Every CSS file this plan creates or rewrites is added to `ENFORCED` in `viewer/tests/unit/style-rules.test.js` and passes. Read that test before writing CSS.
- No `window.confirm`, `window.alert` or `window.prompt` in any code path this plan touches. No `innerHTML` with task or user data except through `renderMarkdown()`.
- Every animation has a `prefers-reduced-motion` fallback (the global rule in `tokens.css` covers it; do not defeat it).
- Every new file starts with a 1–3 line `User intent:` header.
- Tests never touch a live backlog server. Mocked specs call `mockApi` before `page.goto` and assert `unmockedWrites(page)` is empty in `afterEach`.
- Work in `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr` (written `<wt>` below). No pushes, no amends, never `git add -A`; stage only the files the task names. Never chain `cd`; use `env --chdir=<wt>`, `npm --prefix <wt>/viewer`, `git -C <wt>`.

New for 2b:

- **One popover.** Anything that floats over the page (menus, suggestion lists, "More", "Filters") opens through `openPopover()` from Task 2. It is inserted directly after its anchor in the DOM (so Tab order follows what is seen and a modal's focus containment includes it) and positioned `fixed`; it is never appended to `<body>`. A press outside it closes it and still reaches whatever was pressed.
- **Stacking ladder:** page popover 70, mobile drawer 80, modal overlay 85, conflict banner 90. A popover inside a modal stacks within that modal's overlay.
- **One line, never wrapped.** Chip rows and the topbar's row 2 never wrap; what does not fit goes behind a "More" / "Filters" button built on `overflowRow()` from Task 7.
- **Real controls only.** Every clickable thing is a `<button>`, an `<a href>` or a form control. No interactive element sits inside another (axe `nested-interactive` stays at zero).
- **Cut text keeps its words.** Text cut with an ellipsis carries the full text in `title` (`truncate()` from Task 8, or the component sets `title` itself).
- **Touch targets.** At ≤768px every chip, popover item, row control and banner button is at least 44px tall.
- **Refused writes are words.** Every write error shown on the page goes through `describeWriteError()`; no method, URL, status code or JSON reaches the page.
- **Screens stay plan 3's.** 2b edits a screen file only where a task names it: `screens/table.js` and `css/screens/table.css` (Task 11), `screens/ideas.js` and `css/screens/ideas.css` (Task 6), and the one-line topbar caller edits in Task 9.
- **Parallel tasks** (see "Order and parallelism") run in sibling worktrees under `<wt>/../` branched from the current plan HEAD and are merged back locally `--no-ff` by the controller, one at a time. `viewer/index.html` stylesheet links and the `ENFORCED` array are merged as the union of both sides.

Commands (cwd never changed):

- One unit file: `env --chdir=<wt> node --test viewer/tests/unit/<file>`
- Unit suite: `npm --prefix <wt>/viewer run test:unit` (two known timing-flaky tests are listed in the ledger's `common.md`; if only those fail, re-run once and say so)
- Mocked specs: `npm --prefix <wt>/viewer run test:mock -- <spec file or -g "title">`
- Server: `C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider` with cwd `<wt>` (via `env --chdir=<wt>`)

## Review Focus

1. **A popover whose anchor is redrawn while it is open** (another writer changes the task while the handover status menu is open in the detail modal; the board's poll redraws a row). The menu must vanish with its anchor and leave nothing listening: the very next Escape closes the dialog, the next click does what it says. → Task 2, test "a menu whose button is redrawn closes with it and leaves no listener behind" (`popover.mock.spec.js`).
2. **A press outside an open popover onto another control** (open the handover menu, then click the "Reviewer note" toggle; open "More", then click a visible chip). The press must close the popover and act on the control in that one click. → Task 2, test "a click outside the menu closes it and still lands on what was clicked"; Task 7, test "a click on a visible chip while More is open closes More and toggles the chip".
3. **A pressed filter that reaches zero, or is parked behind "More"** (filter on Blocked, then the last blocked task moves on; press an epic that later overflows). The pressed chip must stay enabled so it can be turned off, and a pressed chip behind "More" must still be visible as pressed from the row. → Task 7, unit test "a pressed chip at zero count stays enabled; released, it disables" and mocked test "a pressed chip parked behind More is announced on the More button".
4. **A click on a row that is a link** (Ctrl/⌘-click, middle-click, a click on the row's own copy button). Modified clicks are the browser's (new tab, no modal); a click on a sibling control does that control's job and never opens the row. → Task 8, test "modified clicks are the browser's and a row's own control never opens the row" (`rows.mock.spec.js`).
5. **Changing screens with controls parked behind "Filters"** (at 390px the Table's controls sit in the Filters popover; the user goes to Kanban). Nothing from the old screen may survive in the topbar or the popover, the popover must be closed, and the new screen's own controls must lay out afresh. → Task 10, test "leaving a screen with its controls parked behind Filters leaves nothing behind" (`shell.mock.spec.js`).

## Order and parallelism

| Wave | Tasks (parallel within a wave) | Why they can run together |
|---|---|---|
| 1 | 1, 2, 8 | No shared source file; `index.html` / `ENFORCED` merge by union |
| 2 | 3, 4 (after 1), 7 (after 2), 12 (after 8) | 3 owns `entity-modal.js`, 4 owns `inline-field.js` + `task-detail-document.js`, 7 and 12 own new files; 12 writes its own spec file, not `task-form.mock.spec.js` |
| 3 | 5 (after 2, 3, 4), 9 (after 4) | 5 owns the field renderers; 9 owns `lib/topbar.js` and the caller lines |
| 4 | 6 (after 5, 9), 10 (after 7, 9), 11 (after 7, 8, 9) | 6 owns Ideas, 10 owns the topbar row, 11 owns Table |
| 5 | 13 | Verification |

Serial run order when one worktree is used: 1, 2, 8, 3, 4, 7, 12, 5, 9, 6, 10, 11, 13.

## Out of scope (owned by later plans)

- **Plan 3a (Kanban + detail modal):** the card becomes a `linkRow` (replacing the §11 interim `role=article`); the epic filter (`epic-chips.js`, `epic-dropdown.js`: KB-05, KB-06, KB-07's adoption) and the archived-phases dropdown are rebuilt on `chipRow`/`openPopover`; `priority-chips.js` ("Cr/Hi/Me/Lo") becomes a `chipRow` with full words; `EPIC_PALETTE` hex in `lib/epics.js` moves to `--cat-N`; Group/Sort selects named.
- **Plan 3b (Table + Epics):** fixed layout, sticky id + title, the scroll cue, stacked cards at 390px, rows as `linkRow`, markers in the cells.
- **Plan 3c (detail pages):** issue and bug detail templates; `.if-error` full-row rule outside task detail; refused-reason wording on other screens; the gate restyle (raw `review-gate:pending`, the hidden gate word); graph labels and the graph's own string `truncate` (`task-detail-graph.js:180`).
- **Plan 3d (Issues, Bugs, Ideas):** severity/status/tag chips (IE-03, IS-07, BG-01), the Ideas "Tags" popover, idea rows with `IDEA_STATUS` markers, bug rows as `linkRow` (BG-03).
- **Plan 3e (Sessions, Archived, Dashboard, Settings):** session chips (SE-02), the generic `.right-rail` frame (X-13; `components.css:93-186` shadow, serif, italic), the Settings segmented controls, a failed handover status change said in words.
- **All of plan 3:** primary actions move to topbar row 1 through `claimTopbarPrimary()`; locale and time-zone of absolute dates.
- **Plan 4 (cleanup):** dead code (`conflict-banner.js` `currentEtag` parameter, `task-form.js` no-op `validate`); legacy rules left in `components.css` (`.tm-card`, `.tm-empty`, `.cmp-*`, the old `.ho-status-pill` block); bare 32/44px heights in `modal.css`; the capture tools' SIGINT hook, ignored stdio and port check; the icon test's `planes`/`IEND` checks; px literals in `edit-fields.css`; `ENFORCED` → all files; the "44px float" test flake noted by the 2a final wave.
- **Settled, no work:** free text in an `allowFree` chip input becomes a chip on blur (by design, `chip-input.js:147-150`); a 400 JSON `error` is shown as the server's reason; an open inline picker cancels on window blur; a failed refresh after a successful edit closes silently; `aria-describedby` on a renderer wrapper without `.control` (every inline renderer exposes `.control`); the estimate picker's own message is already linked (`estimate-field.js:55-60`); §5.11's ten extra glyphs exist (plan 1 mapped epics, sessions and settings to `folder`, `document`, `sliders`).

---

### Task 1: Modal shell — hooks for forms, stack edges, leaving by navigation, soft reloads that keep what was open

**Depends on:** nothing in this plan. **Parallel:** wave 1.

**Files:**
- Modify: `viewer/js/components/modal.js`, `viewer/js/components/edit/entity-modal.js`, `viewer/js/components/detail-modal.js`, `viewer/js/components/task-detail-document.js` (`rememberFocus` becomes `rememberView`), `viewer/js/screens/task-detail.js`
- Test: `viewer/tests/unit/modal.test.js`, `viewer/tests/unit/entity-modal.test.js`, `viewer/tests/unit/task-detail-document.test.js`, `viewer/tests/modal.mock.spec.js`, `viewer/tests/task-detail.mock.spec.js`

**Interfaces:**
- Consumes: plan 2a `openModal`, `topModal`, `confirmDialog`, `focusableIn`.
- Produces:
  ```js
  // modal.js — added to the handle openModal() returns
  onKey(fn) → off()        // fn(e: KeyboardEvent) → boolean. Runs for every keydown inside this dialog, in the capture
                           // phase, before the focused control sees it, and only while this modal is topmost. Returning
                           // true makes the shell call preventDefault() and stopPropagation(). off() removes it; all
                           // handlers are dropped on close.
  pressing() → boolean     // a pointer press that began inside this modal's overlay has not been released yet
  afterPress(fn) → void    // no press held: fn runs now. Press held: fn runs once, on the task after the press ends
                           // (pointerup or pointercancel anywhere in the document, or the window losing focus).
                           // The same fn queued twice in one press runs once. Queued fns are dropped on close.
  // task-detail-document.js — replaces rememberFocus (same call shape)
  export function rememberView(scope) → (next = scope) => boolean
  ```
- Behaviour contract (continues plan 2a's numbering; each line is a test):
  11. `onKey` as above. A handler returning `false` leaves the key alone: the focused input's own listener runs and Escape still closes the modal.
  12. `pressing`/`afterPress` as above, including release by window `blur` and the de-duplication.
  13. **Out-of-order close.** When a modal that is not topmost closes, every modal above it whose opener lies inside the closing dialog takes over the closing modal's opener (and its saved ancestors) as its own focus-return target. Example: A opened from `#opener`, B opened from a button in A's body; `A.close()` leaves B open, topmost and `.shell` still inert; `B.close()` puts focus on `#opener`, not on `#screen-mount`'s first link.
  14. **Focus set.** `focusableIn()` matches the browser's Tab sequence: `CAN_FOCUS` becomes `'a[href], area[href], button, input, select, textarea, iframe, audio[controls], video[controls], details > summary:first-of-type, [tabindex], [contenteditable]:not([contenteditable="false"])'`, and `canTakeFocus` also rejects anything inside a closed `<details>` other than its first `summary`. A zero-size element that is rendered stays in the set (the browser tabs to it).
  15. **A guard that never settles.** With `onRequestClose: () => new Promise(() => {})`, two Escapes leave the modal open and `requestClose()` returns the identical pending promise both times; `close()` still closes it. The JSDoc above `openModal` states this rule in one sentence.
  16. **Leaving by navigation.** `detail-modal.js` `onHash`: when the detail is topmost it closes; otherwise it asks every modal above it to close, top first, with `requestClose()`, and closes the detail with `close()` only if all of them did. One that stays open (the user chose "Keep editing") keeps the whole stack open over the new screen. A second `hashchange` while that is being asked is ignored. The detail's later close never calls `history.back()` (its history entry is no longer current).
  17. **Soft reload keeps what was open.** `rememberView(scope)` remembers focus exactly as `rememberFocus` did, plus the `data-focus` value of every `button[aria-expanded="true"][data-focus]:not([aria-haspopup])` in `scope`. The returned function re-opens each matching toggle in `next` that reads `aria-expanded="false"` by calling its `click()`, then restores focus, and returns whether focus was restored. `detail-modal.js` and `screens/task-detail.js` use it for every re-mount.
  18. `entity-modal.js` no longer touches the shell's DOM: its Ctrl/⌘+Enter goes through `modal.onKey`, and a message owed while a press is held goes through `modal.afterPress(paint)`. The variables `pointerHeld` and `paintDue` are gone.

- [ ] **Step 1: Unit tests.** In `modal.test.js` add one test per line 11–15 (jsdom; reuse the file's `PAGE`, `fire`, `key`, `tick`). For 12 assert: a `pointerdown` on the overlay makes `pressing()` true; `afterPress(f)` twice during the press runs `f` zero times before `pointerup` on `document` and exactly once on the next task after it; a `blur` on `window` also releases; after `close()` a queued `f` never runs. For 14 build, inside the dialog body: `<details><summary id="s1">a</summary><input id="hidden-in"></details>` (closed), `<div id="ce" contenteditable="plaintext-only"></div>`, `<div contenteditable="false" id="cef"></div>`, `<audio controls id="au"></audio>`, `<a id="nohref">x</a>`; assert `focusableIn(body).map(e => e.id)` equals `['s1', 'ce', 'au']`. For 15 race `requestClose()` against a 20 ms timer and assert the timer wins, and assert `p1 === p2`. In `entity-modal.test.js` add: the file text of `viewer/js/components/edit/entity-modal.js` (read with `readFileSync`) matches neither `/parentElement/` nor `/dialog\.addEventListener/`; and a field left (focusout to another field) while a press is held shows its message only after `pointerup` on `document`. In `task-detail-document.test.js` rename the `rememberFocus` cases to `rememberView` and add: a document with the "Reviewer note" toggle expanded, re-mounted into a fresh root, has that toggle `aria-expanded="true"` and its note visible; a `[aria-haspopup]` button with `aria-expanded="true"` is not clicked.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/modal.test.js viewer/tests/unit/entity-modal.test.js viewer/tests/unit/task-detail-document.test.js` — Expected: the new tests FAIL (`onKey is not a function`, `rememberView` not exported, focus set mismatch).
- [ ] **Step 3: Implement** in `modal.js`: one capture-phase `keydown` listener on the dialog dispatching to the handler list while `isTop()`; press tracking on the overlay (`pointerdown` capture) released by `pointerup`/`pointercancel` on `document` (capture) and `blur` on `window`, flushing a `Set` of queued fns with `setTimeout(…, 0)`; in `close()`, before splicing the stack, re-point `from`/`ancestors` of every modal above whose `from` is inside this dialog (keep `from` and `ancestors` on a per-modal record the stack can read); the new `CAN_FOCUS` and closed-`details` check; the JSDoc sentence. Then `entity-modal.js` per line 18, `detail-modal.js` per line 16, and `rememberView` per line 17 with both callers switched.
- [ ] **Step 4: Mocked tests.** In `modal.mock.spec.js` (existing harness `boot`/`open`): (a) a dialog whose last control is a 0×0 `<button id="z">` (style `width:0;height:0;padding:0;border:0`): Tab from the input before it lands on `#z`, Tab again wraps to the first control, Shift+Tab from the first lands on `#z`; (b) a closed `<details>` with an input inside: Tab never lands on that input; opening the `details` puts it in the cycle. In `task-detail.mock.spec.js` (existing `board`, `openCard`, `renamedElsewhere` helpers): (c) "navigating away with a clean form stacked closes both, with no confirm": open T-102, press Edit, `page.evaluate(() => { location.hash = '#/table'; })`; expect `.modal` count 0, no `alertdialog`, no page error, and `document.activeElement.isConnected` true (the new screen may still be mounting, so focus may rest on `body`, but never on a removed node); (d) "navigating away with unsaved edits asks; Keep editing keeps the stack over the new screen": as (c) but fill the form's title first; expect `alertdialog` "Discard changes?"; choose "Keep editing"; expect the form and the detail both visible, `location.hash` `#/table`, focus in the form's title input; Escape → "Discard" → the form closes and focus is on the detail's Edit button; Escape → the detail closes; focus is not `body`; no page error; (e) "another writer's change keeps the reviewer note open": open T-102, click the "Reviewer note" toggle, `renamedElsewhere(page, 'Renamed elsewhere')`; expect the dialog title `Renamed elsewhere`, the toggle `[data-focus="spec-note"]` still `aria-expanded="true"` with its note visible, and focus still on it.
- [ ] **Step 5: Run** the unit suite and `npm --prefix <wt>/viewer run test:mock -- modal.mock.spec.js task-detail.mock.spec.js task-form.mock.spec.js` — Expected: PASS (`task-form.mock.spec.js` "a message that appears when a field is left does not eat the click that left it" and "Ctrl+Enter saves from inside a textarea" prove the hooks).
- [ ] **Step 6: Commit** — `feat(viewer): the modal shell gives forms key and press hooks, keeps focus targets through out-of-order closes, and the detail modal leaves by navigation through the stack and keeps open disclosures on a soft reload`

---

### Task 2: Popover primitive, and the handover status menu on it

**Depends on:** nothing in this plan. **Parallel:** wave 1.

**Files:**
- Create: `viewer/js/components/popover.js`, `viewer/css/components/popover.css`, `viewer/tests/unit/popover.test.js`, `viewer/tests/popover.mock.spec.js`
- Modify: `viewer/js/components/right-rail.js` (`openStatusMenu` on `openPopover`; its `place()` moves into `popover.js`), `viewer/css/components/handover-status.css` (menu rules move to `popover.css`; the pill stays), `viewer/css/components.css` (delete the legacy `/* Status override dropdown menu */` block, `.ho-status-menu` and `.ho-status-menu-item*`, ~lines 451–478), `viewer/index.html` (link `css/components/popover.css` after `modal.css`), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/popover.css`), `viewer/tests/unit/right-rail.test.js`

**Interfaces:**
- Consumes: `h()` (`viewer/js/util/h.js`).
- Produces (`viewer/js/components/popover.js`):
  ```js
  export function openPopover({
    anchor,               // Element, required. While open it has aria-expanded="true" and aria-controls=<popover id>.
    content,              // Node | Node[], required — appended directly into the popover element
    role = 'dialog',      // 'menu' | 'listbox' | 'dialog' — set on the popover element
    label,                // string → aria-label (required for 'menu' and 'listbox')
    labelledBy,           // id string → aria-labelledby (alternative to label)
    focus = 'first',      // 'first' | 'checked' | 'none'
    className = '',       // extra classes on the popover element (e.g. 'ho-status-menu')
    minWidth = 'none',    // 'anchor' → at least the anchor's width
    onClose,              // (reason) => void — 'escape' | 'outside' | 'focusout' | 'scroll' | 'detached' | 'replaced' | 'api'
  }) → {
    el,                   // the popover element, id `popover-<n>`, class `popover <className>`
    close(reason = 'api', { returnFocus = false } = {}),   // idempotent; onClose runs once
    isOpen(),             // boolean
    reposition(),
  }
  export function openPopoverCount() → number
  export function placePopover(el, anchor, { minWidth = 'none' } = {})   // the measured placement, exported for tests
  ```
- Behaviour contract (each line is a test):
  1. The popover is inserted with `anchor.after(el)`, is `position: fixed`, sits 4px below the anchor's left edge, flips above when it does not fit below, and is clamped 4px inside the viewport horizontally. The offset of a containing block (a frosted overlay, a transformed panel) is measured and subtracted, as `right-rail.js` `place()` does today.
  2. Escape pressed inside the popover, or on the anchor while open and not already `defaultPrevented`, closes with `'escape'`, calls `preventDefault()` and `stopPropagation()` (a modal around it stays open), and puts focus on the anchor.
  3. A `pointerdown` (document, capture) outside both the popover and its anchor closes with `'outside'` and calls neither `preventDefault()` nor `stopPropagation()`: the press and its click reach their target. A press on the anchor is left to the anchor's own click handler.
  4. Focus leaving for an element outside the popover and the anchor (a `focusout` with a `relatedTarget`) closes with `'focusout'` and moves no focus.
  5. A `scroll` (document, capture) whose target is not inside the popover closes with `'scroll'`; scrolling inside the popover does not. A window `resize` repositions.
  6. When the anchor leaves the document the popover closes with `'detached'` within one `MutationObserver` callback (observing `document.body`, `childList` + `subtree`, only while open), is removed, and every document/window listener and the observer are released: afterwards `openPopoverCount()` is 0 and a `pointerdown`, `keydown` Escape or `scroll` on `document` calls no `onClose` and throws nothing.
  7. Opening a popover closes every open one whose element does not contain the new anchor (`'replaced'`); a popover opened from inside an open popover leaves its parent open.
  8. Focus on open: `'first'` → the first enabled item, else the first focusable in the content; `'checked'` → the item with `aria-checked`, `aria-selected` or `aria-pressed` `"true"`, else as `'first'`; `'none'` → focus stays where it is. Items are `[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"], [data-popover-item]` that are not `disabled` and not `aria-disabled="true"`.
  9. With focus on an item, ArrowDown/ArrowUp move to the next/previous enabled item and wrap, Home/End go to the first/last, each with `preventDefault()`. With focus on anything else (a text input in a `dialog` popover) arrows are left alone.
  10. The anchor gets `aria-haspopup` = the role (`'menu'`, `'listbox'` or `'dialog'`) unless it is itself `role="combobox"`; `aria-expanded` is `"false"` again after close.
  11. `close(…, { returnFocus: true })`, or any close while focus is inside the popover (other than `'focusout'` and `'detached'`), focuses the anchor if it is still connected.
- `popover.css` (tokens only): `.popover { z-index: 70; box-sizing: border-box; display: flex; flex-direction: column; min-width: 168px; max-width: min(360px, calc(100vw - 8px)); max-height: min(360px, 60vh); overflow: auto; padding: var(--space-micro); border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--overlay-surface); color: var(--foreground-default); animation: popover-in var(--dur-micro) var(--ease-hourglass) backwards; }` with `@keyframes popover-in { from { opacity: 0; } }`. `.popover-item` takes today's `.ho-status-menu-item` rules (`handover-status.css:49-71`) unchanged; `.popover-item[aria-checked="true"], .popover-item.is-current` keep the bold word; `@media (max-width: 768px) { .popover-item { min-height: 44px; } }`. `handover-status.css` keeps only the pill rules and `.ho-status-menu-check`.
- `openStatusMenu(anchor, handoverId, currentStatus)` keeps its signature and toggle (a second click on the same pill closes). Items are `button.popover-item.ho-status-menu-item[role="menuitemradio"]`; it calls `openPopover({ anchor, content: items, role: 'menu', label: 'Handover status', focus: 'checked', className: 'ho-status-menu' })`. Choosing closes with `returnFocus: true`, then posts as today.

- [ ] **Step 1: Unit tests** — `popover.test.js` (jsdom; layout-free lines 2–4 and 6–11): each line above with concrete assertions; for 2 put the anchor inside a `div` with a bubbling `keydown` listener and assert it never sees the Escape; for 3 assert the outside button's `click` listener ran and `e.defaultPrevented` was false on the `pointerdown`; for 6 remove the anchor's parent and `await` one microtask; for 9 build a `menu` of three items with the middle one `disabled` and assert ArrowDown from the first lands on the third. Update `right-rail.test.js` only where selectors must change (they should not: `.ho-status-menu`, `[role="menuitemradio"]` remain).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/popover.test.js viewer/tests/unit/right-rail.test.js` — Expected: popover tests FAIL (module not found); right-rail tests PASS.
- [ ] **Step 3: Implement** `popover.js` with all listeners registered through one `AbortController` per popover (abort on close) plus the `MutationObserver`; `popover.css`; move the menu onto it; delete the legacy block in `components.css`; link the stylesheet; extend `ENFORCED`.
- [ ] **Step 4: Mocked tests** — `popover.mock.spec.js`, using `mockApi` with the board and `taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED)` from `mock-fixtures.js` (the rail shows one handover pill), `'/api/bugs': []`, and `POST /api/handover/2026-09-30-kanban-reskin/status` answered `{ ok: true }`:
  - "a click outside the menu closes it and still lands on what was clicked": on `#/task/T-102`, open the pill's menu, then click the "Reviewer note" toggle once; expect no `.ho-status-menu` and the toggle `aria-expanded="true"`.
  - "Escape closes the menu only, then the dialog": in the detail modal (click card T-102), open the menu, Escape → no menu, dialog still open, focus on the pill; Escape → dialog closed.
  - "a menu whose button is redrawn closes with it and leaves no listener behind" (Review Focus 1): in the detail modal open the menu, then run the `renamedElsewhere` steps inline (route `**/api/task/T-102/detail` to a renamed task, bump the board revision); expect the title to update, `.ho-status-menu` count 0, no page error; press Escape once → the dialog closes.
  - placement: at 390×844 the open menu's box is inside the viewport; with the pill scrolled to within 60px of the viewport bottom the menu opens above it.
  - in both themes: computed `box-shadow` of `.popover` is `none`; axe on the open menu reports zero violations.
- [ ] **Step 5: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- popover.mock.spec.js task-detail.mock.spec.js` — Expected: PASS. Style-rules: `components/popover.css` enforced, 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): one popover primitive — placed after its anchor, dismissed by Escape, outside press, focus loss or a redraw of its anchor without eating the press — and the handover status menu on it`

---

### Task 3: The entity form and its fields — the carried defects

**Depends on:** Task 1 (`entity-modal.js`). **Parallel:** wave 2, beside Tasks 4, 7, 12.

**Files:**
- Modify: `viewer/js/components/edit/entity-modal.js`, `viewer/js/components/edit/fields/keyvalue-field.js`, `viewer/js/components/edit/fields/number-field.js`, `viewer/css/components/edit-fields.css` (`.ef-num-note`), `viewer/js/api.js` (409 message)
- Test: `viewer/tests/unit/entity-modal.test.js`, `viewer/tests/unit/keyvalue-field.test.js`, `viewer/tests/unit/number-field.test.js`, `viewer/tests/unit/api.test.js`, `viewer/tests/task-form.mock.spec.js`

**Interfaces:**
- Consumes: `describeWriteError(e, { noun })` (`edit/write-errors.js`), `modal.afterPress` (Task 1).
- Produces:
  - KeyValueField wrapper gains `markInvalid(on: boolean)`: on → `aria-invalid="true"` on every input at fault (the type input of a row missing its type or repeating one; the value input of a row missing its value); off → removed from all. `entity-modal.js` `paint()` calls `f.el.markInvalid(!!message)` when the wrapper has it, else keeps today's single-control rule.
  - KeyValueField `validate` returns every fault, joined with ` · `, in row order. Any value quoted in a message is cut to 40 characters plus `…`.
- Behaviour (each line is a test):
  1. **Touched.** A field is touched when focus leaves it for anything other than the dialog's header or footer controls (a click on blank dialog space counts — focus lands on the dialog itself — and so does another field); leaving for Save, Cancel or the close button, or leaving the window, does not.
  2. **Create judges every field.** In create mode every field is validated in full on save; the edit-mode rule (an unchanged field is judged only for `required`) stays for edit mode. A Create form prefilled with an epic that no longer exists is refused in the form ("Unknown epic" on the Epic field) and sends nothing.
  3. **No raw error.** An `onSave` that throws, or a `wait` that rejects, shows `describeWriteError(err, { noun })` in the footer alert, never `err.message`.
  4. **409 reason.** `api.js` builds a 409 error's message from `j.error` only when it is a non-empty string, else `'stale'`.
  5. **Docs keep what they were not asked to change.** A stored docs value that is not a string (`{ spec: ['a.md', 'b.md'], plan: 'p.md' }`) is edited as its JSON text, and a row whose text is left as shown saves its original value; editing `plan` to `p2.md` saves `{ spec: ['a.md', 'b.md'], plan: 'p2.md' }` (deep equal). Opening and closing untouched is not dirty.
  6. **Every faulted row is flagged.** Rows `[{key:'', value:'x'}, {key:'spec', value:''}]` give two messages joined by ` · ` and `aria-invalid="true"` on the first row's type input and the second row's value input, and on nothing else.
  7. **A stored number the field cannot show is shown.** NumberField `edit` with a stored value that is not a finite number (`'beta'`) renders an empty input plus `span.ef-num-note` "Current: beta — not a number. It is kept unless you type one.", whose id is added to the input's `aria-describedby`; the untouched form is not dirty and the value is not sent.
  8. **A held form is scrollable from the keyboard.** In the saving state and while the conflict banner holds the form, axe reports zero `scrollable-region-focusable` inside the dialog.

- [ ] **Step 1: Tests.** Unit: lines 1 (jsdom: focusout with `relatedTarget` = the dialog → touched; = the Save button → not; = `null` → not), 2, 3 (an `onSave` throwing `Object.assign(new Error('POST /api/tasks → 500: {"error":"x"}'), { code: 500 })` → footer text "The server could not save this change. Try again in a moment." and no `→`, `/api`, `{`), 4 (`api.test.js`: a mocked 409 with `{ error: { nested: 1 } }` gives `message === 'stale'`), 5, 6 (including the 41-character URL cut to 40 + `…`), 7. Mocked (`task-form.mock.spec.js`): "Create refuses an epic that no longer exists in the form and sends nothing" (mock `/api/board` with `context.active_epic: 'gone'`, assert no POST recorded); "clicking blank space in the dialog after leaving a field shows its message" (empty the title, click the dialog body padding, expect the title's message); line 8 by axe with `runOnly: ['scrollable-region-focusable']` in the saving state (hold the POST like `capture-modals.mjs` `holdCreate`) and in the banner-held state (the file's `conflict()` helper).
- [ ] **Step 2: Run** the four unit files and the spec — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Keep the keyvalue rows' original value beside the text shown (`{ key, value, raw }`); `coerce` uses `raw` when `value === text(raw)`. Replace `fault()` with `faults()` returning all, keep `problem()` as their joined messages. `.ef-num-note` copies `.ef-estimate-note`'s rule in `edit-fields.css`.
- [ ] **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- task-form.mock.spec.js` — Expected: PASS.
- [ ] **Step 5: Commit** — `fix(viewer): the entity form judges a field once it is left for anywhere in the form, judges every field of a new item, words every refusal; docs keep values nobody edited and flag every faulted row; a stored non-number stage is shown`

---

### Task 4: Inline fields — a refusal stays said, and the title's message stays out of the heading

**Depends on:** Task 1 (`task-detail-document.js`). **Parallel:** wave 2, beside Tasks 3, 7, 12.

**Files:**
- Modify: `viewer/js/components/edit/inline-field.js`, `viewer/js/components/task-detail-document.js` (title message host), `viewer/css/screens/task-detail.css` (`.td-title-message`)
- Test: `viewer/tests/unit/inline-field.test.js`, `viewer/tests/unit/task-detail-document.test.js`, `viewer/tests/task-detail.mock.spec.js`

**Interfaces:**
- Consumes: `rememberView` (Task 1).
- Produces: `mountInlineField(parent, { schema, fieldKey, entity, onSave, readOnly, getBacklog, messageHost })` — the status glyph and the message element are appended to `messageHost ?? parent`.
- Behaviour (each line is a test):
  1. A refused save's reason stays visible after the editor closes (a picker left with Tab, or `refuse()` cancelling because focus already left), and through `update()` repaints in read mode. It is cleared when the field is opened again or when a later save succeeds.
  2. The task title's inline field passes a `messageHost`: on the page, a `div.td-title-message` placed right after the `h1` inside `.td-head`; in the modal, a `div.td-title-message` as the first child of `.td-body`. A refused title save leaves the heading's text (and so the dialog's accessible name) exactly the title. `.td-title-message:empty { display: none; }`; otherwise it uses the `.ef-error` text style.

- [ ] **Step 1: Tests.** Unit (`inline-field.test.js`): a status field whose `onSave` returns `{ error: 'Gates are still open' }`, picker blurred → read mode shows "Gates are still open"; `handle.update({...})` → still shown; click to edit → gone; a status field that saves after a refusal → message gone. Unit (`task-detail-document.test.js`): embedded chrome with a `titleHost` inside an `h2`, title save refused → `h2.textContent` equals the title and `.td-title-message` holds the reason. Mocked (`task-detail.mock.spec.js`): in the detail modal, make the title `PATCH` answer `{ status: 409, json: { ok: false, error: 'Titles are frozen during review' } }`, edit the title and press Enter; expect `getByRole('dialog', { name: DETAIL_TASK.title })` still to resolve and the reason visible in `.td-title-message`; then press Tab away from a refused status picker (existing test "a refused status choice…" fixture) and expect the reason still visible.
- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement** (`paint()` stops clearing the message; `enterEdit()` and a successful save clear it). **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- task-detail.mock.spec.js` — PASS; `screens/task-detail.css` still 0 violations.
- [ ] **Step 5: Commit** — `fix(viewer): a refused inline save keeps its reason in view after the picker is left, and the title's message sits under the heading instead of inside it`

---

### Task 5: Field suggestion lists are comboboxes on the popover; a task is never offered as its own dependency

**Depends on:** Tasks 2, 3, 4. **Parallel:** wave 3, beside Task 9.

**Files:**
- Modify: `viewer/js/components/edit/fields/chip-input.js`, `viewer/js/components/edit/fields/relation-picker.js`, `viewer/js/components/edit/entity-modal.js` (passes `entityId`), `viewer/js/components/edit/inline-field.js` (passes `entityId`), `viewer/css/components/edit-fields.css` (`.ef-chip-dropdown` loses its own positioning; its look stays)
- Test: `viewer/tests/unit/chip-input.test.js`, `viewer/tests/unit/relation-picker.test.js`, `viewer/tests/task-form.mock.spec.js`

**Interfaces:**
- Consumes: `openPopover`, `openPopoverCount` (Task 2).
- Produces:
  - Renderer `edit()` contract addition: `entityId` (string or undefined) — the id of the entity being edited. `entity-modal.js` passes `initial.id`; `inline-field.js` passes `currentEntity.id`. Renderers that do not need it ignore it.
  - `makeRelationSource(kind, getBacklog, { exclude = [] } = {})` — results whose `value` is in `exclude` are dropped. `RelationPicker.edit` passes `{ exclude: entityId ? [entityId] : [] }`.
- Behaviour (each line is a test):
  1. The chip input's text input has `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded` (`"true"` only while suggestions show) and, while open, `aria-controls` = the list's id and `aria-activedescendant` = the highlighted option's id.
  2. Suggestions open through `openPopover({ anchor: input, content: options, role: 'listbox', label: `${label} suggestions`, focus: 'none', minWidth: 'anchor', className: 'ef-chip-dropdown' })`; each option is `div[role="option"][id]` with `aria-selected="true"` on the highlighted one only.
  3. Existing keys are unchanged: ArrowDown/ArrowUp move the highlight, Enter and Tab pick, Escape clears and closes before the form sees it, Backspace on empty removes the last chip. A `mousedown` on an option picks it and keeps focus in the input.
  4. The list closes on pick, blur, Escape, an outside press and when the field is removed; after each, `openPopoverCount()` is 0.
  5. A task's own id is never offered by its Depends-on field, in the form or inline.

- [ ] **Step 1: Tests.** Unit: lines 1–4 (jsdom, a `source` returning three items); `relation-picker.test.js`: `makeRelationSource('tasks', () => BOARD, { exclude: ['T-102'] })('T-10')` contains no `T-102`. Mocked (`task-form.mock.spec.js`): "Depends on never offers the task itself": open Edit for T-102, type `T-10` in Depends on; the listbox's options include `T-101` and not `T-102`; the existing "relation list opened at the bottom edge … is fully visible and uncovered" tests still pass; axe on the open list in both themes: zero `aria-*` violations.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- task-form.mock.spec.js task-detail.mock.spec.js` — PASS; `edit-fields.css` 0 violations.
- [ ] **Step 5: Commit** — `feat(viewer): field suggestion lists are announced comboboxes on the shared popover, and a task is never offered as its own dependency`

---

### Task 6: The Ideas create form is the shared entity form

**Depends on:** Tasks 3, 5, 9. **Parallel:** wave 4, beside Tasks 10, 11.

**Files:**
- Create: `viewer/js/components/edit/forms/idea-form.js`, `viewer/js/components/edit/idea-actions.js`, `viewer/tests/unit/idea-form.test.js`, `viewer/tests/ideas-form.mock.spec.js`
- Modify: `viewer/js/components/status.js` (`IDEA_STATUS`), `viewer/js/api.js` (`createIdea`), `viewer/js/components/edit/entity-modal.js` (section `open` flag), `viewer/js/components/edit/forms/task-form.js` (`description` gets `open: true`), `viewer/js/screens/ideas.js` (delete `openCreateIdeaModal` and the local `createIdea`; the New Idea button opens the shared form), `viewer/css/screens/ideas.css` (delete the `.em-*` rules plan 2a parked there), `viewer/tests/unit/status.test.js`

**Interfaces:**
- Consumes: `openEntityModal` (with Task 3's behaviour), `ChipInput` (Task 5), `TextField`, `EnumSelect`, `MdField`, `describeWriteError`.
- Produces:
  ```js
  // status.js — STATUS_KINDS gains `idea`
  export const IDEA_STATUS;   // exploring ['Exploring','◐','accent'], candidate ['Candidate','○','neutral'],
                              // 'parking-lot' ['Parking lot','○','neutral'], promoted ['Promoted','→','neutral'],
                              // dropped ['Dropped','✕','neutral']   (spec §5.1 by meaning)
  // api.js — named export and on the `api` object
  export const createIdea = (payload) => http('POST', '/api/ideas', payload);
  // forms/idea-form.js
  export function ideaSchema({ getIdeas }) → {
    entity: 'idea', label: 'Idea',
    fields: [
      { key: 'title',  label: 'Title',  renderer: TextField,  group: 'basics', wide: true, required: true, maxLength: 140 },
      { key: 'status', label: 'Status', renderer: EnumSelect, group: 'basics', required: true,
        options: /* IDEA_STATUS in table order as { value, label } */ },
      { key: 'tags',   label: 'Tags',   renderer: ChipInput,  group: 'basics', allowFree: true,
        placeholder: 'add a tag…', source: /* async q → distinct tags of getIdeas() containing q, case-insensitive, max 8 */ },
      { key: 'body',   label: 'Body',   renderer: MdField,    group: 'content', open: true },
    ],
    systemManaged: ['id', 'created', 'updated', 'archived', 'promoted_to'],
  }
  // edit/idea-actions.js
  export function openIdeaCreateModal({ store, onCreated })   // initialEntity { status: 'exploring', tags: [] }
  ```
  `entity-modal.js`: a Content section starts open when it has a value or, in create mode, when its spec has `open: true` (the hard-coded `description` check goes).
- Behaviour (each line is a test):
  1. "New Idea" opens a dialog named "Create idea" on the shared shell: labelled fields, Escape on an untouched form closes with no dialog of any kind, a typed title asks "Discard changes?" in-app.
  2. Save sends one `POST /api/ideas` with exactly the defaults plus what was typed: title "Faster board" and tag "ux" → body deep-equals `{ status: 'exploring', tags: ['ux'], title: 'Faster board' }`. On success the form closes and the list re-reads `/api/ideas`.
  3. A refusal is words: a 400 `{ error: 'title is required' }` shows "title is required" in the footer; a 500 shows "The server could not save this change. Try again in a moment."; the footer never contains `→`, `/api` or `{`.
  4. No `.em-` class remains in `ideas.js` or `ideas.css`; `ideas.js` no longer creates `#entity-modal-host` or calls `window.confirm`.

- [ ] **Step 1: Tests.** Unit (`idea-form.test.js`): schema keys and groups; the tags `source` returns distinct, matching, at most 8; `status.test.js`: `IDEA_STATUS` table and `statusMeta('idea', 'parking-lot').label === 'Parking lot'`. Mocked (`ideas-form.mock.spec.js`, `/api/ideas` mocked with three ideas carrying tags, `POST /api/ideas` answered `{ ok: true, id: 'IDEA-9' }` or the refusals above): lines 1–3; record `page.on('dialog')` and assert it never fired; axe on the open form in both themes: zero `color-contrast`, `label`, `select-name`, `aria-*` inside the dialog; 390×844: one column, Save and Cancel visible without scrolling.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**; `onSave` catches with `describeWriteError(e, { noun: 'idea' })`; a failed list refresh after a successful create is swallowed (the form still closes). **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- ideas-form.mock.spec.js task-form.mock.spec.js` — PASS; `grep -n "em-\|window.confirm" viewer/js/screens/ideas.js viewer/css/screens/ideas.css` → no match.
- [ ] **Step 5: Commit** — `feat(viewer): the Ideas create form is the shared entity form — labelled, honestly dirty, refusals in words, no native dialog`

---

### Task 7: Overflow row, filter chips and chip rows

**Depends on:** Task 2. **Parallel:** wave 2, beside Tasks 3, 4, 12.

**Files:**
- Create: `viewer/js/components/overflow-row.js`, `viewer/js/components/chips.js`, `viewer/css/components/chips.css`, `viewer/tests/unit/chips.test.js`, `viewer/tests/unit/overflow-row.test.js`, `viewer/tests/chips.mock.spec.js`
- Modify: `viewer/js/lib/epics.js` (`epicSwatch`), `viewer/tests/unit/epics.test.js`, `viewer/index.html`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/chips.css`)

**Interfaces:**
- Consumes: `openPopover` (Task 2), `icon()`.
- Produces:
  ```js
  // overflow-row.js
  export function fitCount(widths, available, { gap = 0, moreWidth = 0 } = {}) → number
    // how many leading items stay: all when sum(widths) + gap*(n-1) <= available; otherwise the largest k with
    // sum(first k) + gap*k + moreWidth <= available (k may be 0)
  export function overflowRow(row, {
    moreLabel = 'More', moreIcon = null, popoverLabel = moreLabel,
    keep = () => false,     // (el) → true for children that never move (measured first)
    onLayout,               // ({ hidden: Element[] }) → void, after every layout
  } = {}) → { more, relayout(), reset(), destroy() }
  // chips.js
  export function filterChip({ label, value, pressed = false, count, swatch, title, onToggle }) → HTMLButtonElement
  export function chipRow({ label, chips, onToggle, hint }) → { el, update(chips), destroy() }
    // chips: [{ value, label, pressed?, count?, swatch?, title? }]; onToggle(value, event)
  // lib/epics.js
  export function epicSwatch(epicId, epics) → 1 | 2 | 3 | 4 | 5 | 6 | null
    // the epic's position among `epics` (entries with an id), modulo 6, plus 1; null when not found
  ```
- `overflowRow` behaviour (each line is a test; layout lines in Playwright):
  1. It appends `more` = `button.btn.btn--ghost.btn--sm.overflow-more` (`moreIcon` when given, the label, and `span.overflow-more__count` with the hidden count) to `row`; `more.hidden` is true while nothing overflows.
  2. Children that do not fit move, in order, into the popover's list (`div.overflow-list`); when room returns they move back in their original order. Each moved child gets `data-popover-item` while parked. The children are the same nodes: listeners and state survive.
  3. `more` toggles `openPopover({ anchor: more, content: list, role: 'dialog', label: popoverLabel, focus: 'first' })`.
  4. Layout runs on a `ResizeObserver` of `row` and when children are added or removed (a `MutationObserver` on `row` that ignores its own moves); while the popover is open layout waits until it closes.
  5. `reset()` closes the popover and moves every parked child back into `row`. `destroy()` resets and disconnects both observers.
  6. Without `ResizeObserver` (jsdom) nothing moves and `more` stays hidden.
- `filterChip` (each line is a test): `<button type="button" class="chip" aria-pressed="true|false" data-value>`; optional `span.chip__swatch.chip__swatch--cat-<1..6>[aria-hidden]`; `span.chip__label` (text only); `span.chip__count` when `count` is a number. `title` is always set: `title ?? label`. `disabled` when `count === 0 && !pressed`. Click → `onToggle(value, event)`.
- `chipRow`: `div.chip-row[role="group"][aria-labelledby]` → `span.chip-row__label` (with `title = hint` when given) + `div.chip-row__chips` under `overflowRow(…, { moreLabel: 'More', popoverLabel: `More ${label}` })`. `update(chips)` reuses the button of each value already shown (focus and an open "More" survive) and updates its `aria-pressed`, `disabled`, count and label in place. Added chips, removed chips and a new order are applied, followed by a relayout, at once while More is closed and when it closes while it is open: a chip the user is pressing inside More never moves under them. While hidden chips include pressed ones, `more` shows `span.overflow-more__on` "· n on" and has `aria-label` "More <label>, <h> hidden, <n> selected"; otherwise "More <label>, <h> hidden".
- `chips.css` (tokens only): `.chip-row { display: flex; align-items: center; gap: var(--space-xs); min-width: 0; }` `.chip-row__label` Technical label voice (`--font-technical`, 800, `--size-technical-label`, uppercase, `--tracking-ultra`, `--foreground-subtle`). `.chip-row__chips { display: flex; flex-wrap: nowrap; align-items: center; gap: var(--space-micro); min-width: 0; flex: 1 1 auto; overflow: hidden; }` `.chip { flex: 0 0 auto; display: inline-flex; align-items: center; gap: var(--space-micro); min-height: 28px; padding: 0 var(--space-sm); border: 1px solid var(--border-default); border-radius: var(--radius-sm); background: transparent; color: var(--foreground-default); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); letter-spacing: var(--tracking-wide); white-space: nowrap; cursor: pointer; transition: background-color var(--dur-standard) var(--ease-hourglass), border-color var(--dur-standard) var(--ease-hourglass), color var(--dur-standard) var(--ease-hourglass); }` hover (not pressed, not disabled) `background: var(--ground-15); border-color: var(--border-strong); color: var(--foreground-bold)`; `[aria-pressed="true"]` `background: var(--signature-glow-strong); border-color: var(--signature-dim); color: var(--foreground-bold)`; `:disabled` `color: var(--foreground-disabled); border-color: var(--border-subtle); cursor: not-allowed`. `.chip__label { max-width: 24ch; overflow: hidden; text-overflow: ellipsis; }` `.chip__count { color: var(--foreground-subtle); }` (pressed: `--foreground-default`). `.chip__swatch { width: 8px; height: 8px; border-radius: var(--radius-xs); }` with `.chip__swatch--cat-1 … --cat-6 { background: var(--cat-N); }`. `.overflow-list { display: flex; flex-direction: column; align-items: stretch; gap: var(--space-micro); }` and `.overflow-list .chip { justify-content: space-between; }`. `@media (max-width: 768px) { .chip, .overflow-more { min-height: 44px; } }`.

- [ ] **Step 1: Unit tests.** `overflow-row.test.js`: `fitCount([50,50,50], 160, { gap: 5 })` → 3; `([50,50,50], 150, { gap: 5, moreWidth: 40 })` → 1; `([50], 10, { moreWidth: 40 })` → 0; `([], 100)` → 0; line 6. `chips.test.js`: `filterChip` structure; a label `<img src=x onerror=alert(1)>` is text (no `img` element); `title` equals the label when not given; **"a pressed chip at zero count stays enabled; released, it disables"** (Review Focus 3): `chipRow` with `{ value: 'blocked', count: 0, pressed: true }` → that button is enabled; `update` with `pressed: false` → disabled, and it is the same button element; `onToggle` receives `(value, event)` with `event.shiftKey` from a synthetic shift-click; `role="group"` labelled by the label span. `epics.test.js`: `epicSwatch('b', [{id:'a'},{id:'b'}])` → 2; the 7th epic → 1; unknown → null.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** + CSS + link + ENFORCED.
- [ ] **Step 4: Mocked tests** — `chips.mock.spec.js` (harness like `modal.mock.spec.js`: boot `#/settings`, import `/js/components/chips.js`, mount a `chipRow` of 20 chips labelled "Epic 01"…"Epic 20" with counts into a `div` of fixed width inside `#screen-mount`):
  - at width 600px: every visible chip has the same `offsetTop` (no wrap), `.overflow-more__count` equals 20 minus the visible count, opening More lists exactly the hidden chips in order;
  - toggling a chip inside More (with a test `onToggle` that calls `row.update` with that chip pressed) keeps More open and flips that chip's `aria-pressed`;
  - **"a pressed chip parked behind More is announced on the More button"** (Review Focus 3): with chip 20 pressed and hidden, More shows "· 1 on" and its accessible name contains "1 selected";
  - widening the container to 1400px returns every chip to the row in order and hides More;
  - **"a click on a visible chip while More is open closes More and toggles the chip"** (Review Focus 2);
  - Escape in More closes it and focuses More; at 390px every chip and More is ≥44px tall; axe with More open, both themes: zero violations; computed `box-shadow` none.
- [ ] **Step 5: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- chips.mock.spec.js` — PASS; `components/chips.css` 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): filter chips that never wrap — pressed state, zero counts disabled unless pressed, the rest behind a More popover — on a reusable overflow row`

---

### Task 8: Link rows, sortable headers, and `truncate()`

**Depends on:** nothing in this plan. **Parallel:** wave 1.

**Files:**
- Create: `viewer/js/lib/text.js`, `viewer/js/components/link-row.js`, `viewer/js/components/sort-header.js`, `viewer/css/components/rows.css`, `viewer/tests/unit/text.test.js`, `viewer/tests/unit/link-row.test.js`, `viewer/tests/unit/sort-header.test.js`, `viewer/tests/rows.mock.spec.js`
- Modify: `viewer/js/components/icon.js` (`sort` glyph), `viewer/tests/unit/icon.test.js` (24 glyphs), `viewer/index.html`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/rows.css`)

**Interfaces:**
- Consumes: `icon()`, `h()`; the detail interceptor in `lib/open-detail.js` (unchanged).
- Produces:
  ```js
  // lib/text.js
  export function truncate(text, { lines = 1, tag = 'span', className = '' } = {}) → HTMLElement
    // class `truncate` (+ `truncate--2` / `truncate--3`), textContent = String(text ?? ''), title = the same string, always.
    // lines other than 1, 2 or 3 → RangeError.
  // components/link-row.js
  export function linkRow({ href, name, content = [], controls = [], tag = 'div', className = '' }) → HTMLElement
  export function isInteractive(node) → boolean   // node is or contains a[href], button, input, select, textarea, [tabindex], [contenteditable]
  // components/sort-header.js
  export function nextSort(sort, key) → { by, dir }   // same key flips 'asc' ↔ 'desc'; another key → { by: key, dir: 'asc' }
  export function sortHeader({ key, label, sortable = true, sort, onSort }) → HTMLTableCellElement
  // icon.js
  ICONS.sort = '<path d="M8 9.5 12 5.5 16 9.5M8 14.5 12 18.5 16 14.5"></path>'
  ```
- `linkRow` behaviour (each line is a test):
  1. Structure: `<tag class="link-row …">` → `a.link-row__link[href]` holding `name` (string or Node) → `div.link-row__content` holding `content` → `div.link-row__controls` holding `controls` (omitted when empty). The anchor's accessible name is its text.
  2. `name` or any `content` node that `isInteractive` → `TypeError` naming the element's tag and class.
  3. The whole row is the link's hit area: `.link-row { position: relative; }` `.link-row__link::after { content: ''; position: absolute; inset: 0; }` `.link-row__controls { position: relative; z-index: 1; }`.
  4. The ring is drawn on the row: `.link-row:has(> .link-row__link:focus-visible) { outline: 2px solid var(--border-focus); outline-offset: 2px; }` and `.link-row__link:focus-visible { outline-color: transparent; }`. Hover: `.link-row:hover { background: var(--card-bg-hover); }` — colour only.
  5. `href` is used verbatim, so the interceptor turns a plain click on `#/task/…` or `#/epic/…` into the detail modal with the link as opener; modified clicks are the browser's.
- `sortHeader` behaviour (each line is a test): `<th scope="col" class="sort-th">`. Sortable: a `button.sort-header[type="button"]` holding `span.sort-header__label` and an icon — the active column `icon('chevron', { size: 12 })` in `span.sort-header__dir.sort-header__dir--asc|--desc`, other columns `icon('sort', { size: 12 })` in `span.sort-header__dir.sort-header__dir--none`. Only the active column's `th` carries `aria-sort` (`"ascending"` / `"descending"`). The button's click calls `onSort(nextSort(sort, key))`. Not sortable: the `th` holds the label span only.
- `rows.css` (tokens only) also carries: `.truncate { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }` `.truncate--2, .truncate--3 { display: -webkit-box; -webkit-box-orient: vertical; white-space: normal; }` with `-webkit-line-clamp: 2` / `3`; `.sort-header { appearance: none; display: inline-flex; align-items: center; gap: var(--space-micro); width: 100%; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; letter-spacing: inherit; text-transform: inherit; text-align: left; cursor: pointer; }` hover `color: var(--foreground-bold)`; `.sort-header__dir--asc .icon { transform: rotate(-90deg); }` `.sort-header__dir--desc .icon { transform: rotate(90deg); }` (static, not hover) `.sort-header__dir--none { color: var(--foreground-subtle); }`.

- [ ] **Step 1: Unit tests** for every line above, plus: `truncate(null).title === ''` and `textContent === ''`; `truncate(42).title === '42'`; `truncate('x', { lines: 4 })` throws `RangeError`; `nextSort({ by: 'title', dir: 'asc' }, 'title')` → desc; `nextSort({ by: 'title', dir: 'desc' }, 'id')` → `{ by: 'id', dir: 'asc' }`; icon test count 24 including `sort`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** + CSS + link + ENFORCED.
- [ ] **Step 4: Mocked tests** — `rows.mock.spec.js` (board mocks as in `task-detail.mock.spec.js`; boot `#/kanban` so the interceptor is installed; import `/js/components/link-row.js` and mount into `#screen-mount` a `linkRow({ href: '#/task/T-102', name: 'T-102 · Re-skin the Kanban cards and columns', content: [<span>In progress</span>], controls: [<button class="copy">Copy id</button>] })` whose copy button sets `window.__copied = true`):
  - a click on the row's content opens the detail modal for T-102; Escape closes it and focus is on the row's link;
  - **"modified clicks are the browser's and a row's own control never opens the row"** (Review Focus 4): Ctrl+click on the content opens a new page (`page.context().waitForEvent('page')`) and no `.modal`; a click on "Copy id" sets `window.__copied` and opens no `.modal`;
  - Tab reaches the link, then the copy button; with the link focused from the keyboard, the row's computed `outline-style` is `solid`;
  - a `<table>` whose header row is built from `sortHeader` for `id` (active asc) and `title`: Tab to the Title button and press Enter → the test's `onSort` received `{ by: 'title', dir: 'asc' }`; after re-rendering with that sort, `th[aria-sort]` count is 1 and it is the Title column;
  - axe on the row and the table, both themes: zero `nested-interactive`, `color-contrast`, `aria-*`.
- [ ] **Step 5: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- rows.mock.spec.js` — PASS; `components/rows.css` 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): link rows whose controls are siblings, sortable headers with aria-sort, and a truncate() that always keeps the full text`

---

### Task 9: Topbar controls on the shared components

**Depends on:** Task 4 (`task-detail-document.js`). **Parallel:** wave 3, beside Task 5.

From plan 1's carry list ("Buttons and segmented control: `.tm-segmented`, `.tm-action` … Remove dead 'coming soon' controls"), spec §5.2, §5.4, X-08.

**Files:**
- Create: `viewer/css/components/toolbar.css`, `viewer/tests/unit/topbar.test.js`
- Modify: `viewer/js/lib/topbar.js`; `viewer/css/components.css` (delete `.tm-subcount`, `.tm-search*`, `.tm-segmented*`, `.tm-action*`, `.cmp-kbd` and the mobile block that sizes them, ~lines 52–61, 190–312, 378–411); the caller lines `viewer/js/screens/kanban.js:153`, `viewer/js/screens/table.js:68`, `viewer/js/screens/ideas.js:313` (`icon: 'plus'`), `viewer/js/components/task-detail-document.js:91` (`icon: 'edit'`), `viewer/js/screens/issues.js:88-92` and `viewer/js/screens/sessions.js:53-60` (delete the disabled "coming soon" button and its `appendChild`); `viewer/index.html`; `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/toolbar.css`); `viewer/tests/shell.mock.spec.js`

**Interfaces:**
- Consumes: `.btn` family, `icon()`, `ICONS`.
- Produces (same function names and options; class names `tm-search`, `tm-segmented`, `tm-subcount`, `cmp-kbd` kept because specs select on them):
  - `tmAction({ icon, label, variant, title, onClick, href, disabled })`: `icon` is an `ICONS` name (an unknown name throws, as `icon()` does); classes are `btn` + variant: `'primary'` → `btn--primary`, `'ghost'` → `btn--ghost`, `'icon'` → `btn--ghost btn--icon`, none → `btn--secondary`. No `tm-action` class.
  - `tmSearch`: the clear control is `button.tm-search__clear.btn.btn--ghost.btn--icon.btn--sm[aria-label="Clear search"]` holding `icon('dismiss', { size: 14 })` (no `×` text).
  - `tmSegmented`: unchanged API and `aria-pressed`; restyled.
- `toolbar.css` (tokens only): `.tm-search { display: inline-flex; align-items: center; gap: var(--space-xs); flex: 1 1 280px; min-width: 0; min-height: 32px; padding: 0 var(--space-xs) 0 var(--space-sm); background: var(--bg-recessed); border: 1px solid var(--field-border); border-radius: var(--radius-md); }` hover `border-color: var(--field-border-hover)`; `.tm-search > .icon { color: var(--foreground-subtle); }` `.tm-search > input { flex: 1 1 auto; min-width: 0; border: 0; background: transparent; color: var(--foreground-bold); font-family: var(--font-narrator); font-size: var(--size-narrator-small); }` placeholder `--foreground-subtle`; `.tm-search__clear { display: none; }` `.tm-search--has-value .tm-search__clear { display: inline-flex; }`; `.cmp-kbd { font-family: var(--font-technical); font-size: var(--size-technical-small); color: var(--foreground-subtle); border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 0 var(--space-micro); }`; `.tm-subcount { font-family: var(--font-technical); font-size: var(--size-technical-small); color: var(--foreground-subtle); }`; `.tm-segmented { display: inline-flex; flex: 0 0 auto; border: 1px solid var(--border-strong); border-radius: var(--radius-md); overflow: hidden; }` `.tm-segmented > button { min-height: 32px; padding: 0 var(--space-sm); border: 0; background: transparent; color: var(--foreground-default); font-family: var(--font-declaration); font-weight: var(--font-declaration-weight); font-size: var(--size-technical-small); letter-spacing: var(--tracking-wide); text-transform: uppercase; cursor: pointer; }` `.tm-segmented > button + button { border-left: 1px solid var(--border-strong); }` (a neutral structural divider) hover `background: var(--ground-15); color: var(--foreground-bold)`; `[aria-pressed="true"] { background: var(--signature-fill); color: var(--on-accent-fill); }`; `@media (max-width: 768px)` `.tm-search, .tm-segmented > button, .tm-search__clear { min-height: 44px; }`.

- [ ] **Step 1: Tests.** Unit (`topbar.test.js`, jsdom): `tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task' })` has classes `btn btn--primary`, an `svg.icon[aria-hidden="true"]`, the label text and `aria-label="Add task"`; each variant's classes; `tmAction({ icon: '+' })` throws; `tmSearch()` clear button is a `button` with an `svg` and no `×`; `tmSegmented` toggles `aria-pressed`; the source of `screens/issues.js` and `screens/sessions.js` (read with `readFileSync`) has no `coming soon` (case-insensitive). Mocked (`shell.mock.spec.js`): on `#/kanban` the "Add task" button has classes `btn` and `btn--primary` and is visible at 1440 and 390; the search field shows the focus ring on its wrapper when focused from the keyboard (existing check still passes); axe on `#topbar` in both themes: zero `color-contrast`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**, delete the legacy blocks, link `toolbar.css` after `button.css`, extend ENFORCED. **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock` (full mocked suite: every screen builds its topbar with these) — PASS.
- [ ] **Step 5: Commit** — `feat(viewer): topbar actions, search, segmented control and counts on the shared button and field styles; no "coming soon" controls`

---

### Task 10: Topbar row 2 parks what does not fit behind "Filters"

**Depends on:** Tasks 7, 9. **Parallel:** wave 4, beside Tasks 6, 11.

From spec §4 ("Filters that don't fit collapse into a 'Filters' popover; nothing wraps") and plan 1's carry list ("topbar row 2 at 390").

**Files:**
- Modify: `viewer/js/lib/topbar.js`, `viewer/css/shell.css` (row 2 rules), `viewer/tests/shell.mock.spec.js`

**Interfaces:**
- Consumes: `overflowRow` (Task 7).
- Produces: `claimTopbar()` keeps its contract (returns `#topbar-actions`, empties `#topbar-count` and `#topbar-primary`); the first call installs `overflowRow(#topbar-actions, { moreLabel: 'Filters', moreIcon: 'sliders', popoverLabel: 'Filters', keep: (el) => el.matches('.tm-search') })` once for the page's life; every call first runs its `reset()`, then removes every child of row 2 except the Filters button.
- Behaviour (each line is a test):
  1. Row 2 never scrolls sideways or wraps: `.topbar-row2 { overflow: hidden; }` (replacing `overflow-x: auto`); `.topbar-row2:empty` still hides it, and a row holding only the hidden Filters button is hidden too (`.topbar-row2:not(:has(> :not([hidden])))`).
  2. At 390×844 on `#/table`: row 2's `scrollWidth <= clientWidth`, the search field and Filters are visible, and the controls that did not fit are inside the Filters popover and work there (the "Add task" button opens the Create form).
  3. In the Filters popover parked controls are a column; a parked `.tm-chip-row` wraps (`.overflow-list .tm-chip-row { flex-wrap: wrap; }`).
  4. At 1440×900 on `#/table` nothing is parked and Filters is hidden.

- [ ] **Step 1: Mocked tests** (`shell.mock.spec.js`): lines 2–4; **"leaving a screen with its controls parked behind Filters leaves nothing behind"** (Review Focus 5): at 390×844 on `#/table`, open Filters, then `page.evaluate(() => { location.hash = '#/kanban'; })`; expect no element with placeholder `Filter… (prefix ! to exclude)` anywhere in the document, no open `.popover`, and the Kanban search field visible in row 2.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `npm --prefix <wt>/viewer run test:mock` (full) — PASS; `shell.css` 0 violations.
- [ ] **Step 5: Commit** — `feat(viewer): topbar row 2 stays on one line — what does not fit waits behind a Filters popover and is cleared with the screen`

---

### Task 11: The Table adopts chips and sortable headers

**Depends on:** Tasks 7, 8, 9. **Parallel:** wave 4, beside Tasks 6, 10.

The proving consumer for §5.3 and §5.6's header: the Table's chip rail is the one place with every chip feature at once (four groups, a long epic list, filters that reach zero) and its headers are TB-03. Its layout work (fixed columns, sticky id, stacked rows) stays in plan 3b.

**Files:**
- Modify: `viewer/js/screens/table.js`, `viewer/css/screens/table.css` (the chip and header rules only, on tokens; the file is not added to ENFORCED until plan 3b)
- Create: `viewer/tests/table.mock.spec.js`

**Interfaces:**
- Consumes: `chipRow` (Task 7), `epicSwatch` (Task 7), `sortHeader`, `nextSort` (Task 8), `TASK_STATUS`, `PRIORITY` (status.js), `chipClickNext`, `CHIP_CLICK_HINT`.
- Behaviour (each line is a test):
  1. The rail holds one `chipRow` per group with options (Status, Priority, Epic, Area — a group with none is omitted, as today), built once at mount and updated with `row.update()` on every paint. Labels: Status and Priority use the `TASK_STATUS` / `PRIORITY` words ("In progress", "Critical"); Epic uses the epic's name, else its id, with `swatch: epicSwatch(id, epics)`; Area uses the value. `count` = tasks in the whole backlog with that value. `hint` = `CHIP_CLICK_HINT`. Click keeps today's rule through `chipClickNext`.
  2. "Clear filters" is `button.btn.btn--ghost.btn--sm` with `icon('dismiss', { size: 14 })`, present only while a filter or the search is set.
  3. Every header is a `sortHeader`; the sort is persisted as today (`prefs.patch({ table: state })`).
  4. `.tbl-chips` lays the rows out as `display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-lg);` — a whole group may move to the next line, chips inside a group never wrap. Status and Priority rows are `flex: 0 0 auto`; Epic and Area rows `flex: 1 1 240px; min-width: 240px`. The old `.tbl-chip*`, `.tbl-th.is-sortable*`, `.tbl-th-arrow` rules are deleted; new rules use tokens only.

- [ ] **Step 1: Mocked tests** (`table.mock.spec.js`; board = `BOARD` plus 12 extra epics named "Epic A"…"Epic L", one of which has no tasks; capture `PUT /api/viewer/prefs` bodies): chips are buttons with `aria-pressed`; click "Done" → every row's status cell reads Done and the chip is pressed; Shift+click "Blocked" → Done and Blocked rows only; the epic with no tasks is disabled; at 1440×900 the Epic row's visible chips share one `offsetTop` and More is visible; at 390×844 no chip row wraps; Tab to the Title header button, Enter → `th[aria-sort="ascending"]` is Title and the first row is the alphabetically first title; Enter again → `descending`; the last prefs PUT carries `table.sort` `{ by: 'title', dir: 'desc' }`; axe on `#screen-mount` in both themes: zero `nested-interactive`, `color-contrast`, `aria-*`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `npm --prefix <wt>/viewer run test:mock -- table.mock.spec.js shell.mock.spec.js`; style-rules report shows `screens/table.css` with fewer violations than before (state both numbers) — PASS.
- [ ] **Step 5: Commit** — `feat(viewer): the Table's filters are chip rows with a More overflow and its headers are buttons that set aria-sort`

---

### Task 12: Conflict banner restyle

**Depends on:** Task 8 (`truncate`). **Parallel:** wave 2, beside Tasks 3, 4, 7.

**Files:**
- Rewrite: `viewer/css/components/conflict-banner.css` (tokens only; ENFORCED)
- Modify: `viewer/js/components/edit/conflict-banner.js`, `viewer/js/components/edit/task-actions.js` (passes `labels`), `viewer/css/tokens.css` (`--conflict-banner-height: 0px` on `:root`, hand-written block), `viewer/css/components/modal.css` (offset), `viewer/tests/unit/conflict-banner.test.js`, `viewer/tests/unit/style-rules.test.js`
- Create: `viewer/tests/conflict-banner.mock.spec.js` (its own `openEdit`/`conflict` helpers, modelled on `task-form.mock.spec.js:31-50, 384-409`)

**Interfaces:**
- Consumes: `marker()` (status.js), `truncate()` (Task 8), `taskSchema` (labels).
- Produces:
  ```js
  showFieldConflict({ entityKind, entityId, fieldKey, fieldLabel, localValue, currentValue, currentEtag, onKeepMine, onUseServer })  // unchanged
  showFullConflict({ entityKind, entityId, localDraft, currentValue, currentEtag, labels = {}, onResolve, onDismiss })
    // labels: { [key]: string }; a key without one is shown sentence-cased ('depends_on' → 'Depends on')
  export function conflictValueText(v) → string
    // null, '', [], {} → '—'; array → items joined ', '; plain object → 'key: value' lines joined '\n'
    // (a non-string value as JSON); anything else → String(v)
  ```
  `task-actions.js` passes `labels` built from `taskSchema(...).fields` (`key → label`).
- Behaviour (each line is a test):
  1. The banner is `div.cb-banner[role="region"][aria-labelledby=<headline id>]`. Its headline `div.cb-headline[role="alert"]` holds `marker({ label: 'Conflict', shape: '▲', tone: 'warning' })` and a sentence in `--foreground-bold`: field — `"<label>" on <kind> <id> was changed by someone else`; full — `<kind> <id> was changed by someone else — choose what to keep for each field`. No text is drawn in a hue (fixes the light amber headline that failed AA).
  2. Each differing key is a `div.cb-multi-row`: `div.cb-key[id]` with the label; "Yours" and "Saved" values (Technical label tags in `--foreground-subtle`, values `truncate(conflictValueText(v), { lines: 3 })` on `--overlay-surface-sunken` in `--foreground-bold`); a `div[role="radiogroup"][aria-labelledby=<cb-key id>]` with radios "Keep mine" / "Use server" whose `name` is unique per banner (`cb-<banner seq>-<key>`).
  3. Buttons keep their names and classes (`.cb-keep-mine`, `.cb-use-server`, `.cb-dismiss`, `.cb-resolve` on `.btn`).
  4. While a banner is shown, `document.documentElement.style` holds `--conflict-banner-height: <banner height>px`, kept current by a `ResizeObserver` when available and on window `resize`; it is removed when the banner goes.
  5. `modal.css`: `.modal-overlay { padding-top: calc(var(--space-2xl) + var(--conflict-banner-height)); }` and `.modal { max-height: calc(100vh - 2 * var(--space-2xl) - var(--conflict-banner-height)); }`; at ≤768px `.modal-overlay { padding: var(--conflict-banner-height) 0 0; }` (the confirm's own padding rule stays). The dialog header is never under the banner.
  6. `.cb-banner { position: fixed; top: 0; left: 0; right: 0; z-index: 90; max-height: 50vh; overflow: auto; box-sizing: border-box; padding: var(--space-md) var(--space-lg); background: var(--overlay-surface); border-bottom: 1px solid var(--border-strong); color: var(--foreground-default); font-family: var(--font-narrator); font-size: var(--size-narrator-small); }`; at ≤768px rows stack and `.cb-actions .btn` are full width and 44px tall. No legacy alias remains in the file.

- [ ] **Step 1: Tests.** Unit: lines 1–4 (`labels: { title: 'Title' }` shows "Title"; `depends_on` without a label shows "Depends on"; each `radiogroup`'s `aria-labelledby` resolves to the label text; two banners shown one after the other use different radio names; `conflictValueText` table: `null`, `''`, `[]`, `{}`, `['a','b']`, `{ spec: 'a.md', plan: 'b.md' }`, `{ spec: ['x'] }`, `3`; the property is set while shown and absent after dismiss); the existing tests keep passing. Mocked (`conflict-banner.mock.spec.js`): edit T-102, change the title, answer the PATCH with the 409 `STALE` body (with `current_etag`): at 1440×900 and 390×844 the banner's bottom edge is at or above the dialog header's top edge (±1px); `getByRole('radiogroup', { name: 'Title' })` exists; Tab from the dialog reaches the banner's radios and back; with 8 changed fields at 390×844 the banner is at most half the viewport tall, scrolls, and the dialog header stays visible; axe on `#conflict-banner-host` in both themes: zero violations.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** unit suite + `npm --prefix <wt>/viewer run test:mock -- conflict-banner.mock.spec.js task-form.mock.spec.js modal.mock.spec.js` — PASS; `components/conflict-banner.css` enforced, 0 violations; the contrast test still passes.
- [ ] **Step 5: Commit** — `feat(viewer): the conflict banner speaks in field labels, names its choices, holds AA in both themes, and pushes dialogs down instead of covering their header`

---

### Task 13: Verification

**Depends on:** Tasks 1–12.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs` (new scenes; still mocked, static-served, never a live server), `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11 bullets)

- [ ] **Step 1: Scenes.** Add to `ALL_SCENES` (with the fixtures they need: twelve extra epics on the capture board, `/api/ideas` with three ideas, the `STALE` 409 for T-102's PATCH):
  - `handover-menu` — detail modal of T-102 with the rail's status menu open;
  - `relation-suggestions` — Edit T-102 with `T-1` typed in Depends on;
  - `conflict-banner` — Edit T-102 after a 409 with `current_etag`;
  - `ideas-create` — `#/ideas`, New Idea, untouched;
  - `ideas-create-error` — after a 500 on save;
  - `table-chips` — `#/table` with the Epic row's More open;
  - `table-sorted` — `#/table` sorted by Title descending;
  - `topbar-filters` — `#/table` with the Filters popover open (at the desktop width it captures the row with Filters hidden).
  Popover scenes run axe on the page (`scope: 'body'`) so the popover is included.
- [ ] **Step 2:** Run `node viewer/tests/tools/capture-modals.mjs <scratch dir>` (all scenes, both themes, both widths). LOOK at every new image and at one old one per scene family. Fix what is wrong within this plan's files, with a test where one makes sense. In the report describe each new image plainly and list anything that belongs to a later plan.
- [ ] **Step 3:** Full runs: unit, mocked, and the server pytest command from Global Constraints. Report exact counts.
- [ ] **Step 4:** Append to spec §11 one bullet per ruling this plan made against earlier spec text:
  - §5.11: the icon set stays a JS map in `icon.js` (plan 1), not an SVG sprite file; epics, sessions and settings use `folder`, `document` and `sliders`; `sort` is added.
  - §10: the conflict banner is changed beyond restyling — labels, named radio groups, AA headline, offset dialogs — because 2a made it keyboard-reachable and it failed contrast.
  - §5.8: a popover is placed after its anchor in the DOM and positioned fixed; a press outside it is never swallowed.
  - §5.3: a chip's `title` is its full label; the shift-click hint moves to the group label.
  - §4: topbar row 2 parks what does not fit behind "Filters" (Task 10), keeping the search field.
  - §9: the Table adopts chips and sortable headers in plan 2b as the proving consumer; its layout stays in plan 3b.
- [ ] **Step 5: Commit** — `test(viewer): capture the shared components in both themes and widths` and, separately, `docs(viewer): spec §11 records plan 2b's rulings`
