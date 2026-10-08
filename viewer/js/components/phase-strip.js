// User intent: the Kanban phase filter as one line of named phase chips — the current phase wider, the rest behind
// More, archived phases in a menu — replacing the old carousel stepper (no transforms, shadows or glyph art).
import { h } from '../util/h.js';
import { icon } from './icon.js';
import { overflowRow } from './overflow-row.js';
import { openPopover } from './popover.js';
import { bucketPhases } from '../lib/phase-buckets.js';

let seq = 0;

// "P1.5" → "1.5": the number a phase is known by.
function phaseNum(p) {
  if (p.num != null) return String(p.num);
  const m = String(p.id || '').match(/(\d+(?:\.\d+)?)/);
  return m ? m[1] : '';
}

const KIND = { done: 'phase-chip--done', active: 'phase-chip--current' };

// Repaints a phase chip's content in place, so the button (and its focus) survives an update.
function paintPhase(b, p, kind) {
  b.className = `chip phase-chip ${kind}`;
  b.title = `${p.name} · ${p.done}/${p.total} done`;
  const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
  // replaceChildren turns a null argument into the text "null", so the optional parts are filtered out first.
  b.replaceChildren(...[
    h('span', { class: 'phase-chip__num' }, phaseNum(p)),
    kind === 'phase-chip--done' ? icon('check', { size: 12 }) : null,
    h('span', { class: 'phase-chip__name' }, p.name),
    h('span', { class: 'phase-chip__count' }, `${p.done}/${p.total}`),
    kind === 'phase-chip--current'
      ? h('span', { class: 'phase-chip__bar' }, h('span', { style: `width: ${pct}%` }))
      : null,
  ].filter(Boolean));
}

/**
 * `phaseStrip({ onSelect })` → `{ el, update({ phases, active }), destroy() }`.
 * `phases` are in display order with `status` one of done|active|planned|future|archived; `active` is '__all__',
 * '__orphans__' or a phase id. `onSelect(value)` reports a pick; the caller decides toggling.
 */
export function phaseStrip({ onSelect }) {
  const id = `phase-strip-${++seq}`;
  const items = h('div', { class: 'phase-strip__items' });
  const el = h('div', { class: 'phase-strip', role: 'group', 'aria-labelledby': id },
    h('span', { class: 'phase-strip__label chip-row__label', id }, 'Phase'), items);
  const buttons = new Map();   // value → button, in the order shown
  let archived = [];
  let active = '__all__';
  let deferred = null;

  const ov = overflowRow(items, {
    moreLabel: 'More',
    popoverLabel: 'More phases',
    keep: (b) => b.matches('.phase-chip--all, .phase-chip--current, [aria-pressed="true"], .phase-archived'),
    onLayout() {
      if (deferred) { const next = deferred; deferred = null; arrange(next); }
    },
  });

  const chip = (value, cls, ...kids) => {
    const b = h('button', { type: 'button', class: cls, 'data-value': value, 'aria-pressed': 'false' }, ...kids);
    b.addEventListener('click', () => onSelect?.(value));
    return b;
  };

  let menu = null;
  function openArchived(anchor) {
    // A second press on Archived closes its menu rather than reopening it.
    if (menu?.isOpen()) { menu.close('toggle', { returnFocus: true }); menu = null; return; }
    // The items exist before the popover opens: it picks the item to focus (the checked one, else the first) at open.
    const list = h('div', { class: 'phase-archived__menu' });
    let handle = null;
    for (const p of archived) {
      const item = h('button', {
        type: 'button', class: 'popover-item', role: 'menuitemradio',
        'aria-checked': String(p.id === active), 'data-value': p.id,
      },
      h('span', { class: 'phase-archived__name' }, p.name),
      h('span', { class: 'phase-archived__count' }, `${p.done}/${p.total}`),
      p.archived_reason ? h('span', { class: 'phase-archived__reason' }, p.archived_reason) : null);
      item.addEventListener('click', () => {
        handle?.close('select', { returnFocus: true });
        onSelect?.(p.id);
      });
      list.append(item);
    }
    handle = openPopover({
      anchor, content: list, role: 'menu', label: 'Archived phases', focus: 'checked',
    });
    menu = handle;
  }

  function archivedButton() {
    // The icon stands in for the word on a phone (kanban.css), where the strip's one line needs the room.
    const b = h('button', { type: 'button', class: 'btn btn--ghost btn--sm phase-archived', 'aria-pressed': 'false' },
      icon('archive', { size: 16 }), h('span', { class: 'phase-archived__label' }, 'Archived'),
      h('span', { class: 'phase-archived__count' }, ''));
    b.addEventListener('click', () => openArchived(b));
    return b;
  }

  // The wanted buttons, in order, reusing each by value and repainting it in place.
  function want(phases) {
    const { past, active: current, future, archived: arch } = bucketPhases(phases);
    archived = arch;
    const out = new Map();
    out.set('__all__', buttons.get('__all__') ?? chip('__all__', 'chip phase-chip phase-chip--all', 'All'));
    if (arch.length) {
      const b = buttons.get('__archived__') ?? archivedButton();
      b.querySelector('.phase-archived__count').textContent = String(arch.length);
      out.set('__archived__', b);
    }
    const rows = [...past, ...(current ? [current] : []), ...future];
    for (const p of rows) {
      const kind = p === current ? KIND.active : (String(p.status).toLowerCase() === 'done' ? KIND.done : 'phase-chip--future');
      const b = buttons.get(p.id) ?? chip(p.id, '');
      paintPhase(b, p, kind);
      out.set(p.id, b);
    }
    out.set('__orphans__', buttons.get('__orphans__') ?? chip('__orphans__', 'chip phase-chip phase-chip--orphans', 'No phase'));
    for (const [value, b] of out) {
      const pressed = value === '__archived__' ? arch.some((p) => p.id === active) : value === active;
      b.setAttribute('aria-pressed', String(pressed));
    }
    return out;
  }

  function arrange(next) {
    const focused = el.ownerDocument.activeElement;
    for (const [value, b] of buttons) if (!next.has(value)) b.remove();
    ov.reset();
    let at = items.firstElementChild;
    for (const b of next.values()) {
      if (b === at) { at = at.nextElementSibling; continue; }
      items.insertBefore(b, at);
    }
    buttons.clear();
    for (const [value, b] of next) buttons.set(value, b);
    if (focused && focused !== el.ownerDocument.activeElement && focused.isConnected) focused.focus({ preventScroll: true });
    ov.relayout();
  }

  function update({ phases = [], active: a = '__all__' } = {}) {
    active = a || '__all__';
    const next = want(phases);
    const shown = [...buttons.keys()];
    const keys = [...next.keys()];
    const same = keys.length === shown.length && keys.every((k, i) => shown[i] === k);
    deferred = null;
    if (same) ov.relayout();   // a repainted chip (count, kind, pressed) changes width or keep in place
    else if (ov.isOpen()) { deferred = next; ov.relayout(); }   // applied when More closes
    else arrange(next);
  }

  return { el, update, destroy: () => ov.destroy() };
}
