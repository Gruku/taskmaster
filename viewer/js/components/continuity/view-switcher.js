// Segmented control: Action / Time / Entity.
import { h } from '../../util/h.js';

const VIEWS = [
  { key: 'action', label: 'Action' },
  { key: 'time',   label: 'Time' },
  { key: 'entity', label: 'Entity' },
];

export function createViewSwitcher({ active = 'action', onSelect }) {
  const root = h('div', { class: 'co-view-switcher' });
  for (const v of VIEWS) {
    const btn = h('button', {
      type: 'button',
      class: 'co-view-switcher__btn' + (v.key === active ? ' is-active' : ''),
      on: { click: () => onSelect?.(v.key) },
    }, v.label);
    root.appendChild(btn);
  }
  return { root };
}
