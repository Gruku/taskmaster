// Shared right rail of the task detail views (document and graph).
// `railPanels({ task, related, level })` gives the panels that have something to show, in order:
//   Relations (links · depends on · unblocks · blockers) · Docs · Handovers · Issues
// — none at all for a task with nothing related, so the caller can leave the rail out.
// `mountRightRail(root, ctx)` puts them in `root` and returns a cleanup function.
// Also here: the handover status pill and its menu (a refused change is said beside the pill), and `RightRail`,
// the generic in-page panel a screen opens beside its list (Sessions).

import { linkPillsEl, legacyLinksToTyped } from './link-pills.js';
import { renderMarkdown } from './markdown.js';
import { statusMarker, priorityMarker, marker } from './status.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';
import { topModal } from './modal.js';
import { describeWriteError } from './edit/write-errors.js';
import { formatStamp } from '../lib/time.js';

export function mountRightRail(root, ctx = {}) {
  root.classList.add('td-rail');
  root.replaceChildren(...railPanels(ctx));
  return () => { root.replaceChildren(); };
}

// `level` is the heading level of a panel title; sub-lists sit one below it.
export function railPanels({ task, related, level = 2 } = {}) {
  const t = task && typeof task === 'object' ? task : {};
  const r = related && typeof related === 'object' ? related : {};
  return [
    panelRelations(t, r, level),
    panelDocs(t, level),
    panelHandovers(list(r.handovers), level),
    panelIssues(list(r.issues), level),
  ].filter(Boolean);
}

// Relation data is written by many hands: a list that is not a list is no list, and holes are dropped.
function list(v) {
  return Array.isArray(v) ? v.filter((x) => x != null) : [];
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
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

function panel(name, label, level, children) {
  return h('section', { class: `td-panel td-panel-${name}`, 'data-panel': name },
    [h(`h${level}`, { class: 'td-rail-h' }, label), ...children]);
}

function sub(name, label, level, body) {
  return h('div', { class: 'td-rail-group', 'data-sub': name }, [h(`h${level + 1}`, { class: 'td-rail-sub' }, label), body]);
}

// A related task as the server resolved it ({id, title, status}), or just its id.
function relatedTasks(v) {
  return list(v)
    .map((d) => (typeof d === 'string' ? { id: d } : d))
    .filter((d) => typeof d === 'object' && (typeof d.id === 'string' || typeof d.id === 'number') && d.id !== '');
}

function taskRows(tasks) {
  return h('ul', { class: 'td-dep-list' }, tasks.map((d) => h('li', {}, [
    h('a', { class: 'td-dep', href: `#/task/${encodeURIComponent(d.id)}` }, [
      h('span', { class: 'td-dep__id' }, String(d.id)),
      typeof d.title === 'string' && d.title ? h('span', { class: 'td-dep__title' }, d.title) : null,
      typeof d.status === 'string' && d.status ? statusMarker('task', d.status) : null,
    ]),
  ])));
}

function blockerText(b) {
  if (typeof b === 'string') return b;
  if (b && typeof b === 'object') return typeof b.text === 'string' ? b.text : JSON.stringify(b);
  return String(b);
}

function panelRelations(task, related, level) {
  const deps = relatedTasks(related.dependencies);
  const unblocks = relatedTasks(related.unblocks);
  // Typed links (Plan C) come from `task.links`; an unmigrated project falls back to the legacy fields.
  // A dependency already listed with its title and status is not repeated as a pill.
  const typed = Array.isArray(task.links) && task.links.length ? task.links : legacyLinksToTyped(task, 'task');
  const pills = linkPillsEl(list(typed).filter((l) =>
    !(deps.length && l.type === 'depends_on') && !(unblocks.length && l.type === 'blocks')));
  const blockers = (Array.isArray(task.blockers) ? task.blockers : [task.blockers]).filter((b) => b != null && b !== '');

  const groups = [
    pills && sub('links', 'Links', level, pills),
    deps.length && sub('depends', 'Depends on', level, taskRows(deps)),
    unblocks.length && sub('unblocks', 'Unblocks', level, taskRows(unblocks)),
    blockers.length && sub('blockers', 'Blockers', level,
      h('ul', { class: 'td-blocker-list' }, blockers.map((b) => h('li', { class: 'td-blocker' }, blockerText(b))))),
  ].filter(Boolean);
  return groups.length ? panel('relations', 'Relations', level, groups) : null;
}

function panelDocs(task, level) {
  const docs = task.docs && typeof task.docs === 'object' && !Array.isArray(task.docs) ? task.docs : {};
  const items = Object.entries(docs)
    .filter(([, href]) => typeof href === 'string' && href.trim())
    .map(([type, href]) => {
      const external = /^https?:\/\//i.test(href);
      // A path is served from the project root by the viewer's file route.
      const attrs = external
        ? { href, target: '_blank', rel: 'noopener noreferrer' }
        : { href: `/file/${encodeURI(href.replace(/^\/+/, ''))}`, target: '_blank', rel: 'noopener' };
      return h('li', {}, [h('a', { class: 'td-doc-link', ...attrs },
        [h('span', { class: 'td-doc-type' }, type), h('span', { class: 'td-doc-path' }, href)])]);
    });
  return items.length ? panel('docs', 'Docs', level, [h('ul', { class: 'td-doc-list' }, items)]) : null;
}

function stampEl(iso) {
  const stamp = formatStamp(typeof iso === 'string' ? iso : null);
  if (!stamp.title) return null;
  return h('time', { datetime: iso, title: stamp.title }, stamp.text);
}

function panelHandovers(handovers, level) {
  const items = handovers.filter((ho) => typeof ho === 'object' && typeof ho.id === 'string' && ho.id);
  if (!items.length) return null;
  return panel('handovers', 'Handovers', level, items.map((ho) => {
    const quote = typeof ho.quote === 'string' && ho.quote.trim()
      ? h('div', { class: 'td-handover-quote md-body' }) : null;
    // renderMarkdown sanitises; it is the only path handover text takes into innerHTML.
    if (quote) quote.innerHTML = renderMarkdown(ho.quote);
    return h('div', { class: 'td-handover' }, [
      h('div', { class: 'td-handover-head' }, [
        statusPill(ho.id, typeof ho.status === 'string' && ho.status ? ho.status : 'open'),
        typeof ho.kind === 'string' && ho.kind ? h('span', { class: 'td-handover-kind' }, ho.kind) : null,
        stampEl(ho.created),
      ]),
      h('div', { class: 'td-handover-id' }, ho.id),
      quote,
    ]);
  }));
}

function panelIssues(issues, level) {
  const items = issues.filter((i) => typeof i === 'object' && typeof i.id === 'string' && i.id);
  if (!items.length) return null;
  return panel('issues', 'Issues', level, [h('ul', { class: 'td-issue-list' }, items.map((i) => h('li', {}, [
    h('a', { class: 'td-issue', href: `#/issue/${encodeURIComponent(i.id)}` }, [
      h('span', { class: 'td-issue-id' }, i.id),
      typeof i.title === 'string' && i.title ? h('span', { class: 'td-issue-title' }, i.title) : null,
      // Severity uses the priority scale: the same four words and shapes.
      typeof i.severity === 'string' && i.severity ? priorityMarker(i.severity.toLowerCase()) : null,
    ]),
  ])))]);
}

// ── Handover status: a button that names the status and opens a menu to change it ──
const HO_STATUSES = ['open', 'closed', 'superseded'];
export const HO_STATUS_LABEL = Object.freeze({ open: 'Open', closed: 'Closed', superseded: 'Superseded' });
// status.js (track 3d) has no handover table, so it lives here. By meaning: an open handover is not yet picked up,
// a closed one is complete, a superseded one has moved on into the handover that replaced it.
export const HANDOVER_STATUS = Object.freeze({
  open: Object.freeze({ label: 'Open', shape: '○', tone: 'neutral' }),
  closed: Object.freeze({ label: 'Closed', shape: '●', tone: 'success' }),
  superseded: Object.freeze({ label: 'Superseded', shape: '→', tone: 'neutral' }),
});
export function handoverStatusMarker(status) {
  const meta = Object.hasOwn(HANDOVER_STATUS, status) ? HANDOVER_STATUS[status]
    : { label: String(status || '—'), shape: '○', tone: 'neutral' };
  return marker({ ...meta });
}
const statusClass = (status) => `ho-status-pill-${String(status).replace(/[^a-z0-9-]/gi, '')}`;

export function statusPill(handoverId, status) {
  return h('button', {
    type: 'button',
    class: `ho-status-pill ${statusClass(status)}`,
    'data-handover-id': handoverId,
    'data-status': status,
    'aria-haspopup': 'menu',
    'aria-expanded': 'false',
    title: `Status: ${status} — click to change`,
    on: { click: (ev) => openStatusMenu(ev.currentTarget, handoverId, ev.currentTarget.dataset.status) },
  }, [h('span', { class: 'ho-status-pill__word' }, handoverStatusMarker(status)), icon('chevron', { size: 12 })]);
}

// A pill built here holds a word and an arrow; one written by a screen's own template is just its word.
function paintPill(pill, status) {
  for (const s of HO_STATUSES) pill.classList.remove(statusClass(s));
  pill.classList.add(statusClass(status));
  pill.setAttribute('data-status', status);
  pill.title = `Status: ${status} — click to change`;
  const word = pill.querySelector('.ho-status-pill__word');
  if (word) word.replaceChildren(handoverStatusMarker(status));
  else pill.textContent = status;
}

// Throws the way api.js's http() does, so describeWriteError words it like any other refused write.
export async function postHandoverStatus(handoverId, status) {
  const path = `/api/handover/${encodeURIComponent(handoverId)}/status`;
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ status, reason: 'viewer-override' }),
  });
  if (resp.ok) return;
  let body = null;
  try { body = JSON.parse(await resp.text()); } catch { body = null; }
  const reason = typeof body?.error === 'string' ? body.error : null;
  if (resp.status === 409) {
    const err = new Error(reason && reason.trim() ? reason : 'stale');
    err.code = 409;
    throw err;
  }
  const err = new Error(`POST ${path} → ${resp.status}`);
  err.code = resp.status;
  if (reason != null) err.reason = reason;
  throw err;
}

const pillsOf = (doc, handoverId) => doc.querySelectorAll(`.ho-status-pill[data-handover-id="${CSS.escape(handoverId)}"]`);

function clearStatusError(doc, handoverId) {
  for (const pill of pillsOf(doc, handoverId)) {
    const id = pill.getAttribute('aria-describedby');
    if (!id) continue;
    doc.getElementById(id)?.remove();
    pill.removeAttribute('aria-describedby');
  }
}

let errorSeq = 0;

// The sentence sits right after the pill it was chosen from, and that pill points to it.
function showStatusError(pill, handoverId, message) {
  const doc = pill.ownerDocument;
  clearStatusError(doc, handoverId);
  const id = `ho-status-error-${++errorSeq}`;
  pill.after(h('span', { class: 'ho-status-error', role: 'alert', id }, message));
  pill.setAttribute('aria-describedby', id);
}

let openMenu = null;   // { anchor, popover } — one menu at a time

// Opens the menu under `anchor`; called again for the same anchor while it is open, it closes it.
export function openStatusMenu(anchor, handoverId, currentStatus) {
  // `openMenu` is the menu open now: its popover's onClose forgets it however it closes.
  if (openMenu) {
    const same = openMenu.anchor === anchor;
    openMenu.popover.close();
    if (same) return;
  }
  const doc = anchor.ownerDocument;
  const view = doc.defaultView;
  const items = HO_STATUSES.map((opt) => {
    const current = opt === currentStatus;
    const item = h('button', {
      type: 'button', role: 'menuitemradio', 'aria-checked': String(current),
      class: `popover-item ho-status-menu-item${current ? ' is-current' : ''}`,
    }, [h('span', { class: 'ho-status-menu-check' }, current ? icon('check', { size: 14 }) : null), opt]);
    item.addEventListener('click', () => choose(opt));
    return item;
  });
  const popover = openPopover({
    anchor, content: items, role: 'menu', label: 'Handover status', focus: 'checked', className: 'ho-status-menu',
    // However it closes (Escape, a press outside, a redraw), it is no longer the open menu.
    onClose: () => { if (openMenu?.popover === popover) openMenu = null; },
  });
  openMenu = { anchor, popover };

  async function choose(opt) {
    popover.close('api', { returnFocus: true });
    try {
      await postHandoverStatus(handoverId, opt);
    } catch (e) {
      // The pills keep the status the server still has; the pill it was chosen from says why.
      const pill = anchor.isConnected ? anchor : [...pillsOf(doc, handoverId)][0];
      if (pill) showStatusError(pill, handoverId, describeWriteError(e, { noun: 'handover' }));
      return;
    }
    clearStatusError(doc, handoverId);
    // Patch every pill rendered for this handover (task rail and sessions rail).
    for (const pill of pillsOf(doc, handoverId)) paintPill(pill, opt);
    view?.dispatchEvent(new view.CustomEvent('viewer:handover-status-changed', {
      detail: { id: handoverId, status: opt },
    }));
  }
}

// ---------------------------------------------------------------------------
// Generic right rail: a panel in the page, placed by its host, that a screen opens and closes as rows are picked.
// It takes focus to its title when it opens and hands it back to the row that opened it when it closes.
// ---------------------------------------------------------------------------

let railSeq = 0;

export class RightRail {
  constructor({ host, label = 'Details' } = {}) {
    if (!host || typeof host.appendChild !== 'function') throw new TypeError('RightRail needs a host element');
    this.host = host;
    this.label = label;
    this.el = null;
    this._opener = null;
    this._onClose = null;
    this._onKey = null;
  }

  open({ kind = 'plain', title, head = [], body = [], opener = null, onClose } = {}) {
    // Swapping content: the new title takes focus, so the old opener is not focused (and scrolled to) on the way.
    this.close({ returnFocus: false });
    // A host taken out of the page (the screen was left while its data loaded) gets no rail and no key listener.
    if (!this.host.isConnected) return null;
    const doc = this.host.ownerDocument;
    const titleId = `rr-title-${++railSeq}`;
    const closeBtn = h('button', {
      type: 'button', class: 'rr-close btn btn--ghost btn--icon btn--sm', 'aria-label': 'Close details',
      on: { click: () => this.close() },
    }, icon('dismiss', { size: 14 }));
    const titleEl = h('h2', { class: 'rr-title', id: titleId, tabindex: '-1' }, String(title ?? ''));
    const el = h('aside', {
      id: 'right-rail', class: `right-rail right-rail--${kind}`, 'aria-label': this.label, 'aria-labelledby': titleId,
    }, [h('div', { class: 'rr-h' }, [...head, closeBtn]), titleEl, ...body]);
    this.host.appendChild(el);
    this.el = el;
    this._opener = opener;
    this._onClose = typeof onClose === 'function' ? onClose : null;
    // A key a menu or a field already used is theirs; with a modal open, the modal answers Escape.
    this._onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented || topModal()) return;
      this.close();
    };
    doc.addEventListener('keydown', this._onKey);
    // On a narrow screen the rail may sit out of view, and the reader is taken to it — with its top below the sticky
    // topbar (whose height depends on its second row), not under it as a plain focus scroll leaves it.
    titleEl.focus({ preventScroll: true });
    const win = doc.defaultView;
    const barBottom = doc.querySelector('.topbar')?.getBoundingClientRect().bottom ?? 0;
    const railTop = el.getBoundingClientRect().top;
    if (win && (railTop < barBottom || railTop > win.innerHeight - 44)) win.scrollBy(0, railTop - barBottom);
    return el;
  }

  close({ returnFocus = true } = {}) {
    if (!this.el) return;
    const el = this.el;
    const doc = el.ownerDocument;
    doc.removeEventListener('keydown', this._onKey);
    const active = doc.activeElement;
    const hadFocus = !active || active === doc.body || el.contains(active);
    const opener = this._opener;
    const onClose = this._onClose;
    this.el = null;
    this._opener = null;
    this._onClose = null;
    this._onKey = null;
    el.remove();
    if (returnFocus && hadFocus && opener?.isConnected) opener.focus();
    onClose?.();
  }

  isOpen() { return !!this.el; }
}
