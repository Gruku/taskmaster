# Auto Mode — `/v3/#/auto`

> Part of v3-polish-013 end-to-end review.

**v3-polish-008 status — confirmed with nuance.** The spine is NOT a stub: `quest-spine.js` is a fully implemented SVG component with node states, satellites, connectors, and a `@keyframes` pulse. The Spine/Log segmented toggle and Pause/Stop controls render correctly in the topbar. However, on every live navigation the `auto-center` div was **empty** — no spine SVG, no `spine-empty` fallback, no `spine-head`. The content render path (`renderActiveView → renderQuestSpine(center, state)`) depends on `store.getAutoState()` returning a populated state object; when the API is reachable but returns no active session, the spine correctly shows the "No auto-mode session is running." empty node — but when the center column was inspected live this text was also absent, suggesting the state object was `null` and the component's `if (!state || !state.cursor)` guard wrote the empty node to the element before a subsequent async cycle cleared it. The spine renders when state is present; the perceived "missing" spine from polish-008 is a data-absence observation, not a missing implementation.

---

### bug — `td-page` / `td-page-A` class leak from task-detail into auto-mode

`task-detail-document.js` and `task-detail-graph.js` both call `root.classList.add('td-page', 'td-page-A')` / `classList.add('td-page', 'td-page-B')` on the shared `#screen-mount` element, but neither cleanup function removes those classes. The router's `mountEl.replaceChildren()` clears innerHTML but does not reset classList. Navigating from any task-detail view to `/auto` leaves `td-page td-page-A` on screen-mount, stacking task-detail's `padding: 14px 18px` on top of `.auto-page`'s `padding: var(--page-pad)` and polluting CSS specificity for subsequent screens.

Fix: add `root.classList.remove('td-page', 'td-page-A', 'td-page-B')` to both task-detail cleanup closures, or — better — have the router reset `className` to `screen-mount` before each mount.

### bug — sessions strip shows nothing, no empty-state, when `/api/auto/sessions` returns `[]`

`renderSessionsStrip` returns early with no DOM output if `sessions.length === 0` (line 11 of sessions-strip.js: `if (!sessions.length) return () => {};`). With no active auto-mode sessions the strip root is empty — no "No sessions yet" message, no hint that auto-mode hasn't run. The `stripRoot` div sits invisibly above the grid with zero height.

### bug — `spine-active-pulse` animation uses `transform: scale()` — motion-on-element violation

`.spine-node--active .spine-node-circle` uses `animation: spine-active-pulse 1.6s ease-in-out infinite` which applies `transform: scale(1.11)`. This is motion on an element (not hover, but continuous), violating the project's no-transform-motion rule. The active node should use a pulsing opacity or stroke-color animation only, not geometric scale.

### inconsistency — Stop uses `window.confirm()`, not a design-system modal

`stopBtn.addEventListener('click', ...)` calls `if (!confirm(`Stop auto-mode session ${sid}?`)) return;`. This is a browser-native blocking dialog — different styling, non-dismissable by keyboard in all browsers, and inconsistent with any modal or inline confirmation pattern used elsewhere. Should use an inline confirmation (e.g., button flips to "Confirm stop?" for 3 seconds) or the app's own modal if one exists.

### inconsistency — Pause fires immediately, Stop has a confirm — asymmetric destructiveness guard

Pause calls the API directly with no confirmation. Stop requires `confirm()`. Both are potentially destructive to a running session, but the UX treats them differently without surfacing why (e.g., pause is reversible; stop is not). The guard difference should be visible — at minimum a tooltip or button subtitle: `Stop (irreversible)` vs `Pause (resumable)`.

### friction — side panels (`auto-left`, `auto-right`) silently empty when no session selected

When no session is active, `refreshSidePanels()` sets `left.innerHTML = ''; right.innerHTML = '';` with no placeholder. The left panel (Subagents · Hook firings) and right panel (Budget · Tool log) render nothing. The three-column grid still takes up space. Result: a wide empty layout with nothing useful to read. Each column should show a lightweight idle state.

### friction — helper note appears on every first visit but is positioned above the sessions strip

The `auto-helper-note` div is `insertBefore(note, root.firstChild)` — it goes above the sessions strip. The note reads "Spine is the live view. Log swaps to chronological waterfall — same data, denser. Use Log when debugging." This is useful orientation, but its position above the strip (which selects which session is in view) means users read the orientation hint before they've even selected a session to look at. The note would make more sense inlined below the sessions strip, just above the main grid.

### friction — sessions strip dot always renders as green/pulsing regardless of session state

`.sstrip-dot` applies `background: var(--green); animation: pulse ...` unconditionally for every session tab. If a session is paused or errored, the tab still shows a green pulse dot. The strip renders `s.status` in the data but doesn't use it to vary dot color. A paused session should show amber; a stopped/errored session should show red or no pulse.

### friction — Pause/Stop buttons lack visible disabled affordance when no session

`pauseBtn` and `stopBtn` receive `aria-disabled="true"` (not `disabled`), with `pointer-events: none; opacity: 0.55`. No tooltip change is visible on hover because `pointer-events: none` suppresses it. Users who hover to understand why buttons are greyed out get no feedback. Either use native `disabled` (which preserves tooltip display via `title`) or add a wrapping element that can still show a tooltip.

### gap — no loading/skeleton state during initial session fetch

On first mount, `refreshSessions()` fires asynchronously. Until it resolves, `stripRoot` is empty and all three grid columns are empty. There's no spinner, skeleton, or even a text placeholder. The page looks broken for the fetch duration (typically <200 ms on local, but perceptible). A single-line placeholder ("Loading sessions…") in stripRoot would suffice.

### gap — Spine/Log toggle state not reflected in page URL

The active view (Spine or Log) is persisted to viewer prefs (`prefs.screens.auto_mode.view`), but it's not reflected in the hash (still `/auto` regardless of view). Sharing or bookmarking a link to the Log view is not possible. Consider `/auto?view=log` as a secondary affordance.

### gap — no breadcrumb or title for the selected session

When a session is selected via the strip, the center column renders the spine/log for that session, but there is no heading or breadcrumb above the center column identifying which session is in view. The session ID is only visible as the active tab in the strip. If the strip is scrolled out of view on short viewports, there's no way to see which session is being inspected.

### polish — `.sstrip-dot` pulse animation fires on all tabs, not just running ones

All session tabs pulse continuously. This makes the strip feel like "everything is active" even when sessions are completed or paused. The pulse should only animate on sessions where `status === 'running'`.

### polish — `aside-h` section headers have no separator between Budget and Tool log

The right panel renders "Budget" and "Tool log" headers using `.aside-h` with `padding-top: 6px`. When both sections have content, the visual boundary between them is only the padding — no border. On dark backgrounds this is fine, but "Subagents" and "Hook firings" in the left panel, and "Budget" and "Tool log" in the right panel, benefit from a thin `border-top: 1px solid var(--border-soft)` on the second `aside-h` to give structural separation.

### polish — `stop` button title changes to "No active auto-mode session" when idle, but label still says "Stop"

When `noSession` is true, `stopBtn.title` is set to `'No active auto-mode session'` but `stopBtn`'s visible label remains "Stop". Assistive technology reads the `aria-label` (which was set at construction to "Stop auto-mode session") not the updated `title`. The `aria-label` should also be updated by `syncRunControls()`.
