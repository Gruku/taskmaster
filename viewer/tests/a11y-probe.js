// User intent: the audit's two hand-rolled probes axe has no rule for — something that looks clickable but no keyboard
// can reach, and a control too small for a thumb — as functions a spec runs inside the page (page.evaluate(fn)).
// Each closes over nothing: page.evaluate serialises the function's source alone.

// Shown, not inert elements with cursor:pointer that no keyboard reaches: no interactive self or ancestor, not inheriting
// the pointer from their parent, not a label with a control, not a .link-row (or its content outside the controls slot)
// that its link's ::after covers. "tag#id.class \"text\"".
// Interactive = a native control, or an interactive role with tabindex ≥ 0. A container that is merely focusable (a
// dialog, a tab panel, a scroller) does not count: Enter on it does not activate the pointer child inside it.
export function pointerOnlyTargets() {
  const ROLES = ['button', 'link', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'checkbox', 'switch'];
  const INTERACTIVE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, '
    + '[contenteditable=""], [contenteditable="true"], '
    + `:is(${ROLES.map((r) => `[role="${r}"]`).join(', ')})[tabindex]:not([tabindex^="-"])`;
  const describe = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    return `${el.tagName.toLowerCase()}${id}${cls} "${(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)}"`;
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && getComputedStyle(el).visibility !== 'hidden';
  };
  // The box a link's ::after covers: as coverBox in smallTouchTargets (rendered, absolute, inset 0 on all four sides,
  // measured on its containing block), or null.
  const coverBox = (el) => {
    const after = getComputedStyle(el, '::after');
    if (after.content === 'none' || after.display === 'none' || after.position !== 'absolute') return null;
    if (![after.top, after.right, after.bottom, after.left].every((v) => v === '0px')) return null;
    const contains = (s) => s.position !== 'static' || s.transform !== 'none' || s.filter !== 'none'
      || s.backdropFilter !== 'none' || s.perspective !== 'none' || /size/.test(s.containerType)
      || s.contentVisibility === 'auto' || /paint|layout|strict|content/.test(s.contain)
      || /transform|filter|perspective/.test(s.willChange);
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      if (contains(getComputedStyle(a))) return a;
    }
    return null;
  };
  // Under the cover: the row itself, or its content outside .link-row__controls (lifted above the cover, it takes its
  // own clicks) — and only when the row's own link's ::after covers exactly the row.
  const coveredByRowLink = (el) => {
    const row = el.closest('.link-row');
    if (!row) return false;
    if (el !== row && el.closest('.link-row__controls, .link-row') !== row) return false;
    const link = [...row.children].find((c) => c.matches('a[href].link-row__link'));
    return !!link && coverBox(link) === row;
  };
  const out = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (getComputedStyle(el).cursor !== 'pointer' || !shown(el)) continue;
    if (el.closest('[inert]') || el.closest(INTERACTIVE)) continue;
    const parent = el.parentElement;
    if (parent && getComputedStyle(parent).cursor === 'pointer') continue;
    if (el.matches('label') && el.control) continue;
    if (coveredByRowLink(el)) continue;
    out.push(describe(el));
  }
  return out;
}

// Shown, enabled (not disabled, not inert) controls under 43.5px tall. Skips display:inline links (links in running
// text), anything inside .md-body, and a checkbox/radio whose label is ≥43.5px; a link whose ::after covers a box is
// measured by that box. "Shown" = width and height > 1 and inside the viewport horizontally.
// "tag#id.class \"text\" <height>px".
export function smallTouchTargets() {
  const MIN = 43.5;
  const SEL = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, '
    + '[role="button"], [role="tab"], [role="menuitem"], [role="menuitemradio"], [role="option"]';
  const describe = (el, h) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    const text = (el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return `${el.tagName.toLowerCase()}${id}${cls} "${text}" ${Math.round(h * 10) / 10}px`;
  };
  // A link whose ::after is stretched over a box (the stretched-link pattern — a .link-row, a Table card): that box is
  // what a finger hits, so it is measured. Strict: the ::after is rendered, absolute and at inset 0 on all four sides;
  // the box is its containing block — the link itself when the link is positioned, else the nearest ancestor that is
  // positioned or otherwise contains absolute boxes (transform, filter, perspective, containment, will-change).
  // Anything else: the link.
  const coverBox = (el) => {
    if (!el.matches('a[href]')) return null;
    const after = getComputedStyle(el, '::after');
    if (after.content === 'none' || after.display === 'none' || after.position !== 'absolute') return null;
    if (![after.top, after.right, after.bottom, after.left].every((v) => v === '0px')) return null;
    const contains = (s) => s.position !== 'static' || s.transform !== 'none' || s.filter !== 'none'
      || s.backdropFilter !== 'none' || s.perspective !== 'none' || /size/.test(s.containerType)
      || s.contentVisibility === 'auto' || /paint|layout|strict|content/.test(s.contain)
      || /transform|filter|perspective/.test(s.willChange);
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      if (contains(getComputedStyle(a))) return a;
    }
    return null;
  };
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    // Inert content (the page behind an open modal) cannot be tapped; its own route is read without the modal.
    if (el.disabled || el.closest('[inert]') || el.closest('.md-body')) continue;
    const r = (coverBox(el) ?? el).getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.right <= 0 || r.left >= innerWidth) continue;
    if (getComputedStyle(el).visibility === 'hidden') continue;
    if (el.matches('a[href]') && getComputedStyle(el).display === 'inline') continue;
    if (el.matches('input[type="checkbox"], input[type="radio"]')
      && [...(el.labels ?? [])].some((l) => l.getBoundingClientRect().height >= MIN)) continue;
    if (r.height < MIN) out.push(describe(el, r.height));
  }
  return out;
}
