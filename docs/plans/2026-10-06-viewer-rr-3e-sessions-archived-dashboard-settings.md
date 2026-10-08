<!-- User intent: put Sessions, Archived, the Dashboard and Settings on the RR components in both themes, by keyboard and at phone width — and make the shared right rail a plain panel whose status changes say in words when they fail — so these four screens stop being the viewer's leftover one-offs. -->

# Viewer × Reality Reprojection — Plan 3e: Sessions, Archived, Dashboard, Settings and the right rail

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sessions, Archived, Dashboard and Settings rebuilt on the plan 1/2a/2b foundation and matching spec §6 in dark and light at 1440px and 390px, with the generic right rail (X-13) turned into a border-separated panel on tokens, every pointer-only target on these screens made a real control, every write they make said in words when it fails, and the audit findings SE-01–05, AR-01–03, DB-01–04, ST-01–02 and X-13 closed.

**Architecture:** The right rail stops being a fixed overlay with a shadow and becomes an in-flow panel the screen places (docked beside the Sessions timeline on wide screens, above it on narrow ones); it is built from nodes, never from HTML strings, and it takes and returns focus. Screens consume the shared parts — `chipRow`, `linkRow`, `truncate`, `stateBlock`, `tmSegmented`, `openPopover` through the handover status menu, `describeWriteError` — and keep only their own layout CSS. The Dashboard gains one new part, the summary strip, fed from the store's board and one read each of issues and bugs.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §5 item 12 (right rail), §6 Sessions, Archived, Dashboard, Settings, §11 (overrides; in particular "Dashboard notes" — solid coloured paper in both themes with fixed dark ink, tilt and folded corner kept — and "theme": dark is the default). Audit `docs/specs/2026-10-01-viewer-audit.md`: DB-01–04, SE-01–05, AR-01–03, ST-01–02, X-03, X-07, X-12, X-13. Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "3e" and "Controller allocation".

**Index:** `docs/plans/2026-10-06-viewer-rr-3-screens.md` — its Global Constraints, File ownership, Review Focus and commands are binding and not restated here.

**Depends on:** Plan 2b (integration HEAD 638acec). Cross-track inbound: Tasks 2 and 3 wait for 3a Task 2 (row 1 at 390px); Task 8 Step 3b uses 3b Task 2 when it has merged. Outbound: 3c's rail panels wait for this plan's Task 1 to merge.

## Global Constraints

The plan 3 index's Global Constraints apply to every task. 3e-specific:

- **Worktree and port.** `<wt>` = `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr3e`, branch `rr3/e-sessions-archived-dashboard-settings` from the integration HEAD. Mocked specs run with `MOCK_PORT=8835`. Port 8835 has no listener when a task finishes (`netstat -ano | grep ':8835 '` prints nothing).
- **Task 1 first.** Task 1 is reported to the controller as soon as it passes review so it merges before any other 3e task; 3c's rail panels wait on it.
- **Rail content is nodes.** Nothing in `right-rail.js`, `sessions.js`, `timeline.js`, the desk components or `archived.js` assigns `innerHTML` (or uses `h(…, { html })`) with data; the only exception is `renderMarkdown()` / `mountMarkdown()`. Each task that rewrites one of these files ends with `grep -n "innerHTML\|html:" <file>` showing only `mountMarkdown`/`renderMarkdown` lines.
- **Counts live in row 1.** A screen's count is set with `setTopbarCount(text)` from `lib/topbar.js` after `claimTopbar()`; row 2 holds only the search field. Filter chips sit in a bar at the top of the page, as the Table's do (a chip row nested in row 2's own overflow row would fight it for space). None of these four screens has a primary action, so none uses `claimTopbarPrimary()`.
- **No writes after leaving.** Every screen keeps an `alive` flag set false by its cleanup; a debounced search callback, a late fetch or a write's follow-up refresh does nothing once it is false, so nothing lands in the next screen's topbar.
- **Two small voices.** "Technical label voice" below means `--font-technical`, 800, `--size-technical-label` (11px), uppercase, `--tracking-ultra`, `--foreground-subtle` — for section and group labels only. Tags (kinds, reasons, type words) use the Technical tag voice at 12px, never the 11px label (spec §6 Sessions: badges ≥ 12px).
- **Handover status words.** Status values `open`, `closed`, `superseded` are shown as `Open`, `Closed`, `Superseded` (`HO_STATUS_LABEL`, Task 1). Handover kinds (`mid-task`, `checkpoint`, `wrap`, `standalone`) are shown sentence-cased as neutral Technical tags; neither carries a hue.
- **Owned shared CSS.** `viewer/css/components/handover-status.css` (the pill `right-rail.js` builds) and the new `viewer/css/components/right-rail.css` are this plan's; `components.css` loses only the `.right-rail` block (Task 1). The legacy `.ho-status-pill-*` and `.tm-empty*` rules in `components.css` stay for plan 4.
- **Every CSS file a task rewrites** joins `ENFORCED`: `components/right-rail.css` (Task 1), `screens/sessions.css` (Task 2), `screens/archived.css` (Task 3), `screens/continuity.css` (Task 6), `screens/settings.css` (Task 7). `screens/desk.css` is already enforced and stays at zero violations.
- **Shared append-only files** this plan appends to: `viewer/index.html` (one stylesheet link, Task 1), `ENFORCED`, `viewer/tests/mock-fixtures.js` (fixtures named below), `viewer/tests/tools/capture-modals.mjs` (Task 8 scenes and their `TABLE` keys), and spec §11 (Task 8, bullets appended at the end).

## Review Focus

1. **A handover status change the server refuses or never receives** (a 500 from a locked store, a 409 "already superseded", the network down). The person must read why in words beside the pill, the pill must keep the status the server still has, and a later successful change must clear the message. → Task 1, unit "a failed status change is said beside the pill and the pill keeps its status" and mocked "a failed handover status change is said in words beside the pill" (`right-rail.mock.spec.js`); Task 2, mocked "the sessions rail says a failed status change in words" (`sessions.mock.spec.js`).
2. **A very long note on the Dashboard** (4,000 characters of paragraphs plus a 300-character unbroken URL). It must be clamped with a fade, offer "Show more" that a keyboard reaches and opens, close again with "Show less", and never push the page sideways at 390px. → Task 4, mocked "a very long note is clamped and expands from the keyboard" (`desk.mock.spec.js`).
3. **A theme chosen in Settings** must apply at once, survive a reload with no flash of the other theme (the pre-paint script reads it before boot), reach the server, and stay in step with the topbar toggle. → Task 7, mocked "a theme chosen in Settings applies at once, persists, and never flashes" and "the topbar toggle moves the Settings control with it" (`settings.mock.spec.js`).
4. **Both themes on every 3e screen.** Light theme is where the old palette failed (hue-as-text badges, accent chips, the 2.26:1 "+17 older"). → every task's mocked spec runs axe in dark and light with zero violations on what it changed; Task 8 runs axe on all four routes in both themes and reports zero `color-contrast`.
5. **Leaving a screen, or the data changing under it** (the search debounce still pending, the rail and its status menu open, a note mid-edit; the board poll redrawing Archived or the summary strip while a row has focus). Nothing may write into the next screen's topbar, no popover or listener may survive, focus must not fall to `<body>`. → Task 2 "leaving with the rail and its status menu open leaves nothing behind" and "leaving within the search debounce leaves the next screen's count alone"; Task 3 "a board redraw keeps focus on the focused row" and the same debounce test; Task 4 "leaving with a note mid-edit saves it once and throws nothing"; Task 5 "a board redraw updates the counts in place and keeps focus".

**Index Review Focus pins:** (1) 390px with volume → Task 2 "thirty sessions with long slugs stay inside a phone screen", Task 3 "forty archived tasks with long titles stay inside a phone screen", Task 8 measurements. (2) Light theme → item 4 above. (3) Keyboard walk per screen → Task 2 "keyboard: a row opens the rail, Escape closes it and focus returns to the row", Task 3 "keyboard: an archived row opens its task and Escape returns to the row", Task 4 "pin and archive work from the keyboard", Task 6 "a handover row expands from the keyboard", Task 7 "keyboard: Tab reaches the theme control and Enter applies it". (4) Another writer → item 5 above (Tasks 3, 5). (5) Leaving a screen → item 5 above (Tasks 2, 3, 4).

## Carry-in

Checked against the integration HEAD 638acec:

- *Archived timer ReferenceError* — `archived.js` has no timer; dropped.
- *Dead widget ids in the default `dashboard.layout`* — `VIEWER_PREFS_DEFAULTS["dashboard"]["layout"]` is already `[]` (`taskmaster/taskmaster_v3.py:3622-3625`) and no viewer code reads `dashboard.layout`; stored values are kept by design (`load_viewer_prefs` preserves unknown keys). No task and no server change.
- *Sessions "coming soon" New note* — removed in 2b Task 9; Sessions and Archived have no primary action left to move to row 1.
- *Sessions pill a non-focusable span; `h.status` captured at mount goes stale* — Task 1 builds it with `statusPill()`, which reads its status from `dataset.status` at click.
- *`openMenu` keeps its last popover* — Task 1.
- *Topbar raised over `.right-rail` while Filters is open* — real: `shell.css:205` lifts the topbar to 70 over the fixed rail at 50. Dissolved by Tasks 1–2: the rail is no longer fixed.
- *Right rail shadow, `--bg-panel`, serif, italic, dead `.ic-btn[disabled]` rule* — Task 1 deletes the whole block from `components.css`.
- *Sessions slugs, 9px hue badges, serif, 9,160px page; SE-01 off chip; SE-02 span chips* — Task 2.
- *Archived italic "superseded", clipped titles, heading order* — Task 3.
- *Dashboard note bodies clipped; "+17 older"; composer autofocus* — Tasks 4 and 6.
- *Dashboard project label* — never rendered (`desk.js` calls `store.projectName`, which the store does not have); Task 5 deletes it.
- *Settings native radios, one setting* — Task 7.
- *Pointer-only targets* (index Review Focus 3) on these screens: timeline rows, chips, thread cards, the rail's "click to copy" path (Tasks 1–2); note bodies, decision options, continuity rows (Tasks 4, 6).
- *Epic progress on the Dashboard* — the summary strip shows no epic progress, so this plan does not wait for 3b's `epic-format.js`.
- *Legacy `.ho-status-pill`/`.tm-empty` rules in `components.css`* — plan 4 per the index; untouched.

## Order

All tasks run in the one 3e worktree, in this order; each is reviewed and handed to the controller before the next starts.

| # | Task | Waits for | Why this order |
|---|---|---|---|
| 1 | Right rail | — | 3c waits on it; Task 2 builds on its API |
| 2 | Sessions | 1; 3a Task 2 | Uses the rail API and `HO_STATUS_LABEL` |
| 3 | Archived | 3a Task 2 | Independent; after 2 only because one worktree |
| 4 | Dashboard notes | — | Owns `desk.js` board part, `desk.css` |
| 5 | Dashboard summary strip | 4 | Same `desk.js`, `desk.css`, `desk.mock.spec.js` |
| 6 | Dashboard continuity band | 5 | Same files; deletes the live `desk.spec.js` once its last check is covered |
| 7 | Settings | — | Independent |
| 8 | Verification | 1–7 | Captures and full runs |

---

### Task 1: The generic right rail — an in-flow panel on tokens, built from nodes, whose status changes say in words when they fail

**Depends on:** nothing in this plan.

**Files:**
- Create: `viewer/css/components/right-rail.css`, `viewer/tests/right-rail.mock.spec.js`
- Modify: `viewer/js/components/right-rail.js`; `viewer/js/screens/sessions.js` (only the `RightRail` construction, `openSessionDetail`, `openHandoverDetail`, `bindRailClose` (deleted), `railSessionTimeLine`, `renderSessionRail`, `renderHandoverRail`); `viewer/css/components/handover-status.css` (`.ho-status-error`); `viewer/css/components.css` (delete the block from `/* RightRail (shared with task-detail in Plan 3) */` through `.right-rail .files-list .more { … }`, today lines 81–175, nothing else); `viewer/index.html` (link `css/components/right-rail.css` after `handover-status.css`); `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/right-rail.css`); `viewer/tests/unit/right-rail.test.js`; `viewer/tests/mock-fixtures.js` (append `SESSIONS`, `SESSION_DETAILS`, `THREADS`)

**Interfaces:**
- Consumes: `openPopover` (2b; its `onClose`), `describeWriteError(e, { noun })` (`edit/write-errors.js`), `topModal()` (`modal.js`), `icon()`, `h()` (`util/h.js`), `truncate()` (`lib/text.js`), `bindCopy()` (`lib/copy.js`), `formatRelative`, `formatAbsolute`, `formatDurationCompact` (`lib/time.js`).
- Produces (`viewer/js/components/right-rail.js`; `railPanels`, `mountRightRail`, `statusPill(handoverId, status)` and `openStatusMenu(anchor, handoverId, currentStatus)` keep their signatures and output — 3c consumes them unchanged):
  ```js
  export const HO_STATUS_LABEL;   // Object.freeze({ open: 'Open', closed: 'Closed', superseded: 'Superseded' })
  export async function postHandoverStatus(handoverId, status) → void
    // POST /api/handover/<id>/status { status, reason: 'viewer-override' }. Throws, in api.js's shape:
    //   409 → Error(message = body.error when a non-empty string, else 'stale'), code 409
    //   other non-2xx → Error(`POST <path> → <status>`), code = status, reason = body.error when a string
    //   network failure → the fetch rejection, no code
  export class RightRail {
    constructor({ host, label = 'Details' })   // host: Element the rail is appended to; missing → TypeError
    open({ kind = 'plain', title, head = [], body = [], opener = null, onClose }) → HTMLElement
      // kind: 'session' | 'handover' | 'plain'; title: string (required); head/body: Node[]
    close()          // idempotent; onClose runs once
    isOpen() → boolean
  }
  ```
  Rail DOM: `aside#right-rail.right-rail.right-rail--<kind>[aria-label=<label>][aria-labelledby=rr-title-<n>]` → `div.rr-h` (the `head` nodes, then `button.rr-close.btn.btn--ghost.btn--icon.btn--sm[aria-label="Close details"]` holding `icon('dismiss', { size: 14 })`) → `h2.rr-title#rr-title-<n>[tabindex="-1"]` (the title) → the `body` nodes.
- Behaviour contract (each line is a test):
  1. `open()` closes any open content first, appends the aside to `host`, and focuses the title (`focus()` without `preventScroll`, so a narrow screen scrolls to it). No `body.rail-open` class is set.
  2. `close()` removes the aside, runs `onClose` once, and — when focus was inside the rail or on `<body>` — focuses `opener` if it is still connected.
  3. Escape (document `keydown`) closes the rail unless the event is already `defaultPrevented` or a modal is open (`topModal()` is not null). An Escape that the status menu handles (it calls `preventDefault`) closes only the menu.
  4. The close button closes it.
  5. A status change that fails shows `describeWriteError(err, { noun: 'handover' })` in `span.ho-status-error[role="alert"][id]` inserted directly after the pill it was chosen from (replacing one already there); that pill gets `aria-describedby` = its id; every pill for the handover keeps its `data-status`, class and word. A later successful change for that handover removes the message and the `aria-describedby`.
  6. `openMenu` is cleared in the popover's `onClose`, so after a menu closes by Escape, outside press or a redraw, the next pill's first click opens its menu (`openPopoverCount()` is 1).
- Sessions rail content (built in `sessions.js` with `h()`; text via text nodes only):
  - Session: head = `span.rr-kind` "Thread", `span.rr-when` (the existing `railSessionTimeLine`); title = `s.tldr || s.id`; body = `div.rr-slug` with `s.id` (only when the title is the tldr), `div.rr-meta` "Tasks" + one `a.rr-task[href="#/task/<id>"]` per task id (or "—"), then `section.rr-section` with `h3` "Handovers" + `span.rr-count`, one `button.rr-ho.btn.btn--ghost.btn--sm` per handover (kind tag, id, relative time; no tldr — it is said once, in the handover's own rail) that opens that handover's rail with this button as opener.
  - Handover: head = `span.rr-kind` (kind sentence-cased: `mid-task` → "Mid-task"), `statusPill(h.id, h.status || 'open')`, `span.rr-when` (`formatRelative`); title = `h.tldr || h.id`; body = `div.rr-slug` (id), `div.rr-meta` with "Session" `a[href="#/sessions/<owner.id>"]` and `button.rr-path.btn.btn--ghost.btn--sm[aria-label="Copy path .taskmaster/handovers/<id>.md"]` (icon `copy` + `truncate(path)`, `bindCopy`), `section.rr-resume` (`span.rr-label` "Resume", `button.btn.btn--ghost.btn--sm` icon `copy` + "Copy", `div.rr-resume__body` text), "What's done" and "What's open" sections (`ul.rr-checklist` › `li.rr-check` with `icon('check'|'minus', { size: 14 })` `aria-hidden` and the item text), "Related" (`a.rr-task` per task id), "Files touched" (`ul.rr-files` › `li` › `truncate(path)`, first 8, then plain text "+ n more").
  - `new RightRail({ host: root.querySelector('[data-role=rail-host]'), label: 'Session details' })`. The rows that open the rail are still the old divs in this task; Task 2 makes them buttons and passes them as `opener`.
- `right-rail.css` (tokens only): `.right-rail { box-sizing: border-box; display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; padding: var(--space-lg); background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); color: var(--foreground-default); font-family: var(--font-narrator); font-size: var(--size-narrator-small); overflow-wrap: anywhere; }` (no position, no width, no z-index — the host places it). `.rr-h { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); }` `.rr-close { margin-left: auto; }` `.rr-kind` in the Technical tag voice (`--font-technical`, `--font-technical-weight`, `--size-technical-small` — spec §3.3 puts tags at 12px — `--foreground-default`) with `border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 0 var(--space-micro);` `.rr-when, .rr-slug, .rr-count, .rr-task, .rr-files` Technical (`--font-technical`, `--font-technical-weight`, `--size-technical-small`); `.rr-when, .rr-slug, .rr-count` `--foreground-subtle`. `.rr-title { margin: 0; font-family: var(--font-narrator); font-weight: 700; font-size: var(--size-narrator-large); line-height: var(--leading-heading); color: var(--foreground-bold); }` `.rr-title:focus-visible` uses the global ring. `.rr-section > h3, .rr-label` Technical label voice. `.rr-resume { display: flex; flex-direction: column; gap: var(--space-xs); padding: var(--space-md); border: 1px solid var(--border-strong); border-radius: var(--radius-md); }` `.rr-resume__body { color: var(--foreground-bold); font-size: var(--size-narrator-default); line-height: var(--leading-body); white-space: pre-wrap; }` `.rr-checklist, .rr-files { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-micro); }` `.rr-check { display: flex; gap: var(--space-xs); align-items: flex-start; }` `.rr-check > .icon { flex: 0 0 auto; color: var(--foreground-subtle); }` `@media (max-width: 768px) { .right-rail .btn, .right-rail .ho-status-pill { min-height: 44px; } }`. In `handover-status.css`: `.ho-status-error { display: block; flex-basis: 100%; color: var(--foreground-bold); font-size: var(--size-narrator-small); }` and `:has(> .ho-status-error) { flex-wrap: wrap; }` (a full-width sentence under the pill row wherever the pill sits — Sessions' `.rr-h` and 3c's `.td-handover-head` alike, so 3c needs no rule for it; never hue-coloured). The mocked test on `#/task/T-102` also asserts the alert's box is as wide as its row and starts below the pill.

- [ ] **Step 1: Unit tests** (`right-rail.test.js`, jsdom, the file's `page()` and `menuPage()` helpers). Replace the two string-render tests with: "open() puts a labelled panel in its host and focuses its title" (`new RightRail({ host })`, `open({ title: 'M1 shipped', body: [p] })` → `host.querySelector('aside#right-rail')`, `aria-labelledby` resolves to `M1 shipped`, `document.activeElement` is `.rr-title`); "open() twice swaps the content"; "close() hands focus back to the opener and runs onClose once" (opener button in the host, focus in the rail, `close()` twice → `onClose` called once, focus on the opener); "Escape closes the rail unless a menu or a modal took it" (plain Escape → closed; an Escape dispatched with `preventDefault()` already called in a capture listener → open; with `openModal({ title: 'x' })` open, on a page built from `modal.test.js`'s `PAGE` markup with the rail host inside `#screen-mount` → the modal closes and the rail stays open); "a rail without a host refuses" (`new RightRail({})` throws `TypeError`). Status tests (`global.CSS = { escape: (s) => s }`; `global.fetch` stubs returning `{ ok, status, text: async () => body }` or rejecting): "a failed status change is said beside the pill and the pill keeps its status" — 500 with `'{"error":"sqlite3.OperationalError: database is locked"}'` → the alert after the pill reads exactly "The server could not save this change. Try again in a moment.", the pill's `aria-describedby` is the alert's id, `data-status` is `open`, the twin pill elsewhere has no alert; 409 with `'{"error":"Handover is already superseded by 2026-10-02-wrap"}'` → that sentence; `fetch` rejecting `new TypeError('Failed to fetch')` → "Could not reach the server, so nothing was saved. Check that the viewer is still running."; then a stub returning `{ ok: true }` and choosing Closed again → no `.ho-status-error`, no `aria-describedby`, pill `closed`. "a closed menu does not stay remembered": open the menu on pill A, Escape, click pill B once → `.ho-status-menu` exists and is B's (`aria-expanded="true"` on B), `openPopoverCount()` is 1. `HO_STATUS_LABEL.superseded === 'Superseded'`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/right-rail.test.js` — Expected: the new tests FAIL (`RightRail` builds from strings, no host check, no `.ho-status-error`, `HO_STATUS_LABEL` undefined).
- [ ] **Step 3: Implement** `right-rail.js` per the contract (the Escape listener is added in `open()` and removed in `close()`; `postHandoverStatus` reads the body with `resp.text()` then `JSON.parse` in a `try`); the two sessions rail builders and the two openers in `sessions.js`; `right-rail.css`; `.ho-status-error`; delete the `components.css` block; link the stylesheet; extend ENFORCED; append the fixtures: `THREADS` (the two threads of today's `threads-board.spec.js`), `SESSIONS` (two sessions: `team-relayout` with handovers `2026-07-13-m1-shipped` (open, checkpoint, tldr "M1 shipped") and `2026-07-12-scope` (closed, mid-task, tldr "Scope the relayout"); `guard-hooks-polish` with `2026-07-10-hooks-old` (superseded, wrap, tldr "Old hook plan")), `SESSION_DETAILS` (an object keyed by session id with `{ session, handovers }` in `getSessionDetail`'s shape: each handover with `created`, `done_items`, `open_items`, `task_ids: ['T-102']`, `files_touched` (ten paths, one 140 characters long), `next_action`, `resume_prompt`).
- [ ] **Step 4: Mocked tests** (`right-rail.mock.spec.js`; `mockApi` with `'/api/board': BOARD, '/api/backlog': BOARD`, `'/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED)`, `'/api/bugs': []`, `'/api/sessions': SESSIONS`, `'/api/threads': THREADS`, `'/api/sessions/team-relayout': SESSION_DETAILS['team-relayout']`):
  - **"a failed handover status change is said in words beside the pill"** (Review Focus 1): on `#/task/T-102` answer `POST /api/handover/2026-09-30-kanban-reskin/status` with `{ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } }`; open the pill's menu, choose "closed"; expect the alert next to the pill to read the 5xx sentence, the pill to read `open`, and `#screen-mount` text to contain none of `sqlite3`, `500`, `/api`. Re-route the POST to `{ ok: true }` (`page.unroute` then `page.route`), choose "closed" again: the alert is gone and the pill reads `closed`.
  - "a refusal gives the server's reason": the POST answers `{ status: 409, json: { ok: false, error: 'Handover is already superseded by 2026-10-02-wrap' } }` → that sentence beside the pill.
  - "the sessions rail is a panel in the page": on `#/sessions` click `.ho-child[data-handover-id="2026-07-13-m1-shipped"]`; expect `aside#right-rail` inside `[data-role=rail-host]`, its computed `position` `static`, `box-shadow` `none`, its heading "M1 shipped" focused, the path button named "Copy path .taskmaster/handovers/2026-07-13-m1-shipped.md", a Related link `#/task/T-102`; Escape → no `#right-rail`. At 390×844 the open rail's right edge is ≤ `innerWidth` and `document.documentElement.scrollWidth <= innerWidth`.
  - both themes: axe on `#right-rail` (open on Sessions) and on the task page's rail with the menu open (`scope` body): zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 right-rail.mock.spec.js popover.mock.spec.js task-detail.mock.spec.js` — Expected: PASS; style-rules shows `components/right-rail.css` enforced with 0 violations; `grep -n "innerHTML\|html:" viewer/js/components/right-rail.js` shows only the `renderMarkdown` line; `grep -n "right-rail" viewer/css/components.css` prints nothing.
- [ ] **Step 6: Commit** — `feat(viewer): the right rail is a bordered panel in the page, built from nodes, that takes and returns focus — and a refused handover status change is said beside its pill`

---

### Task 2: Sessions — real rows and chips, titles before slugs, the rail docked beside the timeline

**Depends on:** Task 1; cross-track, 3a Task 2 (row 1 at 390px — this task's 390 checks of `#topbar-count` hold only after it merges).

**Files:**
- Modify: `viewer/js/screens/sessions.js`, `viewer/js/components/timeline.js`, `viewer/tests/threads-board.spec.js` (selectors only), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/sessions.css`), `viewer/tests/mock-fixtures.js` (append `manySessions(n)`)
- Rewrite: `viewer/css/screens/sessions.css`
- Create: `viewer/tests/sessions.mock.spec.js`, `viewer/tests/unit/timeline.test.js`
- Delete: `viewer/tests/sessions.spec.js` (live; asserts the retired Diary/Lanes/By Task switcher), `viewer/tests/handover-status.spec.js` (its pill and chip checks move to `sessions.mock.spec.js`; `threads-board.spec.js` keeps `playwright.threads.config.js` and `run_smoke.sh` working)

**Interfaces:**
- Consumes: `RightRail`, `statusPill`, `HO_STATUS_LABEL` (Task 1); `chipRow` (2b), `linkRow`, `truncate`, `stateBlock` (`components/empty-state.js`), `claimTopbar`, `setTopbarCount`, `tmSearch`, `chipClickNext`, `CHIP_CLICK_HINT`, `bindCopy`, `icon()`.
- Produces (`timeline.js`; `clusterParallelSessions` unchanged):
  ```js
  export function renderTimeline(root, { sessions, handovers, onSelect, selected = null }) → () => void
    // onSelect({ kind: 'session' | 'handover', id }, button); selected: { kind, id } | null marks that row
  export function kindLabel(kind) → string   // 'mid-task' → 'Mid-task', 'standalone' → 'Standalone', '' → 'Handover'
  ```
- Behaviour (each line is a test):
  1. **Topbar.** Row 1: `setTopbarCount` in the plan 3 count wording "n <noun> · m visible": "`2 threads · 3 handovers`" while nothing is hidden, "`2 threads · 3 handovers · 1 visible`" while the search or a chip hides threads (m counts the threads shown). Row 2: `tmSearch({ placeholder: 'Search sessions…', ariaLabel: 'Search sessions' })`. The page's first child is `div.sessions-filters` holding, in order, `chipRow({ label: 'Show', chips: [{ value: 'session', label: 'Threads', count, pressed }, { value: 'handover', label: 'Handovers', count, pressed }] })` — each click flips that one kind, as today; `chipRow({ label: 'Status', hint: CHIP_CLICK_HINT, chips: open/closed/superseded with `HO_STATUS_LABEL` words and counts })` — clicks go through `chipClickNext`, persisted with `prefs.patch({ screens: { sessions: { handoverStatus } } })` (the `viewer:prefs-patch` event is no longer used). Rows are rebuilt with `update()`, never re-created.
  2. **Rows are buttons.** A session head is `button.ho[type=button][data-session-id][aria-controls="right-rail"]`: `span.ho-kind` "Thread", `span.ho-time` (Technical), `span.ho-title` = `truncate(s.tldr || s.id, { lines: 2 })`, `span.ho-slug` = `s.id` (only when the title is the tldr), task ids as `span.ho-task` Technical tags. A handover is `button.ho-child[type=button][data-handover-id][aria-controls="right-rail"]`: `span.ho-kind` `kindLabel(viewer_kind)`, `span.ho-status` `HO_STATUS_LABEL[status]`, `span.ho-title` = `truncate(h.tldr || id, { lines: 2 })`, `span.ho-slug` = id. Only `span`s inside a button. The row shown in the rail has `aria-current="true"`; the mark moves with the rail and goes when it closes.
  3. **Search hides, it does not dim.** Sessions that do not match are left out (dimmed rows failed AA contrast); the count says "… · m visible". No match → `stateBlock({ label: 'Search', headline: 'No sessions match your search', action: { label: 'Clear search', onClick } })`, where `onClick` empties the search input, dispatches `input` and focuses it. No sessions → `stateBlock({ label: 'Sessions', headline: 'No sessions yet', hint: 'Sessions appear here as you start and end your work cycles.' })`. `listSessions()` failing → `stateBlock({ label: 'Error', headline: 'Sessions could not be loaded.', hint: 'Check that the viewer is still running, then reload the page.' })`, no page error.
  4. **Thread cards are links.** Each open or parked thread is `linkRow({ href: '#/sessions/<encoded name>', name: truncate(t.name), className: 'thread-card thread-card-<status>', content: [span.tc-stale, truncate(t.tldr, { lines: 2 }), next action ("→ …") when set, task id tags, branch], controls: [button.tc-copy.btn.btn--ghost.btn--sm (icon `copy` + "Resume line", `aria-label` "Copy resume line for <name>", `bindCopy` as today)] })`. Parked threads stay in `details.tb-parked` with `summary` "n parked".
  5. **The route opens a session.** `#/sessions/<id>` opens that session's rail on mount (`subpath[0]` decoded, falling back to `params.id`); an id with no session opens nothing and throws nothing.
  6. **The rail sits beside the timeline.** `.sessions-body` holds `[data-role=mount]` then `[data-role=rail-host]`. While a rail is open (`.sessions-body:has(> .right-rail-host > .right-rail)`) at > 1024px it is a two-column grid `minmax(0, 1fr) minmax(320px, 420px)` and the host is `position: sticky; top: 0; align-self: start; max-height: 85vh; overflow-y: auto;`; at ≤ 1024px one column with the host `order: -1` (above the timeline; opening focuses its title, which scrolls it into view). The timeline is never dimmed or made unclickable while the rail is open.
  7. **Leaving.** Cleanup sets `alive = false`, closes the rail, destroys both chip rows. A search callback, a session-detail fetch or a status change that finishes after leaving does nothing.
- `sessions.css` (rewrite, tokens only, enforced): `.sessions-page { display: flex; flex-direction: column; gap: var(--space-lg); min-width: 0; }` (no outer padding); `.sessions-filters { display: flex; flex-wrap: wrap; gap: var(--space-xs) var(--space-lg); min-width: 0; }` `.sessions-filters > .chip-row { flex: 0 1 auto; min-width: 0; }` (a whole group may move to the next line; chips inside a group never wrap); `.tl { display: flex; flex-direction: column; gap: var(--space-md); min-width: 0; }`; `.ho, .ho-child { box-sizing: border-box; display: flex; flex-direction: column; gap: var(--space-micro); width: 100%; padding: var(--space-sm) var(--space-md); text-align: left; background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); color: var(--foreground-default); font: inherit; cursor: pointer; transition: background-color var(--dur-standard) var(--ease-hourglass), border-color var(--dur-standard) var(--ease-hourglass); }` hover `background: var(--card-bg-hover); border-color: var(--border-strong);` `[aria-current="true"] { background: var(--signature-glow-strong); border-color: var(--signature-dim); color: var(--foreground-bold); }`; `.ho-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-xs); }`; `.ho-kind, .ho-status` in the Technical tag voice (`--font-technical`, `--font-technical-weight`, `--size-technical-small` — spec §3.3 puts tags at 12px — `--foreground-default`) with `border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 0 var(--space-micro);` (no hue); `.ho-time, .ho-slug, .ho-task` Technical small `--foreground-subtle`; `.ho-title { font-weight: 600; color: var(--foreground-bold); }`; `.ses-children { margin-left: var(--space-md); padding-left: var(--space-md); border-left: 1px solid var(--border-default); display: flex; flex-direction: column; gap: var(--space-xs); }` (a neutral structural connector); `.par-block { border: 1px dashed var(--border-strong); border-radius: var(--radius-lg); padding: var(--space-md); }` `.par-label` Technical label voice; `.par-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-sm); }` (timeline.js stops setting `gridTemplateColumns` inline); `.tb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--space-sm); }` `.thread-card { padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); }` `.tb-parked > summary` Technical small, `cursor: pointer`; the `.sessions-body` grid rules of line 6; `@media (max-width: 768px) { .tc-copy, .tb-parked > summary { min-height: 44px; } }`. No `.sessions-kind-chip`, `.status-chip`, `body.rail-open` or legacy alias remains.

- [ ] **Step 1: Unit tests** (`timeline.test.js`, jsdom): rows are `button`s with `aria-controls="right-rail"`; a session with a tldr shows it as `.ho-title` and its id in `.ho-slug`; without a tldr the id is the title and there is no slug; `.ho-title` has `title` equal to its text; `selected: { kind: 'handover', id }` marks exactly that row `aria-current="true"`; a click calls `onSelect({ kind, id }, button)`; `kindLabel('mid-task') === 'Mid-task'`, `kindLabel('') === 'Handover'`; a tldr `<img src=x onerror=alert(1)>` renders as text (no `img`); no element inside a row is a `button`, `a` or `[tabindex]`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/timeline.test.js viewer/tests/unit/parallel-block.test.js` — Expected: the new tests FAIL; `parallel-block.test.js` PASS.
- [ ] **Step 3: Implement** `timeline.js`, `sessions.js`, `sessions.css`; ENFORCED; append `manySessions(n)` to `mock-fixtures.js` (returns `{ sessions, details }`: `n` sessions, ids `thread-` + 70 more slug characters + index, each with a 120-character tldr and two handovers); update `threads-board.spec.js` to the new structure (`.thread-card-open` and `.tc-copy` keep their classes; the lane test looks for `.ho-slug` "team-relayout" and `.ho-kind` "Thread"; the chip test reads `getByRole('group', { name: 'Status' }).locator('.chip')` with texts `/Open/`, `/Closed/`, `/Superseded/`); delete the two specs.
- [ ] **Step 4: Mocked tests** (`sessions.mock.spec.js`; `mockApi` with `'/api/sessions': SESSIONS`, `'/api/threads': THREADS`, each `'/api/sessions/<id>'` from `SESSION_DETAILS`, `'/api/board': BOARD`):
  - "chips are buttons and filter the timeline": `.sessions-filters` holds `getByRole('group', { name: 'Show' })` and `getByRole('group', { name: 'Status' })`; "Superseded" is `aria-pressed="false"` and no row "Old hook plan" shows; Shift+click "Superseded" → pressed, the row shows, and a `PUT /api/viewer/prefs` body carries `screens.sessions.handoverStatus` containing `superseded`; click "Handovers" in Show → no `.ho-child`; `#topbar-count` reads "2 threads · 3 handovers"; type `scope` in the search → it reads "2 threads · 3 handovers · 1 visible".
  - **"keyboard: a row opens the rail, Escape closes it and focus returns to the row"**: focus `.ho-child[data-handover-id="2026-07-13-m1-shipped"]` by pressing Tab from the search field (at most 20 presses, asserting each lands somewhere visible); Enter → `#right-rail` heading "M1 shipped" focused and the row `aria-current="true"`; Escape → no rail, focus on that row, no `aria-current`.
  - **"the sessions rail says a failed status change in words"** (Review Focus 1): `POST /api/handover/2026-07-13-m1-shipped/status` answered 500; open the rail, the pill's menu, choose "closed" → the 5xx sentence beside the pill; the pill reads `open`.
  - "a thread card is a link and its copy button is not": click the card's tldr → `location.hash` is `#/sessions/team-relayout` and the rail heading for that session shows; back on `#/sessions`, click "Copy resume line for team-relayout" → hash unchanged; axe: zero `nested-interactive`.
  - "search hides what does not match and offers to clear it": type `zzz` → state block "No sessions match your search"; "Clear search" → both sessions back, focus in the search field.
  - **"leaving with the rail and its status menu open leaves nothing behind"** (Review Focus 5): open a handover rail and its menu; `page.evaluate(() => { location.hash = '#/settings'; })`; expect no `#right-rail`, no `.popover`, `await page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()))` is 0; Escape → no page error.
  - **"leaving within the search debounce leaves the next screen's count alone"** (Review Focus 5): type `t` and at once set `location.hash = '#/settings'`; after 400 ms `#topbar-count` is empty and no `pageerror` was raised.
  - **"thirty sessions with long slugs stay inside a phone screen"** (index RF 1): at 390×844 with `manySessions(30)`: `document.documentElement.scrollWidth <= innerWidth`, every `.ho-title` and `.ho-slug` has `scrollWidth <= clientWidth + 1` or a `title` equal to its text, every chip and row button is ≥ 44px tall.
  - both themes: axe on `#screen-mount` with the rail open and on `#topbar`: zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 sessions.mock.spec.js right-rail.mock.spec.js shell.mock.spec.js`, then `THREADS_PORT=8835 env --chdir=<wt>/viewer npx playwright test --config tests/playwright.threads.config.js` — Expected: PASS; `screens/sessions.css` 0 violations; the innerHTML grep on `sessions.js` and `timeline.js` prints nothing.
- [ ] **Step 6: Commit** — `feat(viewer): Sessions rows and chips are real controls, titles lead and slugs follow, search hides instead of dimming, and the rail docks beside the timeline`

---

### Task 3: Archived — full-width groups under named headings, rows as links that keep their words

**Depends on:** nothing in this plan; cross-track, 3a Task 2 (row 1 at 390px — this task's 390 checks of `#topbar-count` hold only after it merges).

**Files:**
- Modify: `viewer/js/screens/archived.js`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/archived.css`), `viewer/tests/mock-fixtures.js` (append `archivedBoard(n)`)
- Rewrite: `viewer/css/screens/archived.css`
- Create: `viewer/tests/archived.mock.spec.js`

**Interfaces:**
- Consumes: `linkRow`, `truncate`, `stateBlock`, `claimTopbar`, `setTopbarCount`, `tmSearch` (its returned `input`), `pluralize`.
- Produces (`archived.js`, exported for its test):
  ```js
  export function archivedGroups(backlog, query = '') → Array<{ key: string, label: string, tasks: Task[] }>
    // archived tasks matching `query` (id or title, case-insensitive), grouped by epic: groups in the order of
    // backlog.epics, then epic ids the backlog does not list (alphabetical), then key '__none__' labelled 'No epic';
    // label = the epic's name, else its title, else its id. A missing or non-object backlog → [].
  ```
- Behaviour (each line is a test):
  1. Row 1 count via `setTopbarCount` in the plan 3 wording: "40 archived tasks", and "40 archived tasks · m visible" while the search hides some; row 2 the search only (`ariaLabel: 'Filter archived tasks'`).
  2. Each group is `section.arch-group[aria-labelledby]` spanning the content width with `h2.arch-group-h` (label + `span.arch-group-count`).
  3. Each row is `linkRow({ href: '#/task/<encoded id>', name: span.arch-name[span.arch-id, truncate(title, { lines: 2, className: 'arch-title' })], content: [span.arch-phase (when set), span.arch-reason (when set; the reason text with its first letter upper-cased)], className: 'arch-row' })` with `data-task-id` on the row. The id is never cut; a plain click opens the detail modal through the interceptor.
  4. A board redraw keeps focus: if a row link had focus, the link of the row with the same `data-task-id` is focused after the repaint (`preventScroll: true`); if that row is gone, the search field is.
  5. No archived tasks → `stateBlock({ label: 'Archived', headline: 'No archived tasks yet' })`; no match → `stateBlock({ label: 'Search', headline: 'No archived tasks match "<q>"', action: { label: 'Clear search', onClick } })`.
  6. Cleanup unsubscribes and sets `alive = false`; a search callback after leaving does nothing.
- `archived.css` (rewrite, tokens only, enforced): `.archived-page { display: flex; flex-direction: column; gap: var(--space-lg); min-width: 0; }` (no padding); `.arch-group { display: flex; flex-direction: column; gap: var(--space-micro); min-width: 0; }`; `.arch-group-h { display: flex; align-items: baseline; gap: var(--space-xs); margin: 0 0 var(--space-xs); font-family: var(--font-narrator); font-weight: 700; font-size: var(--size-narrator-default); color: var(--foreground-bold); }` `.arch-group-count` Technical small subtle; `.arch-row { display: flex; align-items: baseline; gap: var(--space-sm); padding: var(--space-xs) var(--space-sm); background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); }` (`.link-row:hover` gives the hover); `.arch-row > .link-row__link { flex: 1 1 auto; min-width: 0; color: var(--foreground-bold); text-decoration: none; }` `.arch-name { display: flex; align-items: baseline; gap: var(--space-sm); min-width: 0; }` `.arch-id { flex: 0 0 auto; white-space: nowrap; font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); color: var(--foreground-subtle); }` `.arch-title { min-width: 0; }` `.arch-row > .link-row__content { flex: 0 0 auto; display: flex; gap: var(--space-xs); align-items: baseline; }` `.arch-phase` Technical small subtle; `.arch-reason` Technical small, `--foreground-default`, `border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 0 var(--space-micro);`; `@media (max-width: 768px) { .arch-row { flex-wrap: wrap; min-height: 44px; } .arch-row > .link-row__content { flex-basis: 100%; } }`.

- [ ] **Step 1: Unit tests** — new `viewer/tests/unit/archived.test.js` for `archivedGroups`: order (listed epics, then unknown ids alphabetically, then "No epic"); labels from `name`, then `title`, then id; query matches id and title case-insensitively; non-archived tasks never appear; `archivedGroups(null)` and `archivedGroups({ tasks: 'x' })` → `[]`.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/archived.test.js` — Expected: FAIL (`archivedGroups` not exported).
- [ ] **Step 3: Implement**; append `archivedBoard(n)` to `mock-fixtures.js` (`BOARD` plus `n` archived tasks over the two fixture epics, one unlisted epic `legacy` and no epic; ids `T-1001`…; every fifth title 120 characters long; one reason `superseded by T-140`, one `duplicate`).
- [ ] **Step 4: Mocked tests** (`archived.mock.spec.js`; `'/api/board'` and `'/api/backlog'` = `archivedBoard(40)`; `'/api/task/T-1001/detail': taskDetail({ ...EMPTY_TASK, id: 'T-1001', status: 'archived' })`):
  - headings: the `getByRole('heading', { level: 2 })` texts begin with "Viewer re-skin", "Native store", "legacy" and "No epic", in that order, each followed by its count; axe zero `heading-order`.
  - **"keyboard: an archived row opens its task and Escape returns to the row"**: Tab from the search to the first row link, Enter → detail modal for T-1001; Escape → modal gone, focus on that link.
  - "the count says how many are visible": `#topbar-count` reads "40 archived tasks"; type `T-101` → it reads "40 archived tasks · <n> visible" where n is the number of `.arch-row` elements then shown (and n < 40).
  - "the reason is a tag, not italics": `.arch-reason` computed `font-style` `normal`, text "Superseded by T-140".
  - **"a board redraw keeps focus on the focused row"** (Review Focus 5 / index RF 4): focus the third row's link; `page.evaluate(async () => { const { store } = await import('/js/store.js'); store.setBoard({ ...store.getBacklog(), revision: 'r2' }); })`; the focused element is a link whose row has the same `data-task-id`.
  - **"leaving within the search debounce leaves the next screen's count alone"**: type `T-10`, at once `location.hash = '#/settings'`; after 400 ms `#topbar-count` is empty.
  - **"forty archived tasks with long titles stay inside a phone screen"** (index RF 1): at 390×844 `scrollWidth <= innerWidth`; every `.arch-id` has `scrollWidth <= clientWidth`; every `.arch-title` has a `title` equal to its text; each row is ≥ 44px tall.
  - both themes: axe on `#screen-mount`: zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 archived.mock.spec.js` — Expected: PASS; `screens/archived.css` 0 violations; the innerHTML grep on `archived.js` prints nothing.
- [ ] **Step 6: Commit** — `feat(viewer): Archived groups are full-width sections under epic names, rows are links that keep the id whole and the title in reach, and a redraw keeps focus`

---

### Task 4: Dashboard notes — clamped with a way to read the rest, editable and pinnable by keyboard, writes that say when they fail

**Depends on:** nothing in this plan.

**Files:**
- Modify: `viewer/js/components/desk/note-card.js`, `viewer/js/components/desk/composer.js`, `viewer/js/screens/desk.js` (board part: `act()`, the error line, no autofocus), `viewer/css/screens/desk.css`, `viewer/tests/mock-fixtures.js` (append `LONG_NOTE`)
- Create: `viewer/tests/unit/note-card.test.js`, `viewer/tests/desk.mock.spec.js`

**Interfaces:**
- Consumes: `h()`, `icon()`, `mountMarkdown`, `formatStamp`, `tiltFor`, `describeWriteError`.
- Produces:
  ```js
  // note-card.js — callbacks resolve true when the write went through, false when it was refused
  export function createNoteCard({ note, onPin, onArchive, onSave }) → { root, measure() }
    // measure(): re-checks whether the body overflows its clamp and shows or hides "Show more"
  // composer.js
  export function createComposer({ onCreate }) → { root, focus() }   // onCreate(text) → Promise<boolean>
  ```
  `desk.js` wraps each write in `act(fn)`: runs it, on success clears the board's message, refreshes the board and resolves `true`; on a throw shows `describeWriteError(e, { noun: 'note' })` in `p.dk-board-error[role="alert"]` (first child of `.dk-board`, hidden while empty) and resolves `false`.
- Note card behaviour (each line is a test):
  1. `article.dk-note[aria-labelledby=<who id>]`; head: `span.dk-note__who` "You" or "Claude", `time.dk-note__when[datetime][title]` from `formatStamp(note.created)`, then `button.dk-note__edit.btn.btn--ghost.btn--icon.btn--sm[aria-label="Edit note"]` (icon `edit`), `button.dk-note__pin.btn.btn--ghost.btn--sm[aria-pressed]` with text "Pin" / "Unpin", `button.dk-note__archive.btn.btn--ghost.btn--icon.btn--sm[aria-label="Archive note"]` (icon `archive`). No emoji, no `✕`.
  2. The body (`div.dk-note__body[id]`) is clamped at `max-height: 16em`. When `scrollHeight > clientHeight + 1` the note gets `.is-clamped`, a `span.dk-note__fade[aria-hidden]` and `button.dk-note__more.btn.btn--ghost.btn--sm[aria-expanded="false"][aria-controls=<body id>]` "Show more"; pressing it sets `.is-expanded`, `aria-expanded="true"` and "Show less"; pressing again collapses. `measure()` runs on the next animation frame after mount and on window `resize` (listener removed when the note leaves the document); without overflow there is no button.
  3. Edit opens from the Edit button or a click on the body that is not on a link (a link in the body follows its `href`). The textarea is labelled "Edit note" and focused; Escape cancels, Ctrl/⌘+Enter or leaving it saves; afterwards focus is on the Edit button when it is still in the document. A save whose `onSave` resolves `false` re-opens the editor holding the typed text.
  4. The composer is not focused on mount. Enter with text calls `onCreate`; the text is cleared only when it resolves `true`; on `false` it stays and focus stays in the composer.
  5. A board refresh keeps focus: `desk.js` `renderBoard()` remembers the focused element's note (`closest('[data-note-id]')`) and control (`dk-note__edit`, `dk-note__pin`, `dk-note__archive`, `dk-note__more`) and focuses the same control of the same note after the redraw; when that note is gone (archived), the composer's textarea takes focus.
- `desk.css` additions (tokens only; the file stays at 0 violations): `.dk-note__head` keeps its row and gains `.dk-note__head .btn { color: var(--note-ink-soft); }` hover `color: var(--note-ink); background: transparent; border-color: var(--note-ink-soft);`; the controls' existing opacity rules move from `.dk-note__pin, .dk-note__archive` to `.dk-note__head .btn` and `@media (max-width: 768px) { .dk-note__head .btn, .dk-note__more { opacity: 1; min-height: 44px; } }`; `.dk-note__body { position: relative; max-height: 16em; overflow: hidden; }` `.dk-note.is-expanded .dk-note__body { max-height: none; }` `.dk-note__fade { display: none; }` `.dk-note.is-clamped:not(.is-expanded) .dk-note__fade { display: block; position: absolute; left: 0; right: 0; bottom: 0; height: 3em; pointer-events: none; background: linear-gradient(transparent, var(--note-paper-user)); }` with `.dk-note--claude` using `var(--note-paper-claude)` (the fade sits inside the body, so `.dk-note__body` is its containing block); `.dk-note__more { margin-top: var(--space-xs); color: var(--note-ink); border-color: var(--note-ink-soft); }`; `.dk-note__body a { overflow-wrap: anywhere; }`; `.dk-board-error { grid-column: 1 / -1; margin: 0; color: var(--foreground-bold); font-size: var(--size-narrator-small); }` `.dk-board-error:empty { display: none; }`. Delete the `filter: grayscale` rules.

- [ ] **Step 1: Unit tests** (`note-card.test.js`, jsdom; stub `requestAnimationFrame` to run at once): head words and button names (line 1); `measure()` with `scrollHeight` 900 / `clientHeight` 300 defined on the body via `Object.defineProperty` → "Show more" with `aria-expanded="false"`, click → "Show less", `aria-expanded="true"`, `.is-expanded`; 100 / 300 → no `.dk-note__more`; Edit button → labelled textarea focused; Escape → body back, focus on Edit; Ctrl+Enter with changed text → `onSave(note, text)`; `onSave` resolving `false` → the textarea is back holding the typed text; a click on an `a` inside the body opens no editor; composer: `onCreate` resolving `false` keeps the text, `true` clears it; a note body `<img src=x onerror=alert(1)>` is sanitised by `mountMarkdown` (no `onerror` attribute in the DOM).
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/note-card.test.js viewer/tests/unit/desk-lib.test.js` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement**; `desk.js`: delete `composer.focus()` at mount, add `act()` and the error line, pass `(n) => act(() => api.updateNote(…))` etc. Append `LONG_NOTE` to `mock-fixtures.js`: `{ id: 'NOTE-099', author: 'claude', pinned: false, created: ago(2), body: Array.from({ length: 8 }, (_, i) => `Paragraph ${i + 1}: ` + 'The cutover moves every row into the native store and checks the counts. '.repeat(7)).join('\n\n') + '\n\nhttps://example.com/' + 'a'.repeat(300) }` (`ago` is the file's existing helper).
- [ ] **Step 4: Mocked tests** (`desk.mock.spec.js`; `'/api/notes': NOTES` unless the test routes it, `'/api/continuity': { items: [] }`):
  - "the board mounts with no error, no focused composer, and marked loaded locally": no `pageerror`; `document.activeElement` is not `.dk-composer__input`; `typeof window.marked?.parse === 'function'`.
  - "the composer creates a note": route `/api/notes` GET to `NOTES` first and `NOTES` plus `{ id: 'NOTE-005', author: 'user', body: 'Ship it', created: new Date().toISOString() }` after the POST; `'POST /api/notes': { ok: true, id: 'NOTE-005' }`; type "Ship it", Enter → one POST with body `{ text: 'Ship it', pinned: false }`, the new note visible, the composer empty.
  - **"pin and archive work from the keyboard"** (`'POST /api/notes/NOTE-002/update': { ok: true }`, `'POST /api/notes/NOTE-002/archive': { ok: true }`): Tab to NOTE-002's "Pin", Space → one `POST /api/notes/NOTE-002/update` with body `{ pinned: true }` and, after the board refreshes, focus is still on NOTE-002's Pin button (line 5); Shift+Tab/Tab to its "Archive note", Enter → `POST /api/notes/NOTE-002/archive`; with the archive mocked to remove NOTE-002 from the next `GET /api/notes`, focus lands in the composer, not on `<body>`.
  - **"a very long note is clamped and expands from the keyboard"** (Review Focus 2): notes = `[LONG_NOTE]`; the body's `clientHeight` ≤ 16 × its computed font size + 1; "Show more" visible; focus it by Tab, Enter → `aria-expanded="true"`, body `clientHeight >= scrollHeight - 1`; Enter → collapsed again; at 390×844 `document.documentElement.scrollWidth <= innerWidth`.
  - "a failed note write is said in words and keeps the text": `'POST /api/notes': { status: 500, json: { ok: false, error: 'database is locked' } }`; type and Enter → `.dk-board-error` reads "The server could not save this change. Try again in a moment."; the composer still holds the text.
  - **"leaving with a note mid-edit saves it once and throws nothing"** (Review Focus 5): `'POST /api/notes/NOTE-003/update': { ok: true }`; Edit NOTE-003, type " later", `location.hash = '#/settings'` → exactly one update POST, no `pageerror`.
  - both themes: notes keep their paper and ink (the `desk-notes.mock.spec.js` assertions still pass); axe on `.dk-board` with "Show more" visible: zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 desk.mock.spec.js desk-notes.mock.spec.js` — Expected: PASS; `screens/desk.css` 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): Dashboard notes clamp with a fade and a Show more button, edit and pin by keyboard, and a refused note write is said in words without losing the text`

---

### Task 5: Dashboard summary strip — four counts that are links

**Depends on:** Task 4 (same files).

**Files:**
- Create: `viewer/js/components/desk/summary-strip.js`, `viewer/tests/unit/summary-strip.test.js`
- Modify: `viewer/js/screens/desk.js`, `viewer/css/screens/desk.css`, `viewer/tests/desk.mock.spec.js`, `viewer/tests/mock-fixtures.js` (append `ISSUES_LIST`, `BUGS_LIST`)

**Interfaces:**
- Consumes: `h()`, `store.getBacklog()`, `store.getIssues()`, `store.subscribe('backlog', …)`, `getIssues({ includeResolved })` and `listBugs({ status })` (`api.js`).
- Produces (`summary-strip.js`):
  ```js
  export function summaryCounts({ tasks, issues, bugs }) → { inProgress, waiting, issues, bugs }
    // inProgress / waiting: tasks with status 'in-progress' / 'in-review' (0 for a non-array);
    // issues: status 'open' or 'investigating'; bugs: status 'open' (each null when its input is not an array)
  export const SUMMARY_LINKS;   // [{ key: 'inProgress', label: 'In progress', href: '#/table?status=in-progress' },
                                //  { key: 'waiting', label: 'Waiting on you', href: '#/table?status=in-review' },
                                //  { key: 'issues', label: 'Open issues', href: '#/issues' },
                                //  { key: 'bugs', label: 'Open bugs', href: '#/bugs' }]
  export function createSummaryStrip() → { root, update(counts) }
    // root: nav.dk-summary[aria-label="Project summary"] > ul > li × 4 > a.dk-stat[href] >
    //   span.dk-stat__n + span.dk-stat__label. update() rewrites the numbers in place (same anchors);
    //   a null count shows "—" and the anchor's title "Not loaded".
  ```
- Behaviour (each line is a test):
  1. The strip is the first child of `.dk-desk`, above the board.
  2. Counts come from `store.getBacklog().tasks`, re-counted on every `backlog` emit (unsubscribed in cleanup); issues from `store.getIssues()` when it is an array, else `(await getIssues({ includeResolved: false })).issues`; bugs from `await listBugs({ status: 'open' })`, filtered again by `summaryCounts`. A failed read gives `null` for that count only; nothing is thrown.
  3. The two Table links filter once 3b's Task 2 merges (it reads `#/table?status=<v>` at mount, presses those Status chips for that visit and writes nothing to prefs). This plan does not wait for it: Task 5's tests check the `href` only, and Task 8 adds the landing check when 3b Task 2 is on the integration branch.
  4. The dead project label goes: `desk.js` no longer appends `.dk-proj` (the store has no `projectName`), and its CSS rule is deleted. Row 2 stays empty on the Dashboard.
- `desk.css`: `.dk-summary > ul { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-sm); margin: 0; padding: 0; list-style: none; }` `.dk-stat { display: flex; flex-direction: column; gap: var(--space-micro); min-height: 44px; padding: var(--space-sm) var(--space-md); background: var(--card-bg); border: 1px solid var(--border-default); border-radius: var(--radius-lg); color: var(--foreground-default); text-decoration: none; transition: background-color var(--dur-standard) var(--ease-hourglass), border-color var(--dur-standard) var(--ease-hourglass); }` hover `background: var(--card-bg-hover); border-color: var(--border-strong);` `.dk-stat__n { font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-declaration-h4); color: var(--foreground-bold); }` `.dk-stat__label` Technical label voice; `@media (max-width: 768px) { .dk-summary > ul { grid-template-columns: repeat(2, minmax(0, 1fr)); } }`.

- [ ] **Step 1: Unit tests** (`summary-strip.test.js`): `summaryCounts` over `BOARD.tasks` → `inProgress` 2, `waiting` 1; archived tasks are not counted; issues `[{status:'open'},{status:'investigating'},{status:'fixed'}]` → 2; bugs `[{status:'open'},{status:'fixed'}]` → 1; `issues: null` → `null`; `createSummaryStrip()`: four links with the `SUMMARY_LINKS` hrefs, accessible text "2 In progress"; `update()` twice keeps the same four anchor elements; a null count shows "—" with title "Not loaded".
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/summary-strip.test.js` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement**; append `ISSUES_LIST` (`{ issues: [...] }`: two open, one investigating, one fixed) and `BUGS_LIST` (an array: two open, one fixed).
- [ ] **Step 4: Mocked tests** (`desk.mock.spec.js`; `'/api/board'`/`'/api/backlog': BOARD`, `'/api/issues': ISSUES_LIST`, `'/api/bugs': BUGS_LIST`):
  - "the strip counts from the board, issues and bugs, and each count is a link": `getByRole('navigation', { name: 'Project summary' })` holds links "2 In progress" → `#/table?status=in-progress`, "1 Waiting on you", "3 Open issues" → `#/issues`, "2 Open bugs" → `#/bugs`; clicking "Open issues" lands on `#/issues`.
  - "a read that fails leaves the other counts": `'/api/issues': { status: 500, json: {} }` → "Open issues" shows "—"; the others still count; no `pageerror`.
  - **"a board redraw updates the counts in place and keeps focus"** (Review Focus 5 / index RF 4): focus "In progress"; `store.setBoard` with T-104 set to `in-progress` and `revision: 'r2'` → the link reads "3 In progress" and is still `document.activeElement`.
  - at 390×844: two columns, `scrollWidth <= innerWidth`, each link ≥ 44px tall; `#topbar-actions` has no visible child.
  - both themes: axe on `.dk-summary`: zero violations.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 desk.mock.spec.js desk-notes.mock.spec.js` — Expected: PASS; `screens/desk.css` 0 violations.
- [ ] **Step 6: Commit** — `feat(viewer): the Dashboard opens on four counts — in progress, waiting on you, open issues, open bugs — each a link to where they are`

---

### Task 6: Dashboard continuity band — rows that are links or disclosures, decisions as buttons, refusals in words

**Depends on:** Task 5 (same files).

**Files:**
- Modify: `viewer/js/components/continuity/item-row.js`, `viewer/js/components/continuity/spine.js`, `viewer/js/components/continuity/decision-card.js`, `viewer/js/screens/desk.js` (band part), `viewer/tests/unit/decision-card.test.js`, `viewer/tests/desk.mock.spec.js`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/continuity.css`), `viewer/tests/mock-fixtures.js` (append `CONTINUITY`, `DECISION`)
- Rewrite: `viewer/css/screens/continuity.css`
- Create: `viewer/tests/unit/item-row.test.js`
- Delete: `viewer/tests/desk.spec.js` (live; its five checks are now in `desk.mock.spec.js`)

**Interfaces:**
- Consumes: `linkRow`, `truncate`, `h()`, `formatRelative`, `hasKnownTags`/`renderInline`/`renderBlock` (`lib/xml-render.js`, unchanged), `describeWriteError`.
- Produces:
  ```js
  // item-row.js
  export const ITEM_ROUTE;   // { task: (id) => `#/task/${enc(id)}`, issue: (id) => `#/issue/${enc(id)}`, idea: () => '#/ideas' }
  export function createItemRow({ item, onToggle }) → { root, isExpanded(), setExpanded(node), setLoading(), clearExpanded() }
    // onToggle(item, controller) — handover and decision rows only
  // decision-card.js — onResolve(idx) and onDrop(id) return promises; a rejection is shown on the card
  export function createDecisionCard({ item, decision, onResolve, onDrop }) → { root }
  ```
- Behaviour (each line is a test):
  1. A `task`, `issue` or `idea` item with an id is `linkRow({ href: ITEM_ROUTE[type](id), name: title, content: [type tag, when, next, where], className: 'co-row' })`.
  2. A `handover` or `decision` item is `div.co-row` holding `button.co-row__toggle[type=button][aria-expanded][aria-controls]` (type tag, title, when, next, where) and, once opened, `div.co-row__expanded[id][role="region"][aria-label="<Type> <id>"]`; `aria-expanded` follows `setExpanded`/`setLoading` (true) and `clearExpanded` (false). A body that cannot be fetched shows "This could not be loaded. Try again in a moment."
  3. Any other item (a branch) is a plain `div.co-row` with no control.
  4. The type tag is `span.co-chip` with the word ("Decision", "Handover", "Task", "Branch", "Idea", "Issue") in the Technical tag voice (`--font-technical`, `--font-technical-weight`, `--size-technical-small` — spec §3.3 puts tags at 12px — `--foreground-default`), no hue. A plain-text title is `truncate(title)`; a tagged title keeps its rendered nodes and gets `title` = its text.
  5. Spine heading is `h2.co-spine__label` + `span.co-spine__count`.
  6. "+N older" is `a.dk-older.btn.btn--ghost.btn--sm[href]` with text `+N older` and `aria-label` "<N> older <rail> items".
  7. Decision options are `button.co-decision__opt[type=button]`; the recommended one carries `span.co-decision__rec` "Recommended". "Pick option N" is `button.btn.btn--primary.btn--sm`, "Drop" `button.btn.btn--ghost.btn--sm`. While a resolve or drop runs every button on the card is `disabled`; a rejection re-enables them and shows `describeWriteError(e, { noun: 'decision' })` in `p.co-error[role="alert"]` on the card.
- `continuity.css` (rewrite, tokens only, enforced): `.co-spine { display: flex; flex-direction: column; gap: var(--space-xs); min-width: 0; }` `.co-spine__head { display: flex; align-items: baseline; gap: var(--space-xs); padding-bottom: var(--space-micro); border-bottom: 1px solid var(--border-default); }` `.co-spine__label { margin: 0; }` + Technical label voice; `.co-spine__count` Technical small subtle; `.co-row` card (`--card-bg`, `1px solid var(--border-default)`, `--radius-md`, `padding: var(--space-xs) var(--space-sm)`); `.co-row__toggle { display: flex; flex-direction: column; gap: var(--space-micro); width: 100%; padding: 0; text-align: left; background: transparent; border: 0; color: inherit; font: inherit; cursor: pointer; }`; `.co-row__line1 { display: flex; align-items: baseline; gap: var(--space-xs); min-width: 0; }` `.co-row__title { flex: 1 1 auto; min-width: 0; color: var(--foreground-bold); font-weight: 600; }` `.co-row__when, .co-row__next, .co-row__where` Technical small subtle; `.co-chip, .co-xtag` in the Technical tag voice (`--font-technical`, `--font-technical-weight`, `--size-technical-small` — spec §3.3 puts tags at 12px — `--foreground-default`) with `border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 0 var(--space-micro);` (no per-type colour rules); `.co-xblock`, `.co-xblock__tag` (`border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-sm);`), `.co-xblock__tag-label` Technical label voice, `.co-xblock__p, .co-xblock__tag-body` Narrator small, `white-space: pre-wrap`; `.co-row__expanded { margin-top: var(--space-xs); padding-top: var(--space-xs); border-top: 1px solid var(--border-subtle); }`; `.co-decision` card with `h3.co-decision__title` Narrator 16/700 bold; `.co-decision__opt { display: flex; gap: var(--space-xs); width: 100%; text-align: left; padding: var(--space-xs) var(--space-sm); background: transparent; border: 1px solid var(--border-default); border-radius: var(--radius-md); color: var(--foreground-default); font: inherit; cursor: pointer; }` hover `background: var(--card-bg-hover); border-color: var(--border-strong);` `.co-decision__rec` tag like `.co-chip`; `.co-error { margin: 0; color: var(--foreground-bold); }`; `@media (max-width: 768px) { .co-row__toggle, .co-decision__opt, .co-decision .btn, .dk-older { min-height: 44px; } }`.

- [ ] **Step 1: Unit tests** — `item-row.test.js`: a task item is a `.link-row` whose link `href` is `#/task/T-102` and holds no other control; an issue item links to `#/issue/ISS-012`; an idea to `#/ideas`; a branch item has no `a`, `button` or `[tabindex]`; a handover row's toggle reads `aria-expanded="false"`, `setLoading()` → `"true"`, `clearExpanded()` → `"false"`, and `aria-controls` names the region once it exists; a title `<img src=x onerror=alert(1)>` is text. `decision-card.test.js` (keep its existing assertions): options are `button`s; clicking option 3 calls `onResolve(3)`; `onResolve` returning a promise rejecting `Object.assign(new Error('POST … → 500'), { code: 500 })` → every button disabled while pending, then enabled, and `.co-error` reads the 5xx sentence.
- [ ] **Step 2: Run** `env --chdir=<wt> node --test viewer/tests/unit/item-row.test.js viewer/tests/unit/decision-card.test.js` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement**; `desk.js`: `onItemClick` becomes `onToggle`; resolve/drop rethrow so the card can say why; delete `desk.spec.js`. Append `CONTINUITY` (`{ items: [...] }`: seven `resume` handovers `2026-10-05-r1`…`2026-10-05-r7` aged 1–7 days (r1 the youngest), two `review` tasks (T-107, T-102), one `decide` decision `DEC-001`, `clean-up` items: task T-106, issue ISS-012, idea IDEA-7, all with `action_class`, `age_days`, `timestamp`, `next`, `where`) and `DECISION` (`{ id: 'DEC-001', title: 'Land the cutover', options: ['Push the MR', 'Merge develop first', 'Hold'], recommendation: 2, body: '' }`).
- [ ] **Step 4: Mocked tests** (`desk.mock.spec.js`; `'/api/continuity': CONTINUITY`, `'/api/decisions/DEC-001': DECISION`, `'/api/handover/2026-10-05-r1': { body: '<lc>Cards done</lc>' }`, `'/api/board': BOARD`, `'/api/task/T-106/detail': taskDetail({ ...EMPTY_TASK, id: 'T-106' })`):
  - "rails cap at five with a +n older link": the Resume spine has 5 rows and a link "+2 older" with `href` `#/sessions`.
  - "clean-up rows are links": click the ISS-012 row → `location.hash` `#/issue/ISS-012`; back on `#/dashboard`, click the T-106 row → the detail modal for T-106.
  - **"a handover row expands from the keyboard"**: Tab to the first Resume toggle, Enter → `aria-expanded="true"` and "Cards done" visible in its region; Enter → `aria-expanded="false"`.
  - "a failed decision is said in words": `'POST /api/decisions/DEC-001/resolve': { status: 500, json: { ok: false, error: 'locked' } }`; press "Pick option 2" → the card's alert reads the 5xx sentence and the buttons are enabled again.
  - both themes: axe on `.dk-continuity`: zero violations, zero `nested-interactive`.
- [ ] **Step 5: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 desk.mock.spec.js desk-notes.mock.spec.js` — Expected: PASS; `screens/continuity.css` enforced, 0 violations; the innerHTML grep on the three continuity components and `desk.js` prints nothing.
- [ ] **Step 6: Commit** — `feat(viewer): the Dashboard's continuity rows open by link or by disclosure, decisions are buttons that say when the server refuses, and "+N older" is a control`

---

### Task 7: Settings — three segmented controls: Theme, Card density, Detail view

**Depends on:** nothing in this plan.

**Files:**
- Modify: `viewer/js/screens/settings.js`, `viewer/tests/unit/style-rules.test.js` (ENFORCED += `screens/settings.css`)
- Rewrite: `viewer/css/screens/settings.css`
- Create: `viewer/tests/settings.mock.spec.js`
- Delete: `viewer/tests/settings.spec.js` (live; its one check is in the new spec)

**Interfaces:**
- Consumes: `tmSegmented` (2b), `setThemePref`, `currentPref` (`lib/theme.js`), `detailViewMode` (`lib/view-mode.js`), `prefs.patch`, `claimTopbar`.
- Produces: none for other tasks.
- Behaviour (each line is a test):
  1. Three `section.set-block[aria-labelledby]`, in order, each `h2.set-h[id]` + `p.set-desc[id]` + `div.set-control[role="group"][aria-labelledby=<h2 id>][aria-describedby=<p id>]` holding one `tmSegmented`:
     - **Theme** — "Choose the theme. System follows your computer's setting." — Dark / Light / System (`dark`, `light`, `system`), pressed = `currentPref()`; a press calls `setThemePref(key)` (which applies it, writes `localStorage` `tm.theme` and patches `theme`).
     - **Card density** — "How much each Kanban card shows." — Full / Minimal (`full`, `minimal`), pressed = `store.getPrefs()?.card_density === 'minimal' ? 'minimal' : 'full'`; a press patches `{ card_density }`.
     - **Detail view** — "How a task or epic opens when you click it." — Modal / Full page (`modal`, `full`), pressed = `detailViewMode(store.getPrefs())`; a press patches `{ ui: { detail_view_mode } }`.
  2. A `theme:changed` event (the topbar toggle, or another tab's choice applied here) moves the Theme control's pressed button to `e.detail.pref`; the listener is removed in cleanup.
  3. The screen adds no outer padding (the page gutter is `#screen-mount`'s) and nothing to the topbar.
- `settings.css` (rewrite, tokens only, enforced): `.settings { max-width: 720px; }` `.set-block + .set-block { margin-top: var(--space-xl); }` `.set-h { margin: 0 0 var(--space-micro); font-family: var(--font-narrator); font-weight: 700; font-size: var(--size-narrator-default); color: var(--foreground-bold); }` `.set-desc { margin: 0 0 var(--space-sm); color: var(--foreground-subtle); font-size: var(--size-narrator-small); }` `.set-control { display: flex; min-width: 0; }` (the segmented control's look and 44px phone height come from `toolbar.css`). No `.set-radio` rules remain.

- [ ] **Step 1: Mocked tests** (`settings.mock.spec.js`; record every `PUT /api/viewer/prefs` body with `page.on('request')`):
  - **"a theme chosen in Settings applies at once, persists, and never flashes"** (Review Focus 3): prefs `{ theme: 'dark', ui: {}, screens: {} }`; press "Light" in `getByRole('group', { name: 'Theme' })` → `html[data-theme="light"]` at once, `localStorage['tm.theme'] === 'light'`, and a PUT body has `theme: 'light'` (`page.waitForRequest`). Then: route `**/api/viewer/prefs` GET to wait on a gate before answering `{ theme: 'light', ui: {}, screens: {} }`, `page.reload({ waitUntil: 'commit' })` → `data-theme` is `light` while `#sidebar .sidebar-link` has count 0 (boot still waiting); release the gate → still `light`, and "Light" is `aria-pressed="true"`.
  - **"the topbar toggle moves the Settings control with it"**: from dark, click `#theme-toggle` → "Light" pressed, "Dark" not.
  - "System follows the computer": `page.emulateMedia({ colorScheme: 'light' })`, press "System" → `data-theme` `light`; emulate `dark` → `dark`; "System" stays pressed.
  - "density and detail view are saved and shown again": press "Minimal" and "Full page" → PUT bodies carry `card_density: 'minimal'` and `ui.detail_view_mode: 'full'`; reload with those prefs mocked → both pressed.
  - **"keyboard: Tab reaches the theme control and Enter applies it"**: Tab from the page start until focus is on "Light" (at most 30 presses), Enter → `data-theme` `light`.
  - each group's accessible name is its heading and its description is the sentence; at 390×844 each segment button is ≥ 44px tall and `scrollWidth <= innerWidth`; both themes: axe on `#screen-mount`: zero violations.
- [ ] **Step 2: Run** `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 settings.mock.spec.js` — Expected: FAIL (no Theme group).
- [ ] **Step 3: Implement** `settings.js` and `settings.css`; ENFORCED; delete `settings.spec.js`.
- [ ] **Step 4: Run** the unit suite and `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2 settings.mock.spec.js theme.mock.spec.js` — Expected: PASS; `screens/settings.css` 0 violations.
- [ ] **Step 5: Commit** — `feat(viewer): Settings offers Theme, Card density and Detail view as segmented controls that apply at once and stay in step with the topbar toggle`

---

### Task 8: Verification

**Depends on:** Tasks 1–7.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs` (scenes and `TABLE` keys only; still mocked, static-served, never a live server)
- Modify: `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§11, append-only bullets)
- Modify: `viewer/tests/mock-fixtures.js` (four exported route-table functions), `viewer/tests/sessions.mock.spec.js`, `viewer/tests/archived.mock.spec.js`, `viewer/tests/desk.mock.spec.js`, `viewer/tests/settings.mock.spec.js` (their base `mockApi` tables come from those functions)

- [ ] **Step 1: Scenes.** Add to `TABLE`: `'/api/sessions': SESSIONS`, `'/api/threads': THREADS`, each `'/api/sessions/<id>'` from `SESSION_DETAILS`, `'/api/notes': { notes: [...NOTES.notes, LONG_NOTE] }`, `'/api/continuity': CONTINUITY`, `'/api/decisions/DEC-001': DECISION`, `'/api/issues': ISSUES_LIST`; the archived scenes route `/api/board` and `/api/backlog` to `archivedBoard(40)` through their `routes`. Add to `ALL_SCENES` (each `fullPage: true` unless noted):
  - `sessions` — `#/sessions`, scope `#screen-mount`;
  - `sessions-rail` — `#/sessions`, the "M1 shipped" handover row clicked (not full page), scope `#screen-mount`;
  - `sessions-status-error` — as `sessions-rail`, `POST /api/handover/2026-07-13-m1-shipped/status` answered 500, "closed" chosen from the pill's menu, scope `body`;
  - `archived` — `#/archived`, scope `#screen-mount`;
  - `dashboard` — `#/dashboard`, scope `#screen-mount`;
  - `dashboard-note-expanded` — `#/dashboard`, the long note's "Show more" pressed, scope `#screen-mount`;
  - `settings` — `#/settings`, scope `#screen-mount`.
- [ ] **Step 1b: Route tables plan 4 can reuse** (index Global Constraints, "Every screen has a mocked spec plan 4 can reuse"). Export from `mock-fixtures.js`, each with the signature `({ theme = 'dark' } = {})` and returning a fresh `mockApi` table built from this plan's fixtures whose `/api/viewer/prefs` carries that `theme` (plan 4's gate calls each builder once per theme), and make each spec's base table that call (a test may still spread its own keys over it):
  | Route | Function | Table | "Content is loaded" selector |
  |---|---|---|---|
  | `#/sessions` | `sessionsMocks({ theme })` | `/api/viewer/prefs`: `{ theme, ui: {}, screens: {} }`, `/api/sessions`: `SESSIONS`, `/api/threads`: `THREADS`, each `/api/sessions/<id>` from `SESSION_DETAILS`, `/api/board` and `/api/backlog`: `BOARD` | `.ho-child[data-handover-id="2026-07-13-m1-shipped"]` |
  | `#/archived` | `archivedMocks({ theme })` | `/api/viewer/prefs`: `{ theme, ui: {}, screens: {} }`, `/api/board` and `/api/backlog`: `archivedBoard(40)`, `/api/task/T-1001/detail` | `.arch-row[data-task-id="T-1001"] .link-row__link` |
  | `#/dashboard` | `dashboardMocks({ theme })` | `/api/viewer/prefs`: `{ theme, ui: {}, screens: {} }`, `/api/notes`: `{ notes: [...NOTES.notes, LONG_NOTE] }`, `/api/continuity`: `CONTINUITY`, `/api/decisions/DEC-001`: `DECISION`, `/api/issues`: `ISSUES_LIST`, `/api/bugs`: `BUGS_LIST`, `/api/board` and `/api/backlog`: `BOARD` | `.dk-note[data-note-id="NOTE-001"] .dk-note__body` |
  | `#/settings` | `settingsMocks({ theme })` | `/api/viewer/prefs`: `{ theme, card_density: 'full', ui: { detail_view_mode: 'modal' }, screens: {} }` | `.set-control[role="group"] .tm-segmented > button[data-key="system"]` |
  None of these selectors matches a `stateBlock` (`.tm-empty`). Add one test per route and theme to its spec, "<route> loads its content from <function>() in <theme>": `mockApi(page, <function>({ theme }))`, `page.goto('/#/<route>')`, the selector is visible, `html[data-theme]` is that theme, `.tm-empty[data-state="error"]` has count 0. The capture scenes of Step 1 use the same functions. List function and selector per route in the report.
- [ ] **Step 2: Capture.** `node <wt>/viewer/tests/tools/capture-modals.mjs C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-3e/shots-task-8 --port=8835` (all scenes, both themes, both widths). LOOK at every 3e image and at `detail-rich-rail` (3c's rail consumer of Task 1). Fix what is wrong within this plan's files, with a test where one makes sense. In the report, describe each 3e image plainly (what is where, what reads as primary) and list anything that belongs to another track or plan 4.
- [ ] **Step 3: Measure.** From `metrics.json`: for every 3e scene in both themes and widths, `overflowX` is false (no sideways page scroll) and axe has zero violations (zero `color-contrast` in particular) — list the key and result for each; report the 390px page height of `sessions`, `archived` and `dashboard`. Any non-zero is fixed in this task or named with its owner.
- [ ] **Step 3b: Strip landing (only when 3b Task 2 is merged into the integration branch this worktree was rebased on; otherwise say it was skipped and why).** In `desk.mock.spec.js` add "a summary link lands on the filtered Table": click "In progress" → `location.hash` `#/table?status=in-progress`, the Status chip "In progress" is `aria-pressed="true"`, and every row's status cell reads "In progress".
- [ ] **Step 4: Full runs.** `npm --prefix <wt>/viewer run test:unit`; `MOCK_PORT=8835 npm --prefix <wt>/viewer run test:mock -- --workers=2` (the whole mocked suite); `THREADS_PORT=8835 env --chdir=<wt>/viewer npx playwright test --config tests/playwright.threads.config.js`; `timeout 900 env --chdir=<wt> C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider` (count dots and failures if the process hangs after 100%). Report exact pass/fail/skip counts for each, the style-rules report line for each file in `ENFORCED`, and that `netstat -ano | grep ':8835 '` prints nothing.
- [ ] **Step 5: Rulings for spec §11.** Append one bullet per ruling to the end of §11 (append-only; never edit or reorder another track's bullets), each tagged "(plan 3e)": §6 Sessions — search hides non-matching sessions instead of dimming them (dimmed text failed AA); the rail docks beside the timeline above 1024px and sits above it below; §5.12 — the right rail is an in-flow panel the screen places, not a fixed overlay; §6 Dashboard — summary links go to `#/table?status=in-progress` / `in-review` (filtered by 3b Task 2's route parameter), `#/issues`, `#/bugs`; "+N older" is a link styled as a button (it navigates); dead `dashboard.layout` ids needed no change (the default is already empty); §6 Settings — Card density is Full / Minimal, Detail view is Modal / Full page; §4 — Sessions' filter chips sit in a bar at the top of the page, as the Table's do, not in topbar row 2 (a chip row's own overflow inside row 2's overflow would fight it); tags (kinds, reasons, type words) are 12px Technical, the 11px uppercase label is for section labels only; the Sessions and Archived counts use "n <noun> · m visible".
- [ ] **Step 6: Commit** — stage `viewer/tests/tools/capture-modals.mjs`, `viewer/tests/mock-fixtures.js` and the four specs; `test(viewer): Sessions, Archived, Dashboard and Settings each load from a named mock table, captured in both themes and widths`; then, separately, stage the spec only: `docs(viewer): spec §11 records plan 3e's rulings`
