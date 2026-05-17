// Resume rail — like a spine, but the items are split into Open (todo /
// in-progress handovers + any non-handover work to resume) and Recent (the
// most-recent done handovers, surfaced for context).
import { h } from '../../util/h.js';
import { createItemRow } from './item-row.js';

function isOpen(item) {
  if (item.type === 'handover') {
    const s = item.status || 'todo';
    return s === 'todo' || s === 'in-progress';
  }
  // Non-handover items only reach the resume bucket when their action_class
  // is already 'resume' (in-progress tasks etc.) — treat them as open work.
  return true;
}

function buildSubSection({ label, items, variant, onItemClick }) {
  const section = h('section', { class: 'co-resume__sub' });
  section.appendChild(h('div', { class: 'co-resume__sub-head' },
    h('span', { class: 'co-resume__sub-label' }, label),
    h('span', { class: 'co-resume__sub-count' }, String(items.length)),
  ));
  if (items.length === 0) {
    section.appendChild(h('div', { class: 'co-spine__empty' }, 'Nothing here'));
  } else {
    const list = h('div', { class: 'co-resume__list' });
    for (const item of items) {
      const row = createItemRow({ item, onClick: onItemClick, variant });
      list.appendChild(row.root);
    }
    section.appendChild(list);
  }
  return section;
}

/**
 * @param {Object} opts
 * @param {Array}  opts.items  All items with action_class === 'resume'
 * @param {Function} [opts.onItemClick]
 * @param {boolean}  [opts.empty]  Hide entirely when items list is empty
 */
export function createResumeRail({ items = [], onItemClick, empty = false }) {
  if (empty && items.length === 0) return { root: null };

  const open = items.filter(isOpen);
  const recent = items.filter((it) => !isOpen(it));

  const root = h('section', { class: 'co-spine co-resume' });
  root.appendChild(h('div', { class: 'co-spine__head' },
    h('span', { class: 'co-spine__label' }, 'resume'),
    h('span', { class: 'co-spine__count' }, String(items.length)),
  ));

  if (items.length === 0) {
    root.appendChild(h('div', { class: 'co-spine__empty' }, 'Nothing here'));
    return { root };
  }

  root.appendChild(buildSubSection({
    label: 'Open',
    items: open,
    variant: 'default',
    onItemClick,
  }));
  if (recent.length > 0) {
    root.appendChild(buildSubSection({
      label: 'Recent',
      items: recent,
      variant: 'compact',
      onItemClick,
    }));
  }
  return { root };
}
