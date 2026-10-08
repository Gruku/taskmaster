<!-- User intent: rebuild both task modals (the card-click detail view and the Create/Edit form) on one shared, accessible, Reality Reprojection-styled modal shell — the first visible slice of the shared-components work, pulled forward at the user's request. -->

# Viewer × Reality Reprojection — Plan 2a: Task Modals

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One shared modal shell, and on it a rebuilt task detail modal and a rebuilt Create/Edit task form, correct in dark and light, usable by keyboard, with rendered markdown, one status language, and no native browser dialogs.

**Architecture:** A new `modal.js` owns everything every modal needs (overlay, focus containment, labelling, close paths, stacking, motion). `entity-modal.js` and `detail-modal.js` keep their public functions and their own logic (draft/validation; history/peek) but delegate the frame to the shell. The task document component is shared by the modal and the full page, so its template and `task-detail.css` are converted here; the full page benefits at the same time. Status and priority are rendered by a new `status.js` used through the existing field renderers.

**Tech Stack:** Vanilla JS ES modules, plain CSS on RR tokens, `node --test` + jsdom, Playwright with mocked APIs (`viewer/tests/mock-api.js`).

**Spec:** `docs/specs/2026-10-01-viewer-reality-reprojection-design.md` §5 (items 1, 2, 4, 5, 7, 9, 10) and §6 "Detail template" / "Detail modal". Audit findings: DM-01…03, EM-01, EM-03…08, TD-01…05, TD-08, TD-09, X-01 (task part), X-04, X-08 (task part).

**Depends on:** Plan 1 and its follow-up (tokens incl. `--text-accent`, `--on-accent-fill`, `--card-bg`, `--col-bg`; theme; shell; `icon()`; style-rules ratchet; mocked-API test setup).

## Global Constraints

- No `box-shadow`. No `transform`/`translate`/`scale`/`rotate` in any `:hover` rule. No colored left border. No `outline: none|0`. No italic. No text below 11px (11px only for uppercase labels). No named colours, hex or rgb literals outside `tokens.css`.
- Focus ring is the global one: `outline: 2px solid var(--border-focus); outline-offset: 2px`. Do not restyle it per component.
- Signature-hued text uses `--text-accent` only. Text on a signature-tinted fill is `--foreground-bold`. Text on a solid `--signature-fill` is `--on-accent-fill`.
- Cards, panels and modal surfaces use `--card-bg` / `--surface-overlay`, never `--surface-raised` directly and never a legacy alias.
- Status, severity and priority are a shape plus a word; the shape carries the hue, the word is a `foreground` colour.
- Every CSS file this plan creates or rewrites is added to `ENFORCED` in `viewer/tests/unit/style-rules.test.js` and passes.
- No `window.confirm`, `window.alert` or `window.prompt` in any code path this plan touches.
- No `innerHTML` with task/user data except through `renderMarkdown()` (sanitised).
- Every animation has a `prefers-reduced-motion` fallback (the global rule in `tokens.css` covers transitions and keyframes; do not defeat it).
- Every new file starts with a 1–3 line `User intent:` header.
- Tests never touch a live backlog server. Mocked specs call `mockApi` before `page.goto` and assert no unmocked writes.
- Work in `C:/Users/gruku/Files/Claude/taskmaster/.worktrees/viewer-rr`. No pushes, no amends, never `git add -A`.

## Review Focus

1. **Modal opened from a modal** (Edit from the detail modal; the discard confirm from the form): focus must stay in the topmost one, Escape must close only the topmost, and closing it must return focus to the control that opened it — not to `<body>`, not to the page behind. → Task 1 tests.
2. **Opener removed from the DOM while the modal is open** (the board re-renders on its 3 s poll): closing must not throw and must put focus somewhere sensible (the nearest surviving focusable ancestor, else the screen mount). → Task 1 test.
3. **Markdown with hostile content** in a task's notes (`<img onerror>`, `javascript:` links, `<script>`, a link with `target`, raw HTML tables): must render inert. → Task 3 test.
4. **A task whose fields are null, missing, or of the wrong type** (`depends_on: null`, `docs` as an object, `estimate: 3`, unknown status): the form must open not-dirty, and the detail modal must render without throwing. → Tasks 3 and 4 tests.
5. **Very long content** (a 140-character unbroken title, a 5,000-line plan, 40 dependencies): the modal must scroll its body only, keep header and footer visible, and never widen past the viewport at 390px. → Task 5 Playwright test.

---

### Task 1: Modal shell and confirm dialog

**Files:**
- Create: `viewer/js/components/modal.js`, `viewer/css/components/modal.css`, `viewer/tests/unit/modal.test.js`, `viewer/tests/modal.mock.spec.js`
- Modify: `viewer/index.html` (one `<div id="modal-host"></div>` replacing `#entity-modal-host` and `#detail-modal-host`; one stylesheet link replacing `entity-modal.css` and `detail-modal.css` once Tasks 4–5 land — in this task add `modal.css` and keep the old ones), `viewer/tests/unit/style-rules.test.js` (ENFORCED += `components/modal.css`)

**Interfaces:**
- Produces:
  ```js
  // Opens a modal on top of any already-open one. Returns a handle.
  openModal({
    title,                 // string | Node — rendered in the header; required for the accessible name
    eyebrow,               // optional string above the title (Technical label voice), e.g. a task id
    size = 'md',           // 'sm' (420px) | 'md' (640px) | 'lg' (960px)
    className,             // extra class on the dialog element
    onRequestClose,        // () => boolean | Promise<boolean> | void — return false to veto; default: close
    opener,                // Element to return focus to; default: document.activeElement at open
    initialFocus,          // (dialog) => Element | null; default: first focusable in body, else the close button
  }) → {
    dialog,                // the role="dialog" element
    header, actions,       // header row; `actions` is an empty slot left of the close button
    body, footer,          // scrollable body; footer is hidden while empty
    setTitle(title), setEyebrow(text),
    requestClose(),        // runs onRequestClose, then close() unless vetoed
    close(),               // closes immediately, restores inert state and focus; idempotent
    isTop(),               // true when this is the topmost open modal
    onClosed(fn),          // fn runs once after close
  }
  confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', tone = 'default' | 'critical' }) → Promise<boolean>
  openModalCount() → number
  ```
- Behaviour contract (each line is a test):
  1. The dialog has `role="dialog"`, `aria-modal="true"`, and `aria-labelledby` pointing at the title element's id (unique per modal).
  2. While any modal is open, `.shell` is `inert` and `body` has class `modal-open`; both are restored when the last modal closes — and only then.
  3. Stacking: a second modal makes the first one's dialog `inert`; closing the second removes it. `isTop()` reflects this.
  4. Tab and Shift+Tab cycle inside the topmost dialog (wrap at both ends), independent of `inert` support.
  5. Escape calls `requestClose()` on the topmost modal only. A click on the overlay outside the dialog does the same; a drag that starts inside the dialog and ends on the overlay does not.
  6. `onRequestClose` returning `false` (or a promise of it) keeps the modal open.
  7. On close, focus returns to `opener` if it is still connected and focusable; otherwise to the first focusable element of `#screen-mount`; never to `<body>` when a focusable target exists. Never throws.
  8. `close()` twice is a no-op; `onClosed` callbacks run once.
  9. `confirmDialog` resolves `true` on confirm, `false` on cancel, Escape, or overlay click; focuses the cancel button initially (the confirm button when `tone` is not `critical`); the `critical` tone renders the confirm button with the `btn--critical` class.
  10. The close button has `aria-label="Close"` and renders `icon('dismiss')`.

- [ ] **Step 1: Write the unit tests** — `viewer/tests/unit/modal.test.js`, jsdom, one test per contract line 1–3, 6–10 (jsdom has no layout: test Tab wrapping and overlay-drag in Playwright instead). Set up a DOM with `<div class="shell"><button id="opener">o</button><section id="screen-mount"><a href="#x" id="fallback">f</a></section></div><div id="modal-host"></div>`. For line 7 include the three cases: opener connected; opener removed before close; opener removed and no fallback (focus on `document.body`, no throw).
- [ ] **Step 2: Run** `env --chdir=<worktree> node --test viewer/tests/unit/modal.test.js` — Expected: FAIL, module not found.
- [ ] **Step 3: Implement `modal.js`.** Keep a module-level stack array. Build the DOM with `document.createElement` / the shared `h()` from `viewer/js/util/h.js` (no `innerHTML`). The focus-return and "first focusable" logic lives in small exported pure helpers (`focusableIn(root)`, `resolveFocusTarget(opener)`) so they are unit-testable.
- [ ] **Step 4: `modal.css`** (tokens only):
  - Overlay: `position: fixed; inset: 0; background: var(--overlay-bg); backdrop-filter: blur(var(--frost-blur-light)); display: grid; place-items: start center; padding: var(--space-2xl) var(--space-md);` z-index above the mobile drawer (80) and below the conflict banner (90): use 85, stacked modals in DOM order.
  - Dialog: `background: var(--surface-overlay); border: 1px solid var(--border-default); border-radius: var(--radius-xl); max-height: calc(100vh - 2 * var(--space-2xl)); display: flex; flex-direction: column; overflow: hidden;` widths by size class; `width: min(<size>, 100%)`.
  - Header: `padding: var(--space-md) var(--space-lg); border-bottom: 1px solid var(--border-subtle); display: flex; align-items: flex-start; gap: var(--space-md);` title in Declaration voice (`--font-declaration`, weight `--font-declaration-weight`, `--size-declaration-h4`, uppercase, `--tracking-tight`, colour `--foreground-bold`), eyebrow in Technical label voice (`--font-technical`, 800, `--size-technical-label`, uppercase, `--tracking-ultra`, `--foreground-subtle`). A dialog with class `modal--plain-title` renders the title in Narrator voice instead (`--font-narrator`, 700, `--size-narrator-large`, no uppercase) — used by the detail modal, whose title is a long sentence.
  - Body: `padding: var(--space-lg); overflow: auto; flex: 1; min-height: 0;`
  - Footer: `padding: var(--space-md) var(--space-lg); border-top: 1px solid var(--border-subtle); background: var(--bg-recessed); display: flex; align-items: center; gap: var(--space-sm);` hidden with `:empty`.
  - Entrance: dialog `@keyframes` from `opacity: 0; transform: translateY(var(--space-md))` to rest over `var(--dur-macro) var(--ease-bell)`; overlay fades over `var(--dur-standard) var(--ease-hourglass)`.
  - ≤768px: overlay padding 0; dialog `width: 100%; height: 100%; max-height: 100%; border-radius: 0; border: 0;` header gets `padding-top: max(var(--space-md), env(safe-area-inset-top))`, footer the matching bottom inset.
  - `body.modal-open { overflow: hidden; }`
- [ ] **Step 5: Playwright spec** `viewer/tests/modal.mock.spec.js`: a tiny harness page is not available, so drive the shell through `page.evaluate(() => import('/js/components/modal.js').then(m => { window.__m = m; }))` on `#/settings`, then: Tab wraps forward and backward (contract 4); Escape closes only the top of two (5); overlay click closes, inside-to-overlay drag does not (5); `.shell` is inert while open and a click on the sidebar does nothing (2); at 390×844 the dialog fills the viewport; computed `box-shadow` is `none` on overlay and dialog in both themes; the `reduced-motion` media emulation yields an animation duration ≤ 1ms.
- [ ] **Step 6: Run** unit + mocked suites — Expected: PASS. Style-rules: `components/modal.css` enforced, 0 violations.
- [ ] **Step 7: Commit** — `feat(viewer): shared modal shell with focus containment, stacking and an in-app confirm`

---

### Task 2: Buttons and the status / priority markers

**Files:**
- Create: `viewer/css/components/button.css`, `viewer/js/components/status.js`, `viewer/css/components/status.css`, `viewer/tests/unit/status.test.js`
- Modify: `viewer/index.html` (two stylesheet links), `viewer/tests/unit/style-rules.test.js` (ENFORCED += both CSS files)

**Interfaces:**
- Produces (CSS): `.btn` (+ `.btn--primary | --secondary | --ghost | --critical`, `.btn--sm`, `.btn--icon`). Spec per RR `Button` (`docs/specs/assets/reality-reprojection-2026-10-01/components/Button.md` and `.btn` in `components/bundle.css`), minus shadows and hover lift:
  - base: `display: inline-flex; align-items: center; justify-content: center; gap: var(--space-xs); padding: var(--space-xs) var(--space-md); min-height: 32px; border: 1px solid transparent; border-radius: var(--radius-md); font-family: var(--font-declaration); font-weight: var(--font-declaration-weight); font-size: var(--size-technical-default); letter-spacing: var(--tracking-wide); text-transform: uppercase; line-height: var(--leading-tight); cursor: pointer; transition: background-color, border-color, color` at `var(--dur-standard) var(--ease-hourglass)`.
  - primary: `background: var(--signature-fill); color: var(--on-accent-fill);` hover `background: var(--signature-fill-hover)`.
  - secondary: `background: transparent; border-color: var(--border-strong); color: var(--foreground-bold);` hover `background: var(--ground-15)`.
  - ghost: `background: transparent; color: var(--foreground-default);` hover `background: var(--ground-15); color: var(--foreground-bold)`.
  - critical: `background: var(--color-critical-bold); color: var(--ground-100)` in dark / verify ≥ 4.5:1 in both themes with the contrast unit test from the follow-up (`viewer/tests/unit/` — extend it with the button pairs); if a pair fails, use the nearest passing RR token and report it.
  - `:disabled` → `opacity: 0.4; cursor: not-allowed;` and no hover change. `:active` → background one ground step deeper, no transform.
  - `.btn--sm`: `padding: var(--space-micro) var(--space-sm); min-height: 24px; font-size: var(--size-technical-small);` `.btn--icon`: square 32px (24px with `--sm`), padding 0.
- Produces (JS), `viewer/js/components/status.js`:
  ```js
  export const TASK_STATUS;       // { todo, 'in-progress', 'in-review', blocked, done, archived } → { label, shape, tone }
  export const PRIORITY;          // { critical, high, medium, low } → { label, shape, tone }
  export function statusMeta(kind, value)   // kind: 'task' (others added in later plans); unknown value → { label: String(value || '—'), shape: '○', tone: 'neutral' }
  export function priorityMeta(value)
  export function statusMarker(kind, value) → HTMLElement   // <span class="marker marker--<tone>"><span class="marker__shape" aria-hidden="true">●</span><span class="marker__word">Done</span></span>
  export function priorityMarker(value) → HTMLElement
  ```
  Task status table (spec §5.1): todo ○ neutral "Todo"; in-progress ◐ accent "In progress"; in-review ▲ warning "In review"; blocked ◆ critical "Blocked"; done ● success "Done"; archived ✕ neutral "Archived". Priority: critical ◆ critical; high ▲ orange; medium ● warning; low ○ neutral — always the full word.
- `status.css`: `.marker { display: inline-flex; align-items: center; gap: var(--space-micro); font-family: var(--font-technical); font-weight: var(--font-technical-weight); font-size: var(--size-technical-small); letter-spacing: var(--tracking-wide); color: var(--foreground-default); white-space: nowrap; }` `.marker__shape` colour by tone: neutral `--foreground-subtle`, accent `--text-accent`, warning `--color-warning`, critical `--color-critical`, success `--color-success`, orange `--accent-orange`. The word never takes the tone colour.

- [ ] **Step 1: Unit tests** (`status.test.js`, jsdom): every task status and priority returns the table's label/shape/tone; unknown, `null`, `undefined`, and a number do not throw and return the neutral fallback with the value as label; `statusMarker` builds the structure above with `textContent` (assert a value like `<img onerror=x>` appears as text, with no `img` element); the shape span is `aria-hidden`.
- [ ] **Step 2: Run** — Expected: FAIL (module missing). **Step 3: Implement** both files and the CSS. **Step 4: Run** unit suite — PASS; style-rules 0 violations for both CSS files.
- [ ] **Step 5: Commit** — `feat(viewer): button family and shape-plus-word status and priority markers`

---

### Task 3: Fields, labels, markdown and dates

**Files:**
- Rewrite: `viewer/css/components/edit-fields.css` (tokens only; ENFORCED)
- Create: `viewer/js/components/edit/fields/estimate-field.js`, `viewer/tests/unit/estimate-field.test.js`, `viewer/tests/unit/markdown-safety.test.js`
- Modify: every renderer in `viewer/js/components/edit/fields/` (accept an `id` and apply it to the focusable control; accept `describedBy`), `fields/md-field.js` (`read` renders through `renderMarkdown`), `fields/enum-select.js` (`read` uses a marker when the field spec carries `marker: 'status' | 'priority'`), `forms/task-form.js` (status/priority get `marker`; estimate uses `EstimateField`; fields get a `group`), `viewer/js/components/markdown.js` (hardening), `viewer/js/lib/time.js` (`formatStamp`)

**Interfaces:**
- Consumes: `statusMarker`, `priorityMarker` (Task 2).
- Produces:
  - Renderer contract addition: `edit({ id, describedBy, … })` sets `id` on the control that should receive the label's click and `aria-describedby` when given. `ChipInput` and `RelationPicker` put it on their inner text input.
  - `EstimateField` renderer: value is `'S' | 'M' | 'L' | '<n>d'` (n a positive integer) or `null`. `edit` renders a three-button segmented control (S, M, L; `aria-pressed`) plus a number input labelled "days"; choosing a size clears the days, typing days clears the size. `coerce`, `validate` (rejects `'0d'`, `'-1d'`, `'XL'`, `'3 d'`; accepts `'s'` by normalising to `'S'`), `read`.
  - Task schema fields gain `group: 'basics' | 'tracking' | 'relations' | 'content'`: basics = title, status, priority, epic, phase, estimate; tracking = stage, sub_repo, branch, worktree, release; relations = depends_on, docs, anchors; content = description, specification, plan, notes, review_instructions, patchnote. Estimate's label becomes "Estimate".
  - `formatStamp(iso, now?) → { text, title }`: `text` is the relative form from the existing `formatRelative`, `title` the absolute form from `formatAbsolute`; a missing or unparsable input gives `{ text: '—', title: '' }`.
  - `renderMarkdown` additionally: strips `on*` attributes (already implied by the allow-list — assert it), drops `href`/`src` whose scheme is not `http`, `https`, `mailto` or a relative/`#` reference, removes `img` entirely (no remote loads from task text), and adds `rel="noopener noreferrer"` and `target="_blank"` to absolute links.
- `edit-fields.css` essentials: inputs, selects, textareas — `background: var(--bg-recessed); border: 1px solid var(--border-default); border-radius: var(--radius-md); color: var(--foreground-bold); padding: var(--space-xs) var(--space-sm); min-height: 32px; font-size: var(--size-narrator-small);` hover `border-color: var(--border-strong)`; invalid (`[aria-invalid="true"]`) `border-color: var(--color-critical)` plus the error text (never colour alone); native `select` gets `appearance: none`; its arrow is NOT a background data-URI (that needs a colour literal). `EnumSelect.edit` returns a `<span class="ef-select">` wrapper holding the `<select>` and an absolutely positioned `icon('chevron', { size: 16 })` rotated 90° by a static transform, `pointer-events: none`. The wrapper exposes the control as `wrapper.control`; update the callers that assumed `edit()` returns the control itself (`inline-field.js`, `entity-modal.js`, tests) to use `el.control ?? el` for focus, `id` and `disabled`. Textarea `min-height: 6lh; resize: vertical; font-family: var(--font-technical); font-size: var(--size-technical-default);`. Chips and the dropdown: `--surface-overlay`, 1px `--border-default`, no shadow. Read-mode editable affordance: `cursor: text; border-radius: var(--radius-sm);` hover `background: var(--ground-15)`. Placeholder / "no content" text: `--foreground-subtle`, not italic.

- [ ] **Step 1: Tests.** `estimate-field.test.js` (coerce/validate table above; segmented/days mutual exclusion in jsdom; `aria-pressed`). `markdown-safety.test.js` (jsdom + the vendored `viewer/vendor/marked.min.js` loaded onto `window`): for each of `<img src=x onerror=alert(1)>`, `[a](javascript:alert(1))`, `<script>alert(1)</script>`, `<a href="data:text/html,x">d</a>`, `<iframe src=x>`, `<p style="color:red" onclick="x()">t</p>` assert the output contains no `script`/`iframe`/`img` element, no `on*` attribute, no `style` attribute, and no `javascript:`/`data:` URL; assert a GFM pipe table and `##` heading render as `table` and `h2`; assert an `https` link gets `rel` and `target`. Extend the existing renderer tests (`md-field.test.js`, `enum-select.test.js`, `text-field.test.js`, …) for `id`/`describedBy`, markdown read mode, and marker read mode. `formatStamp` cases in the existing time test file.
- [ ] **Step 2: Run** — Expected: new tests FAIL. **Step 3: Implement.** **Step 4: Run** unit suite — PASS (update existing assertions that pinned the old `<br>` read output or the old estimate label; list each in the report).
- [ ] **Step 5: Commit** — `feat(viewer): labelled token-styled fields, estimate picker, safe rendered markdown, date stamps`

---

### Task 4: Create/Edit task form on the shell

**Files:**
- Rewrite: `viewer/js/components/edit/entity-modal.js` (keep the export `openEntityModal({ schema, mode, initialEntity, onSave, onCancel, onClose })` and its return value), `viewer/css/components/entity-modal.css` (tokens only; ENFORCED)
- Modify: `viewer/js/components/edit/task-actions.js` (only if the modal's contract needs it), `viewer/tests/unit/entity-modal.test.js`, `viewer/tests/unit/task-actions.test.js`
- Create: `viewer/tests/task-form.mock.spec.js`

**Interfaces:**
- Consumes: `openModal`, `confirmDialog` (Task 1); `.btn` (Task 2); renderer `id` contract, field `group`, `EstimateField` (Task 3).
- Behaviour:
  1. Title: "Create task" / "Edit task" (Declaration voice, upper-cased by CSS); in edit mode the eyebrow is the task id.
  2. Layout: four groups with Technical-label headings ("Basics", "Tracking", "Relations", "Content"); basics and tracking are a two-column grid (`repeat(2, minmax(0, 1fr))`, one column ≤768px; Title spans both); each Content field is a collapsible section (a `<button aria-expanded>` heading), expanded when it has content or when it is `description` in create mode.
  3. Every control has a bound `<label for>`; required fields show "required" in `--foreground-subtle` beside the label, not an asterisk in a hue.
  4. **Dirty** is computed from a snapshot: `snapshot = coerce(initialEntity[key])` per field at open, compared (deep, order-sensitive for arrays) with `coerce(draft[key])`. Opening and closing an untouched form, including focusing and blurring every field, is never dirty (fixes EM-01). `null`, `undefined`, `''`, `[]` and `{}` are equal to each other for this purpose.
  5. Closing (Escape, overlay, Cancel, close button) when dirty asks `confirmDialog({ title: 'Discard changes?', message: 'Your edits to this task will be lost.', confirmLabel: 'Discard', cancelLabel: 'Keep editing', tone: 'critical' })`; when not dirty it closes immediately.
  6. Validation messages appear per field only after that field was blurred once or after a save attempt; never on open (fixes EM-05). The message is linked with `aria-describedby` and the control gets `aria-invalid="true"`.
  7. Save is a `.btn--primary`, enabled whenever the form is dirty and not saving. Pressing it with invalid fields shows all messages, moves focus to the first invalid control, and shows a summary "1 field needs attention" / "3 fields need attention" in the footer (`role="status"`). Ctrl/⌘+Enter anywhere in the form triggers Save.
  8. While saving: button label "Saving…", all controls `disabled`, close paths ignored. A server error is shown in the footer summary (`role="alert"`) and the form stays open and editable.
  9. Footer: summary left, `Cancel` (`.btn--secondary`) and `Save` right.

- [ ] **Step 1: Tests.** Unit (`entity-modal.test.js`, jsdom): contract lines 4 (matrix: untouched; focus+blur every field; type then restore the original text; `depends_on: null` vs `[]`; `docs` object untouched; `estimate: 3` (number) opens without throwing and is not dirty), 6, 7 (summary grammar for 1 and 3), 8 (server error keeps the form open). Mocked Playwright (`task-form.mock.spec.js`): open Create from the Kanban "+ Task" action → Escape closes with no dialog of any kind (assert no `dialog` event fired on the page and no `.modal` remains); type a title → Escape shows the in-app confirm → "Keep editing" returns focus to the title input → Escape → "Discard" closes; labels: clicking each label focuses its control; Tab order follows visual order and never leaves the dialog; Save with an empty title focuses the title and shows the summary; a successful save sends one POST with the typed values and closes (mock `POST /api/tasks` explicitly); axe on the open form in both themes: zero `color-contrast`, `label`, `select-name`, `aria-*` violations inside the dialog; 390×844: the form is one column and Save/Cancel are visible without scrolling the page.
- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** Keep all draft/validation/save logic in `entity-modal.js`; it must not create its own overlay, key listeners or host.
- [ ] **Step 4: Run** unit + mocked — PASS. `grep -rn "window.confirm\|confirm(" viewer/js/components/edit` → no match in `entity-modal.js`.
- [ ] **Step 5: Commit** — `feat(viewer): task create/edit form on the shared modal — grouped, labelled, honest dirty state`

---

### Task 5: Task document template and the detail modal

**Files:**
- Rewrite: `viewer/js/components/detail-modal.js` (keep `openDetailModal({ kind, id })` and the history contract), `viewer/css/screens/task-detail.css` (tokens only; ENFORCED), delete `viewer/css/components/detail-modal.css` and its link once nothing uses its classes
- Modify: `viewer/js/components/task-detail-document.js`, `viewer/js/components/right-rail.js`, `viewer/js/components/link-pills.js` (+ its CSS in `task-detail.css`), `viewer/js/lib/open-detail.js` (selector for "inside an open modal"), `viewer/index.html`, existing unit tests for these modules, `viewer/tests/detail-modal.spec.js` (live-suite spec: update selectors, do not run)
- Create: `viewer/tests/task-detail.mock.spec.js`

**Interfaces:**
- Consumes: `openModal` (Task 1), `.btn`, markers (Task 2), markdown/`formatStamp`/field read modes (Task 3), `openTaskEditModal` (Task 4).
- Task document template (same component for page and modal; `chrome: 'page' | 'embedded'`):
  1. **Meta line** (Technical, `--foreground-subtle`): id (click copies; a real `<button>`), then links `Tasks` → `#/kanban`, epic → `#/epic/<id>`, phase; then `created <formatStamp>` with the absolute time as `title`. No "‹ back" element in either chrome.
  2. **Title**: `h1` on the page, `h2` in the modal; Narrator header voice (`--font-narrator`, 700, `--size-narrator-large`); inline-editable as today.
  3. **Marker row**: status marker and priority marker (inline-editable through `EnumSelect` with `marker`), estimate, epic swatch + name, then branch / worktree / release / sub-repo as Technical tags (branch and worktree are copy `<button>`s with `icon('copy')`).
  4. Lock banner, spec-review block, gate pipeline, merge ladder: restyled to tokens, same content.
  5. **Sections** (Technical label heading, then body): Docs, Specification, Plan, Notes, Review instructions (in-review only), Latest activity, Patchnote (done only), Linked bugs, Dates. Markdown bodies render through `renderMarkdown` in `.md-body`. Empty editable sections collapse into one line at the end of the section list: "Empty: Specification · Plan" where each name is a `<button>` that expands that section into edit mode (fixes TD-05).
  6. **Dates** use `formatStamp`: relative text, absolute in `title`; no raw ISO strings.
  7. **Rail**: one "Relations" panel combining Links (link pills), Depends on, Unblocks, Blockers — each sub-list only when non-empty, with the status marker for each related task; Docs / Handovers / Issues panels only when non-empty. When everything is empty the rail is omitted and the body takes the full width. Handover quotes render through `renderMarkdown`, no serif, no italic, no surrounding quote marks.
  8. Exactly one `<main>` in the document: the body container is a `<div class="td-body">` (fixes SH-08).
  9. Link pills: `<a class="link-pill">` with a Technical label, a space, and the id; `--text-accent` only on `--card-bg`/page ground, otherwise `--foreground-bold` with an underline.
- Detail modal (`detail-modal.js`), on `openModal({ size: 'lg', className: 'modal--plain-title' })`:
  1. Eyebrow = entity id; title = task title (epic name for epics); the document body does NOT repeat the title in embedded chrome (the title's inline-edit host moves into the modal header for tasks).
  2. Header actions: `Edit` (`.btn--secondary .btn--sm`, tasks only → `openTaskEditModal`, stacked on top), `Open full` (`.btn--ghost .btn--sm` with `icon('external')`, a real link to the full route), then the shell's close button.
  3. Body: the document in `chrome: 'embedded'`, with the shell's body padding; at ≥960px dialog width the rail sits beside the body, otherwise below it.
  4. History contract unchanged: opening pushes one entry; Escape/overlay/close go through `history.back()`; `popstate` and `hashchange` close; "Open full" replaces the entry and navigates. `onRequestClose` is wired so the shell's Escape/overlay paths call `history.back()` rather than closing twice.
  5. Peeking a linked task inside the modal swaps content in place, moves focus to the dialog title, and keeps one history entry.
  6. Loading and error states use the `.tm-empty` state block; the error state offers "Open full" and never prints a raw API string.
  7. Focus returns to the card or link that opened the modal (the interceptor passes the clicked element as `opener`).

- [ ] **Step 1: Tests.** Unit (jsdom, existing `task-detail-document.test.js` + `right-rail.test.js` extended): template lines 1, 3, 5 (empty-section line lists exactly the empty sections; clicking a name opens the editor), 6, 7 (rail omitted when all empty; Relations sub-lists), 8 (no `main` element produced), and a task with null/missing/wrong-typed fields renders without throwing. Mocked Playwright (`task-detail.mock.spec.js`) with a rich task fixture (markdown with a table and headings in notes, two dependencies, one handover, a branch): on the Kanban, click a card → modal opens, `aria-labelledby` resolves to the task title, the title text appears exactly once in the dialog, there is no element with text "‹ back", markdown table renders as `table`, status marker for a `done` task shows "Done" with the success shape and for `in-progress` shows a different shape and word; `Edit` opens the form on top, Escape closes only the form, focus returns to `Edit`; Escape again closes the modal and focus returns to the card; browser Back closes the modal; "Open full" navigates to `#/task/<id>` and the full page shows the same template with an `h1`; peek a dependency → content swaps, one history entry (one Back closes); long-content fixture (140-char unbroken title, 5,000-line plan, 40 dependencies) at 1440×900 and 390×844: dialog never wider than the viewport, header and (if any) footer stay visible while the body scrolls; axe inside the dialog in both themes: zero `color-contrast` and zero `nested-interactive`; the page has exactly one `main`.
- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** Convert `task-detail.css` rule by rule to tokens while restyling to the template; remove dead rules (`.td-back`, serif/italic rules, shadow/gradient recess) — the graph variant's rules are converted to tokens with no layout change (its redesign is a later plan); the graph frame becomes a bordered `--bg-recessed` box.
- [ ] **Step 4: Run** unit + mocked — PASS; style-rules: `screens/task-detail.css` and `components/entity-modal.css` enforced with 0 violations; `components/detail-modal.css` no longer exists.
- [ ] **Step 5: Commit** — `feat(viewer): task document template and detail modal on the shared shell`

---

### Task 6: Verification

**Files:**
- Create: `viewer/tests/tools/capture-modals.mjs` (mocked, static-served — never a live server)

- [ ] **Step 1:** A script that serves the static viewer the way `playwright.mock.config.js` does, mocks every `/api/**` call with fixtures (a board with ~12 cards across statuses; the rich task; the long-content task; an empty task with every optional field null), and captures in dark and light at 1440×900 and 390×844: detail modal (rich, empty, long, error state), detail modal with the edit form stacked, create form (untouched, with validation errors after a save attempt, saving state), the discard confirm, and the full task page for the rich and empty tasks. Output directory is an argument; nothing is committed but the script.
- [ ] **Step 2:** Run it; LOOK at every image; fix what is wrong within this plan's files (with a test where one makes sense). In the report describe each image plainly and list anything that belongs to a later plan.
- [ ] **Step 3:** Full run: unit, mocked, and `pytest tests -k "server or viewer" -q` (root `.venv` python, cwd = worktree). Report exact counts.
- [ ] **Step 4: Commit** — `test(viewer): mocked capture tool for the task modals`
