# Table — `/v3/#/table`

> Part of v3-polish-013 end-to-end review. Captured against a live, healthy server with 29 tasks loaded.

---

### bug — `Title` column expands to ~925 px on wide viewports, leaving the right side of the table sparsely populated

The grid template is `110px minmax(220px, 1fr) 110px 90px 90px 140px 60px 180px 110px`. With a 2092 px viewport and an 840 px sum of fixed columns, the title's `1fr` claims everything left over (~925 px measured). The result: long titles never wrap or truncate (they don't need to — they fit in 925 px), but the page reads as a thin strip of metadata followed by acres of empty white space inside the title cell. Cap title to a sensible `minmax(220px, 600px)` or split overflow across multiple text columns once the title fits.

### bug — title cell truncation has no tooltip / `title` attribute

`text-overflow: ellipsis` + `white-space: nowrap` is set on every cell (`.tbl-cell`). On smaller viewports (or once the title column is bounded — see above), titles will silently truncate with ellipsis. The cell has no `title=""` attribute, so the truncated text is unrecoverable without resizing the window or drilling in. Branch column hits the same issue (`feat/graph-readability` is 182 px in a 180 px cell — already truncated by 2 px today). Add `title="${task.title}"` (and same for branch) at minimum.

### bug — column header role downgraded to generic in the accessibility tree

`<th>` cells render text inside `<span class="tbl-th-label">` but the parent `<table class="tbl">` uses `display: grid` and the `<thead>/<tbody>/<tr>` use `display: contents`. Browsers strip the implicit `columnheader`/`row`/`rowgroup` semantics when these display modes are applied — confirmed by `read_page`: every `<th>` shows as `generic`, not `columnheader`. Screen readers will not announce "column 3 of 9, Status, sort ascending." Add explicit `role="table" / "row" / "columnheader" / "cell"` ARIA roles, or restructure to keep native table semantics.

### inconsistency — chip rail labels duplicate three column headers verbatim

Chip-rail labels (`STATUS`, `PRIORITY`, `EPIC`) render in the same uppercase mono micro-style as the column headers (`STATUS`, `PRIORITY`, `EPIC`). The eye lands on six identical-looking labels stacked vertically. Either differentiate the chip-rail-label typography (e.g., title-case "Filter by status:") or drop the chip-rail labels entirely — the chip values themselves are self-describing.

### inconsistency — Started column renders `YYYY-MM-DD`, dashboard renders `MM/DD/YYYY, h:mm:ss AM/PM`, other surfaces use relative time

`formatDate()` returns `d.toISOString().slice(0, 10)` → `2026-04-26`. The dashboard auto-strip and other timestamp surfaces use `toLocaleString()` and relative-time helpers. Three formats across three screens. Normalize on a shared `formatTaskDate()` (e.g., `Apr 26` for in-quarter, `Apr 26 '25` otherwise; or `2d ago`).

### gap — no ArrowUp/ArrowDown keyboard nav between rows

Rows have `tabIndex=0` and respond to `Enter`/`Space` (drill-in) but ignore `ArrowDown` / `ArrowUp` / `Home` / `End`. Verified live: ArrowDown on a focused row keeps focus pinned to that row. The natural data-grid pattern is Tab to enter the table, arrows to navigate rows, Enter to drill, Esc to leave. As-is, keyboard users must Tab through every cell and intermediate UI between rows.

### gap — no visible focus ring on the `+ Task` topbar button or chip toggles

`tbl-row:focus-visible .tbl-cell { background: rgba(74,158,255,0.08) }` covers row focus, but neither the topbar `+ Task` button nor the filter chips define a `:focus-visible` style, so they fall back to the browser default outline (often suppressed by global resets). Test by Tab-ing through the topbar — the button receives focus but is visually indistinguishable from hover.

### gap — chip toggles missing `aria-pressed`

`.tbl-chip` is a `<button type="button">` with `is-active` toggled in JS, but no `aria-pressed="true|false"` is set. Screen-reader users can hear "Todo, button" but won't know whether Todo is currently part of the active filter. Add `aria-pressed` when toggling `is-active`.

### gap — non-sortable column header (`Branch`) is visually indistinguishable from sortable ones

The Branch `<th>` lacks `is-sortable` and gets `cursor: auto`, but its color, weight, casing, and padding match the eight sortable columns. Users who try to click it get no feedback (no cursor change visible until hover, no hover-color change on non-sortable). Differentiate non-sortable headers — e.g., dim the label slightly, or move them to a tooltip-able icon column.

### friction — active sort header color is identical to inactive headers

`.tbl-th.is-active { color: var(--ink) }` resolves to `rgb(230, 231, 235)` — and inactive `.tbl-th` resolves to the same `rgb(230, 231, 235)` (verified live). Only the small `↑`/`↓` arrow communicates which column is sorted. The arrow is rendered in the sans-serif font while the header label is mono, so visually they look like two separate UI elements rather than a unified "sorted column" treatment. Either bump the active label's weight/color or change the inactive headers' color to a dimmer ink tier so the active one stands out.

### friction — `× Clear` button uses U+00D7 multiplication sign and floats far right

The chip-rail clear button text is `× Clear` (the leading character is U+00D7 MULTIPLICATION SIGN, not the close glyph U+2715). It's positioned with `margin-left: auto` so it shoots to the right edge of the chip rail, away from the chips a user just toggled on the left. Two friction points: wrong glyph and disconnected placement. Replace with a properly-sized `✕` or drop the glyph entirely; place it inline next to the last active chip group.

### polish — Phase column shows raw codes (`P-01`, `P-02`, …) with no human label

The Phase column renders `t.phase || '—'` directly. There's no mapping from `P-01` → `Discovery` / `Foundations` / etc. (those phase labels do exist in the dashboard's phase pip). Either resolve `P-NN` to the friendly phase name, or render both (e.g., `P-03 Foundations`).

### polish — Priority badge styling is inconsistent across values

Of the four priority levels, two (`Critical`, `High`) get tinted `background` pills, and two (`Medium`, `Low`) get transparent backgrounds — they appear as bare uppercase text rather than badges. Either give all four badges a tinted background tier, or strip the background entirely from `Critical`/`High` and rely on text color alone for consistency.

### polish — row hover is effectively invisible

`.tbl-row:hover .tbl-cell { background: rgba(255,255,255,0.024) }` — that's about 2.4% opacity, on top of an already-light card surface. The hover BG is below the just-noticeable-difference threshold on most monitors. Bump to `0.06` or `0.08` so users can see which row they're about to click. Cursor is correctly set to `pointer`, but visual feedback is the missing half.

### polish — sort arrow font family mismatches the column label

Active column reads `STATUS ↑` where `STATUS` is `font-family: var(--font-mono)` and the arrow `↑` is `font-family: var(--font-sans)` (Inter). The arrow looks slightly off-grid, especially at 11 px. Either bring the arrow into the mono font, or replace with a CSS chevron pseudo-element so it inherits the same metrics as the label.
