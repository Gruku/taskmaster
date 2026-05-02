# v3-polish-013 — Synthesis

> Output of the e2e walkthrough. 169 findings across 11 screens, distilled into cross-cutting themes, recommended new polish tasks, and re-prioritization of the existing 12.

## Top cross-cutting themes

### 1. Routing/data contracts are silently broken in places

- **Recap arrow nav** updates the URL to `#/recap/SES-0002` (path-segment) but the screen reads only `?id=` (query-string). Result: arrows change the URL, page doesn't follow. Only nav control on the screen, completely silent failure.
- **Sessions "New note"** sets `?new=1` but the param is never read — button advertises a primary-styled create flow that doesn't exist.
- **Recap receipts contradict narrative** — "0 · No changes" cards while the narrative describes Plan 3 closing 6 milestones. Either `getSnapshotDiff` is buggy or fixture snapshot IDs don't resolve, with no error or tell.
- **Issues blocks chip** silently returns 0 because `store.getTasksIndex` doesn't exist — the guard `getTasksIndex ? getTasksIndex() : {}` swallows the missing API.
- **Issues aging bar** computes from `discovered` but the field is `created` — bar always shows "Fresh".

**Theme:** the viewer trusts inputs from the store/router that aren't there and fails open without telling anyone. Needs a contract pass: assert in dev mode, surface in UI when assumptions break.

### 2. Self-XSS via unsanitized `${id}` in not-found innerHTML

Confirmed live on **lesson-detail** and **issue-detail** — crafted URLs like `/v3/#/issue/<img src=x onerror=...>` inject real HTML elements into the not-found message. Same class likely exists on task-detail, recap-by-id, and any other detail screen using template-literal `innerHTML` for missing-resource paths. Fix: switch all error-state renders to `textContent` for user-supplied identifiers.

### 3. Dead/stub button anti-pattern

Buttons that look interactive but do nothing are spread across most screens:

- **Sessions:** "New note" (dead route), Edit/Open buttons in handover rail (no listeners)
- **Lessons:** "+ Lesson" — `aria-disabled="true"` but renders at full opacity and primary style
- **Issues:** "+ Issue" same pattern; "Kanban" view toggle is a no-op (button flips pressed state, layout unchanged)
- **Task detail:** Edit and Archive — both `tmAction` calls without `onClick`, render but do nothing
- **Auto Mode:** side panels go silently empty when no session selected (no idle state)
- **Auto Mode:** `aria-disabled` Pause/Stop buttons suppress hover tooltips via `pointer-events: none`

**Theme:** the dashboard pattern of "show controls regardless of state" leaks everywhere. Either wire up or remove — affordances that lie about being interactive are worse than missing.

### 4. Pluralization bugs across screens

- Issues resolved header: `Resolved · 1 issues`
- Kanban filter badge: `1 filters · clear all`

Two confirmed; almost certainly more. **Need a shared `pluralize(n, 'issue', 'issues')` helper + sweep.**

### 5. Time/date format chaos

- Dashboard auto-strip: `4/30/2026, 5:30:00 PM` (raw `toLocaleString`)
- Kanban: four formats visible at once for v3-031 (`48h31m` / `2d` / `48:31:15` / `48:30:45`)
- Sessions timeline: `09:14 → 10:47` with no date context
- No shared time helper

**Need:** one canonical relative-time helper (`2d ago`, `48h31m running`) + one canonical absolute-time helper, applied everywhere.

### 6. Empty-state copy drift

8+ different phrasings on the dashboard alone:
*"Nothing newly unblocked." · "Nothing since you last looked." · "No prior session yet." · "No open issues." · "No core lessons yet." · "No commits yet." · "No agents running." · "Tests: 0/0 passed"*

Plus on other screens: *"— filtered out —"* (kanban, lying about a non-existent filter), and several screens with no empty-state at all (lessons/issues filtered to zero, sessions search to zero, auto side-panels). **Need:** an empty-state convention with copy guide and a shared `<EmptyState>` component, then sweep.

### 7. Accessibility gaps are systematic, not local

- **Filter chips** (lessons, issues, kanban) lack `role="button"` + `aria-pressed` — not keyboard-reachable
- **Card link semantics** (lessons, issues) use `<article role="link">` with manual keydown handler instead of `<a href="...">` — fragile in AT, breaks right-click → open in new tab
- **Heading hierarchy broken throughout** — widget banners, shelf headers, column headers all rendered as `<span>` inside `<header>`. Two `<h1>`s on lesson-detail (topbar literal "Lesson" + body title)
- **Table semantics destroyed** by `display: contents` on `<thead>/<tbody>/<tr>` — strips implicit `columnheader`/`row` roles
- **Hover-only affordances** (lesson reinforce button at `opacity: 0`) invisible to keyboard users
- **`text-transform: uppercase`** without ARIA-friendly readability checks

**Need:** a viewer-wide a11y sweep, not per-screen patching. Pick one screen as the audit-and-fix reference, then propagate the corrections.

### 8. CSS scoping accidents

- `--sev-critical/high/medium/low` defined inside `.issues { ... }` and not inherited by `.issue-detail` — severity label colors flatten to gray on the detail screen
- `td-page` / `td-page-A` classes added to shared `#screen-mount` by task-detail screens and never removed on cleanup — pollutes auto-mode and any screen navigated to next
- Phantom CSS variables (history of c949b4c, 4704964 fixes) suggest the screen-scoping convention isn't fully internalized

**Need:** lint or convention check that surface-stepping vars and severity vars are at `:root` scope; cleanup-on-unmount discipline for screen-specific `mountEl` classes.

### 9. Concept deltas validated (drives polish-009 + polish-010)

- **Issues** (polish-009): severity chips missing colored dots + count badges + "All" reset; aging bar always Fresh due to data field mismatch; column headers wrong typography (uppercase 12px vs concept's serif italic 14px + tagline + count); resolved shelf is single-column not 2-column grid
- **Lessons** (polish-010): filters-row missing `Scope:` label and per-category counts; sparkline missing terminal `<circle>` dot (concept's clear "latest point" anchor); search placeholder understates capability ("Search lessons…" vs concept's "Search lessons & anchors…"); chips not keyboard-accessible

These confirm and sharpen the existing high-priority tasks.

### 10. No-transform-motion rule violation (auto-mode)

`.spine-node--active .spine-node-circle` runs `animation: spine-active-pulse 1.6s ease-in-out infinite` with `transform: scale(1.11)`. Continuous geometric scale on a live element — explicit project rule violation per CLAUDE.md ("No motion on hover" extended to "no transform-based motion"). Switch to opacity or stroke-color pulse.

### 11. Mojibake on task-detail quoted text

`â€"` instead of `—` on codex review notes and handover quotes — UTF-8 file decoded as Latin-1 somewhere in the data path. **Localized to task-detail** (lesson-detail and issue-detail confirmed clean). Likely the response Content-Type or the file-read encoding for the specific subtree feeding quoted human text on task-detail.

### 12. Server fragility (test infrastructure)

Python's stdlib `HTTPServer` is single-threaded; the parallel-agent storm killed it. The local-test phase the user wants before release relies on this server. Switch to `ThreadingHTTPServer` (drop-in replacement) so parallel test traffic doesn't take it down. **This is infrastructure, not viewer — but it's a blocker for trustworthy local testing.**

### 13. Right-rail naming hides architectural divergence (polish-003 confirmed)

`.td-rail-h` (task-detail: always-visible, no close button) and `.rr-h` (sessions: overlay, dismiss-on-click + close button + Escape) are two **architecturally different** rail systems sharing one source file. The naming makes them look like a typo or near-variant; they aren't. Either rename to surface the distinction (`.td-rail-h` → `.tm-rail-pinned-h`, `.rr-h` → `.tm-rail-overlay-h`), or unify on one rail pattern and pick which screens get pinned vs overlay.

### 14. Recap is build, not rebuild (polish-001 scope change)

The current `/v3/#/recap` has **no picker** — only a single `‹` button in the topbar. Five concrete deficiencies for the brainstorm:
- No identity anchor (loaded recap's date is invisible everywhere)
- Linear-only navigation (can't jump from today to last Tuesday)
- Ambiguous directionality without a date anchor
- Density/gap legibility unclear
- Three picker shapes to evaluate: calendar grid · timeline spine · flat searchable list

**Promote v3-polish-001 from M to L** and reframe it as "Build recap picker" — there's nothing to rebuild.

---

## Recommended NEW polish tasks

| Proposed ID | Title | Pri | Est | Source |
|---|---|---|---|---|
| v3-polish-014 | Sanitize `${id}` in not-found innerHTML across detail screens (security) | **critical** | S | lesson-detail, issue-detail |
| v3-polish-015 | Fix recap routing contract (path-segment vs query-string) | high | S | recap |
| v3-polish-016 | Audit & fix dead/stub buttons across viewer (sessions, lessons, issues, task-detail, auto) | high | M | cross-cutting |
| v3-polish-017 | Pluralization helper + sweep ("1 issues", "1 filters") | low | S | issues, kanban |
| v3-polish-018 | Shared time-format helper + unify formats (4 formats on kanban; raw `toLocaleString` on dashboard) | medium | M | dashboard, kanban, sessions |
| v3-polish-019 | Empty-state copy convention + shared component | medium | M | dashboard, lessons, issues, sessions, auto |
| v3-polish-020 | Viewer-wide a11y sweep (chip roles, link semantics, heading hierarchy, table ARIA) | high | L | cross-cutting |
| v3-polish-021 | Fix mojibake on task-detail quoted text (UTF-8/Latin-1 charset) | medium | S | task-detail |
| v3-polish-022 | Migrate dev server to `ThreadingHTTPServer` (test-infra blocker) | high | S | infrastructure |
| v3-polish-023 | Hoist `--sev-*` CSS vars to `:root` (issue-detail labels flatten to gray) | medium | XS | issue-detail |
| v3-polish-024 | Cleanup `td-page` / `td-page-A` classes on task-detail unmount | medium | XS | task-detail → auto |
| v3-polish-025 | Fix `spine-active-pulse` `transform: scale()` motion-rule violation | medium | XS | auto |
| v3-polish-026 | Snapshot/diff data plumbing for recap receipts (currently always "0 · No changes") | high | M | recap |

**13 new tasks proposed.** Critical-bug (014, security) should land before any release work.

---

## Re-prioritization of existing v3-polish-001 → 012

| ID | Original | New | Rationale |
|---|---|---|---|
| v3-polish-001 | high · M | **high · L** + reframe as "Build recap picker" | There is no picker. Audit found 5 deficiencies feeding the brainstorm. |
| v3-polish-002 | medium · M | **low · M** | tm-card retrofit is pure consolidation, no user-visible bug. Defer behind security/a11y/dead-button work. |
| v3-polish-003 | medium · S | **medium · S** + reframe as "Rename or unify rail systems" | Confirmed at depth — naming hides architectural divergence. |
| v3-polish-004 | low · S | **medium · S** | Per-chip counts are now part of a larger pattern (concept deltas + kanban filter feedback). Bundle with 019 or polish-009/010. |
| v3-polish-005 | medium · S | **medium · S** | Test failures still real, no change. |
| v3-polish-006 | medium · S | **medium · S** | Auto-mode duplication confirmed at depth. No change. |
| v3-polish-007 | medium · M | **low · M** | Visual nit. Defer behind everything above. |
| v3-polish-008 | medium · S | **medium · XS** + reframe | Spine implementation is fine; the missing-spine perception is a data-state gap. Pair with the scale-motion fix (025). |
| v3-polish-009 | high · M | **high · L** | Concept-delta scope plus 3 real bugs (blocks chip dead, Kanban toggle no-op, aging bar always Fresh). Bigger than M. |
| v3-polish-010 | medium · M | **high · M** | Concept-delta is real and high-value. Promote priority. |
| v3-polish-011 | medium · M | **medium · M** | Dashboard pass already informed by 11 findings. No change. |
| v3-polish-012 | medium · M | **medium · M** | Token audit still relevant; CSS scoping accidents (theme 8) feed it. |
| v3-polish-013 | high · L | **DONE** | This audit. |

---

## Recommended sequencing for the polish phase

**Wave 1 — security and infrastructure (must land first, ~1 day total):**
- v3-polish-014 — sanitize id (critical security)
- v3-polish-022 — ThreadingHTTPServer (unblocks parallel local testing)
- v3-polish-023 — hoist sev vars (XS)
- v3-polish-024 — cleanup td-page classes (XS)
- v3-polish-025 — fix scale-motion (XS)

**Wave 2 — content/contract bugs (~3 days):**
- v3-polish-015 — recap routing
- v3-polish-026 — recap snapshot/diff plumbing
- v3-polish-005 — fix test failures (already small)
- v3-polish-021 — mojibake (small)
- v3-polish-016 — dead-button audit

**Wave 3 — cross-cutting design (~3 days):**
- v3-polish-017 — pluralization
- v3-polish-018 — time format
- v3-polish-019 — empty-state convention
- v3-polish-008 — auto-mode spine data-state

**Wave 4 — concept-delta polish (~5 days, parallelizable across designers):**
- v3-polish-009 — Issues alignment (large)
- v3-polish-010 — Lessons usability
- v3-polish-001 — Build recap picker (after brainstorm)
- v3-polish-006 — Auto-mode header slim

**Wave 5 — system + accessibility (~5 days):**
- v3-polish-020 — a11y sweep (large)
- v3-polish-012 — design tokens
- v3-polish-003 — rail rename/unify
- v3-polish-002 — tm-card retrofit
- v3-polish-007 — kanban gaps
- v3-polish-011 — dashboard pass
- v3-polish-004 — per-chip counts

Then **local testing phase** (the user's stated step before release).
