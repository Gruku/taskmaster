// User intent: one task document for the full page and the detail modal — a clear reading order (what it is, its state,
// then its sections), nothing printed twice, nothing empty printed at all, dates a person reads, and every control a
// real link or button the keyboard can reach.
// Exports `mountTaskDetailDocument(root, { task, related, claim, chrome, titleHost, … })`.
//   chrome 'page'     — the meta line and an h1 title head the document.
//   chrome 'embedded' — the dialog shows the id and the title; the title's inline field mounts in `titleHost`
//                       and the document starts at the marker row.
// Either way a refused title save is said under the heading, never inside it: the heading names the dialog.

import { railPanels } from './right-rail.js';
import { claimTopbar, claimTopbarPrimary, tmSegmented, tmAction } from '../lib/topbar.js';
import { formatAbsolute } from '../lib/time.js';
import { epicIndex } from '../lib/epics.js';
import { mountInlineField } from './edit/inline-field.js';
import { taskSchema } from './edit/forms/task-form.js';
import { describeWriteError, lostRace } from './edit/write-errors.js';
import { EstimateField } from './edit/fields/estimate-field.js';
import { renderGatePipeline } from './gate-pipeline.js';
import { renderMergeLadder } from './merge-status.js';
import { marker, statusMarker } from './status.js';
import { icon } from './icon.js';
import {
  detailMeta, stampEl, copyButton, copyId, detailHead, detailTag, sectionHeading, markdownBody, datesList, detailGrid,
} from './detail-page.js';

let seq = 0;

// The documents of a task that can be edited in place, in reading order. One that is empty is not printed: it is
// offered by name on the "Empty:" line — except the description, which only shows once the task has one.
const SECTIONS = [
  { key: 'description', label: 'Description', test: 'sec-description', offer: false },
  { key: 'specification', label: 'Specification', test: 'sec-spec' },
  { key: 'plan', label: 'Plan', test: 'sec-plan' },
  { key: 'notes', label: 'Notes', test: 'sec-notes' },
  { key: 'review_instructions', label: 'Review instructions', test: 'sec-review-instructions', when: (task) => task.status === 'in-review' },
];

const VERDICT_MARK = {
  pass: ['●', 'success'],
  warn: ['▲', 'warning'],
  fail: ['◆', 'critical'],
};

// Task data is written by people, agents and old versions: a field of the wrong type is shown as nothing rather
// than as "[object Object]", and never throws.
const text = (v) => (typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : '');
const prose = (v) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');

// Inline-edit save callback. Returns either undefined (success) or { error }.
function inlineSave(taskId, fieldKey, ctx) {
  return async (newValue) => {
    try {
      await ctx.api.patchTask(taskId, { [fieldKey]: newValue });
    } catch (e) {
      if (lostRace(e)) throw e; // a lost race goes back to inline-field, which settles it from the conflict banner
      return { error: describeWriteError(e) };
    }
    // The write has landed. Refresh so other screens show it; a refresh that fails catches up on the next poll and
    // never turns the saved change into a reported failure.
    await Promise.resolve(ctx.store.refreshBoard(ctx.api)).catch(() => {});
  };
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

// The page's meta line: the id to copy, the way back to the tasks, the epic, the phase and when it was created.
export function taskMeta(raw, { timers } = {}) {
  raw ??= {};
  const epic = text(raw.epic);
  return detailMeta([
    copyId({ id: text(raw.id), noun: 'task', timers }),
    h('a', { href: '#/kanban' }, 'Tasks'),
    epic ? h('a', { href: `#/epic/${encodeURIComponent(epic)}` }, epic) : null,
    text(raw.phase),
    stampEl(raw.created, { prefix: 'created' }),
  ]);
}

// The full page's top bar: the Document / Graph switch alone in row 2, and Edit as the page's primary in row 1.
// `view` is the view actually on screen.
export function mountTaskTopbar({ view, onToggleVariant, onEdit }) {
  const topbar = claimTopbar();
  if (!topbar) return;
  const seg = tmSegmented(
    [
      { key: 'A', label: 'Document' },
      { key: 'B', label: 'Graph' },
    ],
    { value: view === 'B' ? 'B' : 'A', onChange: (v) => onToggleVariant?.(v) },
  );
  topbar.append(seg);
  claimTopbarPrimary()?.append(tmAction({
    icon: 'edit', label: 'Edit', title: 'Edit task', variant: 'primary',
    onClick: () => onEdit?.(),
  }));
}

let editOpening = null;
export function openEditForm(ctx) {
  // Late-import to avoid loading edit code on every viewer boot. A second press while that load is under way would
  // open a second form, with a second edit lease, so it joins the first.
  editOpening ??= import('./edit/task-actions.js').then(({ openTaskEditModal }) => {
    openTaskEditModal({ store: ctx.store, api: ctx.api, task: ctx.task });
  }).finally(() => { editOpening = null; });
  return editOpening;
}

// A disclosure the user opened (the reviewer note). A menu button also reads expanded, but its menu is not part of
// the document and a click would only open it again.
const OPEN_TOGGLE = 'button[aria-expanded="true"][data-focus]:not([aria-haspopup])';
const SHUT_TOGGLE = 'button[aria-expanded="false"][data-focus]:not([aria-haspopup])';

// What the user had open, what a field was still saying about a refused save, and where focus sat inside `scope`, as
// something a re-mounted document can find again. Returns a function that re-opens the same disclosures under `next`,
// says the same refusals beside the same fields, puts focus on the same thing, and says whether focus could be restored.
// A refusal is said again only while the field still holds the stored value it was refused against (`data-stored`):
// once another writer has changed that field, the reason was about a value that is gone. A field that had just saved
// says "Saved" again in its new self, since the redraw a save brings would otherwise take the word before it is heard.
export function rememberView(scope) {
  const open = scope ? [...scope.querySelectorAll(OPEN_TOGGLE)].map((b) => b.dataset.focus) : [];
  const said = scope ? [...scope.querySelectorAll('.if-wrap[data-key]')]
    .map((w) => [w.dataset.key, w.refusal?.(), w.dataset.stored]).filter(([, text]) => text) : [];
  const saved = scope ? [...scope.querySelectorAll('.if-wrap[data-key]')].filter((w) => w.saved?.()).map((w) => w.dataset.key) : [];
  const active = scope?.ownerDocument.activeElement;
  const focused = !!active && scope.contains(active) && active !== scope;
  const key = focused ? active.closest('.if-wrap')?.dataset.key : null;
  const mark = focused ? active.dataset?.focus : null;
  const href = focused && active.matches('a[href]') ? active.getAttribute('href') : null;
  return (next = scope) => {
    if (!next) return false;
    const find = (sel, test) => [...next.querySelectorAll(sel)].find(test);
    for (const toggle of next.querySelectorAll(SHUT_TOGGLE)) {
      if (open.includes(toggle.dataset.focus)) toggle.click();
    }
    for (const [field, text, stored] of said) {
      find('.if-wrap', (w) => w.dataset.key === field && w.dataset.stored === stored)?.sayRefusal?.(text);
    }
    for (const field of saved) find('.if-wrap', (w) => w.dataset.key === field)?.saySaved?.();
    if (!focused) return false;
    const target = (key && find('.if-wrap', (w) => w.dataset.key === key)?.querySelector('[tabindex="0"]'))
      || (key && find('[data-focus]', (e) => e.dataset.focus === `edit:${key}`))
      || (mark && find('[data-focus]', (e) => e.dataset.focus === mark))
      || (href && find('a[href]', (a) => a.getAttribute('href') === href))
      || null;
    target?.focus();
    return !!target && next.ownerDocument.activeElement === target;
  };
}

export function mountTaskDetailDocument(root, ctx) {
  const chrome = ctx.chrome === 'embedded' ? 'embedded' : 'page';
  const raw = ctx.task && typeof ctx.task === 'object' ? ctx.task : {};
  // What the inline fields read: text where text belongs, so a stray object is an empty field, not a crash.
  const task = {
    ...raw,
    id: text(raw.id),
    title: text(raw.title),
    ...Object.fromEntries(SECTIONS.map((s) => [s.key, prose(raw[s.key])])),
  };
  const level = chrome === 'page' ? 2 : 3;   // section headings: under the page's h1, or the dialog's h2
  const view = root.ownerDocument.defaultView;
  const schema = taskSchema({ getBacklog: () => ctx.store?.getBacklog() });
  const fields = [];      // inline-field handles, destroyed on unmount
  const observers = [];
  const timers = new Set();

  // The write precondition belongs to the revision the user actually sees,
  // never to a refresh that was fetched but suppressed during an active edit.
  if (ctx.etag) ctx.store?.setEtag?.(`task:${task.id}`, ctx.etag);
  root.replaceChildren();
  const classes = chrome === 'page' ? ['td-doc', 'td-doc--page', 'td-page', 'td-page-A'] : ['td-doc', 'td-doc--embedded'];
  root.classList.add(...classes);
  // On its own page a link to another task navigates; it is not turned into a modal over this one.
  if (chrome === 'page') root.dataset.detailLinks = 'follow';

  // One inline-editable field. What the renderer paints in read mode is a click target only, so here it also becomes
  // a keyboard stop (`asButton`) — or, for a document whose links must stay links, a separate Edit button opens it —
  // and focus lost with the closing editor is put back. `onRead` hears every return to read mode.
  function inlineField(host, fieldKey, { asButton = true, name, hint, onRead, messageHost } = {}) {
    const handle = mountInlineField(host, { schema, fieldKey, entity: task, onSave: inlineSave(task.id, fieldKey, ctx), messageHost });
    fields.push(handle);
    const wrap = [...host.children].find((el) => el.classList.contains('if-wrap') && el.dataset.key === fieldKey);
    // The value this field was drawn from, so a refusal carried across a re-mount can tell whether it still applies.
    wrap.dataset.stored = JSON.stringify(task[fieldKey] ?? null);
    let editing = false;
    function dress() {
      const el = wrap.firstElementChild;
      if (!el) return;
      if (!el.classList.contains('ef-editable')) { editing = true; return; }
      if (asButton && !el.hasAttribute('tabindex')) {
        el.setAttribute('tabindex', '0');
        el.setAttribute('role', 'button');
        const label = name?.(el);
        if (label) el.setAttribute('aria-label', label);
        if (hint) el.setAttribute('title', hint);
        el.addEventListener('keydown', (e) => {
          if (e.target !== el || (e.key !== 'Enter' && e.key !== ' ')) return;
          e.preventDefault();
          el.click();
        });
      }
      if (!editing) return;
      editing = false;
      const active = root.ownerDocument.activeElement;
      const lost = !active || active === root.ownerDocument.body || !active.isConnected;
      if (lost && asButton) el.focus();
      onRead?.({ el, lost });
    }
    dress();
    if (view?.MutationObserver) {
      const observer = new view.MutationObserver(dress);
      observer.observe(wrap, { childList: true });
      observers.push(observer);
    }
    return { handle, wrap, open: () => wrap.firstElementChild?.click() };
  }

  // A refused title is said on its own line under the heading; the save glyph stays beside the title.
  function mountTitle(host) {
    const messageHost = h('div', { class: 'td-title-message' });
    inlineField(host, 'title', { hint: 'Edit title', messageHost });
    return messageHost;
  }

  // ── Marker row ──
  function renderMarkers() {
    const row = h('div', { class: 'td-markers', 'data-test': 'chips' });
    for (const [key, label] of [['status', 'Status'], ['priority', 'Priority']]) {
      const host = h('span', { class: 'td-marker-host td-inline-host', 'data-field': key });
      inlineField(host, key, {
        name: (el) => `${label}: ${el.querySelector('.marker__word')?.textContent || 'not set'} — change`,
      });
      row.appendChild(host);
    }
    // The estimate is read the way the picker stores it ('3' is three days).
    const estimate = EstimateField.read({ value: typeof raw.estimate === 'object' ? null : raw.estimate, readOnly: true });
    if (!estimate.classList.contains('ef-placeholder')) row.appendChild(detailTag('estimate', 'Estimate', estimate.textContent));

    // The epic is its swatch and its name; one missing from the backlog shows its id and no swatch.
    const epic = text(raw.epic);
    if (epic) {
      const known = epicIndex(ctx.store?.getBacklog?.()?.epics).get(epic);
      row.appendChild(h('a', { class: 'td-tag td-epic', 'data-tag': 'epic', href: `#/epic/${encodeURIComponent(epic)}` }, [
        known ? h('span', { class: `td-swatch td-swatch--cat-${known.swatch}`, 'aria-hidden': 'true' }) : null,
        h('span', { class: 'td-tag__k' }, 'Epic'),
        h('span', { class: 'td-tag__v' }, known?.name ?? epic),
      ]));
    }
    const phase = text(raw.phase);
    if (chrome === 'embedded' && phase) row.appendChild(detailTag('phase', 'Phase', phase));

    for (const [key, label] of [['branch', 'Branch'], ['worktree', 'Worktree']]) {
      const value = text(raw[key]);
      if (!value) continue;
      row.appendChild(copyButton({
        value, label: `Copy ${label.toLowerCase()} ${value}`, className: 'td-tag', tag: key,
        test: key === 'branch' ? 'branch' : null, focus: `copy:${key}`, timers,
        children: [h('span', { class: 'td-tag__k' }, label), h('span', { class: 'td-tag__v' }, value)],
      }));
    }
    const release = text(raw.release);
    if (release) row.appendChild(detailTag('release', 'Release', release));
    const subRepo = text(raw.sub_repo);
    if (subRepo) row.appendChild(detailTag('sub_repo', 'Sub-repo', subRepo));
    return row;
  }

  // ── Lock, spec review, gates, merge ──
  function renderLock() {
    const claim = ctx.claim;
    if (!claim || claim.state !== 'held' || claim.expired) return null;
    const until = typeof claim.expires_at === 'string' ? formatAbsolute(claim.expires_at) : '';
    return h('div', { class: 'td-lock-banner', 'data-test': 'lock-banner' }, [
      marker({ label: 'Locked', shape: '▲', tone: 'warning' }),
      h('span', {}, ` by ${text(claim.holder) || 'another session'}${until ? ` until ${until}` : ''}`),
    ]);
  }

  function renderSpecReview() {
    // Stored as { verdict, codex_note }; older records hold the verdict alone.
    const sr = raw.spec_review;
    const stored = sr && typeof sr === 'object' ? sr.verdict : sr;
    const verdict = typeof stored === 'string' ? stored.trim() : '';
    if (!verdict) return null;
    const [shape, tone] = VERDICT_MARK[verdict] ?? ['○', 'neutral'];
    const note = sr && typeof sr === 'object' ? prose(sr.codex_note).trim() : '';
    const badge = marker({ label: verdict, shape, tone });
    badge.classList.add('td-spec-badge', verdict.replace(/[^a-z0-9-]/gi, ''));
    const label = h('span', { class: 'td-strip__label' }, 'Spec review');
    if (!note) return h('div', { class: 'td-strip td-spec-block', 'data-test': 'spec-review' }, [label, badge]);

    const noteId = `td-spec-note-${++seq}`;
    const noteEl = h('div', { class: 'td-codex-note', id: noteId, hidden: '' }, note);
    const toggle = h('button', {
      type: 'button', class: 'td-spec-toggle', 'aria-expanded': 'false', 'aria-controls': noteId, 'data-focus': 'spec-note',
    }, [badge, h('span', { class: 'td-spec-toggle__hint' }, 'Reviewer note'), icon('chevron', { size: 14 })]);
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      noteEl.hidden = !open;
      noteEl.classList.toggle('open', open);
    });
    return h('div', { class: 'td-strip td-spec-block', 'data-test': 'spec-review' }, [label, toggle, noteEl]);
  }

  // The gate and merge modules escape what they print; their markup is shared with the board cards.
  function renderStrip(label, html, cls, dataTest) {
    if (!html) return null;
    const track = h('div', { class: 'td-strip__body' });
    track.innerHTML = html;
    return h('div', { class: `td-strip ${cls}`, 'data-test': dataTest }, [h('span', { class: 'td-strip__label' }, label), track]);
  }

  // ── Sections ──
  // An editable document: heading, an Edit button, and the inline field. Empty in read mode, it collapses.
  function editableSection(spec) {
    const editBtn = h('button', {
      type: 'button', class: 'td-section-edit btn btn--ghost btn--sm btn--icon',
      'aria-label': `Edit ${spec.label.toLowerCase()}`, title: `Edit ${spec.label.toLowerCase()}`, 'data-focus': `edit:${spec.key}`,
    }, icon('edit', { size: 14 }));
    const section = h('section', { class: 'td-section td-inline-host', 'data-test': spec.test, 'data-section': spec.key },
      [sectionHeading(spec.label, level, editBtn)]);
    const field = inlineField(section, spec.key, {
      asButton: false,
      onRead: ({ el, lost }) => {
        if (el.classList.contains('ef-placeholder')) collapse(spec, lost);
        else if (lost) editBtn.focus();
      },
    });
    editBtn.addEventListener('click', () => field.open());
    return { section, field };
  }

  const open = new Map();      // section key → { section, field }
  let empty = [];              // keys offered on the "Empty:" line, in reading order
  const sectionsEnd = document.createComment('sections');
  const tail = document.createComment('tail');
  const emptiesLine = h('p', { class: 'td-empties', 'data-test': 'empty-sections' });

  function paintEmpties() {
    const offered = SECTIONS.filter((s) => empty.includes(s.key));
    emptiesLine.replaceChildren(h('span', { class: 'td-empties__label' }, 'Empty:'), ' ');
    offered.forEach((spec, i) => {
      if (i) emptiesLine.append(' · ');
      emptiesLine.appendChild(h('button', {
        type: 'button', class: 'td-empties__add', 'data-section': spec.key, 'data-focus': `add:${spec.key}`,
        title: `Write the ${spec.label.toLowerCase()}`,
        on: { click: () => expand(spec) },
      }, spec.label));
    });
    if (!offered.length) emptiesLine.remove();
    else if (!emptiesLine.isConnected) tail.after(emptiesLine);
  }

  // Puts a section back among its siblings in reading order.
  function place(spec, section) {
    const later = SECTIONS.slice(SECTIONS.indexOf(spec) + 1).map((s) => open.get(s.key)?.section).find(Boolean);
    (later ?? sectionsEnd).before(section);
  }

  function expand(spec) {
    const made = editableSection(spec);
    open.set(spec.key, made);
    place(spec, made.section);
    empty = empty.filter((k) => k !== spec.key);
    paintEmpties();
    made.field.open();
  }

  function collapse(spec, refocus) {
    const made = open.get(spec.key);
    if (!made) return;
    open.delete(spec.key);
    made.field.handle.destroy();
    made.section.remove();
    if (spec.offer !== false && !empty.includes(spec.key)) empty.push(spec.key);
    paintEmpties();
    if (refocus) emptiesLine.querySelector(`[data-section="${spec.key}"]`)?.focus();
  }

  function renderActivity() {
    const source = raw.activity ?? raw.activity_lines;
    const lines = (Array.isArray(source) ? source : typeof source === 'string' ? [source] : [])
      .filter((l) => l != null && l !== '')
      .map((l) => (typeof l === 'string' ? l : typeof l === 'object' ? JSON.stringify(l) : String(l)));
    if (!lines.length) return null;
    return h('section', { class: 'td-section', 'data-test': 'sec-activity' }, [
      sectionHeading('Latest activity', level),
      h('ul', { class: 'td-activity' }, lines.slice(0, 8).map((l) => h('li', {}, l))),
    ]);
  }

  function renderPatchnote() {
    const note = prose(raw.patchnote);
    if (raw.status !== 'done' || !note.trim()) return null;
    return h('section', { class: 'td-section', 'data-test': 'sec-patchnote' }, [sectionHeading('Patchnote', level), markdownBody(note)]);
  }

  // ── Assemble ──
  let titleMessage = null;
  if (chrome === 'page') {
    const title = h('h1', { class: 'td-title', 'data-test': 'title' });
    titleMessage = mountTitle(title);
    root.appendChild(detailHead({ meta: taskMeta(raw, { timers }), title, after: [titleMessage] }));
  } else if (ctx.titleHost) {
    titleMessage = mountTitle(ctx.titleHost);
  }

  const body = h('div', { class: 'td-body' }, [
    chrome === 'embedded' ? titleMessage : null,
    renderMarkers(),
    renderLock(),
    renderSpecReview(),
    renderStrip('Gates', renderGatePipeline(raw), 'td-gate-section', 'gate-pipeline'),
    renderStrip('Merged to', renderMergeLadder(raw), 'td-merge-section', 'merge-ladder'),
    sectionsEnd,
    renderActivity(),
    renderPatchnote(),
    tail,
    datesList([['Created', raw.created], ['Started', raw.started], ['Completed', raw.completed]]),
  ].filter(Boolean));

  for (const spec of SECTIONS) {
    if (spec.when && !spec.when(raw)) continue;
    if (task[spec.key].trim()) {
      const made = editableSection(spec);
      open.set(spec.key, made);
      sectionsEnd.before(made.section);
    } else if (spec.offer !== false) empty.push(spec.key);
  }
  paintEmpties();

  root.appendChild(detailGrid({ body, panels: railPanels({ task: raw, related: ctx.related, level }) }));

  // A claim can expire without any committed row changing (and hence while
  // every board poll is 304). Never leave its banner visible past that instant.
  let expiryTimer;
  const expiresAt = Date.parse(ctx.claim?.expires_at);
  function expireBanner() {
    if (!Number.isFinite(expiresAt)) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) root.querySelector('.td-lock-banner')?.remove();
    else expiryTimer = setTimeout(expireBanner, Math.min(remaining, 2147483647));
  }
  if (root.querySelector('.td-lock-banner')) expireBanner();

  // Fire-and-forget: fetch linked bugs and inject section into the body asynchronously.
  let disposed = false;
  if (typeof ctx.api?.listBugs === 'function' && task.id) {
    mountLinkedBugs(tail, { listBugs: ctx.api.listBugs, taskId: task.id, level, alive: () => !disposed });
  }

  if (chrome === 'page') mountTaskTopbar({ view: 'A', onToggleVariant: ctx.onToggleVariant, onEdit: () => openEditForm(ctx) });

  return () => {
    disposed = true;
    clearTimeout(expiryTimer);
    for (const timer of timers) clearTimeout(timer);
    for (const observer of observers) observer.disconnect();
    // Destroying a field ends its edit: abandoned drafts lose their timers and edit-state leases.
    for (const field of fields) field.destroy();
    root.replaceChildren();
    root.classList.remove(...classes);
    delete root.dataset.detailLinks;
  };
}

// ── Linked Bugs subsection (async, inserted before `anchor` after the sync render) ──
async function mountLinkedBugs(anchor, { listBugs, taskId, level, alive }) {
  try {
    const bugs = await listBugs({ found_in: taskId });
    if (!alive() || !anchor.isConnected || !Array.isArray(bugs) || !bugs.length) return;

    const section = h('section', { class: 'td-section td-linked-bugs', 'data-test': 'linked-bugs' });
    const openCount = bugs.filter((b) => b?.status === 'open').length;
    const warn = openCount > 0
      ? h('span', { class: 'td-linked-bugs__blocker' }, [
        marker({ label: `${openCount} open bug${openCount === 1 ? '' : 's'} blocking close`, shape: '◆', tone: 'critical' }),
      ])
      : null;
    section.appendChild(h('div', { class: 'td-section-head td-linked-bugs__heading' },
      [h(`h${level}`, { class: 'td-section-h' }, 'Linked bugs'), warn]));

    const ul = h('ul', { class: 'td-linked-bugs__list' });
    for (const b of bugs) {
      if (!b || typeof b.id !== 'string') continue;
      ul.appendChild(h('li', { class: 'td-linked-bugs__item' }, [
        h('a', { class: 'td-linked-bugs__link', href: `#/bug/${encodeURIComponent(b.id)}` },
          [h('span', { class: 'td-linked-bugs__id' }, b.id), ' — ', text(b.title)]),
        text(b.status) ? h('span', { class: 'td-linked-bugs__status' }, statusMarker('bug', text(b.status))) : null,
      ]));
    }
    section.appendChild(ul);
    anchor.before(section);
  } catch (e) {
    // listBugs failures are non-fatal — task detail still renders without bugs
    console.warn('listBugs failed in task-detail:', e);
  }
}
