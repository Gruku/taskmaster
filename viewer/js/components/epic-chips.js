// Single-row epic filter: All + up to 5 quick chips + dropdown trigger.
// Quick chips = pinned (in pin order) + top-ranked non-pinned, capped at QUICK_CAP.
// Dropdown shows the rest with filter/sort/multiselect/pin.
//
// Inputs:
//   epics:        full ranked list, each { id, name, color, status, count, last_referenced }
//   selectedIds:  array of currently-active epic filter ids
//   pinnedIds:    array of pinned epic ids (from prefs)
//   activeCounts: Map(epicId → number of todo+in-progress+in-review tasks)
//   sort:         current dropdown sort key ('count' | 'status' | 'recent' | 'alpha')
//   filterCount:  badge count for the right-side "N filters · clear all" link
//   onToggleEpics, onPinToggle, onSortChange, onClearFilters

import { epicCssVar } from '../lib/epics.js';
import { pluralize } from '../util/pluralize.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { splitQuickAndDropdown } from '../lib/epic-ranking.js';
import { renderEpicDropdown } from './epic-dropdown.js';

export const QUICK_CAP = 5;

export function renderEpicChips({
  epics = [],
  selectedIds = [],
  pinnedIds = [],
  activeCounts = new Map(),
  sort = 'count',
  filterCount = 0,
  onToggleEpics,
  onPinToggle,
  onSortChange,
  onClearFilters,
}) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-epic-row';
  wrap.dataset.cmp = 'epic-chips';
  const sel = new Set(selectedIds);

  const lbl = document.createElement('span');
  lbl.className = 'label';
  lbl.textContent = 'Epic';
  wrap.appendChild(lbl);

  // All chip (clears epic filter only)
  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'kanban-epic-chip' + (sel.size === 0 ? ' on' : '');
  all.dataset.key = '__all__';
  all.textContent = 'All';
  all.addEventListener('click', () => onToggleEpics && onToggleEpics([]));
  wrap.appendChild(all);

  // Quick chips
  const { quick, dropdown } = splitQuickAndDropdown(epics, pinnedIds, QUICK_CAP);
  for (const ep of quick) {
    const btn = chipFor(ep, sel, onToggleEpics);
    wrap.appendChild(btn);
  }

  // Dropdown trigger
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'kanban-epic-more';
  // The trigger always exists if there is any epic at all — even when N=0 it
  // gives access to pinning/sort. Hide only on a fully empty backlog.
  if (!epics.length) trigger.classList.add('hidden');
  trigger.innerHTML = `<span class="lbl">More</span><span class="count">${dropdown.length}</span><span class="chev">▾</span>`;
  wrap.appendChild(trigger);

  // Panel (rendered lazily, attached to wrap, toggled by trigger)
  let panel = null;
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (panel && panel.isConnected) {
      panel.remove();
      panel = null;
      return;
    }
    panel = renderEpicDropdown({
      epics,                        // full list — dropdown applies its own sort/filter
      selectedIds: [...sel],
      pinnedIds,
      activeCounts,
      sort,
      onToggleEpic: (id, checked) => {
        if (checked) sel.add(id); else sel.delete(id);
        onToggleEpics && onToggleEpics([...sel]);
      },
      onPinToggle: (id, pinned) => onPinToggle && onPinToggle(id, pinned),
      onSortChange: (next) => onSortChange && onSortChange(next),
      onClearAll:   () => { sel.clear(); onToggleEpics && onToggleEpics([]); },
      onClose:      () => { if (panel) { panel.remove(); panel = null; } },
    });
    wrap.appendChild(panel);
  });

  // Close panel on outside click. Self-removes when this component's wrap node
  // is detached (next paint replaced the row). Mirrors the pattern in
  // archived-phases-dropdown.js.
  const ctrl = new AbortController();
  document.addEventListener('click', (e) => {
    if (!wrap.isConnected) { ctrl.abort(); return; }
    if (!panel) return;
    if (!wrap.contains(e.target)) { panel.remove(); panel = null; }
  }, { signal: ctrl.signal });

  // Right side: filter count + clear-all link
  const right = document.createElement('div');
  right.className = 'right';
  if (filterCount > 0) {
    const fc = document.createElement('span');
    fc.className = 'filter-count';
    fc.textContent = `${filterCount} ${pluralize(filterCount, 'filter', 'filters')}`;
    right.appendChild(fc);

    const clr = document.createElement('span');
    clr.className = 'kanban-reset-link';
    clr.textContent = 'clear all';
    clr.addEventListener('click', () => onClearFilters && onClearFilters());
    right.appendChild(clr);
  }
  wrap.appendChild(right);

  return wrap;
}

function chipFor(ep, selSet, onToggleEpics) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'kanban-epic-chip' + (selSet.has(ep.id) ? ' on' : '');
  btn.dataset.key = ep.id;
  btn.title = CHIP_CLICK_HINT;
  btn.setAttribute('style', epicCssVar(ep.color).replace(/--epic:/g, '--ec:').replace(/--epic-soft:/g, '--ec-soft:'));
  btn.innerHTML = `<span class="marker"></span>${escapeHtml(ep.name || ep.id)}<span class="count">${ep.count || 0}</span>`;
  btn.addEventListener('click', (ev) => {
    const next = chipClickNext(ev, selSet, ep.id);
    if (onToggleEpics) onToggleEpics(next);
  });
  return btn;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
