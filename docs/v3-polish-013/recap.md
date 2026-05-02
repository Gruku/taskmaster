# Recap — `/v3/#/recap`

> Part of v3-polish-013 end-to-end review. **D·5 Recap picker rebuild (v3-polish-001)** is queued — no picker concept HTML exists yet. Findings below are direct inputs to that brainstorm. Validated against live runtime with three fixture recaps (SES-0001 → SES-0003).

---

### bug — topbar arrow navigation is silently broken

Clicking `‹ Previous recap` updates the URL hash to `#/recap/SES-0002` but the page **does not change** — it stays on the most-recent recap (SES-0003). Same for `Next recap`. The user sees the URL flicker but the content never advances. Reproduced cleanly: starting on `#/recap/SES-0001` (which works only because it was reached via `?id=` style), clicking `Next recap` produced URL `#/recap/SES-0002` but content snapped back to SES-0003.

Root cause: `recap.js` reads `params.id` (query-string) but `‹/›` arrows produce path-segment URLs (`#/recap/${prev.id}`). The router routes the path-segment portion into `subpath`, not `params`, so `params.id` is undefined → falls back to `recapSessions[0]` (most recent). Result: **the only navigation control on the screen is non-functional**. Picker rebuild must fix this routing contract.

### bug — there is no picker; only one arrow renders

Live state on `/v3/#/recap` (default landing): the topbar shows a single `‹` button labelled "Previous recap" and nothing else navigation-related. No pill, no dropdown, no calendar, no list, no date input, no jump field. The brainstorm concept (`.superpowers/brainstorm/.../recap-detail.html`) shows a prominent topbar pill (`SES-0184 · Viewer redesign · 2026-04-26 · 3h 14m ▾`) flanked by `‹/›` arrows — none of that exists in the implementation.

When `next == null` (most recent recap), the `›` arrow simply isn't rendered, so the disabled-state convention isn't followed — the user can't tell whether they're at the head, the tail, or the middle of the recap series.

**Picker-rebuild relevance (concrete inputs for v3-polish-001 brainstorm):**

1. **No identity anchor.** The currently-loaded recap's date is *invisible* anywhere in the topbar or page-title. The page `<h1>` is the literal string `"Recap"` (constant; never updates with the loaded session). The hero meta shows `SES-0003 · claude · 0s · vs SNAP-0002` — session ID and snapshot ID, but no human-readable date. A date-pickable view that never shows the date is the core defect.
2. **Linear-only, no jump.** With only `‹/›` arrows, reaching session 1 from session 30 requires 29 clicks. No way to jump to a specific date or session.
3. **Directionality is ambiguous.** `‹ Previous recap` goes *older* (recapSessions[idx+1]); `› Next recap` goes *newer* (recapSessions[idx-1]). Without a date anchor, the user cannot reason about which direction is forward in time.
4. **No density/gap legibility.** With three sessions today, this is invisible. With 30 sessions across 60 days (some daily, some weekly gaps), the user has no signal of where the long gaps are.
5. **Picker shape tradeoffs:**
   - **Calendar grid:** Best when the user thinks "the recap from last Tuesday." Makes weekend/break gaps obvious. Risk: recaps aren't daily — multiple per day collapse, sparse weeks look broken.
   - **Timeline/spine:** Matches the Sessions-screen diary metaphor. Density and gaps are visually obvious. Lets bar height encode duration. Risk: needs scroll-to-active on mount; needs a day-grouping label so the user doesn't lose where they are.
   - **Flat list / dropdown:** Lowest complexity, fine when count <30. Falls apart at scale. Could pair with `‹/›` arrows as keyboard nav and gain a search-by-title field.

### bug — receipts always show "No changes" while narrative describes substantial work

On all three fixture recaps (live confirmed on SES-0003), the receipts grid shows **all four cards empty**: `Tasks 0 · No changes`, `Files touched 0 · No changes`, `Lessons fired 0 · No changes`, `Issues 0 · No changes`. The narrative meanwhile reads "Plan 3 landed in 6 milestones over 2 days... Closed v3-023 plus all of Plan 3's M1–M6 tasks." This is a real data wiring issue, not just empty fixtures: `getSnapshotDiff(snapshot_before, snapshot_after)` is returning empty arrays even when both snapshot IDs exist on a recap that clearly involved many task transitions. Either the diff endpoint is buggy or the snapshot IDs in the fixture don't resolve to real snapshot files. Whichever it is, the screen has no tell — user sees a recap that contradicts itself.

### bug — page-title `<h1>` never updates from the literal string "Recap"

The topbar `<h1 id="page-title">Recap</h1>` is set by router to `mod.meta?.title || matchPrefix` — i.e., the static screen meta title, *not* the loaded recap's title or date. So a user who deep-links to a specific recap, or navigates via arrows (when those work), has nothing in the document title or topbar text indicating what they're viewing. Browser tab title also doesn't update. This is the second h1 problem (see accessibility below) but also a wayfinding/identity bug in its own right.

### inconsistency — duration `0s` rendered on zero-duration sessions

API returns `duration: 0` and `start === end` for all three fixtures. `formatDuration(0)` returns `"0s"`, which appears in the hero meta as `SES-0003 · claude · 0s · vs SNAP-0002`. "0s" implies the session ran instantly. Either fall back to "—" when duration is unknown/zero, or render the actual `start` date instead.

### inconsistency — issue-stat tile uses red `del` color

`stat('del', stats.issues_opened, 'issues opened')` colors "issues opened" red via `var(--diff-del)`. Opening an issue is not a deletion. The diff-del red implies *something was removed*. Either use `add` (a new issue is created), `mod` (amber, "attention"), or a neutral ink. The brainstorm concept omits a color class on issues entirely, leaving it neutral.

### inconsistency — `tasks_moved` stat conflates added and changed tasks

`computeStats` does `tasks_moved = tasks_changed.length - tasks_done + tasks_added.length`. Newly-added tasks were not "moved" anywhere — they were created. On a session that creates 5 tasks and modifies 0, the stats read `0 done · 5 moved`, which is misleading. Split into a third "tasks created" stat or exclude added tasks from the moved count.

### inconsistency — RECAP kicker uses handover-purple

`.recap-hero-kind` is colored `var(--purple)` (rgb 160,127,224). Purple is established elsewhere in the viewer for *handover* artifacts (the brainstorm concept reserves it for that). A recap is not a handover. Pick a distinct color or use neutral ink to avoid implying the recap is a handover variant.

### inconsistency — footer renders raw ISO timestamp

Footer reads `2026-04-29T22:15:00Z · 6180 tok` — raw ISO, no locale formatting, no relative-time helper. The same field is sliced to 16 chars in the edit-mode draft caption (`draft generated 2026-04-29T22:15 by claude`). Two different formats for the same field. Standardize on the project's relative-time helper (e.g., `Apr 29, 22:15` or `3d ago`).

### friction — edit mode triple-renders identical draft caption

In edit mode, the same line `draft generated 2026-04-29T22:15 by claude` appears under each of the three textareas. There is one `generated_at` per recap, not per section, so this caption is the same text repeated 3×. Once at the top of the edit form (or once near the title input) is enough.

### friction — edit mode allows navigation away with no unsaved-changes warning

While editing, the topbar still shows `‹ Previous recap` (and `› Next recap` if applicable). Clicking it changes the hash, drops the user into another recap, and discards typed-but-unsaved edits silently. Either disable navigation while editing, or show a confirm-discard prompt.

### friction — "Regenerate" topbar action does not regenerate; it reloads from disk

The `↻ Regenerate` button (`title="Restore draft from disk"`) re-fetches the existing recap document from the server and overwrites the textarea contents. It does not regenerate via Claude or any generation endpoint. The label "Regenerate" implies LLM re-generation. Either rename to "Reset" / "Restore from disk", or wire an actual regeneration endpoint.

### friction — `Copy resume` is undiscoverable; no failure feedback

`⧉ Copy resume` copies `what_happened + whats_next` to the clipboard. Neither the label nor the title attribute (`Copy resume to clipboard`) tells the user that this is "copy a context blurb to paste into your next chat session." First-time users will not know what "resume" means here. Also: if `navigator.clipboard` is undefined (older browser, insecure context), the call is silently skipped — the success label "Copied" still flashes briefly because the icon swap runs before the clipboard write resolves.

### friction — filter chips match by `textContent` prefix, not data attribute

`bindFilterChips` shows/hides receipt cards using `ttl.toLowerCase().startsWith('tasks' | 'files' | 'lessons' | 'issues')`. If "Files touched" is ever renamed to "Modified files," the filter silently breaks. The cards already exist — adding `data-kind="files"` and matching on that attribute would be a one-line robustness fix.

### gap — no "N of M" position indicator

There is no signal of where the loaded recap sits in the series. With 3 fixtures it's manageable; at scale the user has no idea if they're 5 sessions back or 50. A simple `Recap 1 of 3` next to the arrows would help even before the picker is rebuilt.

### gap — handover link href uses session ID, not handover ID

Footer renders `Handovers: <a href="#/sessions/SES-0003">2026-05-01-plan6-design-pass-handoff</a>`. The anchor *text* is the handover ID; the *href* is the parent session ID. Clicking the link doesn't take the user to the specific handover within Sessions — it takes them to the session that owns it. The Sessions screen would need to scroll-to/highlight that specific handover to make the link feel correct; right now it's a vague jump.

### gap — narrative empty-state can't distinguish "blank" from "not generated"

When a `what_happened`/`what_landed`/`whats_next` field is missing, the section renders `<em class="empty">—</em>`. This is identical whether the recap was generated and intentionally left blank, or never generated at all. Add distinct copy: "(left blank)" vs "No draft yet — click Edit to write one." vs a loading state.

### polish — token-cost rendered as raw integer

Footer renders `6180 tok` — fine at this scale, but on a multi-hour session with 1.8M tokens it'll read as `1847293 tok`. Add thousands separators or k/M abbreviation.

### accessibility — heading hierarchy is broken (two h1s; h4 before h3)

Live DOM heading order on the recap page: `H1: Recap` (topbar page-title), `H1: Plan 3 (Task Detail) complete...` (hero title), `H4: WHAT HAPPENED`, `H4: WHAT LANDED`, `H4: WHAT'S NEXT`, `H3: Receipts`. Two `<h1>` elements on one screen breaks document outline. Going from h1 to h4 then back up to h3 is not a valid heading hierarchy. Fix: hero title should be `<h2>`, narrative section labels `<h3>`, receipts heading `<h3>` (sibling to narrative) — or a single `<h2>` "Receipts" if it's a peer section.

### accessibility — filter chips have no `aria-pressed` / `aria-selected`

`.filt-chip.on` adds visual styling only. Screen readers get no indication of which filter is active when the user activates a chip. Add `role="button"` + `aria-pressed="true|false"` (or use real `<button>` elements with `aria-pressed`).
