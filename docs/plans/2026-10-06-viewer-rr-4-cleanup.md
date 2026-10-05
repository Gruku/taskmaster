<!-- User intent: leave the viewer re-skin release-ready — no migration aliases, no legacy CSS, every stylesheet under the
     style rules, the carried robustness defects fixed or openly accepted, an accessibility gate on every route in both
     themes, a fresh visual re-audit, and a CHANGELOG entry the user can version and ship. -->

# Viewer × Reality Reprojection — Plan 4: Cleanup, hardening, re-audit and CHANGELOG

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The re-skin branch ready for the user to release: the stage-1 migration aliases and every legacy rule gone, the style rules enforced on every CSS file, the robustness defects carried from plans 1–2b fixed or listed as accepted, an axe gate over every route × both themes × both widths, a fresh-context visual re-audit against spec §6 and the audit's finding IDs, and an Unreleased CHANGELOG entry.

**Architecture:** Three verify-first hardening tasks (shell, shared components, entity form) come first, while the code they touch is still in its plan 3 shape; then test-tool hygiene; then the CSS purge in dependency order (legacy rules → aliases → enforce everything), so each step shrinks the next; then the gates that judge the result (a11y spec, visual re-audit); then the words (CHANGELOG, spec bookkeeping) and a final verification. Every task that removes something first re-derives what is there from the code at that moment — plan 3 runs before this plan and changes most files — and writes a test that fails while any of it remains.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`), axe-core 4.13, the static capture tool `viewer/tests/tools/capture-modals.mjs`.

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` — §1 success criteria, §3.2 (migration aliases), §3.3–3.4 (type, radius), §8 (style rules enforcing; a11y gate `viewer/tests/a11y.spec.js`; visual verification), §9 stage 5, §10, §11 (amendments override earlier sections). Audit: `docs/specs/2026-10-01-viewer-audit.md` (finding IDs). Carry-in: `.superpowers/sdd/plan-3-4-carries.md` section "4" and its "Controller allocation", plan 2b's "Out of scope → Plan 4" list.

**Depends on:** Plan 3 merged into `feat/viewer-reality-reprojection` (all five tracks and the plan 3 index's final step). Plan 2b's components (`openPopover`, `overflowRow`, `chipRow`, `linkRow`, `sortHeader`, `truncate`, `stateBlock`, `describeWriteError`, the modal shell, the conflict banner, the topbar controls).

## Global Constraints

Plan 3's Global Constraints carry over unchanged (`docs/plans/2026-10-06-viewer-rr-3-screens.md`, "Global Constraints"); restated in short:

- No `box-shadow`. No `transform`/`translate`/`scale`/`rotate` in any `:hover` rule. No colored left border (only `var(--border-*)` on `border-left`). No `outline: none|0`. No italic. No text below 11px (11px only for uppercase labels). No named colours, hex or rgb literals outside `tokens.css`.
- Focus ring is the global one (`outline: 2px solid var(--border-focus); outline-offset: 2px`); a component may move it to a wrapper, never restyle it.
- Signature-hued text uses `--text-accent` only; text on a signature-tinted fill is `--foreground-bold`; on a solid `--signature-fill` it is `--on-accent-fill`.
- Cards and rows use `--card-bg`/`--card-bg-hover`; modals, popovers and the banner the `--overlay-surface*` roles.
- Status, severity and priority are a shape plus a word (`marker()` and the status maps).
- No `window.confirm`/`alert`/`prompt`. No `innerHTML` with task or user data except through `renderMarkdown()`.
- Every animation has a `prefers-reduced-motion` fallback (the global rule in `tokens.css`).
- Every new file starts with a 1–3 line `User intent:` header.
- Real controls only; nothing interactive inside anything interactive. Cut text keeps its words. Touch targets at ≤768px are at least 44px tall. Refused writes are words through `describeWriteError()`. Empty, not-found and error states are `stateBlock()`.
- Tests never touch a live backlog server. Mocked specs call `mockApi` before `page.goto` and assert `unmockedWrites(page)` is empty in `afterEach`. Live-server specs (`smoke.spec.js`, `playwright.config.js` suites, `run_smoke.sh`) are not run. Never start the live viewer server.

New for plan 4:

- **Verify first.** A task that takes over a carried item first checks the current code and runs (or writes) the test that would show it. An item already fixed is dropped with one line in the task report naming the commit or test that fixed it. An item still open with a user-visible or test-visible effect gets a failing test, then the fix. Anything left is appended to the accepted ledger (below) — never silently dropped.
- **Accepted ledger.** `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-4-cleanup/accepted.md` (git-ignored; create it on first use). One line per item: `- <area>: <what remains> — <why it is accepted> — user-visible: yes|no`. Task 10 turns the `yes` lines into the CHANGELOG's "Known limitations" and the `no` lines into spec §10.
- **Re-derive, never trust line numbers.** Every list in this plan that names files, lines or counts is today's (HEAD 638acec). The first step of each task re-derives it from the code at that time; a difference is expected, not an error. A leftover that plan 3 should have removed (a screen still using an alias, a screen CSS file with more than 10 style-rule violations) is fixed here when it is a substitution; when it would mean re-skinning a screen, the task stops and reports `NEEDS_CONTEXT` naming the screen and the plan 3 track.
- **No release steps.** No push, no version bump, no tag, no merge into a shared branch. The CHANGELOG entry sits under `## Unreleased` for the user to version.
- **Git:** each task works in its own worktree `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/rr4-t<N>` on branch `rr4/task-<N>`, branched from the integration HEAD; the controller merges each into `feat/viewer-reality-reprojection` locally `--no-ff`, one at a time. No pushes, no amends, never `git add -A`; stage only the files the task names. Never chain `cd`; use `env --chdir=<wt>`, `npm --prefix <wt>/viewer`, `git -C <wt>`.

Commands (cwd never changed; `<wt>` = the task's worktree; `<P>` = the task's port, `8840 + N` for Task N):

- One unit file: `env --chdir=<wt> node --test viewer/tests/unit/<file>`
- Unit suite: `npm --prefix <wt>/viewer run test:unit` (known timing-flaky under load: `inline-field.test.js` "Keep mine retains a newer draft…"; if only that fails, re-run once and say so)
- Mocked specs: `MOCK_PORT=<P> npm --prefix <wt>/viewer run test:mock -- --workers=2 <spec file or -g "title">`. The port must have no listener when a task finishes.
- Server: `timeout 900 env --chdir=<wt> C:/Users/gruku/Files/Claude/taskmaster/.venv/Scripts/python.exe -m pytest tests -k "server or viewer" -q -p no:cacheprovider` (on Windows the process can hang after printing 100% — count the dots and failures in the output)
- Screenshots: `node <wt>/viewer/tests/tools/capture-modals.mjs <dir> --port=<P>`; output under `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr/.superpowers/sdd/2026-10-06-viewer-rr-4-cleanup/shots-t<N>/` (written `<shots-tN>` below); never commit images.
- `<run date>` is the day the task runs, `YYYY-MM-DD`.

## Review Focus

1. **Preferences saved while the server is briefly down** (the viewer restarts while the user flips the theme, then a filter). The newest value of every key must reach the server once it is back; an older batch must never overwrite a newer one; a server that stays down must not be retried forever. → Task 1, unit test "a failed save is retried with newer patches merged over it, newest wins, and given up after three tries" (`prefs-writer.test.js`).
2. **Closing the tab within the debounce window** (flip the theme, close the tab 100 ms later). The change must be saved. → Task 1, mocked test "a pending preference is sent with keepalive when the page is hidden" (`shell.mock.spec.js`).
3. **An alias that differed per theme replaced by a token that does not** (`--red` was `color-critical` dark but `color-critical-bold` light; `--sev-high` was `accent-orange` dark but `color-warning-bold` light). A mechanical substitution keeps dark and silently changes light. → Task 6 maps each themed alias to the role that carries both values (table in Task 6) and its Step 4 compares capture metrics before and after: zero new contrast nodes in light; Task 8's gate re-checks every route in light.
4. **A tightened style rule that fires on a legitimate pattern** (a quoted font name containing "Black", `content: "{"`, a 2px tree guide line drawn with `::before` in `--border-default`). A false positive gets the test switched off. → Task 7, selftests for every new rule in both directions ("caught" and "clean").
5. **A route whose mocks are incomplete passes the accessibility gate vacuously** (it renders an error block with five nodes and no contrast problems). → Task 8: every route asserts `unmockedReads(page)` is empty and waits for real content (`ready`), and the not-found routes are separate entries whose `ready` is their state block (`.tm-empty[data-state="missing"]`); each builder is called with the theme under test, so its own prefs carry that theme.

## Order and parallelism

| Wave | Tasks (parallel within a wave) | Why they can run together |
|---|---|---|
| 1 | 1, 2 | 1 owns router/prefs/shell files; 2 owns shared component files; no shared source file |
| 2 | 3 (after 1), 4 (after 2) | 3 owns the edit form, `api.js`, the 404 sites; 4 owns `tests/tools/*`, `mock-api.js`, the flaky specs, favicon and font licences |
| 3 | 5 | Legacy rules out of `components.css` |
| 4 | 6 | Aliases out of every CSS and JS file |
| 5 | 7 | Style rules on every file |
| 6 | 8 | Accessibility gate |
| 7 | 9 | Visual re-audit |
| 8 | 10 | CHANGELOG and spec |
| 9 | 11 | Verification |

Serial run order when one worktree is used: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11. Tasks 5–7 sweep many files and must not overlap with anything.

## Carry-in

From the controller allocation (carries file, "Controller allocation"). Items the carry list says are already fixed are confirmed by the named task's first step and dropped there:

- Filters count of zero-width children — fixed 168c823 (2b Task 13). Self-dependency in the relation list — fixed c4d91d4. Form reaching into the shell DOM — fixed e8ff8e7 (Task 3 Step 1 confirms with the existing source test in `entity-modal.test.js`). `describeWriteError` in `entity-modal.js` — present at today's `entity-modal.js:254,259`. Modal focusable set, never-settling `onRequestClose`, out-of-order close opener — 2b Task 1 contract lines 13–15 (Task 2 Step 1 confirms by running `modal.test.js`). Docs rows `aria-invalid`, long URL cut, non-string docs values, touched-on-leave, missing epic on Create, non-numeric stage, bare 409 message — 2b Task 3. Refusal reason lost when leaving the picker — 2b Task 4. Python on PATH for the mock config — the config runs `static-server.mjs`. Settled by design in 2b (no work): free chip text becomes a chip on blur; a failed refresh after a successful save closes silently.
- Not taken (accepted now, recorded by Task 10 in spec §10): `markdown.css` task-list bullet relies on `:has()` (supported by every engine the viewer targets); marker glyph sizes are geometry, not tokens; the unit suite is ~25 s slower because the live-spec guard tests spawn Git Bash; the app saves `last_task_id` on plain navigation (live specs are guarded by `TM_LIVE_SPECS_OK`); `theme.js`'s `if (loaded)` branch is covered by Playwright only; light `--tone-orange` equals the medium tone (2a ruling: shape and word differ). Stale live-server specs are retired in Task 4, not ledgered. The SVG favicon link (`vendor/icon.svg`, spec §11 "shipped as SVG and a multi-size .ico") is an open user decision, not plan 4's: Task 11 lists it for the user.

---

### Handed over by plan 3 (controller, 2026-10-06)

The plan 3 writers moved these here; each joins the named task's verify-first list (re-derive, then test-and-fix or ledger):

- **Task 2 — non-visual saving cue** (from 3a): an inline field's saving / saved state is visible only as a glyph (`inline-field.js`); add a polite live-region word ("Saving…" / "Saved") or ledger it. Test in `inline-field.test.js`: after a successful save the field's status region's text is "Saved".
- **Task 3 — absolute times and `createBug`**: see verify item (e) and the dead-code line in Task 3.
- **Task 3 — `mountEpicDetail({ onNavigate })`** (from 3b): still accepted, no longer called; remove the option and its caller's argument (`detail-modal.js`), unless 3b's merged code calls it again.
- **Task 4 — one long-data fixture** (from 3a/3b): `mock-fixtures.js` holds 3a's `longBoard()` and 3b's `LONG_IDS_BOARD`; fold them into one builder with the union of what both need (27-character slug IDs, archived tasks, planned epics, 27 epics, 230 tasks), update both tracks' specs to it, delete the other.

### Task 1: Shell robustness — preferences, router failures, the remembered task, the phone shell

**Depends on:** plan 3. **Parallel:** wave 1, beside Task 2.

**Files:**
- Modify: `viewer/js/lib/prefs-writer.js`, `viewer/js/main.js`, `viewer/js/api.js` (`savePrefs` options; the unused raw `savePrefs` export), `viewer/js/router.js`, `viewer/js/screens/task-detail.js` (`last_task_id`, the view toggle's direct save), `viewer/css/shell.css` (phone `.shell` box)
- Test: `viewer/tests/unit/prefs-writer.test.js`, `viewer/tests/router.mock.spec.js`, `viewer/tests/shell.mock.spec.js`, `viewer/tests/task-missing.mock.spec.js`

**Interfaces:**
- Consumes: `stateBlock({ label, headline, hint, action, state })` (`components/empty-state.js`), `claimTopbar()`.
- Produces:
  ```js
  // lib/prefs-writer.js
  createPrefsWriter({ save, delayMs, retries = 3, retryDelayMs = (n) => delayMs * 2 ** n,
                      setTimer = setTimeout, clearTimer = clearTimeout, onError = () => {} })
    → { queue(patch), flush() }
    // save(batch, { keepalive }) → Promise. A rejected save merges its batch UNDER whatever is pending
    // (pending = deepMerge(batch, pending ?? {}) — a newer value of the same key wins) and sends again after
    // retryDelayMs(attempt). After `retries` consecutive failures the merged batch is given up and
    // onError(err, { dropped }) runs once. A success resets the count.
    // flush(): clears the debounce timer and sends what is pending at once with { keepalive: true },
    // even while a save is in flight (the page is going away); no retry.
  // api.js
  savePrefs: (p, { keepalive = false } = {}) => http('PUT', '/api/viewer/prefs', p, { keepalive })
    // http() passes options.keepalive into the fetch init.
  ```
- Behaviour (each line is a test):
  1. Retry and give-up as above (unit, fake timers).
  2. `main.js` calls `prefsWriter.flush()` on `pagehide`; `save` is `(p, o) => api.savePrefs(p, o)`.
  3. Every preference write goes through `prefs.patch()`: `screens/task-detail.js`'s view toggle stops calling `store.setPrefs` + `api.savePrefs` itself. The raw `export async function savePrefs` in `api.js` (today `:182`) is deleted when no file imports it.
  4. A screen that fails to load or to mount shows `stateBlock({ state: 'error', label: 'Could not open', headline: 'This screen could not be opened.', hint: 'Reload the page. If it keeps happening, restart the viewer.', action: { label: 'Go to the dashboard', href: '#/dashboard' } })` — never the error's message. `failureStub` and the `.stub`/`.stub-meta` classes leave `router.js`.
  5. After a failed load the title reads "Could not open" and no sidebar item is current; after a failed mount the title is the screen's `meta.title` and its sidebar item is current. The router dispatches `route:changed` on both failures with `sidebarKey: mod?.meta?.sidebarKey ?? null`.
  6. A bare `#/task` that follows a remembered `last_task_id` to a task answering 404 shows the not-found block and clears the memory (`prefs.patch({ ui: { last_task_id: null } })`), so the next bare `#/task` shows "No task open".
  7. At ≤768px with a conflict banner shown over a page, the page is not taller than its content plus the banner: `body:not(.modal-open) .shell { box-sizing: border-box; }` applies at every width (today only inside `@media (min-width: 769px)`).
- Verify-only (test first; fix only if it fails; else ledger): (a) with the phone drawer open, Tab and Shift+Tab never leave the drawer, including onto the empty `#conflict-banner-host` / modal hosts; (b) one layout shift on a screen's first load — measure the cumulative layout shift of `#/kanban` and `#/table` first loads with a `PerformanceObserver({ type: 'layout-shift', buffered: true })`; under 0.1 is accepted with the number in the ledger; (c) a screen that throws while mounting leaves behind whatever it registered — the router cannot know what that was; accepted unless a screen in the tree registers a document/window listener or timer before its first `await` (grep `addEventListener\(|setInterval\(` in `viewer/js/screens/*.js` and read each `mount`); (d) `closeDrawer` focuses the hamburger just before the hamburger is hidden when the viewport widens past 768px with the drawer open — test in `shell.mock.spec.js` "widening the window with the drawer open leaves focus on something visible": at 390×844 open the drawer, focus its second link, `setViewportSize({ width: 1440, height: 900 })`, wait two frames; `document.activeElement` is connected, is not `body`, is not the hamburger, and `checkVisibility()` is true (the fix, if needed, is in `components/sidebar.js`: on a widen, focus stays on the focused sidebar link, which is now in the desktop sidebar).

- [ ] **Step 1: Re-derive.** `grep -rn "savePrefs\|setPrefs(" viewer/js` (every preference write path); `grep -n "failureStub\|stub" viewer/js/router.js`; `grep -n "last_task_id" viewer/js -r`; read `shell.css`'s `.shell` rules at both widths. Note in the report which of lines 1–7 already hold.
- [ ] **Step 2: Unit tests** (`prefs-writer.test.js`, fake `setTimer`/`clearTimer` collecting callbacks): **"a failed save is retried with newer patches merged over it, newest wins, and given up after three tries"** (Review Focus 1): `queue({ theme: 'light', ui: { a: 1 } })`, fire the debounce, the save rejects; `queue({ theme: 'dark' })` while the retry waits; the retry's batch deep-equals `{ theme: 'dark', ui: { a: 1 } }`; three more rejections → `onError` called once with `{ dropped: { theme: 'dark', ui: { a: 1 } } }` and no further `save`; a later `queue` starts a fresh count. "flush sends what is pending at once with keepalive, even during a save": a save held pending, `queue({ x: 1 })`, `flush()` → a second `save` call with `({ x: 1 }, { keepalive: true })` before the first settles; `flush()` with nothing pending calls nothing.
- [ ] **Step 3: Mocked tests.** `shell.mock.spec.js`: **"a pending preference is sent with keepalive when the page is hidden"** (Review Focus 2): record `PUT /api/viewer/prefs` requests; click the theme toggle; within 100 ms `page.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide')))`; expect one PUT whose body has `theme: 'light'` recorded before 400 ms have passed since the click. "with the phone drawer open, Tab stays in the drawer" (390×844, open the hamburger, press Tab 30 times and Shift+Tab 30 times, `document.activeElement.closest('#sidebar')` is never null). "a banner over a phone page adds no extra scroll" (390×844 on `#/settings`: read `S = document.querySelector('.main').getBoundingClientRect().height`; `page.evaluate` imports `/js/components/edit/conflict-banner.js` and calls `showFieldConflict({ entityKind: 'task', entityId: 'T-1', fieldKey: 'title', fieldLabel: 'Title', localValue: 'a', currentValue: 'b', onKeepMine: async () => {}, onUseServer: () => {} })`; read the banner height `B`; expect `document.documentElement.scrollHeight <= Math.max(innerHeight, S + B) + 1`). `router.mock.spec.js`: "a screen that fails to load says so in words" (route `**/js/screens/epics.js` to `{ status: 500, body: '' }`, go to `#/epics`: `.tm-empty[data-state="error"]` visible with "This screen could not be opened.", the mount's text contains none of `Failed`, `fetch`, `import`, `http`, `.js`; `#page-title` reads "Could not open"; no `.sidebar-link[aria-current]`; then `#/kanban` mounts normally) and "a screen that throws while mounting keeps its title and sidebar item" (route `**/js/screens/settings.js` to a module body `export const meta = { title: 'Settings', sidebarKey: 'settings' }; export async function mount() { throw new Error('boom'); }` with `contentType: 'text/javascript'`: the error block shows, `#page-title` is "Settings", the Settings sidebar link has `aria-current="page"`, the text "boom" is nowhere on the page). `task-missing.mock.spec.js`: "a remembered task that no longer exists is forgotten" (prefs `{ theme: 'dark', ui: { last_task_id: 'T-999' }, screens: {} }`, `/api/task/T-999/detail` → `{ status: 404, json: { error: 'not found' } }`, go to `#/task`: `.tm-empty[data-state="missing"]` visible; a recorded prefs PUT body has `ui.last_task_id === null`).
- [ ] **Step 4: Run** the unit file and the three spec files — Expected: the new tests FAIL (no `flush`, no keepalive, raw stub, title unchanged after a failed load, no `last_task_id` repair).
- [ ] **Step 5: Implement** lines 1–7; the verify-only items (a)–(c) per their rule; append what is accepted to the ledger.
- [ ] **Step 6: Run** the unit suite and `MOCK_PORT=8841 npm --prefix <wt>/viewer run test:mock -- --workers=2 shell.mock.spec.js router.mock.spec.js task-missing.mock.spec.js theme.mock.spec.js task-detail.mock.spec.js` — Expected: PASS; `shell.css` still 0 style-rule violations.
- [ ] **Step 7: Commit** — `fix(viewer): preferences survive a failed save and a closing tab, a screen that cannot open says so in words with the right title and sidebar, a remembered task that is gone is forgotten, and a banner adds no phantom scroll on a phone`

---

### Task 2: Shared component hardening — rows, headers, overflow row, topbar observer, popover, buttons

**Depends on:** plan 3. **Parallel:** wave 1, beside Task 1.

**Files:**
- Modify: `viewer/js/components/link-row.js`, `viewer/css/components/rows.css`, `viewer/js/components/overflow-row.js`, `viewer/js/lib/topbar.js`, `viewer/css/components/edit-fields.css` and `viewer/css/components/popover.css` (the `.ef-chip-dropdown` surface only)
- Test: `viewer/tests/unit/link-row.test.js`, `viewer/tests/unit/overflow-row.test.js`, `viewer/tests/unit/topbar.test.js`, `viewer/tests/unit/popover.test.js`, `viewer/tests/unit/contrast.test.js`, `viewer/tests/rows.mock.spec.js`, `viewer/tests/chips.mock.spec.js`, `viewer/tests/popover.mock.spec.js`, `viewer/tests/shell.mock.spec.js`

**Interfaces:**
- Consumes: plan 2b's `linkRow`, `overflowRow`, `openPopover`, `claimTopbar`, `truncate`; `contrast.test.js`'s `assertNonText`.
- Produces: `linkRow(...)` — same signature; the full text of cut content goes on the **row** (`row.title`), not the link, and `.link-row__controls` carries `title=""` so a control does not inherit it. Everything else is unchanged contracts.
- Behaviour (each line is a test unless marked verify):
  1. **A row's link is named once.** `link.hasAttribute('title')` is false; `row.title` is the given `title` or the cut titles joined by `\n` (as today on the link); `.link-row__controls` has an empty `title` attribute. In the browser the link's accessible description is empty and its accessible name is its text.
  2. **Every row control is a touch target.** At ≤768px the 44px rule reaches controls nested inside wrappers: `.link-row__controls :is(button, a[href], input, select, [role="button"])` (descendant, not `>`).
  3. **Cut content keeps its words in a real browser.** A row whose content is `truncate()` of a 120-character title, in a 240px container: the visible text is cut (`scrollWidth > clientWidth` on the `.truncate`) and `row.title` is the whole title.
  4. **The unsorted-column glyph is a visible shape.** `--foreground-subtle` reaches 3:1 on `--bg-page`, `--card-bg` and `--overlay-surface` in both themes (`assertNonText`). Pass → no CSS change, record nothing; fail → `.sort-header__dir--none` takes `--foreground-default`.
  5. **More's width settles.** `overflowRow` repeats the extra pass until More's width stops growing, at most 3 passes in one layout (today one `layout(1)`): with an `onLayout` that lengthens More's label on each of its first two calls, the row ends with `scrollWidth <= clientWidth`.
  6. **A long press is never cut short.** The unconditional 3 s release (`STUCK_MS`) goes. After an outside press closes More, the parked layout waits for the press's `click`, for `RELEASE_MS` after a `pointerup` with no click, for a `pointercancel`, for a `contextmenu` (then `RELEASE_MS`), or for the window's `blur`. A press held 5 s then released onto a chip toggles that chip.
  7. **A control unhidden in place relayouts the topbar.** The topbar's `MutationObserver` also observes `attributes` with `attributeFilter: ['hidden', 'class', 'style']` (subtree; same `grown` filter, extended so an attribute record on the row itself or on Filters is ignored). At 390px, a row-2 control with `hidden` set at mount, unhidden later, either fits or is parked behind Filters within two animation frames, and row 2's `scrollWidth <= clientWidth`.
  8. **Popover paths only unit-tested.** (a) A popover closed before its first placement frame runs throws nothing and leaves no element behind; (b) after close, a `keydown` Escape and a `pointerdown` on the old anchor reach no popover handler (the anchor's own listeners only); (c) inside the detail modal at 1440×900 and 390×844, after the modal's entrance animation, the handover menu's top edge is 4±1px below the pill (or its bottom edge 4±1px above it when flipped). Fix only what fails.
  9. **One popover surface.** `.popover.ef-chip-dropdown` keeps only its own sizing; any `background`, `border`, `border-radius` or `padding` that differs from `.popover` is deleted, so the suggestion list and every other popover compute the same `background-color` and `border-color` in both themes.
  10. **Verify** (ledger if holding, no change): `.btn--icon.btn--sm` at ≤768px reaches 44px through `min-width`/`min-height`, which the CSS cascade always lets win over `width`/`height` — confirm one such button computes ≥44×44 at 390px; `tmAction`'s `disabled` option — `grep -rn "tmAction(" viewer/js` and delete the option and its branch if no caller passes `disabled`; the modal's focusable set, a never-settling `onRequestClose` and the out-of-order opener — `modal.test.js` passes with 2b Task 1's contract lines 13–15 (drop); `focusableIn` sorting per Tab and `rememberView` re-opening any future `[data-focus][aria-expanded]` toggle — accepted (`no` user-visible).

- [ ] **Step 1: Re-derive.** Read each file named above; run `env --chdir=<wt> node --test viewer/tests/unit/modal.test.js` (item 10's modal lines); `grep -n "STUCK_MS\|layout(1)" viewer/js/components/overflow-row.js`; `grep -n "observe(root" viewer/js/lib/topbar.js`; `grep -n "ef-chip-dropdown" viewer/css -r`. Note which of 1–10 already hold.
- [ ] **Step 2: Unit tests.** `link-row.test.js`: line 1 (structure: no `title` on `a.link-row__link`, `row.title` from a `truncate()`d content node and from an explicit `title`, controls `title === ''` and `hasAttribute('title')`). `overflow-row.test.js` (jsdom has no `ResizeObserver`, so test the release logic directly: lift the inner `afterPress` out of `overflowRow` as `export function waitForRelease(doc, view, fn) → AbortController` — `overflowRow` keeps the returned controller as `waiting` and aborts it exactly where it aborts today): with fake timers, `pointerdown` then nothing for 5000 ms → `fn` not run; `pointerup` + `click` → `fn` runs on the next task; `contextmenu` → runs after `RELEASE_MS`; window `blur` → runs. `topbar.test.js`: the observer options include `attributes: true` and the filter list (spy on `MutationObserver.prototype.observe`). `popover.test.js`: 8(a) and 8(b). `contrast.test.js`: line 4 with `assertNonText([[BOTH, '--foreground-subtle', ['--bg-page', '--card-bg', '--overlay-surface']]])`.
- [ ] **Step 3: Mocked tests.** `rows.mock.spec.js`: `await expect(link).toHaveAccessibleDescription('')` and `toHaveAccessibleName('T-102 · Re-skin the Kanban cards and columns')`; line 2 at 390×844 with a control wrapped in a `<span>`; line 3. `chips.mock.spec.js`: line 5 (the file's 20-chip harness with an `onLayout` that appends " (more)" to More's label on its first two calls; at 600px `row.scrollWidth <= row.clientWidth`); **"a long press outside More still lands on the chip it was released on"** (open More; `page.mouse.move` to a visible chip, `mouse.down()`, wait 5000 ms, `mouse.up()`; that chip's `aria-pressed` flipped and More is closed). `shell.mock.spec.js`: line 7 (on `#/table` at 390×844, `page.evaluate` sets `hidden` on a row-2 control, waits two frames, removes `hidden`, waits two frames; row 2 `scrollWidth <= clientWidth`; the control is either visible in the row or listed in the Filters popover). `popover.mock.spec.js`: 8(c) and line 9 (open the relation suggestions in the Edit form and the handover menu; their computed `background-color` and `border-top-color` are equal, both themes).
- [ ] **Step 4: Run** — Expected: the new tests FAIL where the item is still open.
- [ ] **Step 5: Implement** the failing items; append items 10's accepted lines (and any other kept as is) to the ledger.
- [ ] **Step 6: Run** the unit suite and `MOCK_PORT=8842 npm --prefix <wt>/viewer run test:mock -- --workers=2 rows.mock.spec.js chips.mock.spec.js popover.mock.spec.js shell.mock.spec.js table.mock.spec.js task-form.mock.spec.js` — Expected: PASS; the touched CSS files 0 violations.
- [ ] **Step 7: Commit** — `fix(viewer): a row's link is named once and its nested controls are touch-sized, More settles its own width and never cuts a long press short, the topbar relayouts a control unhidden in place, and every popover shares one surface`

---

### Task 3: Entity form hardening and dead code

**Depends on:** Task 1 (`api.js`, `screens/task-detail.js`). **Parallel:** wave 2, beside Task 4.

**Files:**
- Modify: `viewer/js/api.js` (`http()` parse failure), `viewer/js/components/edit/write-errors.js`, `viewer/js/components/edit/fields/keyvalue-field.js`, `viewer/js/components/edit/conflict-banner.js`, `viewer/js/components/edit/inline-field.js` and `viewer/js/components/edit/task-actions.js` (stop passing `currentEtag`), `viewer/js/components/edit/forms/task-form.js`, and every file still matching `→ 404` (today `components/detail-modal.js:151`, `screens/task-detail.js:102`, `screens/bug-detail.js:50`)
- Test: `viewer/tests/unit/api.test.js`, `viewer/tests/unit/write-errors.test.js`, `viewer/tests/unit/keyvalue-field.test.js`, `viewer/tests/unit/conflict-banner.test.js`, `viewer/tests/unit/task-form.test.js`, `viewer/tests/unit/entity-modal.test.js`, `viewer/tests/unit/inline-field.test.js`, `viewer/tests/task-missing.mock.spec.js`

**Interfaces:**
- Consumes: `describeWriteError(e, { noun })`, `lostRace(e)`.
- Produces:
  - `http()`: a JSON body that fails to parse throws an `Error` with `code = resp.status`, `unreadable = true` and the old console message.
  - `describeWriteError`: checked first — `e?.unreadable` → `'The server answered in a form the viewer cannot read, so it cannot tell whether the change was saved. Reload to check.'`
  - Not found is `e?.code === 404` everywhere; no code reads the status out of a message.
  - `showFieldConflict({ entityKind, entityId, fieldKey, fieldLabel, localValue, currentValue, onKeepMine, onUseServer })` and `showFullConflict({ entityKind, entityId, localDraft, currentValue, labels = {}, onResolve, onDismiss })` — `currentEtag` removed (never read).
  - `taskSchema(...)`: the `depends_on` field has no `validate` (the self-dependency guard is the `crossField` entry).
- Behaviour (each line is a test):
  1. A 200 whose JSON is cut off, met by a write, shows the `unreadable` sentence, never "Could not reach the server".
  2. A task or bug whose id contains `404` (`T-404`) that fails with a 500 shows the error block (`data-state="error"`, "Could not load"), not the missing one; a real 404 shows `.tm-empty[data-state="missing"]` (detail modal, task page, bug page).
  3. **A stored empty docs value is left alone.** `{ spec: null, plan: 'p.md' }`: the row `spec` shows an empty value; editing `plan` to `p2.md` saves `{ spec: null, plan: 'p2.md' }` (deep equal) with no "needs a path or URL" message; typing a path into `spec` saves it; untouched, the form is not dirty. (A row whose stored value is `null` and whose text is still empty keeps `null` and is not a fault.)
  4. No `currentEtag` in `conflict-banner.js`, `inline-field.js`'s or `task-actions.js`'s banner calls, or the banner's unit tests.
  5. `taskSchema(...).fields.find((f) => f.key === 'depends_on').validate === undefined`, and a task depending on itself is still refused by `crossField` with the same message as today.
- Verify-only, pin with a test, ledger as "by design" (no code change unless the test shows otherwise): (a) after a refused inline save a text editor keeps the typed text beside the reason so it can be corrected, while a select returns to the stored value (a select left on a refused value would look saved) — one `inline-field.test.js` test asserting both; (b) text typed into a chip input and not yet made a chip makes the form dirty, so Escape asks "Discard changes?" (typed text is never thrown away silently) — one `entity-modal.test.js` test; (c) `entity-modal.js` no longer reaches into the shell's DOM — the existing source test (`/parentElement/`, `/dialog\.addEventListener/`) passes; (d) `describeWriteError` wraps every `onSave` throw and `wait` rejection in `entity-modal.js` — `grep -n "err.message\|e.message" viewer/js/components/edit/entity-modal.js` finds none on a path to the page; (e) absolute times (`lib/time.js`, controller-assigned from 3c): find which stamp formats the store actually writes (`grep -rn "isoformat\|strftime\|datetime.now\|utcnow" taskmaster/*.py`, and one real `created`/`updated` value from a mocked fixture). If any stamp is written as UTC without a zone (`2026-10-06T12:00:00`), `isoToMs` must read it as UTC — test "a zone-less server stamp is read as UTC" in `time.test.js`, then fix; if every stamp carries a zone or is date-only, ledger "zone-less ISO read as local" as by design. The absolute time in `title` following the browser's locale is by design (ledger it).
- Dead code (controller-assigned): `api.js` `createBug` has no caller in `viewer/js` (`grep -rn "createBug" viewer/js` → its definition only); delete it and any unit test that only exercises it.

- [ ] **Step 1: Re-derive.** `grep -rn "→ 404" viewer/js`; `grep -rn "currentEtag" viewer/js viewer/tests/unit`; `grep -n "validate" viewer/js/components/edit/forms/task-form.js`; read `http()`'s JSON branch and `keyvalue-field.js`'s `toRows`/`kept`/`faults`. Run the verify-only checks (c) and (d).
- [ ] **Step 2: Unit tests** for lines 1, 3, 4, 5 and verify (a), (b): `api.test.js` — mocked `fetch` answering 200 with `Content-Type: application/json` and body `{"ok": tr` → the error has `code === 200`, `unreadable === true`; `write-errors.test.js` — that error → the sentence, and it contains no `200`, `JSON`, `/api`; `keyvalue-field.test.js` — line 3 as stated (through the field's `edit()`/`coerce` the way the file's existing docs tests do); `conflict-banner.test.js` — `readFileSync` of the three source files contains no `currentEtag`, and every existing banner test still passes with the argument removed; `task-form.test.js` — line 5.
- [ ] **Step 3: Mocked test** (`task-missing.mock.spec.js`): line 2 — `/api/task/T-404/detail` → `{ status: 500, json: { error: 'x' } }`: on `#/task/T-404` the block reads "Could not load", not "not found"; and in the detail modal (click a board card whose id is `T-404`; add it to the spec's board) the same.
- [ ] **Step 4: Run** — Expected: new tests FAIL (parse failure has no code; `T-404` reads as not found; null docs row faulted).
- [ ] **Step 5: Implement.** In `keyvalue-field.js`, a row keeps `raw: null` beside its empty text; `kept()` returns `null` for it while its text is still empty; `faults()` skips such a row. Replace each `/→ 404\b/.test(String(e?.message))` with `e?.code === 404` and delete the comment above it that explains the message format. Remove `currentEtag` from both banner signatures, their JSDoc, both callers and the tests.
- [ ] **Step 6: Run** the unit suite and `MOCK_PORT=8843 npm --prefix <wt>/viewer run test:mock -- --workers=2 task-missing.mock.spec.js task-form.mock.spec.js task-detail.mock.spec.js conflict-banner.mock.spec.js` — Expected: PASS.
- [ ] **Step 7: Commit** — `fix(viewer): an unreadable answer is not called a network failure, "not found" comes from the status not the message, an empty stored doc is left alone, and the banner's unused etag and the form's no-op validator are gone`

---

### Task 4: Test and tool hygiene — capture tools, unmocked reads, flaky specs, the icon test, font licences

**Depends on:** Task 2 (`rows.mock.spec.js`, `chips.mock.spec.js`). **Parallel:** wave 2, beside Task 3.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs`, `viewer/tests/tools/capture.mjs`, `viewer/tests/mock-api.js`, `viewer/tests/unit/favicon.test.js`, `viewer/tools/gen-favicon.mjs` (usage comment), the flaky spec files (today `viewer/tests/rows.mock.spec.js`, `viewer/tests/modal.mock.spec.js`, `viewer/tests/chips.mock.spec.js`), `viewer/tests/live-guard.js` (`--list`), `viewer/tests/unit/live-specs-guard.test.js` (temp dirs), `viewer/tests/shell.mock.spec.js` (the icon-link test), `viewer/tests/mock-fixtures.js` and every spec that imports the folded issue/bug fixtures
- Delete: the live-server specs whose screens plan 3 replaced with mocked specs (at least `viewer/tests/epics.spec.js`, `epic-detail.spec.js`, `issues.spec.js`, `issues-routing.spec.js`; Step 1 lists the rest)
- Create: `viewer/tests/unit/capture-tools.test.js`, `viewer/tests/unit/font-licences.test.js`, `viewer/vendor/fonts/OFL-LeagueSpartan.txt`, `viewer/vendor/fonts/OFL-DMSans.txt`, `viewer/vendor/fonts/OFL-JetBrainsMono.txt`
- Test: `viewer/tests/mock-api.mock.spec.js`

**Interfaces:**
- Produces:
  ```js
  // mock-api.js
  export function unmockedReads(page) → string[]   // distinct "GET /api/…" (or HEAD) answered by the {} catch-all
  ```
  `capture-modals.mjs`: `--port=` is checked free before the static server starts (a port in use exits 2 with `port <n> is in use; pass --port=<free port>`); the server's stderr is piped and printed when it fails to start; `SIGINT` and `SIGTERM` stop the server and exit 130/143; unknown `--only` / `--themes` / `--widths` values exit 2 **before** the output directory is created. `capture.mjs`: the same `--only` order fix (it needs a live server and is not run by this plan). A scene may carry `mocks: ({ theme }) => table`, called with the scene's theme and used instead of the tool's default table (its prefs included) — Task 9 uses it.
- Behaviour (each line is a test):
  1. `unmockedReads` lists a GET nobody mocked and is empty when every read was mocked (`mock-api.mock.spec.js`).
  2. `node capture-modals.mjs <tmp>/out --only=nope` exits 2 and `<tmp>/out` does not exist.
  3. With a `net` server listening on a free port `p`, `node capture-modals.mjs <tmp>/out --port=p --only=detail-rich` exits 2 within 10 s, prints `in use`, and launches no browser (the output directory holds no `metrics.json`).
  4. The `relation-suggestions` scene waits for the field to stop moving (its `getBoundingClientRect().top` equal on two consecutive animation frames) instead of `waitForTimeout(300)`.
  5. `favicon.test.js`: every directory entry has `planes === 1`; every PNG payload ends with an `IEND` chunk (`png.toString('latin1', png.length - 8, png.length - 4) === 'IEND'`).
  6. `gen-favicon.mjs`'s usage comment says it needs `npm --prefix viewer install` first (it drives Playwright).
  7. Each `viewer/vendor/fonts/*.woff2` has its licence beside it: `OFL-<Family>.txt` containing `SIL OPEN FONT LICENSE Version 1.1` and a `Copyright` line (`font-licences.test.js`).
  8. **The smoke-guard tests leave no temp dirs.** `live-specs-guard.test.js`: a `before()` removes `tm-smoke-guard-*` directories in `os.tmpdir()` older than one hour (left by killed runs), and `runSmoke`'s `rmSync` gets `{ recursive: true, force: true, maxRetries: 5, retryDelay: 100 }` (on Windows a Git Bash child can still hold a handle). Test: the `tm-smoke-guard-*` names in `os.tmpdir()` after the file's tests are those before them minus the stale ones removed.
  9. **The live guard lets `--list` through.** `requireLiveOptIn` returns without refusing when `process.argv` includes `--list` (listing runs nothing and writes nothing). Test in `live-specs-guard.test.js`: `spawnSync(process.execPath, [<the @playwright/test cli>, 'test', '--config', 'tests/playwright.config.js', '--list'], { cwd: <viewer dir>, env: <env without TM_LIVE_SPECS_OK> })` exits 0; the same without `--list` and with a filter that matches no test still exits non-zero with the opt-in message.
  10. **The icon-link test asserts what matters, not the list.** Today `shell.mock.spec.js` "the tab icon is the pixel-fitted ICO, a real file the server can deliver" asserts that the `link[rel="icon"]` paths equal exactly `['/vendor/favicon.ico']`, which breaks the moment the open SVG-link decision is taken. It becomes: every `link[rel~="icon"]` answers 200 with an `image/*` content type, and `/vendor/favicon.ico` is among them with `sizes="16x16 32x32 48x48 256x256"`.
  11. **One issue set and one bug set.** `mock-fixtures.js` holds three overlapping issue/bug fixture families from plan 3 (3c `ISSUES`/`BUG`, 3d `LIST_ISSUES`/`LIST_BUGS`, 3e `ISSUES_LIST`/`BUGS_LIST`). Fold them, as the long boards were folded, into one `ISSUES` and one `BUGS`: the union of records, every id a spec or builder names kept with the fields it relies on (ISS-012 stale, ISS-009 fixed, ISS-1234 long, B-031 open, B-030 fixed, B-1234 long); single-record exports a spec needs (`BUG`, `BUG_FIXED`, `LONG_BUG`, `LONG_ISSUE`) become lookups into them; every builder and spec points at them; the other exports are deleted. A spec whose count assertion changes with the union gets its expected number updated, with the reason in the report. Test: the full mocked suite passes, and `grep -rn "LIST_ISSUES\|LIST_BUGS\|ISSUES_LIST\|BUGS_LIST" viewer/tests` finds nothing.
- Retire stale live specs: list every `viewer/tests/*.spec.js` that is not `*.mock.spec.js` and that `playwright.config.js` runs (not `threads-board.spec.js`, which the threads config serves itself). For each test in each, name the mocked test that covers the same behaviour. A live spec whose every test is covered is deleted — at least `epics.spec.js`, `epic-detail.spec.js`, `issues.spec.js` and `issues-routing.spec.js`, whose screens plan 3 rebuilt with mocked specs. A behaviour with no mocked cover gets a mocked test in that screen's `*.mock.spec.js` first. A live spec that must stay (it checks the real server) is listed in the ledger with its reason. The mapping table goes in the report.
- Flakes: run `MOCK_PORT=8844 npm --prefix <wt>/viewer run test:mock -- --workers=2 --repeat-each=5 rows.mock.spec.js modal.mock.spec.js chips.mock.spec.js`. For every test that fails at least once, find the race (no fixed sleeps: wait on the condition the assertion needs — the animation's end, a layout settled over two frames, the element's final box) and fix the test, or the code when the race is the product's. Re-run `--repeat-each=10` on those files: zero failures. Name each fixed test and its cause in the report.

- [ ] **Step 1: Re-derive.** Read both capture tools' argument handling and server start; `grep -n "waitForTimeout" viewer/tests/tools/capture-modals.mjs`; run the flake sweep above and record which tests failed and how often; list the issue/bug fixture exports in `mock-fixtures.js` and their importers; list the live specs and build the coverage mapping; count the `tm-smoke-guard-*` leftovers in `os.tmpdir()`.
- [ ] **Step 2: Tests** for lines 1, 2, 3, 5, 7, 8, 9, 10 (`capture-tools.test.js` uses `spawnSync(process.execPath, [tool, …], { timeout: 20000 })` and `mkdtempSync`; it removes its temp dir in `after()`).
- [ ] **Step 3: Run** — Expected: FAIL (no `unmockedReads`; the out dir is created before validation; no port check; no planes/IEND checks; no licence files; `--list` refused; the icon test fails only once an extra icon link is added in a scratch run).
- [ ] **Step 4: Implement.** Licence files: fetch the three upstream licence files verbatim — `https://raw.githubusercontent.com/google/fonts/main/ofl/leaguespartan/OFL.txt`, `…/ofl/dmsans/OFL.txt`, `…/ofl/jetbrainsmono/OFL.txt` — and save them under the names above. If the network is unavailable, stop this item, leave the test out, and append `- fonts: the three vendored fonts ship without their OFL licence files — network unavailable during plan 4 — user-visible: no` to the ledger. Then the flake fixes, the fixture fold (line 11) and the live-spec retirement.
- [ ] **Step 5: Run** the unit suite; `MOCK_PORT=8844 npm --prefix <wt>/viewer run test:mock -- --workers=2` (the full mocked suite: the fixture fold touches many specs); `node <wt>/viewer/tests/tools/capture-modals.mjs <shots-t4> --port=8844` (every scene: the scenes read the folded fixtures too) — Expected: PASS, the capture exits 0, port 8844 free afterwards.
- [ ] **Step 6: Commit** — two commits: `test(viewer): capture tools refuse a busy port and bad options before touching disk and stop their server on Ctrl+C; mocks report unmocked reads; the icon test checks planes and IEND and tolerates more icon links; the live guard lets --list through and the guard tests leave no temp dirs; flaky specs wait on conditions; vendored fonts carry their licences`, then `test(viewer): one issue and one bug fixture set, and the live specs whose screens have mocked specs are retired`

---

### Task 5: Legacy rules out — `components.css` and `_placeholders.css` are gone

**Depends on:** Tasks 1, 2, 3 (the router no longer emits `.stub`; the components they touched are final). **Parallel:** wave 3, alone.

**Files:**
- Delete: `viewer/css/components.css`, `viewer/css/screens/_placeholders.css` (both links in `viewer/index.html`)
- Create (only for a rule block that still has a consumer): `viewer/css/components/copy.css` (`.cmp-flash-copied`, today used by `lib/copy.js`), `viewer/css/components/right-rail.css` (only if plan 3e left the `.right-rail` block in `components.css`)
- Modify: `viewer/index.html`; `viewer/css/components/chips.css` (`.tm-chip-row`, only if still used); JS files that still use a deleted legacy class (today `components/card.js` `.cmp-icon-btn`, `screens/bug-detail.js` `.cmp-btn`); `viewer/js/components/empty-state.js` (delete `emptyState()` if nothing imports it)
- Create test: `viewer/tests/unit/legacy-css.test.js`

**Interfaces:**
- Consumes: `.btn` family (`button.css`), `stateBlock` (`state.css` owns every `.tm-empty*` rule), `handover-status.css` (owns every `.ho-status-pill*` rule).
- Produces: no new API. `viewer/css/components.css` does not exist.
- Behaviour (`legacy-css.test.js`; each line is a test):
  1. `viewer/css/components.css` and `viewer/css/screens/_placeholders.css` do not exist, and `viewer/index.html` links neither.
  2. No CSS file has a selector containing `.cmp-chip`, `.cmp-pill`, `.cmp-btn`, `.cmp-icon-btn`, `.tm-card` or `.stub` (use `rules()` exported by `style-rules.test.js`).
  3. Every rule whose selector contains `.tm-empty` is in `components/state.css`; every rule whose selector contains `.ho-status-pill` or `.ho-status-menu` is in `components/handover-status.css` or `components/popover.css`.
  4. No file under `viewer/js` (excluding `js/_dormant/`) or `viewer/index.html` contains the class names `cmp-chip`, `cmp-pill`, `cmp-btn`, `cmp-icon-btn`, `tm-card`, `stub-meta` or `'stub'`.

- [ ] **Step 1: Re-derive the inventory.** For each top-level rule block in `components.css` (today: `.cmp-chip*`, `.cmp-pill*`, `.cmp-btn*`, `.cmp-flash-copied`, `.cmp-icon-btn*`, `.right-rail*`, `.tm-chip-row`, `.tm-card`, `.tm-empty*`, `.ho-status-pill*`), list every class it styles and grep `viewer/js` (minus `_dormant`), `viewer/index.html` and `viewer/tests` for each. Classify each block: **dead** (no JS/HTML use), **duplicate** (`.tm-empty*`, `.ho-status-*` — owned elsewhere), **live** (used, no other home). Same for `_placeholders.css` (`.stub` — dead after Task 1). Put the table in the report.
- [ ] **Step 2: Write `legacy-css.test.js`** (lines 1–4). Run — Expected: FAIL.
- [ ] **Step 3: Capture before.** `node <wt>/viewer/tests/tools/capture-modals.mjs <shots-t5>/before --port=8845` (every scene).
- [ ] **Step 4: Remove.** Dead and duplicate blocks: delete. Live blocks: move to the component file named above, rewritten on tokens (the plan 3 Global Constraints apply; the file is enforced by Task 7 — make it pass `violations()` now), each new file with a `User intent:` header and linked in `index.html` after `button.css`. A JS consumer of a dead legacy class switches to the shared class (`.cmp-btn` → `btn btn--secondary btn--sm`; `.cmp-icon-btn` → `btn btn--ghost btn--icon btn--sm` with an `aria-label`). Delete `emptyState()` if `grep -rn "emptyState\b" viewer/js` finds only its definition. Delete both files and their `<link>` lines.
- [ ] **Step 5: Capture after** into `<shots-t5>/after` and compare scene by scene: `metrics.json` problems must be empty; for each scene whose route used a removed class, LOOK at both images and describe any difference in the report. A difference that is a regression (lost spacing, unstyled control) is fixed in the owning component file.
- [ ] **Step 6: Run** the unit suite and `MOCK_PORT=8845 npm --prefix <wt>/viewer run test:mock -- --workers=2` (full mocked suite) — Expected: PASS.
- [ ] **Step 7: Commit** — `refactor(viewer): the legacy shared stylesheet and the placeholder stub are gone — every live rule lives with its component, every dead one is deleted`

---

### Task 6: Migration aliases out of `tokens.css`, every stylesheet and every script

**Depends on:** Task 5. **Parallel:** wave 4, alone.

From spec §3.2 ("stage 5 deletes them") and §9 stage 5.

**Files:**
- Modify: `viewer/css/tokens.css` (delete both `Legacy aliases — DELETE in plan 4` blocks; keep and re-file the tokens below), every CSS file that still references an alias, every JS file that still writes one (today `components/card.js:74`, `lib/epics.js`, `components/epic-chips.js`, `components/epic-dropdown.js` — plan 3a may have removed some), `viewer/tests/unit/contrast.test.js` (delete the test "legacy aliases that paint the accent as text go through --text-accent")
- Create: `viewer/tests/unit/legacy-aliases.test.js`

**Interfaces:**
- Produces:
  ```js
  // legacy-aliases.test.js — the names spec §3.2 and stage 1 introduced as stand-ins. Not a prefix ban: --bg-page,
  // --bg-recessed, --accent-cyan … are RR names and stay.
  export const LEGACY = ['--bg-canvas', '--bg-shell', '--bg-panel', '--bg-card', '--bg-card-hover', '--bg-board-col',
    '--bg-deep', '--bg-issue', '--border', '--border-soft', '--ink', '--ink-1', '--ink-2', '--ink-3', '--ink-4',
    '--ink-on-accent', '--accent', '--accent-2', '--accent-soft', '--accent-blue', '--accent-edit', '--accent-green',
    '--green', '--amber', '--gold', '--red', '--purple', '--diff-add', '--diff-mod', '--diff-del', '--sev-critical',
    '--sev-high', '--sev-medium', '--sev-low', '--epic-1', '--epic-2', '--epic-3', '--epic-4', '--epic-5', '--epic-6',
    '--bundle-1', '--bundle-2', '--bundle-3', '--bundle-4', '--bundle-5', '--bundle-6', '--font-sans', '--font-mono',
    '--font-serif', '--text-xs', '--text-sm', '--text-base', '--text-md', '--text-lg', '--text-xl', '--text-2xl',
    '--text-3xl', '--sp-1', '--sp-2', '--sp-3', '--sp-4', '--sp-5', '--sp-6', '--sp-7', '--sp-8', '--sp-9', '--sp-10',
    '--sp-12', '--sp-13', '--r-sm', '--r-md', '--r-lg', '--r-xl', '--r-2xl', '--t-fast', '--t-base', '--t-slow',
    '--ease', '--page-pad', '--page-gap', '--bl', '--surface', '--surface-1', '--surface2', '--s2', '--danger-ink',
    '--danger-border', '--danger-bg', '--amber-tint-bg', '--amber-tint-border', '--muted-tint-bg', '--muted-tint-border',
    '--shell-bg-sidebar', '--shell-label-size', '--shell-hover-overlay', '--card-recent-glow', '--issues-card-bg',
    '--issues-card-border', '--issues-investigating', '--task-grid-gap', '--task-section-mb', '--task-section-h',
    '--graph-frame-bg', '--graph-frame-border', '--graph-frame-shadow', '--graph-edge-stroke',
    '--graph-edge-stroke-active', '--graph-col-guide'];
  ```
  Kept, moved under `/* ── Set from JS ── */` or `/* ── Layout ── */` with a one-line comment each: `--conflict-banner-height`, `--tilt`, `--anim-dur`, `--card-w`, `--epic`, `--epic-soft`, `--ec`, `--ec-soft`, `--bundle` (each only while some JS still sets it — `setProperty('--x'` or `--x:` in a style string — else deleted); `--task-rail-w`, `--graph-canvas-h` (layout sizes with no RR token; kept while a stylesheet uses them, recorded as accepted). `--bundle`'s default becomes `var(--cat-2)` (it pointed at `--bundle-1`).
- Replacement table (a use is replaced by the role on the right; "shape" = `background`, `border*`, `fill`, `stroke`, `outline-color`; "text" = `color`, `-webkit-text-fill-color`, `caret-color`):

  | Alias | Replacement |
  |---|---|
  | `--bg-canvas` | `--bg-page` |
  | `--bg-shell`, `--shell-bg-sidebar` | `--surface-ground` |
  | `--bg-panel`, `--bg-card`, `--bg-issue`, `--surface`, `--surface-1`, `--issues-card-bg` | `--card-bg` |
  | `--bg-card-hover`, `--shell-hover-overlay` | `--card-bg-hover` |
  | `--bg-board-col` | `--col-bg` |
  | `--bg-deep`, `--graph-frame-bg` | `--bg-recessed` |
  | `--surface2`, `--s2` | `--overlay-surface` inside a modal or popover, else `--card-bg-hover` |
  | `--border`, `--border-soft`, `--issues-card-border`, `--graph-frame-border`, `--graph-col-guide` | `--border-subtle` |
  | `--bl`, `--muted-tint-border` | `--border-default` |
  | `--ink`, `--ink-1` | `--foreground-bold` |
  | `--ink-2` | `--foreground-default` |
  | `--ink-3`, `--ink-4` | `--foreground-subtle` (`--foreground-disabled` on a disabled control) |
  | `--ink-on-accent` | `--on-accent-fill` |
  | `--accent`, `--graph-edge-stroke-active` | text: `--text-accent`; shape: `--signature` |
  | `--accent-2`, `--accent-blue`, `--accent-edit`, `--issues-investigating` | `--text-accent` |
  | `--accent-soft` | `--signature-glow` |
  | `--graph-edge-stroke` | `--signature-dim` |
  | `--green`, `--accent-green`, `--diff-add` | shape: `--tone-success`; text: `--foreground-default` plus the marker shape |
  | `--amber`, `--gold`, `--diff-mod`, `--sev-medium`, `--amber-tint-border` | shape: `--tone-warning`; text: as above |
  | `--red`, `--danger-ink`, `--danger-border`, `--diff-del`, `--sev-critical` | shape: `--tone-critical`; text: as above |
  | `--sev-high` | shape: `--tone-orange`; text: as above |
  | `--sev-low` | `--tone-neutral` |
  | `--purple` | shape: `--pastel-signature`; text: `--foreground-default` |
  | `--danger-bg` | `--color-critical-subtle` |
  | `--amber-tint-bg` | `--color-warning-subtle` |
  | `--muted-tint-bg` | `--ground-15` |
  | `--epic-N` | `--cat-N` |
  | `--bundle-N` | `--cat-(N mod 6 + 1)` (`--bundle-1` → `--cat-2` … `--bundle-6` → `--cat-1`) |
  | `--font-sans`, `--font-serif` | `--font-narrator` |
  | `--font-mono` | `--font-technical` |
  | `--text-xs` | `--size-technical-small` |
  | `--text-sm` | `--size-narrator-small` |
  | `--text-base`, `--text-md` | `--size-narrator-default` |
  | `--text-lg` | `--size-narrator-large` |
  | `--text-xl`, `--text-2xl` | `--size-declaration-h4` |
  | `--text-3xl` | `--size-declaration-h3` |
  | `--sp-1` / `-2` / `-3` / `-4`,`-5` / `-6`,`-7`,`-8` / `-9`,`-10` / `-12` / `-13` | `--space-micro` / `-xs` / `-sm` / `-md` / `-lg` / `-xl` / `-2xl` / `-3xl` |
  | `--r-sm` | `--radius-lg` |
  | `--r-md`, `--r-lg`, `--r-xl`, `--r-2xl` | `--radius-xl` |
  | `--t-fast` / `--t-base` / `--t-slow` | `--dur-micro` / `--dur-standard` / `--dur-macro` |
  | `--ease` | `--ease-hourglass` |
  | `--page-pad` | `--page-gutter` |
  | `--page-gap`, `--task-grid-gap`, `--task-section-mb` | `--space-md` |
  | `--task-section-h` | `--space-sm` |
  | `--shell-label-size` | `--size-technical-label` |
  | `--card-recent-glow`, `--graph-frame-shadow` | delete the declaration that uses it (it was a shadow value) |

  A hue alias used as **text** is a word in a hue, which the hue-on-text rule forbids: the word becomes the foreground role and, when the hue carried meaning (a status, a severity, a diff kind), the element gets its marker shape. When that is more than a one-element change in a screen file, it is a plan 3 miss: report `NEEDS_CONTEXT` with the file and selector.
  JS: `components/severity-glyph.js` and `components/severity-label.js` go dead once plan 3c's pages use `severityMarker()`: `grep -rn "severity-glyph\|severity-label" viewer/js viewer/tests` must find no importer, then both files are deleted with any unit test that only exercises them (an importer that remains switches to `severityMarker()` from `status.js` first, in this task) — they are not re-tokened. `card.js`'s inline `cssText` becomes a class on tokens in the card's own stylesheet.
- Behaviour (`legacy-aliases.test.js`; each line is a test):
  1. `tokens.css` defines no `LEGACY` name (`/(--name)\s*:/`).
  2. No CSS file references one (`/var\(\s*--name\s*[,)]/`).
  3. No file under `viewer/js` (excluding `js/_dormant/`), nor `viewer/index.html`, contains one as a whole name (`/(^|[^\w-])--name(?![\w-])/`).
  4. Every token under `/* ── Set from JS ── */` in `tokens.css` is set by some file under `viewer/js` (`setProperty('--x'` or `--x:` in a string).

- [ ] **Step 1: Re-derive.** Write `legacy-aliases.test.js`; run it — the failure output is the work list (every remaining definition and use, by file). Paste the counts per file in the report. Plan 3's files should show none; each hit there is a plan 3 miss fixed here by the table.
- [ ] **Step 2: Capture before.** `node <wt>/viewer/tests/tools/capture-modals.mjs <shots-t6>/before --port=8846`.
- [ ] **Step 3: Replace** every use by the table (CSS and JS), delete both alias blocks, re-file the kept tokens, delete the `contrast.test.js` alias test.
- [ ] **Step 4: Capture after** into `<shots-t6>/after`. **Review Focus 3:** for every scene and theme, the axe `color-contrast` node count in `after/metrics.json` is not higher than in `before/metrics.json` (list both numbers for every scene where either is non-zero); LOOK at the light-theme images of every scene whose route's CSS had a themed hue alias (`--green`, `--amber`, `--gold`, `--red`, `--danger-ink`, `--sev-*`, `--diff-*`, `--purple`) and describe any colour change.
- [ ] **Step 5: Run** `env --chdir=<wt> node --test viewer/tests/unit/legacy-aliases.test.js viewer/tests/unit/style-rules.test.js viewer/tests/unit/contrast.test.js`, the unit suite, and the full mocked suite on port 8846 — Expected: PASS; "every var(--x) used in any CSS file is defined" still passes.
- [ ] **Step 6: Commit** — `refactor(viewer): the stage-1 migration aliases are gone — every stylesheet and script names the RR role it means, in both themes`

---

### Task 7: The style rules hold on every stylesheet

**Depends on:** Task 6. **Parallel:** wave 5, alone.

From spec §1 criterion 4, §8 ("Report-only in stage 1, enforcing from stage 5"), §3.3–3.4 (type and radius tokens only).

**Files:**
- Modify: `viewer/tests/unit/style-rules.test.js`; `viewer/css/tokens.css` (`/* ── Layout ── */`: `--control-size`, `--control-size-sm`, `--touch-target`); `viewer/css/components/modal.css`, `viewer/css/components/button.css`, `viewer/css/components/edit-fields.css` (bare control sizes); every CSS file the new "all files" run reports (expected after plan 3: `screens/continuity.css` and any 44px literal or off-scale radius)

**Interfaces:**
- Produces:
  ```css
  /* tokens.css, Layout — sizes the RR scale has no name for, derived from it where it can be */
  --control-size: var(--space-xl);      /* 32px: an icon button, a field, a modal heading row */
  --control-size-sm: var(--space-lg);   /* 24px: a small button, a chip */
  --touch-target: 44px;                 /* the smallest thing a finger is asked to hit, at ≤768px */
  ```
  `style-rules.test.js`: `ENFORCED` and the "report" test are deleted; "every CSS file satisfies every style rule" runs `violations()` over every file under `viewer/css` (tokens.css under its own existing test). `rules()` is unchanged in shape; quoted strings are neutralised before parsing.
- New rules (each with selftests both ways — **Review Focus 4**):
  1. **Quoted strings.** Before `rules()` parses, each `"…"` / `'…'` (with escapes) has its contents replaced by `x`: `content: "{"` no longer corrupts the parse, and a quoted name (`font-family: 'Archivo Black'`, `content: "red"`) is never a colour literal. Selftests: caught `.a::before { content: '}'; } .b { color: #fff; }` (on `.b`); clean `.a { font-family: 'Archivo Black', var(--font-narrator); }`, `.a::after { content: "red"; }`.
  2. **Radius on the scale.** `border-radius` and every `border-*-radius` longhand: each word is `0`, `var(--radius-xs|sm|md|lg|xl|full)`, `/`, `inherit`, `initial`, `unset` or `revert`. Message `radius off the RR scale (<value>)`. Selftests: caught `border-radius: 6px`, `50%`, `999px`, `var(--r-md)`; clean `0 var(--radius-md) var(--radius-md) 0`, `var(--radius-full)`.
  3. **Touch target is one token.** `height`, `min-height`, `width`, `min-width`, `block-size`, `min-block-size`, `inline-size`, `min-inline-size` with a value `44px` → `touch target literal (use --touch-target)`. Selftests: caught `min-height: 44px`; clean `min-height: var(--touch-target)`, `min-height: 440px`.
  4. **No rail drawn by a pseudo-element.** A rule whose selector has `::before` or `::after`, whose body sets `left: 0` or `inset-inline-start: 0` (or an `inset` whose fourth value is `0`), a `width` of at most 4px (`1px`–`4px` or `var(--space-micro)`), and a `background`/`background-color` that is not `none`, `transparent` or `var(--border-*)` → `left rail via pseudo-element`. Selftests: caught `.a::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--signature); }`; clean the same with `background: var(--border-default)` (a tree guide line), and `.dot::before { left: 0; width: 8px; background: var(--tone-success); }`.
  5. **Every CSS named colour.** `COLOR_NAME` (today ten names) becomes the full CSS Color 4 list as a `Set`, matched as whole words in the value left after strings, `url()` and `var(--…)` are taken out (the existing `bare()` plus rule 1): `aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen` (148; a selftest asserts the size). Selftests: caught `color: tomato`, `border-color: rebeccapurple`, `background: linear-gradient(to bottom, ivory, var(--card-bg))`; clean `white-space: nowrap`, `animation-name: rise`, `color: transparent`, `color: currentColor`, `font-family: 'Snow Sans', var(--font-narrator)`.
  6. **Selector lists are judged one selector at a time.** `rules()` keeps the selector text; the checks that depend on the selector (hover motion, rule 4's pseudo-element, rule 7's one-sided border) split it on top-level commas (never inside `:is()`/`:where()`/`:not()` parentheses) and judge each selector alone, and the message names that selector, not the whole list. Selftests: `.a, .b:hover { transform: scale(1.1); }` caught with `.b:hover` in the message; `.a:hover, .b { color: var(--foreground-bold); }` clean; `.a::before, .b { left: 0; width: 3px; background: var(--signature); }` caught for `.a::before` only; `:is(.a, .b):hover { translate: 0 -1px; }` caught as one selector.
  7. **No rail drawn with a gradient or a one-sided border.** (a) A `background`/`background-image` holding a `linear-gradient` whose direction is `to right` or `90deg` and which has a colour stop at a px position of 4px or less (or `var(--space-micro)`) whose colour is not `transparent` or `var(--border-*)` → `left rail via gradient`. (b) In one rule, a left border made the only (or the widest) side — `border-width` with four values whose fourth is the only non-zero one or larger than the others, or `border-left-width` / `border-inline-start-width` larger than the rule's `border-width` — together with a `border-color` / `border-left-color` that is not `var(--border-*)` or `transparent` → `colored border-left via border-width`. The two halves split across rules are still not checked (the existing comment says why; it is kept and narrowed to that case). Selftests: caught `background: linear-gradient(to right, var(--signature) 3px, transparent 3px)`, `background-image: linear-gradient(90deg, var(--tone-critical) 0 4px, var(--card-bg) 4px)`, `.a { border-width: 0 0 0 3px; border-style: solid; border-color: var(--signature); }`, `.a { border: 1px solid; border-left-width: 3px; border-color: var(--tone-critical); }`; clean `linear-gradient(to right, var(--border-default) 1px, transparent 1px)`, `linear-gradient(to bottom, var(--signature-glow) 2px, transparent)`, `linear-gradient(to right, transparent, var(--card-bg))` (the Table's scroll cue), `.a { border-width: 1px; border-color: var(--tone-critical); }` (a full-perimeter tint), `.a { border-width: 0 0 0 1px; border-color: var(--border-default); }`.
- Control sizes: `modal.css` — `.modal-heading { min-height: var(--control-size) }`, at ≤768px `.modal-close { width: var(--touch-target); height: var(--touch-target) }`, `.modal-heading { min-height: var(--touch-target) }`, `.modal--confirm .modal-footer .btn { min-height: var(--touch-target) }`; `button.css` — `.btn--sm { min-height: var(--control-size-sm) }`, `.btn--icon { width: var(--control-size); height: var(--control-size) }`, `.btn--icon.btn--sm { width: var(--control-size-sm); height: var(--control-size-sm) }`; `edit-fields.css` — the fields' `min-height: 32px` → `var(--control-size)`, `.ef-chip { min-height: var(--control-size-sm) }`, `.ef-estimate-size { min-height: calc(var(--control-size) - 2px) }` (its group's 1px padding on each side). Other px in `edit-fields.css` stay and are recorded as accepted (`no`): 1px hairlines; the 8px error mark and 20px chip-remove glyph boxes; the select chevron's `16px` and `-8px` (the icon's own size); field widths (`72px`, `140px`, `96px`, `160px`, `200px`, `40px`, `min-width: 200px`), which are sized to their content, not to a scale.

- [ ] **Step 1: Re-derive.** Run the current test; copy the "style-rules backlog" line (every unenforced file and its count) into the report. Any screen file with more than 10 violations is a plan 3 miss: stop and report `NEEDS_CONTEXT` naming the file and its track. `screens/continuity.css` (the dashboard's continuity band) is converted here if it is still on the list.
- [ ] **Step 2: Write the seven rules' selftests** and the switch to all files. Run — Expected: selftests FAIL (rules absent); the all-files test FAILS listing every violation.
- [ ] **Step 3: Implement** the rules and the tokens. Convert every reported violation: colour literals → the role the value matches in `tokens.css` (never a new literal); raw font sizes → the type token of that voice; off-scale radii → the nearest of the six by component kind (spec §3.4: chips/tags `--radius-sm`, buttons/inputs `--radius-md`, cards `--radius-lg`, modals/panels `--radius-xl`, dots/avatars `--radius-full`); `44px` → `var(--touch-target)`; italic → normal; shadows deleted.
- [ ] **Step 4: Run** `env --chdir=<wt> node --test viewer/tests/unit/style-rules.test.js` — Expected: PASS with zero violations in every file; then the unit suite and the full mocked suite on port 8847 (modal, button and field sizes are pinned by `modal.mock.spec.js`, `task-form.mock.spec.js`) — Expected: PASS.
- [ ] **Step 5: Commit** — `test(viewer): the style rules are enforced on every stylesheet — quoted strings parsed safely, radii on the RR scale, one touch-target token, no rails drawn with pseudo-elements, gradients or one-sided borders, every named colour caught, selector lists judged per selector — and control sizes are tokens`

---

### Task 8: The accessibility gate — every route, both themes, both widths

**Depends on:** Tasks 4 (`unmockedReads`), 7. **Parallel:** wave 6, alone.

From spec §1 criteria 1–3, §8 ("axe-core on every route × both themes; zero `color-contrast`, `label`, `select-name`, `nested-interactive`, landmark failures; zero pointer-only targets … kept as `viewer/tests/a11y.spec.js`").

**Files:**
- Create: `viewer/tests/route-fixtures.js`, `viewer/tests/a11y-probe.js`, `viewer/tests/a11y.mock.spec.js` — named `a11y.mock.spec.js` (not `a11y.spec.js`) so the mocked config's `testMatch: /\.mock\.spec\.js$/` runs it and the live config never does; spec §11 records the rename (Task 10)
- Modify: `viewer/tests/mock-fixtures.js` (only where a builder below misses a read the gate reports), `viewer/tests/desk.mock.spec.js` (the summary-strip landing test, if 3e skipped it), and whatever component or screen file a gate failure points at

**Interfaces:**
- Consumes: `mockApi`, `unmockedWrites`, `unmockedReads` (Task 4); plan 3's route builders in `mock-fixtures.js` (`.superpowers/sdd/plan-3-reconcile.md`, "Plan-4 reusable mocks"). Ruling sent to every track: each builder takes `{ theme = 'dark' } = {}` and sets `'/api/viewer/prefs'` itself, so the gate never spreads a prefs key of its own over it (a builder's own prefs — `settingsMocks`'s `card_density`, `issueDetailMocks`'s aging — would otherwise be lost, or the builder's dark theme would override the gate's).
- Produces:
  ```js
  // route-fixtures.js — one table every route-level tool reads (the gate here, the capture scenes in Task 9).
  export const ROUTES = [
    // { name, route, build, ready, open?, state? }
    //   route: the hash the page is opened on
    //   build: ({ theme }) → the mockApi table (a plan 3 builder, or one spread with a single override key)
    //   ready: a selector visible only once the route shows real content — or, with `state`, its state block
    //   open:  async (page) → void, run after `ready` for a state that is not a route (the detail modal)
    //   state: 'missing' for the routes that exist to show a not-found block
  ];
  // a11y-probe.js — each function runs inside the page (page.evaluate(fn)) and closes over nothing.
  export function pointerOnlyTargets() → string[]   // shown elements with cursor:pointer, no focusable self or ancestor,
                                                    // not inheriting the pointer from their parent, not a label with a
                                                    // control, not a .link-row whose link covers it; "tag#id.class \"text\""
  export function smallTouchTargets() → string[]    // shown, enabled a[href], button, input (not hidden), select, textarea,
                                                    // summary, [role=button|tab|menuitem|menuitemradio|option] under 43.5px
                                                    // tall; skips display:inline links, anything inside .md-body, and a
                                                    // checkbox/radio whose label is ≥43.5px; "shown" = width and height > 1
                                                    // and inside the viewport horizontally
  ```
- `ROUTES`, in this order (20 entries; the not-found state is `.tm-empty[data-state="missing"]` everywhere — plan 3 ruling):

  | name | route | build | ready |
  |---|---|---|---|
  | `dashboard` | `#/dashboard` | `dashboardMocks` | `.dk-note[data-note-id="NOTE-001"] .dk-note__body` |
  | `kanban` | `#/kanban` | `kanbanMocks` | `.card-task[data-task-id] > .link-row__link` |
  | `detail-modal` | `#/kanban` | `kanbanMocks` | `.card-task[data-task-id] > .link-row__link`; `open`: click T-102's card link, wait for `.modal--detail .td-doc--embedded` |
  | `table` | `#/table` | `tableMocks` | `table.tbl .tbl-row` |
  | `epics` | `#/epics` | `epicsMocks` | `.epic-row .link-row__link` |
  | `epic` | `#/epic/viewer` | `epicDetailMocks` | `h1.ed-title` |
  | `epic-missing` | `#/epic/NOPE-999` | `(o) => ({ ...epicDetailMocks(o), '/api/epic/NOPE-999': { status: 404, json: { ok: false, error: 'unknown epic' } } })` | `.tm-empty[data-state="missing"]` |
  | `task` | `#/task/T-102` | `taskPageMocks` | `.td-page-A h1.td-title` |
  | `task-b` | `#/task/T-102?view=B` | `taskPageMocks` | `.td-page-B h1.td-title` |
  | `task-missing` | `#/task/NOPE-999` | `taskPageMocks` | `.tm-empty[data-state="missing"]` |
  | `issues` | `#/issues` | `issuesMocks` | `.issues-col .issue-card` |
  | `issue` | `#/issue/ISS-012` | `issueDetailMocks` | `.dp-page--issue h1.td-title` |
  | `issue-missing` | `#/issue/ISS-999` | `issueDetailMocks` | `.tm-empty[data-state="missing"]` |
  | `bugs` | `#/bugs` | `bugsMocks` | `.bugs__list .bug-row` |
  | `bug` | `#/bug/B-031` | `bugDetailMocks` | `.dp-page--bug h1.td-title` |
  | `bug-missing` | `#/bug/B-999` | `bugDetailMocks` | `.tm-empty[data-state="missing"]` |
  | `ideas` | `#/ideas` | `ideasMocks` | `.ideas__list .idea-row` |
  | `sessions` | `#/sessions` | `sessionsMocks` | `.ho-child[data-handover-id="2026-07-13-m1-shipped"]` |
  | `archived` | `#/archived` | `archivedMocks` | `.arch-row[data-task-id="T-1001"] .link-row__link` |
  | `settings` | `#/settings` | `settingsMocks` | `.set-control[role="group"] .tm-segmented > button[data-key="system"]` |

  Names, ids and selectors are the ones plan 3 recorded; Step 1 checks each against the merged code and corrects the table where the code differs (the report lists every correction). `epic-missing` is the one entry plan 3 did not provide; if the epic screen reads more than `/api/epic/<id>`, `unmockedReads` names the extra key and it is added to that override.
- The spec — 20 routes × 2 themes × 2 widths = 80 tests titled `<route> · <theme> · <desktop|phone>`, plus 2 topbar tests — in full:
  ```js
  // User intent: the release gate for accessibility — every route, in both themes and at both widths, is read by axe and by
  // the audit's probes, so a contrast, label, landmark, mouse-only or tiny-target regression cannot ship unnoticed.
  import { test, expect } from '@playwright/test';
  import { createRequire } from 'node:module';
  import { readFileSync } from 'node:fs';
  import { mockApi, unmockedWrites, unmockedReads } from './mock-api.js';
  import { ROUTES } from './route-fixtures.js';
  import { pointerOnlyTargets, smallTouchTargets } from './a11y-probe.js';

  const AXE = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
  // Spec §8's list. "Landmark failures" are axe's landmark-* rules; `region` (a best-practice rule that fires on the
  // banner and modal hosts by design) is not part of the gate.
  const GATE = ['color-contrast', 'label', 'select-name', 'nested-interactive', 'scrollable-region-focusable',
    'landmark-one-main', 'landmark-no-duplicate-main', 'landmark-unique', 'landmark-main-is-top-level',
    'landmark-complementary-is-top-level', 'landmark-banner-is-top-level', 'landmark-contentinfo-is-top-level',
    'landmark-no-duplicate-banner', 'landmark-no-duplicate-contentinfo'];
  const WIDTHS = [['desktop', 1440, 900], ['phone', 390, 844]];

  async function openRoute(page, r, theme) {
    await page.addInitScript((t) => { try { localStorage.setItem('tm.theme', t); } catch { /* storage unavailable */ } }, theme);
    await mockApi(page, r.build({ theme }));
    await page.goto(`/${r.route}`);
    await expect(page.locator(r.ready).first()).toBeVisible();
    await r.open?.(page);
    await expect(page.locator('#screen-mount [aria-busy="true"]')).toHaveCount(0);
    if (!r.state) await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
  }

  test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

  for (const r of ROUTES) for (const theme of ['dark', 'light']) for (const [size, width, height] of WIDTHS) {
    test(`${r.name} · ${theme} · ${size}`, async ({ page }) => {
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.setViewportSize({ width, height });
      await openRoute(page, r, theme);
      expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(theme);

      await page.addScriptTag({ content: AXE });
      const { missing, violations } = await page.evaluate(async (gate) => {
        const known = new Set(axe.getRules().map((x) => x.ruleId));
        const res = await axe.run(document, { runOnly: { type: 'rule', values: gate.filter((id) => known.has(id)) }, resultTypes: ['violations'] });
        return { missing: gate.filter((id) => !known.has(id)),
          violations: res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 8).map((n) => n.target.join(' ')).join(' | ')}`) };
      }, GATE);
      expect(missing, 'every gate rule exists in this axe version').toEqual([]);
      expect(violations).toEqual([]);
      expect(await page.evaluate(pointerOnlyTargets)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      if (width <= 768) expect(await page.evaluate(smallTouchTargets)).toEqual([]);
      expect(unmockedReads(page)).toEqual([]);
      expect(errors).toEqual([]);
    });
  }

  // Spec §4: the topbar is one of exactly two heights — one row, or one row plus row 2 — on every route; and a screen's
  // count reads "n <noun>", followed by " · m visible" only while something narrows the list — never "m of n"
  // (plan 3 ruling; with nothing narrowed, as here, a screen may show either form — Task 9 checks when the suffix shows).
  for (const [size, width, height] of WIDTHS) {
    test(`topbar · two heights and one count wording · ${size}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      const seen = [];
      for (const r of ROUTES.filter((x) => !x.open)) {
        await page.goto('about:blank');
        await page.unrouteAll({ behavior: 'ignoreErrors' });
        await openRoute(page, r, 'dark');
        seen.push(await page.evaluate((name) => {
          const row2 = document.querySelector('.topbar-row2');
          const twoRows = !!row2 && row2.getBoundingClientRect().height > 0;
          return { name, twoRows, h: Math.round(document.getElementById('topbar').getBoundingClientRect().height),
            count: document.getElementById('topbar-count')?.textContent.trim() ?? '' };
        }, r.name));
      }
      for (const twoRows of [false, true]) {
        const heights = [...new Set(seen.filter((s) => s.twoRows === twoRows).map((s) => s.h))];
        expect(heights.length, `${twoRows ? 'two-row' : 'one-row'} topbars: ${JSON.stringify(seen)}`).toBeLessThanOrEqual(1);
      }
      for (const s of seen.filter((x) => x.count)) {
        expect(s.count, s.name).toMatch(/^\d+ \S.*?( · \d+ visible)?$/);
        expect(s.count, s.name).not.toMatch(/\bof\b/);
      }
    });
  }
  ```
- Dashboard summary strip (spec §6 Dashboard: "each a link to the filtered screen"): `desk.mock.spec.js` must hold "a summary link lands on the filtered Table" (3e Task 8 Step 3b; it was conditional on 3b's route-parameter task merging first). If it is missing, add it: from `dashboardMocks()`, click "In progress" → `location.hash` is `#/table?status=in-progress`, the Status chip "In progress" has `aria-pressed="true"`, and every row's status cell reads "In progress". If the Table ignores the parameter, the fix is in `screens/table.js` (read `params.status` at mount into the status filter, as a filter the user can turn off).

- [ ] **Step 1: Build `ROUTES`** from the table above and write `a11y-probe.js` and the spec. Check every builder name, route id and `ready` selector against the merged `mock-fixtures.js` and the screens (a scratch run per route: `mockApi(page, build({ theme: 'light' }))`, `goto`, `ready` visible within 5 s, the page's `data-theme` is `light`); correct the table where the code differs and list each correction. A builder that ignores its `theme` argument is fixed in `mock-fixtures.js`. **Review Focus 5:** a content route whose `ready` would also match a state block is wrong — `ready` names real content (a card, a row, the record's heading). Then the summary-strip test above.
- [ ] **Step 2: Run** `MOCK_PORT=8848 npm --prefix <wt>/viewer run test:mock -- --workers=2 a11y.mock.spec.js` — Expected: some FAIL. Paste the failure list grouped by rule and file in the report.
- [ ] **Step 3: Fix** each failure in the file that owns the element (component CSS/JS or screen), the smallest change that satisfies the rule and the Global Constraints. A failure that needs a screen redesign is a plan 3 miss: report `NEEDS_CONTEXT`. Never weaken the gate, never scope axe away from an element, never add a route-specific exclusion.
- [ ] **Step 4: Run** the spec until all 82 pass (80 route tests, 2 topbar tests), then the unit suite and the full mocked suite — Expected: PASS.
- [ ] **Step 5: Commit** — `test(viewer): an accessibility gate over every route in both themes and both widths — axe's contrast, label, landmark and nesting rules, no mouse-only targets, no sideways scroll, 44px targets on a phone — and the fixes it asked for`

---

### Task 9: Visual re-audit

**Depends on:** Task 8. **Parallel:** wave 7, alone.

From spec §8 ("Visual verification: … a fresh-context agent re-runs the capture script in both themes at both widths and checks the result against this spec; the orchestrator reviews the screenshots") and §1 criterion 5.

**Files:**
- Modify: `viewer/tests/tools/capture-modals.mjs` (one `route-<name>` scene per `ROUTES` entry: `mocks: r.build`, `open` = `goto(r.route)` + wait for `r.ready` + `r.open?.(page)`, `fullPage: true`, `scope: 'body'`); whatever file a confirmed open finding points at
- Create: `docs/reports/<run date>-viewer-rr-reaudit.md` (the reviewer's findings table, committed)

- [ ] **Step 1: Scenes.** Add the route scenes (import `ROUTES` from `../route-fixtures.js`; `open` = `goto(r.route)` + wait for `r.ready` + `r.open?.(page)`; `mocks: r.build`). Run `node <wt>/viewer/tests/tools/capture-modals.mjs <shots-t9> --port=8849` (every scene: routes, plan 2a/2b's modal scenes and every scene plan 3 added; both themes, both widths). `metrics.json` `problems` must be empty and every scene's axe list free of `color-contrast`.
- [ ] **Step 2: Fresh-context review.** The controller dispatches a reviewer that has not seen this branch's work (`model: opus`), with: the shots directory, `metrics.json`, spec §2–§6 and §11, the audit file, and these instructions — for every one of the audit's 110 finding IDs, decide **fixed**, **open** (what is still wrong, which image shows it) or **not applicable** (the screen or element no longer exists), citing the image file; then list anything in the images that breaks spec §2's precedence rules or §6's per-screen description and has no finding ID; then name the five images most likely to disappoint the user and why. It reports text only. The controller saves the table as `docs/reports/<run date>-viewer-rr-reaudit.md` with a 1–3 line `User intent:` header.
- [ ] **Step 3: Close what is open.** Every **open** high or medium finding (spec §1 criterion 5) is fixed: in the owning file, with a mocked test where the defect is observable from the DOM (a box, a computed style, an attribute), then the scene re-captured and LOOKED at. A low finding is fixed the same way or appended to the ledger with a reason (it goes to spec §10 in Task 10). A fix that needs a screen redesign is reported `NEEDS_CONTEXT` as a release blocker. Carried visual items the reviewer must rule on explicitly (open or accepted): popover and suggestion-list surfaces against the dialog surface in dark; the conflict banner's row dividers in dark; the light banner headline; a light card on the page hovers to the page colour (it must step off the page, not merge into it); every screen's count reads "n <noun>" and adds " · m visible" only while a filter or the search narrows the list, never "m of n" (plan 3 ruling, 3d's rule is the target; Task 8's topbar test checks the wording, the reviewer checks the numbers match what is shown). Kanban, Table and Epics showing " · m visible" with nothing narrowed is a fix in this step: a one-line change in each screen's count call (the suffix only when the visible count differs from the total or a filter/search is set), pinned by one mocked test per screen — no filter: `#topbar-count` has no "visible"; after one filter: it ends "· m visible"; filter cleared: the suffix is gone.
- [ ] **Step 4: Re-review** the re-captured scenes with the same reviewer (SendMessage) until it reports no open high or medium finding; update the report file.
- [ ] **Step 5: Run** the unit suite and the full mocked suite — Expected: PASS.
- [ ] **Step 6: Commit** — `test(viewer): every route is a capture scene` (tool), one `fix(viewer): …` commit per coherent fix, its message saying what the user now sees, `docs(viewer): the re-skin re-audit — every audit finding fixed, accepted or gone`

---

### Task 10: CHANGELOG entry and spec bookkeeping

**Depends on:** Task 9. **Parallel:** wave 8, alone.

**Files:**
- Modify: `CHANGELOG.md` (repo root), `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` (§3.2, §8, §9, §10, §11; status line)

- [ ] **Step 1: Gather.** `git -C <wt> log --oneline $(git -C <wt> merge-base HEAD feat/database-native-foundation)..HEAD` (every commit of the re-skin), the ledger `accepted.md`, the re-audit report, spec §6 and §11.
- [ ] **Step 2: Write the entry** at the top of `CHANGELOG.md`, directly under the intro's `---` and above `## 7.0.0` (or whichever version is now first), followed by its own `---`. Voice and form as the existing entries: bold lead sentences, short paragraphs, bullet groups, measured facts where there are any. User-facing only: no audit IDs (`KB-01`), no backlog IDs (`B-095`), no plan or task numbers, no file names except where an operator or an API user needs one. The draft below is the starting point; for each bullet, the report names the commit or test that bears it out, and a bullet the branch does not bear out is deleted; a user-visible change found in the log and missing here is added to its group.

  ```markdown
  ## Unreleased

  **The viewer has a new look, in a dark and a light theme.** Every screen is rebuilt on one design system, so the board, the table, the epics, the task, issue and bug pages, sessions, the archive, the dashboard and settings share one set of colours, type, spacing and controls. The viewer starts dark; the button at the right of the top bar switches theme, and Settings offers Dark, Light or System. Its three fonts ship with the viewer: nothing is loaded from Google Fonts any more, and it looks the same offline.

  **Reading the board.**

  - Status, severity and priority are a shape and a word everywhere ("In review", "Critical"), never a colour alone and never an abbreviation.
  - A title or ID cut short ends in an ellipsis and shows its full text on hover; IDs never break across lines.
  - On a phone the Kanban and Issues boards show one column at a time behind a row of tabs, the table becomes stacked cards, and no screen scrolls sideways.
  - Filter chips stay on one line: those that do not fit wait behind "More", and top-bar controls that do not fit wait behind "Filters". A chip with nothing to show is disabled unless it is switched on.
  - The dashboard opens with a summary — In progress, Waiting on you, Open issues, Open bugs — each a link to the filtered screen.
  - An epic's page lists its tasks grouped by status, and one progress figure ("35/55 closed · 25 done · 10 archived") is used wherever an epic's progress is shown.

  **Keyboard and screen readers.**

  - Every card, row, chip, header and menu is a real link or button: Tab reaches it, Enter opens it, and Ctrl-click (⌘-click) opens a card or row in a new tab.
  - Table headers sort from the keyboard and announce the order.
  - Dialogs keep focus inside, close with Escape, and give focus back to what opened them. Ctrl K (⌘K) focuses search.
  - Text meets WCAG AA contrast in both themes, every field has a label, and focus is always visible.

  **Editing.**

  - Creating or editing a task or an idea uses one form: labelled fields, a field's problem shown once you leave it or press Save, and an in-app "Discard changes?" instead of the browser's own dialog.
  - A refused change says why, in words, beside the field or at the foot of the form — never a status code or the server's raw reply.
  - If someone else changed the task while you were editing it, a banner names each changed field with "Keep mine" and "Use server", and moves the dialog down instead of covering it.
  - A task is never offered as its own dependency.

  **Fixed.**

  - Bug pages show the bug; they were empty. `GET /api/bugs/<id>` returns one bug or 404, and `GET /api/bugs` still returns the list.
  - Issue pages show the discovered date and the evidence.
  - Epics no longer all read "Exploring", and every epic shows its progress bar.
  - A preference that fails to save is tried again, and the last change is saved when the tab closes.
  - A screen that cannot open says so in a sentence, with a way back to the dashboard.

  **Known limitations.**

  - (one bullet per `user-visible: yes` line of the ledger, in plain words)

  ---
  ```
  The "Known limitations" bullet above is replaced by the ledger's `yes` lines written as sentences; if there are none, the group is removed.
- [ ] **Step 3: Spec bookkeeping.** §3.2: one closing sentence — the aliases were deleted in stage 5 and a test keeps them out. §8: "Report-only in stage 1, enforcing from stage 5" → "Enforcing on every stylesheet (stage 5)", plus one line listing the rules added in plan 4 (quoted strings, radius scale, touch-target token, pseudo-element, gradient and one-sided-border rails, every named colour, selector lists judged per selector); the a11y gate line names `viewer/tests/a11y.mock.spec.js`, its rule list, and the not-found routes, overflow and phone-target checks. §9 stage 5: "(done)". §10: one bullet per ledger `no` line and per accepted low finding from the re-audit, each with its reason. §11: one bullet per ruling plan 4 made against earlier text — the alias list is explicit, not a prefix ban; `--control-size`, `--control-size-sm` and `--touch-target` are viewer layout roles; a link row's full text lives on the row, not the link; preferences retry three times and flush on `pagehide`; the gate is `a11y.mock.spec.js` and excludes axe's `region` rule; route captures are mocked (`capture-modals.mjs` route scenes) — `capture.mjs` needs a live server and is the user's tool. Status line: "implemented; awaiting release".
- [ ] **Step 4: Commit** — `docs: CHANGELOG entry for the viewer re-skin (Unreleased)` and, separately, `docs(viewer): spec records stage 5 — aliases gone, rules enforcing everywhere, the gate, the accepted items and plan 4's rulings`

---

### Task 11: Verification

**Depends on:** Tasks 1–10.

- [ ] **Step 1: Static checks** on the integration branch (`viewer-rr` worktree, after the controller has merged Task 10): `test ! -e viewer/css/components.css`; `grep -rn "Legacy aliases" viewer/css` → nothing; `grep -rn "ENFORCED" viewer/tests` → nothing; `grep -rn "→ 404" viewer/js` → nothing; `git -C <wt> status --short` → only ignored files.
- [ ] **Step 2: Full runs** with exact counts (passed / failed / skipped, and duration): unit suite (`style-rules.test.js`, `legacy-aliases.test.js`, `legacy-css.test.js` named individually in the report); full mocked suite with `MOCK_PORT=8851 … --workers=2` (`a11y.mock.spec.js`'s 82 named); server pytest command from Global Constraints. Compare with plan 2b's end (unit 890 / mocked 229 / pytest 332) and with plan 3's end as its controller recorded it; explain every difference by the tests added or removed.
- [ ] **Step 3: Captures.** `node viewer/tests/tools/capture-modals.mjs <shots-t11> --port=8851` — exit 0, `problems` empty, zero `color-contrast` nodes in every scene; port 8851 free afterwards.
- [ ] **Step 4: Report to the controller** (text, under 60 lines): the counts; the re-audit summary (fixed / open / not applicable / accepted by severity); the ledger's lines; the open user decision (the SVG favicon link beside the `.ico`, spec §11 "shipped as SVG and a multi-size .ico"); and the release steps that are the user's, not this plan's: versioning the `## Unreleased` heading, the version-string bump, pushing.
