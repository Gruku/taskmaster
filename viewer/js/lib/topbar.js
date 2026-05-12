// Shared #topbar-actions helpers — Layer 2/3 of v3-control-consistency.
// Each screen's mount() calls claimTopbar() to wipe the slot (preserving the
// auto-status pill, which lives globally) and gets back the slot element.
// Then it appends primitives built with tmSubcount/tmSearch/tmSegmented/tmAction.

export function claimTopbar() {
  const root = document.getElementById('topbar-actions');
  if (!root) return null;
  const pill = root.querySelector('.auto-status-pill');
  root.replaceChildren();
  if (pill) root.appendChild(pill);
  return root;
}

export function tmSubcount(text = '') {
  const el = document.createElement('span');
  el.className = 'tm-subcount';
  el.textContent = text;
  return el;
}

export function tmSearch({ placeholder = 'Search…', value = '', onInput, kbd = '⌘K', debounceMs = 180, ariaLabel } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'tm-search';
  const icon = document.createElement('span');
  icon.className = 'icon';
  icon.textContent = '⌕';
  const input = document.createElement('input');
  // Use type="text" to suppress the WebKit-native clear pseudo-element which is
  // unstyleable and fires inconsistent events. We provide our own clear button.
  input.type = 'text';
  input.placeholder = placeholder;
  input.value = value || '';
  if (ariaLabel) input.setAttribute('aria-label', ariaLabel);

  // Clear button — visible only when the field has content.
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'tm-search__clear';
  clearBtn.setAttribute('aria-label', 'Clear search');
  clearBtn.textContent = '×';

  function syncClearVisibility() {
    wrap.classList.toggle('tm-search--has-value', input.value.length > 0);
  }

  function clearSearch() {
    input.value = '';
    syncClearVisibility();
    // Dispatch a real input event so debounced handlers and chip-count guards fire.
    input.dispatchEvent(new Event('input', { bubbles: true }));
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

  wrap.append(icon, input, clearBtn);

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
      t = setTimeout(() => onInput(input.value), debounceMs);
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
    if (opt.key === value) {
      b.classList.add('on');
      b.setAttribute('aria-pressed', 'true');
    } else {
      b.setAttribute('aria-pressed', 'false');
    }
    b.addEventListener('click', () => {
      for (const x of wrap.querySelectorAll('button')) {
        const isOn = x.dataset.key === opt.key;
        x.classList.toggle('on', isOn);
        x.setAttribute('aria-pressed', String(isOn));
      }
      onChange?.(opt.key);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

// variant: 'primary' | 'ghost' | 'icon' | undefined
export function tmAction({ icon, label, variant, title, onClick, href, disabled = false } = {}) {
  const el = href ? document.createElement('a') : document.createElement('button');
  el.className = 'tm-action' + (variant ? ` tm-action--${variant}` : '');
  if (!href) el.type = 'button';
  if (href) el.href = href;
  const ariaLabel = title || label || '';
  if (title) el.title = title;
  if (ariaLabel) el.setAttribute('aria-label', ariaLabel);
  if (icon) {
    const i = document.createElement('span');
    i.className = 'tm-action__icon';
    i.textContent = icon;
    el.appendChild(i);
  }
  if (label) {
    const t = document.createElement('span');
    t.textContent = label;
    el.appendChild(t);
  }
  if (disabled) {
    el.disabled = true;
    el.setAttribute('aria-disabled', 'true');
  } else {
    el.disabled = false;
    el.removeAttribute('aria-disabled');
  }
  if (onClick) el.addEventListener('click', onClick);
  return el;
}

// Convenience: create a [subcount, search, ...] right-aligned cluster.
// Most screens want subcount on the left; pass `cluster: 'right'` to push the rest right.
export function rightCluster() {
  const el = document.createElement('div');
  el.className = 'tm-right';
  el.style.cssText = 'margin-left:auto; display:inline-flex; align-items:center; gap:var(--sp-2);';
  return el;
}
