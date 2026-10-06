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
 * @param {number} [opts.olderCount]  Items left out as older; when there are some, an empty rail says nothing itself
 * @returns {{ root: HTMLElement, rows: Array<{ item: Object, row: Object }> }}  each row as createItemRow built it
 */
export function createSpine({ label, items = [], onToggle, olderCount = 0 }) {
  const root = h('section', { class: 'co-spine' });
  root.appendChild(createSpineHead({ label, count: items.length }));

  const rows = items.map((item) => ({ item, row: createItemRow({ item, onToggle }) }));
  if (rows.length) {
    root.appendChild(h('div', { class: 'co-spine__list' }, rows.map((r) => r.row.root)));
  } else if (!olderCount) {
    root.appendChild(h('p', { class: 'co-spine__empty' }, 'Nothing here.'));
  }

  return { root, rows };
}
