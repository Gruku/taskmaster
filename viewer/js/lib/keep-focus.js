// User intent: a list screen redrawn by the poll puts the user's focus back on the same control in the fresh DOM rather
// than dropping it to <body> — and never moves focus that was outside the list.

// Call before the redraw; call the returned function after it. It returns whether focus is back on the same control.
export function keepFocus(scope) {
  const active = document.activeElement;
  if (!active || active === scope || !scope.contains(active)) return () => false;
  const attr = active.hasAttribute('data-focus') ? 'data-focus' : active.hasAttribute('href') ? 'href' : null;
  if (!attr) return () => false;
  const key = active.getAttribute(attr);
  return () => {
    const target = [...scope.querySelectorAll(`[${attr}]`)].find((el) => el.getAttribute(attr) === key);
    if (!target) return false;
    target.focus({ preventScroll: true });
    return document.activeElement === target;
  };
}
