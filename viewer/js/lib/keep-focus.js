// User intent: a list screen redrawn by the poll puts the user's focus back on the same control in the fresh DOM rather
// than dropping it to <body> — and never moves focus that was outside the list.

/**
 * Call before the redraw; call the returned function after it. It returns whether focus is back on the same control.
 *
 * The control is found again by its `data-focus`, else its `href`; when several elements share that key (a row's title
 * link and its "open" link), the one at the same position among them wins, else the first.
 * When it cannot be found and focus was lost to <body>, focus goes to `fallback` (an element, or a function returning
 * one), else to the scope itself — given `tabindex="-1"` first if it is not focusable — and the call still returns false.
 * Focus that started outside the scope (or on the scope itself) is never moved.
 */
export function keepFocus(scope, { fallback } = {}) {
  const active = document.activeElement;
  if (!active || active === scope || !scope.contains(active)) return () => false;
  const attr = active.hasAttribute('data-focus') ? 'data-focus' : active.hasAttribute('href') ? 'href' : null;
  const key = attr && active.getAttribute(attr);
  const same = () => [...scope.querySelectorAll(`[${attr}]`)].filter((el) => el.getAttribute(attr) === key);
  const index = attr ? same().indexOf(active) : -1;

  return () => {
    const matches = attr ? same() : [];
    const target = matches[index] ?? matches[0];
    if (target) {
      target.focus({ preventScroll: true });
      if (document.activeElement === target) return true;
    }
    const now = document.activeElement;
    if (!now || now === document.body) {
      const spare = typeof fallback === 'function' ? fallback() : fallback;
      if (spare) spare.focus({ preventScroll: true });
      else {
        if (scope.tabIndex < 0 && !scope.hasAttribute('tabindex')) scope.setAttribute('tabindex', '-1');
        scope.focus({ preventScroll: true });
      }
    }
    return false;
  };
}
