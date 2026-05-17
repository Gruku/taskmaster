// Segmented control: Action / Time / Entity.
import { h } from '../../util/h.js';

const VIEWS = [
  { key: 'action', label: 'Action' },
  { key: 'time',   label: 'Time' },
  { key: 'entity', label: 'Entity' },
];

export function createViewSwitcher({ active = 'action', onSelect }) {
  const root = h('div', { class: 'co-view-switcher', role: 'tablist' });
  const buttons = [];

  function setActive(key) {
    for (const b of buttons) {
      const on = b.dataset.key === key;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', String(on));
    }
  }

  for (const v of VIEWS) {
    const isActive = v.key === active;
    const btn = h('button', {
      type: 'button',
      class: 'co-view-switcher__btn' + (isActive ? ' is-active' : ''),
      role: 'tab',
      'aria-selected': String(isActive),
      'data-key': v.key,
      on: { click: () => {
        setActive(v.key);
        onSelect?.(v.key);
      } },
    }, v.label);
    buttons.push(btn);
    root.appendChild(btn);
  }

  return { root, setActive };
}
