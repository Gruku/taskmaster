import { epicCssVar } from '../lib/epics.js';

/**
 * epics: [{id, name, color, count}]
 * active: array of epic ids (strings) selected
 */
export function renderEpicChips({ epics = [], active = [], filterCount = 0, onToggle, onClear }) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-epic-row';
  wrap.dataset.cmp = 'epic-chips';
  const set = new Set(active);

  const lbl = document.createElement('span');
  lbl.className = 'label';
  lbl.textContent = 'Epic';
  wrap.appendChild(lbl);

  // "All" chip → clears the epics filter only (other filters untouched).
  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'kanban-epic-chip' + (set.size === 0 ? ' on' : '');
  all.dataset.key = '__all__';
  all.textContent = 'All';
  all.addEventListener('click', () => {
    if (onToggle) onToggle([]);
  });
  wrap.appendChild(all);

  for (const ep of epics) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'kanban-epic-chip' + (set.has(ep.id) ? ' on' : '');
    btn.dataset.key = ep.id;
    btn.setAttribute('style', epicCssVar(ep.color).replace(/--epic:/g, '--ec:').replace(/--epic-soft:/g, '--ec-soft:'));
    btn.innerHTML = `<span class="marker"></span>${escapeHtml(ep.name || ep.id)}<span class="count">${ep.count || 0}</span>`;
    btn.addEventListener('click', () => {
      if (set.has(ep.id)) set.delete(ep.id);
      else                set.add(ep.id);
      if (onToggle) onToggle([...set]);
    });
    wrap.appendChild(btn);
  }

  const right = document.createElement('div');
  right.className = 'right';
  if (filterCount > 0) {
    const fc = document.createElement('span');
    fc.className = 'filter-count';
    fc.textContent = `${filterCount} filters`;
    right.appendChild(fc);

    const clr = document.createElement('span');
    clr.className = 'kanban-reset-link';
    clr.textContent = 'clear all';
    clr.addEventListener('click', () => onClear && onClear());
    right.appendChild(clr);
  }
  wrap.appendChild(right);

  return wrap;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
