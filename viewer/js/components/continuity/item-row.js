import { h } from '../../util/h.js';
import { formatRelative } from '../../lib/time.js';

const CHIP_BY_TYPE = {
  decision: ['co-chip co-chip--dec', 'Decision'],
  handover: ['co-chip co-chip--han', 'Handover'],
  task:     ['co-chip co-chip--tsk', 'Task'],
  branch:   ['co-chip co-chip--brn', 'Branch'],
  idea:     ['co-chip co-chip--ide', 'Idea'],
  issue:    ['co-chip co-chip--iss', 'Issue'],
};

export function createItemRow({ item, onClick }) {
  const [chipCls, chipLabel] = CHIP_BY_TYPE[item.type] || ['co-chip', item.type];
  const row = h('div', { class: 'co-row', on: { click: () => onClick?.(item) } },
    h('div', { class: 'co-row__line1' },
      h('span', { class: chipCls }, chipLabel),
      h('span', { class: 'co-row__title' }, item.title),
      h('span', { class: 'co-row__when' }, formatRelative(item.timestamp, { suffix: '' })),
    ),
    item.next ? h('div', { class: 'co-row__next' }, item.next) : null,
    item.where ? h('div', { class: 'co-row__where' }, item.where) : null,
  );
  return { root: row };
}
