import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';

const PRIORITIES = [
  { key: 'critical', label: 'Critical', short: 'Cr' },
  { key: 'high',     label: 'High',     short: 'Hi' },
  { key: 'medium',   label: 'Medium',   short: 'Me' },
  { key: 'low',      label: 'Low',      short: 'Lo' },
];

export function renderPriorityChips({ active = [], onToggle }) {
  const wrap = document.createElement('div');
  wrap.className = 'tm-chip-row kanban-pri-row';
  wrap.dataset.cmp = 'priority-chips';
  wrap._active = new Set(active);

  for (const p of PRIORITIES) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `kanban-pri-tog ${p.key}` + (wrap._active.has(p.key) ? ' on' : '');
    btn.dataset.key = p.key;
    btn.title = `${p.label} — ${CHIP_CLICK_HINT}`;
    btn.textContent = p.short;
    btn.addEventListener('click', (ev) => {
      const next = chipClickNext(ev, wrap._active, p.key);
      wrap._active = new Set(next);
      // Sync visual state on every chip in this row, since plain-click
      // collapses the selection and may turn off other chips.
      wrap.querySelectorAll('.kanban-pri-tog').forEach(b => {
        b.classList.toggle('on', wrap._active.has(b.dataset.key));
      });
      if (onToggle) onToggle(next);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

export function updatePriorityChips(el, { active }) {
  if (!el) return;
  el._active = new Set(active || []);
  el.querySelectorAll('.kanban-pri-tog').forEach(btn => {
    btn.classList.toggle('on', el._active.has(btn.dataset.key));
  });
}
