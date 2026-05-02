# Taskmaster — Current UI Reference

> A snapshot of what exists today. Treat this as a reference, not a constraint — the redesign is free to keep, reshape, or discard any of it.

## Overall layout and structure

The viewer is a single-page, vertically-stacked layout that fills the entire browser viewport. From top to bottom: a sticky header bar, an optional collapsible file-load bar, an optional phase banner section, an epics row, the kanban board (which takes the remaining flex space and scrolls within its columns), and two collapsible archive drawers below the board. The body has `overflow: hidden` on desktop so only the column bodies scroll internally. On mobile it flips to a single-column stacked layout.

## Header

The header is a slim gradient bar (about 54px tall) that spans full width. On the left: a small emoji icon badge (30×30 rounded square, clickable to open an emoji picker grid) alongside the project name (bold, 15px) and a small subtitle showing the last updated date. Two amber "READ-ONLY · AI Workflow Viewer" pill badges sit beside the title, creating a prominent notice that this is not an editable tool. On the right side, left to right: a load-status indicator (shows "ok" in green or an error in red), a 240px search field with a clear button, an "Epic" dropdown button, an optional "Phase" dropdown button (hidden if no phases exist), a row of four priority toggle pills (Critical / High / Medium / Low, each styled in its priority color), a "Sorting" dropdown button, and a gear icon for settings. All controls are 32px or smaller and sit tightly in one row — it is a dense, utility-first header with no wasted breathing room.

## Phase banner

Directly below the header is a subdued horizontal strip showing the current phase. The left side contains a horizontally-scrollable pill train of numbered phase steps connected by short horizontal lines — done phases show a green checkmark, the active phase is filled in accent blue and slightly larger, the user-selected phase inverts to a filled dark badge. The right side has a compact progress bar (8px tall), a fraction stat like "4/12 done (33%)", and a date countdown (red if overdue, amber if due within a week). When viewing a completed phase, the banner switches to a retrospective layout showing duration, on-time status, and a disabled checkbox list of deliverables. A "History" toggle button reveals a drop-down list of past phases with duration and on-time stats.

## Epics row

Below the phase banner is a horizontal flex row of epic cards. Each card (150–240px wide, auto-sizing to fill the row) shows the epic name, a small status pill (active = green, planned = grey, done = green), optional doc-link chips in accent blue, a 4px segmented progress bar (green for done, amber for in-review, blue for in-progress), and a text breakdown like "3 done · 2 active · 1 todo — 42%." Clicking an epic card filters the board; the selected card gets an accent-colored border and tinted background. Archived epics appear in a separate collapsible strip below with a chevron toggle.

## Kanban board

The board is a flex row of four to five equal-width columns: Blocked (hidden by default, toggled in settings), Todo, In Progress, In Review, and Done. Each column is a rounded-corner surface card with a header (column dot in status color + column name + count badge + collapse button) and a scrollable body. The Done column header also shows a secondary `+N archived` note. Any column can be collapsed to a 44px-wide vertical strip with the name rotated 90 degrees — collapse state is persisted to localStorage.

## Task cards

Cards are compact but readable. They have two zones: a darker header strip and a body. The header strip shows the task ID (monospace, dim), status label in status color (e.g. blue for "In Progress"), time-in-status (e.g. "3d"), an optional git branch name in green monospace, and the priority badge pushed to the right. The header background is tinted with a low-saturation hue derived from the task's epic — a subtle but useful at-a-glance epic identity signal. The body contains the task title (15px, semi-bold), an optional row of tags (stage number in blue, estimate in amber, sub-repo in monospace grey), and a footer row with the epic name tag, optional flags (docs chip in accent blue, dependency count in amber, spec-review verdict), and a date pushed to the right. Cards that moved status in the last two days get a glowing accent-colored inset ring. Done-column titles are dimmed to muted grey.

## Task detail modal

Clicking any card opens a centered modal (max 720px wide, full-height safe) over a blurred dark backdrop. The modal header has the task ID (styled as a monospace accent chip), priority badge, and large title. Below is a responsive auto-filling metadata grid with fields like Status (colored chip), Epic, Stage, Estimate, Sub-repo, Created/Started/Completed timestamps, Branch, Worktree. Optional sections follow in labeled dividers: a Docs section showing clickable file-path badges with type labels (spec, design, etc.); a dependency section with chips for "depends on" and "unblocks" (clicking a dep chip navigates to that task); a red-tinted Blockers section; and a Notes section rendering a subset of Markdown (headers, bold, code blocks, tables, task checkboxes, blockquotes). The modal has a small "×" button in the top-right corner; Escape also closes it.

## Filtering, searching, and sorting

Search is a single text input that filters by ID, title, notes, epic name, and branch. The slash key (`/`) focuses it from anywhere; Escape clears it. Epic and Phase filtering are dropdown buttons that turn accent-colored when active. Priority filtering is four persistent toggle pills — inactive ones fade to 30% opacity. Sorting is a dropdown with six options (Priority, Created, Started, Completed, Last Updated, Alphabetical) plus an ascending/descending toggle. All filter state is in memory; none is persisted except column collapse state.

## Color language

The default theme is dark (`#1a1a1e` background, `#222226` surface). Priority uses a consistent four-color system: Critical = red (`#f85149`), High = amber (`#d29922`), Medium = blue (`#5ea8ff`), Low = grey. Status uses: green for done, blue for in-progress, amber for in-review, red for blocked, grey for archived. A project-specific accent color is derived deterministically from the project name (name-hash to HSL hue), applied to borders, the header gradient tint, and interactive highlights throughout. There is also a "colored" theme variant that deepens these accent tints. A light theme exists with the same semantic colors mapped to lighter backgrounds. The overall aesthetic is a GitHub-dark / Linear-inspired developer tool: information-dense, neutral base, color used only for semantic signal.

## Typography

Inter is the sole typeface, rendered with antialiasing. Base UI text is 13–14px. Card titles are 15px / 600 weight. The smallest labels are 10–11px, mostly used for metadata, tags, and status labels. ID fields and branch names use a system monospace stack. Letter-spacing and uppercase tracking are used on section labels to create visual hierarchy without size differences.

## Quirks and notable choices worth flagging

- The **"recently moved" card glow** (accent inset ring) is a clever freshness signal that requires no user action.
- **Phase steps double as both a navigation breadcrumb and a clickable filter** — an unusual but compact interaction.
- The **project icon is a clickable emoji picker** — a personal-touches feature that feels incongruous with the otherwise technical aesthetic.
- The **Blocked column is off by default** and tucked behind a settings toggle, which means blocked tasks silently merge into Todo — easy to miss.
- The **`READ-ONLY` badge is displayed in amber priority styling**, which could cause users to mistake it for a task-status warning.
- The **project accent color is hash-derived from the project name** — every project gets a distinct identity hue without configuration.

## What feels missing or bolted on

- There is **no global stats summary** (total tasks, overall % done, velocity).
- The **archived tasks section lives below the board** rather than being integrated into a Done column, creating an awkward two-level done concept.
- **Keyboard navigation** (arrow keys / j-k / Enter) is implemented but not surfaced anywhere in the UI.
- The **settings panel has only three options** (theme, project color, blocked column) and feels underpowered for the complexity of the board.
- The **file-load bar is the only way to load a new file** after initial load — it collapses and offers no re-open affordance visible on screen.
- There is **no surface at all for handovers, lessons, issues, recap, or auto-mode** — the narrative/knowledge layer of the product is invisible in the current UI. The kanban is a structural-lens-only view today.
