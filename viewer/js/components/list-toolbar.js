// User intent: the Issues, Bugs and Ideas toolbars share one filter rail — a named group whose "Clear filters" shows only
// when a filter is on — and every select, input or segmented control in them carries a real label.
import { h } from '../util/h.js';
import { icon } from './icon.js';

let seq = 0;

export function filterRail({ label = 'Filters', onClear } = {}) {
  const clear = h('button', { type: 'button', class: 'btn btn--ghost btn--sm list-filters__clear' },
    icon('dismiss', { size: 14 }), 'Clear filters');
  clear.hidden = true;
  clear.addEventListener('click', () => onClear?.());
  const el = h('div', { class: 'list-filters', role: 'group', 'aria-label': label }, clear);
  return {
    el,
    add(...nodes) { for (const n of nodes) el.insertBefore(n, clear); },
    setClearable(on) { clear.hidden = !on; },
  };
}

// A select or input is named by a <label for>; anything else (a segmented control) becomes a group named by its label.
export function labelled({ label, control }) {
  const field = control.matches('select, input') ? control : control.querySelector('select, input');
  if (field) {
    if (!field.id) field.id = `list-ctl-${++seq}`;
    return h('div', { class: 'list-labelled' }, h('label', { class: 'list-labelled__label', for: field.id }, label), control);
  }
  const id = `list-lbl-${++seq}`;
  return h('div', { class: 'list-labelled', role: 'group', 'aria-labelledby': id },
    h('span', { class: 'list-labelled__label', id }, label), control);
}
