// Canonical state block: one look for "nothing here", loading, not found and failed, across the viewer.
//
// Tone: matter-of-fact, no apology, no marketing voice. For filter-induced empties, prefer
// "No X match your filters" over "No X" so the user knows it is a filter result, not data absence.
//
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
