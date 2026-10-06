// User intent: a continuity row on the Dashboard is one honest control — a link when the item has a page, a disclosure
// when its body opens in place (handovers, decisions), nothing when there is nowhere to go — and never markup from data.
import { h } from '../../util/h.js';
import { formatRelative } from '../../lib/time.js';
import { hasKnownTags, renderInline } from '../../lib/xml-render.js';
import { linkRow } from '../link-row.js';
import { truncate } from '../../lib/text.js';
import { statusMarker, severityMarker } from '../status.js';

const enc = encodeURIComponent;
export const ITEM_ROUTE = {
  task: (id) => `#/task/${enc(id)}`,
  issue: (id) => `#/issue/${enc(id)}`,
  idea: () => '#/ideas',
};

const TYPE_WORD = { decision: 'Decision', handover: 'Handover', task: 'Task', branch: 'Branch', idea: 'Idea', issue: 'Issue' };
const DISCLOSES = new Set(['handover', 'decision']);

let seq = 0;

// Wrap a possibly-tagged string into a DOM node — chip-render recognized
// tags, leave plain text alone, return null for empty input.
function renderField(text) {
  if (!text) return null;
  if (!hasKnownTags(text)) return document.createTextNode(text);
  const span = h('span', { class: 'co-row__xml' });
  for (const node of renderInline(text)) span.appendChild(node);
  return span;
}

function titleNode(text) {
  if (!hasKnownTags(text)) return truncate(text, { className: 'co-row__title' });
  return h('span', { class: 'co-row__title', title: text }, renderInline(text));
}

// The server sends a task's status, and an issue's "severity · status", as stored slugs in `next`; a row says them as
// a shape plus a word, like every status in the viewer.
function nextNode(item) {
  const text = typeof item.next === 'string' ? item.next.trim() : '';
  if (item.type === 'task' && text) return statusMarker('task', text);
  if (item.type === 'issue' && text) {
    const [sev, status] = text.split('·').map((s) => s.trim());
    if (sev && status) return h('span', { class: 'co-row__markers' }, severityMarker(sev), statusMarker('issue', status));
  }
  if (item.type === 'idea' && text) {
    // Older idea statuses ("brainstorm", "raw") are not in the idea table; they still read as a word, not a slug.
    const el = statusMarker('idea', text);
    const word = el.querySelector('.marker__word');
    if (word.textContent === text) word.textContent = text.charAt(0).toUpperCase() + text.slice(1).replace(/-/g, ' ');
    return el;
  }
  return renderField(item.next);
}

// An idea's `where` is its status again; the row says it once.
const whereOf = (item) => (item.type === 'idea' && item.where === item.next ? null : item.where);

// The row's words, as spans so they may sit inside a button: tag, title and age on one line, then next and where.
function parts(item, word, label) {
  const chip = h('span', { class: 'co-chip' }, word);
  const when = h('span', { class: 'co-row__when' }, formatRelative(item.timestamp, { suffix: '' }));
  const next = nextNode(item);
  const where = renderField(whereOf(item));
  return {
    chip, when, title: titleNode(label),
    next: next && h('span', { class: 'co-row__next' }, next),
    where: where && h('span', { class: 'co-row__where' }, where),
  };
}

export function createItemRow({ item, onToggle }) {
  const word = TYPE_WORD[item.type] || String(item.type || '');
  const label = item.title || item.id || word;
  const p = parts(item, word, label);
  const route = item.id ? ITEM_ROUTE[item.type] : null;

  if (route) {
    const root = linkRow({ href: route(item.id), name: p.title, content: [p.chip, p.when, p.next, p.where], className: 'co-row' });
    root.dataset.itemId = item.id;
    return { root };
  }

  const words = [h('span', { class: 'co-row__line1' }, p.chip, p.title, p.when), p.next, p.where];
  if (!DISCLOSES.has(item.type) || !item.id) {
    return { root: h('div', { class: 'co-row', 'data-item-id': item.id }, words) };
  }

  const regionId = `co-row-${++seq}-body`;
  const toggle = h('button', {
    type: 'button', class: 'co-row__toggle', 'aria-expanded': 'false',
    on: { click: () => onToggle?.(item, controller) },
  }, words);
  const root = h('div', { class: 'co-row', 'data-item-id': item.id }, toggle);

  // Expansion controller — the caller fills the region with setExpanded(node) or
  // empties it with clearExpanded(). State is per-row, so several rows can be open.
  let expandedEl = null;
  function open(child, busy) {
    controller.clearExpanded();
    expandedEl = h('div', {
      class: 'co-row__expanded', id: regionId, role: 'region', 'aria-label': `${word} ${item.id}`,
      'aria-busy': busy ? 'true' : null,
    }, child);
    root.appendChild(expandedEl);
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-controls', regionId);
  }
  const controller = {
    root,
    isExpanded: () => expandedEl !== null,
    setExpanded(node) {
      if (!node) { controller.clearExpanded(); return; }
      open(node, false);
    },
    setLoading() {
      open(h('p', { class: 'co-xblock__p' }, 'Loading…'), true);
    },
    clearExpanded() {
      expandedEl?.remove();
      expandedEl = null;
      toggle.setAttribute('aria-expanded', 'false');
      toggle.removeAttribute('aria-controls');
    },
  };
  return controller;
}
