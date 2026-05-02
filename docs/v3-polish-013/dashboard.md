# Dashboard — `/v3/#/dashboard`

> Part of v3-polish-013 end-to-end review.

### bug — briefing strip template not interpolated

The briefing strip renders the literal template: *"Since you left, tasks closed, new issues, lessons promoted."* — N counts are missing. Either the data wiring is broken or the template segments without values are leaving raw connector text. This is a real bug, not polish — should be fixed before any visual work on this strip.

### bug — phase pip overload on long phase lists

Briefing strip renders all 11 phases as pips inline (Discovery → Spike → Foundations → Skeleton → Migration → Visual Redesign → Polish → Launch → Soak → GA → Documentation). On any project with >5 phases it becomes a wall. Truncate around the active phase (e.g., show ±2 around current + collapse rest behind a toggle), or switch to a compact "phase N of M" indicator with a popover of the full list.

### inconsistency — auto-mode signal duplicated in topbar pill + dashboard strip

- Topbar pill: *"Auto Mode · Implement · 47h39m"*
- Dashboard strip: *"Auto-mode · 1 running · v3-031 · 30% · 47:39:11 · view all →"*

Two different formats (h47m39 vs 47:39:11), two different copies of the running state, both visible at once. v3-polish-006 already proposes slimming both — confirmed scope from the e2e walk.

### inconsistency — date format raw `toLocaleString`

Auto-strip timestamp renders as `4/30/2026, 5:30:00 PM`. That's a JS default `toLocaleString()` artifact — mixes US date order, has no design-system styling, doesn't match relative-time strings used elsewhere. Standardize to a relative-time helper (e.g., "2d ago" or "Apr 30, 5:30 PM") across all surfaces.

### friction — "Cycle size" button affordance

Each widget exposes a "Cycle size" button. Clicking cycles between (presumably) 2-3 sizes, but there's no indication of (a) how many sizes exist, (b) which size is active, (c) what the next click will do. A discrete size segmented control or a labelled current-state would be clearer.

### friction — multiple "Add widget" CTAs on a single grid

Three "Add widget" buttons render at once (one per row in the bento). Identical labels, identical-looking buttons, repeated three times — confusing for screen readers and visually noisy. Either consolidate to one "Add widget" affordance per dashboard, or differentiate by row (e.g., "Add to top row").

### friction — edit-mode buttons always present on widgets

Each widget shows "Drag handle" + "Remove widget" + "Cycle size" buttons even when not in edit mode. If edit-mode is meant to be a discrete state (per `createEditMode`), these handles should hide outside it.

### gap — empty-state strings inconsistent

- *"Nothing newly unblocked."*
- *"Nothing since you last looked."*
- *"No prior session yet."*
- *"No open issues."*
- *"No core lessons yet."*
- *"No commits yet."*
- *"No agents running."*
- *"Tests: 0/0 passed"*

Each widget invents its own empty-state phrasing. Some are sentences, some are fragments, one ("Tests: 0/0 passed") doesn't read as an empty state at all. Need a shared empty-state convention — at minimum a tone guide ("Nothing X yet" vs "No X." vs "X · 0").

### inconsistency — task title hack `"Locked: keyboard nav for kanban (in progress, do not touch)"`

Task v3-051's title is prefixed with `"Locked:"` and suffixed with `(in progress, do not touch)`. That's title-string used as state metadata. Should be a proper locked-task status badge/icon, not in the title.

### polish — "Tests: 0/0 passed" reads wrong on cold state

When no tests have run yet, "0/0 passed" is technically correct but linguistically odd. Better: *"No test runs yet."*

### accessibility — widget headers not `<h*>` tags

Widget headers (Suggested next, Phase deliverables, etc.) are rendered as `banner` with `generic` text inside, not as `heading` elements. Breaks nav-by-headings.
