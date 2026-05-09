// Dropdown panel for the epic filter row. Used by epic-chips.js.
// Stateless from the caller's perspective: caller passes { epics, selectedIds, pinnedIds, sort, ... }
// and gets callbacks for: onToggleEpic, onPinToggle, onSortChange, onClearAll, onClose.

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

  function renderList() {
    const q = filterInput.value.trim().toLowerCase();
    const sorted = sortEpicsForDropdown(epics, sort, activeCounts);
    const filtered = q ? sorted.filter(e => String(e.name || e.id || '').toLowerCase().includes(q)) : sorted;
    list.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'ed-empty';
      empty.textContent = q ? `No epics match "${q}"` : 'No epics';
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
