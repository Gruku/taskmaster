const PRIORITIES = [
  { key: 'critical', label: 'Critical', short: 'Cr' },
  { key: 'high',     label: 'High',     short: 'Hi' },
  { key: 'medium',   label: 'Medium',   short: 'Me' },
  { key: 'low',      label: 'Low',      short: 'Lo' },
];

export function renderPriorityChips({ active = [], onToggle }) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-pri-row';
  wrap.dataset.cmp = 'priority-chips';
  wrap._active = new Set(active);

  for (const p of PRIORITIES) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `kanban-pri-tog ${p.key}` + (wrap._active.has(p.key) ? ' on' : '');
    btn.dataset.key = p.key;
    btn.title = p.label;
    btn.textContent = p.short;
    btn.addEventListener('click', () => {
      if (wrap._active.has(p.key)) wrap._active.delete(p.key);
      else                          wrap._active.add(p.key);
      btn.classList.toggle('on');
      if (onToggle) onToggle([...wrap._active]);
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
