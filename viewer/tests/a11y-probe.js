// User intent: the audit's two hand-rolled probes axe has no rule for — something that looks clickable but no keyboard
// can reach, and a control too small for a thumb — as functions a spec runs inside the page (page.evaluate(fn)).
// Each closes over nothing: page.evaluate serialises the function's source alone.

// Shown elements with cursor:pointer that no keyboard reaches: no focusable self or ancestor, not inheriting the pointer
// from their parent, not a label with a control, not inside a .link-row whose link covers it. "tag#id.class \"text\"".
export function pointerOnlyTargets() {
  const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable=""], [contenteditable="true"]';
  const describe = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    return `${el.tagName.toLowerCase()}${id}${cls} "${(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)}"`;
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && getComputedStyle(el).visibility !== 'hidden';
  };
  const coveredByRowLink = (el) => {
    const row = el.closest('.link-row');
    const link = row && [...row.children].find((c) => c.matches('.link-row__link'));
    if (!link) return false;
    const after = getComputedStyle(link, '::after');
    return after.content !== 'none' && after.position === 'absolute';
  };
  const out = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (getComputedStyle(el).cursor !== 'pointer' || !shown(el)) continue;
    if (el.closest(FOCUSABLE)) continue;
    const parent = el.parentElement;
    if (parent && getComputedStyle(parent).cursor === 'pointer') continue;
    if (el.matches('label') && el.control) continue;
    if (coveredByRowLink(el)) continue;
    out.push(describe(el));
  }
  return out;
}

// Shown, enabled (not disabled, not inert) controls under 43.5px tall. Skips display:inline links (links in running text), anything inside
// .md-body, and a checkbox/radio whose label is ≥43.5px. "Shown" = width and height > 1 and inside the viewport
// horizontally. "tag#id.class \"text\" <height>px".
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
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    // Inert content (the page behind an open modal) cannot be tapped; its own route is read without the modal.
    if (el.disabled || el.closest('[inert]') || el.closest('.md-body')) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.right <= 0 || r.left >= innerWidth) continue;
    if (getComputedStyle(el).visibility === 'hidden') continue;
    if (el.matches('a[href]') && getComputedStyle(el).display === 'inline') continue;
    if (el.matches('input[type="checkbox"], input[type="radio"]')
      && [...(el.labels ?? [])].some((l) => l.getBoundingClientRect().height >= MIN)) continue;
    if (r.height < MIN) out.push(describe(el, r.height));
  }
  return out;
}
