// Standalone pill+popover for archived phases. Hidden when phases is empty.

export function renderArchivedPhasesDropdown({ phases = [], active = '__all__', onSelect }) {
  const root = document.createElement('div');
  root.className = 'phs-archived';
  root.dataset.cmp = 'archived-phases';

  if (!phases.length) {
    root.classList.add('hidden');
    return root;
  }

  const isActiveSelection = phases.some(p => p.id === active);

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'phs-archived-trigger' + (isActiveSelection ? ' filtered' : '');
  trigger.title = `${phases.length} archived ${phases.length === 1 ? 'phase' : 'phases'}`;
  trigger.innerHTML = `
    <span class="ic">⌫</span>
    <span class="lbl">Archived</span>
    <span class="count">${phases.length}</span>
  `;
  root.appendChild(trigger);

  const pop = document.createElement('div');
  pop.className = 'phs-archived-pop';
  pop.hidden = true;
  root.appendChild(pop);

  for (const p of phases) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'phs-archived-row' + (active === p.id ? ' on' : '');
    const reason = p.archived_reason ? `<span class="reason">${escapeHtml(p.archived_reason)}</span>` : '';
    const stat = `${p.done || 0}/${p.total || 0}`;
    row.innerHTML = `
      <span class="name">${escapeHtml(p.name || p.id)}</span>
      <span class="meta"><span class="stat">${stat}</span>${reason}</span>
    `;
    row.addEventListener('click', () => {
      pop.hidden = true;
      if (onSelect) onSelect(p.id);
    });
    pop.appendChild(row);
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    pop.hidden = !pop.hidden;
  });

  // Close popover on outside click. Self-removes when this component's root
  // node is detached (next paint replaces the stepper subtree). Mirrors the
  // ResizeObserver self-disconnect pattern in phase-stepper.js.
  const ctrl = new AbortController();
  document.addEventListener('click', (e) => {
    if (!root.isConnected) { ctrl.abort(); return; }
    if (!root.contains(e.target)) pop.hidden = true;
  }, { signal: ctrl.signal });

  return root;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
