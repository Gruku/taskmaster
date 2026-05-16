import { h } from '../../util/h.js';
import { createItemRow } from './item-row.js';

/**
 * Spine — a labelled rail of item rows.
 * @param {Object} opts
 * @param {string} opts.label  Rail heading (e.g. "decide", "resume")
 * @param {Array}  opts.items  Array of continuity items
 * @param {Function} [opts.onItemClick]  Called with item when a row is clicked
 * @param {boolean} [opts.empty]  If true and no items, renders nothing
 */
export function createSpine({ label, items = [], onItemClick, empty = false }) {
  if (empty && items.length === 0) return { root: null };

  const root = h('section', { class: 'co-spine' });

  const head = h('div', { class: 'co-spine__head' },
    h('span', { class: 'co-spine__label' }, label),
    h('span', { class: 'co-spine__count' }, String(items.length)),
  );
  root.appendChild(head);

  if (items.length === 0) {
    root.appendChild(h('div', { class: 'co-spine__empty' }, 'Nothing here'));
  } else {
    const list = h('div', { class: 'co-spine__list' });
    for (const item of items) {
      const row = createItemRow({ item, onClick: onItemClick });
      list.appendChild(row.root);
    }
    root.appendChild(list);
  }

  return { root };
}
