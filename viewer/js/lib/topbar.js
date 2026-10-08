// Shared #topbar-actions helpers — Layer 2/3 of v3-control-consistency.
// Each screen's mount() calls claimTopbar() to wipe the slot and gets back the slot element.
// Then it appends primitives built with tmSubcount/tmSearch/tmSegmented/tmAction.
// The topbar has two rows: row 1 (title, count, primary action, theme toggle) and row 2
// (#topbar-actions: search, view switcher, filters). claimTopbar() clears both and returns row 2.
// Row 2 stays on one line: what does not fit waits behind its "Filters" button, and goes with the screen.

import { icon } from '../components/icon.js';
import { overflowRow } from '../components/overflow-row.js';

const rows = new WeakMap();   // #topbar-actions → its overflowRow, installed once for the page's life

function topbarRow(root) {
  let row = rows.get(root);
  if (row) return row;
  let mo = null;
  // Every layout writes each child's flex-shrink while it measures; those writes are its own, not news.
  // No count on Filters: it holds controls, set or not, and "Filters 3" would read as three filters on.
  row = overflowRow(root, {
    moreLabel: 'Filters', moreIcon: 'sliders', popoverLabel: 'Filters', keep: (el) => el.matches('.tm-search'), counted: false,
    onLayout: () => mo?.takeRecords(),
  });
  rows.set(root, row);
  // A count or label rewritten in place, or a control unhidden in place (hidden, a class, a style), changes its
  // control's width, which the row does not watch. Its own moves (children of the row itself), the row's and Filters'
  // own attributes, Filters' count and the open popover are left out, or a layout would retrigger itself; so are the
  // writes of every layout itself (dropped in onLayout above).
  const view = root.ownerDocument.defaultView;
  if (view.MutationObserver && view.requestAnimationFrame) {
    let frame = 0;
    const grown = (r) => r.target !== root && !row.more.contains(r.target)
      && !(r.target.nodeType === 1 ? r.target : r.target.parentElement)?.closest('.popover');
    mo = new view.MutationObserver((records) => {
      if (frame || !records.some(grown)) return;
      frame = view.requestAnimationFrame(() => { frame = 0; row.relayout(); });
    });
    mo.observe(root, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'class', 'style'] });
  }
  return row;
}

export function claimTopbar() {
  setTopbarCount('');
  document.getElementById('topbar-primary')?.replaceChildren();
  const root = document.getElementById('topbar-actions');
  if (!root) return null;
  const row = topbarRow(root);
  // The leaving screen's controls come back from the closed popover first, so they go with the rest.
  row.reset();
  for (const el of [...root.children]) if (el !== row.more) el.remove();
  return root;
}

// A claim clears, as claimTopbar() does: a screen that re-renders never finds its old primary still there.
export function claimTopbarPrimary() {
  const el = document.getElementById('topbar-primary');
  el?.replaceChildren();
  return el;
}

// Each " · " part is a span of its own, so at phone width the count wraps between its parts ("2 threads ·" over
// "3 handovers") rather than inside one; the text read out is unchanged. Its title still carries the whole text.
export function setTopbarCount(text = '') {
  const el = document.getElementById('topbar-count');
  if (!el) return;
  const parts = text ? text.split(' · ') : [];
  el.replaceChildren(...parts.flatMap((part, i) => {
    const span = document.createElement('span');
    span.className = 'topbar-count__part';
    span.textContent = i < parts.length - 1 ? `${part} ·` : part;
    return i ? [' ', span] : [span];
  }));
  if (text) el.title = text;
  else el.removeAttribute('title');
}

// The hint next to the search field names the shortcut main.js actually binds.
function searchShortcutHint() {
  const platform = typeof navigator !== 'undefined' ? (navigator.platform || '') : '';
  return /Mac|iPhone|iPad/.test(platform) ? '⌘K' : 'Ctrl K';
}

export function tmSubcount(text = '') {
  const el = document.createElement('span');
  el.className = 'tm-subcount';
  el.textContent = text;
  return el;
}

// kbd: omit for the platform's shortcut hint; pass a falsy value to hide it.
export function tmSearch({ placeholder = 'Search…', value = '', onInput, kbd, debounceMs = 180, ariaLabel } = {}) {
  if (kbd === undefined) kbd = searchShortcutHint();
  const wrap = document.createElement('div');
  wrap.className = 'tm-search';
  const glyph = icon('search', { size: 16 });
  const input = document.createElement('input');
  // Use type="text" to suppress the WebKit-native clear pseudo-element which is
  // unstyleable and fires inconsistent events. We provide our own clear button.
  input.type = 'text';
  input.placeholder = placeholder;
  input.value = value || '';
  input.setAttribute('aria-label', ariaLabel || placeholder.replace(/…$/, ''));
  input.dataset.globalSearch = '';

  // Clear button — visible only when the field has content.
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'tm-search__clear btn btn--ghost btn--icon btn--sm';
  clearBtn.setAttribute('aria-label', 'Clear search');
  clearBtn.appendChild(icon('dismiss', { size: 14 }));

  function syncClearVisibility() {
    wrap.classList.toggle('tm-search--has-value', input.value.length > 0);
  }

  function clearSearch() {
    input.value = '';
    syncClearVisibility();
    // Dispatch a real input event so debounced handlers and chip-count guards fire —
    // unless the screen that built this search is gone.
    if (input.isConnected) input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }

  clearBtn.addEventListener('click', clearSearch);

  // Escape on a focused input is a standard clear shortcut.
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && input.value.length > 0) {
      e.preventDefault();
      clearSearch();
    }
  });

  wrap.append(glyph, input, clearBtn);

  if (kbd) {
    const k = document.createElement('span');
    k.className = 'cmp-kbd';
    k.textContent = kbd;
    wrap.appendChild(k);
  }

  if (typeof onInput === 'function') {
    let t = null;
    input.addEventListener('input', () => {
      syncClearVisibility();
      if (t) clearTimeout(t);
      // The 180ms wait can outlive the screen that built this search; a call then would paint into the next screen.
      t = setTimeout(() => { if (input.isConnected) onInput(input.value); }, debounceMs);
    });
  } else {
    // Even without an onInput callback, keep the clear-button visibility in sync.
    input.addEventListener('input', syncClearVisibility);
  }

  // Sync initial state if a value was pre-filled.
  syncClearVisibility();

  return { el: wrap, input };
}

// options: [{ key, label, title?, ariaLabel? }]
// opts.icon = true → fixed-square glyph buttons
export function tmSegmented(options, { value, onChange, icon = false } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'tm-segmented' + (icon ? ' tm-segmented--icon' : '');
  for (const opt of options) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.key = opt.key;
    b.textContent = opt.label;
    if (opt.title) b.title = opt.title;
    b.setAttribute('aria-label', opt.ariaLabel || opt.title || opt.label);
    b.setAttribute('aria-pressed', String(opt.key === value));
    b.addEventListener('click', () => {
      for (const x of wrap.querySelectorAll('button')) {
        x.setAttribute('aria-pressed', String(x.dataset.key === opt.key));
      }
      onChange?.(opt.key);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

const ACTION_CLASS = { primary: 'btn btn--primary', ghost: 'btn btn--ghost', icon: 'btn btn--ghost btn--icon' };

// icon: an ICONS name (an unknown name throws). variant: 'primary' | 'ghost' | 'icon' | undefined (secondary).
export function tmAction({ icon: glyph, label, variant, title, onClick, href } = {}) {
  const el = href ? document.createElement('a') : document.createElement('button');
  el.className = ACTION_CLASS[variant] || 'btn btn--secondary';
  if (!href) el.type = 'button';
  if (href) el.href = href;
  const ariaLabel = title || label || '';
  if (title) el.title = title;
  if (ariaLabel) el.setAttribute('aria-label', ariaLabel);
  if (glyph) el.appendChild(icon(glyph, { size: 14 }));
  if (label) {
    const t = document.createElement('span');
    t.textContent = label;
    el.appendChild(t);
  }
  if (onClick) el.addEventListener('click', onClick);
  return el;
}

// Convenience: create a [subcount, search, ...] right-aligned cluster.
// Most screens want subcount on the left; pass `cluster: 'right'` to push the rest right.
export function rightCluster() {
  const el = document.createElement('div');
  el.className = 'tm-right';
  return el;
}
