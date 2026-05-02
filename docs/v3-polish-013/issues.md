# Issues — `/v3/#/issues`

> Part of v3-polish-013 end-to-end review. This screen is the primary target of v3-polish-009 ("Issues screen — align with design concept"). Findings below are organized to directly drive that task. Concept references are the `issues-hybrid.html` and `issues-v2-bugreport.html` files in `.superpowers/brainstorm/16245-1777231623/content/`.

---

### bug — blocks chip never renders (store.getTasksIndex missing)

`issues.js:176` calls `store.getTasksIndex ? store.getTasksIndex() : {}`. The `store.js` module does not export `getTasksIndex` — the method does not exist. The guard silently falls back to an empty object, so `computeBlocksCount` always returns `0` and the `⊘ blocks N` chip is never appended to any card.

The concept (`issues-hybrid.html`, `issues-v2-bugreport.html`) shows a prominent `⊘ blocks 1` / `⊘ blocks 2` chip in the card head for issues that block active tasks. This chip is a key affordance for triage priority. Currently dead code.

**Fix:** Either add a `getTasksIndex()` getter to the store (built from `store.getBacklog()`) or look up blocking count from the issue's `related_tasks` fields against the backlog directly.

---

### bug — "Kanban" view toggle is a no-op

The segmented control offers three views: Hybrid · Kanban · List. `render()` only branches on `currentView === 'C'` (List); view B ("Kanban") falls through to the `else` and renders identically to Hybrid. Clicking "Kanban" changes the toggle's visual state (aria-pressed fires) but the layout is unchanged. This is a visible lie to the user.

The concept shows Hybrid and Kanban as meaningfully different layouts. Until view B is implemented, the toggle should expose only Hybrid and List, or the Kanban option should be labeled "coming soon" in the same style as the "New issue" button.

---

### bug — "1 issues" pluralization error in resolved shelf header

`resolvedHeader.innerHTML` is hardcoded as `Resolved · ${resolved.length} issues`. When exactly one resolved issue exists, the header reads "Resolved · 1 issues". Needs a simple ternary: `${resolved.length} ${resolved.length === 1 ? 'issue' : 'issues'}`.

Observed on the live page: the resolved section shows "Resolved · 1 issues".

---

### inconsistency — concept-delta: severity filter chips missing count badges and dot indicators

Concept (`issues-hybrid.html`, `issues-v2-bugreport.html`) severity filter chips include:
- A small colored dot indicator per severity level.
- A monospace count badge showing how many issues match (e.g., `Critical · 1`, `High · 2`).
- An "All" chip that resets all filters.

The live implementation renders plain text chips (`Critical`, `High`, `Medium`, `Low`) with no dots, no counts, and no "All" toggle — only the color-per-severity is present via CSS `data-sev` attribute. The count feedback (how many issues per severity) is absent, making the filter less informative than the concept.

---

### inconsistency — concept-delta: column headers use wrong typographic treatment

The concept uses `font-family: 'Source Serif Pro', serif; font-style: italic; font-size: 14px` for column headers ("Investigating", "Open") with a subdued tagline ("— actively under triage", "— confirmed, not yet started") and a mono count.

The live implementation renders column headers as plain uppercase text (`font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px`) with no tagline and no item count next to the header. The serif-italic column name and the tagline are defining aesthetic elements from the concept; their absence makes the columns feel generic.

---

### inconsistency — concept-delta: aging bar not rendering on cards

The concept (`issues-v2-bugreport.html`) shows an aging bar on every live issue card — a thin horizontal track (3–4px) color-coded by tier (Fresh/Aging/Stale) with a labeled tier chip.

In `issue-card.js:119`, the aging bar is only appended `if (agingCfg)`. The `agingCfg` comes from `store.getPrefs()?.issues?.aging`. The viewer prefs (`viewer.json`) do define aging config (`Critical: 14, High: 30, Medium: 60, Low: 120`). However, if prefs haven't loaded or the server returns a prefs object that omits the `issues.aging` key, `agingCfg` will be `{}` — an empty object — which is truthy but `Object.keys({}).length === 0`. The `agingBar` component will still be called and will emit a "Fresh" bar for every issue (because `computeAgingTier` defaults to `{ percent: 0, tier: 'Fresh' }` when `issue.discovered` is absent).

The live page shows "Fresh" chips on all cards in the accessibility tree — confirming the bar renders but always shows Fresh. Two root issues: (1) issues use a `created` field, not `discovered`, so `computeAgingTier` can't find the date; (2) the bar shows "Fresh" when the date is missing rather than hiding.

**Delta from concept:** The concept shows a meaningful progression (green → amber → red). The implementation shows a static green "Fresh" bar on everything — a meaningless decoration.

---

### inconsistency — concept-delta: resolved shelf uses list layout instead of 2-column grid

Concept shows resolved issues in a 2-column grid (`grid-template-columns: 1fr 1fr`) for space efficiency.

The live implementation uses `.issues__resolved-list { display: flex; flex-direction: column; gap: 4px; }` — a single-column list. With many resolved issues this wastes vertical space significantly.

---

### friction — severity filter chips have no ARIA role or pressed state

The severity chips (`<span class="issues__sev-chip" data-sev="...">`) and component chips (`<span class="issues__comp-chip">`) have click listeners and `is-active` class toggling but no `role="button"` and no `aria-pressed` attribute. Screen readers cannot discover or operate these filters. The segmented view toggle (via `tmSegmented`) correctly uses `aria-pressed` — chips should be consistent.

---

### friction — filter chip inactive state has no visual feedback for "none selected = show all"

When no severity chip is active, all issues show — but there is no "All" chip or visual indicator of the all-pass state. Activating one chip filters to that severity with no way to see the total count. Users don't know how many hidden issues exist. The concept explicitly has an "All · 7" chip as a reset affordance.

---

### friction — component chips appear after severity chips in topbar with no visual separator

The topbar renders: issue count → search → severity chips → component chips → view toggle → new button. The two chip rows are visually indistinct — no label ("Severity:" or "Component:") and no separator. The concept uses `sev-label` spans ("Severity:" in small caps, "Component:" in small caps) to delineate the groups. Without these, a user scanning the topbar cannot tell where severity filtering ends and component filtering begins.

---

### friction — "New issue — coming soon" button uses aria-disabled but not disabled

The new-issue button has `aria-disabled="true"` but is a `<button>` without `disabled`. Keyboard focus will land on it and `Enter` will fire the `onClick` handler (which is currently undefined — no crash, but semantically wrong). Should be `disabled` or the click should be intercepted and produce a toast: "Issue creation coming soon."

---

### gap — column headers not marked as heading elements

`issues__column-header` is a `<header>` element inside each `.issues__column` section. The HTML `<header>` element with no `role` override defaults to `banner` role when it's a top-level element, but inside a sectioning element it should take no landmark role. In the accessibility tree these appear as `banner "Investigating"` and `banner "Open"` — two banner landmarks on the same screen causes navigation confusion for screen reader users. They should be `<h2>` or `<h3>` elements.

---

### gap — issue cards have role="link" but use hash navigation via JS, not an `<a>` tag

`issue-card.js:35` sets `role="link"` on the `<article>` element and uses `location.hash = ...` on click/Enter. A proper `<a href="#/issue/ISS-XXX">` wrapping the card (or at minimum the title) would give real link semantics, enable right-click → open in new tab, and remove the need for the manual keydown handler.

---

### gap — empty state missing: no issues, no search results

No empty-state element exists for either "no open issues" or "no search results match." If search filters reduce the list to zero cards, the columns render as empty boxes with only the "Investigating" / "Open" headers. The accessibility tree had no empty-state node. The concept doesn't address this case either, but the live screen needs it.

---

### gap — Kanban view (view B) has no implementation placeholder comment

The `render()` function branches on `currentView === 'C'` but has no comment or TODO for view B. Future contributors have no indication that B is intentionally unimplemented vs accidentally unreachable. At minimum a `// TODO view B: full kanban layout` comment is needed.

---

### polish — resolved shelf header caret doesn't have aria-expanded

The resolved shelf toggle (`resolvedHeader.addEventListener('click', ...)`) toggles `resolvedList.hidden` and updates `.caret` textContent between `▾` and `▴`. There is no `aria-expanded` attribute on the header, so the open/closed state is invisible to screen readers.

---

### polish — investigating pulse animation uses `::before` pseudo-element, concept uses inline span

The live implementation uses `.issue-card__investigating::before { ... animation: pulse ... }` for the animated dot. The concept uses an explicit `<span class="pulse">` inside the investigating tag, which is more controllable (can be paused via reduced-motion per element) and avoids the generated-content-animation quirk that caused the Firefox stutter noted in ISS-004. Consistent with moving to the explicit span approach.

---

### polish — location rendering: multiple files smash together with no newline or separator

`_renderLocation()` appends all location items inline with two trailing spaces as separator: `backlog_server.py:412  viewer/js/...:201`. On multi-file issues this reads as one long monospace line. The concept shows only one location per card in the stacktrace chip. Consider truncating to the first location with a `+N more` disclosure if multiple exist.

---

### accessibility — `.issues__column-header` uppercasing via CSS obscures screen reader pronunciation

`text-transform: uppercase` on column headers means `Investigating` renders visually as `INVESTIGATING` but the DOM text is `Investigating` — which is fine for screen readers. However, the `letter-spacing: 0.5px` and uppercase combination can make the visual label harder to scan at small font sizes (12px). Consider 11px with `0.06em` spacing to match other screen headers.
