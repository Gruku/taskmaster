# Kanban — `/v3/#/kanban`

> Part of v3-polish-013 end-to-end review. Live walkthrough with 29 tasks across 5 status columns; auto-mode is running on `v3-031` (visible in topbar pill, page strip, and per-card live block).

---

### bug — empty columns show "— filtered out —" even with zero active filters

Switch the group dropdown to "Group: Phase" with NO filters active. Phases P-05 through P-11 plus Orphans (8 columns) all render the empty-state string *"— filtered out —"* even though the user hasn't filtered anything — they're genuinely empty (no tasks ever assigned to those phases). `kanban.js` line 288 unconditionally uses that string for any zero-task column. Should branch on `filterCount > 0`: *"— empty —"* / *"No tasks in this phase yet."* when no filters; *"— filtered out —"* only when filters reduced the column to zero.

### bug — filter count uses "filters" plural for any count, including 1

Activate one priority filter (e.g., Critical). The right side of the epic row shows *"1 filters · clear all"*. Pluralization isn't handled — should be *"1 filter"* for `n === 1`, *"N filters"* otherwise.

### bug — "All" bookend pill missing from phase stepper, only "Orphans" renders

The stepper component (`phase-stepper.js`, line 7) and CSS (`kanban.css` lines 392–419, `.phs-util` selectors) both spec a layout: `[All-pill] [past-region] [active-card] [future-region] [Orphans-pill]`. Live, only the right-side Orphans pill renders. There's no "All" / "Show all phases" affordance on the left; users get out of a phase filter only by clicking the active phase a second time (a non-discoverable toggle described in `kanban.js` lines 209–213).

### bug — Group: Phase columns labeled with raw phase IDs (`P-01`…`P-11`), not phase names

In status grouping, columns read "Blocked / Todo / In Progress / In Review / Done" — clear English. Switch to Group: Phase and the columns read "P-01, P-02, P-03…" — raw IDs. The phase stepper above the board correctly shows "Discovery / Spike / Foundations / Skeleton…", so the names exist in the backlog. `groupTasks()` in `filters.js` line 88 uses `key` for the label instead of looking up `phases[i].name`. Cognitive jump for the user: same phase shown two ways on the same screen.

### bug — `⌘K` keyboard hint shown in search bar but no listener wired

The search input renders `<span class="cmp-kbd">⌘K</span>` next to the icon, training users to expect ⌘K focuses the search. Pressing ⌘K with the page focused leaves focus on `BODY` — there's no `keydown` listener anywhere in `kanban.js` or globally that handles this. The hint is a broken affordance.

---

### inconsistency — three different elapsed-time formats simultaneously visible

For the running auto-mode task `v3-031`, three different formats render at once:
- Topbar pill: *"Auto Mode · Implement · 48h31m"*
- Kanban auto strip: *"running 2d"* AND *"48:31:15"* in the same strip
- Per-card live block: *"48:30:45"*

So a single value appears as `48h31m`, `2d`, `48:31:15`, `48:30:45` — four different renderings. Pick one canonical formatter (likely `48h31m` or `2d 31m`) and use it everywhere.

### inconsistency — auto-strip date stamp uses raw `toLocaleString`

The auto-mode strip shows *"4/30/2026, 5:30:00 PM"* — same `toLocaleString()` artifact already flagged on dashboard. Standardize on a relative-time helper across surfaces.

### inconsistency — search input uses `type="text"` instead of shared `tmSearch` helper

`kanban.js` lines 62–65 build the search field via `innerHTML` instead of `topbar.js#tmSearch`. The inline variant lacks `type="search"` (no native clear button, no search-input semantics) and lacks `aria-label`. The shared helper provides both. Other screens that use `tmSearch` get them automatically — kanban drifts.

### inconsistency — `v3-051` task title encodes lock state inline

Task v3-051's title reads *"Locked: keyboard nav for kanban (in progress, do not touch)"* — lock state and a procedural note crammed into the title. Same finding logged on dashboard; confirmed it shows up here too in the In Progress column.

### inconsistency — "Spike" past chip shows plain `3/3`, active phase card shows `0/16 · 0%`

The past chips' `title` attribute reads *"Discovery · 3/3"* / *"Spike · 3/3"* (no percentage). The active phase card reads *"03 · Foundations · 0/16 · 0%"* — adds a percentage. Future cards follow the active-card format. Same component, three sub-formats. Pick one.

---

### friction — column starts in collapsed state across reloads

The "Blocked" column rendered at width 66px on page load (the collapsed-column width). The `prefs.kanban.collapsed_columns` array persists collapse state across reloads, so a user who collapsed it in a previous session has no obvious cue that it's collapsed today other than the column being narrow with vertical text. There's no "Reset collapsed columns" affordance — the only way back is to find each collapsed column and click it.

### friction — `↳ 1` dependency badge on cards has no tooltip or aria-label

Several cards (`v3-054`, `v3-009`, `v3-050`, `v3-053`, `v3-051`) show a small `↳ 1` indicator. Live HTML: `<span class="card-dep-badge">↳ 1</span>` — no title, no aria-label, no on-hover popover. New users won't know whether it's "1 dependency", "1 dependent task", "1 child", or something else. The icon meaning is undiscoverable.

### friction — phase-stepper carousel arrows have no aria-label or title

Slide buttons render as bare `‹` / `›` glyphs with a counter overlay (e.g., `‹0`, `›5`) and **no** `title` or `aria-label` attribute. Screen readers see four unlabeled buttons. Sighted users without the spec context have to click around to learn what they do.

### friction — collapsed column toggle uses ambiguous `‹` / `›` glyphs

The `kanban-col-toggle` button uses `‹` (when expanded) and `›` (when collapsed). Both glyphs read as horizontal pagination, not "collapse / expand this column." `title` attributes are correct ("Collapse" / "Expand") but only show on slow hover. Consider `⊟` / `⊞` or `▾` / `▸` for instant readability.

### friction — `clear all` reset link buried at right edge of the epic chips row

When filters are active, the only "clear all" affordance is `<span class="kanban-reset-link">clear all</span>` sitting at the right edge of the epic chips row. With many epics (5+), the link is competing for the user's eye against chips. Promote to a more prominent location next to the filter-count badge, or surface it near the search bar where users start filtering.

---

### gap — cards are not keyboard-focusable despite click-to-navigate behavior

`.card-task` has `cursor: pointer` and a click handler that navigates to the task detail. But `tabIndex = -1`, no `role="button"`, no `aria-label`. Keyboard users cannot focus a card to activate it; screen readers announce the card body as plain text without indicating it's interactive. Adding `tabindex="0"`, `role="button"`, `aria-label="Open task <id>: <title>"`, and a `keydown` handler for Enter/Space would close this gap.

---

### polish — `card-tis.stale` only appears on a single old card (`v3-052 · 17d`)

Of 29 cards, only `v3-052` shows a time-in-status indicator at all (`17d` with `.stale` class — amber color). The `card.js#renderCard` requires `task.started || task.created` to compute the value; the rest of the cards have neither populated. The single amber `17d` looks like a ghost element rather than a deliberate signal. Either populate `created` for all tasks (so the indicator is universal) or hide the field on cards that lack the data instead of computing it for one.

### polish — `.kanban-col-head .lbl` font-size hard-coded `18px`

`kanban.css` line 727 hard-codes `font-size: 18px` for the column header label. Every other text size on the screen uses `var(--text-xs|sm|base|md)`. This hard-coded value won't scale if tokens change.
