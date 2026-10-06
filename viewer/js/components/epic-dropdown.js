// Dropdown panel for the epic filter row. Used by epic-chips.js.
// Stateless from the caller's perspective: caller passes { epics, selectedIds, pinnedIds, sort, ... }
// and gets callbacks for: onToggleEpic, onPinToggle, onSortChange, onClearAll, onClose.

import { h } from '../util/h.js';
import { icon } from './icon.js';
import { openPopover } from './popover.js';
import { truncate } from '../lib/text.js';
import { sortEpicsForDropdown } from '../lib/epic-ranking.js';
import { epicCssVar } from '../lib/epics.js';

const SORT_OPTIONS = [
  { key: 'count',  label: 'Task count' },
  { key: 'status', label: 'Status (active → done → archived)' },
  { key: 'recent', label: 'Recent activity' },
  { key: 'alpha',  label: 'Alphabetical' },
];

export function renderEpicDropdown({
  epics = [],
  selectedIds = [],
  pinnedIds = [],
  activeCounts = new Map(),
  sort = 'count',
  onToggleEpic,
  onPinToggle,
  onSortChange,
  onClearAll,
  onClose,
}) {
  const panel = document.createElement('div');
  panel.className = 'kanban-epic-dropdown';
  panel.dataset.cmp = 'epic-dropdown';

  // Header: sort selector + filter input
  const head = document.createElement('div');
  head.className = 'ed-head';

  const filterInput = document.createElement('input');
  filterInput.type = 'search';
  filterInput.className = 'ed-filter';
  filterInput.placeholder = 'Filter epics…';
  head.appendChild(filterInput);

  const sortSel = document.createElement('select');
  sortSel.className = 'ed-sort';
  for (const opt of SORT_OPTIONS) {
    const o = document.createElement('option');
    o.value = opt.key; o.textContent = opt.label;
    if (opt.key === sort) o.selected = true;
    sortSel.appendChild(o);
  }
  sortSel.addEventListener('change', () => onSortChange && onSortChange(sortSel.value));
  head.appendChild(sortSel);

  panel.appendChild(head);

  // List
  const list = document.createElement('div');
  list.className = 'ed-list';
  panel.appendChild(list);

  // Footer
  const foot = document.createElement('div');
  foot.className = 'ed-foot';
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'ed-clear';
  clearBtn.textContent = 'Clear all';
  clearBtn.addEventListener('click', () => onClearAll && onClearAll());
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'ed-close';
  closeBtn.textContent = 'Close';
  closeBtn.addEventListener('click', () => onClose && onClose());
  foot.appendChild(clearBtn);
  foot.appendChild(closeBtn);
  panel.appendChild(foot);

  const selectedSet = new Set(selectedIds);
  const pinnedSet   = new Set(pinnedIds);

  // (b) Archived hidden by default. Track toggle state locally within the panel. (v3-polish-047)
  const archivedCount = epics.filter(e => (e.status || '').toLowerCase() === 'archived').length;
  let showArchived = false;

  // Show-archived toggle (only rendered if archived epics exist)
  if (archivedCount > 0) {
    const archiveToggle = document.createElement('button');
    archiveToggle.type = 'button';
    archiveToggle.className = 'ed-archive-toggle';
    archiveToggle.textContent = `Show archived (${archivedCount})`;
    archiveToggle.addEventListener('click', () => {
      showArchived = !showArchived;
      archiveToggle.textContent = showArchived
        ? `Hide archived (${archivedCount})`
        : `Show archived (${archivedCount})`;
      archiveToggle.classList.toggle('on', showArchived);
      renderList();
    });
    foot.insertBefore(archiveToggle, clearBtn);
  }

  function renderList() {
    const q = filterInput.value.trim().toLowerCase();
    const sorted = sortEpicsForDropdown(epics, sort, activeCounts);
    // (b) Filter out archived unless show-archived is toggled on. (v3-polish-047)
    const visibleByArchive = showArchived
      ? sorted
      : sorted.filter(e => (e.status || '').toLowerCase() !== 'archived');
    const filtered = q
      ? visibleByArchive.filter(e => String(e.name || e.id || '').toLowerCase().includes(q))
      : visibleByArchive;
    list.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'ed-empty';
      empty.textContent = q ? `No epics match "${q}"` : (archivedCount > 0 && !showArchived ? 'No active epics' : 'No epics');
      list.appendChild(empty);
      return;
    }
    for (const ep of filtered) {
      const row = document.createElement('div');
      row.className = 'ed-row';
      row.style.cssText = epicCssVar(ep.color).replace(/--epic:/g, '--ec:').replace(/--epic-soft:/g, '--ec-soft:');

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selectedSet.has(ep.id);
      cb.className = 'ed-check';
      cb.addEventListener('change', () => onToggleEpic && onToggleEpic(ep.id, cb.checked));
      row.appendChild(cb);

      const swatch = document.createElement('span');
      swatch.className = 'ed-swatch';
      row.appendChild(swatch);

      const name = document.createElement('span');
      name.className = 'ed-name';
      name.textContent = ep.name || ep.id;
      row.appendChild(name);

      const status = document.createElement('span');
      status.className = 'ed-status ed-status--' + (String(ep.status || 'active').toLowerCase());
      status.textContent = ep.status || 'active';
      row.appendChild(status);

      const cnt = document.createElement('span');
      cnt.className = 'ed-count';
      cnt.textContent = String(activeCounts.get(ep.id) || 0);
      row.appendChild(cnt);

      const pin = document.createElement('button');
      pin.type = 'button';
      pin.className = 'ed-pin' + (pinnedSet.has(ep.id) ? ' on' : '');
      pin.title = pinnedSet.has(ep.id) ? 'Unpin' : 'Pin';
      pin.setAttribute('aria-label', pin.title);
      pin.textContent = pinnedSet.has(ep.id) ? '★' : '☆';
      pin.addEventListener('click', () => onPinToggle && onPinToggle(ep.id, !pinnedSet.has(ep.id)));
      row.appendChild(pin);

      list.appendChild(row);
    }
  }

  filterInput.addEventListener('input', renderList);
  renderList();

  // Stop propagation so clicks inside the panel don't close it.
  panel.addEventListener('click', (e) => e.stopPropagation());

  return panel;
}

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
  return openPopover({ anchor, content, role: 'dialog', label: 'Epic options', focus: 'first', className: 'epic-options' });
}
