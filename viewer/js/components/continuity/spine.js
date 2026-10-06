// User intent: a continuity rail on the Dashboard is a headed list — a real heading with its count — whose rows open
// by link or by disclosure.
import { h } from '../../util/h.js';
import { createItemRow } from './item-row.js';

/** The rail heading: an h2 and the number of rows under it. */
export function createSpineHead({ label, count }) {
  return h('div', { class: 'co-spine__head' },
    h('h2', { class: 'co-spine__label' }, label),
    h('span', { class: 'co-spine__count' }, String(count)),
  );
}

/**
 * Spine — a labelled rail of item rows.
 * @param {Object} opts
 * @param {string} opts.label  Rail heading (e.g. "Decide", "Resume")
 * @param {Array}  opts.items  Array of continuity items
 * @param {Function} [opts.onToggle]  Called with (item, controller) when a handover or decision row's toggle is pressed
 * @param {boolean} [opts.empty]  If true and no items, renders nothing
 */
export function createSpine({ label, items = [], onToggle, empty = false }) {
  if (empty && items.length === 0) return { root: null };

  const root = h('section', { class: 'co-spine' });
  root.appendChild(createSpineHead({ label, count: items.length }));

  if (items.length === 0) {
    root.appendChild(h('p', { class: 'co-spine__empty' }, 'Nothing here.'));
  } else {
    const list = h('div', { class: 'co-spine__list' });
    for (const item of items) list.appendChild(createItemRow({ item, onToggle }).root);
    root.appendChild(list);
  }

  return { root };
}
