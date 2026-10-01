<!-- User intent: bring the Taskmaster viewer into full alignment with the Reality Reprojection design system, and fix every visual bug and UX/usability problem found along the way — one coherent, accessible, two-theme UI instead of per-screen one-offs. -->

# Viewer × Reality Reprojection — design

Date: 2026-10-01 · Status: awaiting user review · Branch: `feat/viewer-reality-reprojection` (off `feat/database-native-foundation` @ 75989f3)

## 1. Goal

Re-skin the Taskmaster viewer (`viewer/`) to the Reality Reprojection (RR) design system, in dark and light themes, and fix the 110 findings of the 2026-10-01 audit — including layout rework where the layout is the problem and the data bugs that make three screens show wrong or empty content.

**Sources of truth**

- RR design system: https://claude.ai/artifact/TAGXgW2cX1PabtPpN3C9ZG, pinned snapshot (version `1790812061-4bef`) in `docs/specs/assets/reality-reprojection-2026-10-01/`. All token values come from its `tokens.json`; none are invented.
- Audit: `docs/specs/2026-10-01-viewer-audit.md`. Finding IDs (`KB-01`, `X-02`, …) in this spec refer to it.

**Success criteria**

1. Every route renders correctly in dark and light at 1440×900 and 390×844 with no horizontal page overflow.
2. axe-core: zero `color-contrast` failures on every route in both themes.
3. Zero pointer-only click targets (every clickable element is reachable and operable by keyboard).
4. The style-rules test (§7) passes in enforcing mode.
5. All 21 high and 56 medium findings are fixed; each low finding is fixed or listed in §10 with a reason.
6. Existing pytest, unit and Playwright suites pass.

## 2. Precedence rules

Where RR and the user's standing UI rules conflict, the user's rules win:

| RR says | Viewer does |
|---|---|
| Elevation via `shadow-*`; `highlight-inner`, `shadow-recessed`, `shadow-inset` | **No `box-shadow` anywhere.** Depth = surface stepping + 1px borders. Frost = blur + translucent ground + border, no shadow. |
| Buttons lift `translateY(-1px)`, tags/nav shift `translateX(2px)` on hover | **No transform on hover.** Hover changes color, border, background only. |
| (RR already forbids colored accent borders on cards) | **No colored left rails** anywhere, including nav active state. Neutral structural left borders (tree connectors, blockquotes) are allowed. |
| Focus = `border-focus` 2px + `signature-glow-strong` halo (a shadow) | Focus = `outline: 2px solid var(--border-focus); outline-offset: 2px`. |
| Grain on surfaces that earn it | No grain. A dense tracker has no surface that earns it. |

Not affected (kept, with `prefers-reduced-motion` fallbacks): modal entrance (rise 16px, `dur-macro`, `ease-bell`), mobile drawer slide, phase-stepper slide, dropdown open, theme flip (`dur-standard`, `ease-hourglass`).

RR mood for this app: **Utilitarian** (Technical, Constrained, Utilitarian) — `container-wide`, Technical voice for data, sparing signature.

## 3. Foundation

### 3.1 Tokens (`viewer/css/tokens.css`, rewritten)

Three tiers, names identical to RR `tokens.json` with a `--` prefix:

- **Primitive:** `--ground-0 … --ground-100` (13 steps), `--signature`, `--signature-vivid/-muted/-dim/-glow/-glow-strong`, `--accent-cyan/-lime/-pink/-orange` (+ `-subtle`), `--pastel-*` (+ `-subtle`, `-grounded`), `--color-success/-warning/-critical/-info` (+ `-subtle`, `-bold`), `--space-micro/xs/sm/md/lg/xl/2xl/3xl` (4/8/12/16/24/32/48/64), `--radius-xs/sm/md/lg/xl/full` (2/3/4/6/8/9999), `--dur-micro/standard/macro` (80/200/400ms), `--ease-hourglass/-hourglass-settle/-pendulum/-bell`, `--frost-blur-*`, `--frost-bg-*`, `--overlay-bg`, type scale, leading, tracking, weights, font families.
- **Semantic:** `--bg-page` (ground-5), `--surface-ground` (ground-0), `--surface-raised` (ground-10), `--surface-overlay` (ground-15), `--bg-recessed` (ground-0, the milled channel for inputs, tracks and board columns; RR's `surface-recessed` is ground-5 and is not used for channels), `--foreground-bold/default/subtle/disabled` (ground-100/80/60/40), `--border-subtle/default/strong/focus` (ground-15/20/30/signature), `--signature-fill`, `--signature-fill-hover`, `--signature-text`, `--on-signature`.
- **Component:** only where a component needs a role the semantic tier lacks (e.g. `--card-bg`, `--card-bg-hover: var(--ground-15)`, `--col-bg`). Defined in `tokens.css`, never in screen CSS (fixes TK-04).

Shadow and grain tokens from RR are **not** imported.

**Themes.** `:root, [data-theme="dark"]` holds dark values; `[data-theme="light"]` overrides the ground scale (inverted), `signature` (`#3f58c0`), `signature-fill-hover`, warning hues, frost/overlay backgrounds. Semantic aliases re-resolve on their own. Every `--x` used anywhere is defined (fixes TK-03).

**Hue-on-text rule.** In status/severity/priority markers the *shape* carries the hue and the *word* is always a `foreground-*` color. Hue as text is allowed only for `--signature-text` (links, section labels) on `bg-page`/`surface-raised`, and `--color-*-bold` on `surface-ground` in light theme. This makes contrast pass by construction (fixes X-02).

### 3.2 Migration aliases

Stage 1 keeps the old names as aliases so the whole UI flips palette in one commit; stage 5 deletes them.

| Old | New |
|---|---|
| `--bg-canvas` | `--bg-page` |
| `--bg-shell`, `--bg-deep`, `--bg-board-col` | `--surface-ground` / `--surface-recessed` |
| `--bg-panel`, `--bg-card`, `--bg-issue` | `--surface-raised` |
| `--bg-card-hover` | `--ground-15` |
| `--border`, `--border-soft` | `--border-subtle` |
| `--border-strong` | `--border-strong` |
| `--ink`, `--ink-1` | `--foreground-bold` |
| `--ink-2` | `--foreground-default` |
| `--ink-3`, `--ink-4` | `--foreground-subtle` (`--foreground-disabled` only for disabled controls) |
| `--accent`, `--accent-blue`, `--accent-edit` | `--signature` / `--signature-text` |
| `--accent-2` | `--signature-vivid` |
| `--accent-soft` | `--signature-glow` |
| `--green`, `--accent-green` | `--color-success` |
| `--amber`, `--gold` | `--color-warning` |
| `--red` | `--color-critical` |
| `--purple` | `--pastel-signature` |
| `--shadow-*`, `--recess-*` | removed (no alias) |

**Categorical palette** (epics and bundles, cycled; rendered as a small square swatch beside a `foreground` label, never as text color): dark `signature, accent-cyan, accent-pink, accent-orange, accent-lime, pastel-orange`; light `signature, pastel-cyan-grounded, pastel-pink-grounded, accent-orange, pastel-lime-grounded, pastel-orange-grounded`.

### 3.3 Typography

Fonts bundled locally in `viewer/vendor/fonts/` (variable woff2 from the RR snapshot): League Spartan, DM Sans, JetBrains Mono. `@font-face` with `font-display: swap`. The Google Fonts `@import` is removed (X-11); Inter and Source Serif Pro are removed (IS-06, ID-02); Playfair is not shipped. Global `button, input, select, textarea { font: inherit }` (X-10).

| Voice | Use in the viewer |
|---|---|
| Declaration (League Spartan 800, uppercase, `tracking-tight`) | Screen title in the topbar (22px, `declaration-h4`); modal titles (22px); brand wordmark; button labels (14px, 12px small, `tracking-wide`) |
| Narrator (DM Sans) | Entity titles on detail pages (`narrator-header` 20/700); card and row titles (14/600); document body (16/600, leading 1.6); help text (14/600); metadata (`narrator-whisper` 13) |
| Technical (JetBrains Mono 600) | IDs, counts, dates, branches, code (14 / 12); tags and status words (12); section labels (11/800 uppercase `tracking-ultra`) |

No italic. No text below 11px; 11px only for uppercase section labels (fixes X-05). No raw `font-size` in screen CSS — only type tokens.

### 3.4 Space, radius

8px lattice, 4px half-step inside Technical clusters. Radii only from the six RR sizes: chips/tags `radius-sm`, buttons/inputs `radius-md`, cards `radius-lg`, modals/panels `radius-xl`, dots/avatars `radius-full` (fixes X-06). One page gutter token `--page-gutter` (24px desktop, 16px ≤768px) applied once on `#screen-mount`; screens add no outer padding of their own (fixes SH-07, X-09).

### 3.5 Theme switching

- Toggle: icon button at the far right of topbar row 1, RR `polarity` glyph, `aria-label="Switch to light theme"` / `"…dark theme"`, `aria-pressed`.
- State: existing `prefs.theme` (`dark` | `light` | `system`), default `system` (follows `prefers-color-scheme`). Mirrored to `localStorage` so the pre-paint script can read it.
- An inline script in `<head>` sets `data-theme` before CSS paints (no flash).
- Flip animates `background-color`, `color`, `border-color` over `dur-standard` `ease-hourglass`; instant under `prefers-reduced-motion`.

## 4. Shell

- **Topbar:** row 1 = screen title · count (Technical) · spacer · primary action · theme toggle. Row 2 (optional, one fixed height) = search · view switcher · filters. Filters that don't fit collapse into a "Filters" popover; nothing wraps. Topbar height is one of exactly two values (SH-03, IE-01). The stale-topbar bug is fixed by always claiming the topbar on every route outcome including errors (TD-06).
- **Search:** visible focus ring; `aria-label` always set; Ctrl+K / ⌘K focuses it; the hint shows `Ctrl K` or `⌘K` by platform (SH-04, SH-05).
- **Sidebar:** active item = `--signature-glow` background + `foreground-bold` text + signature icon; no rail, no shadow (SH-01). Empty footer removed (SH-02). "Task" nav item removed; the task route remains reachable from cards, rows and links (SH-09). Unused `.badge` slots removed.
- **Mobile drawer:** hamburger gets `aria-expanded` + `aria-controls`; Escape closes; focus moves into the drawer and returns on close; desktop collapse button hidden in drawer (SH-06).
- **Landmarks:** one `<main>`; task detail's inner `<main>` becomes a `<div>` (SH-08).
- **Brand:** League Spartan "TASKMASTER" wordmark + version in Technical; favicon square in signature. RR's own monogram is not used.

## 5. Shared components

New/rewritten in `css/components.css` + `css/components/*.css` and `js/components/*`. Screens consume these; they do not restyle them.

1. **Status / severity / priority** — `js/components/status.js` exports `statusMarker(kind, value)`, `severityMarker(value)`, `priorityMarker(value)`; one map per entity kind (label, shape, hue token). Shape + word, always.

   | Meaning | Shape | Hue |
   |---|---|---|
   | not started (todo, open, planned, candidate, parking-lot) | ○ | `foreground-subtle` |
   | in motion (in-progress, investigating, exploring, active) | ◐ | `signature` |
   | waiting on human (in-review) | ▲ | `color-warning` |
   | blocked | ◆ | `color-critical` |
   | complete (done, fixed, closed) | ● | `color-success` |
   | moved on (promoted, superseded) | → | `foreground-subtle` |
   | dropped (wontfix, shelved, archived) | ✕ | `foreground-subtle` |

   `in-review` is labelled "In review" everywhere; the Kanban column adds a whisper "waiting on you". Severity/priority: Critical ◆ `color-critical`, High ▲ `accent-orange`, Medium ● `color-warning`, Low ○ `foreground-subtle`; always full words (X-01, TD-01, IS-05, KB-09, EP-01, ED-03).
2. **Button** — `.btn` + `--primary | --secondary | --ghost | --critical`, `--sm`, `--icon`. One primary per view. Real `disabled` attribute. "Coming soon" controls are not rendered (EM-08, KB-10, X-08).
3. **Chip / filter toggle** — `<button aria-pressed>`; off state has a `border-default` border; single line, never wraps; zero-count options are disabled. Chip rows overflow into a "More" popover (KB-05, TB-04, SE-01, SE-02, IE-03, IS-07, BG-01).
4. **Fields** — input, select (native, `appearance: none` + RR chevron), textarea, checkbox, radio, segmented control; on `--bg-recessed` with `border-default`; labels bound with `for`/`id`; errors shown only after the field is touched or on submit (EM-03, EM-04, EM-05, KB-08, KB-12, ST-01). Estimate is a segmented `S | M | L | ND` (EM-06).
5. **Focus** — global `:focus-visible` outline rule; all `outline: none` removed (X-04).
6. **Interactive rows and cards** — cards and list rows are real `<a href="#/…">` (or `<button>` when not navigation). Nested controls inside them are siblings, not descendants (KB-02, KB-08, BG-03, TD-03, X-03). Sortable table headers contain a `<button>` and set `aria-sort` (TB-03).
7. **Modal shell** — one implementation used by entity (create/edit) and detail modals: focus trap, `aria-labelledby`, focus returned to the opener, Escape + backdrop close, frost backdrop, `radius-xl`, full-height sheet at ≤768px. Dirty state = current values differ from the initial snapshot; the discard prompt is an in-app confirm, not `window.confirm` (DM-01, DM-02, DM-03, EM-01, EM-07). The Ideas create modal uses the shared entity form (EM-02).
8. **Popover / menu** — one primitive: Escape, outside click, arrow keys, focus return; does not intercept clicks outside its box (KB-07).
9. **Text helpers** — `formatDate()` (relative, absolute in `title`) (TD-08); `truncate()` always sets `title` (X-07, TB-02, BG-02, IE-02, AR-02); `renderMarkdown()` = vendored `marked` + sanitizer, used for spec, plan, notes, patchnote, handover tldr (TD-02, TD-09); `.link-pill` styled, with spacing (TD-04, ID-03).
10. **State block** — `.tm-empty` pattern (Technical label → Narrator sentence → one action) for empty, not-found and error states; no raw API strings (X-12, TD-05, TD-06, ED-04).
11. **Icons** — RR 16-glyph pack as an inline SVG sprite (`viewer/vendor/icons.svg`), `currentColor`, 24px box, 2.25px stroke. Missing glyphs (bug, idea, alert, archive, kanban, table, epic, session, settings, menu) drawn to the same rules. Replaces Unicode glyphs.
12. **Right rail** — border-separated panel on `surface-raised`, no shadow (X-13).

## 6. Screens

**Kanban.** Card: line 1 = ID (Technical, no wrap, ellipsis + title) · priority marker · age; line 2 = title (full width, max 3 lines); line 3 = epic swatch + name · estimate · spec-state tag; branch line only when a branch exists. Remove `padding-right: 100px` (KB-01). Columns are `--bg-recessed` channels; "recent" card = `border-strong` border + "new" tag, no glow (KB-11). Phase strip shows every phase name with ellipsis + title; current phase is wider (KB-04). Epic filter: one line + "More"; a single count definition — open (non-done, non-archived) tasks — used in both chips and menu, labelled in the tooltip (KB-06). Group/Sort are labelled selects. At ≤768px a column switcher (tabs with counts, `role="tablist"`) shows one column at a time; Blocked is a tab (KB-03).

**Table.** `table-layout: fixed`; ID column sized to the longest ID, never truncated; title flexible; the table scrolls horizontally inside its container with ID + title sticky and an edge fade as the scroll cue (TB-01, TB-02). At ≤768px rows render as stacked cards (TB-05). Status/priority via shared markers (TB-06).

**Detail template** (task, issue, bug). Header: Technical line (ID · breadcrumb links · created) → title (`narrator-header`) → marker row. Body: sections with Technical labels and rendered markdown; empty sections collapse to one `foreground-subtle` line. Rail: "Relations" (links, dependencies, unblocks, blockers) plus Docs / Handovers / Issues panels only when non-empty. "‹ back" removed. Task: Document/Graph segmented control reflects the active view; empty graph shows a state block; graph frame is a bordered recess, no shadow or gradient (TD-07, TD-10). Issue detail reads `discovered` and `evidence` (ID-01). Bug detail: not-found state; actions disabled without a record (BD-01).

**Epics.** Fixed columns per row: swatch + title · lifecycle status marker · progress bar (always rendered) · closed/total. Topbar count + search. Stacked layout at ≤768px (EP-01–05).

**Epic detail.** Header + design text, then a status breakdown bar and the epic's tasks grouped by status as link rows. Progress from one function in `js/lib/epic-format.js` returning `{closed, done, archived, total, pct}`; label "35/55 closed · 25 done · 10 archived" (ED-01–03).

**Issues.** Card: line 1 = ID + severity marker; title full width; evidence clamped to 3 lines with expand. Columns min 280px, horizontal scroll on desktop, column switcher at ≤768px (IS-01, IS-02). Switcher label "VIEW" (IS-03). Staleness = a "stale 45d" tag on stale items only, no bar (IS-04). Column heads in Technical label voice (IS-06). Scroll regions focusable (IS-08).

**Bugs.** Row: ID · severity marker · title (2 lines) · status marker · age. Sort select in the toolbar (BG-01–04).

**Ideas.** Toolbar: search · status chips · "Tags" popover (searchable multi-select) · Show archived. Row: ID · title (2 lines) · status marker (none when unset) · first 3 tags + "+n" · age (IE-01–05).

**Sessions.** Chips are buttons; handover title primary, slug as Technical subline; badges ≥12px; tldr rendered once (SE-01–05).

**Archived.** Full-width groups, ellipsis + title, `h2` group headings (AR-01–03).

**Dashboard.** Summary strip: In progress · Waiting on you · Open issues · Open bugs — each a link to the filtered screen, from data already in the store (DB-03). Notes: `surface-raised` with a `pastel-*-subtle` tint and a full-perimeter pastel border (author hue: user `pastel-orange`, Claude `pastel-signature`), `foreground-default` text, static tilt kept, clamped with a fade and an expand button (DB-01, DB-02). "+N older" is a button. Clean-up rows are links (DB-04). Dead widget ids are dropped from the default `dashboard.layout`.

**Settings.** Three segmented controls: Theme (Dark / Light / System), Card density, Detail view (ST-01, ST-02).

## 7. Data and server fixes

- `GET /api/bugs/<id>` returns a single bug or 404; the list route matches `/api/bugs` exactly (`taskmaster/backlog_server.py`, BD-01).
- `js/screens/issue-detail.js` field names (ID-01).
- Epic progress single source (ED-01).
- Inline style literals in `js/main.js` and `js/screens/task-detail.js` move to classes (TK-06).

## 8. Testing

- `npm install` in `viewer/` (Playwright is declared but not installed today).
- **Unit (`node --test`):** status maps, epic progress, `formatDate`, modal dirty check, markdown sanitizer, theme resolution.
- **Style-rules test** (`viewer/tests/unit/style-rules.test.js`), fails on: `box-shadow`; `transform` inside a `:hover` rule; a `border-left` whose color is not a `--border-*` token; `outline: none`; hex/rgb literals or raw `font-size` outside `tokens.css`; `var(--x)` with no definition; custom properties defined outside `tokens.css`. Report-only in stage 1, enforcing from stage 5.
- **Playwright:** existing specs updated; new specs for theme toggle (persistence, no flash), modal focus trap and focus return, keyboard activation of cards/rows/chips/headers, mobile column switcher, Ctrl+K.
- **Accessibility gate:** axe-core on every route × both themes; zero `color-contrast`, `label`, `select-name`, `nested-interactive`, landmark failures; zero pointer-only targets (the audit's probe, kept as `viewer/tests/a11y.spec.js`).
- **Server:** pytest for the bug route (single, 404, list unchanged).
- **Visual verification:** after each stage a fresh-context agent re-runs the capture script in both themes at both widths and checks the result against this spec; the orchestrator reviews the screenshots.

## 9. Stages

Each stage is a task in its own worktree, reviewed, then merged locally `--no-ff` into `feat/viewer-reality-reprojection`. No pushes without explicit approval.

1. **Foundation** — tokens, themes, fonts, aliases, theme toggle + pre-paint script, shell (topbar, sidebar, drawer), page gutter, style-rules test (report-only).
2. **Shared components** — §5.
3. **Data fixes** — §7; independent, parallel with stage 1.
4. **Screens** — five parallel tasks after stage 2: (a) Kanban + detail modal; (b) Table + Epics + Epic detail; (c) Task/Issue/Bug detail; (d) Issues + Bugs + Ideas; (e) Sessions + Archived + Dashboard + Settings.
5. **Cleanup** — remove aliases, style-rules test enforcing, full re-audit in both themes, CHANGELOG entry.

## 10. Out of scope

- Thread board beyond the shared restyle (no thread data in the audit backlog); re-audit when data exists.
- RR `survivalist` theme.
- New features beyond the dashboard summary strip and the epic task list.
- Write-path UX not exercised by the audit (drag-and-drop, conflict banner) beyond restyling to tokens.
- Kanban keyboard reordering.

## 11. Amendments (2026-10-01, after plan 1 and the user's live review)

These override the sections they name.

- **§3.1 contrast.** "Passes by construction" was wrong for RR's values: signature as text fails AA on raised surfaces and on tints. Rule: signature-hued text uses the viewer role `--text-accent` only (dark `signature-vivid`, light `signature`); text on a signature-tinted fill is `foreground-bold`; text on a solid `signature-fill` is `--on-accent-fill` (dark `ground-0`, light `on-signature`). A unit test computes these ratios from the tokens. Known remaining misses in light: `--text-accent` on `--col-bg` (4.01:1) and on `surface-overlay` (3.15:1) — do not place accent text there.
- **§3.1 themes.** Dark values live on `:root` only (the theme attribute is on `<html>`).
- **§3.1 / §6 light surfaces.** In light theme cards are the lightest surface: `--card-bg: ground-0`, `--col-bg: ground-10`, hover `ground-5`. RR's light elevation relies on shadows, which this project bans. RR's own semantic tokens are unchanged; screens use `--card-bg`, not `--surface-raised`.
- **§3.5 theme.** The default is **dark** (user decision). A missing or unknown preference means dark; `system` is honoured only when explicitly stored. The toggle is an action button whose label names the result ("Switch to light theme"); it has no `aria-pressed`, and it is disabled until the saved preference has loaded.
- **§4 sidebar.** The active item uses `--signature-glow-strong` with a full-perimeter `--signature-dim` border.
- **§4 landmarks.** The nested `<main>` in task detail is removed in plan 2a (task document template), not plan 1.
- **§5.4 estimate.** The estimate is `S | M | L | <n>d` (a number of days), not "ND": three size buttons plus a days input.
- **§6 Dashboard notes.** Notes are solid coloured paper in both themes (user decision): user notes `pastel-orange`, Claude's `pastel-signature`, with a fixed dark ink; tilt and folded corner kept.
- **§5.10 brand / favicon.** The app icon is the "Board" mark: three board columns on an off-black tile, the last one Periwinkle; shipped as SVG and a multi-size `.ico`.
- **§9 stages.** Plan series is now: 1 foundation (done) → 2a task modals (shared modal shell, buttons, markers, fields, markdown, task document template) → 2b remaining shared components → 3 screens → 4 cleanup. The carry-forward list is in plan 1's verification report and is copied into each plan as it is written.
- **Tests.** Live-server Playwright specs write viewer prefs and refuse to run without `TM_LIVE_SPECS_OK=1`; all new UI specs are API-mocked.
