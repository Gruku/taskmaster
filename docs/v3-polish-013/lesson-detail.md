# Lesson detail — `/v3/#/lesson/<id>`

> Part of v3-polish-013 end-to-end review. Live runtime walk: L-001 (core, 8 reinforces), L-002 (active, 22d ago), L-004 (retired, 90d), L-006 (active, fenced code in body), L-007 (active, em-dash + inline-code body), and `/lesson/nonexistent`.

### bug — markdown body rendered as plain text (`textContent` + `pre-wrap`)

L-006's body contains a fenced code block:

```
git -C <worktree> checkout -- .fixture-kanban/
rm <worktree>/.fixture-kanban/.taskmaster/auto/sessions/*.events.jsonl
```

It renders with literal triple-backticks visible on screen. L-007's body has `` `--s1` `` `` `--s2` `` `` `--s3` `` `` `--s4` `` and `` `box-shadow` `` — all rendered with literal backticks instead of inline `<code>`. The screen does `body.textContent = lesson.summary` then leans on `white-space: pre-wrap` to preserve newlines. The `marked` library is already loaded in `index.html` for other screens. Use it: `body.innerHTML = marked.parse(lesson.summary)` (or render via the existing `components/markdown.js`). The "rule + Why + How to apply" structure described in the brief depends on this — without markdown, headings inside the body collapse to flat text.

### bug — duplicate `<h1>` on the page

The topbar renders `<h1 id="page-title">Lesson</h1>` and the screen body renders `<h1 class="ld-title">Always read files before editing</h1>`. Both are direct `<h1>` elements inside the same `<main>`. Two `<h1>`s break heading hierarchy for screen readers and `nav-by-headings`. The lesson title should be `<h2>`, or the topbar `<h1>` should be hidden (`aria-hidden`) on detail screens, or the topbar `<h1>` should receive the lesson title.

### bug — topbar title is generic `"Lesson"` for every lesson

`registerScreen('/lesson', ...)` exposes `meta = { title: 'Lesson' }`. The router applies it once: `titleEl.textContent = mod.meta?.title`. There is no per-lesson update — the page `<h1>` always reads "Lesson", regardless of which lesson is open. Browser tab title also stays "Taskmaster" (no per-route update). After loading, the screen should set `document.title` and the topbar `#page-title` to the lesson title (or at least to `Lesson · L-001`).

### bug — `${id}` interpolated unsanitized into `innerHTML` for the not-found path

```js
root.innerHTML = `<div class="ld-empty">Lesson ${id} not found. <a href="#/lessons">Back to Lessons</a>.</div>`;
```

`id` comes straight from `subpath?.[0]` (URL hash). Navigating to `/lesson/<img src=x onerror=alert(1)>` would inject HTML into the page. Same pattern in the catch branch (`Could not load lessons: ${e.message}`). Escape `id` (e.g. set the user-visible label via `textContent`, or run it through a tiny `escapeHtml` helper). Severity is moderate — this is a self-XSS via crafted URL, not a stored XSS — but the unsanitized template should be fixed.

### inconsistency — shelf name appears in three different casings on one screen

For L-004 (retired): the crumb shows "Retired", the meta-pill shows "RETIRED" (CSS `text-transform: uppercase`), and the side-panel "Shelf" `<dd>` shows "retired" (raw lowercase). Three casings of one field on one viewport. Pick one — most other tokens in the app use lowercase data + CSS-uppercase styling, so render the dd with the same `.ld-shelf` styling, or sentence-case everywhere.

### inconsistency — `_fmtRel` uses string-sort and degrades on future / fractional times

`reinforce_events` are sorted with `localeCompare(a.at || '')` — string sort, not date sort. Works for ISO-8601 today, but is fragile. `_fmtRel` itself returns `'now'` for any future date because `ms < 0 → d ≤ 0 → h ≤ 0`, masking a clock-skew or bad data. And the resolution jumps straight from `Nh ago` (under 1 day) to `Nd ago` — there's no minutes/weeks/months bucket, so an event 30 minutes ago shows "0h ago" → falls through to `'now'` (correct), but an event 13 days ago and one 25 days ago both surface as anonymous "Nd ago" with no week/month grouping.

### inconsistency — reinforce button success state weaker on detail than on list card

After clicking "Reinforce", the button gets `is-fired` + `aria-disabled="true"`. Only the generic `.tm-action[aria-disabled="true"] { opacity: 0.5 }` rule applies — the button just dims. The list-card reinforce button has explicit success styling (`.lesson-card__reinforce.is-fired { color: var(--accent-green); border-color: var(--accent-green); }`). On the detail screen, the user gets no positive confirmation — no green tick, no count update next to the button. Add a `.tm-action.is-fired` rule and ideally show the new reinforce-count on the button itself ("Reinforced · 9×").

### friction — events list `note` column overrides remaining width with `1fr`

The grid `80px 80px 1fr` makes the note column consume all remaining space. On L-006's three events, two have notes ("Twice in one M7 run.", "Caught a stale paused:true in fixture.") and one doesn't — the empty row leaves a ~1258px blank rectangle on a 2092px viewport. Either reduce the note column to `minmax(200px, 600px)` or stack the note below `when · src` for cleaner reading at wide viewports.

### friction — events `source` column truncates to 80px

Source column is fixed `80px` — fine for "user" / "claude" — but the `lesson_reinforce` API accepts arbitrary `source: str`. A source like `auto-mode-step` would clip mid-word with no overflow handling. Use `minmax(60px, max-content)`.

### gap — body `pre-wrap` shows source-code line-break artifacts between lessons

`.ld-body` uses `white-space: pre-wrap`. L-001's summary (one sentence, no newlines) wraps naturally. L-007's summary (one ~250-char paragraph, no newlines) also wraps naturally. But any lesson body authored with hard 80-col wrapping in the source markdown will render with awkward mid-sentence line breaks. Once markdown rendering lands (first finding), this goes away.

### gap — no responsive behavior for the 280px side column

`.ld-grid` is hardcoded `minmax(0, 1fr) 280px` with no media queries. On a viewport <600px wide the side column still tries to occupy 280px, leaving the main column ~300px for body content; the events grid (`80px 80px 1fr`) becomes unreadable. Add a single `@media (max-width: 720px) { .ld-grid { grid-template-columns: 1fr; } }`.

### gap — back-link returns to `/lessons` root, losing scroll and shelf context

The crumb `‹ Lessons` is hardcoded to `#/lessons`. The user landed here from a specific shelf card (Core / Active / Retired). Returning drops them at the top of the lessons list, hundreds of pixels above their starting point. The shelf name shown in the crumb (`/ Active`) is a non-clickable `<span>`. Either persist scroll position to prefs (the screen already calls `prefs.patch({ ui: { last_lesson_id: id } })` — extend with `last_lessons_scroll`), or wire the shelf name into a clickable filter link.

### polish — created date uses raw `toLocaleDateString` (locale-dependent)

`_fmtDate` calls `d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })` — output varies by user locale ("Apr 1, 2026" vs "1 avr. 2026"). The dashboard audit already flags raw `toLocaleString`. Standardize via a shared helper across all surfaces.

### polish — `◇` glyph for "pattern" kind is too thin against dark background

L-001 (gotcha → ⚠), L-004 (anti-pattern → ⊘), and the pattern-kind glyph (◇) all render in `var(--ink-2)`. On a dark theme the diamond outline `◇` for "pattern" is so thin it nearly disappears next to the heavier `⚠` and `⊘`. Either use `◆` (filled) or apply a kind-specific accent color (warning amber for gotcha, neutral for pattern, red for anti-pattern).
