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

// The same block for a state that is not "nothing here": loading, not found, failed.
// A Technical label, a Narrator sentence, an optional hint, and at most one action — a link ({ label, href }),
// a button ({ label, onClick }) or a node built by the caller. `busy` marks it as a live "working" status.
export function stateBlock({ label, headline, hint, action, busy = false, state } = {}) {
  const root = document.createElement('div');
  root.className = 'tm-empty';
  if (state) root.dataset.state = state;
  if (busy) {
    root.setAttribute('role', 'status');
    root.setAttribute('aria-busy', 'true');
  }
  for (const [cls, value] of [['tm-empty__label', label], ['tm-empty__headline', headline], ['tm-empty__hint', hint]]) {
    if (!value) continue;
    const el = document.createElement('div');
    el.className = cls;
    el.textContent = value;
    root.appendChild(el);
  }
  if (action && typeof action.nodeType === 'number') root.appendChild(action);
  else if (action?.label && (action.href || typeof action.onClick === 'function')) {
    const el = document.createElement(action.href ? 'a' : 'button');
    if (action.href) el.setAttribute('href', action.href);
    else el.type = 'button';
    el.className = 'btn btn--secondary btn--sm';
    el.textContent = action.label;
    if (action.onClick) el.addEventListener('click', action.onClick);
    root.appendChild(el);
  }
  return root;
}

export default emptyState;
