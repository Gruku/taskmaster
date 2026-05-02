# Task detail — `/v3/#/task/<id>`

> Part of v3-polish-013 end-to-end review. The polish-003 flag about `.td-rail-h` vs `.rr-h` is relevant here — Task Detail uses one right-rail system (always-visible inline column), Sessions uses a different one (overlay). Confirmed live below.

Walked v3-050, v3-009, v3-031, v3-051, and v3-DOES-NOT-EXIST (synthetic 404) on Variant A; toggled to Variant B briefly to verify reload behavior.

---

### bug — "Edit task" and "Archive task" topbar buttons are dead

Both `Edit` and `Archive` buttons render in the topbar but have **no click handlers**. Confirmed live: clicking `Edit` produces no modal, no nav, no state change, no console event. Looking at `task-detail-document.js`:

```js
const editBtn = tmAction({ icon: '✎', label: 'Edit', title: 'Edit task' });
const archiveBtn = tmAction({ icon: '✕', label: 'Archive', title: 'Archive task' });
topbar.append(seg, editBtn, archiveBtn);
```

`tmAction` accepts an `onClick`, but neither call passes one. Both buttons should either be hidden until wired, disabled with a "coming soon" affordance, or implemented. Currently they're affordances that lie about being interactive.

---

### bug — codex review note shows mojibake (`â€"` instead of em-dash)

The CONCERNS / WARN spec-review badge expands a serif quote that comes back from the API double-encoded:

> "Schema looks good; one concern â€" `hooks.jsonl` format varies by hook source..."

Same mojibake appears in the handover quote on v3-031:

> "Plan 6 (Auto Mode) closed 57/58 across 8 milestones. T58 is the manual visual checklist â€" needs user eyeballs."

The literal byte sequence `â€"` is UTF-8 `—` decoded as Latin-1 then re-encoded. Either the data file is mis-encoded on disk or the server is sending Latin-1 content with a UTF-8 charset header (or vice versa). Affects every quoted human text on the page.

---

### bug — codex note has invisible disclosure affordance

The spec-review badge (e.g. `CONCERNS`) is a clickable toggle that hides/reveals `.td-codex-note` (initially `display: none`, click → `display: block`). Confirmed working live. But:
- No chevron, no `▶`, no underline, no hover-cursor change to indicate it's clickable
- The note exists in DOM but is silently invisible — a user has no signal that there's review feedback to expand
- No keyboard activation — `<span>` with no `tabindex`, no `role="button"`, no `aria-expanded`

This is functionally a bug because important review-signal information is hidden behind an undiscoverable affordance.

---

### bug — `‹ back` is a `<span>`, not a button or link

```html
<span class="td-back">‹ back</span>
```

Has a `click → history.back()` listener but no `tabindex`, no `role`, no `aria-label`. Keyboard users cannot focus or activate it. Screen readers announce it as plain text. And `history.back()` itself is wrong on direct navigation — if a user opens a task URL in a fresh tab, "back" leaves the viewer entirely.

---

### inconsistency — `.td-rail-h` (task-detail) vs `.rr-h` (sessions) — polish-003 confirmed

Confirmed live. The two right-rail implementations in `right-rail.js` are architecturally different despite living in the same file:

| Dimension | Task-detail (`mountRightRail`) | Sessions (`RightRail` class) |
|---|---|---|
| Header class | `.td-rail-h` | `.rr-h` |
| CSS file | `task-detail.css` | `components.css` |
| Close button | **None** (confirmed: `railHasCloseButton: false`) | `✕` button in `.rr-h .actions`, wired via `bindRailClose` |
| Layout | Inline `<aside class="td-rail-mount">` always rendered in CSS grid | Overlay `<aside class="right-rail right-rail-{kind}">` appended to body on demand |
| Escape-to-close | No | Yes |

The same file exports both, the names look like variants of one system, but they're separate. The task-detail rail has no dismiss/collapse affordance — at the recorded viewport (2092 px) the rail steals a fixed 280 px column the user cannot reclaim.

---

### inconsistency — `Docs` panel duplicated between body and rail

Confirmed live: when `task.docs` is non-empty, the same docs render twice — once as `td-doc-chips` in the body via `renderDocsSection`, once as `td-panel-docs` in the rail via `panelDocs`. Identical entries (Spec / Runner / Hooks on v3-050), different visual treatments, both visible at once. Pick one location.

---

### inconsistency — status pill renders snake_case API value

Confirmed live: `<span class="td-status-pill">in_review</span>` and `in_progress`. The Kanban and Table screens (presumably) format these as `In Review` / `In Progress`. The detail page shows the raw API field. Should run through a humanizer or use a status map.

---

### inconsistency — date formats not normalized

Same gap called out on the dashboard. Confirmed live on v3-050:
- Meta row: `created 2026-04-26T11:00:00Z` (raw ISO)
- Dates section: `2026-04-26T11:00:00Z` + `6d ago` (relative is correct, absolute is raw)
- Handover ID: `2026-05-01-plan6-design-pass-handoff · ` (trailing `· ` because `kind` is empty — see separator bug below)

Adopt one human-readable format (`Apr 26, 2026` or `26 Apr 11:00 UTC`) and one relative format, used everywhere.

---

### bug — handover ID renders trailing separator when `kind` is empty

In `right-rail.js`:

```js
h('div', { class: 'mono td-handover-id' }, `${ho.id} · ${ho.kind || ''}`)
```

Confirmed on v3-031: handover with empty `kind` renders as `2026-05-01-plan6-design-pass-handoff · ` — visible orphan dot-space. Either skip the separator when `kind` is empty or default to a kind label.

---

### gap — Issues panel rows accept `onNavigate` but don't wire it

`panelIssues(issues, onNavigate)` accepts the navigate callback but the rendered `td-issue` rows don't bind a click handler — the parameter is dead. Issues in the rail are non-interactive, while Dependencies in the same rail (`panelDeps`) drill into the related task on click. Confirmed live: dep click on v3-050 → v3-009 worked correctly; issues would not.

---

### gap — activity list and blocker list have no CSS rules

`renderActivity` produces `<ul class="td-activity">` and `panelBlockers` produces `<ul class="td-blocker-list">`. Neither class appears in the CSS files — confirmed by reading `task-detail.css`. Lists render with browser-default bullets and left margin instead of the flush, compact styling used elsewhere on the page. Visible on v3-050's "Latest activity" section.

---

### gap — Completed cell renders label + em-dash for unfinished tasks

`renderDates` always renders all three date cells (Created / Started / Completed). For an in-review task, Completed shows `Completed / — /` — no relative time, no styling change. Either hide the Completed cell when null or replace with a more meaningful placeholder ("Not yet completed" or "Pending review"). Confirmed: `.completedAbs = '—'`.

---

### gap — non-existent task id shows bare error string with no recovery

Navigated to `/v3/#/task/v3-DOES-NOT-EXIST` to test. Result:

> `Could not load v3-DOES-NOT-EXIST: task v3-DOES-NOT-EXIST not found`

Plain text inside `.td-empty`, no styling, no "back to Kanban" link, no "did you mean..." search. The `if (!id)` branch in `task-detail.js` *does* render a styled empty state with kanban/table links — the not-found branch should do the same.

---

### friction — title duplicates lock state on locked tasks

Confirmed on v3-051:
- Lock banner: `🔒 locked by agent-codex-2`
- Title: `Locked: keyboard nav for kanban (in progress, do not touch)`

The `Locked:` prefix and the `(in progress, do not touch)` suffix are state metadata leaking into the title string. The lock banner already conveys this. Same finding flagged on the Dashboard's "Suggested next" widget — root data issue.

---

### friction — variant toggle does a full page reload

Confirmed live: clicking `Graph` button calls `location.reload()` after persisting the pref. Page fully re-bootstraps — sidebar re-mounts, polls restart, topbar flashes, all sub-screen state is lost. A 2-state toggle inside one screen should re-mount the screen content area in place, not reload the shell.

---

### friction — epic color hardcoded to `--epic-1`

In `renderChips`: `const epicColorVar = '--epic-1';` regardless of which epic the task belongs to. v3-050 (epic: `auto-mode`) and v3-009 (epic: `auto-mode`) both render the same swatch as a hypothetical task in epic `frontdoor` would. The epic chip becomes purely decorative — no visual differentiation between epics.

---

### friction — body column at 1498 px on a 2092 px viewport reads as a wall

The grid is `1fr var(--task-rail-w)` — body takes whatever's left after the 280 px rail. On a 2092 px viewport the body column is 1498 px, which is too wide for prose and code blocks (markdown line lengths exceed 200 chars). Either cap body width with `max-width: 76ch` (or similar measure-based unit) or use a 3-column grid (`gutter | body | rail`).

---

### polish — section headers use `<div>` not `<h*>` elements

Every body section header (`renderMdSection`, `renderDocsSection`, `renderActivity`, `renderDates`) is a `div.td-section-h`, not `<h2>` or `<h3>`. Same a11y gap flagged on dashboard. Screen-reader users cannot navigate the most content-rich page in the viewer by headings.

---

### polish — `‹ back` label tone

Lowercase, custom Unicode `‹`. Tone-incongruent with the rest of the topbar (Title Case actions: `Edit`, `Archive`, `Document`, `Graph`). Either `← Back` with sentence-case capitalization, or remove the back affordance entirely (the sidebar is always present).
