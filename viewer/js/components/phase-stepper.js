// Phase stepper. Reads phases from backlog + a doneCount/total per phase.
//   phases: [{id, name, status: 'done'|'active'|'future', done, total}]
//   active: phase id (string), '__all__', or '__orphans__'
//   onSelect(phaseKey): callback when a cell is clicked.

export function renderPhaseStepper({ phases = [], active = '__all__', onSelect, showHistory = false }) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-phase-stepper';
  wrap.dataset.cmp = 'phase-stepper';

  // Optional history toggle (leftmost). Plan 2 wires only the toggle visual; behaviour is best-effort.
  const histBtn = document.createElement('button');
  histBtn.type = 'button';
  histBtn.className = 'kanban-phase-step history-toggle';
  histBtn.title = 'Show / hide past phases';
  histBtn.setAttribute('aria-label', 'Toggle past phases');
  histBtn.innerHTML = `<span class="util-icon">↺</span><span class="util-text">History</span>`;
  histBtn.addEventListener('click', () => {
    showHistory = !showHistory;
    wrap.querySelectorAll('.kanban-phase-step.done').forEach(el => {
      el.style.display = showHistory ? '' : 'none';
    });
  });
  wrap.appendChild(histBtn);

  // All-phases cell
  const allDone  = phases.reduce((s, p) => s + (p.done || 0), 0);
  const allTotal = phases.reduce((s, p) => s + (p.total || 0), 0);
  const allPct   = allTotal ? Math.round((allDone / allTotal) * 100) : 0;
  const allBtn = document.createElement('button');
  allBtn.type = 'button';
  allBtn.className = 'kanban-phase-step all-step' + (active === '__all__' ? ' active' : '');
  allBtn.dataset.key = '__all__';
  allBtn.title = 'Show all phases';
  allBtn.innerHTML = `<span class="util-icon">⌂</span><span class="util-text">All phases</span><span class="ph-stat">${allDone}/${allTotal} · ${allPct}%</span>`;
  allBtn.addEventListener('click', () => onSelect && onSelect('__all__'));
  wrap.appendChild(allBtn);

  for (const ph of phases) {
    const cls = ph.status || 'future';
    const isActive = active === ph.id;
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'kanban-phase-step ' + cls + (isActive ? ' active' : '');
    cell.dataset.key = ph.id;
    if (cls === 'done' && !showHistory) cell.style.display = 'none';
    const lead =
      cls === 'done'   ? '<span class="check">✓</span>'
    : cls === 'active' ? '<span class="dot"></span>'
    : '';
    const pct = (ph.total ? Math.round(((ph.done || 0) / ph.total) * 100) : 0);
    const statText =
      cls === 'active' ? `${ph.done || 0}/${ph.total || 0} · ${pct}%`
                       : `${ph.done || 0}/${ph.total || 0}`;
    const widthAttr = cls === 'active' ? ` style="width:${pct}%"` : '';
    cell.innerHTML = `
      <div class="ph-head">${lead}<span class="ph-name">${escapeHtml(ph.name || ph.id)}</span><span class="ph-stat">${statText}</span></div>
      <div class="ph-bar"><i${widthAttr}></i></div>
    `;
    cell.addEventListener('click', () => onSelect && onSelect(ph.id));
    wrap.appendChild(cell);
  }

  // Orphans cell (rightmost)
  const orphansBtn = document.createElement('button');
  orphansBtn.type = 'button';
  orphansBtn.className = 'kanban-phase-step orphans-step' + (active === '__orphans__' ? ' active' : '');
  orphansBtn.dataset.key = '__orphans__';
  orphansBtn.title = 'Tasks with no phase';
  orphansBtn.innerHTML = `<span class="util-icon">⚲</span><span class="util-text">Orphans</span>`;
  orphansBtn.addEventListener('click', () => onSelect && onSelect('__orphans__'));
  wrap.appendChild(orphansBtn);

  return wrap;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
