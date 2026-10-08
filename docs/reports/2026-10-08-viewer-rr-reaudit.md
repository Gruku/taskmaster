<!-- User intent: a fresh-context check of every original viewer audit finding against screenshots of the re-skinned viewer
     (both themes, desktop and 390px), done before the user's own visual review and the release, with each one's outcome. -->

# Viewer Reality Reprojection re-skin: re-audit

Run on 2026-10-08, plan 4 Task 9 (`docs/plans/2026-10-06-viewer-rr-4-cleanup.md`).

**How it ran.** `viewer/tests/tools/capture-modals.mjs` captured 88 scenes, every route among them, in both themes at 1440 and
390px: 350 images, since `kanban-collapsed` has no phone form. Every API call was mocked. Four reviewers that had not seen
the branch each took one area. For every audit finding ID in its sections
(`docs/specs/2026-10-01-viewer-audit.md`, 111 IDs), a reviewer ruled fixed, open or not applicable, citing an image. It
then listed defects with no ID and the images most likely to disappoint. The open items were fixed on area branches
`rr4/t9-a` … `rr4/t9-d` and re-reviewed by the same reviewer until no high or medium item was open. The integration pass on
`rr4/task-9` then closed the leftovers named below.

**Final capture** (after every fix): 350 images. `metrics.json` has no problems: no page error, no unmocked write and no
native dialog. No scene has any axe violation (so none has `color-contrast`), and no page scrolls sideways. The unit suite
passes 1269/1269. The full mocked suite (`--workers=2`, run in three shards) passes 771/771, the a11y gate included.

**Outcome.** No high or medium finding is open. Of the 111 IDs: 105 are fixed, 1 is not applicable (KB-10), 1 is accepted
as low (IE-02) and 4 cannot be judged from a still image (SH-06, KB-07, DM-01, EM-07). The behaviour behind those 4 is
covered by code or mocked tests (see each row). The low leftovers are accepted;
each is a line in the plan's accepted ledger, and Task 10 moves them into the CHANGELOG's "Known limitations" or spec §10.

Verdict words: **fixed** · **not applicable** (the screen or element is gone) · **not visible** (keyboard, focus or
motion behaviour a still image cannot show) · **accepted** (left as is, with a reason).

## Area A: shell, dashboard, settings, cross-cutting, tokens

| ID | final verdict | evidence |
|---|---|---|
| SH-01 | fixed: the active nav item is a tinted fill with a full-perimeter border, no rail | route-settings.dark.d, dashboard.light.d |
| SH-02 | fixed: the empty footer band is gone | route-dashboard.dark.d |
| SH-03 | fixed: the topbar is one row or one row plus row 2, never other heights (a11y gate checks every route) | route-dashboard.dark.d, topbar-filters.dark.d |
| SH-04 | fixed: the hint reads "Ctrl K" on Windows, and is hidden on a phone where there is no shortcut | topbar-filters.dark.d, topbar-filters.dark.m |
| SH-05 | fixed: search has a visible border and an accessible name | topbar-filters.dark.d |
| SH-06 | not visible: the drawer's aria-expanded, aria-controls and Escape are in sidebar.js | code |
| SH-07 | fixed: content starts at one gutter on every route (24px desktop, 16px phone) | route-dashboard.dark.d, route-settings.dark.d |
| SH-08 | fixed: one `<main>` (metrics `mains` = 1) | metrics.json |
| SH-09 | fixed: the "Task" nav item is gone | page-empty.dark.d |
| DB-01 | fixed: long notes clamp with Show more / Show less; an expanded note takes two columns and the board stays packed | dashboard-note-expanded.dark.d |
| DB-02 | fixed per spec §11: notes are solid paper from tokens, with a tilt and a folded corner | dashboard.light.d |
| DB-03 | fixed: the summary strip has 4 linked counts; "+2 older" is a legible button | route-dashboard.dark.d |
| DB-04 | fixed: clean-up rows are links and their meta text passes contrast | route-dashboard.light.m |
| ST-01 | fixed: segmented controls with a signature fill on the selected option | route-settings.dark.d |
| ST-02 | fixed: Theme, Card density and Detail view are all present | route-settings.light.m |
| X-01 | fixed (was open, low): relation suggestions show each status as a shape and a word | relation-suggestions.dark.d |
| X-02 | fixed: axe is clean on every scene | metrics.json |
| X-03 | fixed: axe is clean on every scene | metrics.json |
| X-04 | fixed: no `outline: none` or `outline: 0` in css/ | code |
| X-05 | fixed: no raw font sizes outside tokens | code |
| X-06 | fixed: no raw radii | code |
| X-07 | fixed: cut text ends in an ellipsis | route-dashboard.dark.d |
| X-08 | fixed: no dead controls | route-dashboard.dark.d |
| X-09 | fixed: 16px inset at 390px | route-dashboard.light.m |
| X-10 | fixed: controls use `font: inherit` | code |
| X-11 | fixed: no @import, no Google Fonts | code |
| X-12 | fixed: empty sections collapse to one line ("Empty: Specification · Plan · Notes") | page-empty.dark.d |
| X-13 | fixed: no `box-shadow` in css/ | code |
| TK-01 | fixed: tokens are rewritten and there is a light theme | tokens.css |
| TK-02 | fixed: a few one-off literals remain in desk.css (line-height, letter-spacing, note sizes), none on a token scale | desk.css |
| TK-03 | fixed: note tokens are defined | tokens.css |
| TK-04 | fixed: the only custom property set outside tokens.css is each note's `--tilt`, set from JS | note-card.js |
| TK-05 | fixed: no box-shadow, no hover transform, every `border-left` uses a `--border-*` token | code |
| TK-06 | fixed: no inline style literals in main.js or task-detail.js | code |

Carried items the re-audit had to rule on:
- **Popover and suggestion list against the dialog, dark** (medium): fixed. The new `--popover-surface` tokens put
  the popover on its own ground (ground-10 on the dialog's ground-15). Ground-20 was not used because it would put the critical
  ◆ shape under 3:1. Light is accepted: nothing there is lighter than the dialog, so the border carries the edge.
- **Conflict banner row dividers, dark** (medium): fixed. They use `--border-default`.
- **Light banner headline**: accepted. The ▲ carries the hue and the words are dark foreground text.
- **A light card hovering to the page colour** (medium): fixed. Light `--card-bg-hover` is a mix of ground-0 and ground-5,
  so it is neither the card nor the page.

No-ID items:
- Expanding a note broke the board grid (medium): fixed. The board is packed.
- Phone notes cramped in two columns: fixed. They are one column at 390.
- Row holes under short notes: fixed.
- UNPIN floating mid-header: fixed.
- The decision card repeating its title: fixed.
- "Ctrl K" on the phone search: fixed.
- The 390px topbar count was cut: fixed. It wraps between its " · " parts.
- **Phone "Filters n"** (medium): fixed in the integration pass. The number counted the controls parked behind Filters,
  so it read as n filters on when none were set. The topbar's Filters button now shows no number. Pinned by shell.mock
  "Filters on the Kanban at 390 shows no number while nothing is filtered".
- Hover inside popovers was inconsistent (low): fixed in the integration pass. Tag-filter options and Kanban epic options now
  hover with `--popover-surface-hover`, and the Epic options popover paints its scroll cue on `--popover-surface`.
- The dark popover shares the card ground (low, from the tokens): accepted, ledger "surfaces". A page popover over cards
  is separated by its border. The lighter ground would fail the critical shape's contrast.

## Area B: Kanban, Table, Epics, Epic detail

| ID | final verdict | evidence |
|---|---|---|
| KB-01 | fixed: IDs on one line, titles full width and clamped at 3 lines; a card in a bundle frame keeps its age on line 1 | kanban-board.dark.d, kanban-long.dark.d |
| KB-02 | fixed as far as an image shows (a card is one link; kanban.mock pins it) | kanban-board.dark.d |
| KB-03 | fixed: on a phone, column tabs show one column at a time, and the cut last tab fades | route-kanban.light.m |
| KB-04 | fixed: every phase is named, cut with an ellipsis, and the current one is wider | kanban-filters.dark.d |
| KB-05 | fixed (was open, medium): epic chips show their names with square swatches; only the chip squeezed into the leftover width is cut | kanban-board.dark.d |
| KB-06 | fixed: chips, More and Epic options agree on one count (open tasks) | kanban-epic-more.dark.d, kanban-epic-options.dark.d |
| KB-07 | not visible: the keyboard and focus of the More list (chips.mock pins it) | kanban-epic-more.dark.d |
| KB-08 | fixed: Group and Sort have visible labels | route-kanban.dark.d |
| KB-09 | fixed: priorities are words with a shape | kanban-board.dark.d |
| KB-10 | not applicable: the carousel and its slide buttons are gone | kanban-filters.dark.d |
| KB-11 | fixed: no glows; bundle groups have a full-perimeter border | kanban-filters.dark.d |
| KB-12 | fixed: the selects use the RR chevron and font | route-kanban.dark.d |
| TB-01 | fixed (was open, medium): at 1440 every shown column is whole and the next one starts under the end fade; scrolled, a fade starts at the sticky edge | route-table.dark.d, table-long-scrolled.dark.d |
| TB-02 | fixed (was open, medium): titles are mostly whole; the ID column fits the usual IDs and a longer one wraps at its hyphens | route-table.dark.d, table-long.light.d |
| TB-03 | fixed: headers have sort icons and the active direction; the order is right | table-sorted.light.d |
| TB-04 | fixed: chips stay on one line, and the rest go behind More | table-chips.dark.d |
| TB-05 | fixed: phone rows are stacked cards with a Sort select (a lone "—" for no size is accepted, see below) | route-table.light.m |
| TB-06 | fixed: shared markers, no 10px pills, axe clean | route-table.dark.d |
| EP-01 | fixed: lifecycle markers; an unknown status is a capitalised word ("Paused") | route-epics.dark.d |
| EP-02 | fixed: every row has a progress bar with a visible empty track | route-epics.dark.d |
| EP-03 | fixed: stacked cards on a phone | route-epics.light.m |
| EP-04 | fixed: count and search | route-epics.dark.d |
| EP-05 | fixed: axe clean | metrics.json |
| ED-01 | fixed: progress labels say what they count ("1/4 closed · 1 done") | route-epic.dark.d |
| ED-02 | fixed: status bar with a colour-key legend (archived is hatched); tasks are grouped by status, with Done and Archived collapsed | route-epic.dark.d, epic-detail-long.light.m |
| ED-03 | fixed: the design status is a "Design · Locked" tag | route-epic.dark.d |
| ED-04 | fixed: a missing epic shows a not-found block with "All epics" | epic-missing.dark.d |

Counts: every screen reads "n <noun>" and adds " · m visible" only while a filter or the search narrows the list. Nothing
reads "m of n" (a11y gate, plus a mocked test per screen: Kanban, Table and Epics already did this when checked).

No-ID items:
- A card in a bundle group wrapped its first line (medium): fixed.
- The phone phase strip wrapped onto two lines (medium): fixed. It is one line.
- The phone count was cut (medium): fixed, in area A.
- Minimal/Full filling half its frame in Filters: fixed.
- Epic options used the wrong font and had no scroll cue: fixed.
- Epic modal rows stopped at about 600px: fixed.
- Epic rows did not reach the search bar's right edge: fixed.
- The epic legend showed two glyphs per key: fixed.
- Archived was near-black in light: fixed.
- "Filters 3": fixed, in area A above.
- **Phone phase strip, current phase** (low, N1): fixed in the integration pass. On a phone its progress bar now shows
  under its name; the chip's flex layout had been squeezing the bar to nothing. The count still gives way there and is
  accepted (ledger "Kanban phase strip"): it would cut the name below its readable 80px, or push "No phase" behind
  More. Pinned by kanban.mock "at 390 the current phase shows its progress as a bar under its name, inside the chip".
- **Header fragment "IC ⇕" under the Table's start fade** (low, N2): fixed in the integration pass. A header scrolled
  under the sticky edge hides its label until it comes back out. Pinned by table.mock "scrolled sideways, a header cut by
  the sticky edge shows nothing until it comes back out".
- Accepted (ledger), all low:
  - At 390 the Epic row shows only All and More.
  - The epic header's "■ ◐ Active" keeps both glyphs.
  - "Waiting on you" shows only at wide column widths.
  - "No match" sits in one column on an empty search.
  - A phone table card with no size ends in a lone "—".

## Area C: task detail, detail modal, Create/Edit, Ideas

| ID | final verdict | evidence |
|---|---|---|
| TD-01 | fixed: status is a shape plus a word (◐ signature, ● green) | route-task.dark.d |
| TD-02 | fixed: headings, lists, tables, code and task lists render | route-task.dark.d, route-detail-modal.light.m |
| TD-03 | fixed visually: no "‹ back"; each section has a pencil edit button (keyboard: task-page.mock) | route-task.dark.d |
| TD-04 | fixed: link pills are bordered and spaced | route-task.dark.d, detail-rich.light.d |
| TD-05 | fixed: empty sections collapse to one line | page-empty.dark.d |
| TD-06 | fixed: a missing task is a state block and the topbar is claimed | route-task-missing.dark.d, page-missing.dark.d |
| TD-07 | fixed: Graph is marked as the current view | route-task-b.dark.d |
| TD-08 | fixed: dates are relative; activity times are formatted, not raw ISO | route-task.dark.d |
| TD-09 | fixed: the handover rail has a status control and a sans heading, no italic serif | route-task.dark.d |
| TD-10 | fixed: the graph frame is a bordered recess; nodes show about 20 characters; on a phone it opens centred on this task | route-task-b.dark.d, route-task-b.dark.m |
| DM-01 | not visible: focus trap, aria and focus return (detail-modal.mock pins them) | route-detail-modal.dark.d |
| DM-02 | fixed: padded body, no back link or breadcrumb, the title once | route-detail-modal.dark.d |
| DM-03 | fixed: a full-height sheet on a phone; "Open full" on one line | route-detail-modal.light.m |
| EM-01 | fixed: Discard asks with an in-app confirm; an untouched form is not dirty | discard-confirm.dark.d, create-untouched.dark.d |
| EM-02 | fixed: Create idea uses the shared entity form | ideas-create.dark.d |
| EM-03 | fixed: a visible label above every field; axe clean | create-untouched.dark.d |
| EM-04 | fixed: only the focused field shows the ring | create-untouched.dark.d, detail-edit-stacked.dark.d |
| EM-05 | fixed: errors after submit only, with "3 fields need attention" | create-validation.dark.d |
| EM-06 | fixed: Estimate is S/M/L plus days; Stage is a half-width column | detail-edit-stacked.dark.d |
| EM-07 | not visible: motion (every animation has a reduced-motion fallback, pinned by the style rules) | — |
| EM-08 | fixed: Edit secondary, "Open full" ghost, Save the one primary | route-detail-modal.dark.d |
| IE-01 | fixed: two-row topbar; filters in a rail on the page | route-ideas.dark.d |
| IE-02 | accepted (low, ledger "C: IE-02"): on a phone the ID column leaves about 180px, so titles clamp at about 22 characters a line; desktop titles wrap whole, and the pane shows each one in full | ideas-long.light.m |
| IE-03 | fixed visually: chips are button-styled; axe clean | route-ideas.dark.d |
| IE-04 | fixed: an idea with no status shows no marker | route-ideas.dark.d |
| IE-05 | fixed: axe clean | route-ideas.light.d |

No-ID items:
- Ordered-list numbers bled left of the text, or were cut at the modal edge (medium): fixed. The list indent follows its
  widest number.
- The phone graph cut off Unblocks, then the current task's node (medium): fixed. It opens centred on This task, with a
  "Scroll sideways" line.
- The refused inline title looked broken (medium): fixed. The message has its own padding, there is no stray ×, and Edit
  is hidden while the input is open.
- Chip heights were ragged on a phone: fixed. No row mixes heights.
- Graph node titles were cut to about 12 characters: fixed.
- Accepted (ledger "C:" lines), all low:
  - The phone tab strip cuts "Raw JSON" to "Raw".
  - Ideas rail and rows: the rail wraps on a phone, the Tags button style differs from the chips, titles wrap at
    different points, status shows twice in the pane, and a no-status idea leaves a blank line.
  - A 40-dependency rail has no collapse.
  - On a phone, S/M/L is 32px tall beside a 44px days input; there is a double gap after the ID's copy icon.
  - The phone Tags popover leaves a "TERS" fragment of Clear filters beside it. The count truncation it also showed was
    fixed in area A.

## Area D: Sessions, Issues, Issue detail, Bugs, Bug detail, Archived

| ID | final verdict | evidence |
|---|---|---|
| SE-01 | fixed: the off "Superseded" chip has a border | route-sessions.dark.d |
| SE-02 | fixed: the chips are bordered buttons | route-sessions.dark.d |
| SE-03 | fixed: the handover title comes first, with the slug as a subline; the rail shows the summary once | sessions-rail.dark.d |
| SE-04 | fixed: badges are 12px; 11px only for uppercase labels | route-sessions.light.m |
| SE-05 | fixed: no tint or legacy fallbacks in sessions.css | sessions.css |
| IS-01 | fixed: card heads on one line, titles full width | issues-status.dark.d |
| IS-02 | fixed: at 390 a column tab list shows one column at a time | route-issues.light.m |
| IS-03 | fixed: the switcher is labelled "View" | route-issues.dark.d |
| IS-04 | fixed: only stale items carry a "stale 50d" tag; no bars | route-issues.dark.d |
| IS-05 | fixed: severity is a shape plus a word | issues-status.dark.d |
| IS-06 | fixed: column heads are labels with counts | issues-status.dark.d |
| IS-07 | fixed: a zero-count chip is disabled | issues-long.light.m |
| IS-08 | fixed: every card and pill is a link, so the board is reachable by keyboard; axe clean | metrics.json |
| ID-01 | fixed: "discovered 2mo ago" and the Evidence body show | route-issue.dark.d |
| ID-02 | fixed: a sans header, no italic serif | route-issue.dark.d |
| ID-03 | fixed: related and duplicate links are bordered pills | route-issue.dark.d |
| BG-01 | fixed: rows show severity and status markers; "Show archived" is bordered | route-bugs.dark.d |
| BG-02 | fixed: titles clamp at 2 lines and use the row's width (about 520–560px at 1440); no extra phone inset | bugs-long.dark.d, route-bugs.light.m |
| BG-03 | fixed: rows are links | route-bugs.dark.d |
| BG-04 | fixed: a "Sort: Newest first" select | route-bugs.dark.d |
| BD-01 | fixed: the page shows the bug and its actions; B-999 is a not-found state | route-bug.dark.d, route-bug-missing.light.m |
| AR-01 | fixed: phone groups are full width; titles clamp with an ellipsis | archived.light.m |
| AR-02 | fixed: "Superseded by" and "Duplicate" tags sit inline with the meta | route-archived.dark.d |
| AR-03 | fixed: group headings are `h2`; axe clean | route-archived.dark.d |

Counts:
- Sessions (medium): fixed. Hiding handovers (Superseded, a status chip, the Handovers toggle) now adds
  " · m visible".
- Bugs and Issues (low): fixed. The suffix needs the list to be shorter than the total.
- Archived was already right.
- The reviewer's optional note that Sessions' "m visible" adds threads and handovers into one figure was not acted on.

No-ID items:
- Raw markdown on issue cards (medium): fixed.
- Cards cut off at the bottom of the board with no cue (medium): fixed. The cut edge fades while more is below.
- The Status view's fourth column was clipped with no cue: fixed. It has an edge fade.
- Ragged trailing columns on bug rows: fixed. They are grid columns, and the title takes the room.
- Duplication on the issue and bug pages (Notes heading, Discovered, plain-text T-102): fixed.
- The marker order differed between the issue and bug pages: fixed. Both show status, then severity.
- The phone More chip looked like plain text: fixed.
- A large gap above "found in" on phone bug cards: fixed.
- The `issues-evidence` scene showed nothing new: fixed. It now captures the expanded evidence.
- Accepted (ledger "D:" lines), all low:
  - "Show archived" can leave the Bugs list unchanged; it follows the plan-3 amendment.
  - A phone bug card without "found in" has a tighter gap than the others.

## Accepted items

Every accepted item above is one line in the plan's accepted ledger,
`.superpowers/sdd/2026-10-06-viewer-rr-4-cleanup/accepted.md` in the integration worktree. That file is git-ignored, so it
is not in this commit. The lines are tagged by area (`C:`, `D:`, `Kanban …`, `surfaces`, `dashboard notes`, `table (phone)`).
Task 10 turns the `user-visible: yes` lines into the CHANGELOG's "Known limitations" and the rest into spec §10. Two
earlier ledger lines were closed in the integration pass, and each has a FIXED line after it:
- "FILTERS 3"
- the popover hover tokens
