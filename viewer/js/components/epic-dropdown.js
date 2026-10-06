// User intent: one Epic options popover for the Kanban Epic row - order, archived on or off, pins, and a real link to each epic.
import { h } from '../util/h.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';
import { truncate } from '../lib/text.js';
import { sortEpicsForDropdown } from '../lib/epic-ranking.js';

const ORDER_OPTIONS = [['count', 'Open tasks'], ['status', 'Status'], ['recent', 'Recent activity'], ['alpha', 'Name']];
let optSeq = 0;

// Epic options: the order of the Epic row, archived epics on or off, and pins — every epic listed with a real link to
// its page. Changes call back and the popover stays open; rows are redrawn inside it, never the anchor.
export function openEpicOptions({ anchor, epics = [], counts = new Map(), pinnedIds = [], sort = 'count', showArchived = false,
  onPinToggle, onSortChange, onShowArchived }) {
  const id = `epic-options-${++optSeq}`;
  const pinned = new Set(pinnedIds);
  let order = sort;
  let archived = showArchived;
  let query = '';
  const archivedCount = epics.filter((e) => e.status === 'archived').length;

  const list = h('ul', { class: 'epic-options__list' });
  const row = (e) => {
    const name = e.name || e.id;
    const pin = h('button', {
      type: 'button', class: 'btn btn--ghost btn--sm epic-option__pin', 'aria-pressed': String(pinned.has(e.id)), 'aria-label': `Pin ${name}`,
      on: { click: () => {
        const next = !pinned.has(e.id);
        if (next) pinned.add(e.id); else pinned.delete(e.id);
        pin.setAttribute('aria-pressed', String(next));
        onPinToggle?.(e.id, next);
      } },
    }, 'Pin');
    return h('li', { class: 'epic-option', 'data-epic': e.id }, [
      h('span', { class: `card-swatch card-swatch--cat-${e.swatch}`, 'aria-hidden': 'true' }),
      h('a', { class: 'epic-option__name', href: `#/epic/${encodeURIComponent(e.id)}`, title: name }, truncate(name)),
      h('span', { class: 'epic-option__count', title: 'Open tasks' }, String(counts.get(e.id) ?? 0)),
      e.status && e.status !== 'active' ? h('span', { class: 'epic-option__status' }, e.status) : null,
      pin,
    ]);
  };
  const draw = () => {
    const q = query.trim().toLowerCase();
    const shown = sortEpicsForDropdown(epics, order, counts).filter((e) =>
      (archived || e.status !== 'archived' || pinned.has(e.id))
      && (!q || `${e.name || ''} ${e.id}`.toLowerCase().includes(q)));
    list.replaceChildren(...shown.map(row));
  };

  const select = h('select', { id: `${id}-order`, class: 'ef-enum-select', on: { change: () => { order = select.value; draw(); onSortChange?.(order); } } },
    ORDER_OPTIONS.map(([v, l]) => h('option', { value: v }, l)));
  select.value = order;
  const check = h('input', { type: 'checkbox', id: `${id}-archived`,
    on: { change: () => { archived = check.checked; draw(); onShowArchived?.(archived); } } });
  check.checked = archived;
  const search = h('input', { type: 'search', id: `${id}-filter`, class: 'epic-options__filter', autocomplete: 'off',
    on: { input: () => { query = search.value; draw(); } } });

  const content = h('div', { class: 'epic-options__body' }, [
    h('div', { class: 'epic-options__field' }, [
      h('label', { for: `${id}-order`, class: 'epic-options__label' }, 'Order'),
      h('span', { class: 'ef-select' }, select, icon('chevron', { size: 16 })),
    ]),
    h('label', { class: 'epic-options__check', for: `${id}-archived` }, [check, ` Show archived epics (${archivedCount})`]),
    h('div', { class: 'epic-options__field' }, [
      h('label', { for: `${id}-filter`, class: 'epic-options__label' }, 'Filter epics'),
      search,
    ]),
    list,
  ]);
  draw();
  const handle = openPopover({ anchor, content, role: 'dialog', label: 'Epic options', focus: 'first', className: 'epic-options' });
  // A poll while open rewrites the counts in place: rows keep their order, focus and the search text.
  handle.updateCounts = (next) => {
    counts = next;
    for (const li of list.children) {
      const c = li.querySelector('.epic-option__count');
      if (c) c.textContent = String(counts.get(li.dataset.epic) ?? 0);
    }
  };
  return handle;
}
