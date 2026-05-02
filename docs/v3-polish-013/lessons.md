# Lessons — `/v3/#/lessons`

> Part of v3-polish-013 end-to-end review. Findings feed **v3-polish-010** (lessons screen usability refresh). Concept reference: `.superpowers/brainstorm/16245-1777231623/content/lessons-shelves.html`.

---

### gap — "Scope:" filter row is missing counts on category chips

The concept (`lessons-shelves.html`) shows a `filters-row` with an explicit `Scope:` label and chips that carry per-category lesson counts (e.g., "CSS / styling `6`", "Git / branches `4`"). The live implementation renders `lessons__cat-chip` elements dynamically from lesson categories but omits both the `Scope:` label and the count badge on each chip. Without the count, there's no signal about how many lessons live under each category before filtering — you have to click to find out.

**Fix:** Add a `Scope:` leading label element to `.lessons__cats` and append a monospace count badge (e.g., `<span class="cat-chip__ct">6</span>`) to each chip, matching the concept's `filter-chip .ct` pattern.

---

### inconsistency — category chip active state uses wrong class name

The CSS selector targeting the active chip is `.lessons__cat-chip.is-active`, and the JS correctly sets `chip.classList.add('is-active')`. However the chip has no `role`, no `aria-selected`, and `tabIndex` defaults to -1 (not keyboard-focusable). The concept shows these chips as interactive filter controls. Chips are not keyboard-reachable and expose no accessible selected state.

**Fix:** Add `role="button"` (or `role="radio"` within a `role="radiogroup"`) and `tabindex="0"` to each chip; toggle `aria-pressed` or `aria-checked` on click. This is core usability, not just polish.

---

### bug — card `role="link"` on `<article>` does not navigate on keyboard Enter in assistive technology

`lesson-card.js` sets `role="link"` on an `<article>` element with a `keydown` listener for Enter/Space. While the keydown listener works in Chrome without AT, screen readers expect a real `<a>` for link semantics — `role="link"` on a non-anchor does not activate on Enter in all AT/browser combos, and the card is missing an `href` so it won't appear in link-lists. The concept used `cursor: pointer` styling alone with click semantics, not explicit link role.

**Fix:** Either wrap the card in an `<a href="#/lesson/L-001">` (preferred, makes the card a true link), or at minimum ensure the `role="link"` element has `tabindex="0"` (it already does) and test under NVDA/VoiceOver. The current `<article role="link">` pattern is fragile.

---

### gap — sparkline terminal dot missing vs. concept

The concept sparkline uses `fill="none" stroke="#d6b85f"` polyline with a terminal `<circle>` endpoint dot, giving a clear "latest point" visual anchor. The live sparkline (`sparkline.js`) renders a filled polyline with no terminal dot. The filled polyline shape is harder to read for trend direction, and the "most recent" point is visually unmarked.

**Fix (v3-polish-010):** Add a terminal `<circle>` at the last data point to the sparkline component output, matching the concept. Switch polyline `fill` to `none` with a stroke to improve readability at small sizes.

---

### friction — "New lesson" button shows no disabled visual state despite `aria-disabled`

`lessons.js` sets `aria-disabled="true"` on the "+ Lesson" button but does not add a visual disabled style. The button renders at full opacity with a primary style, implying it is clickable. The title tooltip says "New lesson — coming soon" but this only appears on hover. For a sighted user the button looks active.

**Fix:** Add `.tm-action[aria-disabled="true"] { opacity: 0.4; cursor: not-allowed; pointer-events: none; }` or equivalent in the shared topbar stylesheet. Alternatively remove the button entirely until the feature exists.

---

### inconsistency — shelf headers are `<span>` inside `<header>`, no heading hierarchy

`renderShelves()` creates shelf sections with `<header class="lessons-shelf__header">` containing three `<span>` elements (name, tagline, count). None are `<h2>/<h3>` elements. Navigation by headings skips all shelf names — the page has one implicit heading ("Lessons" in the topbar `<h1>`) and zero sub-headings for Core / Active / Retired.

**Fix:** Promote `.lessons-shelf__name` to an `<h2>` (or `<h3>` if the page-level heading is `<h2>`). The CSS can retain the serif italic style on `h2.lessons-shelf__name` without change.

---

### friction — anchor passive dot-meter shows "0 matches · 7d" on every card

All 9 lesson cards render `0 anchor matches in last 7 days`. While this may be accurate for the current fixture data, the dot-meter dots are all unlit and the caption reads identical on every card. This makes the `anchor_matches_7d` signal look broken or always-zero rather than genuinely informative. The concept showed active sparklines implying regular reinforcement activity.

**Fix (data):** Populate `anchor_matches_7d` on fixture lessons with realistic values so the feature can be evaluated. As a display improvement, suppress the dot-meter entirely (or show a faint dash) when the value is 0 rather than rendering `0 matches · 7d` repeatedly.

---

### polish — "× fired" label reads ambiguously for zero-count lessons

Cards with `reinforce_count = 0` render `0× fired`. The `×` symbol reads as a multiplication sign, making "0× fired" feel like "zero times fired" which is clear to developers but odd to less technical users — it could be read as a delete/close action. The concept used the same pattern, but it was always rendered with counts ≥ 1.

**Fix:** When `reinforce_count === 0`, render "never fired" or omit the stat entirely rather than "0× fired".

---

### inconsistency — "By Anchor" view uses `<code>` for shelf name, others use `<span>`

`renderByAnchor()` creates shelf name elements as `<code class="lessons-shelf__name">` (appropriate for glob patterns), while `renderShelves()` and `renderFlat()` use `<span class="lessons-shelf__name">`. This is intentional for the glob pattern content, but `.lessons-shelf__name` inherits a serif-italic font from the CSS, so the `<code>` element fights two font-family overrides (its own monospace default vs. the CSS `font-family: Source Serif Pro`). The glob patterns should always render in `JetBrains Mono`, not a serif italic.

**Fix:** In the CSS, add `.lessons-shelf__header code.lessons-shelf__name { font-family: 'JetBrains Mono', ui-monospace, monospace; font-style: normal; }` so anchor-view shelf headers inherit the correct mono font.

---

### polish — search placeholder text differs between concept and live

Concept search input: `"Search lessons & anchors…"` — live: `"Search lessons…"`. The concept's placeholder communicates that anchor glob patterns are also searchable, which is true (`_matchesSearch` includes `triggers.files` and `triggers.symbols`). The live placeholder understates the capability.

**Fix:** Update placeholder to `"Search lessons & anchors…"` to match the concept and accurately describe the search scope.

---

### gap — no empty state when search or category filter yields zero results

`renderShelves()` / `renderFlat()` / `renderByAnchor()` render empty shelf grids (zero cards) with no empty-state message when filters produce no matches. An empty shelf `<section>` with only a header and an empty grid is confusing — it looks like data failed to load.

**Fix:** When a shelf contains zero items after filtering and a filter is active (`filterActive === true`), either hide the shelf entirely or render an empty-state message ("No Core lessons match your filter") inside the grid.

---

### inconsistency — retired shelf tagline delta vs. concept reference

Live tagline: `"No reinforcement in 30+ days. Click to revive."` Concept: `"Kept for context. Click to revive."` The live tagline surfaces the business rule (30+ days) which is more informative. The concept's was a placeholder. This is resolved in the right direction and needs no change — logging for completeness.

---

### accessibility — reinforce button overlaps card focus outline

`.lesson-card__reinforce` is `position: absolute; right: 10px; bottom: 10px` and appears on hover with `opacity: 0 → 1`. The card's `focus-visible` outline is `outline-offset: 2px`. When a card is keyboard-focused, the reinforce button is invisible (opacity 0 at non-hover), so keyboard users cannot discover or trigger the reinforce action via the hover-only control. The button is not in tab order (it inherits `tabindex` from `<button>` which is 0, but since the card uses a click handler for navigation, pressing Enter navigates rather than reinforcing).

**Fix:** Expose the reinforce button as a separately focusable, keyboard-accessible element. One approach: add `tabindex="0"` explicitly and make the reinforce button visible (not opacity-0) when its nearest ancestor card has `:focus-within`.
