// Canonical empty-state component. One look across the viewer.
//
// Tone:  matter-of-fact, no apology, no marketing voice.
// Shape: terse headline (what's missing) + optional hint (what to do).
// Usage:
//   el.appendChild(emptyState({
//     headline: 'No tasks match your filters',
//     hint: 'Try clearing a chip or the search box.',
//     action: { label: 'Clear filters', onClick: clearFilters },
//   }));
//
// For filter-induced empties, prefer "No X match your filters" over "No X"
// so the user knows it's a filter result, not data absence.

export function emptyState({ headline, hint, action } = {}) {
  const root = document.createElement('div');
  root.className = 'tm-empty';

  if (headline) {
    const h = document.createElement('div');
    h.className = 'tm-empty__headline';
    h.textContent = headline;
    root.appendChild(h);
  }

  if (hint) {
    const p = document.createElement('div');
    p.className = 'tm-empty__hint';
    p.textContent = hint;
    root.appendChild(p);
  }

  if (action && action.label && typeof action.onClick === 'function') {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tm-empty__action';
    btn.textContent = action.label;
    btn.addEventListener('click', action.onClick);
    root.appendChild(btn);
  }

  return root;
}

export default emptyState;
