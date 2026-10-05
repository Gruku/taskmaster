// User intent: one filter chip and one chip row for every filtered screen — a real toggle button whose pressed state is
// always visible and can always be turned off, in a row that never wraps and parks the rest behind "More".

import { h } from '../util/h.js';
import { overflowRow } from './overflow-row.js';

let seq = 0;

// Everything about a chip that an update may change, applied in place so the button (and its focus) survives.
function paint(btn, { label, pressed = false, count, swatch, title }) {
  btn.setAttribute('aria-pressed', String(!!pressed));
  // A pressed filter at zero must stay enabled, or it could never be turned off.
  btn.disabled = count === 0 && !pressed;
  btn.title = title ?? label;
  const name = btn.querySelector(':scope > .chip__label') ?? h('span', { class: 'chip__label' });
  name.textContent = label;
  const tally = typeof count === 'number' ? btn.querySelector(':scope > .chip__count') ?? h('span', { class: 'chip__count' }) : null;
  if (tally) tally.textContent = String(count);
  const dot = swatch ? h('span', { class: `chip__swatch chip__swatch--cat-${swatch}`, 'aria-hidden': 'true' }) : null;
  btn.replaceChildren(...[dot, name, tally].filter(Boolean));
}

export function filterChip({ label, value, pressed = false, count, swatch, title, onToggle }) {
  const btn = h('button', { type: 'button', class: 'chip', 'data-value': value });
  paint(btn, { label, pressed, count, swatch, title });
  btn.addEventListener('click', (e) => onToggle?.(value, e));
  return btn;
}

/**
 * A labelled group of filter chips on one line; the chips that do not fit sit behind "More <label>".
 * `update(chips)` keeps the button of every value already shown and repaints it in place. Added, removed and reordered
 * chips are applied at once while More is closed, and when it closes while it is open, so a chip being pressed inside
 * More never moves under the pointer.
 */
export function chipRow({ label, chips, onToggle, hint }) {
  const id = `chip-row-${++seq}`;
  const chipsEl = h('div', { class: 'chip-row__chips' });
  const el = h('div', { class: 'chip-row', role: 'group', 'aria-labelledby': id },
    h('span', { class: 'chip-row__label', id, title: hint }, label), chipsEl);
  const buttons = new Map();   // value → button, in the order shown
  let deferred = null;         // chips whose adds, removals and order wait for More to close

  const ov = overflowRow(chipsEl, {
    moreLabel: 'More',
    popoverLabel: `More ${label}`,
    onLayout() {
      if (deferred) {
        const next = deferred;
        deferred = null;
        arrange(next);
      }
      announce();
    },
  });
  const on = h('span', { class: 'overflow-more__on' });

  // A pressed chip parked behind More is still said on the row: "· n on" and in More's name.
  function announce() {
    const hidden = [...buttons.values()].filter((b) => b.hasAttribute('data-popover-item'));
    const pressed = hidden.filter((b) => b.getAttribute('aria-pressed') === 'true').length;
    if (pressed) {
      on.textContent = `· ${pressed} on`;
      ov.more.append(on);
    } else on.remove();
    ov.more.setAttribute('aria-label', `More ${label}, ${hidden.length} hidden${pressed ? `, ${pressed} selected` : ''}`);
  }

  const make = (c) => filterChip({ ...c, onToggle: (value, e) => onToggle?.(value, e) });

  function arrange(list) {
    const focused = el.ownerDocument.activeElement;
    const want = new Map(list.map((c) => [c.value, buttons.get(c.value) ?? make(c)]));
    for (const [value, b] of buttons) if (!want.has(value)) b.remove();
    ov.reset();
    let at = chipsEl.firstElementChild;
    for (const b of want.values()) {
      if (b === at) { at = at.nextElementSibling; continue; }
      chipsEl.insertBefore(b, at);
    }
    buttons.clear();
    for (const [value, b] of want) buttons.set(value, b);
    if (focused && focused !== el.ownerDocument.activeElement && focused.isConnected) focused.focus({ preventScroll: true });
    ov.relayout();
  }

  function update(list) {
    for (const c of list) {
      const b = buttons.get(c.value);
      if (b) paint(b, c);
    }
    const shown = [...buttons.keys()];
    const same = list.length === shown.length && list.every((c, i) => shown[i] === c.value);
    deferred = null;
    if (same) ov.relayout();   // a new count or label changes a chip's width in place
    else if (ov.isOpen()) {
      deferred = list;
      ov.relayout();   // waits for More to close; its onLayout then applies the new set
    } else arrange(list);
    announce();
  }

  arrange(chips);
  announce();
  return { el, update, destroy: () => ov.destroy() };
}
