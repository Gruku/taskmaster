# Issue detail — `/v3/#/issue/<id>`

> Part of v3-polish-013 end-to-end review. Audited live against commit 4ef424d (B1c) at `#/issue/ISS-001`, `ISS-005`, `ISS-007` (open/investigating, with location), and `ISS-003` (resolved/Fixed). The screen renders correctly and the user-facing layout matches the spec. Findings below are real defects observed against the running viewer.

---

### bug — severity label color never resolves on detail screen (`--sev-*` vars unscoped)

The header text "Critical" / "High" / "Medium" / "Low" next to the glyph renders gray (`rgb(124, 130, 144)` — the inherited `--ink` value) instead of the matching severity color, on every issue. The CSS rule `.id-sev[data-sev="High"] { color: var(--sev-high); }` exists, but the `--sev-critical` / `--sev-high` / `--sev-medium` / `--sev-low` custom properties are defined inside `.issues { ... }` (issues.css line 9–12) — that scope class lives on the issues list screen and is **not** applied to `.issue-detail`. So `var(--sev-high)` resolves to the empty fallback. The glyph itself is fine because `severity-glyph.js` hardcodes the colors inline, but the text label is a flat gray on every severity, which kills the prominence the design intended.

Verified live: `getComputedStyle('.id-sev').color` is identical (`rgb(124,130,144)`) on Critical, High, and Medium issues.

Fix: hoist the four `--sev-*` vars to `:root` (or to `.issue-detail` plus `.issues`).

---

### bug — aging bar fill is always 0% (driven off missing `discovered` field)

Every issue in the side panel shows `aging-bar--fresh` with `fill width: 0%` regardless of the issue's age. The aging chip says "Fresh" even on `ISS-003` which has been around for 22 days and was resolved 7 days ago. Root cause: `aging-bar.js` does `if (!issue.discovered) return { percent: 0, tier: 'Fresh' };` — but the issues fixture (and the `/api/issues` response) does not include a `discovered` field. The server-side already computes `aging: { percent: 0, tier: 'Fresh' }` and ships it on the issue payload, but the client recomputes from the missing `discovered` field and ignores the server's value. Net effect: the aging bar is decorative-only — every issue is "Fresh, 0%" and the visual track is empty.

The same defect affects the issues list cards, but it's most embarrassing on the detail page where the "Signals" panel is supposed to be the primary signal surface.

Fix: either include `discovered` in the fixture/API, or have `agingBar()` consume the server-precomputed `issue.aging` when present.

---

### bug — self-XSS via unsanitized `${id}` in the not-found template

`issue-detail.js:61` builds the not-found state with a template literal:

```js
root.innerHTML = `<div class="id-empty">Issue ${id} not found. <a href="#/issues">Back to Issues</a>.</div>`;
```

`id` comes from `subpath?.[0] || params?.id`. The router decodes `params` via `decodeURIComponent`. Setting the hash to `#/issue?id=<img src=x onerror=...>` decodes to the literal `<img>` tag and `innerHTML=` injects it into the DOM — confirmed live: `document.querySelectorAll('img').length` returns 1 inside the empty state, and the `<img>` tag is fully parsed (the `onerror` did not fire here only because the chrome sandbox doesn't load the failed `src`, but the unsanitized HTML rendering is the bug). Same class of bug flagged in the heads-up for other detail screens.

Fix: use `textContent` for the user-supplied id, or escape via the same util the impact section uses.

---

### bug — resolved-shelf rows are not navigable (no role, no handler)

`.issue-row` (used for the Fixed / Wontfix shelf at the bottom of the issues list) renders as a plain `<div>` with no `role`, no `tabindex`, no click handler, and `cursor: auto`. Clicking does nothing. Verified live: `r.click()` left `location.hash` unchanged at `#/issues`. By contrast `.issue-card` correctly has `role=link`, `tabindex=0`, `cursor=pointer`, and Enter/Space handlers. The B1c commit message explicitly says "Cards now drill into a full-screen detail view" — the resolved shelf was missed, so resolved issues cannot be drilled into.

This is a card-side defect, but it directly blocks reaching `/issue/:id` for any resolved issue.

---

### bug — detail page never captions the issue in the topbar

`claimTopbar()` is called with no arguments, so the topbar `<h1>` is the static `meta.title = 'Issue'` and the browser tab `<title>` stays at the global `Taskmaster`. With multiple tabs open or when scrolling the title out of view, the user has no way to identify which issue they're on from the chrome alone. Pass the issue ID + truncated title to `claimTopbar` (or set `document.title`).

---

### inconsistency — location strip is plain text on detail, console-style on cards

The card view renders `.issue-card__location` with a deep background, mono font, inset shadow, and **blue line numbers** (`.issue-card__location-num`). The detail view renders the same data as plain `.id-location` — no background, no inset, no colored line numbers. Verified live: `LOC_CSS bg=rgba(0,0,0,0) p=0px br=0px` and `LOC_NUM_SPANS=0` on the detail page. Drilling into a card causes the location signal to **lose** styling, which inverts the usual hierarchy (detail should be richer, not poorer, than the summary card). Reuse the card's `_renderLocation` helper here.

---

### inconsistency — symptom uses two different visual languages (card has a left rail, detail does not)

On the issues list, `.issue-card__symptom` has a 2px solid left border (`border-left: 2px solid var(--ink-3)` — confirmed live) intended as a "decorative quote mark". On the detail page, the same content (`.id-symptom .id-body--italic`) renders with no left border at all. Either both surfaces should match, or — preferably given the global "no colored/structural left rails on cards" guideline — the card should drop the left rail and align with the detail's clean italic-serif treatment. As-is the two surfaces don't read like the same field.

---

### inconsistency — date format mixes absolute (`since Apr 20, 2026`) and relative (`12d ago`) for the same `created` field

The header reads `since Apr 20, 2026` (absolute, via `_fmtDate` with `toLocaleDateString(undefined, …)`). The "Signals" DL three lines below shows `Created · 12d ago` (relative, via `_fmtRel`). Same data, two formats, on the same screen. Pick one role per location: header gets one form, side panel gets the complement, but they should be deliberately different facets, not duplicated facets in different formats. Also: passing `undefined` as the locale to `toLocaleDateString` makes the format machine-locale-dependent.

---

### gap — no "blocks" chip on the detail header even though the card has one

`issue-card.js` renders a `⊘ blocks N` chip in the card head when `computeBlocksCount(issue, tasksIndex) > 0`. The detail screen has nothing equivalent — verified live: querying any `[class*=block]` on `/issue/<id>` matches only the side `.id-side-block` panel containers. The "related blocks chip" called out in the polish task spec is missing from the screen it most needs to be on. It belongs in the header meta row (next to status pill) or as a top item in the Signals block, with a count and clickable drilldown to the blocked tasks.

---

### gap — no loading state during the cold-fetch path

When `store.getIssues()` is empty (cold load directly into `/issue/:id`), `mount()` does `root.innerHTML = ''` and then awaits `api.getIssues(...)`. There is no spinner, skeleton, or "Loading…" string during the round-trip. On a slow connection it's a blank panel for 0.5–2 s. Add a placeholder consistent with task-detail.

---

### gap — not-found state is unframed (no crumb, no header chrome)

The not-found state renders `<div class="id-empty">Issue X not found. <a>…</a></div>` and nothing else: no breadcrumb, no `.id-head` shell, just plain text on a blank panel. Verified live: `root.querySelector('.id-head')` is null. Mirror the not-found pattern used on other detail screens, or at minimum render the crumb (`‹ Issues / Not found`) so the user has a consistent way back without scanning for a small underlined link.

---

### polish — crumb separator has no horizontal padding

The crumb renders as `‹ Issues / Investigating` with `gap: 8px` on the parent flex, but the `.id-crumb-sep` itself has zero margin/padding — `textContent` reads as `"‹ Issues/Investigating"` (no spaces around the `/`). Visually the gap saves it, but it's fragile: any change to the gap value collapses the separator entirely. Add `padding: 0 4px` to `.id-crumb-sep`, or switch to the `›` chevron pattern used elsewhere for visual consistency across crumbs.

---

### polish — DL values are all monospace, including human-readable status & relative-time

`.id-dl dd { font-family: 'JetBrains Mono', ui-monospace, monospace; }` puts `High`, `Investigating`, and `12d ago` in monospace. Mono is appropriate for IDs, file paths, and code; not for severity labels and natural-language relative times. Keep mono only for the ID-bearing rows; switch the rest to the body font.

---

### polish — focus-visible rule missing on `.id-back` link

Computed style on the breadcrumb back link shows `outline-style: none`. There is no `:focus-visible` rule for `.id-back` in `issues.css` — only `.id-back:hover { color: var(--ink); }`. Verified live: focusing the back link via `.focus()` yields only the user-agent default ring, which is invisible against the dark surface. Add `.id-back:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; }`.

---

### polish — curly apostrophe in `'Won't fix'` label

`issue-detail.js:13` defines `wontfix: 'Won't fix'` — the apostrophe is U+2019 RIGHT SINGLE QUOTATION MARK. Syntactically valid (curly quotes are not JS string terminators), but it's the only string in the file with a typographic quote. Standardize to straight `'` for consistency with the rest of the codebase, or commit to typographic quotes site-wide.
