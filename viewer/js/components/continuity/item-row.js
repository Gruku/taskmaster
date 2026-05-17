import { h } from '../../util/h.js';
import { formatRelative } from '../../lib/time.js';
import { hasKnownTags, renderInline } from '../../lib/xml-render.js';

const CHIP_BY_TYPE = {
  decision: ['co-chip co-chip--dec', 'Decision'],
  handover: ['co-chip co-chip--han', 'Handover'],
  task:     ['co-chip co-chip--tsk', 'Task'],
  branch:   ['co-chip co-chip--brn', 'Branch'],
  idea:     ['co-chip co-chip--ide', 'Idea'],
  issue:    ['co-chip co-chip--iss', 'Issue'],
};

// Wrap a possibly-tagged string into a DOM node — chip-render recognized
// tags, leave plain text alone, return null for empty input.
function renderField(text) {
  if (!text) return null;
  if (!hasKnownTags(text)) return document.createTextNode(text);
  const span = h('span', { class: 'co-row__xml' });
  for (const node of renderInline(text)) span.appendChild(node);
  return span;
}

export function createItemRow({ item, onClick, variant = 'default' }) {
  const [chipCls, chipLabel] = CHIP_BY_TYPE[item.type] || ['co-chip', item.type];
  const isCompact = variant === 'compact';
  const rowCls = 'co-row' + (isCompact ? ' co-row--compact' : '');

  const titleNode = hasKnownTags(item.title)
    ? renderInline(item.title)
    : [document.createTextNode(item.title || '')];
  const titleEl = h('span', { class: 'co-row__title' });
  for (const n of titleNode) titleEl.appendChild(n);

  const line1 = h('div', { class: 'co-row__line1' },
    h('span', { class: chipCls }, chipLabel),
    titleEl,
    h('span', { class: 'co-row__when' }, formatRelative(item.timestamp, { suffix: '' })),
  );

  const children = [line1];
  if (!isCompact) {
    const nextNode = renderField(item.next);
    if (nextNode) children.push(h('div', { class: 'co-row__next' }, nextNode));
    const whereNode = renderField(item.where);
    if (whereNode) children.push(h('div', { class: 'co-row__where' }, whereNode));
  }
  const row = h('div', { class: rowCls, on: { click: () => onClick?.(item, controller) } }, children);

  // Expansion controller — caller invokes setExpanded(node) to attach an
  // expanded body below the row, or clearExpanded() to remove it. State is
  // per-row so multiple rows can be open at once.
  let expandedEl = null;
  const controller = {
    isExpanded: () => expandedEl !== null,
    setExpanded(node) {
      controller.clearExpanded();
      if (!node) return;
      expandedEl = h('div', { class: 'co-row__expanded' });
      expandedEl.appendChild(node);
      row.appendChild(expandedEl);
    },
    setLoading() {
      controller.clearExpanded();
      expandedEl = h('div', { class: 'co-row__expanded co-row__expanded-loading' }, 'Loading…');
      row.appendChild(expandedEl);
    },
    clearExpanded() {
      if (expandedEl) {
        expandedEl.remove();
        expandedEl = null;
      }
    },
  };
  return { root: row, ...controller };
}
